"""Deterministic pressure-inspired signal controller.

Per intersection, per cycle:
  stale data -> unknown (never a fresh zero) -> conflicts impossible by FSM -> min/max green
  -> emergency priority (through the same validator) -> highest pressure, with downstream spillback.
Score for an axis = sum over its approaches of max(0, Q_up - max Q_down); an approach whose
downstream link is at/above the spillback occupancy contributes 0. If a downstream reading is stale
the approach is skipped (downstream pressure is required, not optional).
"""
import json

from ..db import now_iso
from .fsm import GREEN, other
from .validator import check_switch, validate_command


class Controller:
    def __init__(self, topology, store, db, intersection_device: dict):
        self.topo = topology
        self.store = store
        self.db = db
        self.intersection_device = intersection_device     # 'I1' -> 'intersection-1'
        self.extra_emergency = lambda: set()               # EV manager plugs in tracked-EV approaches
        self.operator_requests = {}                        # intersection -> {target, until, proposal_id}

    def emergency_approaches(self) -> set:
        rows = self.db.query("SELECT approach FROM emergency_requests WHERE status='active'")
        return {r["approach"] for r in rows} | set(self.extra_emergency())

    def _active_request(self, inter: str):
        req = self.operator_requests.get(inter)
        if req and self.store.clock() >= req["until"]:
            del self.operator_requests[inter]
            req = None
        return req

    # ---- scoring ------------------------------------------------------------------------------
    def axis_score(self, inter: str, axis: str, queues: dict):
        """Returns (score or None if no usable inputs, notes list)."""
        c = self.topo.control
        total, used, notes, blocked = 0.0, False, [], False
        for a in self.topo.approaches_of(inter, axis):
            q = queues.get(a.id)
            if q is None:
                notes.append(f"{a.id}:stale")
                continue
            downs = [queues.get(d) for d in a.downstream]
            if any(d is None for d in downs):
                notes.append(f"{a.id}:downstream_stale")
                continue
            used = True
            if any(d / c.link_capacity >= c.spillback_occupancy for d in downs):
                notes.append(f"{a.id}:spillback")
                blocked = blocked or q > 0
                continue
            total += max(0.0, q - (max(downs) if downs else 0))
        return (total if used else None), notes, blocked

    # ---- decision -----------------------------------------------------------------------------
    def decide(self, inter: str, queues: dict, emergency_approaches: set) -> dict:
        c, tm = self.topo.control, self.topo.timing
        scores, notes, blocked = {}, [], {}
        for ax in ("A", "B"):
            scores[ax], n, blocked[ax] = self.axis_score(inter, ax, queues)
            notes += n
        # gridlock = both axes have waiting vehicles that spillback is holding back
        gridlock = all(blocked[ax] and not scores[ax] for ax in ("A", "B"))
        inputs = {"scores": scores, "notes": notes}
        act = self.store.get_actual(inter, c.stale_after_s)
        ev_axes = sorted({self.topo.approaches[a].movement for a in emergency_approaches
                          if a in self.topo.approaches and self.topo.approaches[a].intersection == inter})
        s = lambda ax: scores[ax] or 0.0

        if act is None:
            if ev_axes:
                return {"target": ev_axes[0], "reason": "emergency_priority", "inputs": inputs}
            tgt = "A" if s("A") >= s("B") else "B"
            return {"target": tgt, "reason": "no_device_state", "inputs": inputs}

        if act["phase"] != GREEN:
            req = self._active_request(inter)
            tgt = ev_axes[0] if ev_axes else (req["target"] if req else act["next_axis"])
            return {"target": tgt, "reason": "transition_in_progress", "inputs": inputs}

        g, el = act["axis"], act["elapsed_ms"]
        if ev_axes:
            tgt = ev_axes[0]
            if tgt == g:
                return {"target": g, "reason": "emergency_holding_green", "inputs": inputs}
            ok, why = check_switch(tm, g, el, tgt)
            return {"target": tgt if ok else g,
                    "reason": "emergency_priority" if ok else "emergency_waiting_min_green", "inputs": inputs}

        req = self._active_request(inter)       # confirmed operator request (emergency priority outranks it)
        if req:
            if req["target"] == g:
                return {"target": g, "reason": "operator_request_holding", "inputs": inputs}
            ok, _ = check_switch(tm, g, el, req["target"])
            return {"target": req["target"] if ok else g,
                    "reason": "operator_request" if ok else "operator_request_waiting_min_green", "inputs": inputs}

        if scores["A"] is None and scores["B"] is None:
            return {"target": g, "reason": "no_fresh_data_hold", "inputs": inputs}

        o = other(g)
        switch_ok, _ = check_switch(tm, g, el, o)
        if gridlock and el >= tm.max_green_ms:
            # every axis is blocked: spillback would starve both, so rotate to bound green/red times
            return {"target": o, "reason": "gridlock_rotation", "inputs": inputs}
        if el >= tm.max_green_ms and s(o) > 0:
            return {"target": o, "reason": "max_green_reached", "inputs": inputs}
        if switch_ok and s(g) == 0 and s(o) > 0:
            return {"target": o, "reason": "current_axis_empty", "inputs": inputs}
        if switch_ok and s(o) > s(g) + c.switch_margin:
            return {"target": o, "reason": "higher_pressure", "inputs": inputs}
        return {"target": g, "reason": "keep_current", "inputs": inputs}

    # ---- cycle --------------------------------------------------------------------------------
    def cycle(self) -> list:
        c, tm = self.topo.control, self.topo.timing
        queues = self.store.fresh_traffic(c.stale_after_s)
        emergencies = self.emergency_approaches()
        decisions = []
        for inter in self.topo.intersections:
            d = self.decide(inter, queues, emergencies)
            cmd = {"intersection": inter, "target": d["target"],
                   "valid_for_ms": tm.command_valid_ms, "reason": d["reason"]}
            ok, why = validate_command(self.topo, cmd)
            if not ok:
                self.db.execute("INSERT INTO system_events(ts,type,source,severity,payload) VALUES(?,?,?,?,?)",
                                (now_iso(), "command_rejected_by_validator", "controller", "error",
                                 json.dumps({"cmd": cmd, "why": why})))
                continue
            device = self.intersection_device.get(inter)
            if device is None:
                continue
            cmd["seq"] = self.store.next_seq(device)
            self.store.put_command(device, cmd)
            key = (d["target"], d["reason"])
            if self.store.last_decision.get(inter) != key:
                prev_target = (self.store.last_decision.get(inter) or (None,))[0]
                self.store.last_decision[inter] = key
                self.db.execute("INSERT INTO signal_decisions(ts,intersection,target,reason,inputs) VALUES(?,?,?,?,?)",
                                (now_iso(), inter, d["target"], d["reason"], json.dumps(d["inputs"])))
                if prev_target != d["target"]:
                    self.db.execute(
                        "INSERT INTO device_commands(device_id,seq,intersection,target,reason,issued_at) VALUES(?,?,?,?,?,?)",
                        (device, cmd["seq"], inter, d["target"], d["reason"], now_iso()))
            self.store.last_full[inter] = {"target": d["target"], "reason": d["reason"], "inputs": d["inputs"]}
            decisions.append({**d, "intersection": inter})
        return decisions
