import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def _load_dotenv(path: Path):
    """Tiny .env reader; real environment variables win. Never logs values."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv(ROOT / ".env")


def _flag(name: str) -> bool:
    return os.getenv(name, "false").strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Settings:
    db_path: str = field(default_factory=lambda: os.getenv("FLOWSYNC_DB_PATH", str(ROOT / "flowsync.db")))
    topology_path: str = field(default_factory=lambda: os.getenv("FLOWSYNC_TOPOLOGY_PATH", str(ROOT / "config" / "topology.yaml")))
    devices_path: str = field(default_factory=lambda: os.getenv("FLOWSYNC_DEVICES_PATH", str(ROOT / "config" / "devices.yaml")))
    operator_password: str = field(default_factory=lambda: os.getenv("FLOWSYNC_OPERATOR_PASSWORD", "operator-dev"))
    viewer_password: str = field(default_factory=lambda: os.getenv("FLOWSYNC_VIEWER_PASSWORD", "viewer-dev"))
    admin_password: str = field(default_factory=lambda: os.getenv("FLOWSYNC_ADMIN_PASSWORD", "admin-dev"))
    image_dir: str = field(default_factory=lambda: os.getenv("FLOWSYNC_IMAGE_DIR", str(ROOT / "data" / "images")))
    controller_autostart: bool = True
    session_ttl_s: int = 8 * 3600
    # LLM reasoning layer: any OpenAI-compatible chat-completions endpoint. Off unless fully configured.
    llm_enabled: bool = field(default_factory=lambda: _flag("LLM_ENABLED"))
    llm_base_url: str = field(default_factory=lambda: os.getenv("LLM_BASE_URL", ""))
    llm_model: str = field(default_factory=lambda: os.getenv("LLM_MODEL", ""))
    llm_api_key: str = field(default_factory=lambda: os.getenv("LLM_API_KEY", ""), repr=False)
    llm_timeout_s: float = 15.0
    llm_deadline_s: float = 25.0
    llm_max_tool_calls: int = 5
    llm_max_tokens: int = 600


def load_yaml(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)
