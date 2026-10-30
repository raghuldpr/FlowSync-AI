"use strict";
const $ = (s, r = document) => r.querySelector(s);
const NS = "http://www.w3.org/2000/svg";
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const css = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();

let token = null;
try { token = sessionStorage.getItem("fs_token"); } catch (e) { /* storage may be blocked */ }
let topo = null, data = null, tab = "events", busy = false, replaying = false, replayTimer = null, replayRuns = null;

const REASONS = {
  keep_current: "Holding green: no stronger demand on the other axis",
  higher_pressure: "Switching: the other axis has higher pressure",
  current_axis_empty: "Switching: nothing left to serve on the current axis",
  max_green_reached: "Switching: maximum green reached and the other axis is waiting",
  gridlock_rotation: "Rotating: both axes are blocked by full downstream links",
  no_fresh_data_hold: "Holding: no fresh traffic data (stale is not zero)",
  no_device_state: "Waiting for the device to report its state",
  transition_in_progress: "Clearance in progress (yellow / all-red)",
  emergency_priority: "Emergency vehicle priority",
  emergency_holding_green: "Holding green for the emergency vehicle",
  emergency_waiting_min_green: "Emergency: waiting for minimum green to elapse",
};

async function api(method, path, body) {
  const r = await fetch(path, {
    method, headers: { "Content-Type": "application/json", ...(token ? { Authorization: "Bearer " + token } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (r.status === 401) { showLogin(); throw new Error("login required"); }
  if (!r.ok) { let m = r.statusText; try { m = (await r.json()).detail || m; } catch (e) {} throw new Error(typeof m === "string" ? m : JSON.stringify(m)); }
  return r.json();
}
let toastTimer = null;
function toast(msg) {
  let t = $("#toast");
  if (!t) { t = document.createElement("div"); t.id = "toast"; t.setAttribute("role", "alert");
    t.style.cssText = "position:fixed;left:50%;bottom:18px;transform:translateX(-50%);background:var(--bad);color:#fff;padding:8px 14px;border-radius:8px;z-index:9;max-width:90vw"; document.body.appendChild(t); }
  t.textContent = msg; t.style.display = "block"; clearTimeout(toastTimer); toastTimer = setTimeout(() => { t.style.display = "none"; }, 5000);
}
// Only touch the DOM when content changed: a button replaced between mouse-down and mouse-up loses the click.
function setHtml(sel, html) { const n = $(sel); if (n._h === html) return false; n.innerHTML = html; n._h = html; return true; }
const act = async (fn) => { try { await fn(); await refresh(); } catch (e) { if (e.message !== "login required") toast(e.message); } };

document.addEventListener("click", (e) => {      // delegated: survives re-renders
  const b = e.target.closest("button"); if (!b) return;
  const d = b.dataset, post = (p, body) => act(() => api("POST", p, body));
  if (d.ack) post(`/api/operator/incidents/${d.ack}/ack`);
  else if (d.res) post(`/api/operator/incidents/${d.res}/resolve`);
  else if (d.pc) post(`/api/operator/proposals/${d.pc}/confirm`);
  else if (d.pd) post(`/api/operator/proposals/${d.pd}/decline`);
  else if (d.clear) post(`/api/operator/ev/${d.clear}/clear`);
  else if (b.id === "ev-go") post("/api/operator/ev", { vehicle_id: $("#ev-id").value, origin: $("#ev-o").value, destination: $("#ev-d").value });
  else if (b.id === "cl-close") post(`/api/operator/edges/${$("#cl-edge").value}/closed`, { closed: true });
  else if (b.id === "cl-open") post(`/api/operator/edges/${$("#cl-edge").value}/closed`, { closed: false });
  else if (d.hv) post(`/api/operator/hazards/${d.hv}/verify`);
  else if (d.hr) post(`/api/operator/hazards/${d.hr}/reject`);
  else if (d.hx) post(`/api/operator/hazards/${d.hx}/resolve`);
  else if (d.himg) showImage(b, d.himg);
  else if (b.id === "au-verify") verifyAudit();
  else if (b.id === "hz-go") post("/api/operator/hazards", { kind: $("#hz-kind").value, edge: $("#hz-edge").value, severity: $("#hz-sev").value });
  else if (b.id === "rp-play") startReplay();
  else if (b.id === "rp-stop") stopReplay();
  else if (b.id === "scn-start") post("/api/operator/scenario/start", { id: $("#scn-id").value });
  else if (b.id === "scn-stop") post("/api/operator/scenario/stop");
});

function showLogin() { $("#login").style.display = "flex"; $("#logout").hidden = true; }
$("#login-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  try {
    const r = await fetch("/api/auth/login", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: $("#u").value, password: $("#p").value }) });
    if (!r.ok) { $("#login-err").textContent = r.status === 429 ? "Too many attempts — wait a minute." : "Invalid credentials."; return; }
    token = (await r.json()).token;
    try { sessionStorage.setItem("fs_token", token); } catch (e) {}
    $("#login").style.display = "none"; $("#login-err").textContent = ""; $("#p").value = "";
    refresh();
  } catch (e) { $("#login-err").textContent = "Backend unreachable."; }
});
$("#logout").addEventListener("click", () => { token = null; try { sessionStorage.removeItem("fs_token"); } catch (e) {} showLogin(); });

