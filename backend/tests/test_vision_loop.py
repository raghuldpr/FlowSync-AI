"""Vision-in-the-loop: simulator physics -> synthetic camera image -> REAL vision pipeline -> signed posts ->
backend -> controller. The backend only ever sees what the pipeline MEASURED. Needs OpenCV."""
import pytest
from fastapi.testclient import TestClient

pytest.importorskip("cv2")

from app.config import Settings
from app.main import create_app
from app.vision import capacity as capacity_tool
from simulator.tabletop import VirtualTabletop
from simulator.vision_world import SyntheticCamera


class Rig:
    def __init__(self, tmp_path, rates):
        physical = tmp_path / "physical.yaml"
        capacity_tool.main(["--write", str(physical)])               # per-lane capacities that fit the camera ROIs
        holder = {}
        clock = lambda: holder["sim"].now_ms / 1000 if "sim" in holder else 0.0
        s = Settings(db_path=str(tmp_path / "v.db"), topology_path=str(physical), operator_password="pw",
                     controller_autostart=False, llm_enabled=False, llm_base_url="", llm_model="", llm_api_key="")
        self.app = create_app(s, clock)
        self.ctx, self.client = self.app.state.ctx, TestClient(self.app)
        self.sim = VirtualTabletop(self.client, self.ctx.topology, self.ctx.device_specs, rates=rates,
                                   external_camera=True)
        holder["sim"] = self.sim
        self.cam = SyntheticCamera(self.sim, self.client, self.ctx.device_specs)
        self.mismatches, self.compared = 0, 0

    def second(self):
        self.sim.step(1000)
        self.cam.step()
        self.ctx.incidents.tick(); self.ctx.ev.tick(); self.ctx.controller.cycle()
        for a, rec in self.ctx.store.traffic.items():                # backend value vs simulator ground truth
            self.compared += 1
            self.mismatches += rec["count"] != self.sim.queues[a]


def test_backend_sees_what_the_camera_measured_and_the_controller_still_works(tmp_path):
    rig = Rig(tmp_path, rates={"E2": 0.4, "L12": 0.05, "L32": 0.05, "W1": 0.05, "S3": 0.05})
    for _ in range(90):
        rig.second()
    assert rig.compared > 500 and rig.mismatches / rig.compared < 0.02, (rig.mismatches, rig.compared)
    assert not rig.sim.unsafe_seen
    assert {r["source"] for r in rig.ctx.store.traffic.values()} == {"simulated"}
    assert rig.ctx.db.query("SELECT 1 FROM signal_decisions WHERE reason IN ('higher_pressure','current_axis_empty')")
    assert max(rig.ctx.store.traffic[a]["count"] for a in rig.ctx.topology.approaches) <= 7


def rig_approaches():
    from app.core.topology import Topology
    return list(Topology.load(Settings().topology_path).approaches)


def drive(rig, seconds, pace=2.0):
    ev = rig.ctx.ev
    for _ in range(seconds):
        s = next((x for x in ev.sessions.values() if x.status == "active"), None)
        if s is None:
            return
        e = rig.ctx.topology.edges[s.edge or s.route[0]]
        edge, prog = e.id, (s.progress if s.edge else 0.0) + 1.0 / (e.length / e.speed * pace)
        if prog >= 1.0:
            nxt = s.route.index(edge) + 1
            edge, prog = (s.route[nxt], 0.05) if nxt < len(s.route) else (edge, 0.97)
        rig.cam.set_ev(edge, min(prog, 0.97))
        rig.second()


def test_ev_is_localised_by_the_pipeline_and_gets_warnings_and_priority(tmp_path):
    rig = Rig(tmp_path, rates={a: 0.01 for a in rig_approaches()})
    for _ in range(12):
        rig.second()
    token = rig.client.post("/api/auth/login", json={"username": "operator", "password": "pw"}).json()["token"]
    h = {"Authorization": f"Bearer {token}"}
    assert rig.client.post("/api/operator/ev", json={"vehicle_id": "EV-1"}, headers=h).status_code == 200
    drive(rig, 150)
    s = next(iter(rig.ctx.ev.sessions.values()))
    assert s.status == "ended", f"EV never reached its destination (edge={s.edge} progress={s.progress})"
    st = {(r["approach"], r["state"]) for r in rig.ctx.db.query("SELECT approach,state FROM ev_warnings")}
    assert any(a == "W1" for a, _ in st) and any(state == "cancelled_passed" for _, state in st)
    assert ("O3", "active") not in st
    assert rig.ctx.db.query("SELECT 1 FROM ev_positions"), "positions are stored when the edge changes"
    reasons = {r["intersection"] for r in rig.ctx.db.query(
        "SELECT intersection FROM signal_decisions WHERE reason LIKE 'emergency%'")}
    assert "I1" in reasons and not rig.sim.unsafe_seen
