import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


@dataclass
class Settings:
    db_path: str = field(default_factory=lambda: os.getenv("FLOWSYNC_DB_PATH", str(ROOT / "flowsync.db")))
    topology_path: str = field(default_factory=lambda: os.getenv("FLOWSYNC_TOPOLOGY_PATH", str(ROOT / "config" / "topology.yaml")))
    devices_path: str = field(default_factory=lambda: os.getenv("FLOWSYNC_DEVICES_PATH", str(ROOT / "config" / "devices.yaml")))
    operator_password: str = field(default_factory=lambda: os.getenv("FLOWSYNC_OPERATOR_PASSWORD", "operator-dev"))
    controller_autostart: bool = True
    session_ttl_s: int = 8 * 3600


def load_yaml(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)
