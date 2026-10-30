"""SUMO bridge: the real backend driven by a fake TraCI (no SUMO needed), plus the pure helpers."""
import pytest
from fastapi.testclient import TestClient

from app import sumogen
from app.config import ROOT, Settings
from app.core.topology import Topology
from app.main import create_app
from simulator import sumo_bridge as sb

TRI = ROOT / "config" / "topology.yaml"


# ---- pure helpers ---------------------------------------------------------------------------------------------------
def test_ryg_state_maps_axes_and_lets_turns_yield():
    by_axis = {"A": [(0, "s"), (1, "l")], "B": [(2, "s"), (3, "r")]}
    assert sb.ryg_state(4, by_axis, {"A": "G", "B": "R"}) == "Ggrr"
    assert sb.ryg_state(4, by_axis, {"A": "Y", "B": "R"}) == "yyrr"
    assert sb.ryg_state(4, by_axis, {"A": "R", "B": "G"}) == "rrGG"
    assert sb.ryg_state(4, by_axis, {"A": "R", "B": "R"}) == "rrrr"


def test_link_tables_group_links_by_the_axis_of_the_approach_they_leave():
    axes = {"W1": ("I1", "B"), "L21": ("I1", "A")}
    conns = [("I1", 0, "L21", "s"), ("I1", 1, "L21", "l"), ("I1", 2, "W1", "R"), ("I1", 3, "O1", "s")]
    n, by_axis = sb.link_tables(conns, axes)["I1"]
    assert n == 4 and by_axis == {"A": [(0, "s"), (1, "l")], "B": [(2, "R")]}


def test_progress_is_clamped_below_one():
    assert sb.ev_progress(50, 100) == 0.5
    assert sb.ev_progress(100, 100) == sb.REPORT_MAX
    assert sb.ev_progress(5, 0) == 0.0


def test_remaining_route_starts_at_the_current_edge():
    assert sb.remaining_route(["W1", "L12", "L23", "O3"], "L12") == ["L12", "L23", "O3"]
    assert sb.remaining_route(["W1", "L13", "O3"], "L12") is None


# ---- bridge against the real backend ----------------------------------------------------------------------------------
class _NS:
    pass


class FakeTraci:
    """Just enough of TraCI: vehicles are placed by the test, lamps written by the bridge are recorded."""
    def __init__(self, topo):
        self.world = {}                                   # vehicle id -> dict(edge, pos, speed, wait)
        self.states, self.added, self.routes, self.reroutes, self.removed = [], [], {}, [], []
        self.steps = 0
        self.edge, self.vehicle, self.lane, self.route, self.trafficlight = _NS(), _NS(), _NS(), _NS(), _NS()
        self.edge.getLastStepVehicleIDs = lambda e: [v for v, d in self.world.items() if d["edge"] == e]
        self.vehicle.getSpeed = lambda v: self.world[v]["speed"]
        self.vehicle.getWaitingTime = lambda v: self.world[v]["wait"]
        self.vehicle.getIDList = lambda: list(self.world)
        self.vehicle.getRoadID = lambda v: self.world[v]["edge"]
        self.vehicle.getLanePosition = lambda v: self.world[v]["pos"]
        self.vehicle.remove = lambda v: (self.world.pop(v), self.removed.append(v))
        self.vehicle.setRoute = lambda v, r: self.reroutes.append((v, r))
        self.vehicle.add = self._add
        self.lane.getLength = lambda lane: 100.0
        self.route.add = lambda rid, edges: self.routes.__setitem__(rid, list(edges))
        self.trafficlight.setRedYellowGreenState = lambda tl, s: self.states.append((tl, s))
        self.trafficlight.setPhaseDuration = lambda tl, d: None

    def _add(self, vid, rid, typeID=None, departSpeed=None):
        self.added.append((vid, self.routes[rid], typeID))
        self.world[vid] = {"edge": self.routes[rid][0], "pos": 1.0, "speed": 0.0, "wait": 0.0}

    def simulationStep(self):
        self.steps += 1


def _connections(topo):
    """Two links per approach (straight, left), numbered per intersection like netconvert does."""
    out, nxt = [], {i: 0 for i in topo.intersections}
    for a, ap in topo.approaches.items():
        i = ap.intersection
        out += [(i, nxt[i], a, "s"), (i, nxt[i] + 1, a, "l")]
        nxt[i] += 2
    return out


class World:
    def __init__(self):
        holder = {}
        s = Settings(db_path=":memory:", topology_path=str(TRI), operator_password="pw", controller_autostart=False,
                     llm_enabled=False, llm_base_url="", llm_model="", llm_api_key="")
        self.app = create_app(s, lambda: holder["b"].now_ms / 1000 if "b" in holder else 0.0)
        self.ctx, self.client = self.app.state.ctx, TestClient(self.app)
        topo = self.ctx.topology
        self.traci = FakeTraci(topo)
        self.bridge = sb.SumoBridge(self.client, topo, self.ctx.device_specs, self.traci, sumogen.build(topo, "t")["map"], _connections(topo))
        holder["b"] = self.bridge
        self.bridge.step(1000)

    def second(self):
        c = self.ctx
        c.scenarios.tick(); c.incidents.tick(); c.ev.tick(); c.roadusers.tick(); c.controller.cycle()
        self.bridge.step(1000)

    def hdr(self):
        t = self.client.post("/api/auth/login", json={"username": "operator", "password": "pw"}).json()["token"]
        return {"Authorization": f"Bearer {t}"}


