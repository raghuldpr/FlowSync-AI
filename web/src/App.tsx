import { useEffect, useState } from 'react'
import { Assistant, Incidents, Provenance, Scenarios, Settings, SystemStatus, Traffic } from './Screens'
import { BuiltVsSimulated, Card, CityStats, CurrentEvent, KpiRow, RecentEvents, SelfChecks, SignalRow, SignalStatus, TrafficFlow } from './Cards'
import { GuestsCard } from './guests'
import MapPanel from './MapPanel'
import { Header, LoginDialog, Sidebar } from './Shell'
import { useLivePoller } from './useLive'
import { clock, mmss } from './sim'
import { useSnap, useStore } from './store'

function useSimClock() {
  const setT = useStore((s) => s.setT)
  useEffect(() => {
    let raf = 0, last = performance.now(), shown = Math.floor(clock.T)
    const tick = (now: number) => {
      clock.T += Math.min(0.25, (now - last) / 1000) * useStore.getState().speed
      last = now
      if (Math.floor(clock.T) !== shown) { shown = Math.floor(clock.T); setT(shown) }
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [setT])
}

// Public, unauthenticated endpoint: tells us if the FlowSync backend is reachable.
function useBackendProbe() {
  const setBackend = useStore((s) => s.setBackend)
  useEffect(() => {
    let alive = true
    const probe = async () => {
      try {
        const r = await fetch('/api/public/topology')
        const j = r.ok ? await r.json() : null
        if (alive) setBackend(j?.nodes ? { state: 'live', intersections: Object.values(j.nodes as Record<string, { kind: string }>).filter((n) => n.kind === 'intersection').length } : { state: 'demo', intersections: 0 })
      } catch { if (alive) setBackend({ state: 'demo', intersections: 0 }) }
    }
    probe()
    const id = setInterval(probe, 10000)
    return () => { alive = false; clearInterval(id) }
  }, [setBackend])
}

function Overview() {
  const s = useSnap()
  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
      <div className="@container flex min-w-0 flex-col gap-4">
        <MapPanel className="h-[clamp(340px,54vh,580px)]" />
        <KpiRow s={s} />
        <div className="grid gap-4 @2xl:grid-cols-2 @5xl:grid-cols-3">
          <TrafficFlow T={s.T} className="@2xl:col-span-2 @5xl:col-span-1" /><SignalStatus s={s} /><RecentEvents s={s} />
        </div>
      </div>
      <div className="flex flex-col gap-4">
        <CurrentEvent s={s} /><GuestsCard /><SelfChecks s={s} /><BuiltVsSimulated /><CityStats s={s} />
      </div>
    </div>
  )
}

function Signals() {
  const s = useSnap()
  return <Card title="All signals"><ul className="divide-y divide-line">{s.signals.map((g) => <SignalRow key={g.id} g={g} />)}</ul></Card>
}

function Emergencies() {
  const s = useSnap()
  return (
    <div className="grid gap-4 lg:grid-cols-[1fr_360px]">
      <MapPanel className="h-[60vh]" />
      <div className="flex flex-col gap-4"><CurrentEvent s={s} /><GuestsCard /><SelfChecks s={s} />
        <Card title="Preemption"><ul className="divide-y divide-line">{s.signals.filter((g) => g.preempt).map((g) => <SignalRow key={g.id} g={g} />)}
          {!s.signals.some((g) => g.preempt) && <li className="py-2 text-[13px] text-muted">No signals are currently held green.</li>}</ul></Card></div>
    </div>
  )
}

export default function App() {
  useSimClock()
  useBackendProbe()
  useLivePoller()
  const nav = useStore((s) => s.nav)
  const T = useStore((s) => s.T)
  const [menu, setMenu] = useState(false)
  return (
    <div className="flex h-full flex-col bg-app">
      <Header onMenu={() => setMenu(true)} />
      <LoginDialog />
      <div className="flex min-h-0 flex-1">
        <Sidebar open={menu} onClose={() => setMenu(false)} />
        <main className="min-w-0 flex-1 overflow-y-auto p-3 md:p-4" aria-label={`FlowSync ${nav}, elapsed ${mmss(T)}`}>
          {nav === 'overview' && <Overview />}
          {nav === 'stage' && <MapPanel className="h-full min-h-[420px]" />}
          {nav === 'signals' && <Signals />}
          {nav === 'emergencies' && <Emergencies />}
          {nav === 'traffic' && <Traffic />}
          {nav === 'incidents' && <Incidents />}
          {nav === 'scenarios' && <Scenarios />}
          {nav === 'assistant' && <Assistant />}
          {nav === 'status' && <SystemStatus />}
          {nav === 'provenance' && <Provenance />}
          {nav === 'settings' && <Settings />}
        </main>
      </div>
    </div>
  )
}
