import pytest


@pytest.fixture
def ready(ctx, clock):
    """Fresh traffic everywhere, I1 reporting green on axis A for 8 s."""
    def setup(green="A", elapsed=8000, **counts):
        for a in ctx.topology.approaches:
            ctx.store.set_traffic(a, counts.get(a, 0), "simulated")
        ctx.store.set_actual("I1", {"phase": "GREEN", "axis": green, "next_axis": "B" if green == "A" else "A",
                                    "elapsed_ms": elapsed, "lamps": {"A": "R", "B": "R"}, "backend_ok": True}, "intersection-1")
    setup()
    ctx.setup_i1 = setup
    return ctx


def propose(ctx, inter="I1", axis="B", dur=15):
    return ctx.proposals.propose(inter, axis, dur, "test", "operator", "operator")


def test_valid_proposal_is_pending_and_changes_nothing(ready):
    r = propose(ready)
    assert r["status"] == "pending" and r["validator_reason"] == "ok"
    assert ready.controller.operator_requests == {}


@pytest.mark.parametrize("args,reason", [
    (dict(inter="I9"), "unknown_intersection"), (dict(axis="C"), "bad_axis"),
    (dict(dur=4), "bad_duration"), (dict(dur=31), "bad_duration"), (dict(inter="I2"), "no_fresh_device_state"),
])
def test_validator_rejects(ready, args, reason):
    r = propose(ready, **args)
    assert (r["status"], r["validator_reason"]) == ("rejected", reason)


def test_rejected_when_emergency_serves_other_axis(ready):
    ready.db.execute("INSERT INTO emergency_requests(source,vehicle_id,approach,status,created_by,created_at) "
                     "VALUES('operator','EV-1','L21','active','t','x')")          # L21 is axis A at I1
    assert propose(ready, axis="B")["validator_reason"] == "conflicts_with_emergency_priority"
    assert propose(ready, axis="A")["status"] == "pending"


def test_rejected_when_all_demand_would_enter_full_links(ready):
    ready.setup_i1(W1=5, L12=7, L13=7)                     # B = W1 only, both downstream links >= 80% full
    r = propose(ready, axis="B")
    assert r["validator_reason"] == "all_demand_blocked_by_full_downstream"


def test_rejected_when_traffic_is_stale(ready, clock):
    clock.t[0] += 10
    ready.store.set_actual("I1", {"phase": "GREEN", "axis": "A", "next_axis": "B", "elapsed_ms": 8000,
                                  "lamps": {"A": "R", "B": "R"}, "backend_ok": True}, "intersection-1")
    assert propose(ready)["validator_reason"] == "no_fresh_traffic_data"


def test_one_pending_per_intersection_and_expiry(ready, clock):
    assert propose(ready)["status"] == "pending"
    assert propose(ready)["validator_reason"] == "pending_proposal_exists"
    clock.t[0] += 121
    ready.setup_i1()
    assert propose(ready)["status"] == "pending"
    assert ready.db.query("SELECT status FROM llm_proposals ORDER BY id")[0]["status"] == "expired"


def test_confirm_revalidates_against_current_state(ready):
    p = propose(ready)
    ready.db.execute("INSERT INTO emergency_requests(source,vehicle_id,approach,status,created_by,created_at) "
                     "VALUES('operator','EV-1','L21','active','t','x')")
    assert ready.proposals.confirm(p["id"], "operator") == (False, "conflicts_with_emergency_priority")
    assert ready.db.one("SELECT status FROM llm_proposals WHERE id=?", (p["id"],))["status"] == "rejected"
    assert ready.controller.operator_requests == {}


def test_confirmed_request_goes_through_controller_safety_rules(ready, clock):
    q = {a: 0 for a in ready.topology.approaches}
    p = propose(ready, axis="B", dur=15)
    assert ready.proposals.confirm(p["id"], "operator") == (True, "ok")
    d = ready.controller.decide("I1", q, set())
    assert (d["target"], d["reason"]) == ("B", "operator_request")
    ready.setup_i1(elapsed=2000)                                   # min green not elapsed: FSM rule still applies
    d = ready.controller.decide("I1", q, set())
    assert (d["target"], d["reason"]) == ("A", "operator_request_waiting_min_green")
    d = ready.controller.decide("I1", q, {"L21"})                  # emergency priority outranks the request
    assert d["reason"].startswith("emergency")
    clock.t[0] += 16                                               # request expires by itself
    ready.setup_i1()
    d = ready.controller.decide("I1", q, set())
    assert "operator" not in d["reason"] and ready.controller.operator_requests == {}


def test_decline(ready):
    p = propose(ready)
    assert ready.proposals.decline(p["id"], "operator") and not ready.proposals.decline(p["id"], "operator")
    assert ready.proposals.confirm(p["id"], "operator") == (False, "not_pending")


def test_proposal_api_is_operator_only_and_audited(client, ready, op_headers):
    p = propose(ready)
    assert client.post(f"/api/operator/proposals/{p['id']}/confirm").status_code == 401
    assert client.get("/api/operator/proposals").status_code == 401
    assert client.post(f"/api/operator/proposals/{p['id']}/confirm", headers=op_headers).status_code == 200
    assert client.post(f"/api/operator/proposals/{p['id']}/confirm", headers=op_headers).status_code == 409
    acts = [(a["action"], a["result"]) for a in ready.db.query("SELECT * FROM audit_log")]
    assert ("proposal_confirm", "ok") in acts and ("proposal_confirm", "denied_unauthenticated") not in acts
    assert any(r == "denied_unauthenticated" for _, r in acts)
    assert client.get("/api/operator/proposals", headers=op_headers).json()[0]["status"] == "confirmed"
