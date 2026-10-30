"""SUMO world for the FlowSync backend:  python -m simulator.sumo_bridge [--url http://127.0.0.1:8000] [--gui]

Plays the role the virtual tabletop plays, but the road world is SUMO. It talks to the backend through the SAME signed
device API (so the backend cannot tell the difference), and the signal decisions stay the backend's:

  SUMO -> backend   queued vehicles per approach (as readings, source "simulated"), the ambulance's edge and progress.
  backend -> SUMO   signal commands go to the same reference SignalFSM the firmware mirrors (min green, yellow, all-red),
                    and only that FSM's lamps are written into SUMO. The bridge never switches a signal on its own.
  backend -> SUMO   when the backend has an active ambulance session, EV-1 is inserted on the route the backend chose and
                    follows it if the backend reroutes.

Build the network first:  python -m app.sumogen --out sumo/triangle --build   (see docs/SUMO.md)
Scenario world commands that only make sense for the queue model (pin, stall, rates) are logged and ignored.
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

import simulator  # noqa: F401  (puts backend/ on sys.path)
from app.config import ROOT, Settings, load_yaml
from app.core.fsm import SignalFSM
from app.core.topology import Topology
from app.device_client import DeviceClient

EV_ID = "EV-1"
REPORT_MAX = 0.97          # the backend treats progress 1.0 as "past the edge"; the tabletop caps its reports the same way
HALT_SPEED = 0.1           # m/s: a vehicle slower than this counts as queued (SUMO's own definition of halting)
YIELDING = ("l", "L", "t", "T")


# ---- pure helpers (unit-tested without SUMO) ------------------------------------------------------------------------
def axis_of_edge(mapping: dict) -> dict:
    """{approach edge: (intersection, axis)} from flowsync_map.json."""
    return {a: (i, axis) for i, axes in mapping["intersections"].items() for axis, lst in axes.items() for a in lst}


def link_tables(connections, axes: dict) -> dict:
    """connections: iterable of (tl_id, link_index, in_edge, direction). Returns {tl: (n_links, {'A': [(idx, dir)], 'B': [...]})}.
    A link belongs to the axis of the approach it leaves; links from an edge that is not an approach are ignored."""
    out = {}
    for tl, idx, edge, direction in connections:
        n, by_axis = out.setdefault(tl, [0, {"A": [], "B": []}])
        out[tl][0] = max(n, idx + 1)
        if edge in axes and axes[edge][0] == tl:
            by_axis[axes[edge][1]].append((idx, direction))
    return {tl: (n, by_axis) for tl, (n, by_axis) in out.items()}


def ryg_state(n_links: int, by_axis: dict, lamps: dict) -> str:
    """SUMO signal state string for the FSM's lamps. Left turns and U-turns get lowercase green: they yield to oncoming traffic."""
    s = ["r"] * n_links
    for axis, links in by_axis.items():
        lamp = lamps.get(axis, "R")
        for idx, direction in links:
            s[idx] = ("g" if direction in YIELDING else "G") if lamp == "G" else "y" if lamp == "Y" else "r"
    return "".join(s)


def ev_progress(lane_pos: float, lane_len: float) -> float:
    return max(0.0, min(REPORT_MAX, lane_pos / lane_len)) if lane_len > 0 else 0.0


def remaining_route(route: list, current_edge: str):
    """The part of the backend's route SUMO still has to drive, or None if the vehicle is not on it."""
    return route[route.index(current_edge):] if current_edge in route else None


def read_connections(net_file: str):
    import sumolib
    net = sumolib.net.readNet(net_file)
    for e in net.getEdges():
        for conns in e.getOutgoing().values():
            for c in (conns if isinstance(conns, list) else [conns]):
                if c.getTLSID() and c.getTLLinkIndex() >= 0:
                    yield c.getTLSID(), c.getTLLinkIndex(), e.getID(), c.getDirection()


