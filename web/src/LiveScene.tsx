// The 3D city built from the REAL backend topology, driven by /api/operator/overview:
// queue counts become parked cars, lamp reports colour the signal heads, ambulance sessions move the ambulance.
import { useCallback, useEffect, useMemo, useRef } from 'react'
import { useFrame } from '@react-three/fiber'
import * as THREE from 'three'
import { useStore } from './store'
import { NODE_R, edgePose, indexTopo, queuePoses, type TopoIndex } from './live'
import type { CarStatus } from './sim'
import {
  Ambulance, Atmosphere, Buildings, CameraRig, CarShape, CarTag, Inst, LAMP_HEX, makeChevronTexture, place, reduced, rng,
  type Focus, type Lot, type Pose,
} from './kit'

const ROAD_W = 5.2
const EXT = 9 // roads run on past the external gates so traffic visibly enters and leaves the city

interface Seg { x0: number; z0: number; x1: number; z1: number; cx: number; cz: number; len: number; rot: number; ux: number; uz: number }
const LAMP_KEY = { G: 'green', Y: 'yellow', R: 'red' } as const

function makeSegs(idx: TopoIndex): Seg[] {
  return idx.roads.map((g) => {
    const srcExt = idx.topo.nodes[g.edge.src]?.kind === 'external', dstExt = idx.topo.nodes[g.edge.dst]?.kind === 'external'
    const x0 = g.ax - (srcExt ? g.ux * EXT : 0), z0 = g.az - (srcExt ? g.uz * EXT : 0)
    const x1 = g.bx + (dstExt ? g.ux * EXT : 0), z1 = g.bz + (dstExt ? g.uz * EXT : 0)
    return { x0, z0, x1, z1, cx: (x0 + x1) / 2, cz: (z0 + z1) / 2, len: Math.hypot(x1 - x0, z1 - z0), rot: g.rot, ux: g.ux, uz: g.uz }
  })
}
function distToSeg(px: number, pz: number, s: Seg) {
  const dx = s.x1 - s.x0, dz = s.z1 - s.z0
  const t = Math.min(1, Math.max(0, ((px - s.x0) * dx + (pz - s.z0) * dz) / (dx * dx + dz * dz)))
  return Math.hypot(px - (s.x0 + dx * t), pz - (s.z0 + dz * t))
}

function makeLiveLots(segs: Seg[], idx: TopoIndex): Lot[] {
  const r = rng(11), lots: Lot[] = []
  const xs = segs.flatMap((s) => [s.x0, s.x1]), zs = segs.flatMap((s) => [s.z0, s.z1])
  const x0 = Math.min(...xs) - 12, x1 = Math.max(...xs) + 12, z0 = Math.min(...zs) - 12, z1 = Math.max(...zs) + 12
  const cx = (x0 + x1) / 2, cz = (z0 + z1) / 2, reach = Math.hypot(x1 - x0, z1 - z0) / 2
  for (let x = x0; x < x1; x += 3.6) for (let z = z0; z < z1; z += 3.6) {
    if (segs.some((s) => distToSeg(x, z, s) < 5.2) || idx.intersections.some((n) => Math.hypot(x - n.x, z - n.z) < NODE_R + 3.4)) continue
    if (r() < 0.12) continue
    const near = 1 - Math.min(1, Math.hypot(x - cx, z - cz) / reach)
    lots.push({ x, z, w: 2.9, d: 2.9, h: 1.1 + Math.pow(r(), 1.7) * 4.6 * (0.45 + near) })
  }
  return lots
}