/* ---------- map ---------- */
const svg = $("#map");
const el = (tag, attrs = {}, parent = svg) => { const e = document.createElementNS(NS, tag); for (const k in attrs) e.setAttribute(k, attrs[k]); parent.appendChild(e); return e; };
const geom = {};      // edge id -> {x1,y1,x2,y2}
const layers = {};

function buildMap() {
  svg.innerHTML = "";
  layers.road = el("g"); layers.route = el("g"); layers.nodes = el("g"); layers.over = el("g");
  const N = topo.nodes;
  for (const e of topo.edges) {
    const a = N[e.src], b = N[e.dst];
    const dx = b.x - a.x, dy = b.y - a.y, len = Math.hypot(dx, dy), ux = dx / len, uy = dy / len;
    const off = topo.lane_offset || 8, ox = -uy * off, oy = ux * off;
    const sa = a.kind === "intersection" ? 30 : 12, sb = b.kind === "intersection" ? 30 : 12;
    const g = { x1: a.x + ux * sa + ox, y1: a.y + uy * sa + oy, x2: b.x - ux * sb + ox, y2: b.y - uy * sb + oy, ux, uy, ox, oy };
    geom[e.id] = g;
    const line = el("line", { x1: g.x1, y1: g.y1, x2: g.x2, y2: g.y2, "stroke-width": 7, "stroke-linecap": "round", id: "edge-" + e.id }, layers.road);
    line.dataset.id = e.id;
    const mx = (g.x1 + g.x2) / 2 + ox * 1.6, my = (g.y1 + g.y2) / 2 + oy * 1.6;
    const t = el("text", { x: mx, y: my + 4, "text-anchor": "middle", "font-size": 11, id: "q-" + e.id, fill: css("--ink") }, layers.road);
    t.textContent = e.approach ? "" : "";
    const title = el("title", {}, line); title.textContent = e.id;
  }
  for (const [id, n] of Object.entries(N)) {
    const g = el("g", { transform: `translate(${n.x},${n.y})` }, layers.nodes);
    if (n.kind === "intersection") {
      el("rect", { x: -26, y: -26, width: 52, height: 52, rx: 10, fill: css("--node"), stroke: css("--line"), id: "box-" + id }, g);
      el("circle", { cx: -11, cy: -4, r: 8, class: "lamp", id: `lamp-${id}-A`, fill: css("--stale") }, g);
      el("circle", { cx: 11, cy: -4, r: 8, class: "lamp", id: `lamp-${id}-B`, fill: css("--stale") }, g);
      const a = el("text", { x: -11, y: 17, "text-anchor": "middle", "font-size": 10, fill: css("--muted") }, g); a.textContent = "A";
      const b = el("text", { x: 11, y: 17, "text-anchor": "middle", "font-size": 10, fill: css("--muted") }, g); b.textContent = "B";
      const l = el("text", { x: 0, y: -33, "text-anchor": "middle", "font-size": 12, "font-weight": 600, fill: css("--ink") }, g); l.textContent = id;
      el("text", { x: 0, y: 44, "text-anchor": "middle", "font-size": 10, id: "flag-" + id, fill: css("--warn") }, g);
    } else {
      el("circle", { r: 11, fill: css("--node"), stroke: css("--line") }, g);
      const l = el("text", { x: 0, y: -17, "text-anchor": "middle", "font-size": 11, fill: css("--muted") }, g); l.textContent = n.label.replace("the ", "");
    }
  }
}

const occColor = (f) => f < 0.34 ? css("--ok") : f < 0.67 ? css("--lamp-y") : css("--bad");
const LAMP = () => ({ R: css("--lamp-r"), Y: css("--lamp-y"), G: css("--lamp-g") });

function pointOn(edge, p) { const g = geom[edge]; return { x: g.x1 + (g.x2 - g.x1) * p, y: g.y1 + (g.y2 - g.y1) * p }; }

