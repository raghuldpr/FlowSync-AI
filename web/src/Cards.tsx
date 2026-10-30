import { useMemo, useState, type ReactNode } from 'react'
import { Area, CartesianGrid, ComposedChart, Line, Pie, PieChart, Cell, ResponsiveContainer, XAxis, YAxis, Tooltip } from 'recharts'
import {
  Ambulance, ArrowDown, ArrowUp, Car, Check, CircleCheck, Clock, Leaf, Minus, Share2, ShieldAlert, Siren, TriangleAlert, UserPlus, Users, Zap,
} from 'lucide-react'
import { liveFlowSeries, statusTone } from './live'
import { flowSeries, mmss, type EventRow, type Snap } from './sim'
import { useStore } from './store'

export function Card({ title, right, children, className = '' }: { title?: string; right?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`card p-4 ${className}`}>
      {title && (
        <header className="mb-3 flex items-center justify-between gap-2">
          <h2 className="text-[15px] font-semibold">{title}</h2>
          {right}
        </header>
      )}
      {children}
    </section>
  )
}

export function Badge({ tone, children }: { tone: 'ok' | 'warn' | 'danger' | 'info' | 'mute'; children: ReactNode }) {
  const cls = {
    ok: 'bg-ok/10 text-emerald-700', warn: 'bg-warn/15 text-amber-700', danger: 'bg-danger/10 text-red-600',
    info: 'bg-primary/10 text-primary', mute: 'bg-slate-100 text-muted',
  }[tone]
  return <span className={`inline-flex items-center rounded-md px-2 py-0.5 text-[11px] font-semibold ${cls}`}>{children}</span>
}

const ViewAll = ({ onClick }: { onClick: () => void }) => (
  <button type="button" onClick={onClick} className="text-xs font-medium text-primary hover:underline">View all →</button>
)

/* ----- KPI row ----- */
function Trend({ d }: { d: number }) {
  if (!d) return <span className="inline-flex items-center text-[11px] text-slate"><Minus size={11} /></span>
  const up = d > 0
  return <span className={`inline-flex items-center text-[11px] font-semibold ${up ? 'text-ok' : 'text-danger'}`}>{up ? <ArrowUp size={11} /> : <ArrowDown size={11} />}{Math.abs(d)}</span>
}

export function KpiRow({ s }: { s: Snap }) {
  const items = [
    { icon: Car, tone: 'text-primary bg-primary/10', v: s.roadUsers, d: 0, label: 'Road users', cap: 'Active in city' },
    { icon: TriangleAlert, tone: 'text-danger bg-danger/10', v: s.warned, d: s.delta.warned, label: 'Warned users', cap: 'Received alerts' },
    { icon: Users, tone: 'text-slate-500 bg-slate-100', v: s.notWarned, d: 0, label: 'Not warned', cap: 'Not on ambulance path' },
    { icon: Share2, tone: 'text-ok bg-ok/10', v: s.reroutes, d: s.delta.reroutes, label: 'Reroutes', cap: 'Route suggestions' },
    { icon: Zap, tone: 'text-ink bg-slate-100', v: s.priority, d: s.delta.priority, label: 'Priority decisions', cap: 'Signal adjustments' },
  ]
  return (
    <div className="grid grid-cols-2 gap-3 @3xl:grid-cols-5">
      {items.map((k) => (
        <div key={k.label} className="card p-3.5 last:odd:col-span-2 @3xl:last:odd:col-span-1">
          <div className="flex items-start justify-between">
            <span className={`grid h-9 w-9 place-items-center rounded-lg ${k.tone}`}><k.icon size={18} /></span>
            <Trend d={k.d} />
          </div>
          <div className="tnum mt-2.5 text-[28px] font-bold leading-none">{k.v}</div>
          <div className="mt-1.5 text-[13px] font-medium leading-tight">{k.label}</div>
          <div className="text-[11px] leading-tight text-muted">{k.cap}</div>
        </div>
      ))}
    </div>
  )
}

