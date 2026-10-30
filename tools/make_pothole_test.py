"""Generates pothole test material and checks it with the real detector (backend/app/vision/pothole.py).

Outputs in testdata/pothole/:
  road_pothole_*.jpg   road with one dark patch (should be detected)
  road_clear.jpg       road, nothing on it (should NOT be detected)
  road_shadow.jpg      a big shadow band (should NOT be detected: the detector must not call a shadow a hole)
  drive_by.mp4         a 12 s "car driving over the road" clip with a pothole passing under the camera
  print_prop.png       A4 page: print it, cut the black ovals out and lay them on the cardboard road as potholes

Run from the repo root:   python tools/make_pothole_test.py
Needs numpy + opencv (pip install -r requirements-vision.txt).
"""
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
from app.vision import pothole                                   # noqa: E402

OUT = os.path.join(os.path.dirname(__file__), "..", "testdata", "pothole")
W, H = 320, 240                                                   # QVGA, what the ESP32-CAM sends
rng = np.random.default_rng(7)


def road(w=W, h=H, base=120):
    """Grey road with a lighting gradient, grain and a faint centre line."""
    y = np.linspace(0, 1, h)[:, None]
    x = np.linspace(0, 1, w)[None, :]
    img = base + 18 * (x - 0.5) + 10 * (y - 0.5)
    img = img + rng.normal(0, 4, (h, w))
    img = np.clip(img, 0, 255).astype(np.uint8)
    bgr = cv2.merge([img, img, img])
    cv2.line(bgr, (w // 2, 0), (w // 2, h), (200, 200, 200), 2)
    return bgr


def add_hole(img, cx, cy, rx, ry, depth=75, angle=0):
    """A dark, slightly irregular oval with a soft rim."""
    mask = np.zeros(img.shape[:2], np.uint8)
    pts = []
    for t in np.linspace(0, 2 * np.pi, 40, endpoint=False):
        r = 1 + rng.uniform(-0.06, 0.06)
        pts.append([cx + rx * r * np.cos(t), cy + ry * r * np.sin(t)])
    cv2.fillPoly(mask, [np.array(pts, np.int32)], 255)
    M = cv2.getRotationMatrix2D((cx, cy), angle, 1.0)
    mask = cv2.warpAffine(mask, M, (img.shape[1], img.shape[0]))
    soft = cv2.GaussianBlur(mask, (5, 5), 0).astype(np.float32) / 255.0
    out = img.astype(np.float32) * (1 - soft[..., None] * depth / 120.0)
    return np.clip(out, 0, 255).astype(np.uint8)


def save(name, img):
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, name)
    cv2.imwrite(path, img, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return path


def check(name, img, want):
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])
    dec = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    det = pothole.detect(dec)
    got = bool(det) and det[0]["confidence"] >= 0.5
    print(f"{'PASS' if got == want else 'FAIL'}  {name:28s} detected={got} {det[:1]}")
    return got == want


def main():
    results = []
    cases = [("road_pothole_small.jpg", (210, 140, 22, 15), True),
             ("road_pothole_medium.jpg", (150, 120, 40, 28), True),
             ("road_pothole_large.jpg", (170, 130, 62, 44), True)]
    for name, (cx, cy, rx, ry), want in cases:
        img = add_hole(road(), cx, cy, rx, ry)
        save(name, img)
        results.append(check(name, img, want))
    clear = road()
    save("road_clear.jpg", clear)
    results.append(check("road_clear.jpg", clear, False))
    shadow = road()
    band = np.zeros(shadow.shape[:2], np.float32)
    cv2.rectangle(band, (0, 0), (W, 70), 1.0, -1)
    band = cv2.GaussianBlur(band, (31, 31), 0)
    shadow = np.clip(shadow.astype(np.float32) * (1 - 0.5 * band[..., None]), 0, 255).astype(np.uint8)
    save("road_shadow.jpg", shadow)
    results.append(check("road_shadow.jpg", shadow, False))

    # drive-by video: the road scrolls down, a pothole enters at the top and leaves at the bottom
    os.makedirs(OUT, exist_ok=True)
    fps, secs = 15, 12
    vw = cv2.VideoWriter(os.path.join(OUT, "drive_by.mp4"), cv2.VideoWriter_fourcc(*"mp4v"), fps, (W, H))
    base = road(W, H * 3)
    base[:, W // 2 - 1:W // 2 + 1] = 200
    for i in range(0, H * 3, 24):
        cv2.line(base, (W // 2, i), (W // 2, i + 12), (230, 230, 230), 3)
    base = add_hole(base, 150, 330, 38, 26)
    n = fps * secs
    for f in range(n):
        top = int((H * 2) * f / (n - 1))
        frame = base[top:top + H].copy()
        frame = np.clip(frame.astype(np.float32) + rng.normal(0, 2, frame.shape), 0, 255).astype(np.uint8)
        vw.write(frame)
    vw.release()
    print("video: drive_by.mp4 (pothole passes under the camera around 3-8 s)")

    # printable props: A4 at 150 dpi (1240 x 1754), black ovals on white
    page = np.full((1754, 1240, 3), 255, np.uint8)
    for (cx, cy, rx, ry) in [(320, 350, 130, 90), (900, 380, 170, 115), (620, 760, 230, 150), (320, 1180, 90, 62), (880, 1230, 200, 130)]:
        cv2.ellipse(page, (cx, cy), (rx, ry), 12, 0, 360, (25, 25, 25), -1)
    cv2.putText(page, "FlowSync pothole props - cut out and lay on the road", (60, 1640), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (90, 90, 90), 2)
    cv2.imwrite(os.path.join(OUT, "print_prop.png"), page)
    print("printable: print_prop.png")
    print("ALL CHECKS PASS" if all(results) else "SOME CHECKS FAILED")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