function drawMap(d) {
  const closed = new Set(d.closed_edges), lamp = LAMP();
  for (const e of topo.edges) {
    const line = $("#edge-" + e.id), q = $("#q-" + e.id), t = d.traffic[e.id];
    let col = css("--road"), label = "";
    if (e.approach) {
      if (!t || t.stale) { col = css("--road"); label = t ? "?" : ""; }
      else { col = occColor(t.count / (topo.capacities[e.id] || topo.capacity)); label = t.count; }
    }
    line.setAttribute("stroke", closed.has(e.id) ? css("--bad") : col);
    line.setAttribute("stroke-dasharray", closed.has(e.id) ? "3 7" : "");
    line.setAttribute("opacity", closed.has(e.id) ? 0.9 : 1);
    q.textContent = label;
  }
  for (const i of Object.keys(d.signals)) {
    const s = d.signals[i], rep = s.reported, fresh = rep && !s.reported_stale;
    for (const ax of ["A", "B"]) $(`#lamp-${i}-${ax}`).setAttribute("fill", fresh ? lamp[rep.lamps[ax]] : css("--stale"));
    $("#flag-" + i).textContent = !rep ? "NO REPORT" : s.reported_stale ? "STALE" : s.mismatch ? "MISMATCH" : "";
    $("#box-" + i).setAttribute("stroke", s.mismatch ? css("--warn") : css("--line"));
  }
  layers.route.innerHTML = ""; layers.over.innerHTML = "";
  for (const s of d.ev.filter((x) => x.status === "active")) {
    for (const eid of s.route) {
      const g = geom[eid]; if (!g) continue;
      el("line", { x1: g.x1, y1: g.y1, x2: g.x2, y2: g.y2, stroke: css("--accent"), "stroke-width": 15, opacity: 0.28, "stroke-linecap": "round" }, layers.route);
    }
    for (const ap of Object.keys(s.warnings)) {
      const g = geom[ap]; if (!g) continue;
      const p = pointOn(ap, 0.2);
      const b = el("g", { transform: `translate(${p.x + g.ox * 2.2},${p.y + g.oy * 2.2})` }, layers.over);
      el("circle", { r: 9, fill: css("--warn") }, b);
      const t = el("text", { y: 4, "text-anchor": "middle", "font-size": 12, "font-weight": 700, fill: "#fff" }, b); t.textContent = "!";
      el("title", {}, b).textContent = `Warning shown on ${ap} (${s.warnings[ap].state})`;
    }
    if (s.edge && geom[s.edge]) {
      const p = pointOn(s.edge, s.progress);
      const m = el("g", { transform: `translate(${p.x},${p.y})` }, layers.over);
      el("circle", { r: 11, fill: s.stale ? css("--stale") : css("--bad"), stroke: "#fff", "stroke-width": 2 }, m);
      const t = el("text", { y: 4, "text-anchor": "middle", "font-size": 9, "font-weight": 700, fill: "#fff" }, m); t.textContent = "EV";
      el("title", {}, m).textContent = `${s.vehicle_id} on ${s.edge}${s.stale ? " (location stale)" : ""}`;
    }
  }
  for (const h of d.hazards || []) {                       // road hazards: diamond (grey = unreviewed report)
    const g = geom[h.edge]; if (!g) continue;
    const p = pointOn(h.edge, h.progress ?? 0.35);
    const m = el("g", { transform: `translate(${p.x - g.ox * 1.2},${p.y - g.oy * 1.2})` }, layers.over);
    el("path", { d: "M0,-10 L10,0 L0,10 L-10,0 Z", fill: h.status === "open" ? css("--warn") : css("--stale"), stroke: "#fff", "stroke-width": 1.5 }, m);
    el("title", {}, m).textContent = `#${h.id} ${h.kind} on ${h.edge} (${h.status})`;
  }
  for (const inc of d.incidents) {
    const g = geom[inc.approach]; if (!g) continue;
    const p = pointOn(inc.approach, 0.62);
    const m = el("g", { transform: `translate(${p.x + g.ox * 2.4},${p.y + g.oy * 2.4})` }, layers.over);
    el("path", { d: "M0,-11 L11,9 L-11,9 Z", fill: inc.severity === "high" ? css("--bad") : css("--warn"), stroke: "#fff", "stroke-width": 1.5 }, m);
    const t = el("text", { y: 6, "text-anchor": "middle", "font-size": 10, "font-weight": 700, fill: "#fff" }, m); t.textContent = "!";
    el("title", {}, m).textContent = `#${inc.id} ${inc.type} on ${inc.approach} (${inc.status})`;
  }
}

/* ---------- panels ---------- */
const age = (iso) => { const s = Math.max(0, (Date.now() - Date.parse(iso)) / 1000); return s < 90 ? Math.round(s) + "s" : Math.round(s / 60) + "m"; };
const fmtScore = (v) => v == null ? "n/a" : Number(v).toFixed(1);

function drawInters(d) {
  const rows = Object.keys(d.signals).map((i) => {
    const s = d.signals[i], r = s.reported, dec = d.decisions[i];
    const rep = r ? `${r.phase.toLowerCase().replace("_", "-")} · axis ${r.axis} · ${(r.elapsed_ms / 1000).toFixed(0)}s${r.backend_ok ? "" : " · <span class='chip bad'>device fallback</span>"}` : "—";
    const flags = (!r ? "<span class='chip stale'>no report</span>" : s.reported_stale ? "<span class='chip stale'>stale</span>" : s.mismatch ? "<span class='chip warn'>mismatch</span>" : "<span class='chip ok'>in sync</span>");
    const sc = dec ? `A ${fmtScore(dec.inputs.scores.A)} · B ${fmtScore(dec.inputs.scores.B)}` : "";
    const why = s.commanded ? (REASONS[s.commanded.reason] || s.commanded.reason) : "";
    return `<tr><td><b>${esc(i)}</b></td><td>${rep}<br>${flags}</td>
      <td>→ axis <b>${esc(s.commanded ? s.commanded.target : "—")}</b><br><span class="muted">${esc(why)}</span><br><span class="mono muted">${esc(sc)}</span></td></tr>`;
  }).join("");
  setHtml("#inters", `<tr><th></th><th>Device reports</th><th>Backend commands</th></tr>${rows}`);
}

