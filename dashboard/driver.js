"use strict";
const $ = (s) => document.querySelector(s);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const NS = "http://www.w3.org/2000/svg";
let topo = null, token = null, version = -1, running = false, audio = null, wasTakeover = false, lastReceipt = null, role = "vehicle";
try { token = sessionStorage.getItem("fs_driver"); } catch (e) { /* storage may be blocked */ }

const LIVE = ["active", "updated", "redirected"];
const LABEL = { ev: "Emergency vehicle", v2v: "Emergency alert (simulated V2V)", incident: "Incident ahead", hazard: "Road hazard", route: "Route update" };
const GATE = (() => { try { return new URLSearchParams(location.search).get("gate"); } catch (e) { return null; } })();

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
  // hazard / incident layer: only the ones relevant to this route (the server decides), marked on their road
  for (const a of v.alerts) {
    const e = a.place && g[a.place]; if (!e || !LIVE.includes(a.state)) continue;
    const x = (e.x1 + e.x2) / 2, y = (e.y1 + e.y2) / 2;
    el("circle", { cx: x, cy: y, r: 13, fill: a.kind === "hazard" ? css("--warn") : css("--bad"), stroke: "#fff", "stroke-width": 2 });
    const t = el("text", { x, y: y + 5, "text-anchor": "middle", "font-size": 16, fill: "#fff", "font-weight": 700 }); t.textContent = "!";
  }
  const e = g[v.edge];
  if (e) el("circle", { cx: e.x1 + (e.x2 - e.x1) * v.progress, cy: e.y1 + (e.y2 - e.y1) * v.progress, r: 15, fill: v.role === "ambulance" ? "#c43030" : (v.color || css("--accent")), stroke: "#fff", "stroke-width": 3 });
}
const css = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();

// ---- takeover: full-screen warning only for phones the server says are relevant ---------------------------------------
function beep() {
  try {
    if (!audio) return;
    const t0 = audio.currentTime;
    [0, 0.35, 0.7].forEach((d, i) => { const o = audio.createOscillator(), g = audio.createGain(); o.frequency.value = i % 2 ? 640 : 880; o.connect(g); g.connect(audio.destination);
      g.gain.setValueAtTime(0.0001, t0 + d); g.gain.exponentialRampToValueAtTime(0.25, t0 + d + 0.03); g.gain.exponentialRampToValueAtTime(0.0001, t0 + d + 0.3); o.start(t0 + d); o.stop(t0 + d + 0.32); });
  } catch (e) { /* audio is optional */ }
}
function toast(text) { const t = $("#toast"); t.textContent = text; t.classList.add("on"); setTimeout(() => t.classList.remove("on"), 7000); }

function takeover(v) {
  const live = v.alerts.filter((a) => (a.kind === "ev" || a.kind === "v2v") && LIVE.includes(a.state));
  const box = $("#takeover");
  if (live.length && v.status === "driving") {
    const a = live.find((x) => x.band === "imminent") || live[0];
    const first = !wasTakeover;
    $("#to-tag").textContent = a.kind === "v2v" ? "Emergency alert · simulated V2V" : "Emergency vehicle";
    $("#to-text").textContent = a.text;
    $("#to-eta").textContent = a.band === "imminent" ? "very soon" : "approaching";
    $("#to-arrow").textContent = "↑";
    box.classList.add("on");
    if (first) { beep(); try { navigator.vibrate && navigator.vibrate([300, 150, 300]); } catch (e) {} }
    wasTakeover = true;
  } else {
    box.classList.remove("on");
    if (wasTakeover) {                                    // live route change: say what happened, in the fixed wording
      const done = v.alerts.find((a) => (a.kind === "ev" || a.kind === "v2v") && !LIVE.includes(a.state));
      if (done) toast(done.text);
    }
    wasTakeover = false;
  }
}

function render(v) {
  version = v.version; role = v.role;
  $("#me").textContent = `${v.id} · ${v.plate}`;
  $("#dot").style.background = v.role === "ambulance" ? "#c43030" : (v.color || "var(--accent)");
  const st = $("#status"); st.textContent = v.status === "arrived" ? "arrived" : v.role === "ambulance" ? "ambulance" : "driving"; st.className = "chip " + (v.status === "arrived" ? "ok" : v.role === "ambulance" ? "bad" : "");
  $("#trip").textContent = `${v.from} → ${v.to}`;
  $("#route").textContent = "Route: " + v.route_text;
  drawMap(v);
  const amb = $("#ambulance"); amb.hidden = !(v.role === "ambulance" && v.status === "driving");
  if (v.ambulance && v.status === "driving") {
    const a = v.ambulance;
    $("#a-eta").textContent = a.eta_s != null ? `${a.eta_s} s (sim.)` : "–";
    $("#a-pri").textContent = a.priority_at && a.priority_at.length ? a.priority_at.join(", ") : "none yet";
    $("#a-route").textContent = a.rerouted ? `rerouted ×${a.reroutes}` : "direct";
    const b = $("#alert"); b.disabled = !a.can_alert || a.alert_cooldown_s > 0;
    b.textContent = a.alert_cooldown_s > 0 ? `Alert sent · again in ${a.alert_cooldown_s} s` : "Raise emergency alert";
  }
  if (!(v.role === "ambulance") ) takeover(v); else { $("#takeover").classList.remove("on"); }
  const cards = v.alerts.map((a) => {
    const live = LIVE.includes(a.state);
    const emer = a.kind === "ev" || a.kind === "v2v";
    const cls = emer ? (live ? "ev live" : "ev done gone") : a.kind === "route" ? "info" : (live ? "warn" : "done gone");
    const band = emer && live && a.band ? `<span class="chip ${a.band === "imminent" ? "bad" : "warn"}">${a.band === "imminent" ? "very soon" : "approaching"}</span>` : "";
    const sim = a.simulated ? ` <span class="sim">simulated</span>` : "";
    return `<div class="card ${cls}"><div class="tag">${esc(LABEL[a.kind] || a.kind)} ${band}${sim}</div><div class="t">${esc(a.text)}</div></div>`;
  });
  if (v.suggestion) cards.push(`<div class="card info"><div class="tag">Suggested route</div><div class="t">${esc(v.suggestion.text)}</div><button class="big" id="accept">Take this route</button></div>`);
  if (!cards.length && v.status === "driving") cards.push(`<div class="card done"><div class="t">All clear. Nothing affects your route right now.</div></div>`);
  $("#alerts").innerHTML = cards.join("");
  const b = $("#accept"); if (b) b.addEventListener("click", async () => { await fetch(`/api/public/road-users/${token}/route/accept`, { method: "POST" }); });
  if (v.receipt) {
    lastReceipt = v.receipt;
    $("#receipt").hidden = false;
    $("#receipt").innerHTML = receiptHtml(v.receipt, false);
  }
}

