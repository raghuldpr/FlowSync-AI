import { lazy, Suspense, useState } from 'react'
import { Crosshair, Layers as LayersIcon } from 'lucide-react'
import { V2vBanner } from './guests'
import { useStore, type Layers } from './store'

const Scene = lazy(() => import('./Scene'))

const LAYER_LABELS: [keyof Layers, string][] = [
  ['vehicles', 'Vehicles'], ['signals', 'Signals'], ['route', 'Emergency route'], ['warnings', 'Warning zones & tags'],
]

function Ctl({ label, active, onClick, children }: { label: string; active?: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      type="button" aria-label={label} title={label} aria-pressed={active} onClick={onClick}
      className={`grid h-10 w-10 place-items-center rounded-lg text-[13px] font-semibold backdrop-blur transition ${
        active ? 'bg-primary text-white shadow-lg shadow-primary/40' : 'bg-navy/70 text-slate-200 ring-1 ring-white/10 hover:bg-mapblue'
      }`}
    >
      {children}
    </button>
  )
}

export default function MapPanel({ className = '' }: { className?: string }) {
  const view = useStore((s) => s.view)
  const setView = useStore((s) => s.setView)
  const bump = useStore((s) => s.bumpRecenter)
  const layers = useStore((s) => s.layers)
  const toggle = useStore((s) => s.toggleLayer)
  const scenario = useStore((s) => s.scenario)
  const live = useStore((s) => (s.auth && s.live.ov ? s.live.ov : null))
  const [open, setOpen] = useState(false)
  const emergency = live ? live.ev.some((e) => e.status === 'active') : scenario === 'emergency'
  const offline = useStore((s) => !!s.auth && !s.live.ok && s.live.updated > 0)
  const sub = live
    ? offline ? 'Backend not reachable · showing the last data received' : `Live from the FlowSync backend${emergency ? ' · ambulance on the road' : ''}`
    : emergency ? 'Real-time traffic simulation with emergency response' : 'Real-time traffic simulation'

  return (
    <section aria-label="Live 3D city view" className={`relative overflow-hidden rounded-xl bg-navy ring-1 ring-mapblue ${className}`}>
      <Suspense fallback={<div className="grid h-full place-items-center text-sm text-slate-400">Loading 3D scene…</div>}>
        <Scene />
      </Suspense>

      <div className="pointer-events-none absolute left-3 top-3 max-w-[calc(100%-5rem)] rounded-lg bg-navy/70 px-3.5 py-2.5 ring-1 ring-white/10 backdrop-blur">
        <div className="flex items-center gap-2 text-[15px] font-semibold text-white">
          <span className="live-dot h-2 w-2 rounded-full bg-ok" aria-hidden /> Live 3D City View
        </div>
        <p className="mt-0.5 text-[11.5px] text-slate-300">
          {sub}
        </p>
      </div>

      {live && <V2vBanner />}

      <div className="absolute right-3 top-3 flex flex-col gap-2">
        <Ctl label="2D view" active={view === '2d'} onClick={() => setView('2d')}>2D</Ctl>
        <Ctl label="3D view" active={view === '3d'} onClick={() => setView('3d')}>3D</Ctl>
        <Ctl label="Recenter on active incident" onClick={bump}><Crosshair size={18} /></Ctl>
        <div className="relative">
          <Ctl label="Map layers" active={open} onClick={() => setOpen((o) => !o)}><LayersIcon size={18} /></Ctl>
          {open && (
            <div className="absolute right-12 top-0 w-52 rounded-lg bg-navy/90 p-2 ring-1 ring-white/10 backdrop-blur">
              {LAYER_LABELS.map(([k, label]) => (
                <label key={k} className="flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 text-[12.5px] text-slate-200 hover:bg-mapblue">
                  <input type="checkbox" checked={layers[k]} onChange={() => toggle(k)} className="accent-primary" /> {label}
                </label>
              ))}
            </div>
          )}
        </div>
      </div>

      <div className="pointer-events-none absolute bottom-3 left-3 hidden gap-3 rounded-lg bg-navy/70 px-3 py-1.5 text-[11px] text-slate-300 ring-1 ring-white/10 backdrop-blur sm:flex">
        <Dot c="bg-white" t="Ambulance" /><Dot c="bg-ok" t="Green corridor" /><Dot c="bg-danger" t="Warned" /><Dot c="bg-[#22d47f]" t="Rerouted" />{live && <Dot c="bg-slate" t="Not warned" />}
      </div>
    </section>
  )
}

function Dot({ c, t }: { c: string; t: string }) {
  return <span className="flex items-center gap-1.5"><i className={`h-2 w-2 rounded-full ${c}`} />{t}</span>
}
