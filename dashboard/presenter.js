"use strict";
const $ = (s) => document.querySelector(s);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
let token = null;
try { token = localStorage.getItem("fs_presenter_token"); } catch (e) { /* storage may be blocked */ }

async function get(path) {
  const r = await fetch(path, { headers: token ? { Authorization: "Bearer " + token } : {} });
  if (r.status === 401) { $("#login").style.display = "flex"; throw new Error("login"); }
  return r.json();
}
$("#login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const r = await fetch("/api/auth/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username: "operator", password: $("#p").value }) });
  if (!r.ok) { $("#err").textContent = "Invalid credentials."; return; }
  token = (await r.json()).token; try { localStorage.setItem("fs_presenter_token", token); } catch (e) {}
  $("#login").style.display = "none"; $("#p").value = ""; tick();
});

const ICON = (s) => !s.expect ? "•" : s.expect.ok === true ? "✓" : s.expect.ok === false ? "✗" : s.state === "done" ? "…" : "○";
const PROV = { Built: "ok", Simulated: "warn", Replay: "warn", "Not connected": "bad", Idle: "stale", "Not built": "stale" };

async function tick() {
  try {
    const [s, prov] = await Promise.all([get("/api/operator/scenario"), get("/api/operator/provenance")]);
    $("#title").textContent = s.title || "FlowSync AI";
    const st = $("#state"); st.textContent = s.state; st.className = "chip " + ({ running: "ok", finished: "ok", stopped: "warn" }[s.state] || "stale");
    const w = $("#world"); w.textContent = s.world === "simulator" ? "simulated world" : "physical table"; w.className = "chip " + (s.world === "simulator" ? "warn" : "ok");
    $("#bar").style.width = s.duration ? Math.min(100, 100 * s.t / s.duration) + "%" : "0%";
    if (s.current) {
      $("#step").textContent = s.current.title; $("#narr").textContent = s.current.narration;
      $("#cue").textContent = s.world === "physical" && s.current.cue ? "▶ On the table: " + s.current.cue : "";
    }
    $("#checks").innerHTML = (s.steps || []).filter((x) => x.expect).map((x) => `<div class="check" style="opacity:${x.expect.ok === null && x.state === "pending" ? 0.5 : 1}">
      <span class="i ${x.expect.ok === true ? "ok" : x.expect.ok === false ? "bad" : ""}">${ICON(x)}</span>
      <div>${esc(x.expect.desc)}${x.expect.detail ? `<div class="muted" style="font-size:14px">${esc(x.expect.detail)}</div>` : ""}</div></div>`).join("");
    const m = s.summary ? { "checks passed": `${s.summary.checks_passed}/${s.summary.checks_total}`, "ambulance trip (sim. s)": s.summary.ev_travel_s, "reroutes": s.summary.reroutes,
      "signal priority decisions": s.summary.emergency_priority_decisions, "incidents raised": s.summary.incidents_opened } : {};
    $("#tiles").innerHTML = Object.entries(m).filter(([, v]) => v != null).map(([k, v]) => `<div class="tile"><b>${esc(v)}</b>${esc(k)}</div>`).join("");
    $("#prov").innerHTML = `<table>${prov.map((r) => `<tr><td>${esc(r.component)}</td><td><span class="chip ${PROV[r.status] || "stale"}">${esc(r.status)}</span></td></tr>`).join("")}</table>`;
  } catch (e) { /* login prompt or backend briefly unreachable */ }
}
tick(); setInterval(tick, 1000);
