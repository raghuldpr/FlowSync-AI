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


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


def _under_data_dir(env_name: str, data_name: str, local_default: Path) -> str:
    """An explicit env var wins; else FLOWSYNC_DATA_DIR/<name> (a hosted app's writable folder); else the repository default."""
    explicit = os.getenv(env_name)
    if explicit:
        return explicit
    data_dir = os.getenv("FLOWSYNC_DATA_DIR", "").strip()
    return str(Path(data_dir) / data_name) if data_dir else str(local_default)


@dataclass
class Settings:
    db_path: str = field(default_factory=lambda: _under_data_dir("FLOWSYNC_DB_PATH", "flowsync.db", ROOT / "flowsync.db"))
    topology_path: str = field(default_factory=lambda: os.getenv("FLOWSYNC_TOPOLOGY_PATH", str(ROOT / "config" / "topology.yaml")))
    devices_path: str = field(default_factory=lambda: os.getenv("FLOWSYNC_DEVICES_PATH", str(ROOT / "config" / "devices.yaml")))
    operator_password: str = field(default_factory=lambda: os.getenv("FLOWSYNC_OPERATOR_PASSWORD", "operator-dev"))
    viewer_password: str = field(default_factory=lambda: os.getenv("FLOWSYNC_VIEWER_PASSWORD", "viewer-dev"))
    admin_password: str = field(default_factory=lambda: os.getenv("FLOWSYNC_ADMIN_PASSWORD", "admin-dev"))
    image_dir: str = field(default_factory=lambda: _under_data_dir("FLOWSYNC_IMAGE_DIR", "images", ROOT / "data" / "images"))
    # Hosting behind a reverse proxy (e.g. Zoho Catalyst AppSail). Off by default: the client address is then the direct peer.
    public_url: str = field(default_factory=lambda: os.getenv("FLOWSYNC_PUBLIC_URL", "").strip().rstrip("/"))
    trust_proxy_hops: int = field(default_factory=lambda: max(0, _int("FLOWSYNC_TRUST_PROXY_HOPS", 0)))   # 0 = do not read X-Forwarded-For
    join_per_min: int = field(default_factory=lambda: max(1, _int("FLOWSYNC_JOIN_PER_MIN", 10)))           # guest joins per client address
    join_ambulance_per_min: int = field(default_factory=lambda: max(1, _int("FLOWSYNC_JOIN_AMBULANCE_PER_MIN", 3)))
    controller_autostart: bool = True
    session_ttl_s: int = 8 * 3600
    # LLM reasoning layer: any OpenAI-compatible chat-completions endpoint. Off unless fully configured.
    llm_enabled: bool = field(default_factory=lambda: _flag("LLM_ENABLED"))
    llm_base_url: str = field(default_factory=lambda: os.getenv("LLM_BASE_URL", ""))
    llm_model: str = field(default_factory=lambda: os.getenv("LLM_MODEL", ""))
    llm_api_key: str = field(default_factory=lambda: os.getenv("LLM_API_KEY", ""), repr=False)
    llm_timeout_s: float = field(default_factory=lambda: _float("LLM_TIMEOUT_S", 15.0))
    llm_deadline_s: float = field(default_factory=lambda: _float("LLM_DEADLINE_S", 25.0))     # keep under a host's request timeout (Catalyst: 30 s)
    llm_max_tool_calls: int = 5
    llm_max_tokens: int = 600


def load_yaml(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)
