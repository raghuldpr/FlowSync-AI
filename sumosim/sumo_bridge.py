"""SumoWorld: drop-in replacement for simulator.tabletop.VirtualTabletop, with SUMO as the traffic world.

It speaks the same signed device API as the ESP32s and the tabletop:
  camera  -> POST /api/device/traffic        (vehicles inside each approach's camera zone)
  device  -> POST /api/device/state, GET /api/device/commands, POST /api/device/command-ack
  puppet  -> GET  /api/device/sim-commands   (scenario world commands), GET /api/device/sim-ev (route for the ambulance)
  camera  -> POST /api/device/ev-position
Every light change goes through SignalFSM (min green, yellow, all-red), exactly as the firmware does it. Nothing here
decides signals; the backend does, and this class only senses and actuates.
"""
import random
import sys

import traci

from app.core.fsm import SignalFSM
from app.device_client import DeviceClient

from . import binary

ZONE_M = 60.0          # camera zone: the last stretch before each stop line (~8 queued cars, the tabletop camera's ROI)
HALT_SPEED = 0.1       # m/s below which a vehicle counts as stopped
STEP_S = 0.5           # SUMO step length
ENTRIES = ("W1", "E2", "S3")
EXITS = ("O1", "O2", "O3")
LAMP = {"G": "G", "Y": "y", "R": "r"}


