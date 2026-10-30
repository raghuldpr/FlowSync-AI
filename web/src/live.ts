// Pure helpers that turn real FlowSync API responses into the shapes the UI already renders.
// Response shapes were captured from the running backend (/api/public/topology, /api/operator/overview).
import type { Check, EventRow, SigState, Snap } from './sim'

export interface TopoNode { x: number; y: number; label: string; kind: 'intersection' | 'external' }
export interface TopoEdge { id: string; src: string; dst: string; approach: boolean; movement: 'A' | 'B' | null }
export interface Topo { nodes: Record<string, TopoNode>; edges: TopoEdge[]; capacity: number; capacities: Record<string, number> }

export interface ApiSignal {
  commanded: { target: string; reason: string } | null
  reported: { phase: string; axis: string; elapsed_ms: number; lamps: Record<string, string> } | null
  reported_stale: boolean
}
export interface EvSession {
  id: number; vehicle_id: string; origin: string; destination: string; status: string
  edge: string | null; progress: number; stale: boolean; route: string[]; warnings: Record<string, unknown>
}
export interface RoadUser { id: string; simulated: boolean; status: string; edge: string | null; progress: number; alerts: Record<string, string> }
export interface ApiIncident {
  id: number; type: string; approach: string; status: string; severity: string; source: string; detected_at: string
  ack_by: string | null; details?: Record<string, unknown>
}
export interface ScenarioStep { at: number; title: string; narration: string; state: string; expect: { desc: string; ok: boolean | null; detail: string | null } | null }
export interface ScenarioInfo { id: string; title: string; kind: string; description: string; duration: number }
export interface ScenarioStatus {
  state: 'idle' | 'running' | 'finished' | string; world: string; id?: string; title?: string; t?: number; duration?: number
  steps?: ScenarioStep[]; current?: { title: string; narration: string; cue: string } | null
  summary?: Record<string, unknown> | null; scenarios: ScenarioInfo[]
}
export interface SysEvent { id: number; ts: string; type: string; source: string; severity: string; payload: string | null }
export interface Overview {
  traffic: Record<string, { count: number; age_s: number; stale: boolean; source: string }>
  signals: Record<string, ApiSignal>
  ev: EvSession[]
  incidents: ApiIncident[]
  road_users: RoadUser[]
  hazards: unknown[]
  scenario: ScenarioStatus
  provenance: { component: string; status: string; detail: string }[]
  events: SysEvent[]
  devices: { device_id: string; role: string; age_s: number | null }[]
  alerts_unacknowledged: number
  llm: { enabled: boolean; model: string | null }
}

/* ---------- topology -> world coordinates ---------- */
const K = 0.07 // layout units (800x520) -> scene units (about 56 wide)
export const toWorld = (n: { x: number; y: number }) => ({ x: (n.x - 400) * K, z: (n.y - 250) * K })

export interface EdgeGeom { id: string; ax: number; az: number; bx: number; bz: number; len: number; ux: number; uz: number; rot: number; edge: TopoEdge }
export interface TopoIndex { topo: Topo; edges: Map<string, EdgeGeom>; roads: EdgeGeom[]; intersections: { id: string; x: number; z: number }[] }
const cache = new WeakMap<Topo, TopoIndex>()
export function indexTopo(topo: Topo): TopoIndex {
  const hit = cache.get(topo)
  if (hit) return hit
  const edges = new Map<string, EdgeGeom>()
  for (const e of topo.edges) {
    const a = topo.nodes[e.src], b = topo.nodes[e.dst]
    if (!a || !b) continue
    const A = toWorld(a), B = toWorld(b)
    const dx = B.x - A.x, dz = B.z - A.z, len = Math.hypot(dx, dz) || 1
    edges.set(e.id, { id: e.id, ax: A.x, az: A.z, bx: B.x, bz: B.z, len, ux: dx / len, uz: dz / len, rot: Math.atan2(-dz / len, dx / len), edge: e })
  }
  const seen = new Set<string>()
  const roads: EdgeGeom[] = []
  for (const g of edges.values()) {
    const k = [g.edge.src, g.edge.dst].sort().join('|')
    if (!seen.has(k)) { seen.add(k); roads.push(g) }
  }
  const intersections = Object.entries(topo.nodes).filter(([, n]) => n.kind === 'intersection').map(([id, n]) => ({ id, ...toWorld(n) }))
  const idx = { topo, edges, roads, intersections }
  cache.set(topo, idx)
  return idx
}

