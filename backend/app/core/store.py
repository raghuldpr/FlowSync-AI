"""In-memory live state (latest readings, device-reported signal state, issued commands)."""
import threading
import time
from typing import Callable, Optional


class Store:
    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self.clock = clock                      # seconds, monotonic; injectable for tests
        self._lock = threading.RLock()
        self.traffic = {}                       # approach -> dict(count, recv_t, source)
        self.actual = {}                        # intersection -> dict(state, recv_t, device_id)
        self.commands = {}                      # device_id -> {intersection -> cmd}
        self.last_decision = {}                 # intersection -> (target, reason)
        self._seq = {}                          # device_id -> last issued seq
        self.closed_edges = set()               # routing closures set by operators
        self.incident_closed = set()            # closures caused by open incidents (e.g. stalled vehicle)
        self.last_full = {}                     # intersection -> latest controller decision incl. inputs

    def set_traffic(self, approach: str, count: int, source: str, stationary_s: float = 0.0):
        with self._lock:
            self.traffic[approach] = {"count": count, "recv_t": self.clock(), "source": source,
                                      "stationary_s": stationary_s}

    def fresh_record(self, approach: str, stale_after_s: float):
        with self._lock:
            r = self.traffic.get(approach)
        return r if r and self.clock() - r["recv_t"] <= stale_after_s else None

    def all_closed(self) -> set:
        with self._lock:
            return set(self.closed_edges) | set(self.incident_closed)

    def fresh_traffic(self, stale_after_s: float) -> dict:
        """approach -> count, or None when stale/missing (never a fresh zero)."""
        now = self.clock()
        with self._lock:
            return {a: (r["count"] if now - r["recv_t"] <= stale_after_s else None) for a, r in self.traffic.items()}

    def set_actual(self, intersection: str, state: dict, device_id: str):
        with self._lock:
            self.actual[intersection] = {"state": state, "recv_t": self.clock(), "device_id": device_id}

    def get_actual(self, intersection: str, stale_after_s: float) -> Optional[dict]:
        with self._lock:
            r = self.actual.get(intersection)
        if r is None or self.clock() - r["recv_t"] > stale_after_s:
            return None
        return r["state"]

    def next_seq(self, device_id: str) -> int:
        with self._lock:
            self._seq[device_id] = self._seq.get(device_id, 0) + 1
            return self._seq[device_id]

    def put_command(self, device_id: str, cmd: dict):
        with self._lock:
            prev = self.commands.get(device_id, {}).get(cmd["intersection"])
            cmd["changed_t"] = prev["changed_t"] if prev and prev["target"] == cmd["target"] else self.clock()
            self.commands.setdefault(device_id, {})[cmd["intersection"]] = cmd

    def get_commands(self, device_id: str) -> list:
        with self._lock:
            return list(self.commands.get(device_id, {}).values())
