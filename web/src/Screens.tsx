import { useEffect, useMemo, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { Bot, CircleCheck, Cpu, Play, Send, Square } from 'lucide-react'
import { Badge, Card, TrafficFlow, useComponentRows } from './Cards'
import { api } from './api'
import { INCIDENT_TITLE, statusTone } from './live'
import { SCENARIOS, mmss, type Snap } from './sim'
import { useIncidentRows, useIsLive, useSnap, useStore, type Incident } from './store'

const ROLE_NOTE = 'Your account is a viewer. Sign in as an operator to do this.'

/** Runs a backend write as the signed-in user, refreshes the data, and surfaces the backend's error text. */
function useAct() {
  const token = useStore((s) => s.auth?.token)
  const bump = useStore((s) => s.bumpPoll)
  const [err, setErr] = useState('')
  const act = async (method: 'POST' | 'GET', path: string, body?: unknown) => {
    setErr('')
    try { const r = await api(method, path, token, body); bump(); return r }
    catch (e) { setErr(e instanceof Error ? e.message : 'Request failed'); return null }
  }
  const banner = err ? <p role="alert" className="mb-3 rounded-lg bg-danger/10 px-3 py-2 text-[12.5px] text-red-600">{err}</p> : null
  return { act, banner }
}
const useCanOperate = () => useStore((s) => !s.auth || s.auth.role !== 'viewer')

/* ---------------- Traffic ---------------- */
const APPROACHES = ['West Gate → Central', 'Central → East Gate', 'North Gate → Central', 'South Gate → Central', 'East Gate → Central', 'Central → West Gate']
function band(v: number) { return v < 0.34 ? 'low' : v < 0.67 ? 'medium' : 'high' }
const BAND_TONE = { low: 'ok', medium: 'warn', high: 'danger' } as const
const BAND_BAR = { low: 'bg-ok', medium: 'bg-warn', high: 'bg-danger' } as const
interface TrafficRow { name: string; v: number; text: string; b: 'low' | 'medium' | 'high'; stale?: boolean }

export function Traffic() {
  const s = useSnap()
  const live = useStore((x) => (x.auth ? x.live : null))
  let rows: TrafficRow[]
  if (live?.ov && live.topo) {
    const { ov, topo } = live
    rows = topo.edges.filter((e) => e.approach).map((e) => {
      const t = ov.traffic[e.id], cap = topo.capacities[e.id] ?? topo.capacity
      const v = t ? Math.min(1, t.count / cap) : 0
      return { name: `${e.id} · ${topo.nodes[e.src]?.label ?? e.src} → ${topo.nodes[e.dst]?.label ?? e.dst}`, v, text: t ? `${t.count} / ${cap}` : 'no data', b: band(v), stale: !t || t.stale }
    })
  } else {
    rows = APPROACHES.map((name, i) => {
      let v = 0.45 + 0.3 * Math.sin(s.T / 17 + i * 1.3) + 0.1 * Math.sin(s.T / 5 + i)
      if (s.emergency && i < 2 && s.phase >= 8 && s.phase < 50) v *= 0.55
      if (s.scenarioName === 'Road Hazard' && i === 1) v = 0.85
      v = Math.min(0.98, Math.max(0.05, v))
      return { name, v, text: `${Math.round(v * 24)} / 24`, b: band(v) }
    })
  }
  const counts = { low: 0, medium: 0, high: 0 }
  rows.forEach((r) => { if (!r.stale) counts[r.b]++ })
  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_380px]">
      <TrafficFlow T={s.T} />
      <Card title="Congestion summary">
        <div className="grid grid-cols-3 gap-2 text-center">
          {(['low', 'medium', 'high'] as const).map((k) => (
            <div key={k} className="rounded-lg bg-app p-3"><div className="tnum text-2xl font-bold">{counts[k]}</div><Badge tone={BAND_TONE[k]}>{k}</Badge></div>
          ))}
        </div>
      </Card>
      <Card title="Approaches" className="xl:col-span-2">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[520px] text-left text-[13px]">
            <thead className="text-[11px] uppercase tracking-wider text-muted"><tr><th className="py-2 font-semibold">Approach</th><th className="font-semibold">Load</th><th className="font-semibold">Vehicles</th><th className="text-right font-semibold">Band</th></tr></thead>
            <tbody className="divide-y divide-line">
              {rows.map((r) => (
                <tr key={r.name}>
                  <td className="py-2.5 font-medium">{r.name}</td>
                  <td className="w-1/3 pr-4"><div className="h-2 rounded-full bg-line"><div className={`h-2 rounded-full ${BAND_BAR[r.b]}`} style={{ width: `${r.v * 100}%` }} /></div></td>
                  <td className="tnum">{r.text}</td>
                  <td className="text-right">{r.stale ? <Badge tone="mute">stale</Badge> : <Badge tone={BAND_TONE[r.b]}>{r.b}</Badge>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {live?.ov && <p className="mt-3 text-[12px] text-muted">Queue counts from {[...new Set(Object.values(live.ov.traffic).map((t) => t.source))].join(', ') || 'no source'}.</p>}
      </Card>
    </div>
  )
}

/* ---------------- Incidents ---------------- */
const SEV_TONE = { high: 'danger', medium: 'warn', low: 'mute' } as const
const ST_TONE = { active: 'danger', acknowledged: 'warn', resolved: 'ok' } as const
const Field = ({ label, children }: { label: string; children: ReactNode }) => <label className="block"><span className="mb-1 block text-xs text-muted">{label}</span>{children}</label>

export function Incidents() {
  const isLive = useIsLive()
  const { addIncident, setIncidentStatus, T } = useStore()
  const topo = useStore((s) => s.live.topo)
  const rows = useIncidentRows()
  const can = useCanOperate()
  const { act, banner } = useAct()
  const [filter, setFilter] = useState<'all' | Incident['status']>('all')
  const [title, setTitle] = useState('')
  const approaches = topo?.edges.filter((e) => e.approach).map((e) => e.id) ?? []
  const [where, setWhere] = useState('West Gate')
  const [type, setType] = useState('congestion_spike')
  const [approach, setApproach] = useState('')
  const [sev, setSev] = useState<Incident['sev']>('medium')
  const shown = rows.filter((i) => filter === 'all' || i.status === filter)

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    if (isLive) { await act('POST', '/api/operator/incidents', { type, approach: approach || approaches[0] }); return }
    if (!title.trim()) return
    addIncident({ title: title.trim(), where, sev, at: T })
    setTitle('')
  }
  const ack = (id: number) => (isLive ? act('POST', `/api/operator/incidents/${id}/ack`) : setIncidentStatus(id, 'acknowledged'))
  const resolve = (id: number) => (isLive ? act('POST', `/api/operator/incidents/${id}/resolve`) : setIncidentStatus(id, 'resolved'))

  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_340px]">
      <Card title="Incidents" right={
        <div role="tablist" className="flex gap-1 text-xs">
          {(['all', 'active', 'acknowledged', 'resolved'] as const).map((f) => (
            <button key={f} role="tab" aria-selected={filter === f} type="button" onClick={() => setFilter(f)}
              className={`rounded-md px-2.5 py-1 font-medium capitalize ${filter === f ? 'bg-primary/10 text-primary' : 'text-muted hover:bg-app'}`}>{f}</button>
          ))}
        </div>
      }>
        {banner}
        {shown.length === 0 && (
          <div className="grid place-items-center gap-2 py-10 text-center text-[13px] text-muted">
            <CircleCheck size={28} className="text-ok" />{filter === 'all' ? 'No incidents. Traffic is flowing normally.' : `No ${filter} incidents.`}
          </div>
        )}
        <ul className="divide-y divide-line">
          {shown.map((i) => (
            <li key={i.id} className="flex flex-wrap items-center gap-3 py-3">
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2 text-[14px] font-semibold">{i.title} <Badge tone={SEV_TONE[i.sev]}>{i.sev}</Badge></div>
                <div className="text-[12px] text-muted">{i.where} · reported {i.label ?? mmss(i.at)}</div>
              </div>
              <Badge tone={ST_TONE[i.status]}>{i.status}</Badge>
              <div className="flex gap-1.5">
                {i.status === 'active' && <button type="button" disabled={!can} title={can ? undefined : ROLE_NOTE} onClick={() => ack(i.id)} className="rounded-md border border-line px-2.5 py-1 text-xs font-medium enabled:hover:bg-app disabled:opacity-40">Acknowledge</button>}
                {i.status !== 'resolved' && <button type="button" disabled={!can} title={can ? undefined : ROLE_NOTE} onClick={() => resolve(i.id)} className="rounded-md bg-primary px-2.5 py-1 text-xs font-medium text-white enabled:hover:bg-primary/90 disabled:opacity-40">Resolve</button>}
              </div>
            </li>
          ))}
        </ul>
      </Card>
      <Card title="Report incident">
        <form onSubmit={submit} className="space-y-3 text-[13px]">
          {isLive ? (
            <>
              <Field label="Type"><select value={type} onChange={(e) => setType(e.target.value)} className="w-full rounded-lg border border-line bg-white px-3 py-2">
                {Object.entries(INCIDENT_TITLE).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></Field>
              <Field label="Approach"><select value={approach || approaches[0]} onChange={(e) => setApproach(e.target.value)} className="w-full rounded-lg border border-line bg-white px-3 py-2">
                {approaches.map((a) => <option key={a}>{a}</option>)}</select></Field>
            </>
          ) : (
            <>
              <Field label="What happened"><input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="e.g. Debris on road" className="w-full rounded-lg border border-line px-3 py-2 outline-none focus:border-primary" /></Field>
              <Field label="Location"><select value={where} onChange={(e) => setWhere(e.target.value)} className="w-full rounded-lg border border-line bg-white px-3 py-2">
                {['West Gate', 'Central', 'East Gate', 'South Gate', 'North Gate'].map((w) => <option key={w}>{w}</option>)}</select></Field>
              <Field label="Severity"><select value={sev} onChange={(e) => setSev(e.target.value as Incident['sev'])} className="w-full rounded-lg border border-line bg-white px-3 py-2">
                <option value="low">Low</option><option value="medium">Medium</option><option value="high">High</option></select></Field>
            </>
          )}
          <button type="submit" disabled={!can} title={can ? undefined : ROLE_NOTE} className="w-full rounded-lg bg-primary py-2 font-semibold text-white enabled:hover:bg-primary/90 disabled:opacity-40">Report</button>
        </form>
      </Card>
    </div>
  )
}

/* ---------------- Scenarios ---------------- */
const SCN_INFO: Record<string, string> = {
  emergency: 'An ambulance crosses West Gate → East Gate. Signals are held green, nearby road users are warned and rerouted.',
  normal: 'Background traffic only. Signals run on their normal timing and no alerts are sent.',
  hazard: 'A road hazard loads the Central → East Gate approach. Congestion bands rise on that link.',
  signal: 'One intersection loses coordination and falls back to fixed timing.',
}
function LiveScenarios() {
  const sc = useStore((s) => s.live.ov!.scenario)
  const can = useCanOperate()
  const { act, banner } = useAct()
  const running = sc.state === 'running'
  return (
    <div className="space-y-4">
      {banner}
      {sc.state !== 'idle' && (
        <Card title={sc.title ?? 'Scenario'} right={<Badge tone={running ? 'ok' : 'mute'}>{sc.state}</Badge>}>
          <div className="h-2 rounded-full bg-line"><div className="h-2 rounded-full bg-primary" style={{ width: `${Math.min(100, ((sc.t ?? 0) / (sc.duration || 1)) * 100)}%` }} /></div>
          <div className="tnum mt-1 text-[11px] text-muted">{Math.round(sc.t ?? 0)}s of {sc.duration}s · world: {sc.world}</div>
          {sc.current && <div className="mt-3 rounded-lg bg-app p-3 text-[13px]"><div className="font-semibold">{sc.current.title}</div><div className="text-muted">{sc.current.narration}</div>{sc.world === 'physical' && sc.current.cue && <div className="mt-1 text-primary">Cue: {sc.current.cue}</div>}</div>}
          <ul className="mt-3 grid gap-1.5 text-[12.5px] md:grid-cols-2">
            {(sc.steps ?? []).map((st) => (
              <li key={st.at + st.title} className="flex items-start gap-2"><CircleCheck size={15} className={st.state === 'done' ? (st.expect?.ok === false ? 'text-danger' : 'text-ok') : 'text-slate'} /><span className={st.state === 'done' ? '' : 'text-muted'}>{st.title}{st.expect?.ok === false && <b className="text-danger"> · check failed: {st.expect.detail}</b>}</span></li>
            ))}
          </ul>
          {sc.summary && <p className="mt-3 text-[12.5px] text-muted">Checks passed: {String(sc.summary.checks_passed)}/{String(sc.summary.checks_total)} · ambulance travel {String(sc.summary.ev_travel_s)}s</p>}
          {running && <button type="button" disabled={!can} title={can ? undefined : ROLE_NOTE} onClick={() => act('POST', '/api/operator/scenario/stop')} className="mt-3 flex items-center gap-1.5 rounded-lg border border-line px-3 py-1.5 text-[13px] font-medium enabled:hover:bg-app disabled:opacity-40"><Square size={14} />Stop</button>}
        </Card>
      )}
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {sc.scenarios.map((x) => (
          <Card key={x.id} className={`flex flex-col ${sc.id === x.id && running ? 'ring-2 ring-primary' : ''}`}>
            <div className="flex items-center justify-between gap-2"><h2 className="text-[15px] font-semibold">{x.title}</h2><Badge tone={x.kind === 'demo' ? 'info' : 'mute'}>{x.kind}</Badge></div>
            <p className="mt-2 flex-1 text-[13px] text-muted">{x.description}</p>
            <div className="mt-3 flex items-center justify-between">
              <button type="button" disabled={running || !can} title={can ? (running ? 'A scenario is already running' : undefined) : ROLE_NOTE} onClick={() => act('POST', '/api/operator/scenario/start', { id: x.id })}
                className="flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-[13px] font-medium text-white enabled:hover:bg-primary/90 disabled:opacity-40"><Play size={14} />Start</button>
              <span className="tnum text-[11px] text-muted">{x.duration}s</span>
            </div>
          </Card>
        ))}
      </div>
    </div>
  )
}
export function Scenarios() {
  const isLive = useIsLive()
  const { scenario, setScenario } = useStore()
  if (isLive) return <LiveScenarios />
  return (
    <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
      {SCENARIOS.map((sc) => {
        const active = sc.id === scenario
        return (
          <Card key={sc.id} className={active ? 'ring-2 ring-primary' : ''}>
            <div className="flex items-center justify-between"><h2 className="text-[15px] font-semibold">{sc.name}</h2>{active && <Badge tone="ok">Running</Badge>}</div>
            <p className="mt-2 min-h-[72px] text-[13px] text-muted">{SCN_INFO[sc.id]}</p>
            {active ? (
              <button type="button" onClick={() => setScenario('normal')} disabled={sc.id === 'normal'} className="mt-3 flex items-center gap-1.5 rounded-lg border border-line px-3 py-1.5 text-[13px] font-medium enabled:hover:bg-app disabled:opacity-40"><Square size={14} />Stop</button>
            ) : (
              <button type="button" onClick={() => setScenario(sc.id)} className="mt-3 flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-[13px] font-medium text-white hover:bg-primary/90"><Play size={14} />Start</button>
            )}
          </Card>
        )
      })}
    </div>
  )
}

/* ---------------- Assistant ---------------- */
function answer(q: string, s: Snap, open: number): string {
  const t = q.toLowerCase()
  if (/ambulance|eta|emergency/.test(t))
    return s.emergency ? (s.ev.state === 'arrived' ? 'The ambulance has reached East Gate and the corridor is released.' : `The ambulance is heading West Gate → East Gate. ETA about ${s.ev.eta}s, and the green corridor is active.`) : 'No emergency is active in this scenario.'
  if (/signal|light|green|red/.test(t)) return s.signals.map((g) => `${g.id} ${g.name}: ${g.lamp} (${g.left}s)${g.preempt ? ', held for the ambulance' : ''}`).join('\n')
  if (/warn|alert|notif/.test(t)) return `${s.warned} road users have been warned and ${s.notWarned} are not on the ambulance path. ${s.reroutes} route suggestions were sent.`
  if (/incident|hazard|pothole/.test(t)) return `${open} incidents are open. See the Incidents screen to acknowledge or resolve them.`
  if (/scenario/.test(t)) return `The current scenario is ${s.scenarioName}.`
  if (/traffic|congest|flow/.test(t)) return `There are ${s.roadUsers} road users in the city. Average travel time is ${s.stats.avgTravel} min.`
  return 'I can answer about the ambulance, signals, warnings, incidents, traffic or the scenario.'
}
interface LlmReply { answer: string; mode: string; model?: string | null; fallback_reason?: string | null; tool_calls: { name: string; ok: boolean }[]; proposals: unknown[] }

export function Assistant() {
  const { chat, pushChat } = useStore()
  const isLive = useIsLive()
  const token = useStore((s) => s.auth?.token)
  const rows = useIncidentRows()
  const s = useSnap()
  const [q, setQ] = useState('')
  const [busy, setBusy] = useState(false)
  const end = useRef<HTMLDivElement>(null)
  useEffect(() => { end.current?.scrollIntoView({ block: 'end' }) }, [chat.length])
  const send = async (text: string) => {
    if (!text.trim() || busy) return
    pushChat({ role: 'user', text })
    setQ('')
    if (!isLive) { pushChat({ role: 'assistant', text: answer(text, s, rows.filter((i) => i.status !== 'resolved').length) }); return }
    setBusy(true)
    try {
      const r = await api<LlmReply>('POST', '/api/llm/query', token, { question: text })
      const how = r.mode === 'llm' ? `LLM · ${r.model}` : `Deterministic fallback${r.fallback_reason ? ` (${r.fallback_reason})` : ''}`
      const tools = r.tool_calls.length ? ` · tools: ${r.tool_calls.map((c) => c.name).join(', ')}` : ''
      pushChat({ role: 'assistant', text: `${r.answer}\n\n— ${how}${tools}${r.proposals.length ? ' · proposal queued, it acts only after an operator confirms' : ''}` })
    } catch (e) { pushChat({ role: 'assistant', text: `The backend could not answer: ${e instanceof Error ? e.message : 'request failed'}` }) }
    setBusy(false)
  }
  return (
    <Card className="mx-auto flex h-[calc(100vh-8.5rem)] min-h-[420px] max-w-3xl flex-col">
      <div className="mb-2 text-[11px] text-muted">{isLive ? 'Connected to the FlowSync assistant (/api/llm/query).' : 'Demo assistant that reads the dashboard state. Sign in to use the real one.'}</div>
      <div className="flex-1 space-y-3 overflow-y-auto pr-1">
        {chat.map((m, i) => (
          <div key={i} className={`flex gap-2 ${m.role === 'user' ? 'justify-end' : ''}`}>
            {m.role === 'assistant' && <span className="grid h-7 w-7 shrink-0 place-items-center rounded-full bg-primary/10 text-primary"><Bot size={15} /></span>}
            <div className={`max-w-[80%] whitespace-pre-line rounded-xl px-3 py-2 text-[13px] ${m.role === 'user' ? 'bg-primary text-white' : 'bg-app'}`}>{m.text}</div>
          </div>
        ))}
        {busy && <div className="text-[12px] text-muted">Thinking…</div>}
        <div ref={end} />
      </div>
      <div className="mt-3 flex flex-wrap gap-1.5">
        {['Where is the ambulance?', 'Signal status', 'How many users are warned?'].map((p) => (
          <button key={p} type="button" onClick={() => send(p)} className="rounded-full border border-line px-3 py-1 text-xs hover:bg-app">{p}</button>
        ))}
      </div>
      <form onSubmit={(e) => { e.preventDefault(); send(q) }} className="mt-2 flex gap-2">
        <input value={q} onChange={(e) => setQ(e.target.value)} aria-label="Ask the assistant" placeholder="Ask about the city…" className="flex-1 rounded-lg border border-line px-3 py-2 text-[13px] outline-none focus:border-primary" />
        <button type="submit" aria-label="Send" disabled={busy} className="grid h-9 w-9 place-items-center rounded-lg bg-primary text-white disabled:opacity-50"><Send size={16} /></button>
      </form>
    </Card>
  )
}

/* ---------------- System status ---------------- */
export function SystemStatus() {
  const { backend, mode } = useStore()
  const live = useStore((s) => (s.auth ? s.live : null))
  const rows = useComponentRows()
  const s = useSnap()
  const devices = live?.ov
    ? live.ov.devices.map((d) => ({ n: d.device_id, role: d.role, age: d.age_s }))
    : [{ n: 'roadcam (ESP32-CAM)', role: 'Pothole camera', age: 2 + (s.T % 3) }, { n: 'traffic module (ESP32)', role: 'Signal lamps', age: 1 + (s.T % 2) }]
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card title="Components">
        <ul className="divide-y divide-line">
          {rows.map((r) => (
            <li key={r.name} className="flex items-start gap-3 py-2.5 text-[13px]"><CircleCheck size={16} className={`mt-0.5 shrink-0 ${statusTone(r.status) === 'ok' ? 'text-ok' : 'text-slate'}`} />
              <span className="flex-1"><span className="block">{r.name}</span>{r.detail && <span className="block text-[11px] text-muted">{r.detail}</span>}</span><Badge tone={statusTone(r.status)}>{r.status}</Badge></li>
          ))}
        </ul>
      </Card>
      <Card title="Backend & devices">
        <ul className="divide-y divide-line text-[13px]">
          <li className="flex items-center gap-3 py-2.5"><Cpu size={16} className="text-primary" /><span className="flex-1">FastAPI backend</span>
            <Badge tone={backend.state === 'live' ? 'ok' : backend.state === 'checking' ? 'mute' : 'warn'}>{backend.state === 'live' ? `connected · ${backend.intersections} intersections` : backend.state === 'checking' ? 'checking' : 'not reachable'}</Badge></li>
          {devices.map((d) => (
            <li key={d.n} className="flex items-center gap-3 py-2.5"><Cpu size={16} className="text-slate" /><div className="flex-1"><div>{d.n}</div><div className="text-[11px] text-muted">{d.role}</div></div>
              {live?.ov
                ? <Badge tone={d.age == null ? 'mute' : d.age > 5 ? 'warn' : 'ok'}>{d.age == null ? 'never seen' : `${d.age}s ago`}</Badge>
                : <Badge tone="mute">{mode === 'simulated' ? 'simulated' : `demo · ${d.age}s ago`}</Badge>}</li>
          ))}
        </ul>
        {!live?.ov && <p className="mt-3 text-[12px] text-muted">Demo values. Sign in to see the real devices and components.</p>}
      </Card>
    </div>
  )
}

