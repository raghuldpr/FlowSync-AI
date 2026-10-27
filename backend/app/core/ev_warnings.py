"""EV warning lifecycle and the ONLY driver-facing wording (fixed, reviewed templates; never LLM text).

States:  active -> updated | redirected  (live)
         -> cancelled_passed | cancelled_route_change | cancelled_stale | expired  (terminal)
"""
LIVE = ("active", "updated", "redirected")
TERMINAL = ("cancelled_passed", "cancelled_route_change", "cancelled_stale", "expired")

TEMPLATES = {
    "approaching": "Emergency vehicle approaching from {origin}. Make way only when it is safe and lawful to do so.",
}

# Review aid: phrases that must never appear in driver-facing text.
FORBIDDEN_PHRASES = ("run the red", "proceed through red", "through the red", "red light", "ignore the signal",
                     "ignore signal", "speed up", "accelerate", "cross the stop", "block the", "do not stop")


def render(template: str, **kw) -> str:
    return TEMPLATES[template].format(**kw)


class WarningLog:
    """Persists warnings and every state transition."""

    def __init__(self, db):
        self.db = db

    def create(self, session_id, approach, state, band, text, now_iso) -> int:
        wid = self.db.execute(
            "INSERT INTO ev_warnings(session_id,approach,state,eta_band,text,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
            (session_id, approach, state, band, text, now_iso, now_iso))
        self._event(wid, None, state, "created", now_iso)
        return wid

    def transition(self, wid, from_state, to_state, reason, now_iso, band=None):
        assert to_state in LIVE + TERMINAL, to_state
        ended = now_iso if to_state in TERMINAL else None
        self.db.execute("UPDATE ev_warnings SET state=?, updated_at=?, ended_at=COALESCE(?,ended_at), "
                        "eta_band=COALESCE(?,eta_band) WHERE id=?", (to_state, now_iso, ended, band, wid))
        self._event(wid, from_state, to_state, reason, now_iso)

    def _event(self, wid, f, t, reason, ts):
        self.db.execute("INSERT INTO ev_warning_events(warning_id,ts,from_state,to_state,reason) VALUES(?,?,?,?,?)",
                        (wid, ts, f, t, reason))
