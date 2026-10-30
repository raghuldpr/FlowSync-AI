"""Road conditions: verification workflow, explainable route penalties, road-user alerts, secure image upload, heuristic detector."""
import base64
import json
import os

import pytest

from app import images
from app.core.hazards import PENALTY_CAP_S
from app.core.routing import dijkstra, edge_costs
from simulator.tabletop import DeviceClient

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 200 + b"\xff\xd9"          # structurally valid enough for the storage checks


def b64(b):
    return base64.b64encode(b).decode()


def route(ctx):
    return dijkstra(ctx.topology, edge_costs(ctx.topology, {}, set(), ctx.store.hazard_penalty), "ext_W", "ext_S")


# ---- lifecycle and effects ----------------------------------------------------------------------------------------
def test_open_hazard_adds_an_explainable_penalty_and_changes_the_best_route(ctx):
    assert route(ctx)[0] == ["W1", "L13", "O3"] and route(ctx)[1] == pytest.approx(13)
    ctx.hazards.create("pothole", "L13", "high", "operator", actor="op")
    assert ctx.store.hazard_penalty == {"L13": 15.0}
    path, cost = route(ctx)
    assert path == ["W1", "L12", "L23", "O3"] and cost == pytest.approx(19)
    hid = ctx.db.one("SELECT id FROM hazards")["id"]
    assert ctx.hazards.resolve(hid, "op") and ctx.store.hazard_penalty == {}
    assert route(ctx)[0] == ["W1", "L13", "O3"]


def test_penalties_add_up_per_road_but_are_capped(ctx):
    for _ in range(4):
        ctx.hazards.create("debris", "L13", "high", "operator", actor="op")
    assert ctx.store.hazard_penalty["L13"] == PENALTY_CAP_S
    ctx.hazards.create("wet_surface", "L12", "low", "operator", actor="op")
    assert ctx.store.hazard_penalty["L12"] == 3.0


def test_reports_do_nothing_until_an_operator_verifies_them(ctx):
    hid = ctx.hazards.create("pothole", "L13", "high", "public", status="reported")
    assert ctx.store.hazard_penalty == {} and route(ctx)[0] == ["W1", "L13", "O3"] and ctx.hazards.pending() == 1
    assert ctx.hazards.verify(hid, "op") and ctx.store.hazard_penalty == {"L13": 15.0}
    assert not ctx.hazards.verify(hid, "op"), "already open"
    h2 = ctx.hazards.create("pothole", "L12", "high", "camera_heuristic", status="reported")
    assert ctx.hazards.reject(h2, "op") and ctx.store.hazard_penalty == {"L13": 15.0}
    assert not ctx.hazards.resolve(h2, "op") and not ctx.hazards.verify(h2, "op")


def test_validation_and_review_queue_bound(ctx):
    for bad in [("sinkhole", "L13", "low"), ("pothole", "ZZ", "low"), ("pothole", "L13", "extreme")]:
        with pytest.raises(ValueError):
            ctx.hazards.create(*bad, "operator")
    for _ in range(25):
        ctx.hazards.create("pothole", "L13", "low", "public", status="reported")
    with pytest.raises(OverflowError):
        ctx.hazards.create("pothole", "L13", "low", "public", status="reported")