/* ---------------- Provenance ---------------- */
const PROV = [
  ['Ambulance position & route', 'Ambulance simulation', 'Built'],
  ['Signal state & preemption', 'Signal controller', 'Built'],
  ['Warnings & reroutes', 'Traffic controller', 'Built'],
  ['Background vehicles', 'Traffic flow generator', 'Simulated'],
  ['Weather', 'Weather system', 'Simulated'],
  ['Traffic flow chart', 'Demo wave function (this dashboard)', 'Simulated'],
  ['Incident list', 'Local state in this dashboard', 'Simulated'],
] as const
export function Provenance() {
  const live = useStore((s) => (s.auth ? s.live.ov : null))
  const rows = live ? live.provenance.map((p) => [p.component, p.detail, p.status] as const) : PROV
  return (
    <Card title={live ? 'Live provenance from the backend' : 'Where each number comes from'}>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[480px] text-left text-[13px]">
          <thead className="text-[11px] uppercase tracking-wider text-muted"><tr><th className="py-2 font-semibold">Data</th><th className="font-semibold">{live ? 'Detail' : 'Source'}</th><th className="text-right font-semibold">Status</th></tr></thead>
          <tbody className="divide-y divide-line">
            {rows.map(([d, src, st]) => <tr key={d}><td className="py-2.5 pr-3 font-medium">{d}</td><td className="pr-3 text-muted">{src}</td><td className="text-right"><Badge tone={statusTone(st)}>{st}</Badge></td></tr>)}
          </tbody>
        </table>
      </div>
      <p className="mt-3 text-[12px] text-muted">{live ? 'This table is computed by the backend from what each data path last reported.' : 'This dashboard is on its scripted 60-second demo loop. Sign in to see where the real data comes from.'}</p>
    </Card>
  )
}

