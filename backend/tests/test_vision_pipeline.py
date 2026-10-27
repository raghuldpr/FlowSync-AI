"""Vision pipeline vs synthetic ground truth. Needs OpenCV (skipped otherwise)."""
import math
import random

import pytest

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")

from app.config import Settings
from app.core.topology import Topology
from app.vision import capacity as capacity_tool, markers as markers_tool
from app.vision.geometry import apply_h, lane_dir, lane_line, roi_capacity
from app.vision.pipeline import VisionPipeline
from app.vision.poster import Poster
from app.vision.synthetic import SyntheticTable


@pytest.fixture(scope="module")
def topo():
    return Topology.load(Settings().topology_path)


@pytest.fixture(scope="module")
def table(topo):
    return SyntheticTable(topo, seed=1)


@pytest.fixture
def pipe(topo, table):
    p = VisionPipeline(topo)
    empty, _ = table.render({})
    assert p.capture_reference([empty] * 3)
    return p


def counts_of(obs):
    return {a: n for a, (n, _) in obs.counts.items()}


def caps(topo, pipe):
    return {a: roi_capacity(topo, pipe.cfg, a) for a in topo.approaches}


def test_homography_maps_the_image_back_to_layout_units(topo, table, pipe):
    Hcam = table.camera_matrix()
    for n, (x, y) in topo.layout.items():
        ix, iy = apply_h(Hcam, x, y)
        assert 0 <= ix <= 1280 and 0 <= iy <= 720
        bx, by = apply_h(pipe.H, ix, iy)
        assert math.hypot(bx - x, by - y) < 1.5, (n, bx, by)
    assert pipe.calib_error < 1.0


def test_empty_table_counts_zero_everywhere(topo, table, pipe):
    img, _ = table.render({})
    obs = pipe.process(img, 0.0)
    assert obs.fresh_calibration and set(counts_of(obs).values()) == {0}


def test_each_approach_counts_exactly_alone(topo, table, pipe):
    c = caps(topo, pipe)
    for a in topo.approaches:
        for n in range(0, c[a] + 1):
            img, placed = table.render({a: n})
            got = counts_of(pipe.process(img, float(n)))
            assert got[a] == placed[a] == n, (a, n, got)
            assert sum(got.values()) == n, f"phantom vehicles when only {a} is occupied: {got}"


def test_all_approaches_at_once_with_noise_and_exposure_drift(topo, table, pipe):
    rng = random.Random(4)
    c = caps(topo, pipe)
    for k, (bright, noise) in enumerate([(1.0, 0), (0.85, 4), (1.15, 4), (0.92, 6)]):
        q = {a: rng.randint(0, c[a]) for a in topo.approaches}
        img, placed = table.render(q, brightness=bright, noise=noise)
        assert counts_of(pipe.process(img, float(k))) == placed, (bright, noise)


def test_touching_queue_is_split_by_area(topo, table, pipe):
    img, placed = table.render({"L12": 5, "L13": 4}, gap=0)
    got = counts_of(pipe.process(img, 0.0))
    assert abs(got["L12"] - placed["L12"]) <= 1 and abs(got["L13"] - placed["L13"]) <= 1, (got, placed)


def test_camera_moved_but_corners_visible_still_counts_correctly(topo, table, pipe):
    other = [(150, 40), (1160, 120), (1120, 690), (110, 600)]
    img, placed = table.render({"L12": 3, "W1": 2, "L23": 2}, quad=other)
    obs = pipe.process(img, 0.0)
    assert obs.fresh_calibration and counts_of(obs) == {**{a: 0 for a in topo.approaches}, **placed}


def test_hidden_corner_keeps_last_calibration_and_reports_its_age(topo, table, pipe):
    img, placed = table.render({"L12": 2})
    assert pipe.process(img, 10.0).fresh_calibration
    img, _ = table.render({"L12": 2}, hide_markers=(11,))
    obs = pipe.process(img, 14.5)
    assert obs.calibrated and not obs.fresh_calibration and obs.calib_age_s == pytest.approx(4.5)
    assert counts_of(obs)["L12"] == 2


