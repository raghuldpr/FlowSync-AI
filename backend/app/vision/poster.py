"""Turns pipeline observations into signed device-API posts (traffic counts and EV position)."""
import time

from .pipeline import Observation

TRAFFIC_MAX_CALIB_AGE_S = 3.0      # stop reporting counts if the corner markers have been hidden this long
EV_KEEPALIVE_S = 1.0               # backend treats a location older than 4 s as stale


class Poster:
    def __init__(self, client, source_label: str, post_hz: float, log=print):
        self.client, self.source, self.period, self.log = client, source_label, 1.0 / post_hz, log
        self._last_traffic = -1e9
        self._last_ev = {}
        self.sent_traffic = self.sent_ev = self.errors = 0

    def _post(self, path, body):
        try:
            r = self.client.request("POST", path, body)
            if r.status_code != 200:
                self.errors += 1
                self.log(f"[post] {path} -> {r.status_code}")
                return False
            return True
        except Exception as e:                      # network hiccup must never stop perception
            self.errors += 1
            self.log(f"[post] {path} failed: {type(e).__name__}")
            return False

    def handle(self, obs: Observation, now: float):
        usable = obs.counts and obs.calib_age_s is not None and obs.calib_age_s <= TRAFFIC_MAX_CALIB_AGE_S
        if usable and now - self._last_traffic >= self.period:
            body = {"source": self.source, "readings": [
                {"approach": a, "count": int(n), "stationary_s": float(s)} for a, (n, s) in obs.counts.items()]}
            if self._post("/api/device/traffic", body):
                self.sent_traffic += 1
            self._last_traffic = now
        for e in obs.ev:
            prev = self._last_ev.get(e.vehicle_id)
            changed = prev is None or prev[0] != e.edge or abs(prev[1] - e.progress) >= 0.02 or now - prev[2] >= EV_KEEPALIVE_S
            if changed:
                if self._post("/api/device/ev-position", {"vehicle_id": e.vehicle_id, "edge": e.edge,
                                                             "progress": round(min(1.0, max(0.0, e.progress)), 3),
                                                             "source": "simulated" if self.source == "simulated" else "camera"}):
                    self.sent_ev += 1
                self._last_ev[e.vehicle_id] = (e.edge, e.progress, now)
