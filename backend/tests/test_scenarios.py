"""Scenario engine, failure drills and live provenance, run end-to-end against the virtual tabletop."""
import pytest
from fastapi.testclient import TestClient

from app.config import Settings, load_yaml
from app.core.topology import Topology
from app.main import create_app
from simulator.tabletop import DeviceClient, VirtualTabletop

PW = "pw"


class Rig:
    def __init__(self, tmp_path, with_sim=True):
        holder = {}
        clock = lambda: holder["sim"].now_ms / 1000 if "sim" in holder else 0.0
        s = Settings(db_path=str(tmp_path / "s.db"), operator_password=PW, controller_autostart=False,
                     llm_enabled=False, llm_base_url="", llm_model="", llm_api_key="")
        self.app = create_app(s, clock)
        self.ctx, self.client = self.app.state.ctx, TestClient(self.app)
        self.sim = VirtualTabletop(self.client, Topology.load(s.topology_path), load_yaml(s.devices_path)["devices"],
                                   rates={a: 0.05 for a in self.ctx.topology.approaches})
        holder["sim"] = self.sim
        self.with_sim = with_sim
        if with_sim:
            self.sim.step(1000)            # first control poll fixes the command cursor

    def hdr(self):
        t = self.client.post("/api/auth/login", json={"username": "operator", "password": PW}).json()["token"]
        return {"Authorization": f"Bearer {t}"}

    def second(self):
        """One simulated second, in the server's loop order: scenario -> incidents -> EV -> controller; then the world moves."""
        self.ctx.scenarios.tick()
        self.ctx.incidents.tick()
        self.ctx.ev.tick()
        self.ctx.roadusers.tick()
        self.ctx.controller.cycle()
        if self.with_sim:
            self.sim.step(1000)

    def run_scenario(self, sid, extra=8):
        run = self.ctx.scenarios.start(sid, "operator")
        for _ in range(int(run.scn.duration) + extra):
            self.second()
            if run.status != "running":
                break
        return run


def failed(run):
    return {i: r for i, r in run.results.items() if not r["ok"]}


@pytest.fixture
def rig(tmp_path):
    return Rig(tmp_path)


def test_ambulance_demo_runs_to_completion_and_every_self_check_passes(rig):
    run = rig.run_scenario("ambulance_demo")
    assert run.status == "finished", run.log[-6:]
    assert not failed(run), {run.scn.steps[i].title: r for i, r in failed(run).items()}
    assert len(run.results) == 9
    assert not rig.sim.unsafe_seen
    rig.second()                                                    # the simulator polls the final 'reset' command
    s = run.summary
    assert s["checks_passed"] == s["checks_total"] == 9
    assert s.get("road_users_warned", 0) >= 0
    assert s["ev_travel_s"] and 30 < s["ev_travel_s"] < 90, s
    assert s["reroutes"] >= 1 and s["emergency_priority_decisions"] >= 2 and s["incidents_opened"] >= 2
    assert not any(k in ("active", "updated", "redirected") for k in s["warnings_by_final_state"]), s
    assert "cancelled_route_change" in s["warnings_by_final_state"]
    # cleaned up: world and backend are back to normal and the demo can be run again
    assert not rig.sim.pins and not rig.sim.stalled and rig.ctx.store.incident_closed == set()
    assert rig.ctx.scenarios.status()["state"] == "finished"


def test_demo_can_be_rerun_after_it_finishes(rig):
    rig.run_scenario("drill_llm_down")
    run = rig.ctx.scenarios.start("ambulance_demo", "operator")
    assert run.status == "running" and run.scn.id == "ambulance_demo"


@pytest.mark.parametrize("sid", ["drill_camera_loss", "drill_ev_location_lost", "drill_llm_down", "drill_controller_link_loss"])
def test_each_failure_drill_shows_the_intended_safe_behaviour(rig, sid):
    run = rig.run_scenario(sid)
    assert run.status == "finished", run.log[-5:]
    assert run.results and not failed(run), {run.scn.steps[i].title: r for i, r in failed(run).items()}
    assert not rig.sim.unsafe_seen
    rig.second()                                                    # the simulator polls the final 'reset' command
    assert rig.ctx.llm.forced_down is False and rig.sim.camera_on and rig.sim.link_up, "drill must restore the world"


