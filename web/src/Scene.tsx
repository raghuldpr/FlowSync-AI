import { lazy, Suspense, useMemo, useRef } from 'react'
import { Canvas, useFrame } from '@react-three/fiber'
import * as THREE from 'three'
import { useStore } from './store'
import {
  AMB_RUN, CARS, EDGE, ROUTE_Z, SIGS, ambulanceX, carAlong, carStatus, clock, phaseOf, sigState,
  type CarDef, type CarStatus, type Lamp, type Sig,
} from './sim'
import {
  Ambulance, Atmosphere, Buildings, CameraRig, CarShape, CarTag, Inst, LAMP_HEX, OFFSET, makeChevronTexture, place, reduced, rng,
  type Focus, type Lot, type Pose,
} from './kit'

const LiveWorld = lazy(() => import('./LiveScene'))
const ROAD_W = 5.2

/* ---------- static demo city (2x2 grid) ---------- */
function makeLots(): Lot[] {
  const r = rng(7)
  const blocks: [number, number][] = [[-EDGE, -9.4], [-2.8, 2.8], [9.4, EDGE]]
  const lots: Lot[] = []
  for (const [x0, x1] of blocks) for (const [z0, z1] of blocks) {
    const nx = Math.max(1, Math.floor((x1 - x0) / 3.6)), nz = Math.max(1, Math.floor((z1 - z0) / 3.6))
    const lw = (x1 - x0) / nx, ld = (z1 - z0) / nz
    const central = x0 === -2.8 && z0 === -2.8
    for (let i = 0; i < nx; i++) for (let j = 0; j < nz; j++) {
      if (r() < 0.08) continue
      const cx = x0 + lw * (i + 0.5), cz = z0 + ld * (j + 0.5)
      const near = 1 - Math.min(1, Math.hypot(cx, cz) / 30)
      const h = 1.1 + Math.pow(r(), 1.7) * 4.6 * (0.45 + near) * (central ? 1.3 : 1)
      lots.push({ x: cx, z: cz, w: lw - 0.7, d: ld - 0.7, h })
    }
  }
  return lots
}

function Roads() {
  const setDashes = useMemo(() => {
    const spots: [number, number, boolean][] = []
    for (const c of [-6, 6]) for (let a = -EDGE - 4; a <= EDGE + 4; a += 2.6) {
      if (Math.abs(a - 6) < 3.6 || Math.abs(a + 6) < 3.6) continue
      spots.push([a, c, true], [c, a, false])
    }
    return { n: spots.length, fn: (m: THREE.InstancedMesh) => spots.forEach(([a, b, h], i) => (h ? place(m, i, a, 0.12, b, 1.2, 1, 0.14) : place(m, i, a, 0.12, b, 0.14, 1, 1.2))) }
  }, [])
  const setZebra = useMemo(() => {
    const spots: [number, number, boolean][] = []
    for (const sx of [-6, 6]) for (const sz of [-6, 6]) for (const side of [-1, 1]) for (let k = -2; k <= 2; k++) {
      spots.push([sx + side * 3.9, sz + k * 0.9, true], [sx + k * 0.9, sz + side * 3.9, false])
    }
    return { n: spots.length, fn: (m: THREE.InstancedMesh) => spots.forEach(([x, z, h], i) => (h ? place(m, i, x, 0.125, z, 1.1, 1, 0.42) : place(m, i, x, 0.125, z, 0.42, 1, 1.1))) }
  }, [])
  const lamps = useMemo(() => {
    const p: [number, number][] = []
    for (const c of [-6, 6]) for (let a = -EDGE; a <= EDGE; a += 8) {
      if (Math.abs(a - 6) < 4.5 || Math.abs(a + 6) < 4.5) continue
      p.push([a, c + 3.4], [a + 4, c - 3.4], [c + 3.4, a], [c - 3.4, a + 4])
    }
    return { n: p.length, fn: (m: THREE.InstancedMesh) => p.forEach(([x, z], i) => place(m, i, x, 1.7, z)) }
  }, [])
  const len = 2 * EDGE + 10
  return (
    <group>
      {[-6, 6].map((c) => (
        <group key={c}>
          <mesh position={[0, 0.06, c]}><boxGeometry args={[len, 0.12, ROAD_W]} /><meshStandardMaterial color="#0b1930" roughness={0.9} /></mesh>
          <mesh position={[c, 0.062, 0]}><boxGeometry args={[ROAD_W, 0.12, len]} /><meshStandardMaterial color="#0b1930" roughness={0.9} /></mesh>
        </group>
      ))}
      <Inst count={setDashes.n} setup={setDashes.fn}><boxGeometry args={[1, 0.02, 1]} /><meshBasicMaterial color="#5c7ea8" /></Inst>
      <Inst count={setZebra.n} setup={setZebra.fn}><boxGeometry args={[1, 0.02, 1]} /><meshBasicMaterial color="#9db8d8" /></Inst>
      <Inst count={lamps.n} setup={lamps.fn}><sphereGeometry args={[0.16, 8, 8]} /><meshBasicMaterial color="#ffb35c" toneMapped={false} /></Inst>
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, 0, 0]}><planeGeometry args={[220, 220]} /><meshStandardMaterial color="#060f1d" roughness={1} /></mesh>
      <gridHelper args={[160, 80, '#0d2440', '#0a1b32']} position={[0, 0.005, 0]} />
    </group>
  )
}

