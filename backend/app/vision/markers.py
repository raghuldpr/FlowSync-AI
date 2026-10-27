"""Print-ready ArUco markers:  python -m app.vision.markers --out print_markers --mm-per-unit 1.0

One PNG per marker (corner markers + the EV marker) with a white quiet zone and the id/size under it, plus
sizes.txt. Print at 100% scale, no 'fit to page', and measure the printed black square against sizes.txt.
"""
import argparse
from pathlib import Path

import cv2

from ..config import Settings
from ..core.topology import Topology
from .config import VisionConfig


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--out", default="print_markers")
    p.add_argument("--mm-per-unit", type=float, default=1.0, help="physical millimetres per layout unit of your table")
    p.add_argument("--dpi", type=int, default=300)
    a = p.parse_args(argv)
    topo = Topology.load(Settings().topology_path)
    cfg = VisionConfig.from_topology(topo)
    dic = cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, cfg.aruco_dictionary))
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    lines = []
    todo = [(mid, "corner", cfg.corner_marker_size) for mid in cfg.corner_markers] + \
           [(mid, f"EV {vid}", cfg.ev_marker_size) for mid, vid in cfg.ev_markers.items()]
    for mid, kind, units in todo:
        mm = units * a.mm_per_unit
        px = int(round(mm / 25.4 * a.dpi))
        m = cv2.aruco.generateImageMarker(dic, mid, px)
        q = max(2, px // 5)
        m = cv2.copyMakeBorder(m, q, q, q, q + 0, cv2.BORDER_CONSTANT, value=255)
        m = cv2.copyMakeBorder(m, 0, 60, 0, 0, cv2.BORDER_CONSTANT, value=255)
        cv2.putText(m, f"id {mid} {kind}  black square {mm:.1f} mm", (10, m.shape[0] - 20), cv2.FONT_HERSHEY_SIMPLEX, 0.9, 0, 2, cv2.LINE_AA)
        cv2.imwrite(str(out / f"marker_{mid}.png"), m)
        lines.append(f"marker {mid:>3} ({kind}): black square {mm:.1f} mm; centre at layout {cfg.corner_markers.get(mid, 'on the car')}")
    (out / "sizes.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"wrote {len(todo)} PNGs to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