def test_only_one_scenario_at_a_time_and_stop_cleans_up(rig):
    rig.ctx.scenarios.start("ambulance_demo", "operator")
    with pytest.raises(RuntimeError):
        rig.ctx.scenarios.start("drill_llm_down", "operator")
    for _ in range(70):
        rig.second()
    assert rig.sim.pins and rig.ctx.ev.sessions
    rig.ctx.scenarios.stop("operator")
    rig.second(); rig.second()
    assert rig.ctx.scenarios.status()["state"] == "stopped"
    assert not rig.sim.pins and not rig.sim.stalled and rig.ctx.store.incident_closed == set()
    assert all(s.status == "ended" for s in rig.ctx.ev.sessions.values())


def test_physical_mode_shows_cues_and_does_not_need_a_simulator(tmp_path):
    rig = Rig(tmp_path, with_sim=False)
    rig.ctx.scenarios.start("ambulance_demo", "operator")
    for _ in range(15):
        rig.sim.now_ms += 1000                      # time passes; nobody polls the simulator channel
        rig.second()
    st = rig.ctx.scenarios.status()
    assert st["world"] == "physical" and st["current"]["cue"]
    assert any(s["cue"] for s in st["steps"])


def test_commands_are_not_replayed_to_a_late_joining_simulator(rig):
    rig.ctx.sim_bus.push({"op": "pin", "approach": "L13", "count": 5})
    late = VirtualTabletop(rig.client, rig.sim.topo, rig.ctx.device_specs)
    late.step(1000); late.step(1000)
    assert late.pins == {}, "history must not be replayed"
    rig.ctx.sim_bus.push({"op": "pin", "approach": "L21", "count": 3})
    late.step(1000)
    assert late.pins == {"L21": 3}


def test_world_commands_apply(rig):
    sim = rig.sim
    sim.apply({"op": "pin", "approach": "L13", "count": 4}); sim.apply({"op": "stall", "approach": "L12", "on": True})
    sim.apply({"op": "camera", "on": False}); sim.apply({"op": "link", "on": False}); sim.apply({"op": "rates", "all": 0.3})
    assert sim.pins == {"L13": 4} and "L12" in sim.stalled and not sim.camera_on and not sim.link_up and set(sim.rates.values()) == {0.3}
    sim.step(1000)
    assert sim.queues["L13"] >= 4
    sim.apply({"op": "reset"})
    assert not sim.pins and not sim.stalled and sim.camera_on and sim.link_up and sim.rates == sim.default_rates


# ---- API, roles, audit ----------------------------------------------------------------------------------------------
def test_scenario_api_is_operator_only_audited_and_validated(rig):
    c, h = rig.client, rig.hdr()
    assert c.get("/api/operator/scenarios").status_code == 401
    assert c.post("/api/operator/scenario/start", json={"id": "ambulance_demo"}).status_code == 401
    assert c.post("/api/operator/scenario/stop").status_code == 401
    assert c.get("/api/operator/provenance").status_code == 401
    ids = {s["id"] for s in c.get("/api/operator/scenarios", headers=h).json()}
    assert ids == {"ambulance_demo", "drill_camera_loss", "drill_ev_location_lost", "drill_llm_down", "drill_controller_link_loss"}
    assert c.post("/api/operator/scenario/start", json={"id": "nope"}, headers=h).status_code == 404
    r = c.post("/api/operator/scenario/start", json={"id": "ambulance_demo"}, headers=h)
    assert r.status_code == 200 and r.json()["state"] == "running" and r.json()["steps"][0]["narration"]
    assert c.post("/api/operator/scenario/start", json={"id": "drill_llm_down"}, headers=h).status_code == 409
    rig.second()
    assert c.get("/api/operator/scenario", headers=h).json()["state"] == "running"
    assert c.post("/api/operator/scenario/stop", headers=h).json()["state"] == "stopped"
    log = {(a["action"], a["result"]) for a in rig.ctx.db.query("SELECT * FROM audit_log")}
    assert ("scenario_start", "ok") in log and ("scenario_stop", "ok") in log and ("scenario_start", "already_running") in log
    assert any(a.startswith("scenario_action:world") for a, _ in log), "scripted actions are audit-logged too"
    assert any(r == "denied_unauthenticated" for _, r in log)


