"""Road users: pseudonymous phone sessions that drive a virtual car along a planned route and receive ONLY the
alerts relevant to them (emergency vehicle ahead, incident / hazard ahead, closed road, suggested alternative).

Privacy by design: a session is a random token + a pseudonym ("Car 7"); no name, no device id, no stored history. It lives in
memory and disappears at arrival or after 10 minutes idle. A user can only ever read their own view, and that view contains
fixed-template text and a coarse ETA band - never the emergency vehicle's id, route or position.
Relevance is decided here on the server, never on the client.
"""
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

from .ev_warnings import LIVE, render
from .routing import dijkstra, route_cost

PACE = 3.0              # table seconds -> real seconds for simulated driving (same pace as the scripted EV)
AHEAD_S = 45.0          # tell the user about incidents/hazards this far ahead on their route
KEEP_TERMINAL_S = 8.0   # keep a cancelled/cleared alert visible this long so the user sees the all-clear
IDLE_TTL_S = 600
MAX_USERS = 200
EV_SLACK_S = 0.0        # the EV must enter a road no earlier than the user: only then does it come up BEHIND them
SUGGEST_MIN_GAIN = 0.15
WHAT = {"congestion_spike": "heavy queue", "stalled_vehicle": "stalled vehicle",
        "pothole": "pothole", "debris": "debris on the road", "wet_surface": "wet or slippery surface"}


@dataclass
class RoadUser:
    id: str
    token: str
    origin: str
    destination: str
    route: list
    edge: str
    progress: float
    created: float
    seen: float
    simulated: bool = False
    status: str = "driving"            # driving | arrived
    arrived_at: Optional[float] = None
    alerts: dict = field(default_factory=dict)
    suggestion: Optional[dict] = None
    version: int = 0
    ev_route_ver: dict = field(default_factory=dict)      # EV alert key -> the EV route version this user last saw