# ---- operator + public API ----------------------------------------------------------------------------------------
def test_operator_api_is_closed_to_the_public_and_audited(client, ctx, op_headers):
    body = {"kind": "pothole", "edge": "L13", "severity": "high"}
    assert client.post("/api/operator/hazards", json=body).status_code == 401
    assert client.get("/api/operator/hazards").status_code == 401
    assert client.post("/api/operator/hazards/1/verify").status_code == 401
    assert client.post("/api/operator/hazards", json={**body, "edge": "nope"}, headers=op_headers).status_code == 422
    assert client.post("/api/operator/hazards", json={**body, "kind": "sinkhole"}, headers=op_headers).status_code == 422
    hid = client.post("/api/operator/hazards", json=body, headers=op_headers).json()["id"]
    assert client.post(f"/api/operator/hazards/{hid}/verify", headers=op_headers).status_code == 409
    assert client.post(f"/api/operator/hazards/{hid}/resolve", headers=op_headers).status_code == 200
    assert client.post(f"/api/operator/hazards/{hid}/resolve", headers=op_headers).status_code == 409
    assert client.get("/api/operator/hazards", params={"status": "resolved"}, headers=op_headers).json()[0]["id"] == hid
    acts = {(a["action"], a["result"]) for a in ctx.db.query("SELECT * FROM audit_log")}
    assert ("hazard_create", "ok") in acts and ("hazard_resolve", "ok") in acts and ("hazard_verify", "not_applicable") in acts
    assert any(r == "denied_unauthenticated" for _, r in acts)


def test_public_report_uses_the_users_own_position_and_is_rate_limited(client, ctx, op_headers):
    j = client.post("/api/public/road-users", json={"origin": "ext_W", "destination": "ext_S"}).json()
    tok = j["token"]
    assert client.post("/api/public/hazards", json={"token": "nope", "kind": "pothole"}).status_code == 404
    assert client.post("/api/public/hazards", json={"token": tok, "kind": "pothole", "edge": "L12"}).status_code == 422, "client cannot choose the place"
    r = client.post("/api/public/hazards", json={"token": tok, "kind": "pothole"})
    assert r.status_code == 200 and "operator will review" in r.json()["message"]
    h = ctx.db.one("SELECT * FROM hazards")
    assert (h["status"], h["source"], h["edge"]) == ("reported", "public", "W1")
    assert ctx.store.hazard_penalty == {}, "a public report changes nothing until verified"
    codes = [client.post("/api/public/hazards", json={"token": tok, "kind": "debris"}).status_code for _ in range(5)]
    assert codes.count(429) >= 1
    assert client.get("/api/operator/hazards", params={"status": "reported"}, headers=op_headers).json()[0]["source"] == "public"


def test_road_users_are_alerted_to_verified_hazards_ahead_and_told_when_cleared(ctx, clock):
    for a in ctx.topology.approaches:
        ctx.store.set_traffic(a, 0, "simulated")
    u = ctx.roadusers.join("ext_W", "ext_S")
    hid = ctx.hazards.create("pothole", "L13", "medium", "operator", actor="op")
    ctx.roadusers.tick()
    a = u.alerts[f"haz:{hid}"]
    assert a["state"] == "active" and "pothole" in a["text"] and "Drive with extra care" in a["text"]
    ctx.hazards.resolve(hid, "op")
    ctx.roadusers.tick()
    assert u.alerts[f"haz:{hid}"]["state"] == "cleared" and "cleared" in u.alerts[f"haz:{hid}"]["text"]


# ---- image upload ------------------------------------------------------------------------------------------------
def cam(client, ctx, name="roadcam-1"):
    return DeviceClient(client, name, ctx.device_specs[name]["key"])


def upload(client, ctx, data=JPEG, edge="L13", progress=0.5, who="roadcam-1", raw_b64=None):
    return cam(client, ctx, who).request("POST", "/api/device/images", {"edge": edge, "progress": progress, "image_b64": raw_b64 or b64(data)})


def test_upload_stores_under_a_server_generated_name(client, ctx, op_headers):
    r = upload(client, ctx)
    assert r.status_code == 200
    iid = r.json()["image"]
    row = ctx.db.one("SELECT * FROM images WHERE id=?", (iid,))
    assert os.path.basename(row["path"]) == f"{iid}.jpg" and os.path.dirname(row["path"]) == ctx.settings.image_dir
    got = client.get(f"/api/operator/images/{iid}", headers=op_headers)
    assert got.status_code == 200 and got.content == JPEG and got.headers["content-type"] == "image/jpeg"
    assert got.headers["x-content-type-options"] == "nosniff"
    assert got.headers["x-detection"] == "[]", "detector boxes travel with the image for the dashboard overlay"
    assert client.get(f"/api/operator/images/{iid}").status_code == 401, "images are operator-only"


