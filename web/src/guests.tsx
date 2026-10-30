// Guests on the big screen: judge cars on the 3D map and the warned / not-warned panel.
// Everything V2V here is SIMULATED by the backend; the wording below is fixed and never urges anyone to hurry.
import { useEffect, useMemo, useRef, useState } from 'react'
import { useFrame } from '@react-three/fiber'
import * as THREE from 'three'
import { Ambulance as AmbulanceIcon, Radio } from 'lucide-react'
import { Badge, Card } from './Cards'
import { CarShape, ScreenTag, reduced, STATUS_HEX } from './kit'
import { isWarned, safeColor, type RoadUser } from './live'
import type { CarStatus } from './sim'
import { useStore } from './store'

export interface GuestPose { x: number; z: number; rot: number }

/** `unwarned` only means something while an emergency alert is live; otherwise the car is just driving. */
export function guestStatus(u: RoadUser, emergency: boolean): CarStatus {
  if (isWarned(u)) return Object.values(u.alerts).includes('redirected') ? 'rerouted' : 'warned'
  return emergency ? 'unwarned' : 'neutral'
}
const STATE_LABEL: Record<CarStatus, string> = { neutral: '', warned: 'WARNED', rerouted: 'REROUTED', unwarned: 'NOT WARNED' }

/** A guest car: its own colour, a status halo on the road, a plate tag, and a ripple the first seconds after joining. */
export function GuestCar({ u, pose, status, bornAt, tag }: { u: RoadUser; pose: GuestPose; status: CarStatus; bornAt: number; tag: boolean }) {
  const g = useRef<THREE.Group>(null)
  const halo = useRef<THREE.Mesh>(null)
  const ripple = useRef<THREE.Mesh>(null)
  const target = useRef(pose)
  target.current = pose
  const ready = useRef(false)
  const color = useMemo(() => safeColor(u.color), [u.color])
  useFrame((_, dt) => {
    const o = g.current
    if (!o) return
    const t = target.current
    if (!ready.current) { o.position.set(t.x, 0, t.z); o.rotation.y = t.rot; ready.current = true }
    else {
      const k = reduced ? 1 : 1 - Math.exp(-dt * 6)
      o.position.x += (t.x - o.position.x) * k
      o.position.z += (t.z - o.position.z) * k
      o.rotation.y += Math.atan2(Math.sin(t.rot - o.rotation.y), Math.cos(t.rot - o.rotation.y)) * k
    }
    if (halo.current) {
      const pulse = status === 'warned' && !reduced ? 0.55 + 0.35 * Math.sin(performance.now() / 220) : 0.6
      ;(halo.current.material as THREE.MeshBasicMaterial).opacity = pulse
    }
    if (ripple.current) {
      const age = bornAt > 0 ? (Date.now() - bornAt) / 1000 : 9
      ripple.current.visible = age < 2.5
      if (age < 2.5) {
        const f = age / 2.5, r = 1 + f * 5
        ripple.current.scale.set(r, r, 1)
        ;(ripple.current.material as THREE.MeshBasicMaterial).opacity = (1 - f) * 0.9
      }
    }
  })
  const label = STATE_LABEL[status]
  return (
    <group ref={g}>
      <CarShape status={status} color={color} />
      <group rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.2, 0]}>
        {status !== 'neutral' && (
          <mesh ref={halo}><ringGeometry args={[1.25, 1.5, 40]} /><meshBasicMaterial color={STATUS_HEX[status]} transparent opacity={0.6} depthWrite={false} toneMapped={false} /></mesh>
        )}
        <mesh ref={ripple} visible={false}><ringGeometry args={[0.93, 1, 48]} /><meshBasicMaterial color={color} transparent depthWrite={false} toneMapped={false} blending={THREE.AdditiveBlending} /></mesh>
      </group>
      {tag && (
        <ScreenTag badge={label || undefined} badgeClass={status === 'warned' ? 'bg-danger/90' : status === 'rerouted' ? 'bg-ok/90' : 'bg-slate-500/90'} text={u.plate ?? u.id} />
      )}
    </group>
  )
}

/* ---------- big-screen overlays and panel ---------- */

/** Guests come from the dedicated feed when it answers, otherwise from the overview's road_users. */
export function useGuests(): RoadUser[] {
  const feed = useStore((s) => s.live.feed)
  const ov = useStore((s) => s.live.ov)
  return useMemo(() => feed?.users ?? ov?.road_users ?? [], [feed, ov])
}

