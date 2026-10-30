import yaml

from app import setup_demo
from app.config import ROOT, load_yaml


def run(tmp_path, *extra):
    return setup_demo.main(["--env", str(tmp_path / ".env"), "--devices-out", str(tmp_path / "devices.demo.yaml"), *extra])


def env_values(path):
    return dict(ln.split("=", 1) for ln in path.read_text(encoding="utf-8").splitlines() if "=" in ln)


def test_generates_private_files_and_prints_no_secret(tmp_path, capsys):
    assert run(tmp_path) == 0
    out = capsys.readouterr()
    env = env_values(tmp_path / ".env")
    devices = yaml.safe_load((tmp_path / "devices.demo.yaml").read_text(encoding="utf-8"))["devices"]
    secrets_ = [env["FLOWSYNC_OPERATOR_PASSWORD"], env["FLOWSYNC_VIEWER_PASSWORD"], env["FLOWSYNC_ADMIN_PASSWORD"]] + [d["key"] for d in devices.values()]
    assert all(len(s) >= 20 for s in secrets_) and len(set(secrets_)) == len(secrets_)
    assert not any(s in out.out + out.err for s in secrets_)
    assert env["FLOWSYNC_DEVICES_PATH"] == str(tmp_path / "devices.demo.yaml")
    assert "change-me" not in (tmp_path / "devices.demo.yaml").read_text(encoding="utf-8")


def test_devices_keep_their_roles_and_ownership(tmp_path):
    run(tmp_path)
    base = load_yaml(str(ROOT / "config" / "devices.yaml"))["devices"]
    new = yaml.safe_load((tmp_path / "devices.demo.yaml").read_text(encoding="utf-8"))["devices"]
    assert set(new) == set(base)
    for name, spec in base.items():
        assert {k: v for k, v in new[name].items() if k != "key"} == {k: v for k, v in spec.items() if k != "key"}
        assert new[name]["key"] != spec["key"]


def test_refuses_to_overwrite_without_force_and_changes_nothing(tmp_path, capsys):
    run(tmp_path)
    before = ((tmp_path / ".env").read_text(encoding="utf-8"), (tmp_path / "devices.demo.yaml").read_text(encoding="utf-8"))
    assert run(tmp_path) == 2
    assert "--force" in capsys.readouterr().err
    assert before == ((tmp_path / ".env").read_text(encoding="utf-8"), (tmp_path / "devices.demo.yaml").read_text(encoding="utf-8"))


def test_force_replaces_secrets_and_keeps_other_env_lines(tmp_path):
    (tmp_path / ".env").write_text("LLM_ENABLED=true\nLLM_MODEL=some-model\n", encoding="utf-8")
    assert run(tmp_path) == 0
    first = env_values(tmp_path / ".env")
    assert first["LLM_MODEL"] == "some-model" and "FLOWSYNC_OPERATOR_PASSWORD" in first
    assert run(tmp_path, "--force") == 0
    second = env_values(tmp_path / ".env")
    assert second["FLOWSYNC_OPERATOR_PASSWORD"] != first["FLOWSYNC_OPERATOR_PASSWORD"]
    assert second["LLM_ENABLED"] == "true" and (tmp_path / ".env").read_text(encoding="utf-8").count("FLOWSYNC_OPERATOR_PASSWORD") == 1


def test_generated_values_are_read_by_the_backend(tmp_path, monkeypatch):
    from app.config import Settings
    run(tmp_path)
    for k, v in env_values(tmp_path / ".env").items():
        monkeypatch.setenv(k, v)
    s = Settings()
    assert s.operator_password not in ("operator-dev", "") and s.devices_path.endswith("devices.demo.yaml")
    assert all("change-me" not in d["key"] for d in load_yaml(s.devices_path)["devices"].values())