/* ----- Traffic flow ----- */
const WINDOWS = [{ l: 'Last 5 minutes', s: 300 }, { l: 'Last 15 minutes', s: 900 }, { l: 'Last 60 minutes', s: 3600 }]
export function TrafficFlow({ T, className = '' }: { T: number; className?: string }) {
  const [w, setW] = useState(900)
  const liveFlow = useStore((s) => (s.auth && s.live.ov ? s.live.flow : null))
  const tick = Math.floor(T / 5)
  const data = useMemo(() => (liveFlow ? liveFlowSeries(liveFlow, w, Date.now()) : flowSeries(T, w)), [tick, w, liveFlow]) // eslint-disable-line react-hooks/exhaustive-deps
  const names = liveFlow ? ['Ring roads (A)', 'External roads (B)', 'Total'] : ['North-South', 'East-West', 'Total']
  return (
    <Card
      title="Live Traffic Flow"
      className={className}
      right={
        <select aria-label="Time range" value={w} onChange={(e) => setW(+e.target.value)} className="rounded-md border border-line bg-white px-2 py-1 text-xs">
          {WINDOWS.map((o) => <option key={o.s} value={o.s}>{o.l}</option>)}
        </select>
      }
    >
      <div className="mb-1 flex gap-3 text-[11px] text-muted">
        {[[names[0], '#1677e8'], [names[1], '#ef4444'], [names[2], '#94a3b8']].map(([n, c]) => (
          <span key={n} className="flex items-center gap-1"><i className="h-2 w-2 rounded-full" style={{ background: c }} />{n}</span>
        ))}
        {liveFlow && <span className="ml-auto">queued vehicles</span>}
      </div>
      <div className="h-44">
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={data} margin={{ top: 4, right: 18, left: -22, bottom: 0 }}>
            <CartesianGrid stroke="#e5ecf4" vertical={false} />
            <XAxis dataKey="label" tick={{ fontSize: 10, fill: '#64748b' }} tickLine={false} axisLine={false} interval={2} />
            <YAxis domain={liveFlow ? [0, (m: number) => Math.max(8, Math.ceil(m))] : [0, 40]} ticks={liveFlow ? undefined : [0, 10, 20, 30, 40]} allowDecimals={false} tick={{ fontSize: 10, fill: '#64748b' }} tickLine={false} axisLine={false} />
            <Tooltip contentStyle={{ fontSize: 12, borderRadius: 8 }} />
            <Area type="monotone" dataKey="total" stroke="#94a3b8" fill="#94a3b8" fillOpacity={0.14} strokeWidth={1.5} isAnimationActive={false} />
            <Line type="monotone" dataKey="ns" stroke="#1677e8" strokeWidth={2} dot={false} isAnimationActive={false} />
            <Line type="monotone" dataKey="ew" stroke="#ef4444" strokeWidth={2} dot={false} isAnimationActive={false} />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </Card>
  )
}

/* ----- Signal status ----- */
const LAMP_CLS = { green: 'bg-ok', yellow: 'bg-warn', red: 'bg-danger', unknown: 'bg-slate' }
const LAMP_TONE = { green: 'ok', yellow: 'warn', red: 'danger', unknown: 'mute' } as const
export function SignalRow({ g }: { g: Snap['signals'][number] }) {
  return (
    <li className="flex items-center gap-3 py-2">
      <span className="flex h-8 w-5 flex-col items-center justify-center gap-[3px] rounded bg-ink" aria-hidden>
        {(['red', 'yellow', 'green'] as const).map((l) => <i key={l} className={`h-1.5 w-1.5 rounded-full ${g.lamp === l ? LAMP_CLS[l] : 'bg-slate-600'}`} />)}
      </span>
      <span className="min-w-0 flex-1 leading-tight">
        <span className="block truncate text-[13px] font-medium">{g.id} — {g.name}</span>
        {g.detail && <span className="block truncate text-[11px] text-muted">{g.detail}</span>}
      </span>
      {g.preempt && <Badge tone="info">Priority</Badge>}
      <Badge tone={LAMP_TONE[g.lamp]}>{g.lamp[0].toUpperCase() + g.lamp.slice(1)}</Badge>
      <span className="tnum w-9 text-right text-[13px] text-muted" title={g.unit === 'elapsed' ? 'Seconds since this phase began' : 'Seconds remaining'}>{g.lamp === 'unknown' ? '—' : `${g.left}s`}</span>
    </li>
  )
}
export function SignalStatus({ s }: { s: Snap }) {
  const nav = useStore((x) => x.setNav)
  return (
    <Card title="Signal Status" right={<ViewAll onClick={() => nav('signals')} />}>
      <ul className="divide-y divide-line">{s.signals.map((g) => <SignalRow key={g.id} g={g} />)}</ul>
    </Card>
  )
}