def test_camera_frames_lists_every_upload_with_time_even_without_a_hazard(client, ctx, op_headers):
    iid = upload(client, ctx).json()["image"]
    assert client.get("/api/operator/camera-frames").status_code == 401, "frame list is not public"
    frames = client.get("/api/operator/camera-frames", headers=op_headers).json()["frames"]
    assert [f["id"] for f in frames] == [iid] and frames[0]["created_at"] and frames[0]["edge"] == "L13"
    assert frames[0]["detections"] == [] and ctx.hazards.list() == [], "a frame with no detection is listed but raises no hazard"


@pytest.mark.parametrize("data,why", [
    (b"GIF89a" + b"\x00" * 50, "not a JPEG"), (b"\xff\xd8\xff" + b"\x00" * 50, "truncated"), (b"", "empty"),
    (b"\xff\xd8\xff" + b"\x00" * (images.MAX_BYTES + 10) + b"\xff\xd9", "too large"),
], ids=["gif", "truncated", "empty", "oversize"])
def test_bad_images_are_rejected(client, ctx, data, why):
    assert upload(client, ctx, data).status_code == 422
    assert not os.path.exists(ctx.settings.image_dir) or os.listdir(ctx.settings.image_dir) == []


def test_upload_input_validation_and_roles(client, ctx, op_headers):
    assert upload(client, ctx, raw_b64="not*base64!").status_code == 422
    assert upload(client, ctx, edge="nope").status_code == 422
    assert upload(client, ctx, progress=2.0).status_code == 422
    assert upload(client, ctx, who="camera-1").status_code == 403, "only the road camera may upload images"
    assert upload(client, ctx, who="car-1").status_code == 403
    assert client.post("/api/device/images", json={"edge": "L13", "progress": 0.1, "image_b64": b64(JPEG)}).status_code in (401, 403)
    for bad in ("../../etc/passwd", "a" * 32 + "/../x", "ZZ" * 16, "0" * 31):
        assert client.get(f"/api/operator/images/{bad}", headers=op_headers).status_code in (404, 422)
    assert client.get("/api/operator/images/" + "0" * 32, headers=op_headers).status_code == 404


def test_location_defaults_to_the_tracked_car(client, ctx):
    d = cam(client, ctx)
    r = d.request("POST", "/api/device/images", {"image_b64": b64(JPEG)})
    assert r.status_code == 422 and "location unknown" in r.text
    ctx.ev.declare("EV-1", "ext_W", "ext_S", "operator", "t")
    ctx.ev.position("EV-1", "W1", 0.4)
    r = d.request("POST", "/api/device/images", {"image_b64": b64(JPEG)})
    assert r.status_code == 200
    assert ctx.db.one("SELECT edge, progress FROM images WHERE id=?", (r.json()["image"],)) == {"edge": "W1", "progress": 0.4}


def test_old_images_are_purged(client, ctx):
    iid = upload(client, ctx).json()["image"]
    path = ctx.db.one("SELECT path FROM images WHERE id=?", (iid,))["path"]
    import time
    assert images.purge_old(ctx.db, ctx.settings.image_dir) == 0 and os.path.exists(path)
    assert images.purge_old(ctx.db, ctx.settings.image_dir, now=time.time() + images.KEEP_S + 60) == 1
    assert not os.path.exists(path) and ctx.db.one("SELECT 1 FROM images") is None


# ---- the heuristic detector (needs OpenCV) -------------------------------------------------------------------------
cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")
from app.vision import pothole  # noqa: E402


def road(seed=0, brightness=170):
    rng = np.random.default_rng(seed)
    img = np.clip(rng.normal(brightness, 8, (240, 320)), 0, 255).astype(np.uint8)
    return cv2.cvtColor(cv2.GaussianBlur(img, (5, 5), 0), cv2.COLOR_GRAY2BGR)


