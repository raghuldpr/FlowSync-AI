"""City-scale grid (12 simulated intersections): the SAME controller, state machine, routing, incidents and scenarios."""
import itertools
import time

import pytest
import yaml
from fastapi.testclient import TestClient

from app import gridgen
from app.config import ROOT, Settings, load_yaml
from app.core.routing import dijkstra, edge_costs
from app.core.topology import Topology
from app.main import create_app
from simulator.tabletop import VirtualTabletop

GRID = ROOT / "config" / "topology.grid.yaml"
GRID_DEV = ROOT / "config" / "devices.grid.yaml"


@pytest.fixture(scope="module")
def topo():
    return Topology.load(str(GRID))


class City:
    def __init__(self, rates_all=0.06, seed=3):
        holder = {}
        clock = lambda: holder["sim"].now_ms / 1000 if "sim" in holder else 0.0
        s = Settings(db_path=":memory:", topology_path=str(GRID), devices_path=str(GRID_DEV), operator_password="pw",
                     controller_autostart=False, llm_enabled=False, llm_base_url="", llm_model="", llm_api_key="")
        self.app = create_app(s, clock)
        self.ctx, self.client = self.app.state.ctx, TestClient(self.app)
        self.sim = VirtualTabletop(self.client, self.ctx.topology, self.ctx.device_specs,
                                   rates={a: rates_all for a in self.ctx.topology.approaches}, seed=seed)
        holder["sim"] = self.sim
        self.sim.step(1000)

    def second(self):
        c = self.ctx
        c.scenarios.tick(); c.incidents.tick(); c.ev.tick(); c.roadusers.tick(); c.controller.cycle()
        self.sim.step(1000)

    def hdr(self):
        t = self.client.post("/api/auth/login", json={"username": "operator", "password": "pw"}).json()["token"]
        return {"Authorization": f"Bearer {t}"}


# ---- the generated network ----------------------------------------------------------------------------------------------
def test_checked_in_grid_files_match_the_generator():
    raw = gridgen.build(3, 4)
    assert yaml.safe_load(GRID.read_text(encoding="utf-8")) == yaml.safe_load(yaml.safe_dump(raw, sort_keys=False, width=140)), \
        "run: python -m app.gridgen"
    assert load_yaml(str(GRID_DEV))["devices"]["intersection-1"]["intersections"] == raw["intersections"]


def test_grid_structure(topo):
    assert (len(topo.intersections), len(topo.edges), len(topo.approaches)) == (12, 62, 48)
    assert all(len(e) <= 16 for e in topo.edges) and all(len(i) <= 8 for i in topo.intersections), "ids must fit the API's field limits"
    for i in topo.intersections:
        axes = {a.movement for a in topo.approaches.values() if a.intersection == i}
        assert axes == {"A", "B"}, f"{i} needs both axes"
    for a in topo.approaches.values():
        for d in a.downstream:
            assert d in topo.approaches and topo.approaches[d].intersection != a.intersection, "downstream = approaches at OTHER intersections"
    ext = [n for n in topo.nodes if n not in topo.intersections]
    assert len(ext) == 14
    for o, d in itertools.permutations(ext, 2):
        assert dijkstra(topo, edge_costs(topo, {}, set()), o, d) is not None, (o, d)


def test_layout_fits_the_map_without_overlaps(topo):
    pts = topo.layout
    assert all(30 <= x <= 770 and 20 <= y <= 500 for x, y in pts.values())
    for (a, pa), (b, pb) in itertools.combinations(pts.items(), 2):
        assert abs(pa[0] - pb[0]) + abs(pa[1] - pb[1]) >= 80, (a, b)


def test_generator_bounds_and_other_sizes():
    for r, c in ((2, 2), (4, 5)):
        t = Topology(gridgen.build(r, c))
        assert len(t.intersections) == r * c
        assert dijkstra(t, edge_costs(t, {}, set()), "ext_W1", f"ext_E{r}") is not None
    assert gridgen.main(["--rows", "9", "--cols", "9", "--out", "x", "--devices-out", "y"]) == 2


