"""Closed loop: virtual tabletop <-> signed device API <-> controller, on a simulated clock."""
import pytest
from fastapi.testclient import TestClient

from app.config import Settings, load_yaml
from app.core.topology import Topology
from app.main import create_app
from simulator.tabletop import VirtualTabletop

PW = "pw"


class Rig:
    def __init__(self, tmp_path, rates=None, seed=3):
        holder = {}
        clock = lambda: holder["sim"].now_ms / 1000 if "sim" in holder else 0.0
        s = Settings(db_path=str(tmp_path / "i.db"), operator_password=PW, controller_autostart=False,
                     llm_enabled=False, llm_base_url="", llm_model="", llm_api_key="")
        self.app = create_app(s, clock)
        self.ctx = self.app.state.ctx
        self.client = TestClient(self.app)
        self.sim = VirtualTabletop(self.client, Topology.load(s.topology_path),
                                   load_yaml(s.devices_path)["devices"], rates=rates, seed=seed)
        holder["sim"] = self.sim
        self.axis_seconds = {i: {"A": 0, "B": 0} for i in self.sim.fsms}
        self.q_sum, self.seconds = {a: 0 for a in self.sim.queues}, 0

    def mean_queue(self, approach):
        return self.q_sum[approach] / max(1, self.seconds)

    def run(self, seconds, cycle=True):
        for _ in range(seconds):
            self.sim.step(1000)
            if cycle:
                self.ctx.incidents.tick()
                self.ctx.ev.tick()
                self.ctx.controller.cycle()
            self.seconds += 1
            for a, q in self.sim.queues.items():
                self.q_sum[a] += q
            for i, f in self.sim.fsms.items():
                if f.phase == "GREEN":
                    self.axis_seconds[i][f.axis] += 1

    def op(self):
        t = self.client.post("/api/auth/login", json={"username": "operator", "password": PW}).json()["token"]
        return {"Authorization": f"Bearer {t}"}


@pytest.fixture
def rig(tmp_path):
    return Rig(tmp_path, rates={"E2": 0.6, "L12": 0.02, "L32": 0.02, "W1": 0.05, "S3": 0.05})


def test_adaptive_beats_fixed_time_on_skewed_demand(tmp_path, rig):
    """Same seed and demand; fixed-time baseline = backend never reachable (device fallback cycle)."""
    rates = {"E2": 0.6, "L12": 0.02, "L32": 0.02, "W1": 0.05, "S3": 0.05}
    fixed = Rig(tmp_path / "fixed", rates=rates) if (tmp_path / "fixed").mkdir() is None else None
    fixed.sim.link_up = False
    rig.run(150)
    fixed.run(150, cycle=False)
    print(f"mean E2 queue  adaptive={rig.mean_queue('E2'):.2f}  fixed={fixed.mean_queue('E2'):.2f}")
    assert not rig.sim.unsafe_seen and not fixed.sim.unsafe_seen
    assert rig.mean_queue("E2") < fixed.mean_queue("E2")
    assert rig.axis_seconds["I2"]["B"] > rig.axis_seconds["I2"]["A"]
    assert not rig.ctx.db.query("SELECT 1 FROM system_events WHERE type='unsafe_lamps_reported'")


def test_dashboard_data_is_device_reported_and_fresh(rig):
    rig.run(30)
    sig = rig.client.get("/api/operator/signals", headers=rig.op()).json()
    for inter, row in sig.items():
        assert row["reported"] is not None and not row["reported_stale"]
        assert row["reported"]["lamps"] == rig.sim.fsms[inter].lamps()
    acks = rig.ctx.db.query("SELECT * FROM device_commands WHERE ack_status IS NOT NULL")
    assert acks and all(a["ack_status"] == "accepted" for a in acks)


def test_backend_loss_falls_back_safely_and_recovers(rig):
    rig.run(20)
    rig.sim.link_up = False
    before = dict(rig.axis_seconds["I1"])
    rig.run(60, cycle=False)
    assert not rig.sim.unsafe_seen
    assert all(not f.backend_ok for f in rig.sim.fsms.values())
    after = rig.axis_seconds["I1"]
    assert after["A"] > before["A"] and after["B"] > before["B"], "fallback must serve both axes"
    rig.sim.link_up = True
    rig.run(10)
    assert all(f.backend_ok for f in rig.sim.fsms.values())
    sig = rig.client.get("/api/operator/signals", headers=rig.op()).json()
    assert not any(r["reported_stale"] for r in sig.values())


def test_stale_device_state_is_flagged(rig):
    rig.run(10)
    rig.sim.link_up = False
    rig.run(12, cycle=False)
    sig = rig.client.get("/api/operator/signals", headers=rig.op()).json()
    assert all(r["reported_stale"] for r in sig.values())
    assert rig.client.get("/api/public/signals").json()["I1"] == "unknown"


def drive_ev(rig, vehicle, seconds, pace=2.0, force_queues=None):
    """Scripted EV: moves along its CURRENT route (so reroutes are followed), reports like the camera would."""
    ev = rig.ctx.ev
    for _ in range(seconds):
        s = next(x for x in ev.sessions.values() if x.vehicle_id == vehicle)
        if s.status != "active":
            return
        e = rig.ctx.topology.edges[s.edge or s.route[0]]
        edge, prog = e.id, (s.progress if s.edge else 0.0) + 1.0 / (e.length / e.speed * pace)
        if prog >= 1.0:
            nxt = s.route.index(edge) + 1
            if nxt >= len(s.route):
                prog = 0.97
            else:
                edge, prog = s.route[nxt], 0.02
        for a, q in (force_queues or {}).items():
            rig.sim.queues[a] = q
        rig.sim.cam.request("POST", "/api/device/ev-position", {"vehicle_id": vehicle, "edge": edge, "progress": prog})
        rig.run(1)