/* ---------------- Settings ---------------- */
export function Settings() {
  const { quality, setQuality, speed, setSpeed, layers, toggleLayer, auth, setAuth, setLoginOpen, backend, live } = useStore()
  const reduced = useMemo(() => window.matchMedia('(prefers-reduced-motion: reduce)').matches, [])
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card title="Backend connection">
        <div className="space-y-2 text-[13px]">
          <div className="flex items-center justify-between"><span>Server</span><Badge tone={backend.state === 'live' ? 'ok' : 'warn'}>{backend.state === 'live' ? 'reachable' : backend.state === 'checking' ? 'checking' : 'not reachable'}</Badge></div>
          <div className="flex items-center justify-between"><span>Data source</span><Badge tone={auth ? 'ok' : 'mute'}>{auth ? 'Live backend' : 'Demo data'}</Badge></div>
          {auth && <div className="flex items-center justify-between"><span>Signed in as</span><b>{auth.user} ({auth.role})</b></div>}
          {auth && live.err && <p className="text-[12px] text-danger">Last error: {live.err}</p>}
          <button type="button" onClick={() => (auth ? setAuth(null) : setLoginOpen(true))} className="mt-2 rounded-lg bg-primary px-3.5 py-1.5 font-medium text-white hover:bg-primary/90">{auth ? 'Sign out' : 'Sign in'}</button>
          <p className="text-[12px] text-muted">The session token is kept for this browser tab only.</p>
        </div>
      </Card>
      <Card title="3D map">
        <div className="space-y-4 text-[13px]">
          <div><div className="mb-1.5 font-medium">Render quality</div>
            <div role="radiogroup" className="inline-flex rounded-lg border border-line bg-app p-0.5">
              {(['high', 'low'] as const).map((q) => <button key={q} type="button" role="radio" aria-checked={quality === q} onClick={() => setQuality(q)} className={`rounded-md px-3.5 py-1 font-medium capitalize ${quality === q ? 'bg-white text-primary shadow-sm' : 'text-muted'}`}>{q}</button>)}
            </div>
            <p className="mt-1 text-[12px] text-muted">Low removes glow and lowers resolution for slower machines.</p></div>
          <div><div className="mb-1.5 font-medium">Default layers</div>
            {(Object.keys(layers) as (keyof typeof layers)[]).map((k) => (
              <label key={k} className="flex items-center gap-2 py-1 capitalize"><input type="checkbox" checked={layers[k]} onChange={() => toggleLayer(k)} className="accent-primary" />{k}</label>
            ))}</div>
          <p className="text-[12px] text-muted">Reduced motion: {reduced ? 'on (from your OS), animations are static' : 'off'}.</p>
        </div>
      </Card>
      <Card title="Demo clock">
        <div className="text-[13px]">
          <label className="mb-1.5 block font-medium" htmlFor="spd">Simulation speed: {speed}×</label>
          <input id="spd" type="range" min={0.5} max={4} step={0.5} value={speed} onChange={(e) => setSpeed(+e.target.value)} disabled={!!auth} className="w-full accent-primary disabled:opacity-40" />
          <p className="mt-1 text-[12px] text-muted">{auth ? 'Only affects the demo loop; the live backend runs in real time.' : 'Speeds up the 60-second scenario loop for demos.'}</p>
        </div>
      </Card>
    </div>
  )
}