def test_never_calibrated_reports_no_counts(topo, table):
    p = VisionPipeline(topo)
    img, _ = table.render({"L12": 2}, hide_markers=(10,))
    obs = p.process(img, 0.0)
    assert not obs.calibrated and obs.counts == {} and obs.reason == "no_calibration"


def test_without_a_reference_the_ev_is_still_tracked_but_counts_are_withheld(topo, table):
    p = VisionPipeline(topo)
    p.capture_reference([table.render({})[0]])
    p.ref = None
    img, _ = table.render({"L12": 2}, ev={"edge": "L12", "progress": 0.5})
    obs = p.process(img, 0.0)
    assert obs.counts == {} and obs.reason == "no_reference" and obs.ev and obs.ev[0].edge == "L12"


@pytest.mark.parametrize("edge", ["W1", "E2", "S3", "L12", "L21", "L13", "L31", "L23", "L32", "O1", "O2", "O3"])
@pytest.mark.parametrize("p", [0.2, 0.5, 0.8])
def test_ev_marker_maps_to_the_right_road_progress_and_heading(topo, table, pipe, edge, p):
    img, _ = table.render({}, ev={"edge": edge, "progress": p})
    obs = pipe.process(img, 0.0)
    assert len(obs.ev) == 1, f"marker not found on {edge}@{p}"
    e = obs.ev[0]
    assert (e.vehicle_id, e.edge) == ("EV-1", edge)
    length = lane_dir(lane_line(topo, pipe.cfg, edge))[2]
    assert abs(e.progress - p) * length < 8.0, (edge, p, e.progress)
    ux, uy, _ = lane_dir(lane_line(topo, pipe.cfg, edge))
    assert e.heading[0] * ux + e.heading[1] * uy > 0.97


def test_ev_in_an_intersection_box_stays_at_the_end_of_the_previous_edge(topo, table, pipe):
    pipe.process(table.render({}, ev={"edge": "W1", "progress": 0.9})[0], 0.0)
    obs = pipe.process(table.render({}, ev={"edge": "W1", "progress": 1.3})[0], 0.5)       # now inside I1's box
    assert [(e.edge, e.progress) for e in obs.ev] == [("W1", 1.0)]


def test_ev_marker_hidden_gives_no_reading_and_noise_does_not_break_detection(topo, table, pipe):
    img, _ = table.render({}, ev={"edge": "L12", "progress": 0.5}, hide_markers=(20,))
    assert pipe.process(img, 0.0).ev == []
    img, _ = table.render({}, ev={"edge": "L12", "progress": 0.5}, noise=6, brightness=0.9)
    assert [e.edge for e in pipe.process(img, 1.0).ev] == ["L12"]


def test_ev_in_the_queue_counts_as_a_vehicle_but_does_not_confuse_the_corner_calibration(topo, table, pipe):
    img, placed = table.render({"L12": 2}, ev={"edge": "L21", "progress": 0.5})
    obs = pipe.process(img, 0.0)
    assert counts_of(obs)["L12"] == 2 and counts_of(obs)["L21"] == 1 and obs.fresh_calibration


def test_stationary_clock_runs_while_nothing_moves_and_resets_when_it_does(topo, table, pipe):
    img, _ = table.render({"L12": 2})
    seen = [pipe.process(img, float(t)).counts["L12"][1] for t in (0, 2, 4, 6)]
    assert seen == [0.0, 2.0, 4.0, 6.0]
    moved, _ = table.render({"L12": 2}, car_shift={"L12": (12, 0)})
    assert pipe.process(moved, 7.0).counts["L12"] == (2, 0.0)
    assert pipe.process(moved, 9.0).counts["L12"][1] == 2.0
    empty, _ = table.render({})
    assert pipe.process(empty, 10.0).counts["L12"] == (0, 0.0)


def test_annotated_debug_image_is_produced(topo, table, pipe):
    img, _ = table.render({"L12": 2}, ev={"edge": "L13", "progress": 0.4})
    obs = pipe.process(img, 0.0)
    dbg = pipe.annotate(obs)
    assert dbg.shape == (520, 800, 3) and dbg.dtype == np.uint8


