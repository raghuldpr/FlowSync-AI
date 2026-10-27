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
class Approach:
    id: str
    intersection: str
    movement: str          # 'A' | 'B'
    downstream: tuple


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
        self.intersections = list(raw["intersections"])
        self.approaches = {
            aid: Approach(aid, a["intersection"], a["movement"], tuple(a.get("downstream", [])))
            for aid, a in raw["approaches"].items()
        }
        self.nodes = list(raw["nodes"])
        self.edges = {
            e["id"]: Edge(e["id"], e["from"], e["to"], float(e["length"]), float(e["speed"]))
            for e in raw["edges"]
        }
        for a in self.approaches.values():
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