# ---- running on the grid -----------------------------------------------------------------------------------------------------
def test_all_twelve_intersections_run_adaptively_and_safely_within_budget():
    city = City()
    for _ in range(90):
        city.second()
    assert not city.sim.unsafe_seen
    assert set(city.ctx.store.last_full) == set(city.ctx.topology.intersections)
    assert {i for i in city.ctx.topology.intersections if city.ctx.store.get_actual(i, 5)} == set(city.ctx.topology.intersections)
    reasons = {v["reason"] for v in city.ctx.store.last_full.values()}
    assert reasons & {"keep_current", "current_axis_empty", "higher_pressure"}
    cyc = city.ctx.metrics.snapshot()["series_ms"]["controller_cycle_ms"]
    assert cyc["p95"] < 100, f"controller cycle too slow for a 1 s loop: {cyc}"
    assert city.ctx.db.query("SELECT 1 FROM signal_decisions LIMIT 1")


def test_impact_assessment_scales_to_the_grid():
    city = City()
    t0 = time.perf_counter()
    imp = city.ctx.incidents.impact("stalled_vehicle", "L6_7")
    assert time.perf_counter() - t0 < 0.5
    assert imp["routes_affected"] >= 1 and imp["max_added_delay_s"] > 0 and imp["unreachable_pairs"] == []
    assert "I6" in imp["affected_intersections"] or "I7" in imp["affected_intersections"]


def test_ev_green_corridor_across_the_city():
    city = City(rates_all=0.05)
    for _ in range(15):
        city.second()
    s = city.ctx.ev.declare("EV-1", "ext_W1", "ext_E3", "operator", "t")
    assert len(s.route) >= 6
    city.sim.apply({"op": "drive_ev", "vehicle_id": "EV-1", "pace": 3.0})
    for _ in range(220):
        city.second()
        if s.status == "ended":
            break
    assert s.status == "ended" and not city.sim.unsafe_seen
    prio = {r["intersection"] for r in city.ctx.db.query("SELECT DISTINCT intersection FROM signal_decisions WHERE reason LIKE 'emergency%'")}
    assert len(prio) >= 4, f"a corridor of priority decisions along the route, got {prio}"
    states = {r["state"] for r in city.ctx.db.query("SELECT state FROM ev_warnings")}
    assert states and not states & {"active", "updated", "redirected"}, "every warning ended cleanly"
    assert city.sim.ev_stats["wait_s"] <= 12, f"the green corridor should keep stops short, waited {city.sim.ev_stats}"


def test_city_scenario_runs_and_every_self_check_passes():
    city = City(rates_all=0.05)
    assert set(city.ctx.scenarios.scenarios) == {"city_demo", "drill_camera_loss", "drill_llm_down", "drill_controller_link_loss"}
    run = city.ctx.scenarios.start("city_demo", "operator")
    for _ in range(int(run.scn.duration) + 8):
        city.second()
        if run.status != "running":
            break
    assert run.status == "finished", run.log[-5:]
    bad = {run.scn.steps[i].title: r for i, r in run.results.items() if not r["ok"]}
    assert not bad, bad
    assert not city.sim.unsafe_seen and run.summary["checks_passed"] == run.summary["checks_total"] == 6


def test_road_users_and_tools_work_on_the_grid():
    city = City()
    u = city.ctx.roadusers.join("ext_N2", "ext_S4")
    assert u.route[0] == "inN2" and u.route[-1] == "outS4"
    for _ in range(100):
        city.second()
    assert u.status in ("arrived",) or u.token not in city.ctx.roadusers.users
    from app.llm import tools as T
    tc = T.ToolCtx(city.ctx, {"username": "operator", "role": "operator"}, True)
    r = T.TOOLS["get_route_alternatives"].handler(tc, {"origin": "ext_W1", "destination": "ext_E3"})
    assert len(r["routes"]) >= 2
    assert "error" in T.TOOLS["get_route_alternatives"].handler(tc, {"origin": "I1", "destination": "ext_E3"})
    city.ctx.store.set_traffic("L1_2", 1, "simulated")
    assert city.ctx.proposals.propose("I12", "A", 15, "t", "operator", "operator")["validator_reason"] != "unknown_intersection"
    topo = city.client.get("/api/public/topology").json()
    assert len(topo["nodes"]) == 26 and len(topo["edges"]) == 62 and topo["capacities"]["inW1"] == 8
