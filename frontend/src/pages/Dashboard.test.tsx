import { render, screen } from '@/test/utils'
import Dashboard from '@/pages/Dashboard'
import { getAlerts } from '@/api/alerts'
import type { Alert } from '@/types/api'

vi.mock('@/api/alerts', () => ({ getAlerts: vi.fn() }))
vi.mock('@/api/incidents', () => ({ getIncidents: vi.fn() }))
vi.mock('@/api/cameras', () => ({ getCameras: vi.fn() }))
vi.mock('@/api/detections', () => ({ getDetections: vi.fn() }))

const mockAlert: Alert = {
  id: 'a1', title: 'Blocklist Hit', severity: 'critical', status: 'open',
  camera_id: 'c1', module_type: 'lpr', created_at: new Date().toISOString(),
  detection_id: null, alert_code: 'lpr.blocklist_hit', message: null,
  acknowledged_by_user_id: null, acknowledged_at: null,
}

beforeEach(() => {
  vi.mocked(getAlerts).mockResolvedValue({ items: [mockAlert], has_more: false })
})

describe('Dashboard', () => {
  it('renders all four KPI card labels', () => {
    render(<Dashboard />)
    expect(screen.getByText('Open Alerts')).toBeInTheDocument()
    expect(screen.getByText('Open Incidents')).toBeInTheDocument()
    expect(screen.getByText('Cameras Active')).toBeInTheDocument()
    expect(screen.getByText('Detections Today')).toBeInTheDocument()
  })

  it('renders Recent Open Alerts section header', () => {
    render(<Dashboard />)
    expect(screen.getByText('Recent Open Alerts')).toBeInTheDocument()
  })

  it('shows alert title after data loads', async () => {
    render(<Dashboard />)
    expect(await screen.findByText('Blocklist Hit')).toBeInTheDocument()
  })

  it('shows empty state message when there are no open alerts', async () => {
    vi.mocked(getAlerts).mockResolvedValue({ items: [], has_more: false })
    render(<Dashboard />)
    expect(await screen.findByText('No open alerts')).toBeInTheDocument()
  })
})
