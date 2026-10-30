"""Rehearsal bots: simulated guests that use ONLY the public guest API (no login, no tokens printed).

    python -m simulator.guest_bots --vehicles 5 --ambulance 1            # one checked run against http://localhost:8000
    python -m simulator.guest_bots --vehicles 5 --ambulance 1 --loop     # keep running slowly, for big-screen rehearsal

A run joins vehicle guests (some on the ambulance's route, some that turn off it, some elsewhere), then guest ambulances,
starts the trip, raises the SIMULATED V2V emergency alert, watches the vehicles' own views, and prints a PASS/FAIL table.
The exit code is non-zero when any check fails. Nothing here is a real phone: the bots stand in for phones, and
everything V2V is simulated by the backend.
"""
import argparse
import random
import sys
import time

import httpx

JOIN = "/api/public/road-users"
LIVE = ("active", "updated", "redirected")
HURRY = ("hurry", "speed up", "faster", "rush", "go now")        # warnings must never encourage hurrying
JOIN_LIMIT = 10                                                  # the backend allows 10 joins/min per address


class Check:
    def __init__(self):
        self.rows = []

    def add(self, name, ok, detail=""):
        self.rows.append((name, bool(ok), detail))
        return bool(ok)

    @property
    def ok(self):
        return all(r[1] for r in self.rows)

    def table(self):
        w = max(len(r[0]) for r in self.rows) if self.rows else 10
        lines = [f"{'PASS' if ok else 'FAIL'}  {n.ljust(w)}  {d}".rstrip() for n, ok, d in self.rows]
        lines.append(f"\n{'ALL CHECKS PASSED' if self.ok else 'SOME CHECKS FAILED'} "
                     f"({sum(1 for r in self.rows if r[1])}/{len(self.rows)})")
        return "\n".join(lines)


class Guest:
    """One bot. Holds its own token privately; only its pseudonym (e.g. 'Car 3') is ever printed."""

    def __init__(self, view, token, group):
        self.token, self.group = token, group
        self.id, self.role = view["id"], view["role"]
        self.seen_live = set()          # kinds of alerts that were ever live for this guest
        self.texts = []                 # distinct alert texts seen
        self.passed_text = False        # saw the fixed 'has passed' wording
        self.route_msgs = 0
        self.receipt = None
        self.gone = False
        self.view = view

    def absorb(self, v):
        self.view = v
        for a in v["alerts"]:
            if a["state"] in LIVE and a["kind"] in ("ev", "v2v"):
                self.seen_live.add(a["kind"])
            if a["text"] not in self.texts:
                self.texts.append(a["text"])
            if a["state"] == "cancelled_passed":
                self.passed_text = True
            if a["kind"] == "route" or a["state"] in ("redirected", "cancelled_route_change"):
                self.route_msgs += 1
        if v.get("receipt"):
            self.receipt = v["receipt"]

    @property
    def warned(self):
        return bool(self.seen_live)


