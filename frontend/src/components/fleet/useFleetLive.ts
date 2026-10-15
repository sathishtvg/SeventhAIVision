/**
 * The fleet map's data, kept current.
 *
 * The live connection says only WHICH vehicles moved or changed status
 * (`fleet_moved`, `fleet_status_changed`) - never where: it reaches everybody
 * in the organisation, and somebody limited to sites must not learn another
 * site's vehicle's position from it. So when it speaks, this asks the server
 * for just those vehicles, through the same site-limited request as the rest,
 * at most once every two seconds, and puts them in place. When the connection
 * is not open the map asks for everything every 30 seconds instead.
 */
import { useEffect, useRef } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useWsStore } from '@/store/websocket'
import { getFleetMap, type FleetMapAnswer } from '@/api/fleet'
import { merge } from './fleetMapFormat'

export const FLEET_EVENTS = ['fleet_moved', 'fleet_status_changed'] as const
const AT_MOST_EVERY_MS = 2_000

/** The vehicle ids a live message names, if it is one of the fleet's. */
export function idsIn(message: string | null): string[] {
  if (!message) return []
  try {
    const event = JSON.parse(message) as { event_type?: string; payload?: { vehicle_ids?: unknown } }
    if (!FLEET_EVENTS.includes(event.event_type as (typeof FLEET_EVENTS)[number])) return []
    const ids = event.payload?.vehicle_ids
    return Array.isArray(ids) ? ids.filter((i): i is string => typeof i === 'string') : []
  } catch {
    return []
  }
}

export function useFleetLive() {
  const qc = useQueryClient()
  const socket = useWsStore((s) => s.status)
  const query = useQuery({
    queryKey: ['fleet-map'],
    queryFn: () => getFleetMap(),
    refetchInterval: socket === 'open' ? 60_000 : 30_000,
  })
  const pending = useRef(new Set<string>())
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    const flush = async () => {
      timer.current = null
      const ids = [...pending.current]
      pending.current.clear()
      if (!ids.length) return
      try {
        const fresh = await getFleetMap(ids)
        qc.setQueryData<FleetMapAnswer>(['fleet-map'], (old) => (old ? { ...old, items: merge(old.items, fresh.items) } : old))
      } catch {
        // A refetch that fails leaves the map as it was; the next message, or the regular refetch, tries again.
      }
    }
    const unsubscribe = useWsStore.subscribe((state, prev) => {
      if (state.lastMessage === prev.lastMessage) return
      const ids = idsIn(state.lastMessage)
      if (!ids.length) return
      ids.forEach((id) => pending.current.add(id))
      if (!timer.current) timer.current = setTimeout(() => { void flush() }, AT_MOST_EVERY_MS)
    })
    return () => {
      unsubscribe()
      if (timer.current) clearTimeout(timer.current)
    }
  }, [qc])

  return query
}
