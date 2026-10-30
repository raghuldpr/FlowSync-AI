"""Hosting behind a reverse proxy (Zoho Catalyst AppSail): data dir, public URL, trusted proxy hops, join limits, whoami."""
import pytest
from fastapi.testclient import TestClient

from app import joinqr
from app.config import Settings
from app.main import create_app
from conftest import PW


def make(tmp_path, clock, **kw):
    s = Settings(db_path=str(tmp_path / "t.db"), operator_password=PW, controller_autostart=False, image_dir=str(tmp_path / "img"),
                 llm_enabled=False, llm_base_url="", llm_model="", llm_api_key="", **kw)
    return create_app(s, clock)


def join(client, xff=None):
    h = {"X-Forwarded-For": xff} if xff else {}
    return client.post("/api/public/road-users", json={}, headers=h)


# ---- settings from the environment --------------------------------------------------------------------
def test_data_dir_sets_db_and_images_and_explicit_paths_win(monkeypatch, tmp_path):
    for k in ("FLOWSYNC_DB_PATH", "FLOWSYNC_IMAGE_DIR"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("FLOWSYNC_DATA_DIR", str(tmp_path / "w"))
    s = Settings()
    assert s.db_path == str(tmp_path / "w" / "flowsync.db") and s.image_dir == str(tmp_path / "w" / "images")
    monkeypatch.setenv("FLOWSYNC_DB_PATH", str(tmp_path / "x.db"))
    assert Settings().db_path == str(tmp_path / "x.db")


def test_defaults_are_the_local_ones(monkeypatch):
    for k in ("FLOWSYNC_DATA_DIR", "FLOWSYNC_DB_PATH", "FLOWSYNC_IMAGE_DIR", "FLOWSYNC_PUBLIC_URL", "FLOWSYNC_TRUST_PROXY_HOPS",
              "FLOWSYNC_JOIN_PER_MIN", "FLOWSYNC_JOIN_AMBULANCE_PER_MIN", "LLM_TIMEOUT_S", "LLM_DEADLINE_S"):
        monkeypatch.delenv(k, raising=False)
    s = Settings()
    assert s.db_path.endswith("flowsync.db") and s.public_url == "" and s.trust_proxy_hops == 0
    assert (s.join_per_min, s.join_ambulance_per_min, s.llm_timeout_s, s.llm_deadline_s) == (10, 3, 15.0, 25.0)


def test_env_overrides_and_bad_values_fall_back(monkeypatch):
    monkeypatch.setenv("FLOWSYNC_PUBLIC_URL", "https://x.example.com/")
    monkeypatch.setenv("FLOWSYNC_TRUST_PROXY_HOPS", "2")
    monkeypatch.setenv("FLOWSYNC_JOIN_PER_MIN", "40")
    monkeypatch.setenv("LLM_DEADLINE_S", "not-a-number")
    s = Settings()
    assert s.public_url == "https://x.example.com" and s.trust_proxy_hops == 2 and s.join_per_min == 40 and s.llm_deadline_s == 25.0
    monkeypatch.setenv("FLOWSYNC_TRUST_PROXY_HOPS", "-3")
    assert Settings().trust_proxy_hops == 0


# ---- the client address behind a proxy ----------------------------------------------------------------
def test_without_trust_the_forwarded_header_is_ignored(tmp_path, clock):
    c = TestClient(make(tmp_path, clock))
    r = c.get("/api/whoami", headers={"X-Forwarded-For": "203.0.113.7"})
    assert r.json()["client_address"] == "testclient" and r.json()["trusted_proxy_hops"] == 0


def test_with_one_trusted_hop_the_last_entry_is_the_client(tmp_path, clock):
    c = TestClient(make(tmp_path, clock, trust_proxy_hops=1))
    # a client can prepend anything; only the entry the trusted proxy appended (the right-most) counts
    r = c.get("/api/whoami", headers={"X-Forwarded-For": "6.6.6.6, 203.0.113.7"})
    assert r.json()["client_address"] == "203.0.113.7"


def test_two_hops_take_the_second_from_the_right(tmp_path, clock):
    c = TestClient(make(tmp_path, clock, trust_proxy_hops=2))
    assert c.get("/api/whoami", headers={"X-Forwarded-For": "6.6.6.6, 203.0.113.7, 10.1.1.1"}).json()["client_address"] == "203.0.113.7"


@pytest.mark.parametrize("header", [None, "", "not-an-ip", "203.0.113.7"])
def test_missing_short_or_malformed_header_falls_back_to_the_peer(tmp_path, clock, header):
    c = TestClient(make(tmp_path, clock, trust_proxy_hops=2))
    h = {"X-Forwarded-For": header} if header is not None else {}
    assert c.get("/api/whoami", headers=h).json()["client_address"] == "testclient"


def test_instance_id_is_stable_within_a_process_and_differs_between_processes(tmp_path, clock):
    (tmp_path / "other").mkdir()
    a, b = make(tmp_path, clock), make(tmp_path / "other", clock)
    ia = TestClient(a).get("/api/whoami").json()["instance"]
    assert ia == TestClient(a).get("/api/whoami").json()["instance"]
    assert ia != TestClient(b).get("/api/whoami").json()["instance"]


# ---- rate limits per real client, not per proxy -------------------------------------------------------
def test_judges_behind_one_proxy_are_limited_per_real_address(tmp_path, clock):
    c = TestClient(make(tmp_path, clock, trust_proxy_hops=1))
    for _ in range(10):
        assert join(c, "198.51.100.1").status_code == 200
    assert join(c, "198.51.100.1").status_code == 429                 # that address used its 10 joins
    assert join(c, "198.51.100.2").status_code == 200                 # another judge is not throttled with them


def test_without_trust_everyone_shares_the_proxy_address_and_the_limit(tmp_path, clock):
    c = TestClient(make(tmp_path, clock))
    codes = [join(c, f"198.51.100.{i}").status_code for i in range(12)]
    assert codes[:10] == [200] * 10 and codes[10] == 429             # why trust must be configured when hosted


def test_join_limits_are_configurable_and_default_unchanged(tmp_path, clock):
    c = TestClient(make(tmp_path, clock, join_per_min=2))
    assert [join(c).status_code for _ in range(3)] == [200, 200, 429]


# ---- join links for a hosted address ------------------------------------------------------------------
def test_build_url_uses_the_public_base_and_keeps_the_hotspot_default():
    items = joinqr.build_url("https://app.example.com/", "config/topology.yaml")
    urls = [u for _, u in items]
    assert "https://app.example.com/driver.html" in urls and all(u.startswith("https://app.example.com/") for u in urls)
    assert any("?gate=ext_" in u for u in urls)
    assert joinqr.build("192.168.137.1", 8000, "config/topology.yaml")[0][1] == "http://192.168.137.1:8000/driver.html"


def test_joinqr_main_rejects_a_url_without_a_scheme(monkeypatch):
    monkeypatch.setattr("sys.argv", ["joinqr", "--url", "app.example.com"])
    with pytest.raises(SystemExit) as e:
        joinqr.main()
    assert "http" in str(e.value)