function chipClass(state) { return state === "redirected" ? "warn" : state.startsWith("cancelled") || state === "expired" ? "stale" : "ok"; }

function drawEV(d) {
  const nodes = Object.keys(topo.nodes).filter((n) => topo.nodes[n].kind === "external");
  const sel = (id, v) => `<select id="${id}">${nodes.map((n) => `<option value="${n}"${n === v ? " selected" : ""}>${esc(topo.nodes[n].label)}</option>`).join("")}</select>`;
  const act_ = d.ev.filter((s) => s.status === "active");
  let h = "<h2>Emergency vehicle</h2>";
  if (!act_.length) {
    h += `<div class="row"><input id="ev-id" value="EV-1" size="7" aria-label="vehicle id"> from ${sel("ev-o", "ext_W")} to ${sel("ev-d", "ext_S")}
      <button id="ev-go">Declare EV</button></div><p class="muted" style="margin:6px 0 0">The EV is localised by the overhead camera (ArUco). Priority and warnings start once its position is seen.</p>`;
  }
  for (const s of act_) {
    const st = s.edge == null ? "<span class='chip warn'>locating…</span>" : s.stale ? "<span class='chip bad'>location stale — priority withdrawn</span>" : `<span class="chip ok">tracking</span>`;
    const w = Object.entries(s.warnings).map(([a, x]) => `<span class="chip ${chipClass(x.state)}">${esc(a)} · ${esc(x.state)} · ${esc(x.band)}</span>`).join("") || "<span class='muted'>none live</span>";
    h += `<div class="row"><b>${esc(s.vehicle_id)}</b> ${st}<span class="spacer" style="flex:1"></span><button class="danger" data-clear="${s.id}">Clear</button></div>
      <div class="muted">on ${esc(s.edge || "—")} ${s.edge ? "(" + Math.round(s.progress * 100) + "%)" : ""} · reroutes: ${s.route_version}</div>
      <div class="mono" style="margin:4px 0">${s.route.map(esc).join(" → ")}</div>
      <div><span class="muted">Driver warnings:</span> ${w}</div>`;
  }
  const ring = topo.edges.filter((e) => e.approach).map((e) => e.id);
  const oc = new Set(d.operator_closed_edges);
  h += `<hr style="border:0;border-top:1px solid var(--line);margin:10px 0"><div class="row"><span class="muted">Road closure</span>
    <select id="cl-edge">${ring.map((e) => `<option value="${e}">${e}${oc.has(e) ? " (closed)" : ""}</option>`).join("")}</select>
    <button class="ghost" id="cl-close">Close</button><button class="ghost" id="cl-open">Reopen</button></div>`;
  const keepEv = { id: $("#ev-id")?.value, o: $("#ev-o")?.value, d: $("#ev-d")?.value, c: $("#cl-edge")?.value };
  if (setHtml("#ev-panel", h)) {          // restore what the operator was typing/selecting
    if ($("#ev-id") && keepEv.id) { $("#ev-id").value = keepEv.id; $("#ev-o").value = keepEv.o; $("#ev-d").value = keepEv.d; }
    if (keepEv.c) $("#cl-edge").value = keepEv.c;
  }
}

function pathStr(p) { return p ? p.join(" → ") : "none"; }
function drawIncidents(d) {
  const a = $("#alerts"); a.textContent = d.alerts_unacknowledged;
  a.className = "alert-badge" + (d.alerts_unacknowledged ? " pulse" : " zero");
  if (!d.incidents.length) { setHtml("#incidents", "<p class='muted'>No active incidents.</p>"); return; }
  setHtml("#incidents", d.incidents.map((i) => {
    const im = i.impact, rer = im ? im.pairs.filter((p) => p.rerouted) : [];
    const impact = im ? `<div class="muted">Affects ${esc(im.affected_intersections.join(", "))} · ${im.routes_affected} route(s) · worst added delay <b>+${im.max_added_delay_s}s</b>${im.unreachable_pairs.length ? " · <span style='color:var(--bad)'>unreachable: " + esc(im.unreachable_pairs.join(", ")) + "</span>" : ""}</div>
      ${rer.slice(0, 2).map((p) => `<div class="mono">${esc(p.from.replace("ext_", ""))}→${esc(p.to.replace("ext_", ""))}: ${esc(pathStr(p.alt_path))} (+${p.added_delay_s}s)</div>`).join("")}` : "";
    return `<div class="incident ${esc(i.severity)}"><div class="row"><b>#${i.id} ${esc(i.type)}</b> on ${esc(i.approach)}
      <span class="chip ${i.status === "open" ? "bad" : "warn"}">${esc(i.status)}</span><span class="muted">${esc(i.source)} · <span data-age="${esc(i.detected_at)}"></span> ago</span></div>
      <div class="mono muted">${esc(JSON.stringify(i.details))}</div>${impact}
      <div class="row" style="margin-top:6px">${i.status === "open" ? `<button data-ack="${i.id}">Acknowledge</button>` : ""}<button class="ghost" data-res="${i.id}">Resolve</button></div></div>`;
  }).join(""));
  document.querySelectorAll("[data-age]").forEach((n) => { n.textContent = age(n.dataset.age); });   // ticking text, no re-render
}

