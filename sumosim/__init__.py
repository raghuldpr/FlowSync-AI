"""SUMO view of the FlowSync triangle. Optional: the Python virtual tabletop (simulator/) stays the fallback."""
import os
import sys
from pathlib import Path

_backend = str(Path(__file__).resolve().parents[1] / "backend")
if _backend not in sys.path:
    sys.path.insert(0, _backend)


def sumo_home() -> str:
    """SUMO_HOME from the environment, else the one shipped by `pip install eclipse-sumo`. '' if neither."""
    home = os.environ.get("SUMO_HOME", "")
    if not home:
        try:
            import sumo
            home = sumo.SUMO_HOME
        except ImportError:
            return ""
        os.environ["SUMO_HOME"] = home
    return home


def available() -> bool:
    if not sumo_home():
        return False
    try:
        import traci  # noqa: F401
    except ImportError:
        return False
    return os.path.exists(os.path.join(sumo_home(), "bin", "netconvert.exe" if os.name == "nt" else "netconvert"))


def binary(name: str) -> str:
    return os.path.join(sumo_home(), "bin", name + (".exe" if os.name == "nt" else ""))
