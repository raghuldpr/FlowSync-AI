"""Synthetic overhead camera: renders the tabletop (roads, queued cars, ArUco corner markers and the EV marker)
from KNOWN ground truth, seen through a perspective camera with optional noise / exposure drift.

Used to test the vision pipeline against the truth, and to drive the whole system with 'camera' data before the
real phone is mounted. It is a stand-in: real lighting, shadows and phone optics will be harder than this.
"""
import math
from typing import Optional

import cv2
import numpy as np

from .config import VisionConfig
from .geometry import lane_dir, lane_line, roi_capacity

BOARD = (238, 240, 242)
ROAD = (200, 200, 200)
CAR_COLORS = [(60, 60, 170), (160, 70, 50), (45, 45, 45), (60, 130, 70), (150, 90, 20)]
DEFAULT_QUAD = [(110, 60), (1190, 95), (1160, 680), (70, 640)]      # where the board's corners land in the image


class SyntheticTable:
    S = 2   # render the board at 2x then warp, so markers stay crisp

    def __init__(self, topo, cfg: Optional[VisionConfig] = None, image_size=(1280, 720), seed: int = 0):
        self.topo, self.cfg, self.size = topo, cfg or VisionConfig.from_topology(topo), image_size
        self.rng = np.random.default_rng(seed)
        self.dictionary = cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, self.cfg.aruco_dictionary))

    # ---- board ----------------------------------------------------------------------------------
    def _marker_image(self, marker_id: int, side_units: float):
        px = int(round(side_units * self.S))
        m = cv2.aruco.generateImageMarker(self.dictionary, marker_id, px)
        q = max(2, px // 5)                                            # white quiet zone around the black border
        return cv2.copyMakeBorder(m, q, q, q, q, cv2.BORDER_CONSTANT, value=255)

    def _blit_marker(self, canvas, marker_id, side_units, cx, cy, up=(0.0, -1.0)):
        img = self._marker_image(marker_id, side_units)
        h, w = img.shape
        ux, uy = up                                                   # rotation taking the marker's "up" (0,-1) to `up`
        R = np.array([[-uy, -ux], [ux, -uy]], np.float64)
        t = np.array([cx * self.S, cy * self.S]) - R @ np.array([w / 2, h / 2])
        M = np.hstack([R, t.reshape(2, 1)])
        rgb = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
        cv2.warpAffine(rgb, M, (canvas.shape[1], canvas.shape[0]), dst=canvas, flags=cv2.INTER_LINEAR,
                       borderMode=cv2.BORDER_TRANSPARENT)

    def car_slots(self, approach: str, n: int, gap: Optional[float] = None):
        """Centres/orientations of n queued cars, nose at the stop line. Returns (list, placed)."""
        c = self.cfg
        gap = c.car_gap if gap is None else gap
        x1, y1, x2, y2 = lane_line(self.topo, c, approach)
        ux, uy, L = lane_dir((x1, y1, x2, y2))
        n = min(n, roi_capacity(self.topo, c, approach) if gap == c.car_gap else int(L // (c.car_length + gap)))   # tighter packing may exceed the ROI: that is a test of merged blobs
        slots = []
        for i in range(n):
            d = c.car_length / 2 + i * (c.car_length + gap)
            slots.append((x2 - ux * d, y2 - uy * d, ux, uy))
        return slots, n

    def draw_board(self, queues: Optional[dict] = None, ev: Optional[dict] = None, gap: Optional[float] = None,
                   hide_markers=(), car_shift: Optional[dict] = None):
        c, S = self.cfg, self.S
        W, H = c.canvas
        canvas = np.full((H * S, W * S, 3), BOARD, np.uint8)
        for eid in self.topo.edges:                                   # lanes
            x1, y1, x2, y2 = lane_line(self.topo, c, eid)
            cv2.line(canvas, (int(x1 * S), int(y1 * S)), (int(x2 * S), int(y2 * S)), ROAD, int(2 * c.roi_half_width * S))
        for n in self.topo.intersections:                             # intersection areas are CIRCLES of node_radius
            x, y = self.topo.layout[n]
            cv2.circle(canvas, (int(x * S), int(y * S)), int(c.node_radius * S), (185, 185, 185), -1)
        for mid, (cx, cy) in c.corner_markers.items():
            if mid not in hide_markers:
                self._blit_marker(canvas, mid, c.corner_marker_size, cx, cy)
        placed = {}
        for a, n in (queues or {}).items():
            slots, k = self.car_slots(a, n, gap)
            placed[a] = k
            dx, dy = (car_shift or {}).get(a, (0.0, 0.0))
            for i, (x, y, ux, uy) in enumerate(slots):
                x, y = x + dx, y + dy
                nx, ny = -uy, ux
                hl, hw = c.car_length / 2, c.car_width / 2
                pts = np.array([[x + ux * hl + nx * hw, y + uy * hl + ny * hw], [x + ux * hl - nx * hw, y + uy * hl - ny * hw],
                                [x - ux * hl - nx * hw, y - uy * hl - ny * hw], [x - ux * hl + nx * hw, y - uy * hl + ny * hw]]) * S
                cv2.fillPoly(canvas, [pts.astype(np.int32)], CAR_COLORS[(sum(map(ord, a)) + i) % len(CAR_COLORS)])
        if ev is not None:
            x1, y1, x2, y2 = lane_line(self.topo, c, ev["edge"])
            p = ev["progress"]
            ex, ey = x1 + (x2 - x1) * p, y1 + (y2 - y1) * p
            ux, uy, _ = lane_dir((x1, y1, x2, y2))
            if ev.get("marker_id", 20) not in hide_markers:
                self._blit_marker(canvas, ev.get("marker_id", 20), c.ev_marker_size, ex, ey, (ux, uy))
        return canvas, placed

    # ---- camera ---------------------------------------------------------------------------------
    def camera_matrix(self, quad=None):
        """3x3 mapping LAYOUT units -> image pixels."""
        W, H = self.cfg.canvas
        src = np.float32([[0, 0], [W * self.S, 0], [W * self.S, H * self.S], [0, H * self.S]])
        Hc = cv2.getPerspectiveTransform(src, np.float32(quad or DEFAULT_QUAD))
        return Hc @ np.diag([self.S, self.S, 1.0])

    def render(self, queues=None, ev=None, brightness: float = 1.0, noise: float = 0.0, quad=None, gap=None,
               hide_markers=(), car_shift=None):
        """Returns (BGR image, ground truth {approach: placed_count})."""
        canvas, placed = self.draw_board(queues, ev, gap, hide_markers, car_shift)
        W, H = self.cfg.canvas
        src = np.float32([[0, 0], [W * self.S, 0], [W * self.S, H * self.S], [0, H * self.S]])
        Hc = cv2.getPerspectiveTransform(src, np.float32(quad or DEFAULT_QUAD))
        img = cv2.warpPerspective(canvas, Hc, self.size, flags=cv2.INTER_AREA, borderValue=(50, 50, 50))
        img = np.clip(img.astype(np.float32) * brightness, 0, 255)
        if noise:
            img += self.rng.normal(0, noise, img.shape).astype(np.float32)
        return np.clip(img, 0, 255).astype(np.uint8), placed