# ---- the bridge -------------------------------------------------------------------------------------------------------
class SumoBridge:
    def __init__(self, client, topology: Topology, devices: dict, traci, mapping: dict, connections, tick_ms: int = 100):
        self.topo, self.traci, self.tick_ms = topology, traci, tick_ms
        self.axes = axis_of_edge(mapping)
        self.links = link_tables(connections, self.axes)
        missing = set(topology.intersections) - set(self.links)
        if missing:
            raise ValueError(f"SUMO network has no traffic light for: {', '.join(sorted(missing))}")
        self.now_ms = 0
        self.fsms = {i: SignalFSM(topology.timing, 0) for i in topology.intersections}
        self.dev = DeviceClient(client, "intersection-1", devices["intersection-1"]["key"])
        self.cam = DeviceClient(client, "camera-1", devices["camera-1"]["key"])
        self.ctl = DeviceClient(client, "simulator-1", devices["simulator-1"]["key"]) if "simulator-1" in devices else None
        self.applied = {}                    # intersection -> last state string written to SUMO
        self.camera_on, self.link_up = True, True
        self.last_ok_ms, self.unsafe_seen = 0, False
        self._cursor = None
        self._warned_ops = set()
        self.ev = {"route": None, "spawned_s": None, "arrived_s": None, "wait_s": 0.0, "stops": 0, "seen": False, "waiting": False}
        self._lane_len = {}

    # ---- SUMO side ---------------------------------------------------------------------------------------------
    def _apply_lamps(self):
        t = self.traci.trafficlight
        for i, f in self.fsms.items():
            lamps = f.lamps()
            if sum(1 for v in lamps.values() if v != "R") > 1:
                self.unsafe_seen = True
            n, by_axis = self.links[i]
            state = ryg_state(n, by_axis, lamps)
            if state != self.applied.get(i):
                t.setRedYellowGreenState(i, state)
                t.setPhaseDuration(i, 86400)             # stop SUMO's own program advancing over our state
                self.applied[i] = state

    def _readings(self):
        tc, out = self.traci, []
        for a, ap in self.topo.approaches.items():
            ids = tc.edge.getLastStepVehicleIDs(a)
            halted = [v for v in ids if tc.vehicle.getSpeed(v) < HALT_SPEED]
            out.append({"approach": a, "count": min(len(halted), ap.capacity),
                        "stationary_s": round(max((tc.vehicle.getWaitingTime(v) for v in halted), default=0.0), 1)})
        return out

    def _lane_length(self, edge: str) -> float:
        if edge not in self._lane_len:
            self._lane_len[edge] = self.traci.lane.getLength(f"{edge}_0")
        return self._lane_len[edge]

    # ---- time ---------------------------------------------------------------------------------------------------
    def step(self, dt_ms: int = 1000):
        t = 0
        while t < dt_ms:
            self.now_ms += self.tick_ms
            t += self.tick_ms
            backend_ok = (self.now_ms - self.last_ok_ms) < self.topo.timing.backend_lost_ms
            for f in self.fsms.values():
                f.set_backend_ok(backend_ok)
                f.tick(self.now_ms)
            self._apply_lamps()
            self.traci.simulationStep()
            self._track_ev()
        if self.link_up:
            self._communicate()
        self._control()

    def _communicate(self):
        if self.camera_on:
            r = self.cam.request("POST", "/api/device/traffic", {"source": "simulated", "readings": self._readings()})
            if r.status_code != 200:
                return
        self.dev.request("POST", "/api/device/state", {"origin": "simulated", "intersections": {
            i: f.state(self.now_ms) for i, f in self.fsms.items()}})
        r = self.dev.request("GET", "/api/device/commands")
        if r.status_code != 200:
            return
        self.last_ok_ms = self.now_ms
        for cmd in r.json()["commands"]:
            ok, why = self.fsms[cmd["intersection"]].receive_command(cmd["seq"], cmd["target"], cmd["valid_for_ms"], self.now_ms)
            if ok or why != "stale_seq":
                self.dev.request("POST", "/api/device/command-ack", {
                    "seq": cmd["seq"], "intersection": cmd["intersection"], "accepted": ok, "reason": why})

    # ---- scenario control channel + ambulance ----------------------------------------------------------------------
    def _control(self):
        if self.ctl is None:
            return
        try:
            r = self.ctl.request("GET", "/api/device/sim-commands", params=None if self._cursor is None else {"after": self._cursor})
            if r.status_code == 200:
                body = r.json()
                self._cursor = body["latest"]
                for c in body["commands"]:
                    self.apply(c["cmd"])
            self._sync_ev()
        except Exception as e:                            # the puppeteer channel must never break the simulation
            if "ctl" not in self._warned_ops:
                self._warned_ops.add("ctl")
                print(f"warning: control channel error: {e!r}", file=sys.stderr)

    def apply(self, cmd: dict):
        op = cmd.get("op")
        if op == "camera":
            self.camera_on = bool(cmd["on"])
        elif op == "link":
            self.link_up = bool(cmd["on"])
        elif op in ("reset", "drive_ev"):
            pass                                          # the ambulance follows the backend's session, not a command
        elif op not in self._warned_ops:
            self._warned_ops.add(op)
            print(f"note: scenario command '{op}' is for the queue model and is ignored in the SUMO world", file=sys.stderr)

    def _sync_ev(self):
        tc, ev = self.traci, self.ev
        r = self.ctl.request("GET", "/api/device/sim-ev", params={"vehicle_id": EV_ID})
        info = r.json() if r.status_code == 200 else {}
        present = EV_ID in tc.vehicle.getIDList()
        if info.get("status") != "active":
            if present:
                tc.vehicle.remove(EV_ID)                 # the backend ended the session: the ambulance is done
            return
        route = info["route"]
        if ev["arrived_s"] is not None and not ev.get("final_sent"):
            ev["final_sent"] = True                      # SUMO removes the vehicle at the end of its last edge, between two reports
            self.cam.request("POST", "/api/device/ev-position", {"vehicle_id": EV_ID, "edge": ev["route"][-1], "progress": REPORT_MAX, "source": "simulated"})
            return
        if not present and not ev["seen"]:
            tc.route.add(f"ev_{int(self.now_ms)}", route)
            tc.vehicle.add(EV_ID, f"ev_{int(self.now_ms)}", typeID="ev", departSpeed="max")
            ev["route"], ev["spawned_s"], ev["seen"] = list(route), self.now_ms / 1000, True
            return
        if not present:
            return
        edge = tc.vehicle.getRoadID(EV_ID)
        if edge.startswith(":"):                          # inside a junction: report again once it is on a road
            return
        if route != ev["route"]:
            rest = remaining_route(route, edge)
            if rest:
                tc.vehicle.setRoute(EV_ID, rest)
                ev["route"] = list(route)
        if edge in self.topo.edges:
            self.cam.request("POST", "/api/device/ev-position", {
                "vehicle_id": EV_ID, "edge": edge, "source": "simulated",
                "progress": ev_progress(tc.vehicle.getLanePosition(EV_ID), self._lane_length(edge))})

    def _track_ev(self):
        """Per tick: waiting time and the arrival moment (the vehicle leaves SUMO when it reaches its last edge)."""
        tc, ev = self.traci, self.ev
        if not ev["seen"] or ev["arrived_s"] is not None:
            return
        if EV_ID in tc.vehicle.getIDList():
            waiting = tc.vehicle.getSpeed(EV_ID) < HALT_SPEED
            if waiting:
                ev["wait_s"] += self.tick_ms / 1000
                if not ev["waiting"]:
                    ev["stops"] += 1
            ev["waiting"] = waiting
        else:
            ev["arrived_s"] = self.now_ms / 1000

    def ev_summary(self) -> dict:
        ev = self.ev
        done = ev["arrived_s"] is not None
        return {"ambulance_inserted": ev["seen"], "arrived": done,
                "travel_s": round(ev["arrived_s"] - ev["spawned_s"], 1) if done else None,
                "wait_s": round(ev["wait_s"], 1), "stops": ev["stops"], "unsafe_lamps_seen": self.unsafe_seen}


