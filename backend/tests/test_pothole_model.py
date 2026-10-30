"""Pretrained pothole model: output decoding, labelled fallback, and that detections stay UNREVIEWED reports."""
import pytest

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")

from app.vision import pothole, pothole_model  # noqa: E402
from test_hazards import jpeg, road, upload  # noqa: E402


def raw_output(boxes):
    """A fake (1, 9, 8400) YOLOv8 output: boxes = [(cx, cy, w, h, class, score)] in 640x640 letterbox pixels."""
    out = np.zeros((1, 9, 8400), np.float32)
    for i, (cx, cy, w, h, c, s) in enumerate(boxes):
        out[0, :4, i] = (cx, cy, w, h)
        out[0, 4 + c, i] = s
    return out


def test_postprocess_maps_letterbox_boxes_back_to_the_original_image():
    # a 320x240 ESP32-CAM frame: scale 2.0, padded 80 px top and bottom
    d = pothole_model.postprocess(raw_output([(320, 320, 100, 100, 3, 0.9)]), 2.0, (0, 80), (240, 320, 3))
    assert len(d) == 1 and d[0]["bbox"] == [135, 95, 50, 50] and d[0]["confidence"] == 0.9
    assert d[0]["severity"] == "low" and d[0]["area_ratio"] == pytest.approx(50 * 50 / (240 * 320), abs=1e-3)


def test_only_potholes_above_the_score_floor_count_and_overlaps_merge():
    raw = raw_output([(320, 320, 100, 100, 0, 0.95),          # a crack, not a pothole
                      (200, 300, 80, 80, 3, 0.10),            # too unsure
                      (400, 300, 90, 90, 3, 0.80), (402, 302, 90, 90, 3, 0.70)])    # the same pothole twice
    d = pothole_model.postprocess(raw, 1.0, (0, 0), (640, 640, 3))
    assert len(d) == 1 and d[0]["confidence"] == 0.8


def test_odd_shaped_output_gives_no_detections():
    assert pothole_model.postprocess(np.zeros((3,), np.float32), 1.0, (0, 0), (10, 10, 3)) == []
    assert pothole_model.postprocess(np.zeros((1, 4, 10), np.float32), 1.0, (0, 0), (10, 10, 3)) == []


def test_without_weights_the_heuristic_runs_and_is_labelled(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOWSYNC_POTHOLE_MODEL", str(tmp_path / "missing.onnx"))
    img = road()
    cv2.ellipse(img, (160, 120), (40, 30), 0, 0, 360, (35, 35, 35), -1)
    det, source = pothole.detect_labelled(img)
    assert source == "camera_heuristic" and len(det) == 1
    assert pothole_model.status()["installed"] is False


def test_a_broken_model_file_falls_back_instead_of_failing(monkeypatch, tmp_path):
    bad = tmp_path / "bad.onnx"
    bad.write_bytes(b"not a model")
    monkeypatch.setenv("FLOWSYNC_POTHOLE_MODEL", str(bad))
    assert pothole.detect_labelled(road())[1] == "camera_heuristic"


def test_model_detections_are_labelled_and_still_need_an_operator(client, ctx, op_headers, monkeypatch):
    class Fake:
        def detect(self, img):
            return [{"confidence": 0.77, "area_ratio": 0.05, "bbox": [10, 10, 60, 40], "severity": "medium", "model": "fake"}]
    monkeypatch.setattr(pothole_model, "get", lambda: Fake())
    r = upload(client, ctx, jpeg(road()), edge="L12", progress=0.4)
    assert r.status_code == 200 and r.json()["reported_hazard"]
    h = ctx.db.one("SELECT * FROM hazards")
    assert (h["status"], h["source"]) == ("reported", "camera_model")
    assert ctx.store.hazard_penalty == {}, "an unreviewed model detection never affects routing or alerts"
    assert client.post(f"/api/operator/hazards/{h['id']}/verify", headers=op_headers).status_code == 200
    assert ctx.store.hazard_penalty == {"L12": 8.0}
