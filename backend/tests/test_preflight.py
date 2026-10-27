from app import preflight
from simulator.tabletop import VirtualTabletop

PW = "test-operator-pw"


def kinds(res):
    return {name: status for status, name, _ in res}


def test_unreachable_backend_fails_fast():
    import httpx
    res = preflight.run(httpx.Client(base_url="http://127.0.0.1:9", timeout=0.5), PW)
    assert res[0][0] == preflight.FAIL and len(res) == 1


def test_wrong_password_is_reported_clearly(client):
    res = preflight.run(client, "nope")
    assert kinds(res)["operator login"] == preflight.FAIL and "FLOWSYNC_OPERATOR_PASSWORD" in res[-1][2]


def test_fresh_system_warns_about_dev_defaults_missing_devices_and_llm(client):
    res = kinds(preflight.run(client, PW))
    assert res["backend reachable"] == preflight.PASS and res["operator login"] == preflight.PASS
    assert res["credentials"] == preflight.WARN and res["LLM"] == preflight.WARN
    assert res["scenarios available"] == preflight.PASS and res["topology"] == preflight.PASS
    assert res["device intersection-1"] == preflight.WARN


def test_with_a_live_simulator_and_a_drill_the_system_is_ready(app, client, ctx):
    sim = VirtualTabletop(client, ctx.topology, ctx.device_specs, rates={a: 0.05 for a in ctx.topology.approaches})
    ctx.store.clock = lambda: sim.now_ms / 1000                 # simulated time drives the whole system

    def tick(_):                                                # the preflight's sleep() advances the simulated world
        ctx.scenarios.tick(); ctx.incidents.tick(); ctx.ev.tick(); ctx.roadusers.tick(); ctx.controller.cycle()
        sim.step(1000)
    sim.step(1000)
    for _ in range(8):
        tick(None)
    res = preflight.run(client, PW, drill="drill_llm_down", wait_s=60, sleep=tick)
    k = kinds(res)
    assert k["drill drill_llm_down"] == preflight.PASS, res
    assert k["device intersection-1"] == preflight.PASS and k["control loop"] == preflight.PASS
    assert not any(s == preflight.FAIL for s, _, _ in res)