class RoadUserManager:
    def __init__(self, topology, store, ev, clock=None):
        self.topo, self.store, self.ev = topology, store, ev
        self.clock = clock or store.clock
        self.users = {}                      # token -> RoadUser
        self.event_sources = []              # callables -> [{key, kind, edge, what, delay}]
        self.metrics = None                  # optional metrics sink
        self._lock = threading.RLock()
        self._last = None
        self._n = 0

    # ---- text helpers ----------------------------------------------------------------------------
    def _lab(self, node: str) -> str:
        return self.topo.node_labels.get(node, node)

    def road_text(self, edge: str) -> str:
        e = self.topo.edges[edge]
        return f"{self._lab(e.src)} to {self._lab(e.dst)}"

    def route_text(self, route: list) -> str:
        if not route:
            return ""
        nodes = [self.topo.edges[route[0]].src] + [self.topo.edges[e].dst for e in route]
        return " → ".join(self._lab(n) for n in nodes)

    # ---- sessions --------------------------------------------------------------------------------
    def join(self, origin: str, destination: str, simulated: bool = False) -> RoadUser:
        t = self.topo
        ext = [n for n in t.nodes if n not in t.intersections]
        if origin not in ext or destination not in ext or origin == destination:
            raise ValueError("origin and destination must be two different external entrances")
        with self._lock:
            if len(self.users) >= MAX_USERS:
                raise OverflowError("too many active road users")
            res = dijkstra(t, self.ev._costs(), origin, destination)
            if res is None:
                raise ValueError("no route between those points")
            self._n += 1
            now = self.clock()
            u = RoadUser(f"Car {self._n}", secrets.token_urlsafe(16), origin, destination, res[0], res[0][0], 0.0, now, now, simulated)
            self.users[u.token] = u
            self._count("road_user_joined")
            return u

    def leave(self, token: str) -> bool:
        with self._lock:
            return self.users.pop(token, None) is not None

    def get(self, token: str) -> Optional[RoadUser]:
        with self._lock:
            u = self.users.get(token)
            if u:
                u.seen = self.clock()
            return u

    def _count(self, name: str):
        if self.metrics:
            self.metrics.count(name)

    # ---- time ------------------------------------------------------------------------------------
    def tick(self, advance: bool = True):
        """advance=False re-evaluates alerts without moving the cars: used the instant an emergency vehicle reports a new position."""
        with self._lock:
            now = self.clock()
            dt = 0.0 if (self._last is None or not advance) else max(0.0, now - self._last)
            if advance:
                self._last = now
            evs = []
            for s in self.ev.sessions.values():
                if s.status == "active" and s.edge is not None:
                    evs.append((s, self.ev.eta_table(s)))
            events = [e for src in self.event_sources for e in src()]
            for token, u in list(self.users.items()):
                if u.status == "arrived" and now - u.arrived_at > KEEP_TERMINAL_S:
                    del self.users[token]
                    continue
                if not u.simulated and now - u.seen > IDLE_TTL_S:
                    del self.users[token]
                    continue
                if u.status == "driving":
                    self._advance(u, dt, now)
                self._update(u, now, evs, events)

    def _advance(self, u: RoadUser, dt: float, now: float):
        e = self.topo.edges[u.edge]
        u.progress += dt / (e.length / e.speed * PACE)
        while u.progress >= 1.0:
            i = u.route.index(u.edge)
            if i + 1 >= len(u.route):
                u.status, u.progress, u.arrived_at = "arrived", 1.0, now
                u.version += 1
                return
            u.edge, u.progress = u.route[i + 1], u.progress - 1.0

    def _table(self, u: RoadUser) -> list:
        costs = self.ev._costs()
        out, t = [], 0.0
        for k in range(u.route.index(u.edge), len(u.route)):
            e = u.route[k]
            dur = costs.get(e, self.topo.edges[e].length / self.topo.edges[e].speed)
            if e == u.edge:
                dur *= (1 - u.progress)
            out.append({"edge": e, "eta_start": t, "eta_end": t + dur})
            t += dur
        return out

    # ---- alert state ----------------------------------------------------------------------------
    def _set(self, u: RoadUser, key: str, kind: str, state: str, text: str, band: str = None, place: str = None, now: float = 0.0):
        a = u.alerts.get(key)
        if a and a["state"] == state and a["text"] == text and a.get("band") == band:
            return
        if a is None:
            a = u.alerts[key] = {"id": key, "kind": kind, "history": [], "place": place}
        a.update(state=state, text=text, band=band, since=now, place=place or a.get("place"))
        a["history"].append(state)
        u.version += 1
        self._count(f"road_user_alert.{kind}.{state}")

    def _update(self, u: RoadUser, now: float, evs: list, events: list):
        for k in [k for k, a in u.alerts.items()
                  if (a["state"] not in LIVE and now - a["since"] > KEEP_TERMINAL_S) or (a["kind"] == "route" and now - a["since"] > 12.0)]:
            del u.alerts[k]
            u.version += 1
        if u.status != "driving":
            return
        utab = self._table(u)
        self._closures(u, now)
        self._suggest(u)
        self._events_ahead(u, utab, events, now)
        self._ev_alerts(u, utab, evs, now)

    # ---- routes: forced reroute on closure, optional suggestion -------------------------------------
    def _closures(self, u: RoadUser, now: float):
        closed = self.store.all_closed()
        ahead = u.route[u.route.index(u.edge) + 1:]
        if not any(e in closed for e in ahead):
            return
        node = self.topo.edges[u.edge].dst
        res = dijkstra(self.topo, self.ev._costs(), node, u.destination)
        if res is None:
            return
        u.route = [u.edge] + res[0]
        u.version += 1
        self._set(u, "route", "route", "active", render("route_closed", route=self.route_text(u.route)), now=now)

    def _suggest(self, u: RoadUser):
        costs = self.ev._costs()
        node = self.topo.edges[u.edge].dst
        rest = u.route[u.route.index(u.edge) + 1:]
        if not rest:
            u.suggestion = None
            return
        cur = route_cost(rest, costs, self.topo)
        res = dijkstra(self.topo, costs, node, u.destination)
        sug = None
        if res and res[0] != rest and cur < float("inf") and res[1] < cur * (1 - SUGGEST_MIN_GAIN) and cur - res[1] >= 3.0:
            alt = [u.edge] + res[0]
            sug = {"route": alt, "saves_s": int(round(cur - res[1])),
                   "text": render("suggest_route", route=self.route_text(alt), saves=int(round(cur - res[1])))}
        if (sug and u.suggestion and sug["route"] == u.suggestion["route"]) or (sug is None and u.suggestion is None):
            return
        u.suggestion = sug
        u.version += 1

    def accept_suggestion(self, token: str) -> bool:
        with self._lock:
            u = self.users.get(token)
            if not u or not u.suggestion:
                return False
            u.route = list(u.suggestion["route"])
            u.suggestion = None
            u.version += 1
            self._count("road_user_route_accepted")
            return True

    # ---- incidents / hazards ahead ----------------------------------------------------------------------
    def _events_ahead(self, u: RoadUser, utab: list, events: list, now: float):
        by_edge = {r["edge"]: r for r in utab}
        seen = set()
        for ev in events:
            r = by_edge.get(ev["edge"])
            if r is None or r["eta_start"] > AHEAD_S:
                continue
            seen.add(ev["key"])
            tpl, kw = ("hazard_ahead", {"road": self.road_text(ev["edge"]), "what": ev["what"]}) if ev["kind"] == "hazard" else \
                      ("incident_ahead", {"road": self.road_text(ev["edge"]), "what": ev["what"], "delay": int(round(ev.get("delay", 0)))})
            self._set(u, ev["key"], ev["kind"], "active", render(tpl, **kw), now=now, place=ev["edge"])
        existing = {ev["key"] for ev in events}
        for key, a in list(u.alerts.items()):
            if a["kind"] in ("incident", "hazard") and a["state"] == "active" and key not in seen:
                if key in existing:                    # still there, but this user is no longer heading into it (rerouted): say nothing false
                    del u.alerts[key]
                    u.version += 1
                    continue
                road = self.road_text(a["place"]) if a.get("place") else ""
                self._set(u, key, a["kind"], "cleared", render("hazard_cleared" if a["kind"] == "hazard" else "incident_cleared", road=road), now=now)

    # ---- emergency vehicle relevance ----------------------------------------------------------------------
    def _relevance(self, u: RoadUser, utab: list, s, etab: list):
        """(edge, eta_s) where the EV will come up behind this user soon, else None."""
        by_edge = {r["edge"]: r for r in etab}
        horizon = self.topo.ev.warn_horizon_s
        for r in utab:
            e = r["edge"]
            er = by_edge.get(e)
            if er is None or er["eta_start"] > horizon:
                continue
            if e == u.edge:
                if e == s.edge:
                    if u.progress + 1e-6 >= s.progress:               # EV behind the user on the same road
                        ed = self.topo.edges[e]
                        return e, max(0.0, u.progress - s.progress) * ed.length / ed.speed
                    continue                                          # EV already ahead of the user
                ed = self.topo.edges[e]
                return e, er["eta_start"] + u.progress * ed.length / ed.speed
            if er["eta_start"] + EV_SLACK_S >= r["eta_start"]:       # the EV enters this road AFTER the user: it comes up behind them
                return e, er["eta_start"]
        return None

    def _ev_alerts(self, u: RoadUser, utab: list, evs: list, now: float):
        active_keys = set()
        for s, etab in evs:
            key = f"ev:{s.id}"
            active_keys.add(key)
            prev = u.alerts.get(key)
            live = bool(prev and prev["state"] in LIVE)
            rerouted = u.ev_route_ver.get(key, s.route_version) != s.route_version      # the EV's route changed since this user last looked
            u.ev_route_ver[key] = s.route_version
            if s.stale:
                if live:
                    self._set(u, key, "ev", "cancelled_stale", render("ev_stale"), now=now)
                continue
            rel = self._relevance(u, utab, s, etab)
            if rel is None:
                if live:
                    passed = prev["place"] in s.passed or (prev["place"] == s.edge and s.progress > u.progress)
                    self._set(u, key, "ev", "cancelled_passed" if passed else "cancelled_route_change",
                              render("ev_passed" if passed else "ev_route_changed"), now=now)
                continue
            place, eta = rel
            band = "imminent" if eta <= self.topo.ev.imminent_s else "soon"
            origin = self.ev.label(place)
            if not live:
                st = "redirected" if rerouted else "active"
                self._set(u, key, "ev", st, render("ev_redirected" if st == "redirected" else "approaching", origin=origin), band, place, now)
            elif prev["place"] != place:
                st = "redirected" if rerouted else "updated"
                self._set(u, key, "ev", st, render("ev_redirected" if st == "redirected" else "ev_updated", origin=origin), band, place, now)
            elif prev["band"] != band:
                self._set(u, key, "ev", "updated", render("ev_updated", origin=origin), band, place, now)
        for key, a in list(u.alerts.items()):                          # EV session ended: the all-clear
            if a["kind"] == "ev" and a["state"] in LIVE and key not in active_keys:
                self._set(u, key, "ev", "expired", render("ev_passed"), now=now)

    # ---- views ---------------------------------------------------------------------------------------
    def view(self, u: RoadUser) -> dict:
        """What a road user may see about themselves. Nothing about any other vehicle or user."""
        order = {"ev": 0, "route": 1, "incident": 2, "hazard": 3}
        alerts = sorted(u.alerts.values(), key=lambda a: (a["state"] not in LIVE and a["state"] != "active", order.get(a["kind"], 9)))
        return {"id": u.id, "status": u.status, "edge": u.edge, "progress": round(u.progress, 2),
                "from": self._lab(u.origin), "to": self._lab(u.destination),
                "route_text": self.route_text(u.route),
                "alerts": [{"id": a["id"], "kind": a["kind"], "state": a["state"], "text": a["text"], "band": a.get("band")} for a in alerts],
                "suggestion": None if not u.suggestion else {"text": u.suggestion["text"], "saves_s": u.suggestion["saves_s"]},
                "version": u.version}

    def wait_change(self, token: str, after: int, timeout: float = 10.0):
        """Long-poll: return as soon as the user's view changes (phones get pushed updates within ~0.2 s)."""
        end = time.monotonic() + max(0.0, min(timeout, 25.0))
        while True:
            with self._lock:
                u = self.users.get(token)
                if u is None:
                    return None
                u.seen = self.clock()
                if u.version != after:
                    return self.view(u)
            if time.monotonic() >= end:
                with self._lock:
                    u = self.users.get(token)
                    return self.view(u) if u else None
            time.sleep(0.1)

    def refresh(self):
        self.tick(advance=False)

    def snapshot(self) -> list:
        """Operator view: pseudonyms and alert states only."""
        with self._lock:
            return [{"id": u.id, "simulated": u.simulated, "status": u.status, "edge": u.edge, "progress": round(u.progress, 2),
                     "alerts": {a["id"]: a["state"] for a in u.alerts.values()}} for u in self.users.values()]
