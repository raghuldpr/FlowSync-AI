// Pieces shared by the demo scene (Scene.tsx) and the live, topology-driven scene (LiveScene.tsx).
import { useEffect, useLayoutEffect, useMemo, useRef, type ComponentRef, type ReactNode } from 'react'
import { useFrame } from '@react-three/fiber'
import { Html, OrbitControls } from '@react-three/drei'
import { Bloom, EffectComposer } from '@react-three/postprocessing'
import * as THREE from 'three'
import { useStore } from './store'
import type { CarStatus } from './sim'

export const reduced = typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches
export const LAMP_HEX = { green: '#22e58f', yellow: '#ffc933', red: '#ff4350', unknown: '#5b6b82' }
export const STATUS_HEX: Record<CarStatus, string> = { neutral: '#4b8bd6', warned: '#ff5260', rerouted: '#22d47f', unwarned: '#8da2ba' }

export function rng(seed: number) {
  return () => {
    seed = (seed + 0x6d2b79f5) | 0
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

export function Inst({ count, setup, children }: { count: number; setup: (m: THREE.InstancedMesh) => void; children: ReactNode }) {
  const ref = useRef<THREE.InstancedMesh>(null)
  useLayoutEffect(() => {
    const m = ref.current
    if (!m) return
    setup(m)
    m.instanceMatrix.needsUpdate = true
    if (m.instanceColor) m.instanceColor.needsUpdate = true
  }, [setup])
  return <instancedMesh ref={ref} args={[undefined, undefined, count]} frustumCulled={false}>{children}</instancedMesh>
}

const tmp = new THREE.Object3D()
export function place(m: THREE.InstancedMesh, i: number, x: number, y: number, z: number, sx = 1, sy = 1, sz = 1, ry = 0) {
  tmp.position.set(x, y, z)
  tmp.rotation.set(0, ry, 0)
  tmp.scale.set(sx, sy, sz)
  tmp.updateMatrix()
  m.setMatrixAt(i, tmp.matrix)
}

/* ---------- buildings ---------- */
export interface Lot { x: number; z: number; w: number; d: number; h: number }
export function Buildings({ lots }: { lots: Lot[] }) {
  const edges = useMemo(() => {
    const pts: number[] = []
    for (const b of lots) {
      const x0 = b.x - b.w / 2, x1 = b.x + b.w / 2, z0 = b.z - b.d / 2, z1 = b.z + b.d / 2
      const c = [[x0, z0], [x1, z0], [x1, z1], [x0, z1]]
      for (let i = 0; i < 4; i++) {
        const [ax, az] = c[i], [bx, bz] = c[(i + 1) % 4]
        pts.push(ax, b.h, az, bx, b.h, bz, ax, 0, az, ax, b.h, az)
        pts.push(ax, 0, az, bx, 0, bz)
      }
    }
    const g = new THREE.BufferGeometry()
    g.setAttribute('position', new THREE.Float32BufferAttribute(pts, 3))
    return g
  }, [lots])
  const setup = useMemo(() => (m: THREE.InstancedMesh) => {
    const r = rng(3), col = new THREE.Color()
    lots.forEach((b, i) => {
      place(m, i, b.x, b.h / 2, b.z, b.w, b.h, b.d)
      m.setColorAt(i, col.set('#0c1d33').offsetHSL(0, 0, r() * 0.025))
    })
  }, [lots])
  return (
    <group>
      <Inst count={lots.length} setup={setup}>
        <boxGeometry args={[1, 1, 1]} />
        <meshStandardMaterial color="#ffffff" roughness={0.55} metalness={0.25} />
      </Inst>
      <lineSegments geometry={edges}>
        <lineBasicMaterial color="#2a7fe0" transparent opacity={0.55} toneMapped={false} />
      </lineSegments>
    </group>
  )
}

/* ---------- vehicles ---------- */
export function CarShape({ status }: { status: CarStatus }) {
  const hex = STATUS_HEX[status]
  return (
    <>
      <mesh position={[0, 0.34, 0]}><boxGeometry args={[1.5, 0.42, 0.8]} /><meshStandardMaterial color={hex} emissive={hex} emissiveIntensity={status === 'neutral' || status === 'unwarned' ? 0.15 : 0.6} roughness={0.4} /></mesh>
      <mesh position={[-0.1, 0.66, 0]}><boxGeometry args={[0.8, 0.3, 0.7]} /><meshStandardMaterial color="#0e1c30" metalness={0.5} roughness={0.25} /></mesh>
      <mesh position={[0.76, 0.34, 0.25]}><boxGeometry args={[0.04, 0.1, 0.16]} /><meshBasicMaterial color="#fff6d6" toneMapped={false} /></mesh>
      <mesh position={[0.76, 0.34, -0.25]}><boxGeometry args={[0.04, 0.1, 0.16]} /><meshBasicMaterial color="#fff6d6" toneMapped={false} /></mesh>
      <mesh position={[-0.76, 0.34, 0.25]}><boxGeometry args={[0.04, 0.1, 0.16]} /><meshBasicMaterial color="#ff3040" toneMapped={false} /></mesh>
      <mesh position={[-0.76, 0.34, -0.25]}><boxGeometry args={[0.04, 0.1, 0.16]} /><meshBasicMaterial color="#ff3040" toneMapped={false} /></mesh>
    </>
  )
}

export function CarTag({ status, id }: { status: CarStatus; id: string }) {
  const label = status === 'warned' ? 'WARNED' : status === 'rerouted' ? 'REROUTED' : ''
  return (
    <Html position={[0, 1.7, 0]} center zIndexRange={[20, 0]} style={{ pointerEvents: 'none' }}>
      <div className="flex flex-col items-center gap-0.5 whitespace-nowrap font-mono text-[10px] leading-none">
        {label && <span className={`rounded-sm px-1.5 py-[3px] font-semibold text-white ${status === 'warned' ? 'bg-danger/90' : 'bg-ok/90'}`}>{label}</span>}
        <span className="rounded-sm border border-sky-400/70 bg-navy/80 px-1.5 py-[3px] text-sky-200">{id}</span>
      </div>
    </Html>
  )
}

/* ---------- ambulance with warning rings ---------- */
export interface Pose { x: number; z: number; rot: number; rings: boolean }
export function Ambulance({ showRings, getPose }: { showRings: boolean; getPose: () => Pose | null }) {
  const g = useRef<THREE.Group>(null)
  const red = useRef<THREE.MeshStandardMaterial>(null)
  const blue = useRef<THREE.MeshStandardMaterial>(null)
  const rings = useRef<(THREE.Mesh | null)[]>([])
  useFrame(() => {
    const pose = getPose()
    if (!g.current) return
    g.current.visible = !!pose
    if (!pose) return
    g.current.position.set(pose.x, 0, pose.z)
    g.current.rotation.y = pose.rot
    const flash = reduced ? 1 : Math.sin(performance.now() / 85) > 0 ? 1 : 0
    if (red.current) red.current.emissiveIntensity = flash ? 4 : 0.3
    if (blue.current) blue.current.emissiveIntensity = flash ? 0.3 : 4
    rings.current.forEach((m, i) => {
      if (!m) return
      m.visible = showRings && pose.rings
      const f = reduced ? 0.5 : (performance.now() / 2200 + i / 3) % 1
      const r = 1.6 + f * 6.4
      m.scale.set(r, r, 1)
      ;(m.material as THREE.MeshBasicMaterial).opacity = (1 - f) * 0.85
    })
  })
  const ringCols = ['#ff3d4a', '#ff2fa4', '#ff3d4a']
  return (
    <group ref={g}>
      <group position={[0, 0.7, 0]}>
        <mesh><boxGeometry args={[2.8, 1.2, 1.3]} /><meshStandardMaterial color="#f4f7fb" roughness={0.4} /></mesh>
        <mesh position={[0, 0.05, 0.66]}><boxGeometry args={[2.7, 0.22, 0.02]} /><meshBasicMaterial color="#ef2b3b" toneMapped={false} /></mesh>
        <mesh position={[0, 0.05, -0.66]}><boxGeometry args={[2.7, 0.22, 0.02]} /><meshBasicMaterial color="#ef2b3b" toneMapped={false} /></mesh>
        <mesh position={[1.0, 0.22, 0]}><boxGeometry args={[0.9, 0.5, 1.32]} /><meshStandardMaterial color="#14243b" metalness={0.6} roughness={0.2} /></mesh>
        <mesh position={[0.2, 0.7, 0.35]}><boxGeometry args={[0.35, 0.18, 0.3]} /><meshStandardMaterial ref={red} color="#ff2b3b" emissive="#ff2b3b" emissiveIntensity={4} toneMapped={false} /></mesh>
        <mesh position={[0.2, 0.7, -0.35]}><boxGeometry args={[0.35, 0.18, 0.3]} /><meshStandardMaterial ref={blue} color="#3b82ff" emissive="#3b82ff" emissiveIntensity={0.3} toneMapped={false} /></mesh>
      </group>
      <pointLight position={[0, 2.5, 0]} color="#ff4a5a" intensity={18} distance={14} decay={2} />
      <group position={[0, 0.2, 0]} rotation={[-Math.PI / 2, 0, 0]}>
        {ringCols.map((c, i) => (
          <mesh key={i} ref={(el) => { rings.current[i] = el }}>
            <ringGeometry args={[0.93, 1, 64]} />
            <meshBasicMaterial color={c} transparent depthWrite={false} toneMapped={false} blending={THREE.AdditiveBlending} />
          </mesh>
        ))}
        {showRings && (
          <>
            <mesh><circleGeometry args={[8, 64]} /><meshBasicMaterial color="#ff2f6a" transparent opacity={0.07} depthWrite={false} toneMapped={false} /></mesh>
            <mesh><ringGeometry args={[7.85, 8, 64]} /><meshBasicMaterial color="#ff2fa4" transparent opacity={0.7} depthWrite={false} toneMapped={false} /></mesh>
          </>
        )}
      </group>
    </group>
  )
}

/* ---------- camera ---------- */
export const OFFSET = { '3d': new THREE.Vector3(-28, 36, 34), '2d': new THREE.Vector3(0, 66, 0.01) }
export type Focus = () => { x: number; z: number } | null

export function CameraRig({ focus }: { focus: Focus }) {
  const view = useStore((s) => s.view)
  const recenter = useStore((s) => s.recenter)
  const ctrl = useRef<ComponentRef<typeof OrbitControls>>(null)
  const goal = useRef<{ p: THREE.Vector3; t: THREE.Vector3 } | null>(null)
  const seen = useRef({ view, recenter })

  useEffect(() => {
    const c = ctrl.current
    if (!c) return
    const viewChanged = seen.current.view !== view
    const recentered = seen.current.recenter !== recenter
    seen.current = { view, recenter }
    if (!viewChanged && !recentered) return
    const f = recentered ? focus() : null
    const t = f ? new THREE.Vector3(f.x, 0, f.z) : recentered ? new THREE.Vector3() : c.target.clone()
    goal.current = { t, p: t.clone().add(OFFSET[view]) }
  }, [view, recenter, focus])

  useFrame(({ camera }, dt) => {
    const c = ctrl.current, gl = goal.current
    if (!c || !gl) return
    const k = reduced ? 1 : 1 - Math.exp(-dt * 4.5)
    camera.position.lerp(gl.p, k)
    c.target.lerp(gl.t, k)
    c.update()
    if (camera.position.distanceTo(gl.p) < 0.05 && c.target.distanceTo(gl.t) < 0.05) goal.current = null
  })
  return (
    <OrbitControls
      ref={ctrl} makeDefault enableRotate={view === '3d'} enableDamping dampingFactor={0.08}
      minDistance={14} maxDistance={95} maxPolarAngle={Math.PI / 2.2} onStart={() => { goal.current = null }}
    />
  )
}

/* ---------- sky, lights, glow ---------- */
export function Atmosphere() {
  const quality = useStore((s) => s.quality)
  return (
    <>
      <color attach="background" args={['#071426']} />
      <fog attach="fog" args={['#071426', 80, 170]} />
      <ambientLight intensity={0.55} color="#6b8fc4" />
      <directionalLight position={[-20, 30, 15]} intensity={0.9} color="#7fb0ff" />
      {quality === 'high' && (
        <EffectComposer multisampling={4}>
          <Bloom intensity={0.9} luminanceThreshold={0.62} luminanceSmoothing={0.2} mipmapBlur />
        </EffectComposer>
      )}
    </>
  )
}

/** Animated chevron strip used for the green emergency corridor. */
export function makeChevronTexture(lengthUnits: number) {
  const c = document.createElement('canvas')
  c.width = 128; c.height = 32
  const g = c.getContext('2d')!
  g.strokeStyle = '#eafff5'; g.lineWidth = 5; g.lineCap = 'round'; g.lineJoin = 'round'
  g.beginPath(); g.moveTo(44, 6); g.lineTo(70, 16); g.lineTo(44, 26); g.stroke()
  const t = new THREE.CanvasTexture(c)
  t.wrapS = THREE.RepeatWrapping
  t.colorSpace = THREE.SRGBColorSpace
  t.repeat.set(Math.max(1, lengthUnits / 4), 1)
  return t
}
