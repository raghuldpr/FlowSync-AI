import { useEffect } from 'react'
import { ApiError, api } from './api'
import { flowPoint, type ApiIncident, type Overview, type Topo } from './live'
import { useStore } from './store'

interface Decision { reason: string }

/** Polls the operator overview once a second while signed in. A 401 signs the operator out. */
export function useLivePoller() {
  const token = useStore((s) => s.auth?.token)
  const pollNow = useStore((s) => s.pollNow)
  useEffect(() => {
    if (!token) return
    let alive = true, busy = false, n = 0
    const tick = async () => {
      if (busy) return
      busy = true
      const st = useStore.getState()
      try {
        const topo = st.live.topo ?? (await api<Topo>('GET', '/api/public/topology'))
        const ov = await api<Overview>('GET', '/api/operator/overview', token)
        const extra: { priority?: number; incidents?: ApiIncident[] } = {}
        if (n % 3 === 0) {
          const [dec, inc] = await Promise.all([
            api<Decision[]>('GET', '/api/operator/decisions?limit=200', token),
            api<ApiIncident[]>('GET', '/api/operator/incidents?limit=40', token),
          ])
          extra.priority = dec.filter((d) => d.reason.startsWith('emergency')).length
          extra.incidents = inc
        }
        n++
        if (!alive) return
        const now = Date.now()
        const flow = [...useStore.getState().live.flow, flowPoint(ov, topo, now)].filter((p) => p.t > now - 3_600_000).slice(-3600)
        useStore.getState().setLive({ topo, ov, ok: true, err: null, updated: now, flow, ...extra })
      } catch (e) {
        if (!alive) return
        if (e instanceof ApiError && e.status === 401) useStore.getState().setAuth(null)
        else useStore.getState().setLive({ ok: false, err: e instanceof Error ? e.message : 'backend unreachable' })
      } finally { busy = false }
    }
    tick()
    const id = setInterval(tick, 1000)
    return () => { alive = false; clearInterval(id) }
  }, [token, pollNow])
}
