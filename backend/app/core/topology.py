from dataclasses import dataclass

from ..config import load_yaml


@dataclass(frozen=True)
class Timing:
    min_green_ms: int
    yellow_ms: int
    all_red_ms: int
    max_green_ms: int
    max_red_ms: int
    fallback_green_ms: int
    command_valid_ms: int
    backend_lost_ms: int


@dataclass(frozen=True)
class ControlParams:
    interval_s: float
    stale_after_s: float
    switch_margin: float
    spillback_occupancy: float
    link_capacity: int


@dataclass(frozen=True)
class RoutingParams:
    delay_per_vehicle_s: float
    reroute_threshold: float
    min_reroute_interval_s: float


@dataclass(frozen=True)
class EVParams:
    location_ttl_s: float
    warn_horizon_s: float
    imminent_s: float
    default_origin: str
    default_destination: str


@dataclass(frozen=True)
class IncidentParams:
    congestion_occupancy: float
    congestion_hold_s: float
    congestion_clear_occupancy: float
    congestion_clear_hold_s: float
    stall_s: float


@dataclass(frozen=True)
class Approach:
    id: str
    intersection: str
    movement: str          # 'A' | 'B'
    downstream: tuple
    capacity: int = 8      # vehicles that fit in this approach's camera ROI (default: control.link_capacity)


@dataclass(frozen=True)
class Edge:
    id: str
    src: str
    dst: str
    length: float
    speed: float


class Topology:
    def __init__(self, raw: dict):
        self.timing = Timing(**raw["timing"])
        self.control = ControlParams(**raw["control"])
        self.routing = RoutingParams(**raw["routing"])
        self.ev = EVParams(**raw["ev"])
        self.incidents = IncidentParams(**raw["incidents"])
        self.layout = {k: list(v) for k, v in raw.get("layout", {}).items()}
        self.node_labels = dict(raw.get("node_labels", {}))
        self.intersections = list(raw["intersections"])
        self.approaches = {
            aid: Approach(aid, a["intersection"], a["movement"], tuple(a.get("downstream", [])),
                          int(a.get("capacity", self.control.link_capacity)))
            for aid, a in raw["approaches"].items()
        }
        self.vision = dict(raw.get("vision", {}))
        self.nodes = list(raw["nodes"])
        self.edges = {
            e["id"]: Edge(e["id"], e["from"], e["to"], float(e["length"]), float(e["speed"]))
            for e in raw["edges"]
        }
        for a in self.approaches.values():
            if a.capacity < 1:
                raise ValueError(f"approach {a.id}: capacity must be >= 1")
            if a.movement not in ("A", "B"):
                raise ValueError(f"approach {a.id}: movement must be A or B")
            if a.intersection not in self.intersections:
                raise ValueError(f"approach {a.id}: unknown intersection")
            for d in a.downstream:
                if d not in self.approaches:
                    raise ValueError(f"approach {a.id}: unknown downstream {d}")

    @classmethod
    def load(cls, path: str) -> "Topology":
        return cls(load_yaml(path))

    def approaches_of(self, intersection: str, movement: str) -> list:
        return [a for a in self.approaches.values() if a.intersection == intersection and a.movement == movement]
