// Deterministic demo simulation. Everything is a pure function of the clock T (seconds),
// so the 3D scene (smooth, per frame) and the cards (per second) always agree.
export const CYCLE = 60
export const EDGE = 24
export const ROUTE_Z = -6
export const AMB_RUN = 50 // seconds for the ambulance to cross West Gate -> East Gate
export const AMB_SPEED = (2 * EDGE) / AMB_RUN

export const clock = { T: 85 }

export const mmss = (s: number) => {
  const t = Math.max(0, Math.floor(s))
  return `${String(Math.floor(t / 60)).padStart(2, '0')}:${String(t % 60).padStart(2, '0')}`
}
export const phaseOf = (T: number) => ((T % CYCLE) + CYCLE) % CYCLE
export const ambulanceX = (phase: number) => -EDGE + 2 * EDGE * Math.min(1, Math.max(0, phase / AMB_RUN))

export type Lamp = 'green' | 'yellow' | 'red'
export interface Sig { id: string; name: string; x: number; z: number; offset: number; onRoute: boolean }
export const SIGS: Sig[] = [
  { id: 'I1', name: 'West Gate', x: -6, z: -6, offset: 58, onRoute: true },
  { id: 'I2', name: 'Central', x: 6, z: -6, offset: 76, onRoute: true },
  { id: 'I3', name: 'East Gate', x: 6, z: 6, offset: 40, onRoute: false },
  { id: 'I4', name: 'South Gate', x: -6, z: 6, offset: 10, onRoute: false },
]
export interface SigState {
  id: string; name: string; lamp: Lamp | 'unknown'; left: number; preempt: boolean
  unit?: 'left' | 'elapsed'; detail?: string // live: seconds since the phase began, and "A green · B red"
}

export function sigState(s: Sig, T: number, emergency: boolean): SigState {
  const phase = phaseOf(T)
  if (emergency && s.onRoute && phase >= 8 && phase < AMB_RUN) {
    const ax = ambulanceX(phase)
    if (ax < s.x + 4) return { id: s.id, name: s.name, lamp: 'green', left: Math.ceil((s.x + 4 - ax) / AMB_SPEED), preempt: true }
  }
  const p = (((T + s.offset) % 90) + 90) % 90 // 40s green, 8s yellow, 42s red
  if (p < 40) return { id: s.id, name: s.name, lamp: 'green', left: Math.ceil(40 - p), preempt: false }
  if (p < 48) return { id: s.id, name: s.name, lamp: 'yellow', left: Math.ceil(48 - p), preempt: false }
  return { id: s.id, name: s.name, lamp: 'red', left: Math.ceil(90 - p), preempt: false }
}

export type CarGroup = 'route' | 'reroute' | 'unwarned' | 'ambient'
export interface CarDef {
  id: string; tag: boolean; group: CarGroup
  axis: 'h' | 'v'; lane: number; dir: 1 | -1; speed: number; start: number
}
const car = (id: string, tag: boolean, group: CarGroup, axis: 'h' | 'v', lane: number, dir: 1 | -1, speed: number, start: number): CarDef =>
  ({ id, tag, group, axis, lane, dir, speed, start })
export const CARS: CarDef[] = [
  car('FS-42-K3F', true, 'route', 'h', -7.4, 1, 2.0, 4),
  car('TN-07-K3F', true, 'route', 'h', -7.4, 1, 1.6, 16),
  car('FS-31-M2A', false, 'route', 'h', -4.6, -1, 1.8, 8),
  car('TN-22-B9C', false, 'route', 'h', -4.6, -1, 2.2, -12),
  car('FS-08-R5T', false, 'route', 'h', -7.4, 1, 1.2, -20),
  car('FS-11-Q8D', true, 'reroute', 'v', -7.3, 1, 1.7, -10),
  car('TN-15-L4W', false, 'reroute', 'v', -4.7, -1, 2.0, 6),
  car('TN-09-A21', true, 'unwarned', 'h', 4.7, 1, 1.7, 0),
  car('FS-27-N6P', false, 'unwarned', 'v', 4.7, 1, 2.1, 8),
  car('TN-33-J1X', false, 'unwarned', 'h', 7.3, -1, 1.5, 10),
  car('A1', false, 'ambient', 'h', 7.3, 1, 2.4, -6),
  car('A2', false, 'ambient', 'v', 7.3, -1, 1.9, 14),
  car('A3', false, 'ambient', 'v', 4.7, -1, 1.4, -18),
  car('A4', false, 'ambient', 'h', 4.7, -1, 2.0, 20),
]
const LOOP = EDGE + 4
export const carAlong = (c: CarDef, T: number) =>
  ((((c.start + c.dir * c.speed * T + LOOP) % (2 * LOOP)) + 2 * LOOP) % (2 * LOOP)) - LOOP

export type CarStatus = 'neutral' | 'warned' | 'rerouted' | 'unwarned'
export function carStatus(c: CarDef, T: number, emergency: boolean): CarStatus {
  if (!emergency) return 'neutral'
  const ph = phaseOf(T)
  if (c.group === 'route') return ph >= 12 ? 'warned' : 'neutral'
  if (c.group === 'reroute') return ph >= 18 ? 'rerouted' : ph >= 12 ? 'warned' : 'neutral'
  if (c.group === 'unwarned') return 'unwarned'
  return 'neutral'
}