# ---- command line ---------------------------------------------------------------------------------------------------
def _traci():
    try:
        import traci
    except ImportError:
        home = os.environ.get("SUMO_HOME")
        if not home:
            sys.exit("traci not found: install SUMO and set SUMO_HOME (docs/SUMO.md)")
        sys.path.append(str(Path(home) / "tools"))
        import traci
    return traci


def sumo_binary(gui: bool) -> str:
    name = "sumo-gui" if gui else "sumo"
    home = os.environ.get("SUMO_HOME")
    for cand in ([Path(home) / "bin" / f"{name}.exe", Path(home) / "bin" / name] if home else []):
        if cand.exists():
            return str(cand)
    import shutil
    found = shutil.which(name)
    if not found:
        sys.exit(f"{name} not found: install SUMO and set SUMO_HOME (docs/SUMO.md)")
    return found


def main():
    import httpx
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="http://127.0.0.1:8000")
    p.add_argument("--sumocfg", default=str(ROOT / "sumo" / "triangle" / "flowsync.sumocfg"))
    p.add_argument("--gui", action="store_true", help="open sumo-gui and follow it live")
    p.add_argument("--speed", type=float, default=1.0, help="wall-clock speed factor (sim seconds per real second)")
    p.add_argument("--duration", type=int, default=0, help="stop after this many simulated seconds (0 = run until Ctrl+C)")
    a = p.parse_args()

    cfg = Path(a.sumocfg).resolve()
    mapping = json.loads((cfg.parent / "flowsync_map.json").read_text(encoding="utf-8"))
    net = cfg.parent / mapping["net_file"]
    if not net.exists():
        sys.exit(f"{net} not found: run  python -m app.sumogen --out {cfg.parent} --build")
    settings = Settings()
    topo = Topology.load(settings.topology_path)
    devices = load_yaml(settings.devices_path)["devices"]
    traci = _traci()
    traci.start([sumo_binary(a.gui), "-c", str(cfg), "--step-length", str(0.1), "--no-step-log", "true", "--no-warnings", "true",
                 *(["--start", "--quit-on-end"] if a.gui else [])])
    http = httpx.Client(base_url=a.url, timeout=3)
    bridge = SumoBridge(http, topo, devices, traci, mapping, list(read_connections(str(net))))
    print("SUMO world connected to " + a.url + "; Ctrl+C to stop", flush=True)
    bridge.step(1000)                        # the first control poll fixes the command cursor
    try:
        while not a.duration or bridge.now_ms < a.duration * 1000:
            bridge.step(1000)
            ev = bridge.ev_summary()
            print(f"t={bridge.now_ms / 1000:6.1f}s  " + "  ".join(f"{i}:{f.phase[:1]}{f.axis}" for i, f in bridge.fsms.items())
                  + f"  vehicles={traci.vehicle.getIDCount()}  EV={'arrived ' + str(ev['travel_s']) + 's' if ev['arrived'] else 'driving' if EV_ID in traci.vehicle.getIDList() else '-'}",
                  flush=True)
            time.sleep(1.0 / a.speed)
    except KeyboardInterrupt:
        pass
    finally:
        print("summary " + json.dumps(bridge.ev_summary()), flush=True)
        traci.close()


if __name__ == "__main__":
    main()