def test_scripted_ev_trip_reroutes_and_gets_priority(tmp_path):
    """W1 -> S3 with congestion on the direct link L13: reroute via I2, warnings redirect, priority follows."""
    rates = {a: 0.01 for a in Topology.load(Settings().topology_path).approaches}
    rig = Rig(tmp_path, rates=rates)
    rig.run(15)
    hdr = rig.op()
    assert rig.client.post("/api/operator/ev", json={"vehicle_id": "EV-1"}, headers=hdr).status_code == 200
    drive_ev(rig, "EV-1", 6, force_queues={"L13": 6})      # congestion appears while the EV is on W1
    s = next(iter(rig.ctx.ev.sessions.values()))
    assert s.route == ["W1", "L12", "L23", "O3"], s.route
    drive_ev(rig, "EV-1", 120, force_queues={"L13": 6})
    assert s.status == "ended"
    assert not rig.sim.unsafe_seen
    reroutes = rig.ctx.db.query("SELECT * FROM system_events WHERE type='ev_reroute'")
    assert len(reroutes) == 1
    st = {(r["approach"], r["state"]) for r in rig.ctx.db.query("SELECT approach,state FROM ev_warnings")}
    assert ("L12", "redirected") in st or ("L12", "cancelled_passed") in st
    assert ("L13", "cancelled_route_change") in st and ("W1", "cancelled_passed") in st
    assert not any(state in ("active", "updated", "redirected") for _, state in st)
    pri = {(r["intersection"]) for r in rig.ctx.db.query(
        "SELECT intersection FROM signal_decisions WHERE reason LIKE 'emergency%'")}
    assert pri == {"I1", "I2", "I3"}, "each intersection on the path must have received EV priority"
    assert rig.ctx.ev.priority_approaches() == set()
    assert not rig.ctx.db.query("SELECT 1 FROM system_events WHERE type='unsafe_lamps_reported'")


def test_stalled_vehicle_in_sim_is_detected_assessed_and_cleared(tmp_path):
    rates = {a: 0.01 for a in Topology.load(Settings().topology_path).approaches}
    rates["L13"] = 0.3
    rig = Rig(tmp_path, rates=rates)
    rig.sim.stalled.add("L13")
    rig.run(70)
    rows = rig.ctx.db.query("SELECT * FROM incidents WHERE type='stalled_vehicle' AND approach='L13'")
    assert rows and rows[0]["source"] == "detector", "stall should be detected from camera data + device-reported green"
    hdr = rig.op()
    inc = rig.client.get("/api/operator/incidents", params={"status": "active"}, headers=hdr).json()
    stall = next(i for i in inc if i["type"] == "stalled_vehicle")
    assert stall["impact"]["routes_affected"] >= 1 and "L13" in rig.ctx.store.all_closed()
    rig.sim.stalled.clear()                               # tow truck arrives; traffic flows again
    rig.run(40)
    assert rig.ctx.db.one("SELECT status FROM incidents WHERE id=?", (stall["id"],))["status"] == "resolved"
    assert "L13" not in rig.ctx.store.all_closed() and not rig.sim.unsafe_seen


def test_ev_that_goes_silent_loses_priority_and_warnings(tmp_path):
    rig = Rig(tmp_path, rates={a: 0.01 for a in Topology.load(Settings().topology_path).approaches})
    rig.run(10)
    rig.client.post("/api/operator/ev", json={"vehicle_id": "EV-1"}, headers=rig.op())
    drive_ev(rig, "EV-1", 3)
    assert rig.ctx.ev.priority_approaches()
    rig.run(6)                                            # no more position reports
    assert rig.ctx.ev.priority_approaches() == set()
    assert rig.client.get("/api/public/warnings", params={"approach": "W1"}).json()["warning"] is None
    assert not rig.sim.unsafe_seen


def test_emergency_priority_through_safety_machine(tmp_path):
    # axis A at I1 is busy, so without an emergency B (W1) would not get green
    rates = {a: 0.01 for a in Topology.load(Settings().topology_path).approaches}
    rates.update({"L21": 0.5, "L31": 0.5})
    rig = Rig(tmp_path, rates=rates)
    rig.run(20)
    assert rig.ctx.store.last_decision["I1"][1] != "emergency_priority"
    hdr = rig.op()
    eid = rig.client.post("/api/operator/emergencies", json={"approach": "W1"}, headers=hdr).json()["id"]
    seen_b_green = False
    for _ in range(25):
        rig.run(1)
        seen_b_green |= rig.sim.fsms["I1"].lamps()["B"] == "G"
    assert seen_b_green and not rig.sim.unsafe_seen
    assert rig.ctx.db.query("SELECT 1 FROM signal_decisions WHERE reason='emergency_priority' AND intersection='I1'")
    rig.client.post(f"/api/operator/emergencies/{eid}/clear", headers=hdr)
    rig.run(5)
    assert rig.ctx.store.last_decision["I1"][1] != "emergency_priority"
