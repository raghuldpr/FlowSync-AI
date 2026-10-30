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
V2V_COOLDOWN_S = 15.0   # a guest ambulance may raise one emergency alert per this many seconds
V2V_ACTIVE_S = 30.0     # a raised alert stays live this long unless the ambulance finishes first
V2V_HORIZON_S = 30.0    # 'nearby' = the ambulance reaches the user's route within this long (wider than the 20 s infrastructure warning)
MAX_GUEST_AMBULANCES = 3
EV_SLACK_S = 0.0        # the EV must enter a road no earlier than the user: only then does it come up BEHIND them
SUGGEST_MIN_GAIN = 0.15
ROLES = ("vehicle", "ambulance")
COLORS = ["red", "blue", "green", "yellow", "purple", "orange", "teal", "pink"]
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
    role: str = "vehicle"                                 # vehicle | ambulance (chosen by the guest, enforced here)
    color: str = "blue"
    plate: str = ""
    ev_vehicle_id: Optional[str] = None                   # ambulance guests: the EV session they drive
    last_alert_t: float = -1e9                            # ambulance guests: when they last raised a V2V alert
    made_way: set = field(default_factory=set)            # ids of emergency vehicles this user was warned about (for the trip receipt)


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
        self.v2v = {}                        # EV session id -> {until, from, t}: live simulated V2V broadcasts
        self.v2v_log = []

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
    def join(self, origin: str, destination: str, simulated: bool = False, role: str = "vehicle") -> RoadUser:
        t = self.topo
        if role not in ROLES:
            raise ValueError("role must be 'vehicle' or 'ambulance'")
        ext = [n for n in t.nodes if n not in t.intersections]
        if origin not in ext or destination not in ext or origin == destination:
            raise ValueError("origin and destination must be two different external entrances")
        with self._lock:
            if len(self.users) >= MAX_USERS:
                raise OverflowError("too many active road users")
            if role == "ambulance" and sum(1 for x in self.users.values() if x.role == "ambulance") >= MAX_GUEST_AMBULANCES:
                raise OverflowError("too many guest ambulances right now")
            res = dijkstra(t, self.ev._costs(), origin, destination)
            if res is None:
                raise ValueError("no route between those points")
            self._n += 1
            now = self.clock()
            u = RoadUser(f"Car {self._n}", secrets.token_urlsafe(16), origin, destination, res[0], res[0][0], 0.0, now, now, simulated,
                         role=role, color=COLORS[(self._n - 1) % len(COLORS)], plate=f"FS-{self._n:03d}")
            if role == "ambulance":
                u.id = f"Ambulance {self._n}"
                u.ev_vehicle_id = f"GUEST-{self._n}"
                s = self.ev.declare(u.ev_vehicle_id, origin, destination, "guest", "guest")      # same EV path as an operator-declared one
                u.route = list(s.route)
                u.edge = u.route[0]
                self.ev.position(u.ev_vehicle_id, u.edge, 0.0)
            self.users[u.token] = u
            self._count("road_user_joined")
            if role == "ambulance":
                self._count("guest_ambulance_started")
            return u

    def leave(self, token: str) -> bool:
        with self._lock:
            u = self.users.pop(token, None)
            if u is not None and u.ev_vehicle_id:
                s = self._ev_session(u)
                if s is not None:
                    self.ev.end(s.id, "guest_left")
            return u is not None

    def _ev_session(self, u: RoadUser):
        return self.ev._live_session_for(u.ev_vehicle_id) if u.ev_vehicle_id else None

    # ---- V2V: a guest ambulance raises an emergency alert (SIMULATED message, delivered by the backend) -------------
    def raise_alert(self, token: str) -> dict:
        """Only the ambulance role may do this; one alert per V2V_COOLDOWN_S. Who hears it is decided in tick() by server-side relevance."""
        with self._lock:
            u = self.users.get(token)
            if u is None:
                raise KeyError("unknown or expired session")
            if u.role != "ambulance":
                raise PermissionError("only an ambulance can raise an emergency alert")
            s = self._ev_session(u)
            if u.status != "driving" or s is None:
                raise ValueError("no active ambulance trip")
            now = self.clock()
            wait = V2V_COOLDOWN_S - (now - u.last_alert_t)
            if wait > 0:
                raise RuntimeError(f"wait {int(wait) + 1} s before raising another alert")
            u.last_alert_t = now
            self.v2v[s.id] = {"until": now + V2V_ACTIVE_S, "from": u.id, "t": now}
            u.version += 1
            self._count("v2v_alert_raised")
            self.tick(advance=False)                     # deliver now, not at the next second
            live = f"v2v:{s.id}"
            heard = sum(1 for x in self.users.values() if x is not u and live in x.alerts and x.alerts[live]["state"] in LIVE)
            drivers = sum(1 for x in self.users.values() if x is not u and x.status == "driving")
            self.v2v_log.append({"t": round(now, 1), "from": u.id, "heard": heard, "drivers": drivers})
            del self.v2v_log[:-50]
            return {"ok": True, "simulated": True, "reached": heard, "cooldown_s": V2V_COOLDOWN_S}

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
            for token, u in list(self.users.items()):
                if u.status == "arrived" and now - u.arrived_at > KEEP_TERMINAL_S:
                    del self.users[token]
                    continue
                if not u.simulated and now - u.seen > IDLE_TTL_S:
                    del self.users[token]
                    if (s := self._ev_session(u)):
                        self.ev.end(s.id, "guest_idle")
                    continue
                if u.status == "driving":
                    self._advance(u, dt, now)
                    if u.ev_vehicle_id:
                        self._report_ambulance(u)
            for sid in [k for k, b in self.v2v.items() if now > b["until"] + KEEP_TERMINAL_S]:
                del self.v2v[sid]
            evs = []
            for s in self.ev.sessions.values():
                if s.status == "active" and s.edge is not None:
                    evs.append((s, self.ev.eta_table(s)))
            events = [e for src in self.event_sources for e in src()]
            for u in list(self.users.values()):
                self._update(u, now, evs, events)

    def _report_ambulance(self, u: RoadUser):
        """A guest ambulance reports its simulated position exactly as the overhead camera would; the EV manager reroutes and prioritises."""
        s = self._ev_session(u)
        if s is None:
            return
        self.ev.position(u.ev_vehicle_id, u.edge, u.progress)
        if s.status == "active" and s.route != u.route and u.edge in s.route:
            u.route = list(s.route)
            u.version += 1

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
        if u.role != "ambulance":                 # a guest ambulance is routed by the EV manager (priority corridor), not by the road-user layer
            self._closures(u, now)
            self._suggest(u)
        self._events_ahead(u, utab, events, now)
        self._ev_alerts(u, utab, evs, now)
        self._v2v_alerts(u, utab, evs, now)

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
    def _relevance(self, u: RoadUser, utab: list, s, etab: list, horizon: float = None):
        """(edge, eta_s) where the EV will come up behind this user soon, else None."""
        by_edge = {r["edge"]: r for r in etab}
        horizon = self.topo.ev.warn_horizon_s if horizon is None else horizon
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
            if s.vehicle_id == u.ev_vehicle_id:
                continue                                              # an ambulance is never warned about itself
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
            u.made_way.add(s.id)
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

    def _v2v_alerts(self, u: RoadUser, utab: list, evs: list, now: float):
        """Simulated V2V: deliver a live broadcast only to users the ambulance will come up behind within V2V_HORIZON_S."""
        by_sess = {s.id: (s, etab) for s, etab in evs}
        for sid, b in self.v2v.items():
            key = f"v2v:{sid}"
            prev = u.alerts.get(key)
            live = bool(prev and prev["state"] in LIVE)
            sess = by_sess.get(sid)
            if sess is None or sess[0].stale or sess[0].vehicle_id == u.ev_vehicle_id:
                if live:
                    self._set(u, key, "v2v", "expired", render("v2v_ended"), now=now)
                continue
            s, etab = sess
            rel = self._relevance(u, utab, s, etab, V2V_HORIZON_S) if now <= b["until"] else None
            if rel is None:
                if live:
                    passed = prev["place"] in s.passed or (prev["place"] == s.edge and s.progress > u.progress)
                    self._set(u, key, "v2v", "cancelled_passed" if passed else "expired",
                              render("ev_passed" if passed else "v2v_ended"), now=now)
                continue
            place, eta = rel
            band = "imminent" if eta <= self.topo.ev.imminent_s else "soon"
            u.made_way.add(sid)
            if live and prev["place"] == place and prev["band"] == band:
                continue
            self._set(u, key, "v2v", "updated" if live else "active", render("v2v_emergency", origin=self.ev.label(place)), band, place, now)

    # ---- views ---------------------------------------------------------------------------------------
    def view(self, u: RoadUser) -> dict:
        """What a road user may see about themselves. Nothing about any other vehicle or user."""
        order = {"v2v": 0, "ev": 0, "route": 1, "incident": 2, "hazard": 3}
        alerts = sorted(u.alerts.values(), key=lambda a: (a["state"] not in LIVE and a["state"] != "active", order.get(a["kind"], 9)))
        v = {"id": u.id, "role": u.role, "color": u.color, "plate": u.plate, "status": u.status, "edge": u.edge,
             "progress": round(u.progress, 2),
             "from": self._lab(u.origin), "to": self._lab(u.destination),
             "route_text": self.route_text(u.route),
             "alerts": [self._alert_view(a) for a in alerts],
             "suggestion": None if not u.suggestion else {"text": u.suggestion["text"], "saves_s": u.suggestion["saves_s"]},
             "version": u.version}
        if u.role == "ambulance":
            v["ambulance"] = self._ambulance_view(u)
        if u.status == "arrived":
            v["receipt"] = {"trip_s": int(round((u.arrived_at or u.seen) - u.created)), "from": v["from"], "to": v["to"],
                            "made_way_for": len(u.made_way),
                            "privacy": "Your session and location are deleted from the server a few seconds after arrival."}
        return v

    @staticmethod
    def _alert_view(a: dict) -> dict:
        out = {"id": a["id"], "kind": a["kind"], "state": a["state"], "text": a["text"], "band": a.get("band")}
        if a["kind"] in ("hazard", "incident"):
            out["place"] = a.get("place")              # the road, so the phone can mark it on its map
        if a["kind"] == "v2v":
            out["simulated"] = True
        return out

    def _ambulance_view(self, u: RoadUser) -> dict:
        """What a guest ambulance driver sees about their OWN trip: priority and route changes. Simulated."""
        s = self._ev_session(u)
        now = self.clock()
        out = {"simulated": True, "can_alert": u.status == "driving",
               "alert_cooldown_s": max(0, int(V2V_COOLDOWN_S - (now - u.last_alert_t) + 0.999)),
               "alert_live": bool(s and s.id in self.v2v and self.v2v[s.id]["until"] >= now)}
        if s is None:
            return out
        eta = sum(r["eta_end"] - r["eta_start"] for r in self.ev.eta_table(s))
        pri = [a for a in self.ev.priority_approaches() if a in s.route]
        out.update(eta_s=int(round(eta)), rerouted=s.route_version > 0, reroutes=s.route_version,
                   priority_at=sorted({self._lab(self.topo.edges[a].dst) for a in pri}))
        return out

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
        """Operator view: pseudonyms, role, colour and alert states only. `warned` = a live emergency-vehicle or V2V alert right now."""
        with self._lock:
            return [{"id": u.id, "role": u.role, "color": u.color, "plate": u.plate, "simulated": u.simulated, "status": u.status,
                     "edge": u.edge, "progress": round(u.progress, 2),
                     "warned": any(a["kind"] in ("ev", "v2v") and a["state"] in LIVE for a in u.alerts.values()),
                     "alerts": {a["id"]: a["state"] for a in u.alerts.values()}} for u in self.users.values()]

    def feed(self) -> dict:
        """Big-screen feed: every car with warned / not warned, the guest ambulances, and the recent simulated V2V broadcasts."""
        with self._lock:
            users = self.snapshot()
            return {"simulated_v2v": True, "users": users,
                    "ambulances": [x for x in users if x["role"] == "ambulance"],
                    "warned": sum(1 for x in users if x["warned"]), "total": len(users),
                    "v2v": [dict(e) for e in self.v2v_log[-10:]]}
