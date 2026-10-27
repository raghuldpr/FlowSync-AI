"""Overhead-camera perception: ArUco homography calibration, empty-table background differencing for ROI
occupancy, and ArUco tracking of the connected car (the EV).

Frame -> detect ArUco -> homography (image -> layout units) -> rectified top-down image (1 px = 1 layout unit)
      -> |frame - empty-table reference| inside each approach ROI -> vehicle count + "stationary for" seconds
      -> EV marker -> (edge, progress).

The homography is refreshed on every frame in which all four corner markers are visible, so a camera that
shifts slightly stays aligned; if the corners are hidden the last good one is kept and `calib_age_s` grows.
Counts are ESTIMATES: label the source honestly and spot-check against the real table.
"""
import math
from dataclasses import dataclass, field
from typing import Optional

import cv2
import numpy as np

from .config import VisionConfig
from .geometry import project_to_edge, roi_polygon


@dataclass
class EVReading:
    vehicle_id: str
    marker_id: int
    edge: str
    progress: float
    x: float
    y: float
    heading: tuple


@dataclass
class Observation:
    calibrated: bool                 # a homography is available
    fresh_calibration: bool          # all four corner markers were seen in THIS frame
    calib_age_s: Optional[float]     # seconds since the last fresh calibration
    calib_error: Optional[float]     # mean reprojection error of the last calibration (layout units)
    counts: dict = field(default_factory=dict)      # approach -> (vehicles, stationary_s)
    ev: list = field(default_factory=list)          # [EVReading]
    reason: Optional[str] = None     # why counts are missing, if they are


