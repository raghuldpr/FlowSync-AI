"""Deterministic answers built from the same read tools, used whenever the LLM is off, unreachable,
slow or returns something invalid. The system never needs a model to explain itself."""
import re

from . import tools as T

REASON_TEXT = {
    "keep_current": "holding green (no stronger demand opposite)",
    "higher_pressure": "switching because the other axis has higher pressure",
    "current_axis_empty": "switching because nothing is left to serve on the current axis",
    "max_green_reached": "switching because maximum green was reached",
    "gridlock_rotation": "rotating because both axes are blocked by full downstream links",
    "no_fresh_data_hold": "holding because there is no fresh traffic data",
    "no_device_state": "waiting for the device to report its state",
    "transition_in_progress": "completing yellow/all-red clearance",
    "emergency_priority": "giving emergency vehicle priority",
    "emergency_holding_green": "holding green for the emergency vehicle",
    "emergency_waiting_min_green": "waiting for minimum green before serving the emergency vehicle",
    "operator_request": "serving a confirmed operator request",
    "operator_request_holding": "holding green for a confirmed operator request",
    "operator_request_waiting_min_green": "waiting for minimum green before an operator request",
}


def _run(tc, name, **args):
    return T.TOOLS[name].handler(tc, args)


def traffic_section(tc) -> str:
    d = _run(tc, "get_traffic_state")["approaches"]
    if not d:
        return "Traffic: no data received yet."
    stale = [a for a, v in d.items() if v["stale"]]
    busy = sorted((a for a, v in d.items() if not v["stale"]), key=lambda a: -d[a]["vehicles"])[:3]
    parts = [f"{a} {d[a]['vehicles']} vehicles ({round(d[a]['occupancy'] * 100)}% full)" for a in busy]
    s = "Traffic: busiest approaches — " + "; ".join(parts) + "." if parts else "Traffic: all readings are stale."
    if stale:
        s += f" Stale (not trusted): {', '.join(stale)}."
    return s


def signals_section(tc) -> str:
    out = []
    for i, v in _run(tc, "get_signal_states").items():
        if v["report_stale_or_missing"]:
            out.append(f"{i}: no fresh device report")
            continue
        r, c = v["reported"], v["controller"]
        txt = f"{i}: axis {r['axis']} {r['phase'].lower().replace('_', '-')}"
        if c:
            txt += f", controller {REASON_TEXT.get(c['reason'], c['reason'])}"
            sc = c["pressure_scores"]
            txt += f" (pressure A {sc['A'] if sc['A'] is not None else 'n/a'}, B {sc['B'] if sc['B'] is not None else 'n/a'})"
        out.append(txt)
    return "Signals: " + "; ".join(out) + "."


def incidents_section(tc) -> str:
    inc = _run(tc, "get_active_incidents")
    if not inc:
        return "Incidents: none active."
    lines = []
    for i in inc:
        im = _run(tc, "get_incident_impact", incident_id=i["id"])["impact"]
        line = (f"#{i['id']} {i['type']} on {i['approach']} ({i['status']}, {i['source']}): affects "
                f"{', '.join(im['affected_intersections'])}, {im['routes_affected']} route(s), worst added delay "
                f"+{im['max_added_delay_s']}s")
        if im["unreachable_pairs"]:
            line += f", unreachable: {', '.join(im['unreachable_pairs'])}"
        rer = [p for p in im["pairs"] if p["rerouted"]]
        if rer:
            line += f"; alternative {' → '.join(rer[0]['alt_path'])}"
        lines.append(line)
    return "Incidents: " + " | ".join(lines) + "."


def ev_section(tc) -> str:
    evs = _run(tc, "get_active_warnings")["active_emergency_vehicles"]
    if not evs:
        return "Emergency vehicles: none active."
    out = []
    for e in evs:
        w = ", ".join(f"{a} ({v['state']})" for a, v in e["live_warnings"].items()) or "none"
        out.append(f"{e['vehicle']} is {e['state'].replace('_', ' ')} on route {' → '.join(e['route'])} "
                   f"({e['reroutes']} reroute(s)); warnings live on: {w}")
    sess = tc.ctx.ev.snapshot()
    act = next((s for s in sess if s["status"] == "active"), None)
    s = "Emergency vehicles: " + " | ".join(out) + "."
    if act:
        alt = _run(tc, "get_route_alternatives", origin=act["origin"], destination=act["destination"])
        if "routes" in alt:
            s += " Route options now: " + "; ".join(
                f"{' → '.join(r['path'])} ({r['cost_s']}s)" for r in alt["routes"]) + "."
    return s


def answer(ctx, question: str, caller: dict) -> str:
    tc = T.ToolCtx(ctx, caller, write_allowed=False)
    q = question.lower()
    sections = []
    if re.search(r"\b(ev|emergency|ambulance|route|reroute|warning)", q):
        sections = [ev_section, incidents_section]
    elif re.search(r"incident|stall|accident|alert|breakdown|broke", q):
        sections = [incidents_section, traffic_section]
    elif re.search(r"signal|phase|green|light|why", q):
        sections = [signals_section, traffic_section]
    elif re.search(r"congest|traffic|queue|busy|jam", q):
        sections = [traffic_section, signals_section, incidents_section]
    else:
        sections = [traffic_section, signals_section, incidents_section, ev_section]
    text = "\n".join(s(tc) for s in sections)
    if re.search(r"\b(propose|adjust|change|extend|switch|prioriti[sz]e|request)\b", q):
        text += "\nSignal changes cannot be proposed without the LLM layer; operators can declare an EV or close a road from the panels."
    return text