const SEV = { info: "", warning: "warn", error: "bad", critical: "bad" };
async function drawTab(d) {
  const body = $("#tab-body");
  if (tab !== "replay") body._h = null;                 // the replay tab uses setHtml's cache; other tabs write directly
  if (tab === "replay") {
    if (!replayRuns) { try { replayRuns = await api("GET", "/api/operator/replays"); } catch (e) { replayRuns = []; } }
    const opts = replayRuns.map((r) => `<option value="${esc(r.run_id)}">${esc(r.title)} · ${esc(r.started_at.slice(11, 19))} · ${r.frames} frames</option>`).join("") || "<option value=''>no recordings yet</option>";
    setHtml("#tab-body", `<p class="muted" style="margin:0 0 6px">Every scenario run is recorded second by second. Replay one on the map: demo insurance, and a way to review what happened.</p>
      <div class="row"><select id="rp-run" aria-label="Recorded run" style="flex:1;min-width:0">${opts}</select>
      <select id="rp-speed" aria-label="Speed"><option value="1">1×</option><option value="2">2×</option><option value="4" selected>4×</option></select>
      <button id="rp-play">▶ Replay</button><button class="ghost" id="rp-stop">■ Stop</button></div><div id="rp-info" style="margin-top:8px"></div>`);
    return;
  }
  if (tab === "events") {
    body.innerHTML = `<table>${d.events.map((e) => `<tr><td class="mono muted">${esc(e.ts.slice(11, 19))}</td><td><span class="chip ${SEV[e.severity] || ""}">${esc(e.severity)}</span></td><td>${esc(e.type)}</td><td class="mono muted">${esc((e.payload || "").slice(0, 110))}</td></tr>`).join("")}</table>`;
  } else if (tab === "wlog") {
    body.innerHTML = `<table><tr><th>time</th><th>approach</th><th>transition</th><th>reason</th></tr>${d.warning_events.map((e) => `<tr><td class="mono muted">${esc(e.ts.slice(11, 19))}</td><td>${esc(e.approach)}</td><td>${esc(e.from_state || "·")} → <span class="chip ${chipClass(e.to_state)}">${esc(e.to_state)}</span></td><td class="muted">${esc(e.reason)}</td></tr>`).join("") || "<tr><td class='muted'>No warnings yet.</td></tr>"}</table>`;
  } else if (tab === "audit") {
    const rows = await api("GET", "/api/operator/audit?limit=60");
    const html = auditVerifyBar() + `<table><tr><th>time</th><th>actor</th><th>role</th><th>action</th><th>result</th></tr>${rows.map((r) => `<tr><td class="mono muted">${esc(r.ts.slice(11, 19))}</td><td>${esc(r.actor)}</td><td>${esc(r.role)}</td><td>${esc(r.action)} ${esc(r.target || "")}</td><td><span class="chip ${r.result === "ok" ? "ok" : "bad"}">${esc(r.result)}</span></td></tr>`).join("")}</table>`;
    if (body._ah !== html || !body.querySelector("#au-verify")) { body.innerHTML = html; body._ah = html; }   // redraw only on change: a button replaced mid-click loses the click
  } else {
    body.innerHTML = provenanceTable(d.provenance);
  }
}

/* ---------- audit chain ---------- */
let auditCheck = null;                                   // last verify result; the tab is redrawn every second, so it lives here
async function verifyAudit() {
  try { auditCheck = { ...(await api("GET", "/api/operator/audit/verify")), at: new Date() }; }
  catch (e) { if (e.message !== "login required") toast(e.message); return; }
  if (data) drawTab(data);
}
const BROKEN = { row_modified: "this row was edited", broken_link: "a row before this one was deleted or inserted",
  row_without_hash: "this row was added outside the audit writer", tail_missing: "the newest rows were deleted" };
function auditVerifyBar() {
  const c = auditCheck, t = c ? c.at.toTimeString().slice(0, 8) : "";
  const res = !c ? `<span class="muted">Every row stores a hash of the row before it. Editing or deleting any row breaks the chain.</span>`
    : c.ok ? `<span class="chip ok">chain intact</span> <span class="muted">${c.rows} rows · head ${esc(c.head)}… · checked ${t}</span>`
    : `<span class="chip bad">chain broken at row ${esc(c.broken_at)}</span> <span class="muted">${esc(BROKEN[c.reason] || c.reason)} · checked ${t}</span>`;
  return `<div class="row" style="margin-bottom:8px"><button id="au-verify">Verify chain</button>${res}</div>`;
}