/* ---------- signals ---------- */
function SignalPost({ sig, emergency }: { sig: Sig; emergency: boolean }) {
  const lampA = useRef<THREE.MeshStandardMaterial>(null)
  const lampB = useRef<THREE.MeshStandardMaterial>(null)
  const beam = useRef<THREE.Mesh>(null)
  const last = useRef<Lamp | ''>('')
  useFrame(() => {
    const st = sigState(sig, clock.T, emergency)
    if (st.lamp !== last.current) {
      last.current = st.lamp as Lamp
      for (const m of [lampA.current, lampB.current]) if (m) { m.color.set(LAMP_HEX[st.lamp]); m.emissive.set(LAMP_HEX[st.lamp]) }
    }
    if (beam.current) beam.current.visible = st.preempt
  })
  const corners: [number, number][] = [[3.5, 3.5], [-3.5, -3.5]]
  return (
    <group position={[sig.x, 0, sig.z]}>
      {corners.map(([dx, dz], i) => (
        <group key={i} position={[dx, 0, dz]}>
          <mesh position={[0, 1.2, 0]}><cylinderGeometry args={[0.06, 0.08, 2.4, 8]} /><meshStandardMaterial color="#1b2b44" /></mesh>
          <mesh position={[0, 2.55, 0]}>
            <sphereGeometry args={[0.3, 16, 16]} />
            <meshStandardMaterial ref={i ? lampB : lampA} color="#ff4350" emissive="#ff4350" emissiveIntensity={2.4} toneMapped={false} />
          </mesh>
        </group>
      ))}
      {sig.onRoute && (
        <mesh ref={beam} position={[0, 4.5, 0]} visible={false}>
          <cylinderGeometry args={[0.45, 0.45, 9, 24, 1, true]} />
          <meshBasicMaterial color="#27e49a" transparent opacity={0.28} depthWrite={false} blending={THREE.AdditiveBlending} toneMapped={false} side={THREE.DoubleSide} />
        </mesh>
      )}
    </group>
  )
}

/* ---------- emergency corridor ---------- */
function Corridor() {
  const tex = useMemo(() => makeChevronTexture(2 * EDGE + 8), [])
  useFrame((_, dt) => { if (!reduced) tex.offset.x -= dt * 0.35 })
  const len = 2 * EDGE + 8
  return (
    <group position={[0, 0.16, ROUTE_Z]} rotation={[-Math.PI / 2, 0, 0]}>
      <mesh><planeGeometry args={[len, 2.4]} /><meshBasicMaterial color="#10d98a" transparent opacity={0.38} toneMapped={false} depthWrite={false} /></mesh>
      <mesh position={[0, 0, 0.01]}><planeGeometry args={[len, 2.4]} /><meshBasicMaterial map={tex} color="#7dffc4" transparent opacity={0.9} toneMapped={false} depthWrite={false} /></mesh>
    </group>
  )
}

const demoPose = (): Pose => {
  const ph = phaseOf(clock.T)
  return { x: ambulanceX(ph), z: ROUTE_Z, rot: 0, rings: ph < AMB_RUN }
}
const demoFocus: Focus = () => ({ x: ambulanceX(phaseOf(clock.T)), z: ROUTE_Z })
const noFocus: Focus = () => null

/* ---------- vehicles ---------- */
function Vehicle({ c, status, showTag }: { c: CarDef; status: CarStatus; showTag: boolean }) {
  const g = useRef<THREE.Group>(null)
  useFrame(() => {
    const a = carAlong(c, clock.T)
    if (!g.current) return
    if (c.axis === 'h') { g.current.position.set(a, 0, c.lane); g.current.rotation.y = c.dir > 0 ? 0 : Math.PI }
    else { g.current.position.set(c.lane, 0, a); g.current.rotation.y = c.dir > 0 ? -Math.PI / 2 : Math.PI / 2 }
  })
  return (
    <group ref={g}>
      <CarShape status={status} />
      {showTag && <CarTag status={status} id={c.id} />}
    </group>
  )
}

const DEMO_LOTS = makeLots()

function DemoWorld() {
  const layers = useStore((s) => s.layers)
  const T = useStore((s) => s.T)
  const scenario = useStore((s) => s.scenario)
  const emergency = scenario === 'emergency'
  return (
    <>
      <Atmosphere />
      <Roads />
      <Buildings lots={DEMO_LOTS} />
      {layers.route && emergency && <Corridor />}
      {layers.signals && SIGS.map((s) => <SignalPost key={s.id} sig={s} emergency={emergency} />)}
      {layers.vehicles && CARS.map((c) => (
        <Vehicle key={c.id} c={c} status={carStatus(c, T, emergency)} showTag={c.tag && layers.warnings} />
      ))}
      {emergency && <Ambulance showRings={layers.warnings} getPose={demoPose} />}
      <CameraRig focus={emergency ? demoFocus : noFocus} />
    </>
  )
}

export default function Scene() {
  const quality = useStore((s) => s.quality)
  const live = useStore((s) => !!s.auth && !!s.live.ov && !!s.live.topo)
  return (
    <Canvas dpr={quality === 'high' ? [1, 1.75] : 1} camera={{ fov: 34, near: 0.5, far: 400, position: OFFSET['3d'].toArray() }} gl={{ antialias: false, powerPreference: 'high-performance' }}>
      {live ? <Suspense fallback={null}><LiveWorld /></Suspense> : <DemoWorld />}
    </Canvas>
  )
}
