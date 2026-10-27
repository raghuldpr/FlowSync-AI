"""Reference signal state machine for ONE intersection.

This is the behaviour the ESP32 firmware must reproduce (it is the C++ port's spec and test oracle).
Time is the device's own monotonic clock in ms; no wall-clock sync is assumed.

Guarantees by construction:
  * at most one axis is non-red at any moment (no conflicting greens);
  * every change of green axis goes GREEN -> YELLOW -> ALL_RED -> GREEN(other);
  * boots into ALL_RED;
  * a command is honoured only within valid_for_ms of its receipt, and only if its seq is newer;
  * with the backend lost, a fixed-time cycle runs.
"""
from typing import Optional

GREEN, YELLOW, ALL_RED = "GREEN", "YELLOW", "ALL_RED"


def other(axis: str) -> str:
    return "B" if axis == "A" else "A"


class SignalFSM:
    def __init__(self, timing, now_ms: int = 0):
        self.t = timing
        self.phase = ALL_RED
        self.axis = "B"          # axis that "just cleared"; first green goes to A
        self.next_axis = "A"
        self.since = now_ms
        self.backend_ok = True
        self._target: Optional[str] = None
        self._target_until = 0
        self._last_seq = -1

    # ---- input -------------------------------------------------------------------------------
    def receive_command(self, seq: int, target: str, valid_for_ms: int, now_ms: int):
        """Returns (accepted, reason)."""
        if target not in ("A", "B"):
            return False, "bad_target"
        if not (500 <= valid_for_ms <= 10000):
            return False, "bad_validity"
        if seq <= self._last_seq:
            return False, "stale_seq"
        self._last_seq = seq
        self._target = target
        self._target_until = now_ms + valid_for_ms
        return True, "ok"

    def set_backend_ok(self, ok: bool):
        self.backend_ok = ok

    # ---- time --------------------------------------------------------------------------------
    def _enter(self, phase: str, now_ms: int):
        self.phase = phase
        self.since = now_ms

    def _desired(self, now_ms: int) -> Optional[str]:
        if not self.backend_ok:
            # fixed-time fallback: alternate after fallback_green_ms
            if now_ms - self.since >= self.t.fallback_green_ms:
                return other(self.axis)
            return self.axis
        if self._target is not None and now_ms <= self._target_until:
            return self._target
        return None   # no valid command: hold

    def tick(self, now_ms: int):
        elapsed = now_ms - self.since
        if self.phase == GREEN:
            want = self._desired(now_ms)
            if want and want != self.axis:
                min_g = self.t.min_green_ms if self.backend_ok else self.t.fallback_green_ms
                if elapsed >= min_g:
                    self.next_axis = want
                    self._enter(YELLOW, now_ms)
        elif self.phase == YELLOW:
            if elapsed >= self.t.yellow_ms:
                self.next_axis = other(self.axis)
                self._enter(ALL_RED, now_ms)
        else:  # ALL_RED
            if elapsed >= self.t.all_red_ms:
                self.axis = self.next_axis
                self._enter(GREEN, now_ms)

    # ---- output ------------------------------------------------------------------------------
    def lamps(self) -> dict:
        lamps = {"A": "R", "B": "R"}
        if self.phase == GREEN:
            lamps[self.axis] = "G"
        elif self.phase == YELLOW:
            lamps[self.axis] = "Y"
        return lamps

    def state(self, now_ms: int) -> dict:
        return {
            "phase": self.phase,
            "axis": self.axis,
            "next_axis": self.next_axis,
            "elapsed_ms": now_ms - self.since,
            "lamps": self.lamps(),
            "backend_ok": self.backend_ok,
        }
