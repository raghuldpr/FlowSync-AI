"""Generate a city-scale grid topology (default 3 x 4 = 12 intersections) in the same format as config/topology.yaml.

   python -m app.gridgen --rows 3 --cols 4 --out config/topology.grid.yaml --devices-out config/devices.grid.yaml

Run the backend on it with  FLOWSYNC_TOPOLOGY_PATH / FLOWSYNC_DEVICES_PATH  pointing at those files; the controller,
state machine, routing, incidents, warnings and dashboards are the SAME code as for the three-intersection tabletop.
Everything on it is simulated.

Naming:  intersections I1..In (row-major) · internal road I_a -> I_b is edge "L<a>_<b>" (also an approach at I_b)
         entrances "in<side><k>" from node ext_<side><k>, exits "out<side><k>"   (side = W, E, N, S)
Movements: A = east-west, B = north-south.
"""
import argparse
import sys

import yaml

from .config import ROOT, load_yaml

DX, DY = 160, 130


def build(rows: int = 3, cols: int = 4, base: dict = None) -> dict:
    base = base or load_yaml(str(ROOT / "config" / "topology.yaml"))
    n = lambda r, c: r * cols + c + 1
    x0 = (800 - (cols - 1) * DX) / 2
    y0 = (520 - (rows - 1) * DY) / 2
    layout, nodes, labels, edges, approaches = {}, [], {}, [], {}
    inters = [f"I{n(r, c)}" for r in range(rows) for c in range(cols)]
    for r in range(rows):
        for c in range(cols):
            nodes.append(f"I{n(r, c)}")
            layout[f"I{n(r, c)}"] = [round(x0 + c * DX), round(y0 + r * DY)]
    ext = {}                                                         # node -> (side, k)
    for r in range(rows):
        ext[f"ext_W{r + 1}"] = ("W", r, layout[f"I{n(r, 0)}"][0] - 95, layout[f"I{n(r, 0)}"][1], f"I{n(r, 0)}", "A")
        ext[f"ext_E{r + 1}"] = ("E", r, layout[f"I{n(r, cols - 1)}"][0] + 95, layout[f"I{n(r, cols - 1)}"][1], f"I{n(r, cols - 1)}", "A")
    for c in range(cols):
        ext[f"ext_N{c + 1}"] = ("N", c, layout[f"I{n(0, c)}"][0], layout[f"I{n(0, c)}"][1] - 85, f"I{n(0, c)}", "B")
        ext[f"ext_S{c + 1}"] = ("S", c, layout[f"I{n(rows - 1, c)}"][0], layout[f"I{n(rows - 1, c)}"][1] + 85, f"I{n(rows - 1, c)}", "B")
    for name, (side, k, x, y, _, _) in ext.items():
        nodes.append(name)
        layout[name] = [round(x), round(y)]
        labels[name] = f"{ {'W': 'West', 'E': 'East', 'N': 'North', 'S': 'South'}[side] } {k + 1}"
    for i in inters:
        labels[i] = i

    def add_edge(eid, src, dst, length):
        edges.append({"id": eid, "from": src, "to": dst, "length": length, "speed": 10})

    # internal roads, both directions
    for r in range(rows):
        for c in range(cols):
            if c + 1 < cols:
                a, b = n(r, c), n(r, c + 1)
                add_edge(f"L{a}_{b}", f"I{a}", f"I{b}", 60); add_edge(f"L{b}_{a}", f"I{b}", f"I{a}", 60)
            if r + 1 < rows:
                a, b = n(r, c), n(r + 1, c)
                add_edge(f"L{a}_{b}", f"I{a}", f"I{b}", 60); add_edge(f"L{b}_{a}", f"I{b}", f"I{a}", 60)
    for name, (side, k, _, _, inter, _) in ext.items():              # entrances and exits
        add_edge(f"in{side}{k + 1}", name, inter, 40)
        add_edge(f"out{side}{k + 1}", inter, name, 40)

    pos = {nid: layout[nid] for nid in layout}
    by = {e["id"]: e for e in edges}

    def leaving(inter):                                              # edges leaving an intersection, with direction vectors
        out = []
        for e in edges:
            if e["from"] == inter:
                dx, dy = pos[e["to"]][0] - pos[inter][0], pos[e["to"]][1] - pos[inter][1]
                out.append((e["id"], (dx > 0) - (dx < 0), (dy > 0) - (dy < 0)))
        return out

    for e in edges:                                                  # every edge that ENDS at an intersection is an approach
        if e["to"] not in inters:
            continue
        dx, dy = pos[e["to"]][0] - pos[e["from"]][0], pos[e["to"]][1] - pos[e["from"]][1]
        sx, sy = (dx > 0) - (dx < 0), (dy > 0) - (dy < 0)
        movement = "A" if sx != 0 else "B"
        down = []
        for lid, lx, ly in leaving(e["to"]):
            internal = by[lid]["to"] in inters
            straight = (lx, ly) == (sx, sy)
            turn = (lx * sx + ly * sy) == 0                          # perpendicular
            if internal and (straight or turn):
                down.append(lid)
        approaches[e["id"]] = {"intersection": e["to"], "movement": movement, "downstream": down}

    raw = {k: base[k] for k in ("timing", "control", "routing", "ev", "incidents")}
    raw["ev"] = {**base["ev"], "default_origin": "ext_W1", "default_destination": f"ext_E{rows}"}
    raw["node_labels"] = labels
    raw["layout"] = layout
    raw["intersections"] = inters
    raw["approaches"] = approaches
    raw["nodes"] = nodes
    raw["edges"] = edges
    raw["vision"] = base.get("vision", {})
    return raw


def devices_for(raw: dict, base_devices: dict = None) -> dict:
    d = base_devices or load_yaml(str(ROOT / "config" / "devices.yaml"))
    out = {"devices": {k: dict(v) for k, v in d["devices"].items()}}
    out["devices"]["intersection-1"]["intersections"] = list(raw["intersections"])
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--rows", type=int, default=3)
    p.add_argument("--cols", type=int, default=4)
    p.add_argument("--out", default=str(ROOT / "config" / "topology.grid.yaml"))
    p.add_argument("--devices-out", default=str(ROOT / "config" / "devices.grid.yaml"))
    a = p.parse_args(argv)
    if not (2 <= a.rows <= 5 and 2 <= a.cols <= 6):
        print("rows 2-5 and cols 2-6 fit the dashboard map", file=sys.stderr)
        return 2
    raw = build(a.rows, a.cols)
    head = "# GENERATED by `python -m app.gridgen` - a simulated city grid. Do not edit; regenerate.\n"
    with open(a.out, "w", encoding="utf-8", newline="\n") as f:
        f.write(head)
        yaml.safe_dump(raw, f, sort_keys=False, width=140)
    with open(a.devices_out, "w", encoding="utf-8", newline="\n") as f:
        f.write(head + "# Same dev keys as devices.yaml; intersection-1 controls every grid intersection.\n")
        yaml.safe_dump(devices_for(raw), f, sort_keys=False)
    print(f"wrote {a.out} ({len(raw['intersections'])} intersections, {len(raw['edges'])} roads) and {a.devices_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
