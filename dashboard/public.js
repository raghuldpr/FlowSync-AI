"use strict";
const $ = (s) => document.querySelector(s);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const col = (c) => getComputedStyle(document.documentElement).getPropertyValue(c).trim();
let approach = new URLSearchParams(location.search).get("approach") || "W1", lastOk = 0;

async function init() {
  const t = await (await fetch("/api/public/topology")).json();
  const opts = t.edges.filter((e) => e.approach).map((e) => e.id);
  if (!opts.includes(approach)) approach = opts[0];
  $("#approach").innerHTML = opts.map((a) => `<option${a === approach ? " selected" : ""}>${a}</option>`).join("");
  $("#approach").addEventListener("change", (e) => { approach = e.target.value; history.replaceState(null, "", "?approach=" + approach); pollWarning(); });
  pollWarning(); pollOverview();
  setInterval(pollWarning, 500); setInterval(pollOverview, 2000);
}

async function pollWarning() {
  const card = $("#card");
  try {
    const r = await fetch("/api/public/warnings?approach=" + encodeURIComponent(approach), { cache: "no-store" });
    const w = (await r.json()).warning;
    lastOk = Date.now();
    if (w) {
      card.className = "card warn";
      card.innerHTML = `<h2>⚠ Emergency vehicle</h2><p>${esc(w.text)}</p><span class="chip ${w.eta_band === "imminent" ? "bad" : "warn"} band">${w.eta_band === "imminent" ? "Arriving very soon" : "Approaching"}</span>`;
    } else {
      card.className = "card";
      card.innerHTML = `<h2>No emergency vehicle warnings</h2><p class="muted">Drive normally and obey the signals.</p>`;
    }
    $("#stamp").textContent = "Updated " + new Date().toLocaleTimeString();
  } catch (e) {
    card.className = "card";
    card.innerHTML = `<h2>Connection lost</h2><p class="muted">Warnings unavailable. Follow road signs and signals.</p>`;
  }
}

async function pollOverview() {
  try {
    const [s, c] = await Promise.all([fetch("/api/public/signals").then((r) => r.json()), fetch("/api/public/congestion").then((r) => r.json())]);
    const L = { R: col("--lamp-r"), Y: col("--lamp-y"), G: col("--lamp-g") }, bandChip = { low: "ok", medium: "warn", high: "bad", unknown: "stale" };
    $("#lights").innerHTML = Object.entries(s).map(([i, v]) => `<div class="panel"><b>${esc(i)}</b><br>
      ${v === "unknown" ? `<span class="chip stale">no data</span>` : `A<span class="dot" style="background:${L[v.A]}"></span> B<span class="dot" style="background:${L[v.B]}"></span>`}</div>`).join("") +
      `<div class="panel" style="grid-column:1/-1;text-align:left"><span class="muted">Congestion:</span> ${Object.entries(c).map(([a, b]) => `<span class="chip ${bandChip[b]}">${esc(a)} ${esc(b)}</span>`).join("")}</div>`;
  } catch (e) { /* keep last view */ }
}
init();
