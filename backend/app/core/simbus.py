"""Out-of-band command channel from the backend to the virtual tabletop (the 'puppeteer').

The scenario engine pushes world commands (pin a queue, stall a vehicle, drop the camera feed ...). The simulator
polls them over the signed device API as role `simulator`. With a PHYSICAL tabletop nobody polls, and the scenario
shows the human the matching on-table cue instead - the same script drives both.
"""
import threading


class SimBus:
    def __init__(self, clock):
        self._clock, self._lock = clock, threading.Lock()
        self.seq, self.items, self.last_poll = 0, [], None

    def push(self, cmd: dict) -> int:
        with self._lock:
            self.seq += 1
            self.items.append((self.seq, dict(cmd)))
            del self.items[:-200]
            return self.seq

    def poll(self, after):
        """after=None: just report the latest cursor (a fresh simulator must not replay history)."""
        with self._lock:
            self.last_poll = self._clock()
            if after is None:
                return [], self.seq
            return [{"seq": s, "cmd": c} for s, c in self.items if s > after], self.seq

    def connected(self, within_s: float = 5.0) -> bool:
        return self.last_poll is not None and self._clock() - self.last_poll < within_s
