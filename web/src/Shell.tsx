import { useState, type ReactNode } from 'react'
import {
  Activity, Bell, Box, ChevronDown, Clock, ExternalLink, FlaskConical, LayoutDashboard, Maximize2, Menu, Radio, Route, Settings, ShieldCheck, Siren, TriangleAlert, Bot, X,
} from 'lucide-react'
import { Badge, EventList, useComponentRows } from './Cards'
import { login } from './api'
import { statusTone } from './live'
import { SCENARIOS, mmss } from './sim'
import { useStore, useSnap, type Nav } from './store'

export function LoginDialog() {
  const open = useStore((s) => s.loginOpen)
  const setOpen = useStore((s) => s.setLoginOpen)
  const setAuth = useStore((s) => s.setAuth)
  const [user, setUser] = useState('operator')
  const [pw, setPw] = useState('')
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  if (!open) return null
  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setBusy(true); setErr('')
    try {
      setAuth(await login(user, pw))
      setPw(''); setOpen(false)
    } catch (x) { setErr(x instanceof Error ? x.message : 'Sign-in failed') }
    setBusy(false)
  }
  return (
    <div className="fixed inset-0 z-[60] grid place-items-center bg-black/40 p-4" role="dialog" aria-modal="true" aria-label="Sign in to the FlowSync backend">
      <form onSubmit={submit} className="card w-full max-w-sm space-y-3 p-5 shadow-2xl">
        <div className="flex items-center justify-between"><h2 className="text-[16px] font-semibold">Connect to live backend</h2>
          <button type="button" aria-label="Close" onClick={() => setOpen(false)} className="rounded p-1 hover:bg-app"><X size={18} /></button></div>
        <p className="text-[12px] text-muted">Sign in with a FlowSync account. Operators can start scenarios and act on incidents; viewers can only watch.</p>
        <label className="block text-[13px]"><span className="mb-1 block text-xs text-muted">Account</span>
          <select value={user} onChange={(e) => setUser(e.target.value)} className="w-full rounded-lg border border-line bg-white px-3 py-2"><option value="operator">operator</option><option value="viewer">viewer</option></select></label>
        <label className="block text-[13px]"><span className="mb-1 block text-xs text-muted">Password</span>
          <input type="password" autoFocus autoComplete="current-password" value={pw} onChange={(e) => setPw(e.target.value)} className="w-full rounded-lg border border-line px-3 py-2 outline-none focus:border-primary" /></label>
        {err && <p role="alert" className="text-[12.5px] text-danger">{err}</p>}
        <button type="submit" disabled={busy || !pw} className="w-full rounded-lg bg-primary py-2 text-[13.5px] font-semibold text-white enabled:hover:bg-primary/90 disabled:opacity-50">{busy ? 'Signing in…' : 'Sign in'}</button>
      </form>
    </div>
  )
}