/* ----- Events ----- */
const EV_ICON = { emergency: Siren, warning: TriangleAlert, system: ShieldAlert, vehicle: Car, user: UserPlus } as const
const EV_TONE = { emergency: 'bg-danger/10 text-danger', warning: 'bg-warn/15 text-amber-600', system: 'bg-primary/10 text-primary', vehicle: 'bg-primary/10 text-primary', user: 'bg-slate-100 text-slate-600' } as const
export function EventList({ events }: { events: EventRow[] }) {
  return (
    <ul className="space-y-2.5">
      {events.map((e) => {
        const I = EV_ICON[e.kind]
        return (
          <li key={e.at + e.title} className="flex items-start gap-3">
            <span className="tnum w-[3.4rem] shrink-0 pt-0.5 text-[11px] text-muted">{e.label ?? mmss(e.at)}</span>
            <span className={`grid h-7 w-7 shrink-0 place-items-center rounded-full ${EV_TONE[e.kind]}`}><I size={14} /></span>
            <div className="min-w-0 text-[12.5px] leading-snug"><div className="font-medium">{e.title}</div>{e.detail && <div className="text-muted">{e.detail}</div>}</div>
          </li>
        )
      })}
    </ul>
  )
}
export function RecentEvents({ s }: { s: Snap }) {
  const nav = useStore((x) => x.setNav)
  return <Card title="Recent Events" right={<ViewAll onClick={() => nav('incidents')} />}><EventList events={s.events} /></Card>
}

/* ----- Right column ----- */
export function CurrentEvent({ s }: { s: Snap }) {
  const arrived = s.ev.state === 'arrived'
  return (
    <Card title="Current Event" right={<span className="tnum text-sm text-muted">{mmss(s.T)}</span>}>
      {s.emergency ? (
        <>
          <div className="flex items-center gap-3 rounded-xl bg-danger/5 p-3">
            <span className="grid h-11 w-11 place-items-center rounded-xl bg-danger/10 text-danger"><Ambulance size={24} /></span>
            <div>
              <div className="text-[15px] font-semibold">{arrived ? 'Ambulance arrived' : 'Ambulance dispatched'}</div>
              <div className="text-[12.5px] text-muted">{arrived ? `Reached ${s.ev.to}` : `Heading from ${s.ev.from} to ${s.ev.to}`}</div>
            </div>
          </div>
          <div className={`mt-3 flex items-start gap-3 rounded-xl p-3 ${arrived ? 'bg-slate-50' : 'bg-ok/10'}`}>
            <CircleCheck size={20} className={arrived ? 'text-slate' : 'text-ok'} />
            <div>
              <div className="text-[13.5px] font-semibold">{arrived ? 'Green corridor released' : 'Green corridor active'}</div>
              <div className="text-[12px] text-muted">{arrived ? 'Signals returned to normal timing.' : `Signals are being coordinated along the route.${s.ev.eta != null ? ` ETA ${s.ev.eta}s` : ''}`}</div>
            </div>
          </div>
        </>
      ) : (
        <div className="rounded-xl bg-slate-50 p-3">
          <div className="text-[15px] font-semibold">{s.scenarioName}</div>
          <div className="text-[12.5px] text-muted">No active emergency. {s.live ? 'Start the ambulance scenario from the Scenarios screen.' : 'Monitoring city traffic.'}</div>
        </div>
      )}
    </Card>
  )
}

export function SelfChecks({ s }: { s: Snap }) {
  const ok = s.checks.filter((c) => c.state === 'ok').length
  return (
    <Card title="Self-Checks" right={<span className="tnum text-sm text-muted">({ok}/{s.checks.length})</span>}>
      <ul className="space-y-2.5">
        {s.checks.map((c) => (
          <li key={c.label} className="flex items-center gap-3 text-[13px]">
            <span className={`grid h-5 w-5 place-items-center rounded-full text-white ${c.state === 'ok' ? 'bg-ok' : c.state === 'running' ? 'bg-warn' : 'bg-slate-300'}`}>
              {c.state === 'ok' ? <Check size={12} strokeWidth={3} /> : c.state === 'running' ? <Clock size={11} /> : <Minus size={11} />}
            </span>
            <span className="flex-1">{c.label}</span>
            <span className={`text-xs font-medium ${c.state === 'ok' ? 'text-ok' : c.state === 'running' ? 'text-amber-600' : 'text-muted'}`}>
              {c.state === 'ok' ? 'OK' : c.state === 'running' ? 'Running' : 'Pending'}
            </span>
          </li>
        ))}
      </ul>
    </Card>
  )
}