class VisionPipeline:
    def __init__(self, topo, cfg: Optional[VisionConfig] = None):
        self.topo = topo
        self.cfg = cfg or VisionConfig.from_topology(topo)
        c = self.cfg
        self.dictionary = cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, c.aruco_dictionary))
        self.detector = cv2.aruco.ArucoDetector(self.dictionary, cv2.aruco.DetectorParameters())
        self.W, self.H_ = int(c.canvas[0]), int(c.canvas[1])
        self.masks = {}
        for a in topo.approaches:
            m = np.zeros((self.H_, self.W), np.uint8)
            cv2.fillPoly(m, [np.round(np.array(roi_polygon(topo, c, a))).astype(np.int32)], 255)
            self.masks[a] = m
        self.car_area = c.car_length * c.car_width
        self.H = None
        self.calib_error = None
        self._last_fresh = None
        self.ref = None
        self._ref_blur = None
        self._still = {a: {"mask": None, "since": None} for a in topo.approaches}
        self._prev_edge = {}
        self.last_rect = None
        self.last_fg = None
        self._kernel = np.ones((3, 3), np.uint8)

    # ---- markers / calibration ----------------------------------------------------------------
    def detect(self, gray) -> dict:
        corners, ids, _ = self.detector.detectMarkers(gray)
        if ids is None:
            return {}
        return {int(i): c.reshape(4, 2).astype(np.float64) for i, c in zip(ids.flatten(), corners)}

    def _calibrate(self, markers: dict):
        s = self.cfg.corner_marker_size / 2.0
        src, dst = [], []
        for mid, (cx, cy) in self.cfg.corner_markers.items():
            if mid not in markers:
                return None
            src += markers[mid].tolist()
            dst += [(cx - s, cy - s), (cx + s, cy - s), (cx + s, cy + s), (cx - s, cy + s)]
        src, dst = np.array(src, np.float64), np.array(dst, np.float64)
        H, _ = cv2.findHomography(src, dst, 0)
        if H is None:
            return None
        proj = cv2.perspectiveTransform(src.reshape(-1, 1, 2), H).reshape(-1, 2)
        return H, float(np.mean(np.linalg.norm(proj - dst, axis=1)))

    def rectify(self, gray):
        return cv2.warpPerspective(gray, self.H, (self.W, self.H_))

    # ---- reference ----------------------------------------------------------------------------
    def set_reference(self, rect_gray_images: list):
        """Empty-table background (median of a few rectified frames)."""
        self.ref = np.median(np.stack(rect_gray_images), axis=0).astype(np.uint8)
        self._ref_blur = cv2.GaussianBlur(self.ref, (5, 5), 0)

    def capture_reference(self, frames: list) -> bool:
        rects = []
        for f in frames:
            gray = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY) if f.ndim == 3 else f
            cal = self._calibrate(self.detect(gray))
            if cal is None or cal[1] > self.cfg.max_calibration_error:
                continue
            self.H, self.calib_error = cal
            rects.append(self.rectify(gray))
        if len(rects) < max(1, len(frames) // 2):
            return False
        self.set_reference(rects)
        return True

    # ---- per frame ----------------------------------------------------------------------------
    def process(self, frame, now: float) -> Observation:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
        markers = self.detect(gray)
        fresh, reason = False, None
        cal = self._calibrate(markers)
        if cal is not None:
            if cal[1] <= self.cfg.max_calibration_error:
                self.H, self.calib_error = cal
                self._last_fresh, fresh = now, True
            else:
                reason = "calibration_error_high"
        if self.H is None:
            return Observation(False, False, None, self.calib_error, reason=reason or "no_calibration")
        age = None if self._last_fresh is None else now - self._last_fresh
        rect = self.rectify(gray)
        self.last_rect = rect
        obs = Observation(True, fresh, age, self.calib_error)
        obs.ev = self._ev(markers)
        if self.ref is None:
            obs.reason = "no_reference"
        else:
            obs.counts = self._counts(rect, now)
        return obs

    def _ev(self, markers: dict) -> list:
        out = []
        for mid, vid in self.cfg.ev_markers.items():
            if mid not in markers:
                continue
            pts = cv2.perspectiveTransform(markers[mid].reshape(-1, 1, 2), self.H).reshape(-1, 2)
            cx, cy = pts.mean(axis=0)
            up = (pts[0] + pts[1]) / 2 - (pts[3] + pts[2]) / 2          # marker "up" = the way the car faces
            n = float(np.hypot(*up)) or 1.0
            heading = (float(up[0] / n), float(up[1] / n))
            hit = project_to_edge(self.topo, self.cfg, float(cx), float(cy), heading, self._prev_edge.get(vid))
            if hit is None:
                continue
            self._prev_edge[vid] = hit[0]
            out.append(EVReading(vid, mid, hit[0], float(hit[1]), float(cx), float(cy), heading))
        return out

    def _counts(self, rect, now: float) -> dict:
        c = self.cfg
        gain = float(np.clip(np.median(self.ref) / max(1.0, float(np.median(rect))), 0.7, 1.4))
        f = cv2.GaussianBlur(cv2.convertScaleAbs(rect, alpha=gain), (5, 5), 0)     # tolerate +-exposure drift
        fg = (cv2.absdiff(f, self._ref_blur) > c.diff_threshold).astype(np.uint8) * 255
        fg = cv2.morphologyEx(fg, cv2.MORPH_OPEN, self._kernel)
        self.last_fg = fg
        out = {}
        for a, mask in self.masks.items():
            roi = cv2.bitwise_and(fg, mask)
            n_lab, _, stats, _ = cv2.connectedComponentsWithStats(roi, connectivity=8)
            n = 0
            for i in range(1, n_lab):
                area = float(stats[i, cv2.CC_STAT_AREA])
                if area >= c.min_blob_fraction * self.car_area:
                    n += max(1, int(area / self.car_area + 0.5))     # merged (touching) cars: split by area
            n = min(n, 64)
            st = self._still[a]
            if n == 0:
                st["mask"], st["since"], still = None, now, 0.0
            elif st["mask"] is None:
                st["mask"], st["since"], still = roi.copy(), now, 0.0
            else:
                union = int(np.count_nonzero(cv2.bitwise_or(roi, st["mask"])))
                iou = 1.0 if union == 0 else int(np.count_nonzero(cv2.bitwise_and(roi, st["mask"]))) / union
                if iou < c.stationary_iou:
                    st["mask"], st["since"] = roi.copy(), now        # something moved: restart the clock
                still = now - st["since"]
            out[a] = (n, round(still, 1))
        return out

    # ---- debugging ----------------------------------------------------------------------------
    def annotate(self, obs: Observation):
        """Rectified top-down view with ROIs, counts, the EV and corner markers drawn on it (for tuning on the real table)."""
        if self.last_rect is None:
            return None
        img = cv2.cvtColor(self.last_rect, cv2.COLOR_GRAY2BGR)
        if self.last_fg is not None:
            img[self.last_fg > 0] = (0.5 * img[self.last_fg > 0] + 0.5 * np.array([0, 0, 255])).astype(np.uint8)
        for a, mask in self.masks.items():
            poly = np.round(np.array(roi_polygon(self.topo, self.cfg, a))).astype(np.int32)
            cv2.polylines(img, [poly], True, (0, 160, 0), 1)
            n, still = obs.counts.get(a, (None, None))
            cx, cy = poly.mean(axis=0).astype(int)
            if n is not None:
                cv2.putText(img, f"{a}:{n}" + (f" {still:.0f}s" if still and still >= 3 else ""), (int(cx) - 22, int(cy) + 4),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 0, 0), 1, cv2.LINE_AA)
        for e in obs.ev:
            p = (int(e.x), int(e.y))
            cv2.circle(img, p, 12, (255, 0, 255), 2)
            cv2.arrowedLine(img, p, (int(e.x + 26 * e.heading[0]), int(e.y + 26 * e.heading[1])), (255, 0, 255), 2)
            cv2.putText(img, f"{e.vehicle_id} {e.edge} {e.progress:.2f}", (p[0] + 14, p[1] - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 0, 255), 1, cv2.LINE_AA)
        txt = "calib err %.1f%s" % (obs.calib_error or -1, "" if obs.fresh_calibration else "  (corners not visible)")
        cv2.putText(img, txt, (10, self.H_ - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1, cv2.LINE_AA)
        return img