def test_scenario_actions_run_as_the_starting_operator_not_anonymously(rig):
    rig.ctx.scenarios.start("ambulance_demo", "operator")
    for _ in range(60):
        rig.second()
    actors = {a["actor"] for a in rig.ctx.db.query("SELECT actor FROM audit_log WHERE action LIKE 'scenario_action:%'")}
    assert actors == {"scenario:operator"}
    ev = rig.ctx.db.one("SELECT declared_by, source FROM ev_sessions")
    assert (ev["declared_by"], ev["source"]) == ("scenario:operator", "scenario")


def test_simulator_channel_is_role_restricted(rig, op_headers=None):
    d = rig.ctx.device_specs
    cam = DeviceClient(rig.client, "camera-1", d["camera-1"]["key"])
    car = DeviceClient(rig.client, "car-1", d["car-1"]["key"])
    sim = DeviceClient(rig.client, "simulator-1", d["simulator-1"]["key"])
    assert cam.request("GET", "/api/device/sim-commands").status_code == 403
    assert car.request("GET", "/api/device/sim-ev", params={"vehicle_id": "EV-1"}).status_code == 403
    assert rig.client.get("/api/device/sim-commands").status_code in (401, 403)
    assert sim.request("GET", "/api/device/sim-commands").status_code == 200
    assert sim.request("POST", "/api/device/traffic", {"source": "simulated", "readings": []}).status_code == 403


# ---- provenance -----------------------------------------------------------------------------------------------------
def prov(rig):
    return {r["component"]: r for r in rig.client.get("/api/operator/provenance", headers=rig.hdr()).json()}


def test_provenance_reflects_what_is_actually_connected(tmp_path):
    rig = Rig(tmp_path, with_sim=False)
    p = prov(rig)
    assert p["Intersection controller (3 intersections, 18 lamps)"]["status"] == "Not connected"
    assert p["Queue counts"]["status"] == "Not connected"
    (tmp_path / "b").mkdir()
    rig = Rig(tmp_path / "b")
    for _ in range(3):
        rig.second()
    p = prov(rig)
    assert p["Intersection controller (3 intersections, 18 lamps)"]["status"] == "Simulated"
    assert p["Queue counts"]["status"] == "Simulated"
    assert p["Adaptive signal controller + safety validator"]["status"] == "Built"
    assert "deterministic fallback" in p["LLM reasoning layer"]["detail"]
    # a real device labels itself as hardware -> the table says Built
    d = DeviceClient(rig.client, "intersection-1", rig.ctx.device_specs["intersection-1"]["key"])
    st = {"phase": "GREEN", "axis": "A", "next_axis": "B", "elapsed_ms": 10, "lamps": {"A": "G", "B": "R"}, "backend_ok": True}
    assert d.request("POST", "/api/device/state", {"intersections": {"I1": st}}).status_code == 200
    assert prov(rig)["Intersection controller (3 intersections, 18 lamps)"]["status"] == "Built"
    cam = DeviceClient(rig.client, "camera-1", rig.ctx.device_specs["camera-1"]["key"])
    cam.request("POST", "/api/device/traffic", {"source": "overhead_camera", "readings": [{"approach": "W1", "count": 1}]})
    assert prov(rig)["Queue counts"]["status"] == "Built"
    rig.sim.now_ms += 30_000                          # silence for 30 s
    assert prov(rig)["Queue counts"]["status"] == "Not connected"


def test_provenance_marks_the_ev_as_simulated_or_camera(rig):
    rig.run_scenario("drill_ev_location_lost", extra=0)
    assert prov(rig)["Emergency vehicle position"]["status"] in ("Simulated", "Idle")
    cam = DeviceClient(rig.client, "camera-1", rig.ctx.device_specs["camera-1"]["key"])
    rig.ctx.ev.declare("EV-9", "ext_W", "ext_S", "operator", "t")
    cam.request("POST", "/api/device/ev-position", {"vehicle_id": "EV-9", "edge": "W1", "progress": 0.2})
    assert prov(rig)["Emergency vehicle position"]["status"] == "Built"


def test_overview_carries_scenario_and_provenance(rig):
    o = rig.client.get("/api/operator/overview", headers=rig.hdr()).json()
    assert o["scenario"]["state"] == "idle" and o["scenario"]["scenarios"] and len(o["provenance"]) >= 8
