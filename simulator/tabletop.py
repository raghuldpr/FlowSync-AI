"""Virtual tabletop: emulates the intersection ESP32 (via the reference SignalFSM) and the camera's
ROI counts, talking to the backend through the SAME signed device API the real hardware will use.

`client` is any httpx-style client (httpx.Client against a live server, or FastAPI's TestClient).
"""
import json
import random
import secrets

import simulator  # noqa: F401  (puts backend/ on sys.path)
from app.core.fsm import SignalFSM
from app.core.topology import Topology
from app.security import sign


class DeviceClient:
    def __init__(self, client, device_id: str, key: str):
        self.c, self.id, self.key = client, device_id, key
        self.boot, self.seq = secrets.token_hex(4), 0

    def request(self, method: str, path: str, obj=None):
        self.seq += 1
        body = json.dumps(obj).encode() if obj is not None else b""
        headers = {"X-Device-Id": self.id, "X-Boot": self.boot, "X-Seq": str(self.seq),
                   "X-Signature": sign(self.key, method, path, self.id, self.boot, self.seq, body),
                   "Content-Type": "application/json"}
        return self.c.request(method, path, content=body, headers=headers)


class VirtualTabletop:
    DEPART_RATE = 0.7        # cars/s that can discharge on green

    def __init__(self, client, topology: Topology, devices: dict, rates: dict = None, seed: int = 1):
        self.topo = topology
        self.now_ms = 0
        self.rng = random.Random(seed)
        self.rates = {a: 0.15 for a in topology.approaches}   # arrivals, cars/s
        self.rates.update(rates or {})
        self.queues = {a: 0 for a in topology.approaches}
        self._frac = {a: 0.0 for a in topology.approaches}
        self.fsms = {i: SignalFSM(topology.timing, 0) for i in topology.intersections}
        self.dev = DeviceClient(client, "intersection-1", devices["intersection-1"]["key"])
        self.cam = DeviceClient(client, "camera-1", devices["camera-1"]["key"])
        self.stalled = set()                 # approaches with a broken-down vehicle (no departures)
        self.last_move_ms = {a: 0 for a in topology.approaches}
        self.link_up = True
        self.last_ok_ms = 0
        self.unsafe_seen = False

    # ---- physics ------------------------------------------------------------------------------
    def _physics(self, dt_s: float):
        cap = self.topo.control.link_capacity
        for a, ap in self.topo.approaches.items():
            if self.rng.random() < self.rates[a] * dt_s and self.queues[a] < cap:
                self.queues[a] += 1
            if self.queues[a] == 0:
                self.last_move_ms[a] = self.now_ms
            if (self.fsms[ap.intersection].lamps()[ap.movement] == "G" and self.queues[a] > 0
                    and a not in self.stalled):
                self._frac[a] += self.DEPART_RATE * dt_s
                while self._frac[a] >= 1 and self.queues[a] > 0:
                    self._frac[a] -= 1
                    ring = [d for d in ap.downstream if d.startswith("L")]
                    dest = ring[0] if ring and self.rng.random() < 0.5 else None
                    if dest and self.queues[dest] >= cap:
                        self._frac[a] = 0.0       # blocked by a full downstream link
                        break
                    self.queues[a] -= 1
                    self.last_move_ms[a] = self.now_ms
                    if dest:
                        self.queues[dest] += 1

    # ---- stepping -----------------------------------------------------------------------------
    def step(self, dt_ms: int = 1000, tick_ms: int = 100):
        t = 0
        while t < dt_ms:
            self.now_ms += tick_ms
            t += tick_ms
            backend_ok = (self.now_ms - self.last_ok_ms) < self.topo.timing.backend_lost_ms
            for f in self.fsms.values():
                f.set_backend_ok(backend_ok)
                f.tick(self.now_ms)
                lamps = f.lamps()
                if sum(1 for v in lamps.values() if v != "R") > 1:
                    self.unsafe_seen = True
            self._physics(tick_ms / 1000)
        if self.link_up:
            self._communicate()

    def _communicate(self):
        r = self.cam.request("POST", "/api/device/traffic", {
            "source": "simulated",
            "readings": [{"approach": a, "count": q,
                          "stationary_s": round((self.now_ms - self.last_move_ms[a]) / 1000, 1) if q else 0.0}
                         for a, q in self.queues.items()]})
        if r.status_code != 200:
            return
        self.dev.request("POST", "/api/device/state", {"intersections": {
            i: f.state(self.now_ms) for i, f in self.fsms.items()}})
        r = self.dev.request("GET", "/api/device/commands")
        if r.status_code != 200:
            return
        self.last_ok_ms = self.now_ms
        for cmd in r.json()["commands"]:
            f = self.fsms[cmd["intersection"]]
            ok, why = f.receive_command(cmd["seq"], cmd["target"], cmd["valid_for_ms"], self.now_ms)
            if ok or why != "stale_seq":      # re-polled duplicates are silently ignored
                self.dev.request("POST", "/api/device/command-ack", {
                    "seq": cmd["seq"], "intersection": cmd["intersection"], "accepted": ok, "reason": why})