export const COMPONENTS: [string, boolean][] = [
  ['Traffic controller', true], ['Signal controller', true], ['Ambulance simulation', true],
  ['Traffic flow (background)', false], ['Weather system', false],
]
/** Component list: the backend's provenance table when signed in, the fixed demo list otherwise. */
export function useComponentRows(): { name: string; status: string; detail?: string }[] {
  const ov = useStore((s) => (s.auth ? s.live.ov : null))
  return useMemo(() => (ov ? ov.provenance.map((p) => ({ name: p.component, status: p.status, detail: p.detail })) : COMPONENTS.map(([name, built]) => ({ name, status: built ? 'Built' : 'Simulated' }))), [ov])
}
export function BuiltVsSimulated() {
  const rows = useComponentRows()
  return (
    <Card title="Built vs. Simulated">
      <ul className="space-y-2">
        {rows.map((r) => (
          <li key={r.name} className="flex items-center justify-between gap-2 text-[13px]" title={r.detail}><span className="min-w-0 truncate">{r.name}</span><span className="shrink-0"><Badge tone={statusTone(r.status)}>{r.status}</Badge></span></li>
        ))}
      </ul>
    </Card>
  )
}

export function CityStats({ s }: { s: Snap }) {
  const { onRoute, rerouted, notWarned, unaffected } = s.stats
  const data = [
    { n: 'On route', v: onRoute, c: '#1677e8' }, { n: 'Rerouted', v: rerouted, c: '#10b981' },
    { n: 'Not warned', v: notWarned, c: '#94a3b8' }, { n: 'Unaffected', v: unaffected || (onRoute + rerouted + notWarned === 0 ? 1 : 0), c: '#e5ecf4' },
  ]
  return (
    <Card title="City Statistics" right={<ScenarioFilter />}>
      <div className="flex items-center gap-4">
        <div className="relative h-32 w-32 shrink-0">
          <ResponsiveContainer width="100%" height="100%">
            <PieChart>
              <Pie data={data} dataKey="v" innerRadius={42} outerRadius={60} paddingAngle={1} stroke="none" isAnimationActive={false}>
                {data.map((d) => <Cell key={d.n} fill={d.c} />)}
              </Pie>
            </PieChart>
          </ResponsiveContainer>
          <div className="pointer-events-none absolute inset-0 grid place-items-center text-center">
            <div><div className="tnum text-2xl font-bold leading-none">{s.roadUsers}</div><div className="text-[10px] text-muted">Road users</div></div>
          </div>
        </div>
        <ul className="flex-1 space-y-1.5 text-[12.5px]">
          {data.slice(0, 3).map((d) => (
            <li key={d.n} className="flex items-center justify-between"><span className="flex items-center gap-2"><i className="h-2 w-2 rounded-full" style={{ background: d.c }} />{d.n}</span><b className="tnum">{d.v}</b></li>
          ))}
        </ul>
      </div>
      <div className="mt-3 grid grid-cols-2 gap-3 border-t border-line pt-3">
        <div className="flex items-center gap-2"><Clock size={18} className="text-primary" /><div><div className="text-[11px] text-muted">Avg. travel time</div><div className="tnum text-[15px] font-bold">{s.stats.avgTravel == null ? '—' : <>{s.stats.avgTravel} min <span className="text-[11px] font-semibold text-ok">↑ 18%</span></>}</div>{s.live && <div className="text-[10px] text-muted">not measured</div>}</div></div>
        <div className="flex items-center gap-2"><Leaf size={18} className="text-ok" /><div><div className="text-[11px] text-muted">Emission reduction</div><div className="tnum text-[15px] font-bold">{s.stats.emission == null ? '—' : `${s.stats.emission}%`}</div><div className="text-[10px] text-muted">{s.live ? 'not measured' : 'vs. normal traffic'}</div></div></div>
      </div>
    </Card>
  )
}

function ScenarioFilter() {
  return <span className="rounded-md border border-line px-2 py-1 text-xs text-muted">Current scenario</span>
}