# ---- posting ------------------------------------------------------------------------------------
class FakeClient:
    def __init__(self, status=200):
        self.posts, self.status = [], status

    def request(self, method, path, obj=None):
        self.posts.append((path, obj))

        class R:
            status_code = self.status
        return R()


def test_poster_posts_traffic_at_its_rate_and_only_with_a_recent_calibration(topo, table, pipe):
    fake = FakeClient()
    poster = Poster(fake, "overhead_camera", post_hz=2.0, log=lambda *_: None)
    img, _ = table.render({"L12": 2})
    obs = pipe.process(img, 100.0)
    poster.handle(obs, 100.0)
    poster.handle(obs, 100.2)                    # inside the 0.5 s period
    poster.handle(obs, 100.6)
    traffic = [b for p, b in fake.posts if p.endswith("/traffic")]
    assert len(traffic) == 2 and traffic[0]["source"] == "overhead_camera"
    assert {r["approach"] for r in traffic[0]["readings"]} == set(topo.approaches)
    assert next(r for r in traffic[0]["readings"] if r["approach"] == "L12")["count"] == 2
    hidden, _ = table.render({"L12": 2}, hide_markers=(12,))
    stale = pipe.process(hidden, 110.0)           # corners hidden for 10 s: counts must not be reported
    poster.handle(stale, 110.0)
    assert len([1 for p, _ in fake.posts if p.endswith("/traffic")]) == 2


def test_poster_ev_updates_are_deduplicated_but_kept_alive(topo, table, pipe):
    fake = FakeClient()
    poster = Poster(fake, "overhead_camera", post_hz=2.0, log=lambda *_: None)

    def at(progress, t):
        obs = pipe.process(table.render({}, ev={"edge": "L12", "progress": progress})[0], t)
        poster.handle(obs, t)
    at(0.4, 0.0); at(0.4, 0.3); at(0.4, 0.6)
    ev = [b for p, b in fake.posts if p.endswith("/ev-position")]
    assert len(ev) == 1 and ev[0]["edge"] == "L12" and ev[0]["vehicle_id"] == "EV-1"
    at(0.4, 1.2)                                  # keep-alive (backend would call it stale after 4 s)
    at(0.6, 1.3)                                  # moved
    assert len([1 for p, _ in fake.posts if p.endswith("/ev-position")]) == 3


def test_poster_survives_backend_errors(topo, table, pipe):
    fake = FakeClient(status=503)
    poster = Poster(fake, "overhead_camera", post_hz=2.0, log=lambda *_: None)
    poster.handle(pipe.process(table.render({"L12": 1}, ev={"edge": "L12", "progress": 0.5})[0], 0.0), 0.0)
    assert poster.errors == 2 and poster.sent_traffic == 0


# ---- tools ----------------------------------------------------------------------------------------
def test_marker_printout_tool(tmp_path):
    assert markers_tool.main(["--out", str(tmp_path), "--mm-per-unit", "1.0"]) == 0
    assert {p.name for p in tmp_path.glob("marker_*.png")} == {f"marker_{i}.png" for i in (10, 11, 12, 13, 20)}
    assert "black square 40.0 mm" in (tmp_path / "sizes.txt").read_text()
    img = cv2.imread(str(tmp_path / "marker_20.png"), cv2.IMREAD_GRAYSCALE)
    assert img is not None and img.shape[0] > 200
    d = cv2.aruco.ArucoDetector(cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50), cv2.aruco.DetectorParameters())
    _, ids, _ = d.detectMarkers(img[:-60])
    assert ids is not None and int(np.asarray(ids).flatten()[0]) == 20, "the printable PNG must decode as the id it is labelled with"


def test_capacity_tool_writes_a_loadable_physical_topology(tmp_path):
    out = tmp_path / "physical.yaml"
    assert capacity_tool.main(["--write", str(out)]) == 0
    t = Topology.load(str(out))
    assert t.approaches["S3"].capacity == 2 and t.approaches["L12"].capacity == 6 and t.vision["car_length"] == 28
