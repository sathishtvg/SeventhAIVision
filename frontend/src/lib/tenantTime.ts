import { useQuery } from '@tanstack/react-query'
import { getBranding } from '@/api/branding'

/**
 * Render timestamps in the TENANT's timezone, not the browser's.
 *
 * Every date in this app was formatted with `toLocaleString(undefined, …)`,
 * which uses whatever timezone the operator's machine is set to. The backend
 * does not: rosters, attendance and the visitor day-close all reason in the
 * tenant's timezone, held on the tenants row.
 *
 * Those agree only when the machine happens to be set correctly, and gatehouse
 * and wall-display PCs frequently are not. Observed on a real deployment: the
 * tenant is Asia/Singapore while the machine was India Standard Time, two and
 * a half hours apart — so the board displayed 5 Sep 23:03 while the system's
 * date had already become 6 Sep, and the day-close that fires at SG midnight
 * landed at 21:30 on screen. A guard reading that board cannot reconcile it
 * with what the system does, and neither can anyone reading a report from it.
 *
 * A site's clock is a property of the site, not of the machine you happen to
 * be looking at it from — which also makes a client checking their portal from
 * abroad see the times their guards actually worked.
 */
export function useTenantTimeZone(): string | undefined {
  const { data } = useQuery({
    queryKey: ['branding'],
    queryFn: getBranding,
    staleTime: 5 * 60 * 1000,
  })
  // undefined, never a guessed fallback: Intl treats undefined as "the
  // browser's zone", which is the current behaviour and the right thing to do
  // while branding is still loading. Substituting a hardcoded zone here would
  // be worse than the problem, silently mislabelling times for every tenant
  // that is not in it.
  return data?.timezone || undefined
}

/** Date and time, e.g. "06 Sep, 01:33". */
export function formatDateTimeIn(iso: string | null | undefined, timeZone?: string): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  return d.toLocaleString(undefined, {
    day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', timeZone,
  })
}

/** Clock time only, e.g. "01:33". */
export function formatTimeIn(iso: string | null | undefined, timeZone?: string): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  return d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', timeZone })
}

/** Short zone label for a column header or caption, e.g. "SGT" or "GMT+8". */
export function timeZoneLabel(timeZone?: string): string | null {
  if (!timeZone) return null
  try {
    const parts = new Intl.DateTimeFormat(undefined, { timeZone, timeZoneName: 'short' })
      .formatToParts(new Date())
    return parts.find((p) => p.type === 'timeZoneName')?.value ?? null
  } catch {
    // An unknown IANA name from tenant config must not take the page down.
    return null
  }
}
