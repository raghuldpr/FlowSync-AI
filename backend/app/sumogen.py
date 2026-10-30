"""Generate a SUMO network from a FlowSync topology file (the tabletop triangle or the generated city grid).

   python -m app.sumogen --topology config/topology.yaml --out sumo/triangle
   python -m app.sumogen --topology config/topology.grid.yaml --out sumo/grid --build

Writes plain SUMO inputs (nodes, edges, routes, config) plus `flowsync_map.json`, the bridge's lookup table:
which inbound edges belong to which signal axis (A / B) at each intersection, and the ambulance routes.
Signal PHASES are deliberately not written here: SUMO only fixes the link order of a traffic light once netconvert has
built the network, so the TraCI bridge reads `getControlledLinks` at runtime and builds the red/yellow/green strings
from the axis lists in the map. Nothing is sent to a signal unless the backend's validator approved it.

Geometry comes from the dashboard `layout` (800 x 520 canvas units, y down); `--scale` converts layout units to metres.
Edge ids are the topology ids, so a SUMO edge, a FlowSync approach and a dashboard road all share one name.
"""
import argparse
import heapq
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from xml.etree import ElementTree as ET

from .config import ROOT
from .core.topology import Topology

NET = "flowsync.net.xml"


def _write(path: Path, root: ET.Element):
    ET.indent(root)
    path.write_text('<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n", encoding="utf-8")


def _coords(topo: Topology, scale: float) -> dict:
    missing = [n for n in topo.nodes if n not in topo.layout]
    if missing:
        raise ValueError(f"topology has no layout position for: {', '.join(missing)}")
    ymax = max(p[1] for p in topo.layout.values())
    return {n: (round(topo.layout[n][0] * scale, 2), round((ymax - topo.layout[n][1]) * scale, 2)) for n in topo.nodes}


def _edge_len(topo: Topology, xy: dict, eid: str) -> float:
    e = topo.edges[eid]
    (x0, y0), (x1, y1) = xy[e.src], xy[e.dst]
    return ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5


def shortest_path(topo: Topology, xy: dict, first: str, last: str, banned: frozenset = frozenset()):
    """Shortest edge sequence from edge `first` to edge `last`. No U-turns (an edge back to the node it came from)."""
    out = {}
    for e in topo.edges.values():
        out.setdefault(e.src, []).append(e.id)
    dist, prev, heap = {first: 0.0}, {}, [(0.0, first)]
    while heap:
        d, cur = heapq.heappop(heap)
        if cur == last:
            path = [cur]
            while path[-1] in prev:
                path.append(prev[path[-1]])
            return path[::-1]
        if d > dist.get(cur, 1e18):
            continue
        for nxt in out.get(topo.edges[cur].dst, []):
            if nxt in banned and nxt != last or topo.edges[nxt].dst == topo.edges[cur].src:
                continue
            nd = d + _edge_len(topo, xy, nxt)
            if nd < dist.get(nxt, 1e18):
                dist[nxt], prev[nxt] = nd, cur
                heapq.heappush(heap, (nd, nxt))
    return None


def build(topo: Topology, name: str, scale: float = 0.5, speed: float = 13.89, veh_per_hour: int = 300, ev_depart: int = 60) -> dict:
    """Return {filename: ET.Element} for the SUMO inputs, plus 'map' -> dict for flowsync_map.json."""
    xy = _coords(topo, scale)
    external = [n for n in topo.nodes if n not in topo.intersections]

    nod = ET.Element("nodes")
    for n in topo.nodes:
        attrs = {"id": n, "x": str(xy[n][0]), "y": str(xy[n][1])}
        attrs.update({"type": "traffic_light", "tl": n} if n in topo.intersections else {"type": "dead_end"})
        ET.SubElement(nod, "node", attrs)

    edg = ET.Element("edges")
    for e in topo.edges.values():
        ET.SubElement(edg, "edge", {"id": e.id, "from": e.src, "to": e.dst, "numLanes": "1", "speed": str(speed)})

    entrances = [e.id for e in topo.edges.values() if e.src in external]
    exits = [e.id for e in topo.edges.values() if e.dst in external]
    routes = {}
    for a in entrances:
        for b in exits:
            if topo.edges[b].dst == topo.edges[a].src:
                continue                                           # would just leave where it came in
            p = shortest_path(topo, xy, a, b)
            if p:
                routes[f"r_{a}_{b}"] = p

    ev_routes = {}
    ev = topo.ev
    first = next((e.id for e in topo.edges.values() if e.src == ev.default_origin), None)
    last = next((e.id for e in topo.edges.values() if e.dst == ev.default_destination), None)
    if first and last:
        main = shortest_path(topo, xy, first, last)
        if main:
            ev_routes["ev_default"] = main
            alt = shortest_path(topo, xy, first, last, banned=frozenset(main[1:-1]))
            if alt and alt != main:
                ev_routes["ev_alt"] = alt

    rou = ET.Element("routes")
    ET.SubElement(rou, "vType", {"id": "car", "vClass": "passenger", "length": "4.5", "accel": "2.6", "decel": "4.5",
                                 "maxSpeed": str(speed), "sigma": "0.5", "color": "0.3,0.55,0.85"})
    ET.SubElement(rou, "vType", {"id": "ev", "vClass": "emergency", "guiShape": "emergency", "length": "6", "accel": "3.5",
                                 "decel": "5.0", "maxSpeed": str(round(speed * 1.6, 2)), "speedFactor": "1.2", "color": "1,1,1"})
    for rid, edges in {**routes, **ev_routes}.items():
        ET.SubElement(rou, "route", {"id": rid, "edges": " ".join(edges)})
    for a in entrances:
        mine = [rid for rid, p in routes.items() if p[0] == a]
        if not mine:
            continue
        dist = ET.SubElement(rou, "routeDistribution", {"id": f"rd_{a}"})
        for rid in mine:
            ET.SubElement(dist, "route", {"refId": rid, "probability": "1"})
        ET.SubElement(rou, "flow", {"id": f"bg_{a}", "type": "car", "route": f"rd_{a}", "begin": "0", "end": "3600",
                                    "vehsPerHour": str(veh_per_hour), "departLane": "best", "departSpeed": "max"})
    base = ET.Element("routes")          # scripted ambulance, only for a no-backend baseline run (see docs/SUMO.md)
    if "ev_default" in ev_routes:
        ET.SubElement(base, "vehicle", {"id": "EV-1", "type": "ev", "route": "ev_default", "depart": str(ev_depart), "departSpeed": "max"})

    cfg = ET.Element("configuration")
    ET.SubElement(ET.SubElement(cfg, "input"), "net-file", {"value": NET})
    ET.SubElement(cfg.find("input"), "route-files", {"value": "routes.rou.xml"})
    t = ET.SubElement(cfg, "time")
    for k, v in (("begin", "0"), ("end", "3600"), ("step-length", "0.1")):
        ET.SubElement(t, k, {"value": v})
    ET.SubElement(ET.SubElement(cfg, "processing"), "time-to-teleport", {"value": "-1"})   # keep stuck queues visible
    ET.SubElement(ET.SubElement(cfg, "report"), "no-step-log", {"value": "true"})

    axes = {i: {"A": [], "B": []} for i in topo.intersections}
    for a in topo.approaches.values():
        axes[a.intersection][a.movement].append(a.id)
    mapping = {
        "name": name, "scale_m_per_layout_unit": scale, "speed_mps": speed, "net_file": NET,
        "intersections": axes,
        "edges": {e.id: {"from": e.src, "to": e.dst, "length_m": round(_edge_len(topo, xy, e.id), 1),
                         "approach": e.id in topo.approaches, "movement": topo.approaches[e.id].movement if e.id in topo.approaches else None}
                  for e in topo.edges.values()},
        "routes": routes, "ev_routes": ev_routes, "ev_vehicle_id": "EV-1",
        "timing_ms": {"min_green": topo.timing.min_green_ms, "yellow": topo.timing.yellow_ms, "all_red": topo.timing.all_red_ms,
                      "max_green": topo.timing.max_green_ms},
    }
    return {"flowsync.nod.xml": nod, "flowsync.edg.xml": edg, "routes.rou.xml": rou, "ev_baseline.rou.xml": base,
            "flowsync.sumocfg": cfg, "map": mapping}


def find_netconvert() -> str | None:
    home = os.environ.get("SUMO_HOME")
    if home:
        for exe in ("netconvert.exe", "netconvert"):
            p = Path(home) / "bin" / exe
            if p.exists():
                return str(p)
    return shutil.which("netconvert")


def write(topo: Topology, out: Path, name: str, **kw) -> list:
    out.mkdir(parents=True, exist_ok=True)
    parts = build(topo, name, **kw)
    files = []
    for fname, root in parts.items():
        if fname == "map":
            (out / "flowsync_map.json").write_text(json.dumps(root, indent=2) + "\n", encoding="utf-8")
            files.append("flowsync_map.json")
        else:
            _write(out / fname, root)
            files.append(fname)
    return files


def run_netconvert(out: Path) -> int:
    exe = find_netconvert()
    if not exe:
        print("netconvert not found. Install SUMO and set SUMO_HOME (see docs/SUMO.md), then re-run with --build.", file=sys.stderr)
        return 2
    cmd = [exe, "--node-files", "flowsync.nod.xml", "--edge-files", "flowsync.edg.xml", "--output-file", NET, "--no-turnarounds", "true"]
    return subprocess.run(cmd, cwd=out).returncode


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--topology", default=str(ROOT / "config" / "topology.yaml"))
    p.add_argument("--out", default=str(ROOT / "sumo" / "triangle"))
    p.add_argument("--scale", type=float, default=0.5, help="metres per dashboard layout unit")
    p.add_argument("--speed", type=float, default=13.89, help="road speed limit, m/s (13.89 = 50 km/h)")
    p.add_argument("--veh-per-hour", type=int, default=300, help="background cars per entrance")
    p.add_argument("--ev-depart", type=int, default=60, help="second at which the ambulance EV-1 departs")
    p.add_argument("--build", action="store_true", help="also run netconvert to produce flowsync.net.xml")
    a = p.parse_args()
    out = Path(a.out)
    files = write(Topology.load(a.topology), out, out.name, scale=a.scale, speed=a.speed, veh_per_hour=a.veh_per_hour, ev_depart=a.ev_depart)
    print(f"wrote {len(files)} files to {out}: {', '.join(files)}")
    if a.build:
        sys.exit(run_netconvert(out))


if __name__ == "__main__":
    main()
