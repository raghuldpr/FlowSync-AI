"use strict";
const $ = (s) => document.querySelector(s);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const NS = "http://www.w3.org/2000/svg";
let topo = null, token = null, version = -1, running = false;
try { token = sessionStorage.getItem("fs_driver"); } catch (e) { /* storage may be blocked */ }

const LIVE = ["active", "updated", "redirected"];
const LABEL = { ev: "Emergency vehicle", incident: "Incident ahead", hazard: "Road hazard", route: "Route update" };

function drawMap(v) {
  const svg = $("#map"); svg.innerHTML = "";
  const el = (t, a, p = svg) => { const e = document.createElementNS(NS, t); for (const k in a) e.setAttribute(k, a[k]); p.appendChild(e); return e; };
  const g = {}, off = topo.lane_offset || 14, N = topo.nodes;
  for (const e of topo.edges) {
    const a = N[e.src], b = N[e.dst], dx = b.x - a.x, dy = b.y - a.y, L = Math.hypot(dx, dy), ux = dx / L, uy = dy / L;
    const ox = -uy * off, oy = ux * off, sa = a.kind === "intersection" ? 30 : 12, sb = b.kind === "intersection" ? 30 : 12;
    g[e.id] = { x1: a.x + ux * sa + ox, y1: a.y + uy * sa + oy, x2: b.x - ux * sb + ox, y2: b.y - uy * sb + oy };
    el("line", { x1: g[e.id].x1, y1: g[e.id].y1, x2: g[e.id].x2, y2: g[e.id].y2, stroke: css("--road"), "stroke-width": 8, "stroke-linecap": "round" });
  }
  for (const [id, n] of Object.entries(N)) {
    if (n.kind === "intersection") el("circle", { cx: n.x, cy: n.y, r: 30, fill: css("--node"), stroke: css("--line") });
    else { el("circle", { cx: n.x, cy: n.y, r: 12, fill: css("--node"), stroke: css("--line") }); const t = el("text", { x: n.x, y: n.y - 18, "text-anchor": "middle", "font-size": 18, fill: css("--muted") }); t.textContent = n.label.replace("the ", ""); }
  }
  const e = g[v.edge];
  if (e) el("circle", { cx: e.x1 + (e.x2 - e.x1) * v.progress, cy: e.y1 + (e.y2 - e.y1) * v.progress, r: 15, fill: css("--accent"), stroke: "#fff", "stroke-width": 3 });
}
const css = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();

function render(v) {
  version = v.version;
  $("#me").textContent = v.id;
  const st = $("#status"); st.textContent = v.status === "arrived" ? "arrived" : "driving"; st.className = "chip " + (v.status === "arrived" ? "ok" : "");
  $("#trip").textContent = `${v.from} → ${v.to}`;
  $("#route").textContent = "Route: " + v.route_text;
  drawMap(v);
  const cards = v.alerts.map((a) => {
    const live = LIVE.includes(a.state);
    const cls = a.kind === "ev" ? (live ? "ev live" : "ev done gone") : a.kind === "route" ? "info" : (live ? "warn" : "done gone");
    const band = a.kind === "ev" && live && a.band ? `<span class="chip ${a.band === "imminent" ? "bad" : "warn"}">${a.band === "imminent" ? "very soon" : "approaching"}</span>` : "";
    return `<div class="card ${cls}"><div class="tag">${esc(LABEL[a.kind] || a.kind)} ${band}</div><div class="t">${esc(a.text)}</div></div>`;
  });
  if (v.suggestion) cards.push(`<div class="card info"><div class="tag">Suggested route</div><div class="t">${esc(v.suggestion.text)}</div><button class="big" id="accept">Take this route</button></div>`);
  if (!cards.length) cards.push(`<div class="card done"><div class="t">All clear. Nothing affects your route right now.</div></div>`);
  $("#alerts").innerHTML = cards.join("");
  const b = $("#accept"); if (b) b.addEventListener("click", async () => { await fetch(`/api/public/road-users/${token}/route/accept`, { method: "POST" }); });
}

function show(joined) { $("#join").hidden = joined; $("#live").hidden = !joined; }

async function loop() {
  if (running) return; running = true;
  while (token) {
    try {
      const r = await fetch(`/api/public/road-users/${token}/poll?after=${version}&timeout=12`, { cache: "no-store" });
      if (r.status === 404) { reset("Your session ended."); break; }
      if (r.ok) render(await r.json());
    } catch (e) { await new Promise((res) => setTimeout(res, 1000)); }
  }
  running = false;
}

function reset(msg) {
  token = null; version = -1; try { sessionStorage.removeItem("fs_driver"); } catch (e) {}
  show(false); $("#err").textContent = msg || "";
}

async function init() {
  topo = await (await fetch("/api/public/topology")).json();
  const ext = Object.entries(topo.nodes).filter(([, n]) => n.kind === "external");
  const opts = ext.map(([id, n]) => `<option value="${esc(id)}">${esc(n.label)}</option>`).join("");
  $("#from").innerHTML = opts; $("#to").innerHTML = opts; if (ext.length > 1) $("#to").selectedIndex = ext.length - 1;
  if (token) {
    const r = await fetch(`/api/public/road-users/${token}`);
    if (r.ok) { show(true); render(await r.json()); loop(); return; }
    reset("");
  }
  show(false);
}

$("#go").addEventListener("click", async () => {
  $("#err").textContent = "";
  const r = await fetch("/api/public/road-users", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ origin: $("#from").value, destination: $("#to").value }) });
  if (!r.ok) { let m = "Could not start"; try { m = (await r.json()).detail || m; } catch (e) {} $("#err").textContent = typeof m === "string" ? m : "Check your choices."; return; }
  const v = await r.json(); token = v.token;
  try { sessionStorage.setItem("fs_driver", token); } catch (e) {}
  show(true); render(v); loop();
});
$("#leave").addEventListener("click", async () => { if (token) await fetch(`/api/public/road-users/${token}`, { method: "DELETE" }); reset("You left. See you next time."); });
init();
