"""Table geometry in LAYOUT units (the frame of config/topology.yaml `layout`). Pure Python, no OpenCV.

Lane centre-lines use the same offset convention as the dashboard drawing: each directed road is shifted to the
LEFT of its direction of travel (screen coordinates, y down) by `lane_offset`, so the two directions of a road
are separate, parallel lanes. Draw the physical table the same way.
"""
import math


def apply_h(H, x: float, y: float):
    """H: 3x3 nested sequence mapping source -> target."""
    w = H[2][0] * x + H[2][1] * y + H[2][2]
    return ((H[0][0] * x + H[0][1] * y + H[0][2]) / w, (H[1][0] * x + H[1][1] * y + H[1][2]) / w)


def node_radius(topo, cfg, node: str) -> float:
    return cfg.node_radius if node in topo.intersections else cfg.ext_radius


def lane_line(topo, cfg, edge_id: str):
    """(x1, y1, x2, y2): lane centre-line from the source node's boundary to the destination node's boundary."""
    e = topo.edges[edge_id]
    (ax, ay), (bx, by) = topo.layout[e.src], topo.layout[e.dst]
    L = math.hypot(bx - ax, by - ay)
    ux, uy = (bx - ax) / L, (by - ay) / L
    ox, oy = -uy * cfg.lane_offset, ux * cfg.lane_offset
    s, t = node_radius(topo, cfg, e.src), node_radius(topo, cfg, e.dst)
    return (ax + ux * s + ox, ay + uy * s + oy, bx - ux * t + ox, by - uy * t + oy)


def lane_dir(line):
    x1, y1, x2, y2 = line
    L = math.hypot(x2 - x1, y2 - y1)
    return (x2 - x1) / L, (y2 - y1) / L, L


def _roi_span(topo, cfg, edge_id: str):
    """(start, end) distances along the lane covered by the counting region."""
    L = lane_dir(lane_line(topo, cfg, edge_id))[2]
    start = L * cfg.roi_start
    if topo.edges[edge_id].src in topo.intersections:
        start = max(start, cfg.roi_exit_clearance)       # just after a junction lanes are too close together to separate
    return start, max(start, L * cfg.roi_end)


def roi_polygon(topo, cfg, edge_id: str):
    """Four corners of the counting region on an approach lane."""
    line = lane_line(topo, cfg, edge_id)
    ux, uy, L = lane_dir(line)
    s, e = _roi_span(topo, cfg, edge_id)
    x1, y1 = line[0] + ux * s, line[1] + uy * s
    x2, y2 = line[0] + ux * e, line[1] + uy * e
    nx, ny = -uy * cfg.roi_half_width, ux * cfg.roi_half_width
    return [(x1 + nx, y1 + ny), (x2 + nx, y2 + ny), (x2 - nx, y2 - ny), (x1 - nx, y1 - ny)]


def roi_capacity(topo, cfg, edge_id: str) -> int:
    """How many vehicles physically fit in the counting region (>= 1)."""
    s, e = _roi_span(topo, cfg, edge_id)
    return max(1, int((e - s) // (cfg.car_length + cfg.car_gap)))


def _point_segment(px, py, line):
    x1, y1, x2, y2 = line
    dx, dy = x2 - x1, y2 - y1
    L2 = dx * dx + dy * dy
    t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / L2))
    return math.hypot(px - (x1 + t * dx), py - (y1 + t * dy)), t


def project_to_edge(topo, cfg, x: float, y: float, heading=None, prev=None):
    """Map a layout-space point (the EV marker centre) to (edge_id, progress 0..1), or None.

    heading: (hx, hy) direction the vehicle faces (layout frame) - separates the two lanes of a road.
    prev: previously reported edge. Inside an intersection box the vehicle is reported at the END of the edge
    it was on (progress 1.0), which is what the backend needs to detect that the approach was passed.
    """
    for n in topo.nodes:
        if n in topo.layout and math.hypot(x - topo.layout[n][0], y - topo.layout[n][1]) <= node_radius(topo, cfg, n):
            if prev is not None and topo.edges[prev].dst == n:
                return prev, 1.0
            return None
    best = None
    for eid in topo.edges:
        line = lane_line(topo, cfg, eid)
        d, t = _point_segment(x, y, line)
        if d > cfg.ev_snap_distance:
            continue
        score = d
        if heading is not None:
            ux, uy, _ = lane_dir(line)
            hl = math.hypot(*heading)
            cos = (heading[0] * ux + heading[1] * uy) / hl if hl else 1.0
            if cos < 0.2:
                continue                              # facing away from this lane's direction of travel
            score += 12.0 * (1.0 - cos)
        if best is None or score < best[0]:
            best = (score, eid, t)
    return None if best is None else (best[1], best[2])