/** Point `p` (0..1) along an edge, shifted to the right-hand lane. Rotation is the yaw for a car modelled along +x. */
export function edgePose(idx: TopoIndex, edgeId: string, p: number, lane = 1.3) {
  const g = idx.edges.get(edgeId)
  if (!g) return null
  const t = Math.min(1, Math.max(0, p))
  return { x: g.ax + (g.bx - g.ax) * t + -g.uz * lane, z: g.az + (g.bz - g.az) * t + g.ux * lane, rot: g.rot }
}

export const NODE_R = 3.6
/** Queued vehicles on an approach, parked back from the stop line, one car per counted vehicle. */
export function queuePoses(idx: TopoIndex, edgeId: string, count: number) {
  const g = idx.edges.get(edgeId)
  if (!g) return []
  const out: { x: number; z: number; rot: number }[] = []
  for (let i = 0; i < count; i++) {
    const back = NODE_R + 1.3 + i * 1.9
    if (back > g.len - 1) break
    const p = edgePose(idx, edgeId, 1 - back / g.len)
    if (p) out.push(p)
  }
  return out
}

/* ---------- snapshot for the cards ---------- */
const LAMP = { G: 'green', Y: 'yellow', R: 'red' } as const
const LIVE_ALERT = ['active', 'updated', 'redirected']
const clip = (s: string) => s.replace(/_/g, ' ').replace(/^\w/, (c) => c.toUpperCase())
const EVENT_TITLE: Record<string, string> = {
  ev_declared: 'Ambulance declared', ev_ended: 'Ambulance trip ended', ev_reroute: 'Ambulance rerouted',
  ev_location_stale: 'Ambulance location lost', ev_location_recovered: 'Ambulance location recovered',
  incident_opened: 'Incident opened', incident_resolved: 'Incident resolved', incident_acknowledged: 'Incident acknowledged',
  scenario_started: 'Scenario started', scenario_stopped: 'Scenario stopped', scenario_finished: 'Scenario finished',
  llm_fallback: 'Assistant used its fallback', backend_started: 'Backend started',
}
const eventTitle = (type: string) => EVENT_TITLE[type] ?? clip(type)

function eventKind(type: string): EventRow['kind'] {
  if (type.startsWith('ev_')) return 'emergency'
  if (type.startsWith('incident') || type.startsWith('hazard')) return 'warning'
  if (type.includes('road_user')) return 'user'
  return 'system'
}
function eventDetail(e: SysEvent): string | undefined {
  try {
    const p = e.payload ? (JSON.parse(e.payload) as Record<string, unknown>) : null
    if (!p) return undefined
    if (typeof p.approach === 'string') return `approach ${p.approach}${typeof p.type === 'string' ? ` · ${clip(p.type)}` : ''}`
    if (typeof p.id === 'string') return p.id
    if (typeof p.reason === 'string') return clip(p.reason)
  } catch { /* payload is free-form */ }
  return undefined
}
const clock = (iso: string) => new Date(iso).toLocaleTimeString([], { hour12: false })