/* ---------- road conditions ---------- */
async function showImage(btn, id) {                      // images are operator-only: fetch with the token, show as a blob
  try {
    const r = await fetch(`/api/operator/images/${id}`, { headers: { Authorization: "Bearer " + token } });
    if (!r.ok) throw new Error("image unavailable");
    const img = document.createElement("img");
    img.src = URL.createObjectURL(await r.blob()); img.alt = "road camera frame"; img.style.cssText = "max-width:100%;border-radius:6px;margin-top:6px;display:block";
    btn.replaceWith(img);
  } catch (e) { toast(e.message); }
}
function drawHazards(d) {
  const hz = d.hazards || [], pending = hz.filter((h) => h.status === "reported").length;
  const c = $("#hz-count"); c.textContent = hz.length ? `${hz.length}${pending ? " · " + pending + " to review" : ""}` : "0"; c.className = "chip " + (pending ? "bad" : hz.length ? "warn" : "stale");
  setHtml("#hz-edge", topo.edges.map((e) => `<option>${e.id}</option>`).join(""));
  if (!hz.length) { setHtml("#hazards", "<p class='muted' style='margin:0'>No active road hazards.</p>"); return; }
  setHtml("#hazards", hz.map((h) => `<div class="incident ${h.status === "open" ? "medium" : ""}" style="${h.status === "reported" ? "border-left:4px solid var(--stale)" : ""}">
    <div class="row"><b>#${h.id} ${esc(h.kind)}</b> on ${esc(h.edge)} <span class="chip ${h.severity === "high" ? "bad" : h.severity === "medium" ? "warn" : ""}">${esc(h.severity)}</span>
      <span class="chip ${h.status === "open" ? "ok" : "stale"}">${h.status === "reported" ? "unreviewed" : esc(h.status)}</span>
      <span class="muted">${esc(h.source)}${h.confidence != null ? " · " + Math.round(h.confidence * 100) + "% confidence (heuristic)" : ""}</span></div>
    ${h.image_id ? `<button class="ghost" data-himg="${esc(h.image_id)}">Show camera frame</button>` : ""}
    <div class="row" style="margin-top:6px">${h.status === "reported" ? `<button data-hv="${h.id}">Verify</button><button class="ghost" data-hr="${h.id}">Reject</button>` : `<button class="ghost" data-hx="${h.id}">Resolve</button>`}</div></div>`).join(""));
}

/* ---------- replay ---------- */
function stopReplay() {
  replaying = false; clearInterval(replayTimer);
  const i = $("#rp-info"); if (i) i.textContent = "Stopped. The map shows live data again.";
}
async function startReplay() {
  const id = $("#rp-run").value; if (!id) return;
  stopReplay();
  const rec = await api("GET", `/api/operator/replays/${id}`);
  const speed = Number($("#rp-speed").value), frames = rec.frames; let i = 0;
  replaying = true;
  replayTimer = setInterval(() => {
    const f = frames[Math.min(i, frames.length - 1)];
    drawMap({ traffic: f.traffic, signals: f.signals, ev: f.ev, incidents: f.incidents, hazards: f.hazards, closed_edges: f.closed_edges });
    const info = $("#rp-info");
    if (info) info.innerHTML = `<b>REPLAY</b> · ${esc(rec.run.title)} · t=${f.t}s of ${frames[frames.length - 1].t}s · road users ${f.road_users}<br>${f.step ? `<b>${esc(f.step.title)}</b><div>${esc(f.step.narration)}</div>` : ""}<div class="mono muted">${f.events.map(esc).join("<br>")}</div>`;
    if (++i >= frames.length) { clearInterval(replayTimer); replaying = false; }
  }, 1000 / speed);
}

const PROV_CLASS = { Built: "ok", Simulated: "warn", Replay: "warn", "Not connected": "bad", Idle: "stale", "Not built": "stale" };
function provenanceTable(rows) {
  return `<p class="muted" style="margin:0 0 6px">Derived live from what each data path reports about itself.</p><table><tr><th>Component</th><th>Status</th><th>What it is right now</th></tr>` +
    rows.map((r) => `<tr><td>${esc(r.component)}</td><td><span class="chip ${PROV_CLASS[r.status] || "stale"}">${esc(r.status)}</span></td><td class="muted">${esc(r.detail)}</td></tr>`).join("") + "</table>";
}