export interface EventItem { t: number; kind: 'emergency' | 'warning' | 'system' | 'vehicle' | 'user'; title: string; detail?: string }
const SCHEDULE: EventItem[] = [
  { t: 0, kind: 'emergency', title: 'Ambulance dispatched', detail: 'West Gate → East Gate' },
  { t: 4, kind: 'user', title: 'New road user joined', detail: 'South Gate → North Gate' },
  { t: 8, kind: 'system', title: 'Signal coordination started' },
  { t: 12, kind: 'warning', title: 'Warning sent to 7 road users' },
  { t: 18, kind: 'vehicle', title: 'Route suggestion accepted', detail: 'User FS-42-K3F' },
  { t: AMB_RUN, kind: 'system', title: 'Corridor released', detail: 'Ambulance reached East Gate' },
]

export const SCENARIOS = [
  { id: 'emergency', name: 'Emergency Response' },
  { id: 'normal', name: 'Normal Flow' },
  { id: 'hazard', name: 'Road Hazard' },
  { id: 'signal', name: 'Signal Failure' },
] as const

export interface Check { label: string; state: 'ok' | 'running' | 'pending' }
export type EventRow = EventItem & { at: number; label?: string }
export interface Snap {
  live: boolean
  T: number; phase: number; emergency: boolean; scenarioName: string
  roadUsers: number; warned: number; notWarned: number; reroutes: number; priority: number
  delta: { warned: number; reroutes: number; priority: number }
  signals: SigState[]; checks: Check[]; events: EventRow[]
  ev: { state: 'idle' | 'enroute' | 'arrived'; eta: number | null; from: string; to: string }
  stats: { onRoute: number; rerouted: number; notWarned: number; unaffected: number; avgTravel: number | null; emission: number | null }
}

export function snapshot(T: number, scenario: string): Snap {
  const emergency = scenario === 'emergency'
  const phase = phaseOf(T)
  const base = T - phase
  const kpiAt = (p: number) => ({
    warned: emergency && p >= 12 ? 7 : 0,
    reroutes: emergency ? (p >= 18 ? 4 : p >= 12 ? 2 : 0) : 0,
    priority: emergency ? Math.min(25, Math.floor(p / 2)) : 0,
  })
  const k = kpiAt(phase), prev = kpiAt(Math.max(0, phase - 6))
  const sc = SCENARIOS.find((s) => s.id === scenario)?.name ?? scenario
  const events: EventRow[] = emergency
    ? SCHEDULE.filter((e) => e.t <= phase).reverse().slice(0, 5).map((e) => ({ ...e, at: base + e.t }))
    : [
        { t: 0, kind: 'system', title: `Scenario: ${sc}`, detail: 'Monitoring city traffic', at: base },
        { t: 0, kind: 'user', title: 'New road user joined', detail: 'South Gate → North Gate', at: base - 3 },
      ]
  const checks: Check[] = emergency
    ? [
        { label: 'Traffic state ingestion', state: phase >= 1 ? 'ok' : 'running' },
        { label: 'Route checks', state: phase >= 4 ? 'ok' : phase >= 1 ? 'running' : 'pending' },
        { label: 'Warning checks', state: phase >= 20 ? 'ok' : phase >= 12 ? 'running' : 'pending' },
        { label: 'Signal coordination', state: phase >= AMB_RUN ? 'ok' : phase >= 8 ? 'running' : 'pending' },
      ]
    : ['Traffic state ingestion', 'Route checks', 'Warning checks', 'Signal coordination'].map((label) => ({ label, state: 'ok' as const }))
  const rerouted = emergency && phase >= 18 ? 2 : 0
  const notWarned = emergency ? 3 : 0
  return {
    live: false, T, phase, emergency, scenarioName: sc,
    roadUsers: 10, warned: k.warned, notWarned, reroutes: k.reroutes, priority: k.priority,
    delta: { warned: k.warned - prev.warned, reroutes: k.reroutes - prev.reroutes, priority: k.priority - prev.priority },
    signals: SIGS.map((s) => sigState(s, T, emergency)),
    checks, events,
    ev: { state: !emergency ? 'idle' : phase >= AMB_RUN ? 'arrived' : 'enroute', eta: Math.max(0, Math.ceil(AMB_RUN - phase)), from: 'West Gate', to: 'East Gate' },
    stats: { onRoute: k.warned - rerouted, rerouted, notWarned, unaffected: 10 - k.warned - notWarned, avgTravel: emergency ? 2.4 : 2.8, emission: emergency ? 12 : 9 },
  }
}

export function flowSeries(T: number, windowSec: number) {
  const step = windowSec / 15
  const anchor = Math.floor(T / 5) * 5
  return Array.from({ length: 16 }, (_, i) => {
    const ago = (15 - i) * step
    const s = anchor - ago
    const ns = 14 + 8 * Math.sin(s / 23) + 4 * Math.sin(s / 7)
    const ew = 9 + 7 * Math.sin(s / 31 + 1) + 3 * Math.sin(s / 11)
    return {
      label: i === 15 ? 'now' : ago >= 60 ? `-${Math.round(ago / 60)}m` : `-${Math.round(ago)}s`,
      ns: +ns.toFixed(1), ew: +ew.toFixed(1), total: +(ns + ew).toFixed(1),
    }
  })
}