export function liveSnapshot(ov: Overview, topo: Topo | null, extra: { priority: number; T: number }): Snap {
  const label = (id: string) => topo?.nodes[id]?.label ?? id
  const active = ov.ev.find((s) => s.status === 'active')
  const users = ov.road_users
  const warnedUsers = users.filter((u) => Object.values(u.alerts).some((s) => LIVE_ALERT.includes(s)))
  const rerouted = users.filter((u) => Object.values(u.alerts).includes('redirected')).length
  const notWarned = users.length - warnedUsers.length

  const ids = topo ? Object.entries(topo.nodes).filter(([, n]) => n.kind === 'intersection').map(([id]) => id) : Object.keys(ov.signals)
  const signals: SigState[] = ids.map((id) => {
    const s = ov.signals[id], rep = s?.reported
    const preempt = !!s?.commanded?.reason.startsWith('emergency')
    if (!rep || s.reported_stale) return { id, name: label(id), lamp: 'unknown', left: 0, preempt, detail: 'no fresh report' }
    const a = LAMP[rep.lamps.A as 'G'] ?? 'unknown'
    return { id, name: label(id), lamp: a, left: Math.round(rep.elapsed_ms / 1000), unit: 'elapsed', preempt,
      detail: `A ${LAMP[rep.lamps.A as 'G'] ?? '?'} · B ${LAMP[rep.lamps.B as 'G'] ?? '?'}` }
  })
  const anyPre = signals.some((s) => s.preempt)
  const fresh = Object.values(ov.traffic)
  const checks: Check[] = [
    { label: 'Traffic state ingestion', state: fresh.some((t) => !t.stale) ? 'ok' : fresh.length ? 'running' : 'pending' },
    { label: 'Route checks', state: active ? (active.stale ? 'running' : 'ok') : 'pending' },
    { label: 'Warning checks', state: active ? (Object.keys(active.warnings).length ? 'ok' : 'running') : 'pending' },
    { label: 'Signal coordination', state: anyPre ? 'running' : active ? 'pending' : ov.ev.length ? 'ok' : 'pending' },
  ]
  const events: EventRow[] = ov.events.slice(0, 5).map((e) => ({
    t: 0, at: Date.parse(e.ts) / 1000, label: clock(e.ts), kind: eventKind(e.type), title: eventTitle(e.type), detail: eventDetail(e),
  }))
  const sc = ov.scenario
  return {
    live: true, T: extra.T, phase: 0, emergency: !!active,
    scenarioName: sc.state === 'idle' ? 'Idle' : (sc.title ?? 'Scenario'),
    roadUsers: users.length, warned: warnedUsers.length, notWarned,
    reroutes: ov.events.filter((e) => e.type === 'ev_reroute').length, priority: extra.priority,
    delta: { warned: 0, reroutes: 0, priority: 0 },
    signals, checks, events,
    ev: { state: active ? 'enroute' : 'idle', eta: null, from: active ? label(active.origin) : '', to: active ? label(active.destination) : '' },
    stats: { onRoute: warnedUsers.length - rerouted, rerouted, notWarned, unaffected: 0, avgTravel: null, emission: null },
  }
}

/* ---------- traffic-flow buffer ---------- */
export interface FlowPt { t: number; a: number; b: number }
export function flowPoint(ov: Overview, topo: Topo | null, now: number): FlowPt {
  let a = 0, b = 0
  for (const e of topo?.edges ?? []) {
    const c = ov.traffic[e.id]
    if (!c || c.stale) continue
    if (e.movement === 'A') a += c.count
    else if (e.movement === 'B') b += c.count
  }
  return { t: now, a, b }
}
export function liveFlowSeries(buf: FlowPt[], windowSec: number, now: number) {
  const step = (windowSec * 1000) / 15
  let last = buf.length ? buf[0] : null
  return Array.from({ length: 16 }, (_, i) => {
    const hi = now - (15 - i) * step, lo = hi - step
    const pts = buf.filter((p) => p.t > lo && p.t <= hi)
    if (pts.length) last = pts[pts.length - 1]
    const ns = pts.length ? pts.reduce((s, p) => s + p.a, 0) / pts.length : last?.a ?? 0
    const ew = pts.length ? pts.reduce((s, p) => s + p.b, 0) / pts.length : last?.b ?? 0
    const ago = (15 - i) * step / 1000
    return {
      label: i === 15 ? 'now' : ago >= 60 ? `-${Math.round(ago / 60)}m` : `-${Math.round(ago)}s`,
      ns: +ns.toFixed(1), ew: +ew.toFixed(1), total: +(ns + ew).toFixed(1),
    }
  })
}

/* ---------- incidents ---------- */
export const INCIDENT_TITLE: Record<string, string> = { congestion_spike: 'Congestion spike', stalled_vehicle: 'Stalled vehicle' }
export function mapIncident(i: ApiIncident, topo: Topo | null) {
  const edge = topo?.edges.find((e) => e.id === i.approach)
  const where = edge && topo ? `${i.approach} · ${topo.nodes[edge.src]?.label ?? edge.src} → ${topo.nodes[edge.dst]?.label ?? edge.dst}` : i.approach
  const sev = i.severity === 'high' || i.severity === 'critical' ? 'high' : i.severity === 'low' ? 'low' : 'medium'
  const status = i.status === 'resolved' ? 'resolved' : i.status === 'acknowledged' ? 'acknowledged' : 'active'
  return { id: i.id, title: INCIDENT_TITLE[i.type] ?? clip(i.type), where, sev, status, at: 0, label: clock(i.detected_at) } as const
}

export function statusTone(status: string): 'ok' | 'mute' | 'warn' | 'danger' {
  const s = status.toLowerCase()
  if (s === 'built' || s === 'live' || s === 'ok') return 'ok'
  if (s === 'simulated' || s === 'idle') return 'mute'
  if (s.includes('not connected') || s.includes('offline') || s.includes('lost')) return 'danger'
  return 'warn'
}