/* ---------- scenario panel ---------- */
const STEP_ICON = (s) => !s.expect ? (s.state === "done" ? "•" : "○") : s.expect.ok === true ? "✓" : s.expect.ok === false ? "✗" : s.state === "done" ? "…" : "○";
function drawScenario(d) {
  const s = d.scenario;
  setHtml("#scn-id", s.scenarios.map((x) => `<option value="${esc(x.id)}">${x.kind === "drill" ? "Drill: " : ""}${esc(x.title)}</option>`).join(""));
  const st = $("#scn-state"), w = $("#scn-world");
  st.textContent = s.state; st.className = "chip " + ({ running: "ok", finished: "ok", stopped: "warn", idle: "stale" }[s.state] || "stale");
  w.textContent = s.world === "simulator" ? "world: simulator" : "world: physical table (follow the cues)";
  w.className = "chip " + (s.world === "simulator" ? "warn" : "ok");
  $("#scn-start").disabled = s.state === "running"; $("#scn-stop").disabled = s.state !== "running";
  if (s.state === "idle") { setHtml("#scn-body", `<p class="muted" style="margin:6px 0 0">Pick a scenario and press Start. It runs on the same APIs and safety rules as live traffic; each step checks itself.</p>`); return; }
  const pct = Math.min(100, Math.round(100 * s.t / s.duration));
  const cur = s.current;
  const sum = s.summary ? `<div class="row" style="margin-top:6px">${Object.entries({ "checks": `${s.summary.checks_passed}/${s.summary.checks_total}`, "EV travel (simulated s)": s.summary.ev_travel_s, "reroutes": s.summary.reroutes,
    "priority decisions": s.summary.emergency_priority_decisions, "incidents": s.summary.incidents_opened }).filter(([, v]) => v !== undefined && v !== null).map(([k, v]) => `<span class="chip ok">${esc(k)}: <b>${esc(v)}</b></span>`).join("")}</div>` : "";
  setHtml("#scn-body", `<div style="height:6px;background:var(--chip);border-radius:3px;margin:8px 0"><div style="height:6px;width:${pct}%;background:var(--accent);border-radius:3px"></div></div>
    <div class="muted mono">${s.t}s / ${s.duration}s · started by ${esc(s.started_by)}</div>
    ${cur ? `<div style="margin:6px 0"><b>${esc(cur.title)}</b><div>${esc(cur.narration)}</div>${s.world === "physical" && cur.cue ? `<div style="color:var(--warn)">▶ On the table: ${esc(cur.cue)}</div>` : ""}</div>` : ""}
    <table>${s.steps.map((x) => `<tr style="opacity:${x.state === "pending" ? 0.5 : 1}"><td class="mono">${x.at}s</td><td>${STEP_ICON(x)}</td><td>${esc(x.title)}${x.expect ? `<div class="muted" style="font-size:12px">${esc(x.expect.desc)}${x.expect.detail ? " — " + esc(x.expect.detail) : ""}</div>` : ""}</td></tr>`).join("")}</table>${sum}`);
}
document.querySelectorAll("#tabs button").forEach((b) => b.addEventListener("click", () => {
  tab = b.dataset.tab; document.querySelectorAll("#tabs button").forEach((x) => x.classList.toggle("on", x === b)); if (data) drawTab(data);
}));
$("#inc-go").addEventListener("click", () => act(() => api("POST", "/api/operator/incidents", { type: $("#inc-type").value, approach: $("#inc-approach").value })));

function drawAssistant(d) {
  const s = d.llm, el_ = $("#llm-status");
  el_.textContent = s.enabled ? "LLM: " + s.model : s.requested ? "LLM not configured — fallback" : "LLM off — deterministic fallback";
  el_.className = "chip " + (s.enabled ? "ok" : "warn");
  if (!d.proposals.length) return;
  const cls = { pending: "warn", confirmed: "ok", rejected: "bad", declined: "stale", expired: "stale" };
  setHtml("#proposals", d.proposals.map((p) => `<div class="incident" style="border-left:4px solid var(--line)">
    <div class="row"><b>#${p.id} ${esc(p.intersection)} → axis ${esc(p.target)}</b> for ${p.duration_s}s
      <span class="chip ${cls[p.status] || ""}">${esc(p.status)}</span><span class="muted">${esc(p.via)}</span></div>
    <div class="muted">${esc(p.explanation)}</div>${p.rationale ? `<div class="mono muted">${esc(p.rationale)}</div>` : ""}
    ${p.status === "pending" ? `<div class="row" style="margin-top:6px"><button data-pc="${p.id}">Confirm</button><button class="ghost" data-pd="${p.id}">Decline</button></div>` : ""}</div>`).join(""));
}