/** Banner over the map while a simulated V2V alert is reaching cars (and for a few seconds after a new one is raised). */
export function V2vBanner() {
  const feed = useStore((s) => (s.auth ? s.live.feed : null))
  const last = feed?.v2v.length ? feed.v2v[feed.v2v.length - 1] : null
  const key = last ? `${last.t}:${last.from}` : ''
  const [fresh, setFresh] = useState(false)
  const seen = useRef<string | null>(null)
  useEffect(() => {
    if (seen.current === null) { seen.current = key; return } // the alert already on screen when we opened is not "new"
    if (!key || key === seen.current) return
    seen.current = key
    setFresh(true)
    const id = setTimeout(() => setFresh(false), 8000)
    return () => clearTimeout(id)
  }, [key])
  if (!feed || !last || !(fresh || feed.warned > 0)) return null
  return (
    <div role="status" className="pointer-events-none absolute left-3 top-[5.25rem] z-10 max-w-[calc(100%-5rem)] rounded-lg bg-danger/90 px-3.5 py-2 text-white shadow-lg ring-1 ring-white/20 backdrop-blur">
      <div className="flex items-center gap-2 text-[13px] font-semibold">
        <Radio size={15} aria-hidden /> SIMULATED V2V · Emergency vehicle alert
      </div>
      <div className="text-[11.5px] opacity-90">{last.from}: heard by {last.heard} of {last.drivers} cars · {feed.warned} of {feed.total} warned now</div>
    </div>
  )
}

export function GuestsCard() {
  const live = useStore((s) => !!s.auth && !!s.live.ov)
  const users = useGuests()
  const ov = useStore((s) => s.live.ov)
  const feed = useStore((s) => s.live.feed)
  const err = useStore((s) => s.live.err)
  const topo = useStore((s) => s.live.topo)
  if (!live) {
    return <Card title="Guests on the road"><p className="text-[13px] text-muted">Sign in as an operator to see the guests who joined by QR. The map shows the demo scene until then.</p></Card>
  }
  const guests = users.filter((u) => !u.simulated)
  const scripted = users.length - guests.length
  const warned = users.filter(isWarned).length
  const evs = (ov?.ev ?? []).filter((e) => e.status === 'active')
  const emergency = evs.length > 0 || warned > 0
  const label = (id: string) => topo?.nodes[id]?.label ?? id
  const held = Object.values(ov?.signals ?? {}).filter((s) => s.commanded?.reason.startsWith('emergency')).length
  const sorted = [...guests].sort((a, b) => Number(isWarned(b)) - Number(isWarned(a)) || a.id.localeCompare(b.id, undefined, { numeric: true }))
  return (
    <Card title="Guests on the road" right={<Badge tone={warned ? 'danger' : 'mute'}>{warned} of {users.length} warned</Badge>}>
      {err && <p className="mb-2 rounded-md bg-warn/15 px-2 py-1 text-[12px] text-amber-700">Backend not answering ({err}). Showing the last data received.</p>}
      {evs.length > 0 && (
        <ul className="mb-3 space-y-1.5">
          {evs.map((e) => (
            <li key={e.id} className="flex items-start gap-2 rounded-md bg-danger/5 px-2.5 py-2 text-[12.5px]">
              <AmbulanceIcon size={16} className="mt-0.5 shrink-0 text-danger" aria-hidden />
              <span className="min-w-0">
                <b>Emergency vehicle</b>: {label(e.origin)} → {label(e.destination)}
                <span className="block text-muted">
                  {e.stale ? 'Location lost' : 'On route'} · {held ? `${held} signal${held > 1 ? 's' : ''} held for it` : 'no signal held yet'}
                </span>
              </span>
            </li>
          ))}
        </ul>
      )}
      {sorted.length === 0 ? (
        <p className="text-[13px] text-muted">No guests yet. A judge scans the QR code to join and appears here and on the map.</p>
      ) : (
        <ul className="divide-y divide-line">
          {sorted.map((u) => {
            const st = guestStatus(u, emergency)
            return (
              <li key={u.id} className="flex items-center gap-2.5 py-2 text-[13px]">
                <i className="h-3 w-3 shrink-0 rounded-full ring-1 ring-black/10" style={{ background: safeColor(u.color) }} aria-hidden />
                <span className="min-w-0 flex-1"><b>{u.id}</b> <span className="font-mono text-[11.5px] text-muted">{u.plate}</span></span>
                {u.role === 'ambulance' ? <Badge tone="danger">Ambulance</Badge>
                  : st === 'neutral' ? <Badge tone="mute">Driving</Badge>
                  : st === 'unwarned' ? <Badge tone="mute">Not warned</Badge>
                  : <Badge tone={st === 'rerouted' ? 'ok' : 'danger'}>{st === 'rerouted' ? 'Rerouted' : 'Warned'}</Badge>}
              </li>
            )
          })}
        </ul>
      )}
      {scripted > 0 && <p className="mt-2 text-[11.5px] text-muted">{scripted} scripted scenario car{scripted > 1 ? 's' : ''} also on the map (simulated, not phones).</p>}
      {feed && feed.v2v.length > 0 && (
        <div className="mt-3 border-t border-line pt-2">
          <div className="mb-1 flex items-center gap-1.5 text-[12px] font-semibold"><Badge tone="info">SIMULATED</Badge> V2V alerts</div>
          <ul className="space-y-0.5 text-[12px] text-muted">
            {[...feed.v2v].reverse().slice(0, 4).map((v, i) => <li key={`${v.t}:${i}`}>{v.from}: heard by {v.heard} of {v.drivers} cars</li>)}
          </ul>
        </div>
      )}
    </Card>
  )
}