def queue(world, edge, n):
    for k in range(n):
        world.traci.world[f"q{edge}{k}"] = {"edge": edge, "pos": 10 + k, "speed": 0.0, "wait": 12.0 + k}


def test_queued_vehicles_reach_the_backend_as_counts():
    w = World()
    queue(w, "W1", 3)
    w.traci.world["moving"] = {"edge": "W1", "pos": 50, "speed": 9.0, "wait": 0.0}      # moving cars are not queue
    w.second()
    row = w.ctx.store.traffic["W1"]
    assert row["count"] == 3 and row["source"] == "simulated"
    assert w.ctx.store.traffic["L12"]["count"] == 0


def test_queue_count_is_capped_at_the_approach_capacity():
    w = World()
    queue(w, "W1", 20)
    w.second()
    assert w.ctx.store.traffic["W1"]["count"] == w.ctx.topology.approaches["W1"].capacity


def test_backend_commands_become_sumo_lamps_and_are_never_unsafe():
    w = World()
    queue(w, "W1", 6)                                      # demand on I1's B axis only
    for _ in range(40):
        w.second()
    assert w.ctx.store.actual["I1"]["state"]["lamps"]["B"] in ("G", "Y")        # the backend steered the device to B
    assert not w.bridge.unsafe_seen
    i1 = [s for tl, s in w.traci.states if tl == "I1"]
    assert i1 and all(len(s) == 6 for s in i1)                                   # 3 approaches x 2 links
    by_axis = w.bridge.links["I1"][1]
    assert by_axis["A"] and by_axis["B"]
    open_ = lambda s, axis: any(s[i] in "Ggy" for i, _ in by_axis[axis])
    assert any(open_(s, "B") for s in i1), "the demand axis never got a green"
    for s in i1:
        assert not (open_(s, "A") and open_(s, "B")), f"both axes open: {s}"


def test_declared_ambulance_is_inserted_on_the_backends_route_and_reported_back():
    w = World()
    r = w.client.post("/api/operator/ev", json={"vehicle_id": "EV-1", "origin": "ext_W", "destination": "ext_S"}, headers=w.hdr())
    assert r.status_code == 200
    route = r.json()["route"]
    w.second()
    assert w.traci.added == [("EV-1", route, "ev")]
    w.traci.world["EV-1"].update(edge=route[1], pos=25.0)
    w.second()
    ev = w.ctx.ev.snapshot()[0]
    assert ev["edge"] == route[1] and ev["progress"] == pytest.approx(0.25, abs=0.01)


def test_arrival_in_sumo_ends_the_backend_session():
    w = World()
    route = w.client.post("/api/operator/ev", json={"vehicle_id": "EV-1", "origin": "ext_W", "destination": "ext_S"}, headers=w.hdr()).json()["route"]
    w.second()
    w.traci.world["EV-1"].update(edge=route[-1], pos=40.0)
    w.second()
    assert w.ctx.ev.snapshot()[0]["status"] == "active"          # 0.4 along the last edge: not there yet
    del w.traci.world["EV-1"]                                    # SUMO removes the vehicle on arrival
    w.second()
    assert w.ctx.ev.snapshot()[0]["status"] == "ended"
    assert w.bridge.ev_summary()["arrived"] and w.bridge.ev_summary()["travel_s"] is not None


def test_ambulance_follows_a_backend_reroute():
    w = World()
    w.client.post("/api/operator/ev", json={"vehicle_id": "EV-1", "origin": "ext_W", "destination": "ext_S"}, headers=w.hdr())
    w.second()
    route = w.traci.routes[next(iter(w.traci.routes))]
    w.traci.world["EV-1"].update(edge=route[0], pos=30.0)
    new_route = ["W1", "L12", "L23", "O3"]
    w.bridge.ev["route"] = ["W1", "L13", "O3"]            # what SUMO is following
    s = w.ctx.ev.sessions
    next(iter(s.values())).route = new_route               # the backend decided on another way
    w.second()
    assert w.traci.reroutes and w.traci.reroutes[-1] == ("EV-1", new_route)


def test_session_end_removes_the_ambulance_from_sumo():
    w = World()
    r = w.client.post("/api/operator/ev", json={"vehicle_id": "EV-1"}, headers=w.hdr())
    sid = r.json()["session"]
    w.second()
    assert "EV-1" in w.traci.world
    w.client.post(f"/api/operator/ev/{sid}/clear", headers=w.hdr())
    w.second()
    assert "EV-1" not in w.traci.world and w.traci.removed == ["EV-1"]


def test_unsupported_scenario_ops_are_ignored_not_fatal(capsys):
    w = World()
    w.bridge.apply({"op": "pin", "approach": "W1", "count": 5})
    w.bridge.apply({"op": "pin", "approach": "W1", "count": 6})
    assert capsys.readouterr().err.count("ignored in the SUMO world") == 1


def test_camera_loss_stops_traffic_readings():
    w = World()
    queue(w, "W1", 2)
    w.bridge.apply({"op": "camera", "on": False})
    before = dict(w.ctx.store.traffic["W1"])               # the empty reading posted while the camera was still on
    w.second()
    assert w.ctx.store.traffic["W1"] == before             # nothing newer arrived: the 2 queued cars were never reported
