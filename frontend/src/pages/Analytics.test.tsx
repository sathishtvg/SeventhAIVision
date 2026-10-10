import { render, screen, within } from '@/test/utils'
import Analytics from '@/pages/Analytics'
import * as api from '@/api/analytics'
import { useAuthStore } from '@/store/auth'
import type { TopCamera } from '@/types/api'

vi.mock('@/store/auth', () => ({ useAuthStore: vi.fn() }))
// PageHeader reads the tenant's page names from the settings.
vi.mock('@/api/settings', () => ({ getSettings: vi.fn().mockResolvedValue([]), upsertSetting: vi.fn().mockResolvedValue({}) }))
vi.mock('@/api/analytics', () => ({
  getSummary: vi.fn(), getAlertsBySeverity: vi.fn(), getAlertsByModule: vi.fn(), getDetectionsTrend: vi.fn(),
  getAlertsTrend: vi.fn(), getTopCameras: vi.fn(), getIncidentResolutionTime: vi.fn(),
}))

const CAMERAS: TopCamera[] = [
  { camera_id: 'aaaaaaaa-0000-0000-0000-000000000001', camera_name: 'Reception', alert_count: 46 },
  { camera_id: 'bbbbbbbb-0000-0000-0000-000000000002', camera_name: 'Main Atrium', alert_count: 23 },
]
// What the API gives for alerts that did not come from a camera — a roster or
// a payroll alert: one row, counted together, with no camera at all.
const NOT_A_CAMERA: TopCamera = { camera_id: null, camera_name: null, alert_count: 10 }

beforeEach(() => {
  vi.mocked(useAuthStore).mockImplementation(((sel: (s: unknown) => unknown) =>
    sel({ user: { id: 'me', tenantId: 't1', roleId: 2 }, accessToken: 'tok', permissions: null })) as never)
  vi.mocked(api.getSummary).mockResolvedValue({
    open_alerts: 3, open_incidents: 1, active_cameras: 11, active_cameras_total: 12, detections_today: 2,
    alerts_today: 10, alerts_7d: 14, detections_7d: 6, active_recordings: 1, detections_window: 197,
    alerts_window: 207, detections_window_prev: 0, alerts_window_prev: 0,
    last_detection_at: new Date().toISOString(), last_alert_at: new Date().toISOString() })
  vi.mocked(api.getAlertsBySeverity).mockResolvedValue([{ label: 'critical', count: 86 }])
  vi.mocked(api.getAlertsByModule).mockResolvedValue([{ label: 'intrusion', count: 70 }, { label: 'payroll', count: 6 }])
  vi.mocked(api.getDetectionsTrend).mockResolvedValue([])
  vi.mocked(api.getAlertsTrend).mockResolvedValue([])
  vi.mocked(api.getIncidentResolutionTime).mockResolvedValue({ resolved_count: 4, avg_hours: 2.5, p95_hours: null })
  vi.mocked(api.getTopCameras).mockResolvedValue(CAMERAS)
})

const ranked = async () => {
  const table = (await screen.findByText('Reception')).closest('table') as HTMLElement
  return within(table).getAllByRole('row').slice(1)
}

describe('Analytics', () => {
  it('ranks the cameras by their alerts', async () => {
    render(<Analytics />)
    const rows = await ranked()
    expect(rows).toHaveLength(2)
    expect(rows[0]).toHaveTextContent('#1')
    expect(rows[0]).toHaveTextContent('Reception')
    expect(rows[0]).toHaveTextContent('aaaaaaaa')
    expect(rows[1]).toHaveTextContent('Main Atrium')
    expect(screen.queryByText(/did not come from a camera/)).not.toBeInTheDocument()
  })

  // The page went blank on this: it shortened the id of every row, and the row
  // for alerts without a camera has none.
  it('does not break on alerts that came from no camera, and counts them under the table', async () => {
    vi.mocked(api.getTopCameras).mockResolvedValue([CAMERAS[0], NOT_A_CAMERA, CAMERAS[1]])
    render(<Analytics />)
    const rows = await ranked()
    expect(rows.map((r) => r.textContent)).toEqual([
      expect.stringContaining('Reception'), expect.stringContaining('Main Atrium')])
    expect(rows[1]).toHaveTextContent('#2')
    expect(screen.getByText(/10 more alerts in this period did not come from a camera/)).toBeInTheDocument()
    // The rest of the page is still there.
    expect(screen.getByText('Alerts by Module (30d)')).toBeInTheDocument()
  })

  it('says there is no alert data when the only alerts came from no camera', async () => {
    vi.mocked(api.getTopCameras).mockResolvedValue([{ ...NOT_A_CAMERA, alert_count: 1 }])
    render(<Analytics />)
    expect(await screen.findByText('No alert data')).toBeInTheDocument()
    expect(screen.getByText(/1 more alert in this period did not come from a camera/)).toBeInTheDocument()
  })
})