function receiptHtml(r, gone) {
  const way = r.made_way_for ? `You made way for ${r.made_way_for} ambulance${r.made_way_for > 1 ? "s" : ""}. Thank you.` : "No ambulance needed you to make way on this trip.";
  return `<div class="tag">Trip receipt</div><div class="t">${esc(r.from)} → ${esc(r.to)} in ${esc(r.trip_s)} s (simulated)</div>
    <div class="t" style="font-size:16px;margin-top:6px">${esc(way)}</div>
    <div class="muted" style="margin-top:6px">${gone ? "✓ Your session and location have now been deleted from the server." : esc(r.privacy)}</div>
    ${gone ? `<button class="big" id="again">Drive again</button>` : ""}`;
}

function show(joined) { $("#join").hidden = joined; $("#live").hidden = !joined; }

async function loop() {
  if (running) return; running = true;
  while (token) {
    try {
      const r = await fetch(`/api/public/road-users/${token}/poll?after=${version}&timeout=12`, { cache: "no-store" });
      if (r.status === 404) { ended(); break; }
      if (r.ok) render(await r.json());
    } catch (e) { await new Promise((res) => setTimeout(res, 1000)); }
  }
  running = false;
}

function ended() {                                       // the server has deleted the session
  token = null; version = -1; wasTakeover = false; $("#takeover").classList.remove("on");
  try { sessionStorage.removeItem("fs_driver"); } catch (e) {}
  if (lastReceipt) {
    $("#receipt").hidden = false; $("#receipt").innerHTML = receiptHtml(lastReceipt, true);
    $("#ambulance").hidden = true; $("#alerts").innerHTML = ""; $("#status").textContent = "deleted";
    const b = $("#again"); if (b) b.addEventListener("click", () => { lastReceipt = null; reset(""); });
  } else reset("Your session ended.");
}

function reset(msg) {
  token = null; version = -1; lastReceipt = null; try { sessionStorage.removeItem("fs_driver"); } catch (e) {}
  $("#receipt").hidden = true; $("#pick").hidden = false; $("#amb-form").hidden = true;
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

function unlockAudio() { try { audio = audio || new (window.AudioContext || window.webkitAudioContext)(); audio.resume && audio.resume(); } catch (e) { audio = null; } }

async function start(body) {
  $("#err").textContent = "";
  unlockAudio();                                          // the tap that starts the trip also allows sound later
  const r = await fetch("/api/public/road-users", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (!r.ok) { let m = "Could not start"; try { m = (await r.json()).detail || m; } catch (e) {} $("#err").textContent = typeof m === "string" ? m : "Check your choices."; return; }
  const v = await r.json(); token = v.token; lastReceipt = null; $("#receipt").hidden = true;
  try { sessionStorage.setItem("fs_driver", token); } catch (e) {}
  show(true); render(v); loop();
}

$("#as-vehicle").addEventListener("click", () => start(GATE ? { role: "vehicle", origin: GATE } : { role: "vehicle" }));
$("#as-amb").addEventListener("click", () => { $("#pick").hidden = true; $("#amb-form").hidden = false; if (GATE) $("#from").value = GATE; });
$("#back").addEventListener("click", () => { $("#pick").hidden = false; $("#amb-form").hidden = true; });
$("#go-amb").addEventListener("click", () => start({ role: "ambulance", origin: $("#from").value, destination: $("#to").value }));
$("#alert").addEventListener("click", async () => {
  if (!token) return;
  const r = await fetch(`/api/public/road-users/${token}/alert`, { method: "POST" });
  const note = $("#alert-note");
  if (r.ok) { const j = await r.json(); note.textContent = `Simulated V2V alert sent. ${j.reached} car${j.reached === 1 ? "" : "s"} on your route ahead received it.`; }
  else { let m = "Could not send"; try { m = (await r.json()).detail || m; } catch (e) {} note.textContent = m; }
});
$("#leave").addEventListener("click", async () => { if (token) await fetch(`/api/public/road-users/${token}`, { method: "DELETE" }); lastReceipt = null; reset("You left. See you next time."); });
init();
