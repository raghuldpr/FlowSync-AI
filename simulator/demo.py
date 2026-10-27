"""Scripted demo driven over HTTP against a live backend (operator API + signed camera posts).

  t= 15 s  congestion builds on L13 (direct W->S link)  -> congestion_spike incident + impact
  t= 30 s  operator declares EV-1 (West -> South); it drives along its CURRENT route
           (it is rerouted via I2 because of the congestion), warnings follow it, signals give priority
  t=110 s  a vehicle breaks down on L21 -> stalled_vehicle incident, edge closed for routing
  t=170 s  breakdown cleared
"""
import httpx


class Demo:
    def __init__(self, sim, base_url: str, password: str, pace: float = 2.0):
        self.sim, self.pace = sim, pace
        self.http = httpx.Client(base_url=base_url, timeout=3)
        r = self.http.post("/api/auth/login", json={"username": "operator", "password": password})
        r.raise_for_status()
        self.http.headers["Authorization"] = "Bearer " + r.json()["token"]
        self.edges = {e["id"]: e for e in self.http.get("/api/public/topology").json()["edges"]}
        self.topo_len = None
        self.declared = False

    def _edge_time(self, eid: str) -> float:
        e = self.sim.topo.edges[eid]
        return e.length / e.speed * self.pace

    def step(self, t: float):
        s = self.sim
        if 15 <= t < 85:
            s.queues["L13"] = 8      # re-pinned each second; the controller drains it by <1 car/s, so it stays >= 0.75
        if t >= 110 and t < 170:
            s.stalled.add("L21")
        elif t >= 170:
            s.stalled.discard("L21")
        if t >= 30 and not self.declared:
            self.http.post("/api/operator/ev", json={"vehicle_id": "EV-1"})
            self.declared = True
        if self.declared:
            self._drive()

    def _drive(self):
        sessions = [x for x in self.http.get("/api/operator/ev").json() if x["status"] == "active"]
        if not sessions:
            return
        ev = sessions[0]
        edge = ev["edge"] or ev["route"][0]
        prog = (ev["progress"] if ev["edge"] else 0.0) + 1.0 / self._edge_time(edge)
        if prog >= 1.0:
            i = ev["route"].index(edge) + 1 if edge in ev["route"] else len(ev["route"])
            if i >= len(ev["route"]):
                prog = 0.97
            else:
                edge, prog = ev["route"][i], 0.02
        self.sim.cam.request("POST", "/api/device/ev-position",
                             {"vehicle_id": ev["vehicle_id"], "edge": edge, "progress": min(prog, 1.0)})
