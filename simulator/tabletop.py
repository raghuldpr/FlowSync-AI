"""Virtual tabletop: emulates the intersection ESP32 (via the reference SignalFSM) and the camera's
ROI counts, talking to the backend through the SAME signed device API the real hardware will use.

A second, out-of-band channel (role `simulator`) receives WORLD commands from the scenario engine: pin a queue,
stall a vehicle, drop the camera feed or the controller link, drive the EV along its current route. That is what lets
one script run the whole demo; on a physical table a human performs the same cues instead.

`client` is any httpx-style client (httpx.Client against a live server, or FastAPI's TestClient).
"""
import random

import simulator  # noqa: F401  (puts backend/ on sys.path)
from app.core.fsm import SignalFSM
from app.core.topology import Topology
from app.device_client import DeviceClient  # noqa: F401  (re-exported: tests import it from here)


QUEUE_DRAG = 0.15        # an ambulance slows by 1/(1 + 0.15 x queued vehicles) on a queued approach
STALL_DRAG = 0.25        # an ambulance creeps at a quarter speed past a stalled vehicle in its lane
STOP_LINE = 0.9          # progress at which the stop line sits on an approach


class VirtualTabletop:
    DEPART_RATE = 0.7        # cars/s that can discharge on green

    def __init__(self, client, topology: Topology, devices: dict, rates: dict = None, seed: int = 1,
                 external_camera: bool = False):
        self.topo = topology
        self.external_camera = external_camera
        self.now_ms = 0
        self.rng = random.Random(seed)
        self.rates = {a: 0.15 for a in topology.approaches}   # arrivals, cars/s
        self.rates.update(rates or {})
        self.default_rates = dict(self.rates)
        self.queues = {a: 0 for a in topology.approaches}
        self._frac = {a: 0.0 for a in topology.approaches}
        self.fsms = {i: SignalFSM(topology.timing, 0) for i in topology.intersections}
        self.dev = DeviceClient(client, "intersection-1", devices["intersection-1"]["key"])
        self.cam = DeviceClient(client, "camera-1", devices["camera-1"]["key"])
        self.ctl = DeviceClient(client, "simulator-1", devices["simulator-1"]["key"]) if "simulator-1" in devices else None
        self.stalled = set()                 # approaches with a broken-down vehicle (no departures)
        self.pins = {}                       # approach -> queue length held there (a scripted jam)
        self.camera_on = True                # False = the camera feed is dead (traffic readings stop)
        self.ev_drive = None                 # {"vehicle_id", "pace"} while the simulated driver is moving the EV
        self.ev_sink = None                  # optional callable(edge, progress), e.g. a synthetic camera's set_ev
        self.ev_stats = {"wait_s": 0.0, "stops": 0}      # seconds the EV spent held at a stop line, and how many times it stopped
        self._ev_waiting = False
        self._ev_pos = None                  # the simulated driver's TRUE (edge, progress)
        self.departed = 0                    # vehicles that left an approach queue (throughput, for evaluation)
        self._cmd_cursor = None
        self.last_move_ms = {a: 0 for a in topology.approaches}
        self.link_up = True
        self.last_ok_ms = 0
        self.unsafe_seen = False

    # ---- physics ------------------------------------------------------------------------------
    def _physics(self, dt_s: float):
        for a, ap in self.topo.approaches.items():
            if self.rng.random() < self.rates[a] * dt_s and self.queues[a] < ap.capacity:
                self.queues[a] += 1
            if self.queues[a] == 0:
                self.last_move_ms[a] = self.now_ms
            if (self.fsms[ap.intersection].lamps()[ap.movement] == "G" and self.queues[a] > 0
                    and a not in self.stalled):
                self._frac[a] += self.DEPART_RATE * dt_s
                while self._frac[a] >= 1 and self.queues[a] > 0:
                    self._frac[a] -= 1
                    ring = [d for d in ap.downstream if self.topo.edges[d].src in self.topo.intersections]   # roads to other intersections
                    if ring and self.rng.random() < 0.5:
                        dest = self.rng.choice(ring) if len(self.topo.intersections) > 3 else ring[0]     # (tabletop: first, as tuned)
                    else:
                        dest = None
                    if dest and self.queues[dest] >= self.topo.approaches[dest].capacity:
                        self._frac[a] = 0.0       # blocked by a full downstream link
                        break
                    self.queues[a] -= 1
                    self.departed += 1
                    self.last_move_ms[a] = self.now_ms
                    if dest:
                        self.queues[dest] += 1
        for a, n in self.pins.items():            # a scripted jam is topped up as soon as cars leave it
            self.queues[a] = max(self.queues[a], min(n, self.topo.approaches[a].capacity))

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
        self._control()                                # out-of-band: works even while the device link is down

    def _communicate(self):
        if not self.external_camera and self.camera_on:      # else a camera pipeline posts what it MEASURED
            r = self.cam.request("POST", "/api/device/traffic", {
                "source": "simulated",
                "readings": [{"approach": a, "count": q + self._ev_here(a),
                              "stationary_s": round((self.now_ms - self.last_move_ms[a]) / 1000, 1) if q else 0.0}
                             for a, q in self.queues.items()]})
            if r.status_code != 200:
                return
        self.dev.request("POST", "/api/device/state", {"origin": "simulated", "intersections": {
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

    def _ev_here(self, approach: str) -> int:
        """The camera would count the ambulance as a vehicle on the approach it is on (the vision pipeline does)."""
        return 1 if self._ev_pos is not None and self._ev_pos[0] == approach else 0

    # ---- scenario control channel ---------------------------------------------------------------
    def _control(self):
        if self.ctl is None:
            return
        try:
            r = self.ctl.request("GET", "/api/device/sim-commands",
                                 params=None if self._cmd_cursor is None else {"after": self._cmd_cursor})
            if r.status_code == 200:
                body = r.json()
                self._cmd_cursor = body["latest"]
                for c in body["commands"]:
                    self.apply(c["cmd"])
            self._drive_ev()
        except Exception:                                 # the puppeteer channel must never break the simulation
            pass

    def apply(self, cmd: dict):
        op = cmd.get("op")
        if op == "reset":
            self.pins.clear(); self.stalled.clear()
            self.camera_on, self.link_up, self.ev_drive = True, True, None
            self.rates = dict(self.default_rates)
        elif op == "rates":
            if "all" in cmd:
                self.rates = {a: float(cmd["all"]) for a in self.rates}
            else:
                self.rates[cmd["approach"]] = float(cmd["rate"])
        elif op == "pin":
            if cmd.get("count") is None:
                self.pins.pop(cmd["approach"], None)
            else:
                self.pins[cmd["approach"]] = int(cmd["count"])
        elif op == "stall":
            (self.stalled.add if cmd.get("on", True) else self.stalled.discard)(cmd["approach"])
        elif op == "camera":
            self.camera_on = bool(cmd["on"])
        elif op == "link":
            self.link_up = bool(cmd["on"])
        elif op == "drive_ev":
            self.ev_drive = {"vehicle_id": cmd["vehicle_id"], "pace": float(cmd.get("pace", 3.0))} if cmd.get("on", True) else None

    def _drive_ev(self):
        d = self.ev_drive
        if not d:
            self._ev_pos = None
            return
        r = self.ctl.request("GET", "/api/device/sim-ev", params={"vehicle_id": d["vehicle_id"]})
        info = r.json() if r.status_code == 200 else {}
        if info.get("status") != "active":
            self._ev_pos = None
            return
        route = info["route"]
        if self._ev_pos is None or self._ev_pos[0] not in route:
            self._ev_pos = (info["edge"] or route[0], info["progress"] if info["edge"] else 0.0)
        edge, prog = self._ev_pos                           # true progress; the value REPORTED is clamped below
        e = self.topo.edges[edge]
        ap = self.topo.approaches.get(edge)
        # An ambulance is not magic: it slows to weave past a queue and it waits at the stop line for a green.
        slow = 1.0 / (1.0 + QUEUE_DRAG * self.queues[edge]) if ap else 1.0
        if edge in self.stalled:                            # a broken-down vehicle: it has to creep around it
            slow *= STALL_DRAG
        prog += slow / (e.length / e.speed * d["pace"])
        waiting = False
        if ap and prog >= STOP_LINE and self.fsms[ap.intersection].lamps()[ap.movement] != "G":
            prog, waiting = min(prog, STOP_LINE), True
        if waiting:
            self.ev_stats["wait_s"] += 1.0
            if not self._ev_waiting:
                self.ev_stats["stops"] += 1
        self._ev_waiting = waiting
        if prog >= 1.0:
            nxt = route.index(edge) + 1
            if nxt < len(route):
                edge, prog = route[nxt], max(prog - 1.0, 0.02)
        self._ev_pos = (edge, prog)
        report = min(prog, 0.97)
        if self.ev_sink:
            self.ev_sink(edge, report)
        else:
            self.cam.request("POST", "/api/device/ev-position",
                             {"vehicle_id": d["vehicle_id"], "edge": edge, "progress": report, "source": "simulated"})
