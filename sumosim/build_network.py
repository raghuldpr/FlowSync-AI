"""topology.yaml -> SUMO network.   python -m sumosim.build_network [--out sumosim/net]

Edge ids are the topology edge ids (an approach id == the id of its inbound edge). Junction coordinates come from the
dashboard `layout:` block (1 layout unit = 1 m, y flipped because SUMO's y axis points up).
"""
import argparse
import subprocess
from pathlib import Path
from xml.etree import ElementTree as ET

from app.config import Settings
from app.core.topology import Topology

from . import binary, sumo_home

LANES = 2
SPEED_MS = 11.1       # 40 km/h. topology speeds are table-cm/s, meaningless at road scale


def build(topology_path: str, out: Path) -> Path:
    topo = Topology.load(topology_path)
    out.mkdir(parents=True, exist_ok=True)
    nodes = ET.Element("nodes")
    for n in topo.nodes:
        x, y = topo.layout[n]
        ET.SubElement(nodes, "node", id=n, x=str(x), y=str(-y),
                      type="traffic_light" if n in topo.intersections else "dead_end")
    edges = ET.Element("edges")
    for e in topo.edges.values():
        ET.SubElement(edges, "edge", id=e.id, **{"from": e.src, "to": e.dst}, numLanes=str(LANES), speed=str(SPEED_MS))
    cons = ET.Element("connections")
    for a in topo.edges.values():
        for b in topo.edges.values():
            if a.dst == b.src and b.dst != a.src:          # no U-turns
                ET.SubElement(cons, "connection", **{"from": a.id, "to": b.id})
    paths = {}
    for name, root in (("nod", nodes), ("edg", edges), ("con", cons)):
        p = out / f"triangle.{name}.xml"
        ET.indent(root)
        ET.ElementTree(root).write(p, encoding="utf-8", xml_declaration=True)
        paths[name] = p
    net = out / "triangle.net.xml"
    subprocess.run([binary("netconvert"), "-n", str(paths["nod"]), "-e", str(paths["edg"]), "-x", str(paths["con"]),
                    "-o", str(net), "--no-turnarounds", "--tls.default-type", "static", "--no-warnings"], check=True)
    return net


def main():
    if not sumo_home():
        raise SystemExit("SUMO not found: pip install eclipse-sumo traci sumolib")
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(Path(__file__).parent / "net"))
    a = ap.parse_args()
    print("wrote", build(Settings().topology_path, Path(a.out)))


if __name__ == "__main__":
    main()