def jpeg(img):
    return cv2.imencode(".jpg", img)[1].tobytes()


def test_detector_finds_a_dark_patch_and_ignores_plain_road_shadows_and_bright_patches():
    img = road()
    assert pothole.detect(img) == []
    cv2.ellipse(img, (160, 120), (40, 30), 0, 0, 360, (35, 35, 35), -1)
    d = pothole.detect(img)
    assert len(d) == 1 and d[0]["confidence"] >= 0.8 and d[0]["severity"] == "medium" and abs(d[0]["bbox"][0] + d[0]["bbox"][2] / 2 - 160) < 6
    shadow = road(1)
    shadow[:, :230] = (shadow[:, :230] * 0.35).astype(np.uint8)          # a big shadow across the frame
    assert pothole.detect(shadow) == []
    bright = road(2)
    cv2.ellipse(bright, (160, 120), (40, 30), 0, 0, 360, (250, 250, 250), -1)
    assert pothole.detect(bright) == []
    tiny = road(3)
    cv2.circle(tiny, (100, 100), 4, (30, 30, 30), -1)
    assert pothole.detect(tiny) == [], "specks are noise"


def test_camera_detection_becomes_an_unreviewed_report_that_needs_an_operator(client, ctx, op_headers):
    img = road()
    cv2.ellipse(img, (160, 120), (40, 30), 0, 0, 360, (35, 35, 35), -1)
    r = upload(client, ctx, jpeg(img), edge="L12", progress=0.4)
    assert r.status_code == 200 and r.json()["detections"] == 1 and r.json()["reported_hazard"]
    h = ctx.db.one("SELECT * FROM hazards")
    assert (h["status"], h["source"], h["kind"], h["edge"], h["image_id"]) == ("reported", "camera_heuristic", "pothole", "L12", r.json()["image"])
    assert h["confidence"] >= 0.5 and ctx.store.hazard_penalty == {}
    assert client.post(f"/api/operator/hazards/{h['id']}/verify", headers=op_headers).status_code == 200
    assert ctx.store.hazard_penalty == {"L12": 8.0}
    clean = upload(client, ctx, jpeg(road()), edge="L12")
    assert clean.json()["detections"] == 0 and clean.json()["reported_hazard"] is None


# ---- one pothole in many frames; simulated authority hand-off ------------------------------------------------------
def test_find_active_matches_same_road_and_nearby_position(ctx):
    hid = ctx.hazards.create("pothole", "L13", "low", "camera_heuristic", status="reported", progress=0.5)
    assert ctx.hazards.find_active("pothole", "L13", 0.55) == hid
    assert ctx.hazards.find_active("pothole", "L13", 0.9) is None
    assert ctx.hazards.find_active("pothole", "L12", 0.5) is None
    assert ctx.hazards.find_active("debris", "L13", 0.5) is None
    ctx.hazards.reject(hid, "op")
    assert ctx.hazards.find_active("pothole", "L13", 0.5) is None, "a rejected report must not hide a new sighting"


def test_authority_report_needs_a_verified_hazard_and_is_sent_once(client, ctx, op_headers):
    hid = ctx.hazards.create("pothole", "L13", "medium", "camera_heuristic", status="reported", progress=0.5)
    assert client.post(f"/api/operator/hazards/{hid}/notify-authority").status_code == 401
    assert client.post(f"/api/operator/hazards/{hid}/notify-authority", headers=op_headers).status_code == 409
    ctx.hazards.verify(hid, "op")
    assert client.post(f"/api/operator/hazards/{hid}/notify-authority", headers=op_headers).status_code == 200
    assert client.post(f"/api/operator/hazards/{hid}/notify-authority", headers=op_headers).status_code == 409
    out = client.get("/api/operator/authority-reports", headers=op_headers).json()
    assert out["simulated"] is True and [r["hazard_id"] for r in out["reports"]] == [hid]
