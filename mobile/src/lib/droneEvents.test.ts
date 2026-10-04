/**
 * What an officer is offered on a drone event, and the small things around it.
 *
 * The action list is the part that matters: a button the role may not use is a
 * door onto a 403, and a missing one is a guard who cannot open the incident he
 * is standing in front of. The permissions used here are the ones migration
 * 0123 grants each role.
 */
import { clipPlayerHtml, confidenceLabel, droneActions, eventTitle, guardLine, isDroneRealtime, mapsUrl } from './droneEvents'

const holding = (...codes: string[]) => (permission: string) => codes.includes(permission)

const OPERATOR = holding('drone:event:read', 'drone:event:acknowledge', 'drone:event:investigate',
                         'incident:create', 'incident:dispatch')
const GUARD = holding('drone:event:read', 'incident:create')
const VIEWER = holding('drone:event:read')

describe('droneActions', () => {
  it('offers an operator the whole response on a new event', () => {
    expect(droneActions({ status: 'NEW', incident_id: null }, OPERATOR))
      .toEqual(['acknowledge', 'escalate', 'incident', 'dispatch', 'resolve', 'false-positive'])
  })

  it('offers a guard only what a guard may do: open the incident', () => {
    expect(droneActions({ status: 'NEW', incident_id: null }, GUARD)).toEqual(['incident'])
  })

  it('offers a viewer nothing', () => {
    expect(droneActions({ status: 'NEW', incident_id: null }, VIEWER)).toEqual([])
  })

  it('offers nothing on a closed event, whoever asks', () => {
    expect(droneActions({ status: 'RESOLVED', incident_id: null }, OPERATOR)).toEqual([])
    expect(droneActions({ status: 'FALSE_POSITIVE', incident_id: 'i1' }, OPERATOR)).toEqual([])
  })

  it('does not offer to acknowledge twice, escalate twice, or open a second incident', () => {
    expect(droneActions({ status: 'ACKNOWLEDGED', incident_id: null }, OPERATOR)).not.toContain('acknowledge')
    expect(droneActions({ status: 'ESCALATED', incident_id: null }, OPERATOR)).not.toContain('escalate')
    expect(droneActions({ status: 'NEW', incident_id: 'i1' }, OPERATOR)).not.toContain('incident')
  })

  it('still offers dispatch once the incident exists', () => {
    // Dispatch opens the incident itself if there is none, and is the next step
    // when there is one.
    expect(droneActions({ status: 'ESCALATED', incident_id: 'i1' }, OPERATOR)).toContain('dispatch')
  })
})

describe('wording', () => {
  it('names what was seen', () => {
    expect(eventTitle({ module_type: 'intrusion', label: 'person' })).toBe('Intrusion · person')
    expect(eventTitle({ module_type: 'fire_smoke', label: null })).toBe('Fire smoke')
  })

  it('labels AI confidence as AI confidence', () => {
    // Never a bare percentage next to the risk score.
    expect(confidenceLabel(0.914)).toBe('AI confidence 91%')
    expect(confidenceLabel(null)).toBe('AI confidence —')
  })

  it('describes a guard for the picker', () => {
    const g = { user_id: 'g', full_name: 'G', position_source: 'gps' }
    expect(guardLine({ ...g, available: true, distance_m: 84.4, position_age_s: 20 })).toBe('Free · 84 m')
    expect(guardLine({ ...g, available: false, distance_m: 1530, position_age_s: 600 }))
      .toBe('Busy · 1.5 km · seen 10 min ago')
    expect(guardLine({ ...g, available: true, distance_m: null, position_age_s: null }))
      .toBe('Free · position unknown')
  })
})

describe('mapsUrl', () => {
  it("opens the phone's own maps app at the coordinates", () => {
    expect(mapsUrl(1.3001, 103.8002, 'Intrusion · person', 'android'))
      .toBe('geo:1.3001,103.8002?q=1.3001,103.8002(Intrusion%20%C2%B7%20person)')
    expect(mapsUrl(1.3001, 103.8002, 'Fire', 'ios')).toBe('maps://?ll=1.3001,103.8002&q=Fire')
  })
})

describe('isDroneRealtime', () => {
  const ev = (event_type: string, payload: Record<string, unknown> = {}) =>
    ({ event_type, payload, tenant_id: 't', occurred_at: '' })

  it('refreshes on drone events and drone alerts', () => {
    expect(isDroneRealtime(ev('drone_event_created'))).toBe(true)
    expect(isDroneRealtime(ev('drone_event_updated'))).toBe(true)
    expect(isDroneRealtime(ev('alert_created', { module_type: 'drone_patrol' }))).toBe(true)
  })

  it('ignores the telemetry stream and other modules', () => {
    // A flying drone announces its position every couple of seconds; reloading
    // the event list on each would never let it rest.
    expect(isDroneRealtime(ev('drone_telemetry'))).toBe(false)
    expect(isDroneRealtime(ev('alert_created', { module_type: 'lpr' }))).toBe(false)
  })
})

describe('clipPlayerHtml', () => {
  const html = clipPlayerHtml('http://10.0.0.5:8000/api/v1/drone-media/m1/file', 'Bearer abc')

  it('fetches the clip with the token in a header', () => {
    expect(html).toContain('"http://10.0.0.5:8000/api/v1/drone-media/m1/file"')
    expect(html).toContain('Authorization: auth')
    expect(html).toContain('"Bearer abc"')
    expect(html).not.toContain('token=')
  })

  it('cannot be broken out of by its inputs', () => {
    const hostile = clipPlayerHtml('http://x/</script><script>alert(1)</script>', '"; alert(2); "')
    expect(hostile).not.toContain('</script><script>alert(1)')
    expect(hostile.match(/<script>/g)).toHaveLength(1)
    expect(hostile).toContain('\\"; alert(2); \\"')
  })

  it('plays without a token rather than sending an empty header', () => {
    expect(clipPlayerHtml('http://x/y', undefined)).toContain('auth = ""')
  })
})