function LiveRoads({ idx, segs }: { idx: TopoIndex; segs: Seg[] }) {
  const nearNode = (x: number, z: number, m: number) => idx.intersections.some((n) => Math.hypot(x - n.x, z - n.z) < m)
  const dashes = useMemo(() => {
    const pts: [number, number, number][] = []
    for (const s of segs) for (let a = 1.3; a < s.len; a += 2.6) {
      const x = s.x0 + s.ux * a, z = s.z0 + s.uz * a
      if (!nearNode(x, z, NODE_R + 1.4)) pts.push([x, z, s.rot])
    }
    return { n: pts.length, fn: (m: THREE.InstancedMesh) => pts.forEach(([x, z, r], i) => place(m, i, x, 0.12, z, 1.2, 1, 0.14, r)) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [segs, idx])
  const zebra = useMemo(() => {
    const pts: [number, number, number][] = []
    for (const g of idx.edges.values()) {
      if (!g.edge.approach || idx.topo.nodes[g.edge.dst]?.kind !== 'intersection') continue
      const px = g.bx - g.ux * (NODE_R + 0.9), pz = g.bz - g.uz * (NODE_R + 0.9)
      for (let k = -2; k <= 2; k++) pts.push([px + -g.uz * k * 0.9, pz + g.ux * k * 0.9, g.rot])
    }
    return { n: pts.length, fn: (m: THREE.InstancedMesh) => pts.forEach(([x, z, r], i) => place(m, i, x, 0.125, z, 1.1, 1, 0.42, r)) }
  }, [idx])
  const lamps = useMemo(() => {
    const pts: [number, number][] = []
    for (const s of segs) for (let a = 4; a < s.len - 2; a += 8) for (const side of [-1, 1]) {
      const x = s.x0 + s.ux * a + -s.uz * 3.4 * side, z = s.z0 + s.uz * a + s.ux * 3.4 * side
      if (!nearNode(x, z, NODE_R + 3)) pts.push([x, z])
    }
    return { n: pts.length, fn: (m: THREE.InstancedMesh) => pts.forEach(([x, z], i) => place(m, i, x, 1.7, z)) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [segs, idx])
  return (
    <group>
      {segs.map((s, i) => (
        <mesh key={i} position={[s.cx, 0.06, s.cz]} rotation={[0, s.rot, 0]}><boxGeometry args={[s.len, 0.12, ROAD_W]} /><meshStandardMaterial color="#15305a" roughness={0.9} /></mesh>
      ))}
      {idx.intersections.map((n) => (
        <mesh key={n.id} position={[n.x, 0.065, n.z]}><cylinderGeometry args={[NODE_R + 0.5, NODE_R + 0.5, 0.12, 32]} /><meshStandardMaterial color="#15305a" roughness={0.9} /></mesh>
      ))}
      <Inst count={dashes.n} setup={dashes.fn}><boxGeometry args={[1, 0.02, 1]} /><meshBasicMaterial color="#5c7ea8" /></Inst>
      <Inst count={zebra.n} setup={zebra.fn}><boxGeometry args={[1, 0.02, 1]} /><meshBasicMaterial color="#9db8d8" /></Inst>
      <Inst count={lamps.n} setup={lamps.fn}><sphereGeometry args={[0.16, 8, 8]} /><meshBasicMaterial color="#ffb35c" toneMapped={false} /></Inst>
      <mesh rotation={[-Math.PI / 2, 0, 0]}><planeGeometry args={[220, 220]} /><meshStandardMaterial color="#060f1d" roughness={1} /></mesh>
      <gridHelper args={[160, 80, '#0d2440', '#0a1b32']} position={[0, 0.005, 0]} />
    </group>
  )
}

/* ---------- signal heads: one per approach, coloured by the device's reported lamp for that axis ---------- */
type OvRef = { current: NonNullable<ReturnType<typeof useStore.getState>['live']['ov']> }

function LampPost({ edgeId, idx, ovRef }: { edgeId: string; idx: TopoIndex; ovRef: OvRef }) {
  const g = idx.edges.get(edgeId)!
  const mat = useRef<THREE.MeshStandardMaterial>(null)
  const last = useRef('')
  useFrame(() => {
    const rep = ovRef.current.signals[g.edge.dst]
    const lamp = rep?.reported && !rep.reported_stale ? LAMP_KEY[rep.reported.lamps[g.edge.movement ?? 'A'] as 'G'] ?? 'unknown' : 'unknown'
    if (lamp !== last.current && mat.current) {
      last.current = lamp
      mat.current.color.set(LAMP_HEX[lamp]); mat.current.emissive.set(LAMP_HEX[lamp])
    }
  })
  const x = g.bx - g.ux * (NODE_R + 0.5) + -g.uz * 3.3, z = g.bz - g.uz * (NODE_R + 0.5) + g.ux * 3.3
  return (
    <group position={[x, 0, z]}>
      <mesh position={[0, 1.2, 0]}><cylinderGeometry args={[0.06, 0.08, 2.4, 8]} /><meshStandardMaterial color="#1b2b44" /></mesh>
      <mesh position={[0, 2.55, 0]}><sphereGeometry args={[0.3, 16, 16]} /><meshStandardMaterial ref={mat} color="#5b6b82" emissive="#5b6b82" emissiveIntensity={2.4} toneMapped={false} /></mesh>
    </group>
  )
}

function PreemptBeams({ idx, ovRef }: { idx: TopoIndex; ovRef: OvRef }) {
  const refs = useRef<(THREE.Mesh | null)[]>([])
  useFrame(() => {
    idx.intersections.forEach((n, i) => {
      const m = refs.current[i]
      if (m) m.visible = !!ovRef.current.signals[n.id]?.commanded?.reason.startsWith('emergency')
    })
  })
  return (
    <>
      {idx.intersections.map((n, i) => (
        <mesh key={n.id} ref={(el) => { refs.current[i] = el }} position={[n.x, 4.5, n.z]} visible={false}>
          <cylinderGeometry args={[0.55, 0.55, 9, 24, 1, true]} />
          <meshBasicMaterial color="#27e49a" transparent opacity={0.28} depthWrite={false} blending={THREE.AdditiveBlending} toneMapped={false} side={THREE.DoubleSide} />
        </mesh>
      ))}
    </>
  )
}

/* ---------- green corridor along the ambulance's current route ---------- */
function LiveCorridor({ idx, route }: { idx: TopoIndex; route: string[] }) {
  const key = route.join('|')
  const items = useMemo(() => route.map((id) => idx.edges.get(id)).filter((g): g is NonNullable<typeof g> => !!g).map((g) => ({ g, tex: makeChevronTexture(g.len) })),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [key, idx])
  useEffect(() => () => items.forEach((i) => i.tex.dispose()), [items])
  useFrame((_, dt) => { if (!reduced) items.forEach((i) => { i.tex.offset.x -= dt * 0.35 }) })
  return (
    <>
      {items.map(({ g, tex }) => (
        <group key={g.id} position={[(g.ax + g.bx) / 2, 0.16, (g.az + g.bz) / 2]} rotation={[0, g.rot, 0]}>
          <group rotation={[-Math.PI / 2, 0, 0]}>
            <mesh><planeGeometry args={[g.len, 2.4]} /><meshBasicMaterial color="#10d98a" transparent opacity={0.38} toneMapped={false} depthWrite={false} /></mesh>
            <mesh position={[0, 0, 0.01]}><planeGeometry args={[g.len, 2.4]} /><meshBasicMaterial map={tex} color="#7dffc4" transparent opacity={0.9} toneMapped={false} depthWrite={false} /></mesh>
          </group>
        </group>
      ))}
    </>
  )
}

/* ---------- cars: poses arrive once a second, so ease towards them every frame ---------- */
interface CarPose { x: number; z: number; rot: number }
function LiveCar({ pose, status, tag, id }: { pose: CarPose; status: CarStatus; tag: boolean; id: string }) {
  const g = useRef<THREE.Group>(null)
  const target = useRef(pose)
  target.current = pose
  const ready = useRef(false)
  useFrame((_, dt) => {
    const o = g.current
    if (!o) return
    const t = target.current
    if (!ready.current) { o.position.set(t.x, 0, t.z); o.rotation.y = t.rot; ready.current = true; return }
    const k = reduced ? 1 : 1 - Math.exp(-dt * 6)
    o.position.x += (t.x - o.position.x) * k
    o.position.z += (t.z - o.position.z) * k
    let d = t.rot - o.rotation.y
    d = Math.atan2(Math.sin(d), Math.cos(d))
    o.rotation.y += d * k
  })
  return (
    <group ref={g}>
      <CarShape status={status} />
      {tag && <CarTag status={status} id={id} />}
    </group>
  )
}

export default function LiveWorld() {
  const topo = useStore((s) => s.live.topo)!
  const ov = useStore((s) => s.live.ov)!
  const layers = useStore((s) => s.layers)
  const idx = useMemo(() => indexTopo(topo), [topo])
  const segs = useMemo(() => makeSegs(idx), [idx])
  const lots = useMemo(() => makeLiveLots(segs, idx), [segs, idx])
  const ovRef = useRef(ov)
  useEffect(() => { ovRef.current = ov }, [ov])

  const active = ov.ev.find((s) => s.status === 'active')
  const emergency = !!active
  const route = useMemo(() => active?.route ?? [], [active?.route?.join('|')]) // eslint-disable-line react-hooks/exhaustive-deps

  const cars = useMemo(() => {
    const out: { id: string; pose: CarPose; status: CarStatus; tag: boolean }[] = []
    for (const [edge, t] of Object.entries(ov.traffic)) {
      if (t.stale) continue
      queuePoses(idx, edge, Math.min(t.count, 12)).forEach((pose, i) => out.push({ id: `q:${edge}:${i}`, pose, status: 'neutral', tag: false }))
    }
    let tags = 0
    for (const u of ov.road_users) {
      const pose = u.edge ? edgePose(idx, u.edge, u.progress) : null
      if (!pose) continue
      const st = Object.values(u.alerts)
      const status: CarStatus = st.includes('redirected') ? 'rerouted' : st.some((s) => s === 'active' || s === 'updated') ? 'warned' : emergency ? 'unwarned' : 'neutral'
      const tag = (status === 'warned' || status === 'rerouted') && tags++ < 6
      out.push({ id: u.id, pose, status, tag })
    }
    return out
  }, [ov, idx, emergency])

  const smooth = useRef<{ x: number; z: number; rot: number; t: number } | null>(null)
  const getPose = useCallback((): Pose | null => {
    const s = ovRef.current.ev.find((e) => e.status === 'active')
    const p = s?.edge ? edgePose(idx, s.edge, s.progress, 0) : null
    if (!p) { smooth.current = null; return null }
    const now = performance.now(), cur = smooth.current
    if (!cur) { smooth.current = { ...p, t: now }; return { ...p, rings: true } }
    const k = reduced ? 1 : 1 - Math.exp(-((now - cur.t) / 1000) * 5)
    cur.x += (p.x - cur.x) * k
    cur.z += (p.z - cur.z) * k
    cur.rot += Math.atan2(Math.sin(p.rot - cur.rot), Math.cos(p.rot - cur.rot)) * k
    cur.t = now
    return { x: cur.x, z: cur.z, rot: cur.rot, rings: true }
  }, [idx])
  const focus = useCallback<Focus>(() => {
    const s = ovRef.current.ev.find((e) => e.status === 'active')
    const p = s?.edge ? edgePose(idx, s.edge, s.progress, 0) : null
    return p ? { x: p.x, z: p.z } : null
  }, [idx])

  const approachEdges = useMemo(() => [...idx.edges.values()].filter((g) => g.edge.approach && g.edge.movement && topo.nodes[g.edge.dst]?.kind === 'intersection'), [idx, topo])

  return (
    <>
      <Atmosphere />
      <LiveRoads idx={idx} segs={segs} />
      <Buildings lots={lots} />
      {layers.route && emergency && <LiveCorridor idx={idx} route={route} />}
      {layers.signals && approachEdges.map((g) => <LampPost key={g.id} edgeId={g.id} idx={idx} ovRef={ovRef} />)}
      {layers.signals && <PreemptBeams idx={idx} ovRef={ovRef} />}
      {layers.vehicles && cars.map((c) => <LiveCar key={c.id} id={c.id} pose={c.pose} status={c.status} tag={c.tag && layers.warnings} />)}
      <Ambulance showRings={layers.warnings} getPose={getPose} />
      <CameraRig focus={focus} />
    </>
  )
}