export function Header({ onMenu }: { onMenu: () => void }) {
  const { scenario, setScenario, mode, setMode, unread, clearUnread, backend, auth, setAuth, setLoginOpen, live } = useStore()
  const s = useSnap()
  const [bell, setBell] = useState(false)
  const [op, setOp] = useState(false)
  const isLive = !!auth && !!live.ov
  const world = live.ov?.scenario.world === 'simulator' ? 'simulated' : 'physical'
  const shownMode = isLive ? world : mode
  return (
    <header className="relative z-30 flex h-16 shrink-0 items-center gap-3 border-b border-line bg-white px-3 md:px-4">
      <button type="button" aria-label="Open navigation" onClick={onMenu} className="grid h-9 w-9 place-items-center rounded-lg hover:bg-app md:hidden"><Menu size={20} /></button>
      <div className="flex items-center gap-2.5">
        <span className="grid h-9 w-9 place-items-center rounded-xl bg-primary text-white"><Activity size={20} /></span>
        <div className="hidden whitespace-nowrap leading-tight sm:block">
          <div className="text-[19px] font-bold tracking-tight">FlowSync AI</div>
          <div className="hidden text-[11px] text-muted xl:block">Smart Traffic. Safer Cities.</div>
        </div>
      </div>

      <label className="ml-2 hidden items-center gap-1.5 rounded-lg border border-line px-3 py-1.5 text-[13px] lg:flex">
        <span className="text-muted">Scenario:</span>
        {isLive ? (
          <span className="max-w-[12rem] truncate font-semibold">{s.scenarioName}</span>
        ) : (
          <select aria-label="Scenario" value={scenario} onChange={(e) => setScenario(e.target.value)} className="bg-transparent font-semibold outline-none">
            {SCENARIOS.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
          </select>
        )}
      </label>

      <div className="mx-auto hidden items-center gap-3 md:flex">
        {auth && !live.ok && live.updated > 0 ? (
          <span className="flex items-center gap-2 rounded-full bg-danger/10 px-3 py-1.5 text-[13px] font-medium text-red-600"><i className="h-2 w-2 rounded-full bg-danger" />Offline</span>
        ) : (
          <span className="flex items-center gap-2 rounded-full bg-ok/10 px-3 py-1.5 text-[13px] font-medium text-emerald-700"><i className="live-dot h-2 w-2 rounded-full bg-ok" />{isLive ? 'Live' : 'Running'}</span>
        )}
        <div role="radiogroup" aria-label={isLive ? 'Operating mode (reported by the backend)' : 'Operating mode'} className="flex rounded-full border border-line bg-app p-0.5 text-[13px]">
          {(['physical', 'simulated'] as const).map((m) => (
            <button key={m} type="button" role="radio" aria-checked={shownMode === m} disabled={isLive} onClick={() => setMode(m)}
              className={`rounded-full px-3.5 py-1 font-medium capitalize disabled:cursor-default ${shownMode === m ? 'bg-white text-primary shadow-sm ring-1 ring-primary/30' : 'text-muted'}`}>{m}</button>
          ))}
        </div>
        <button type="button" onClick={() => (auth ? setAuth(null) : setLoginOpen(true))} title={auth ? 'Sign out and return to demo data' : 'Sign in to show real backend data'}
          className={`hidden rounded-full border px-3 py-1.5 text-[12.5px] font-medium xl:block ${auth ? 'border-ok/40 bg-ok/5 text-emerald-700' : 'border-line text-muted hover:bg-app'}`}>
          {auth ? (live.ov ? `Live data · ${auth.user}` : 'No backend yet · demo data') : 'Demo data · Sign in'}
        </button>
      </div>

      <div className="ml-auto flex items-center gap-2 md:ml-0">
        <div className="flex items-center gap-1.5 text-[13px]"><Clock size={18} className="text-muted" /><div className="leading-none"><div className="text-[10px] text-muted">Time</div><div className="tnum text-[15px] font-semibold">{mmss(s.T)}</div></div></div>
        <button type="button" aria-label="Toggle full screen" title="Full screen" className="grid h-9 w-9 place-items-center rounded-lg hover:bg-app"
          onClick={() => (document.fullscreenElement ? document.exitFullscreen() : document.documentElement.requestFullscreen())}><Maximize2 size={18} /></button>
        <div className="relative">
          <button type="button" aria-label={`Notifications, ${unread} unread`} onClick={() => { setBell((b) => !b); setOp(false); clearUnread() }} className="relative grid h-9 w-9 place-items-center rounded-lg hover:bg-app">
            <Bell size={19} />{unread > 0 && <span className="absolute -right-0.5 -top-0.5 grid h-4 min-w-4 place-items-center rounded-full bg-danger px-1 text-[10px] font-bold text-white">{unread}</span>}
          </button>
          {bell && <Pop onClose={() => setBell(false)} w="w-80"><div className="mb-2 text-sm font-semibold">Alerts</div><EventList events={s.events} /></Pop>}
        </div>
        <div className="relative">
          <button type="button" aria-label="Operator menu" aria-expanded={op} onClick={() => { setOp((o) => !o); setBell(false) }} className="flex items-center gap-2 rounded-lg px-1.5 py-1 hover:bg-app">
            <span className="grid h-8 w-8 place-items-center rounded-full bg-primary text-[11px] font-bold uppercase text-white">{(auth?.user ?? 'op').slice(0, 2)}</span>
            <span className="hidden text-[13px] font-medium capitalize sm:block">{auth?.user ?? 'Operator'}</span><ChevronDown size={15} className="hidden text-muted sm:block" />
          </button>
          {op && (
            <Pop onClose={() => setOp(false)} w="w-64">
              <div className="mb-2 text-xs text-muted">Backend: {backend.state === 'live' ? `connected · ${backend.intersections} intersections` : backend.state === 'checking' ? 'checking…' : 'not reachable (demo data)'}</div>
              {auth ? (
                <button type="button" onClick={() => { setAuth(null); setOp(false) }} className="mb-1 w-full rounded-md px-2 py-1.5 text-left text-[13px] hover:bg-app">Sign out ({auth.role})</button>
              ) : (
                <button type="button" onClick={() => { setLoginOpen(true); setOp(false) }} className="mb-1 w-full rounded-md px-2 py-1.5 text-left text-[13px] font-medium text-primary hover:bg-app">Sign in to live backend</button>
              )}
              <a href="/operator.html" className="flex items-center gap-2 rounded-md px-2 py-1.5 text-[13px] hover:bg-app">Open legacy operator console <ExternalLink size={14} /></a>
            </Pop>
          )}
        </div>
      </div>
    </header>
  )
}

function Pop({ children, onClose, w }: { children: ReactNode; onClose: () => void; w: string }) {
  return (
    <>
      <button type="button" aria-label="Close" className="fixed inset-0 z-40 cursor-default" onClick={onClose} />
      <div className={`card absolute right-0 top-11 z-50 ${w} p-3 shadow-xl`}>{children}</div>
    </>
  )
}

const NAV: { group: string; items: { id: Nav; label: string; icon: typeof Box }[] }[] = [
  { group: '', items: [
    { id: 'overview', label: 'Overview', icon: LayoutDashboard }, { id: 'stage', label: 'City Stage', icon: Box },
    { id: 'traffic', label: 'Traffic', icon: Route }, { id: 'signals', label: 'Signals', icon: Radio }, { id: 'emergencies', label: 'Emergencies', icon: Siren },
  ] },
  { group: 'Operations', items: [
    { id: 'incidents', label: 'Incidents', icon: TriangleAlert }, { id: 'scenarios', label: 'Scenarios', icon: FlaskConical }, { id: 'assistant', label: 'Assistant', icon: Bot },
  ] },
  { group: 'System', items: [
    { id: 'status', label: 'System Status', icon: Activity }, { id: 'provenance', label: 'Provenance', icon: ShieldCheck }, { id: 'settings', label: 'Settings', icon: Settings },
  ] },
]

export function Sidebar({ open, onClose }: { open: boolean; onClose: () => void }) {
  const rows = useComponentRows()
  const nav = useStore((s) => s.nav)
  const setNav = useStore((s) => s.setNav)
  const offline = useStore((s) => !!s.auth && !s.live.ok && s.live.updated > 0)
  const down = rows.filter((r) => statusTone(r.status) === 'danger').length
  const health = offline ? 'Backend not reachable' : down ? `${down} of ${rows.length} not connected` : 'All systems nominal'
  return (
    <>
      {open && <button type="button" aria-label="Close navigation" className="fixed inset-0 z-40 bg-black/30 md:hidden" onClick={onClose} />}
      <aside className={`fixed inset-y-0 left-0 z-50 flex w-[245px] flex-col border-r border-line bg-[#f7faff] p-3 transition-transform md:static md:z-auto md:w-16 md:translate-x-0 xl:w-[245px] ${open ? 'translate-x-0' : '-translate-x-full'}`}>
        <button type="button" aria-label="Close navigation" onClick={onClose} className="mb-2 self-end rounded p-1 md:hidden"><X size={18} /></button>
        <nav aria-label="Primary" className="flex-1 space-y-4 overflow-y-auto">
          {NAV.map((g) => (
            <div key={g.group}>
              {g.group && <div className="mb-1.5 px-3 text-[11px] font-semibold uppercase tracking-wider text-slate md:hidden xl:block">{g.group}</div>}
              <ul className="space-y-0.5">
                {g.items.map(({ id, label, icon: I }) => (
                  <li key={id}>
                    <button type="button" aria-current={nav === id ? 'page' : undefined} title={label} onClick={() => { setNav(id); onClose() }}
                      className={`flex h-10 w-full items-center gap-3 rounded-lg px-3 text-[13.5px] font-medium ${nav === id ? 'bg-primary/10 text-primary' : 'text-slate-600 hover:bg-white'}`}>
                      <I size={18} className={nav === id ? '' : 'text-slate'} /><span className="md:hidden xl:inline">{label}</span>
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </nav>
        <div className="card mt-3 p-3 md:hidden xl:block">
          <div className="flex items-center gap-2"><i className={`h-2.5 w-2.5 rounded-full ${offline ? 'bg-danger' : down ? 'bg-amber-400' : 'bg-ok'}`} /><div className="leading-tight"><div className="text-[13px] font-semibold">System Health</div><div className="text-[11px] text-muted">{health}</div></div></div>
          <ul className="mt-2.5 space-y-1.5">
            {rows.slice(0, 6).map((r) => (
              <li key={r.name} className="flex items-center justify-between gap-1 text-[11.5px]" title={r.detail}><span className="flex min-w-0 items-center gap-1.5 truncate"><i className={`h-1.5 w-1.5 shrink-0 rounded-full ${statusTone(r.status) === 'ok' ? 'bg-ok' : statusTone(r.status) === 'danger' ? 'bg-danger' : 'bg-slate'}`} /><span className="truncate">{r.name.replace(/ \(.*\)/, '').replace(' (background)', '').replace(' simulation', ' Sim')}</span></span><span className="shrink-0"><Badge tone={statusTone(r.status)}>{r.status}</Badge></span></li>
            ))}
          </ul>
        </div>
      </aside>
    </>
  )
}
