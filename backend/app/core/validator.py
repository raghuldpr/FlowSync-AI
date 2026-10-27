"""Deterministic safety rules. Shared by the controller, EV priority and (later) LLM proposals."""


def lamps_safe(lamps: dict) -> bool:
    """Conflict rule for a two-movement intersection: at most one axis may be non-red."""
    return sum(1 for v in lamps.values() if v != "R") <= 1


def validate_command(topology, cmd: dict):
    """Returns (ok, reason). Applied before any command is issued to a device."""
    if cmd.get("intersection") not in topology.intersections:
        return False, "unknown_intersection"
    if cmd.get("target") not in ("A", "B"):
        return False, "bad_target"
    v = cmd.get("valid_for_ms")
    if not isinstance(v, int) or not (500 <= v <= 10000):
        return False, "bad_validity"
    return True, "ok"


def check_switch(timing, current_axis: str, elapsed_ms: int, target: str):
    """Can the intersection start a change to `target` now? Returns (ok, reason).

    Even emergency requests honour minimum green; the firmware then inserts yellow + all-red.
    """
    if target == current_axis:
        return True, "already_green"
    if elapsed_ms < timing.min_green_ms:
        return False, "min_green_not_elapsed"
    return True, "switch_allowed"
