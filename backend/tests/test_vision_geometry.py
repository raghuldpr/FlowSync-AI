import math

import pytest

from app.config import Settings
from app.core.topology import Topology
from app.vision.config import VisionConfig
from app.vision.geometry import apply_h, lane_dir, lane_line, project_to_edge, roi_capacity, roi_polygon


@pytest.fixture(scope="module")
def topo():
    return Topology.load(Settings().topology_path)


@pytest.fixture(scope="module")
def cfg(topo):
    return VisionConfig.from_topology(topo)


def lane_point(topo, cfg, eid, p):
    x1, y1, x2, y2 = lane_line(topo, cfg, eid)
    return x1 + (x2 - x1) * p, y1 + (y2 - y1) * p


def test_config_loads_and_rejects_typos(topo):
    cfg = VisionConfig.from_topology(topo)
    assert set(cfg.corner_markers) == {10, 11, 12, 13} and cfg.ev_markers == {20: "EV-1"}
    bad = Topology.load(Settings().topology_path)
    bad.vision = {**bad.vision, "diff_treshold": 5}
    with pytest.raises(ValueError):
        VisionConfig.from_topology(bad)


def test_markers_and_roi_stay_on_the_canvas_and_clear_of_each_other(topo, cfg):
    W, H = cfg.canvas
    half = cfg.corner_marker_size / 2
    for (cx, cy) in cfg.corner_markers.values():
        assert half <= cx <= W - half and half <= cy <= H - half
        for eid in topo.approaches:                                # corner markers must not sit inside any counting ROI
            xs = [p[0] for p in roi_polygon(topo, cfg, eid)]
            ys = [p[1] for p in roi_polygon(topo, cfg, eid)]
            assert not (min(xs) - half < cx < max(xs) + half and min(ys) - half < cy < max(ys) + half), eid
    for eid in topo.approaches:
        for x, y in roi_polygon(topo, cfg, eid):
            assert 0 <= x <= W and 0 <= y <= H


def test_every_approach_has_a_roi_that_fits_at_least_two_cars(topo, cfg):
    caps = {a: roi_capacity(topo, cfg, a) for a in topo.approaches}
    assert all(c >= 2 for c in caps.values()), caps
    assert caps["L12"] > caps["S3"], "ring links are longer than the external stubs"


def _axes(poly):
    for i in range(len(poly)):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % len(poly)]
        yield (-(y2 - y1), x2 - x1)


def polys_overlap(a, b) -> bool:
    """Separating-axis test for convex polygons (touching edges do not count)."""
    for ax, ay in list(_axes(a)) + list(_axes(b)):
        pa = [x * ax + y * ay for x, y in a]
        pb = [x * ax + y * ay for x, y in b]
        if max(pa) <= min(pb) + 1e-9 or max(pb) <= min(pa) + 1e-9:
            return False
    return True


def test_no_two_approach_rois_overlap(topo, cfg):
    """Overlapping ROIs would count one car twice (found with the synthetic camera: L21's car showed up on L12)."""
    ids = list(topo.approaches)
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            assert not polys_overlap(roi_polygon(topo, cfg, a), roi_polygon(topo, cfg, b)), (a, b)


def test_a_car_in_one_lane_never_reaches_the_opposite_lanes_roi(topo, cfg):
    # lane centre-lines of a road are 2*lane_offset apart; a car's nearest side must stay outside the other ROI
    assert 2 * cfg.lane_offset - cfg.car_width / 2 > cfg.roi_half_width


def test_opposite_lanes_of_a_road_are_separate_and_parallel(topo, cfg):
    a, b = lane_line(topo, cfg, "L12"), lane_line(topo, cfg, "L21")
    da, db = lane_dir(a), lane_dir(b)
    assert da[0] == pytest.approx(-db[0]) and da[1] == pytest.approx(-db[1])
    gap = abs((b[0] - a[0]) * -da[1] + (b[1] - a[1]) * da[0])
    assert gap == pytest.approx(2 * cfg.lane_offset)


@pytest.mark.parametrize("p", [0.05, 0.25, 0.5, 0.75, 0.95])
def test_every_edge_is_recovered_from_a_point_on_its_lane_with_heading(topo, cfg, p):
    for eid in topo.edges:
        x, y = lane_point(topo, cfg, eid, p)
        ux, uy, _ = lane_dir(lane_line(topo, cfg, eid))
        got = project_to_edge(topo, cfg, x, y, heading=(ux, uy))
        assert got is not None and got[0] == eid and got[1] == pytest.approx(p, abs=0.02), (eid, p, got)


def test_heading_separates_the_two_lanes_of_one_road(topo, cfg):
    # a point on the road AXIS is equidistant (lane_offset) from both lanes: the facing direction decides
    (ax, ay), (bx, by) = topo.layout["I1"], topo.layout["I2"]
    x, y = (ax + bx) / 2, (ay + by) / 2
    ux, uy, _ = lane_dir(lane_line(topo, cfg, "L12"))
    assert project_to_edge(topo, cfg, x, y, heading=(ux, uy))[0] == "L12"
    assert project_to_edge(topo, cfg, x, y, heading=(-ux, -uy))[0] == "L21"


def test_inside_an_intersection_the_vehicle_stays_at_the_end_of_its_previous_edge(topo, cfg):
    cx, cy = topo.layout["I1"]
    assert project_to_edge(topo, cfg, cx + 5, cy + 5, prev="W1") == ("W1", 1.0)
    assert project_to_edge(topo, cfg, cx + 5, cy + 5, prev="L12") is None      # L12 does not end at I1
    assert project_to_edge(topo, cfg, cx + 5, cy + 5) is None


def test_far_from_every_road_maps_to_nothing(topo, cfg):
    assert project_to_edge(topo, cfg, 400, 40) is None


def test_apply_h_identity_and_translation():
    assert apply_h([[1, 0, 0], [0, 1, 0], [0, 0, 1]], 3, 4) == (3, 4)
    assert apply_h([[1, 0, 10], [0, 1, -5], [0, 0, 1]], 3, 4) == (13, -1)
    x, y = apply_h([[2, 0, 0], [0, 2, 0], [0, 0, 2]], 3, 4)
    assert math.isclose(x, 3) and math.isclose(y, 4)