/* ---------- assistant trace ---------- */
const SRC_CLS = { fallback: "warn", stand_in: "stale", provider: "ok" };
const VERDICT_CLS = { pending: "warn", rejected: "bad", confirmed: "ok", declined: "stale", expired: "stale" };
function traceHtml(t) {
  const steps = t.steps.map((s) => `<div class="row" style="align-items:baseline"><span class="chip ${s.ok ? "" : "bad"}">${esc(s.tool)}</span>
    <span class="mono muted">${esc(JSON.stringify(s.args))}</span></div><div class="muted" style="margin:0 0 4px 8px">${esc(s.result)}</div>`).join("")
    || `<div class="muted">No tools were read.</div>`;
  const props = t.proposals.map((p) => `<div class="row"><b>Proposal #${p.id}: ${esc(p.intersection)} → axis ${esc(p.axis)} for ${p.duration_s}s</b>
    <span class="chip ${VERDICT_CLS[p.validator_verdict] || ""}">validator: ${esc(p.validator_verdict)}</span><span class="muted">${esc(p.validator_reason)}</span></div>
    <div class="muted" style="margin-left:8px">${esc(p.note)}</div>`).join("");
  return `<details class="incident" style="border-left:4px solid var(--line)"><summary><span class="mono muted">${esc(t.ts.slice(11, 19))}</span>
    <b>${esc(t.question)}</b> <span class="chip ${SRC_CLS[t.source.kind] || ""}">${esc(t.source.label)}${t.source.model ? " · " + esc(t.source.model) : ""}${t.fallback_reason ? " · " + esc(t.fallback_reason) : ""}</span>
    ${t.proposals.length ? `<span class="chip warn">${t.proposals.length} proposal</span>` : ""}</summary>
    <div style="margin-top:6px"><div class="muted">1. Question asked by ${esc(t.actor)}</div><div class="muted">2. Read-only tools</div>${steps}
    <div class="muted" style="margin-top:4px">3. Proposal and validator</div>${props || `<div class="muted" style="margin-left:8px">None. The assistant cannot change anything itself.</div>`}</div></details>`;
}
let traceBusy = false;
async function pollTraces() {
  if (traceBusy || !token) return;
  traceBusy = true;
  try {
    const rows = await api("GET", "/api/operator/llm/traces?limit=8");
    $("#trace-n").textContent = String(rows.length);
    if (rows.length) setHtml("#traces", rows.map(traceHtml).join(""));
  } catch (e) { /* not signed in, or the panel is not available: leave the last trace on screen */ }
  traceBusy = false;
}
setInterval(pollTraces, 2000);

$("#ask-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const q = $("#ask-q").value.trim(); if (!q) return;
  const out = $("#ask-out"), go = $("#ask-go");
  go.disabled = true; out.innerHTML = "<span class='muted'>Thinking…</span>";
  try {
    const r = await api("POST", "/api/llm/query", { question: q });
    const tools = r.tool_calls.map((c) => `<span class="chip ${c.ok ? "" : "bad"}">${esc(c.name)}</span>`).join("");
    out.innerHTML = `<div class="row"><span class="chip ${r.mode === "llm" ? "ok" : "warn"}">${r.mode === "llm" ? "LLM · " + esc(r.model) : "DETERMINISTIC FALLBACK" + (r.fallback_reason ? " · " + esc(r.fallback_reason) : "")}</span>${tools}</div>
      <div style="white-space:pre-wrap;margin-top:6px">${esc(r.answer)}</div>
      ${r.proposals.length ? "<div class='muted' style='margin-top:6px'>Proposal(s) queued below — they act only after you confirm.</div>" : ""}`;
    refresh(); pollTraces();
  } catch (e) { out.innerHTML = `<span style="color:var(--bad)">${esc(e.message)}</span>`; }
  go.disabled = false;
});

function drawHeader(d) {
  const srcs = [...new Set(Object.values(d.traffic).map((t) => t.source))];
  const m = $("#mode");
  m.textContent = srcs.length ? (srcs.includes("simulated") ? "SIMULATED DATA" : "LIVE: " + srcs.join(", ")) : "no traffic data";
  m.className = "chip " + (srcs.includes("simulated") ? "warn" : srcs.length ? "ok" : "stale");
  const ru = d.road_users, warned = ru.filter((u) => Object.values(u.alerts).some((s) => ["active", "updated", "redirected"].includes(s))).length;
  setHtml("#roadusers", `<a class="chip ${ru.length ? "ok" : "stale"}" href="driver.html" target="_blank" rel="noopener" title="Open the road-user page (share this link / QR with the audience)">road users: ${ru.length}${ru.length ? ` · alerted: ${warned}` : ""}</a>`);
  $("#devices").innerHTML = d.devices.map((x) => `<span class="chip ${x.age_s == null ? "stale" : x.age_s > 5 ? "warn" : "ok"}" title="${esc(x.role)}">${esc(x.device_id)} ${x.age_s == null ? "never" : x.age_s + "s"}</span>`).join("");
}

async function refresh() {
  if (busy || !token) return;
  busy = true;
  try {
    if (!topo) {
      topo = await (await fetch("/api/public/topology")).json(); buildMap();
      $("#inc-approach").innerHTML = topo.edges.filter((e) => e.approach).map((e) => `<option>${e.id}</option>`).join("");
    }
    data = await api("GET", "/api/operator/overview");
    $("#logout").hidden = false;
    drawHeader(data); drawScenario(data); drawHazards(data); drawAssistant(data); if (!replaying) drawMap(data); drawInters(data); drawEV(data); drawIncidents(data); await drawTab(data);
  } catch (e) { if (e.message !== "login required") $("#mode").textContent = "backend unreachable"; }
  finally { busy = false; }
}
if (!token) showLogin();
refresh(); setInterval(refresh, 1000);
