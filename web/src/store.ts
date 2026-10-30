import { useMemo } from 'react'
import { create } from 'zustand'
import { loadAuth, saveAuth, type Auth } from './api'
import { liveSnapshot, mapIncident, type ApiIncident, type Feed, type FlowPt, type Overview, type Topo } from './live'
import { snapshot, type Snap } from './sim'

export type Nav = 'overview' | 'stage' | 'traffic' | 'signals' | 'emergencies' | 'incidents' | 'scenarios' | 'assistant' | 'status' | 'provenance' | 'settings'
export type Layers = { vehicles: boolean; signals: boolean; route: boolean; warnings: boolean }
type Backend = { state: 'checking' | 'live' | 'demo'; intersections: number }
export interface Incident { id: number; title: string; where: string; sev: 'high' | 'medium' | 'low'; status: 'active' | 'acknowledged' | 'resolved'; at: number; label?: string }
export interface ChatMsg { role: 'user' | 'assistant'; text: string }
export interface LiveState {
  topo: Topo | null; ov: Overview | null; incidents: ApiIncident[]; priority: number
  ok: boolean; err: string | null; since: number; updated: number; flow: FlowPt[]
  /** Big-screen guest feed; null when the endpoint is unavailable (the overview's road_users still draw the cars). */
  feed: Feed | null
  /** When each road user was first seen (epoch ms); 0 = already there on the first snapshot, so it does not ripple. */
  born: Record<string, number>
}

interface S {
  T: number; setT: (t: number) => void
  nav: Nav; setNav: (n: Nav) => void
  mode: 'physical' | 'simulated'; setMode: (m: 'physical' | 'simulated') => void
  scenario: string; setScenario: (s: string) => void
  view: '2d' | '3d'; setView: (v: '2d' | '3d') => void
  recenter: number; bumpRecenter: () => void
  layers: Layers; toggleLayer: (k: keyof Layers) => void
  backend: Backend; setBackend: (b: Backend) => void
  unread: number; clearUnread: () => void
  incidents: Incident[]; addIncident: (i: Omit<Incident, 'id' | 'status'>) => void; setIncidentStatus: (id: number, st: Incident['status']) => void
  chat: ChatMsg[]; pushChat: (m: ChatMsg) => void
  quality: 'high' | 'low'; setQuality: (q: 'high' | 'low') => void
  speed: number; setSpeed: (n: number) => void
  auth: Auth | null; setAuth: (a: Auth | null) => void
  loginOpen: boolean; setLoginOpen: (o: boolean) => void
  live: LiveState; setLive: (p: Partial<LiveState>) => void
  pollNow: number; bumpPoll: () => void
}

const EMPTY_LIVE: LiveState = { topo: null, ov: null, incidents: [], priority: 0, ok: false, err: null, since: Date.now(), updated: 0, flow: [], feed: null, born: {} }

export const useStore = create<S>((set) => ({
  T: 85, setT: (T) => set({ T }),
  nav: 'overview', setNav: (nav) => set({ nav }),
  mode: 'physical', setMode: (mode) => set({ mode }),
  scenario: 'emergency', setScenario: (scenario) => set({ scenario }),
  view: '3d', setView: (view) => set({ view }),
  recenter: 0, bumpRecenter: () => set((s) => ({ recenter: s.recenter + 1 })),
  layers: { vehicles: true, signals: true, route: true, warnings: true },
  toggleLayer: (k) => set((s) => ({ layers: { ...s.layers, [k]: !s.layers[k] } })),
  backend: { state: 'checking', intersections: 0 }, setBackend: (backend) => set({ backend }),
  unread: 3, clearUnread: () => set({ unread: 0 }),
  incidents: [
    { id: 3, title: 'Pothole reported', where: 'East Gate approach', sev: 'medium', status: 'active', at: 40 },
    { id: 2, title: 'Stalled vehicle', where: 'Central, northbound', sev: 'high', status: 'acknowledged', at: 25 },
    { id: 1, title: 'Signal flicker', where: 'I4 — South Gate', sev: 'low', status: 'resolved', at: 8 },
  ],
  addIncident: (i) => set((s) => ({ incidents: [{ ...i, id: Math.max(0, ...s.incidents.map((x) => x.id)) + 1, status: 'active' }, ...s.incidents] })),
  setIncidentStatus: (id, status) => set((s) => ({ incidents: s.incidents.map((i) => (i.id === id ? { ...i, status } : i)) })),
  chat: [{ role: 'assistant', text: 'Hi, I can answer questions about the ambulance, signals, warnings, incidents and the current scenario. This is a demo assistant that reads the dashboard state.' }],
  pushChat: (m) => set((s) => ({ chat: [...s.chat, m] })),
  quality: 'high', setQuality: (quality) => set({ quality }),
  speed: 1, setSpeed: (speed) => set({ speed }),
  auth: loadAuth(),
  setAuth: (auth) => { saveAuth(auth); set({ auth, live: { ...EMPTY_LIVE, since: Date.now() } }) },
  loginOpen: false, setLoginOpen: (loginOpen) => set({ loginOpen }),
  live: { ...EMPTY_LIVE },
  setLive: (p) => set((s) => ({ live: { ...s.live, ...p } })),
  pollNow: 0, bumpPoll: () => set((s) => ({ pollNow: s.pollNow + 1 })),
}))

/** True once we are signed in AND the backend has answered, so every screen can show real data. */
export function useIsLive() {
  return useStore((s) => !!s.auth && !!s.live.ov)
}

export function useSnap(): Snap {
  const T = useStore((s) => s.T)
  const scenario = useStore((s) => s.scenario)
  const live = useStore((s) => s.live)
  const auth = useStore((s) => s.auth)
  return useMemo(() => {
    if (auth && live.ov) {
      const sc = live.ov.scenario
      const t = sc.state !== 'idle' && sc.t != null ? Math.round(sc.t) : Math.floor((Date.now() - live.since) / 1000)
      return liveSnapshot(live.ov, live.topo, { priority: live.priority, T: t })
    }
    return snapshot(T, scenario)
  }, [T, scenario, live, auth])
}

/** Incident rows for the Incidents screen: backend incidents when live, local demo ones otherwise. */
export function useIncidentRows(): Incident[] {
  const demo = useStore((s) => s.incidents)
  const auth = useStore((s) => s.auth)
  const live = useStore((s) => s.live)
  return useMemo(() => (auth && live.ov ? live.incidents.map((i) => mapIncident(i, live.topo) as Incident) : demo), [demo, auth, live])
}
