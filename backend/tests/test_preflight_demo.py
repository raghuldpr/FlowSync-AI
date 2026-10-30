from app import preflight

PW = "test-operator-pw"


def kinds(res):
    return {name: status for status, name, _ in res}


def test_demo_mode_fails_on_development_credentials(client):
    normal = kinds(preflight.run(client, PW))
    strict = preflight.run(client, PW, demo=True)
    assert normal["credentials"] == preflight.WARN
    assert kinds(strict)["credentials"] == preflight.FAIL
    assert "app.setup_demo" in dict((n, d) for _, n, d in strict)["credentials"]


def test_llm_line_says_fallback_and_never_shows_a_key(client):
    detail = dict((n, d) for _, n, d in preflight.run(client, PW))["LLM"]
    assert detail.startswith("FALLBACK") and "key" not in detail.lower().replace("keys", "")


def local(**kw):
    base = dict(port=8000, ips=["192.168.137.1"], connect=lambda h, p: True, have_module=lambda m: True, model_installed=True)
    base.update(kw)
    return kinds(preflight.local_checks(**base))


def test_local_checks_all_good():
    r = local()
    assert r["hotspot address"] == preflight.PASS and r["backend on the LAN (port 8000)"] == preflight.PASS
    assert r["SUMO (optional)"] == preflight.PASS and r["pothole model (optional)"] == preflight.PASS
    assert r["hotspot band (reminder)"] == preflight.WARN          # always a visible reminder


def test_missing_hotspot_is_a_warning_but_unreachable_backend_fails():
    r = local(ips=["192.168.1.20"], connect=lambda h, p: False)
    assert r["hotspot address"] == preflight.WARN
    assert r["backend on the LAN (port 8000)"] == preflight.FAIL


def test_no_network_at_all_fails_the_lan_check():
    assert local(ips=[])["backend on the LAN (port 8000)"] == preflight.FAIL


def test_the_lan_check_uses_the_hotspot_address_when_present():
    seen = []
    local(ips=["10.0.0.5", "192.168.137.1"], connect=lambda h, p: seen.append((h, p)) or True)
    assert seen == [("192.168.137.1", 8000)]


def test_optional_extras_only_warn():
    r = local(have_module=lambda m: False, model_installed=False)
    assert r["SUMO (optional)"] == preflight.WARN and r["pothole model (optional)"] == preflight.WARN
