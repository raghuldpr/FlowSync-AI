import pytest
from fastapi.testclient import TestClient

from app.config import Settings, load_yaml
from app.main import create_app

PW = "test-operator-pw"


@pytest.fixture
def clock():
    t = [0.0]
    f = lambda: t[0]
    f.t = t
    return f


@pytest.fixture
def app(tmp_path, clock):
    s = Settings(db_path=str(tmp_path / "t.db"), operator_password=PW, controller_autostart=False,
                 llm_enabled=False, llm_base_url="", llm_model="", llm_api_key="")
    return create_app(s, clock)


@pytest.fixture
def ctx(app):
    return app.state.ctx


@pytest.fixture
def client(app):
    return TestClient(app)


@pytest.fixture
def devices(ctx):
    return ctx.device_specs


@pytest.fixture
def op_headers(client):
    r = client.post("/api/auth/login", json={"username": "operator", "password": PW})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}