class Bots:
    def __init__(self, client, step=time.sleep, say=print):
        """client: an httpx.Client (or the FastAPI TestClient). step(seconds): wait that long (tests advance a fake clock)."""
        self.c, self.step, self.say = client, step, say

    # ---- helpers ----------------------------------------------------------------------------------------------
    def _join(self, role, origin, destination):
        body = {"role": role, "origin": origin, "destination": destination}
        r = self.c.post(JOIN, json=body)
        return r

    def _poll(self, g):
        if g.gone:
            return
        r = self.c.get(f"{JOIN}/{g.token}")
        if r.status_code == 404:
            g.gone = True
            return
        g.absorb(r.json())

    def _entrances(self):
        r = self.c.get("/api/public/topology")
        r.raise_for_status()
        t = r.json()
        nodes = t.get("nodes", {})
        ext = sorted(k for k, n in nodes.items() if n.get("kind") != "intersection")
        return ext or ["ext_W", "ext_E", "ext_S"]

    # ---- one checked run -----------------------------------------------------------------------------------------
    def run_once(self, vehicles=5, ambulances=1, max_s=240, poll_s=1.0, seed=None):
        rnd = random.Random(seed)
        chk = Check()
        if vehicles + ambulances > JOIN_LIMIT:
            chk.add("arguments", False, f"{vehicles}+{ambulances} joins exceed the {JOIN_LIMIT}/min per-address limit; use fewer bots")
            return chk, []
        if not 1 <= ambulances <= 3:
            chk.add("arguments", False, "1 to 3 ambulances (the backend allows at most 3 guest ambulances at once)")
            return chk, []
        ext = self._entrances()
        a_from, a_to = ("ext_W" if "ext_W" in ext else ext[0]), ("ext_S" if "ext_S" in ext else ext[-1])

        # 1. vehicles join FIRST, so they are ahead of the ambulance. Three groups.
        guests, join_ok = [], True
        others = [e for e in ext if e not in (a_from, a_to)] or ext
        for i in range(vehicles):
            if i % 3 == 0:
                grp, o, d = "on-route", a_from, a_to                       # same road as the ambulance: should be warned
            elif i % 3 == 1:
                grp, o, d = "turns-off", a_from, rnd.choice(others)        # shares the first road, then turns away
            else:
                grp, o = "elsewhere", rnd.choice(others)
                d = rnd.choice([e for e in ext if e != o])
            r = self._join("vehicle", o, d)
            if r.status_code != 200:
                join_ok = False
                chk.add(f"vehicle {i + 1} joins", False, f"HTTP {r.status_code}")
                continue
            g = Guest(r.json(), r.json()["token"], grp)
            g.absorb(r.json())
            guests.append(g)
        chk.add("vehicles join as guests (no login, no form)", join_ok and len(guests) == vehicles,
                f"{len(guests)}/{vehicles} joined, roles={sorted({g.role for g in guests})}")

        # 2. role enforcement: a vehicle cannot raise an emergency alert
        if guests:
            r = self.c.post(f"{JOIN}/{guests[0].token}/alert")
            chk.add("a vehicle cannot raise an emergency alert", r.status_code == 403, f"HTTP {r.status_code} (expected 403)")
        r = self.c.post(JOIN, json={"role": "admin"})
        chk.add("unknown roles are refused", r.status_code == 422, f"HTTP {r.status_code} (expected 422)")

        self.step(4.0)                                                     # the cars drive off before the ambulance starts
        for g in guests:
            self._poll(g)

        # 3. ambulances join and start their trip
        ambs = []
        for j in range(ambulances):
            r = self._join("ambulance", a_from, a_to)
            if r.status_code != 200:
                chk.add(f"ambulance {j + 1} starts its trip", False, f"HTTP {r.status_code}")
                continue
            a = Guest(r.json(), r.json()["token"], "ambulance")
            a.absorb(r.json())
            ambs.append(a)
        chk.add("ambulances join and start a trip", len(ambs) == ambulances,
                f"{len(ambs)}/{ambulances}; ids={[a.id for a in ambs]}")
        if not ambs:
            return chk, guests
        chk.add("ambulance view says SIMULATED", all(a.view.get("ambulance", {}).get("simulated") is True for a in ambs))

        # 4. simulated V2V alert, then the rate limit
        self.step(1.0)
        res = [self.c.post(f"{JOIN}/{a.token}/alert") for a in ambs]
        chk.add("ambulance raises a simulated V2V alert",
                all(r.status_code == 200 and r.json().get("simulated") is True for r in res),
                f"HTTP {[r.status_code for r in res]}, reached={[r.json().get('reached') for r in res if r.status_code == 200]}")
        again = self.c.post(f"{JOIN}/{ambs[0].token}/alert")
        chk.add("alert rate limit (cooldown) works", again.status_code == 429, f"second alert straight away: HTTP {again.status_code} (expected 429)")

        # 5. watch until every ambulance has arrived (or time runs out)
        waited = 0.0
        while waited < max_s:
            self.step(poll_s)
            waited += poll_s
            for g in guests + ambs:
                self._poll(g)
            if all(a.gone or a.view["status"] == "arrived" for a in ambs):
                break
        for _ in range(2):                                                 # a last look at the final all-clear states
            for g in guests:
                self._poll(g)
            self.step(poll_s)
        chk.add("ambulance trips finish", all(a.gone or a.view["status"] == "arrived" for a in ambs), f"after {int(waited)} s of watching")
        chk.add("every ambulance trip gets a receipt", all(a.receipt for a in ambs) or all(a.gone for a in ambs),
                "receipt shows trip time and 'deleted' privacy note")

        # 6. who was warned
        route = [g for g in guests if g.group == "on-route"]
        off = [g for g in guests if g.group == "turns-off"]
        els = [g for g in guests if g.group == "elsewhere"]
        chk.add("vehicles on the ambulance's route were warned", all(g.warned for g in route),
                f"{sum(g.warned for g in route)}/{len(route)} warned: {[g.id for g in route if g.warned]}")
        chk.add("warned vehicles saw the 'has passed' all-clear or finished first",
                all(g.passed_text or g.gone or g.view["status"] == "arrived" for g in route + off if g.warned),
                f"'has passed' seen by {[g.id for g in guests if g.passed_text]}")
        # Cars that already arrived are skipped: the backend stops updating an arrived session, so its last alert stays frozen
        # until the session is deleted 8 s later (reported as a follow-up, not a bot problem).
        stuck = [g.id for g in guests if not g.gone and g.view["status"] == "driving"
                 and any(a["state"] in LIVE and a["kind"] in ("ev", "v2v") for a in g.view["alerts"])]
        chk.add("no live emergency alert is left on a driving car when the trip ends", not stuck,
                f"still live on {stuck}" if stuck else "all driving cars are back to all-clear")
        texts = [t for g in guests for t in g.texts]
        chk.add("warnings use fixed wording and never say hurry", texts and not any(h in t.lower() for t in texts for h in HURRY),
                f"{len(set(texts))} distinct texts")
        chk.add("route-change messages (informational: needs a blocked road to trigger)", True,
                f"{sum(g.route_msgs for g in guests)} seen by {[g.id for g in guests if g.route_msgs]}")
        chk.add("vehicles elsewhere (informational)", True, f"warned: {[g.id for g in els if g.warned]} not warned: {[g.id for g in els if not g.warned]}")

        # 7. receipts delete the sessions: leave, then the token must be gone
        for g in guests + ambs:
            if not g.gone:
                self.c.delete(f"{JOIN}/{g.token}")
        deleted = 0
        for g in guests + ambs:
            if self.c.get(f"{JOIN}/{g.token}").status_code == 404:
                deleted += 1
        chk.add("sessions are really deleted afterwards (404)", deleted == len(guests) + len(ambs), f"{deleted}/{len(guests) + len(ambs)} gone")
        return chk, guests

    # ---- loop mode: keeps a few cars on the big screen --------------------------------------------------------------
    def loop(self, vehicles=5, ambulances=1, gap_s=65.0):
        n = 0
        while True:
            n += 1
            self.say(f"\n== rehearsal round {n} ==")
            chk, _ = self.run_once(vehicles, ambulances)
            self.say(chk.table())
            self.say(f"next round in {int(gap_s)} s (the backend limits joins per minute); Ctrl+C to stop")
            self.step(gap_s)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--base", default="http://localhost:8000", help="backend address (default http://localhost:8000)")
    p.add_argument("--vehicles", type=int, default=5)
    p.add_argument("--ambulance", type=int, default=1, help="number of guest ambulances (1 to 3)")
    p.add_argument("--loop", action="store_true", help="repeat forever, slowly, for big-screen rehearsal")
    p.add_argument("--seed", type=int, default=None)
    a = p.parse_args(argv)
    try:
        with httpx.Client(base_url=a.base, timeout=30.0) as c:
            c.get("/api/health").raise_for_status()
            bots = Bots(c)
            if a.loop:
                try:
                    bots.loop(a.vehicles, a.ambulance)
                except KeyboardInterrupt:
                    print("\nstopped")
                return 0
            chk, _ = bots.run_once(a.vehicles, a.ambulance, seed=a.seed)
            print(chk.table())
            return 0 if chk.ok else 1
    except httpx.HTTPError as e:
        print(f"cannot reach the backend at {a.base}: {type(e).__name__}. Start it first (see docs/REHEARSAL.md).")
        return 2


if __name__ == "__main__":
    sys.exit(main())