class SumoWorld:
    def __init__(self, client, topology, devices: dict, net: str, rates: dict = None, seed: int = 1, gui: bool = False,
                 label: str = "flowsync", delay_ms: int = 0):
        self.topo = topology
        self.now_ms = 0
        self.rng = random.Random(seed)
        self.default_rates = {a: 0.0 for a in topology.approaches}
        self.default_rates.update({a: 0.10 for a in ENTRIES})
        self.rates = dict(self.default_rates)
        self.rates.update(rates or {})
        self.fsms = {i: SignalFSM(topology.timing, 0) for i in topology.intersections}
        self.dev = DeviceClient(client, "intersection-1", devices["intersection-1"]["key"])
        self.cam = DeviceClient(client, "camera-1", devices["camera-1"]["key"])
        self.ctl = DeviceClient(client, "simulator-1", devices["simulator-1"]["key"]) if "simulator-1" in devices else None
        self.camera_on, self.link_up = True, True
        self.pins, self.stalled = {}, set()
        self.ev_drive, self._ev_arrived = None, False
        self.ev_stats = {"wait_s": 0.0, "stops": 0, "entered_s": None, "exited_s": None}
        self._ev_waiting = False
        self.unsafe_seen = False
        self.last_ok_ms, self._cmd_cursor = 0, None
        self._n, self._routes = 0, {}
        self._halt_since, self._depart_s, self._wait = {}, {}, {}
        self.stats = {"arrived": 0, "wait_sum": 0.0, "travel_sum": 0.0, "collisions": 0}
        self._frac = {a: 0.0 for a in topology.approaches}
        self._lane_len = {}
        self.scale_note = "SIMULATED (SUMO)"

        cmd = [binary("sumo-gui" if gui else "sumo"), "-n", net, "--step-length", str(STEP_S), "--no-warnings",
               "--no-step-log", "--seed", str(seed), "--collision.action", "warn", "--time-to-teleport", "-1",
               "--waiting-time-memory", "3600"]
        if gui:
            cmd += ["--start", "--quit-on-end", "--delay", str(delay_ms), "--window-size", "1100,760"]
        traci.start(cmd, label=label)
        self.conn = traci.getConnection(label)
        self._setup()

    # ---- setup -------------------------------------------------------------------------------------
    def _setup(self):
        c = self.conn
        c.vehicletype.copy("DEFAULT_VEHTYPE", "car")
        c.vehicletype.copy("DEFAULT_VEHTYPE", "ambulance")
        c.vehicletype.setVehicleClass("ambulance", "emergency")
        c.vehicletype.setColor("ambulance", (255, 30, 30, 255))
        c.vehicletype.setShapeClass("ambulance", "emergency")
        c.vehicletype.setSpeedFactor("ambulance", 1.2)
        c.vehicletype.setLength("ambulance", 6.5)
        for e in self.topo.edges:
            self._lane_len[e] = c.lane.getLength(f"{e}_0")
        self.tl_moves = {}
        for i in self.topo.intersections:
            moves = []
            for links in c.trafficlight.getControlledLinks(i):
                edge = links[0][0].rsplit("_", 1)[0]
                moves.append(self.topo.approaches[edge].movement)
            self.tl_moves[i] = moves
            n = len(moves)
            c.trafficlight.setProgramLogic(i, traci.trafficlight.Logic("flowsync", 0, 0, [traci.trafficlight.Phase(1e7, "r" * n)]))
        self._apply_lamps()

    def close(self):
        try:
            self.conn.close()
        except Exception:
            pass

    # ---- signals -----------------------------------------------------------------------------------
    def _apply_lamps(self):
        for i, f in self.fsms.items():
            lamps = f.lamps()
            if sum(1 for v in lamps.values() if v != "R") > 1:
                self.unsafe_seen = True
            self.conn.trafficlight.setRedYellowGreenState(i, "".join(LAMP[lamps[m]] for m in self.tl_moves[i]))

    # ---- demand --------------------------------------------------------------------------------------
    def _route(self, src: str, dst: str):
        key = (src, dst)
        if key not in self._routes:
            try:
                self._routes[key] = list(self.conn.simulation.findRoute(src, dst, "car").edges)
            except traci.TraCIException:
                self._routes[key] = []
        return self._routes[key]

    def _spawn(self, dt_s: float):
        for a, rate in self.rates.items():
            if rate <= 0:
                continue
            self._frac[a] += rate * dt_s
            while self._frac[a] >= 1:
                self._frac[a] -= 1
                if self.conn.edge.getLastStepVehicleNumber(a) > 24:       # entrance backed up: drop, like a full lane
                    continue
                src_node = self.topo.edges[a].src
                dests = [x for x in EXITS if self.topo.edges[x].dst != src_node and self._route(a, x)]
                if not dests:
                    continue
                edges = self._route(a, self.rng.choice(dests))
                self._n += 1
                rid, vid = f"r{self._n}", f"v{self._n}"
                self.conn.route.add(rid, edges)
                self.conn.vehicle.add(vid, rid, typeID="car", departLane="best", departSpeed="max")
                self.conn.vehicle.setColor(vid, (self.rng.randint(120, 255), self.rng.randint(120, 255), self.rng.randint(120, 255), 255))

    # ---- one simulated second ---------------------------------------------------------------------------
    def step(self, dt_ms: int = 1000):
        t = 0
        while t < dt_ms:
            step_ms = int(STEP_S * 1000)
            self.now_ms += step_ms
            t += step_ms
            backend_ok = (self.now_ms - self.last_ok_ms) < self.topo.timing.backend_lost_ms
            for f in self.fsms.values():
                f.set_backend_ok(backend_ok)
                f.tick(self.now_ms)
            self._apply_lamps()
            self._spawn(STEP_S)
            self.conn.simulation.step()
            self._track()
        if self.link_up:
            self._communicate()
        self._control()

    def _track(self):
        c, now_s = self.conn, self.now_ms / 1000
        for v in c.simulation.getDepartedIDList():
            self._depart_s[v] = now_s
        for v in c.vehicle.getIDList():
            self._wait[v] = c.vehicle.getAccumulatedWaitingTime(v)
        for v in c.simulation.getArrivedIDList():
            if v == "EV-1":
                self.ev_stats["exited_s"] = now_s
                continue
            if v.startswith("pin") or v.startswith("stall"):
                continue
            self.stats["arrived"] += 1
            self.stats["wait_sum"] += self._wait.pop(v, 0.0)
            self.stats["travel_sum"] += now_s - self._depart_s.pop(v, now_s)
        self.stats["collisions"] += c.simulation.getCollidingVehiclesNumber()

    # ---- sensing --------------------------------------------------------------------------------------
    def readings(self) -> list:
        c, now_s, out = self.conn, self.now_ms / 1000, []
        for a in self.topo.approaches:
            lane_len = self._lane_len[a]
            count, longest = 0, 0.0
            for v in c.edge.getLastStepVehicleIDs(a):
                if c.vehicle.getLanePosition(v) < lane_len - ZONE_M:
                    continue
                count += 1
                if c.vehicle.getSpeed(v) < HALT_SPEED:
                    longest = max(longest, now_s - self._halt_since.setdefault(v, now_s))
                else:
                    self._halt_since.pop(v, None)
            out.append({"approach": a, "count": count, "stationary_s": round(longest, 1)})
        live = set(c.vehicle.getIDList())
        for v in [v for v in self._halt_since if v not in live]:
            del self._halt_since[v]
        return out

    def queue_total(self) -> int:
        return sum(r["count"] for r in self.readings())

    # ---- backend conversation ----------------------------------------------------------------------------
    def _communicate(self):
        if self.camera_on:
            r = self.cam.request("POST", "/api/device/traffic", {"source": "simulated", "readings": self.readings()})
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

    def _control(self):
        if self.ctl is None:
            return
        try:
            r = self.ctl.request("GET", "/api/device/sim-commands", params=None if self._cmd_cursor is None else {"after": self._cmd_cursor})
            if r.status_code == 200:
                body = r.json()
                self._cmd_cursor = body["latest"]
                for c in body["commands"]:
                    self.apply(c["cmd"])
            self._topup_pins()
            self._drive_ev()
        except traci.TraCIException as e:
            print("sumo bridge:", e, file=sys.stderr)
        except Exception:                                  # the puppeteer channel must never break the simulation
            pass

    # ---- scenario world commands (same ops as the tabletop) ---------------------------------------------------------
    def apply(self, cmd: dict):
        op = cmd.get("op")
        if op == "reset":
            self._clear_blockers()
            self.pins.clear(); self.stalled.clear()
            self.camera_on, self.link_up, self.ev_drive = True, True, None
            self.rates = dict(self.default_rates)
        elif op == "rates":
            if "all" in cmd:
                for a in ENTRIES:
                    self.rates[a] = float(cmd["all"])
            else:
                self.rates[cmd["approach"]] = float(cmd["rate"])
        elif op == "pin":
            self._remove_blockers(f"pin_{cmd['approach']}_")
            if cmd.get("count") is None:
                self.pins.pop(cmd["approach"], None)
            else:
                self.pins[cmd["approach"]] = int(cmd["count"])
                self._place_blockers(cmd["approach"], int(cmd["count"]), "pin")
        elif op == "stall":
            self._remove_blockers(f"stall_{cmd['approach']}_")
            if cmd.get("on", True):
                self.stalled.add(cmd["approach"])
                self._place_blockers(cmd["approach"], 1, "stall")
            else:
                self.stalled.discard(cmd["approach"])
        elif op == "camera":
            self.camera_on = bool(cmd["on"])
        elif op == "link":
            self.link_up = bool(cmd["on"])
        elif op == "drive_ev":
            if cmd.get("on", True):
                self.ev_drive = {"vehicle_id": cmd["vehicle_id"]}
                self._ev_arrived = False
            else:
                self.ev_drive = None

    def _place_blockers(self, edge: str, n: int, kind: str):
        """A breakdown (stall: one vehicle stopped in the right lane) or a jam (pin: a short crawling queue, see _topup_pins).
        Both stay in lane 0, so an ambulance can overtake in lane 1 as it would on a real two-lane road."""
        c = self.conn
        exits = [e for e in self.topo.edges if self.topo.edges[e].src == self.topo.edges[edge].dst and self.topo.edges[e].dst != self.topo.edges[edge].src]
        if not exits:
            return
        length = self._lane_len[edge]
        for k in range(1 if kind == "stall" else n):
            vid = f"{kind}_{edge}_{self._n}"
            self._n += 1
            pos = length - 25.0 if kind == "stall" else length - 8.0 - 8.0 * k
            c.route.add(f"{vid}_r", [edge, exits[0]])
            c.vehicle.add(vid, f"{vid}_r", typeID="car", departLane="0", departPos=str(max(5.0, pos)), departSpeed="0")
            c.vehicle.setColor(vid, (230, 150, 0, 255) if kind == "pin" else (200, 0, 200, 255))
            c.vehicle.setSpeed(vid, 0.0 if kind == "stall" else 1.0)
            if kind == "pin":
                c.vehicle.setMaxSpeed(vid, 1.0)

    def _topup_pins(self):
        """A scripted jam keeps crawling toward the stop line and is topped up as vehicles leave it (like the tabletop's pinned
        queue). Crawling, not parked: a parked car is a breakdown, and the backend would raise a stalled_vehicle incident."""
        c = self.conn
        for edge, n in self.pins.items():
            length = self._lane_len[edge]
            in_zone = sum(1 for v in c.edge.getLastStepVehicleIDs(edge) if c.vehicle.getLanePosition(v) >= length - ZONE_M)
            exits = [e for e in self.topo.edges if self.topo.edges[e].src == self.topo.edges[edge].dst and self.topo.edges[e].dst != self.topo.edges[edge].src]
            for _ in range(max(0, n + 1 - in_zone)):      # one spare so the reading never dips below n
                vid = f"pin_{edge}_{self._n}"
                self._n += 1
                c.route.add(f"{vid}_r", [edge, exits[0]])
                c.vehicle.add(vid, f"{vid}_r", typeID="car", departLane="0", departPos=str(length - ZONE_M + 1.0), departSpeed="1")
                c.vehicle.setColor(vid, (230, 150, 0, 255))
                c.vehicle.setMaxSpeed(vid, 1.0)
        for v in c.vehicle.getIDList():                           # crawlers that have left their edge drive normally again
            if v.startswith("pin_") and c.vehicle.getRoadID(v) not in self.pins:
                c.vehicle.setMaxSpeed(v, 11.1)
                c.vehicle.setSpeed(v, -1)

    def _remove_blockers(self, prefix: str):
        for v in self.conn.vehicle.getIDList():
            if v.startswith(prefix):
                self.conn.vehicle.remove(v)

    def _clear_blockers(self):
        for v in self.conn.vehicle.getIDList():
            if v.startswith(("pin_", "stall_")):
                self.conn.vehicle.remove(v)

    # ---- the ambulance ---------------------------------------------------------------------------------------------------
    def _drive_ev(self):
        d = self.ev_drive
        if not d or self._ev_arrived:
            return
        c, vid = self.conn, d["vehicle_id"]
        r = self.ctl.request("GET", "/api/device/sim-ev", params={"vehicle_id": vid})
        info = r.json() if r.status_code == 200 else {}
        if info.get("status") != "active":
            return
        route = info["route"]
        if "EV-1" not in c.vehicle.getIDList():
            if self.ev_stats["entered_s"] is not None:        # it was here and has gone: arrived
                self._ev_arrived = True
                return
            c.route.add(f"ev_{self._n}", route)
            c.vehicle.add("EV-1", f"ev_{self._n}", typeID="ambulance", departLane="best", departSpeed="max")
            self._n += 1
            self.ev_stats["entered_s"] = self.now_ms / 1000
            return
        edge = c.vehicle.getRoadID("EV-1")
        if edge.startswith(":") or edge not in self.topo.edges:
            return                                              # inside a junction: keep the last report
        if edge in route:
            tail = route[route.index(edge):]
            if tail != list(c.vehicle.getRoute("EV-1"))[c.vehicle.getRouteIndex("EV-1"):]:
                try:
                    c.vehicle.setRoute("EV-1", tail)
                except traci.TraCIException:
                    pass
        speed = c.vehicle.getSpeed("EV-1")
        waiting = speed < HALT_SPEED
        if waiting:
            self.ev_stats["wait_s"] += 1.0
            if not self._ev_waiting:
                self.ev_stats["stops"] += 1
        self._ev_waiting = waiting
        progress = min(c.vehicle.getLanePosition("EV-1") / self._lane_len[edge], 0.97)
        self.cam.request("POST", "/api/device/ev-position",
                         {"vehicle_id": vid, "edge": edge, "progress": progress, "source": "simulated"})

    def ev_where(self) -> str:
        if "EV-1" not in self.conn.vehicle.getIDList():
            return "-"
        return f"{self.conn.vehicle.getRoadID('EV-1')}@{self.conn.vehicle.getSpeed('EV-1'):.0f}m/s"

    # ---- results ----------------------------------------------------------------------------------------------------------------
    def summary(self) -> dict:
        n = self.stats["arrived"]
        return {"arrived": n, "mean_wait_s": self.stats["wait_sum"] / n if n else 0.0,
                "mean_travel_s": self.stats["travel_sum"] / n if n else 0.0, "collisions": self.stats["collisions"],
                "ev_wait_s": self.ev_stats["wait_s"], "ev_stops": self.ev_stats["stops"]}
