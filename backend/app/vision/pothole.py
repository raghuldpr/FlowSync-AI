"""Pothole / dark-patch candidate finder for road-camera images.

HONEST LABEL: this is a classical-vision HEURISTIC (a compact dark region, clearly darker than the surrounding road, of
plausible size and shape). It is meant for the tabletop (printed pothole props) and for flagging candidates for a human.
It is not a trained model, and every detection becomes an unreviewed report: an operator must verify it before it can
affect routing. Shadows, oil stains and dark vehicles will fool it.
"""
import cv2
import numpy as np

DARK_DELTA = 45          # grey levels below the road's median brightness
MIN_RATIO, MAX_RATIO = 0.01, 0.35
MIN_SOLIDITY = 0.80


def detect(img_bgr) -> list:
    gray = cv2.GaussianBlur(cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY), (7, 7), 0)
    h, w = gray.shape
    med = float(np.median(gray))
    mask = (gray < med - DARK_DELTA).astype(np.uint8) * 255
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    out = []
    for c in contours:
        area = float(cv2.contourArea(c))
        ratio = area / (h * w)
        if not (MIN_RATIO <= ratio <= MAX_RATIO):
            continue
        hull_area = float(cv2.contourArea(cv2.convexHull(c))) or 1.0
        if area / hull_area < MIN_SOLIDITY:
            continue
        x, y, bw, bh = cv2.boundingRect(c)
        if (x <= 1 or y <= 1 or x + bw >= w - 1 or y + bh >= h - 1) and ratio > 0.2:
            continue                                         # a big region running off the frame is a shadow, not a hole
        inside = np.zeros_like(mask)
        cv2.drawContours(inside, [c], -1, 255, -1)
        contrast = (med - float(gray[inside > 0].mean())) / 255.0
        conf = 0.5 * min(1.0, ratio / 0.04) + 0.5 * min(1.0, contrast / 0.35)
        out.append({"confidence": round(conf, 2), "area_ratio": round(ratio, 3), "bbox": [int(x), int(y), int(bw), int(bh)],
                    "severity": "low" if ratio < 0.04 else "medium" if ratio < 0.12 else "high"})
    return sorted(out, key=lambda d: -d["confidence"])
