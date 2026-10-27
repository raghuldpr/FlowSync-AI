from dataclasses import dataclass, field


@dataclass
class VisionConfig:
    canvas: tuple = (800, 520)
    corner_markers: dict = field(default_factory=lambda: {10: (40, 40), 11: (760, 40), 12: (760, 480), 13: (40, 480)})
    corner_marker_size: float = 40
    ev_markers: dict = field(default_factory=lambda: {20: "EV-1"})
    ev_marker_size: float = 28
    aruco_dictionary: str = "DICT_4X4_50"
    max_calibration_error: float = 6.0
    lane_offset: float = 14
    node_radius: float = 30
    ext_radius: float = 12
    roi_exit_clearance: float = 30
    roi_start: float = 0.0
    roi_end: float = 1.0
    roi_half_width: float = 10
    car_length: float = 28
    car_width: float = 16
    car_gap: float = 6
    min_blob_fraction: float = 0.35
    diff_threshold: int = 40
    stationary_iou: float = 0.85
    ev_snap_distance: float = 24
    post_hz: float = 2.0

    @classmethod
    def from_topology(cls, topo) -> "VisionConfig":
        raw = dict(getattr(topo, "vision", {}) or {})
        known = {f for f in cls.__dataclass_fields__}
        unknown = set(raw) - known
        if unknown:
            raise ValueError(f"unknown vision settings: {sorted(unknown)}")
        if "canvas" in raw:
            raw["canvas"] = tuple(raw["canvas"])
        if "corner_markers" in raw:
            raw["corner_markers"] = {int(k): tuple(v) for k, v in raw["corner_markers"].items()}
        if "ev_markers" in raw:
            raw["ev_markers"] = {int(k): str(v) for k, v in raw["ev_markers"].items()}
        cfg = cls(**raw)
        if len(cfg.corner_markers) != 4:
            raise ValueError("exactly four corner markers are required for calibration")
        if cfg.roi_half_width >= cfg.lane_offset:
            raise ValueError("roi_half_width must be smaller than lane_offset, or opposite lanes' ROIs overlap")
        if cfg.car_width / 2 > cfg.roi_half_width:
            raise ValueError("roi_half_width must cover half the car width")
        return cfg
