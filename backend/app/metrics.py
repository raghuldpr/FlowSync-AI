"""In-process counters and latency series (no external monitoring needed for a demo): p50 / p95 / max per series."""
import threading
import time
from collections import Counter, defaultdict, deque


class Metrics:
    def __init__(self, keep: int = 500):
        self._lock = threading.Lock()
        self.counters = Counter()
        self.series = defaultdict(lambda: deque(maxlen=keep))

    def count(self, name: str, n: int = 1):
        with self._lock:
            self.counters[name] += n

    def observe(self, name: str, value: float):
        with self._lock:
            self.series[name].append(float(value))

    class _Timer:
        def __init__(self, m, name):
            self.m, self.name = m, name

        def __enter__(self):
            self.t = time.perf_counter()
            return self

        def __exit__(self, *exc):
            self.m.observe(self.name, (time.perf_counter() - self.t) * 1000.0)

    def timer(self, name: str):
        """with metrics.timer('ev_position_ms'): ...   records elapsed milliseconds."""
        return Metrics._Timer(self, name)

    @staticmethod
    def _pct(xs: list, p: float) -> float:
        s = sorted(xs)
        return s[min(len(s) - 1, int(round(p * (len(s) - 1))))]

    def snapshot(self) -> dict:
        with self._lock:
            series = {k: {"n": len(v), "p50": round(self._pct(list(v), 0.5), 2), "p95": round(self._pct(list(v), 0.95), 2),
                          "max": round(max(v), 2), "last": round(v[-1], 2)} for k, v in self.series.items() if v}
            return {"counters": dict(self.counters), "series_ms": series}
