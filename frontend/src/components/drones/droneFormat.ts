/**
 * The drone screens' non-component helpers: map tiles, words and colours for
 * statuses, time formatting, map marker icons and the live-update hook. Kept
 * apart from droneUi.tsx so that file exports components only (fast refresh).
 */
import { useEffect } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import L from 'leaflet'
import { useWsStore } from '@/store/websocket'
import type { RiskLevel } from '@/api/drones'

export const TILE_URL =
  import.meta.env.VITE_MAP_TILE_URL ?? 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png'
export const TILE_ATTRIBUTION =
  import.meta.env.VITE_MAP_TILE_ATTRIBUTION ?? '&copy; OpenStreetMap contributors'
/** Singapore, as elsewhere in the app, when nothing has a position yet. */
export const DEFAULT_CENTER: [number, number] = [1.3521, 103.8198]

export const pretty = (s: string | null | undefined) =>
  (s ?? '').replace(/_/g, ' ').toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase())

export const RISK_COLOR: Record<RiskLevel, string> = {
  INFO: '#8ea0b8', LOW: '#00c48c', MEDIUM: '#ffb020', HIGH: '#ff7a45', CRITICAL: '#ff3b5c',
}

export const ZONE_COLOR: Record<string, string> = {
  NORMAL: '#8ea0b8', RESTRICTED: '#ffb020', CRITICAL: '#ff3b5c', VEHICLE_RESTRICTED: '#c77dff',
  PERSON_RESTRICTED: '#ff7a45', NO_ENTRY: '#ff3b5c', SPECIAL_INSPECTION: '#00c2ff',
}

export function ago(seconds: number | null | undefined): string {
  if (seconds == null) return 'never'
  if (seconds < 60) return `${seconds}s ago`
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`
  return `${Math.floor(seconds / 86400)}d ago`
}

export function fmt(iso: string | null | undefined): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'medium' })
}

/** Refresh the drone queries when the server announces a drone change. */
export function useDroneRealtime(keys: string[][], filter?: (type: string, payload: Record<string, unknown>) => boolean) {
  const last = useWsStore((s) => s.lastMessage)
  const qc = useQueryClient()
  useEffect(() => {
    if (!last) return
    try {
      const msg = JSON.parse(last) as { event_type?: string; payload?: Record<string, unknown> }
      const type = msg.event_type ?? ''
      if (!type.startsWith('drone_') && !(type === 'alert_created' && msg.payload?.module_type === 'drone_patrol')
          && type !== 'incident_created') return
      if (filter && !filter(type, msg.payload ?? {})) return
      keys.forEach((k) => qc.invalidateQueries({ queryKey: k }))
    } catch {
      /* not JSON: not ours */
    }
    // keys are literals at each call site
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [last])
}

/** A numbered marker drawn with CSS — no image assets to load. */
export function numberIcon(label: string, color = '#4f8cff', size = 26) {
  return L.divIcon({
    className: 'drone-num-icon',
    html: `<div style="width:${size}px;height:${size}px;border-radius:50%;background:${color};color:#fff;`
      + `display:flex;align-items:center;justify-content:center;font:700 12px/1 sans-serif;`
      + `border:2px solid #fff;box-shadow:0 1px 4px rgba(0,0,0,.5)">${label}</div>`,
    iconSize: [size, size],
    iconAnchor: [size / 2, size / 2],
  })
}

export function droneIcon(heading: number | null | undefined, color = '#00d4ff') {
  const rot = heading ?? 0
  return L.divIcon({
    className: 'drone-pos-icon',
    html: `<div style="width:34px;height:34px;display:flex;align-items:center;justify-content:center;`
      + `transform:rotate(${rot}deg)"><svg width="30" height="30" viewBox="0 0 24 24">`
      + `<path d="M12 2 L19 20 L12 16 L5 20 Z" fill="${color}" stroke="#fff" stroke-width="1.5"/></svg></div>`,
    iconSize: [34, 34],
    iconAnchor: [17, 17],
  })
}
