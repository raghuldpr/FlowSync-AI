"""Pretrained pothole detector (YOLOv8s trained on the Road Damage Dataset), run with OpenCV's DNN module on the laptop.

The (standalone) ESP32-CAM only takes the picture; inference happens here. No extra dependency: OpenCV is already required for vision.
The weights are NOT in git. Install them with `python -m app.vision.fetch_model` (see docs/POTHOLE_MODEL.md). When the
file is missing, pothole.detect_labelled() falls back to the labelled heuristic.

Output of the exported model: (1, 4 + 5, 8400) = box centre/size in 640x640 letterbox pixels, then 5 class scores
(0 longitudinal crack, 1 transverse crack, 2 alligator crack, 3 pothole, 4 other). Only class 3 is used.
"""
import os
import threading
from pathlib import Path

import cv2
import numpy as np

from ..config import ROOT

MODEL_NAME = "pothole_yolov8s_rdd.onnx"
DEFAULT_PATH = ROOT / "models" / MODEL_NAME
INPUT = 640
POTHOLE_CLASS = 3
SCORE_MIN = 0.25
NMS_IOU = 0.5

_lock = threading.Lock()
_model = None
_tried = None


def model_path() -> Path:
    return Path(os.getenv("FLOWSYNC_POTHOLE_MODEL", str(DEFAULT_PATH)))


def postprocess(raw: np.ndarray, scale: float, pad: tuple, shape: tuple, score_min: float = SCORE_MIN) -> list:
    """Raw YOLOv8 output -> detections in ORIGINAL image pixels. Separated from inference so it is testable without weights."""
    out = np.squeeze(raw)
    if out.ndim != 2:
        return []
    if out.shape[0] < out.shape[1]:
        out = out.T                                           # (8400, 9)
    if out.shape[1] <= 4 + POTHOLE_CLASS:
        return []
    scores = out[:, 4 + POTHOLE_CLASS]
    keep = scores >= score_min
    if not keep.any():
        return []
    boxes, sc = out[keep, :4], scores[keep]
    h, w = shape[:2]
    px, py = pad
    xywh = []
    for cx, cy, bw, bh in boxes:
        x1 = (cx - bw / 2 - px) / scale
        y1 = (cy - bh / 2 - py) / scale
        x1, y1 = max(0.0, x1), max(0.0, y1)
        xywh.append([int(x1), int(y1), int(min(w - x1, bw / scale)), int(min(h - y1, bh / scale))])
    idx = cv2.dnn.NMSBoxes(xywh, [float(x) for x in sc], score_min, NMS_IOU)
    dets = []
    for i in np.array(idx).flatten():
        x, y, bw, bh = xywh[int(i)]
        if bw <= 0 or bh <= 0:
            continue
        ratio = bw * bh / float(h * w)
        dets.append({"confidence": round(float(sc[int(i)]), 2), "area_ratio": round(ratio, 3), "bbox": [x, y, bw, bh],
                     "severity": "low" if ratio < 0.04 else "medium" if ratio < 0.12 else "high", "model": MODEL_NAME})
    return sorted(dets, key=lambda d: -d["confidence"])


def letterbox(img_bgr: np.ndarray):
    h, w = img_bgr.shape[:2]
    scale = min(INPUT / w, INPUT / h)
    nw, nh = int(round(w * scale)), int(round(h * scale))
    resized = cv2.resize(img_bgr, (nw, nh), interpolation=cv2.INTER_LINEAR)
    canvas = np.full((INPUT, INPUT, 3), 114, np.uint8)
    px, py = (INPUT - nw) // 2, (INPUT - nh) // 2
    canvas[py:py + nh, px:px + nw] = resized
    return canvas, scale, (px, py)


class PotholeModel:
    def __init__(self, path: Path):
        self.net = cv2.dnn.readNetFromONNX(str(path))
        self.lock = threading.Lock()                          # cv2.dnn nets are not thread-safe

    def detect(self, img_bgr: np.ndarray) -> list:
        canvas, scale, pad = letterbox(img_bgr)
        blob = cv2.dnn.blobFromImage(canvas, 1 / 255.0, (INPUT, INPUT), swapRB=True)
        with self.lock:
            self.net.setInput(blob)
            raw = self.net.forward()
        return postprocess(raw, scale, pad, img_bgr.shape)


def get():
    """The loaded model, or None when the weights are not installed (or cannot be loaded). Looked up once per path."""
    global _model, _tried
    p = model_path()
    with _lock:
        if _tried != str(p):
            _tried, _model = str(p), None
            if p.is_file():
                try:
                    _model = PotholeModel(p)
                except Exception:
                    _model = None
        return _model


def status() -> dict:
    p = model_path()
    return {"installed": p.is_file(), "loaded": get() is not None, "path": str(p), "name": MODEL_NAME}
