/**
 * The decisions behind the drone screens, kept out of the components so they
 * can be tested without a renderer: which actions an officer is offered, how an
 * event is worded, where "open in maps" points, and the page that plays a clip.
 */
import type { DroneEvent, GuardOption } from '@/api/drones'
import { OPEN_STATUSES } from '@/api/drones'
import type { RealtimeEvent } from '@/hooks/useWebSocket'

export type DroneAction = 'acknowledge' | 'escalate' | 'incident' | 'dispatch' | 'resolve' | 'false-positive'

/** "fire_smoke" → "Fire smoke" */
export const pretty = (s: string | null | undefined): string => {
  const t = (s ?? '').replace(/_/g, ' ').toLowerCase()
  return t.charAt(0).toUpperCase() + t.slice(1)
}

/** What was seen, in a line: the module and, when the AI named it, the thing. */
export const eventTitle = (e: Pick<DroneEvent, 'module_type' | 'label'>): string =>
  e.label ? `${pretty(e.module_type)} · ${e.label}` : pretty(e.module_type)

/** AI confidence, always labelled as such so it is never read as the risk. */
export const confidenceLabel = (v: number | null | undefined): string =>
  v == null ? 'AI confidence —' : `AI confidence ${Math.round(v * 100)}%`

/**
 * The actions to offer on an event, in the order they are shown.
 *
 * `can` answers whether the user holds a permission. Each action carries the
 * permission its own endpoint demands, so a button is never a door onto a 403.
 * A closed event offers nothing: the server refuses every one of these on it.
 */
export function droneActions(
  e: Pick<DroneEvent, 'status' | 'incident_id'>,
  can: (permission: string) => boolean,
): DroneAction[] {
  if (!OPEN_STATUSES.includes(e.status)) return []
  const out: DroneAction[] = []
  if (e.status === 'NEW' && can('drone:event:acknowledge')) out.push('acknowledge')
  if (e.status !== 'ESCALATED' && can('drone:event:investigate')) out.push('escalate')
  if (!e.incident_id && can('incident:create')) out.push('incident')
  if (can('incident:dispatch')) out.push('dispatch')
  if (can('drone:event:investigate')) out.push('resolve', 'false-positive')
  return out
}

/**
 * A link that opens the device's own maps app at the event.
 *
 * The geo: and maps: schemes hand the coordinates to whichever maps app the
 * officer has chosen, rather than to a particular web service.
 */
export function mapsUrl(lat: number, lng: number, label: string, os: string): string {
  const q = encodeURIComponent(label)
  return os === 'ios'
    ? `maps://?ll=${lat},${lng}&q=${q}`
    : `geo:${lat},${lng}?q=${lat},${lng}(${q})`
}

/** One line about a guard for the picker: free or busy, how far, how fresh. */
export function guardLine(g: GuardOption): string {
  const parts = [g.available ? 'Free' : 'Busy']
  if (g.distance_m != null) {
    parts.push(g.distance_m >= 1000 ? `${(g.distance_m / 1000).toFixed(1)} km` : `${Math.round(g.distance_m)} m`)
    if (g.position_age_s != null && g.position_age_s >= 120) {
      parts.push(`seen ${Math.round(g.position_age_s / 60)} min ago`)
    }
  } else {
    parts.push('position unknown')
  }
  return parts.join(' · ')
}

/** Whether a realtime announcement should refresh the drone screens. */
export function isDroneRealtime(e: RealtimeEvent): boolean {
  if (e.event_type.startsWith('drone_event') || e.event_type === 'drone_media_synced') return true
  return e.event_type === 'alert_created' && e.payload?.module_type === 'drone_patrol'
}

/** A string safe to place inside a <script> block. */
const js = (s: string) => JSON.stringify(s).replace(/</g, '\\u003c')

/**
 * The page that plays a drone clip.
 *
 * A <video> cannot send an Authorization header and the media endpoint accepts
 * the token nowhere else, so the page fetches the file itself and plays it from
 * memory. It is loaded with the API as its base URL, which makes that fetch
 * same-origin. A refusal — most often 409, the clip is still at the site — is
 * shown in the server's own words.
 */
export function clipPlayerHtml(url: string, authorization: string | undefined): string {
  return `<!DOCTYPE html>
<html>
<head>
  <meta name="viewport" content="width=device-width, initial-scale=1, user-scalable=no">
  <style>
    html, body { margin: 0; height: 100%; background: #000; }
    video { width: 100%; height: 100%; object-fit: contain; display: none; }
    #msg { color: #ccc; font: 15px sans-serif; padding: 24px; text-align: center; line-height: 1.5; }
  </style>
</head>
<body>
  <div id="msg">Loading the clip…</div>
  <video id="v" controls playsinline></video>
  <script>
    (function () {
      var url = ${js(url)}, auth = ${js(authorization ?? '')};
      var msg = document.getElementById('msg'), v = document.getElementById('v');
      function refused(status) { return new Error('The clip could not be loaded (' + status + ').'); }
      fetch(url, { headers: auth ? { Authorization: auth } : {} })
        .then(function (r) {
          if (r.ok) return r.blob();
          return r.json().then(
            function (b) { throw (b && typeof b.detail === 'string') ? new Error(b.detail) : refused(r.status); },
            function () { throw refused(r.status); });
        })
        .then(function (b) {
          v.src = URL.createObjectURL(b);
          msg.style.display = 'none';
          v.style.display = 'block';
          v.play().catch(function () {});
        })
        .catch(function (e) { msg.textContent = e.message || 'The clip could not be loaded.'; });
    })();
  </script>
</body>
</html>`
}
