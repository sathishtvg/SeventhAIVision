import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ThemeProvider } from '@mui/material'
import { render, screen, fireEvent, waitFor, within } from '@/test/utils'
import { theme } from '@/theme/glassmorphism'
import { useAuthStore } from '@/store/auth'
import * as board from '@/api/operationsBoard'
import * as api from '@/api/operationsReports'
import type { ReportKind, ReportList } from '@/api/operationsReports'
import { takenLine } from '@/components/board/boardFormat'
import OperationsBoard from './OperationsBoard'

vi.mock('@/store/auth', () => ({ useAuthStore: vi.fn() }))
// PageHeader reads the tenant's page names from the settings.
vi.mock('@/api/settings', () => ({ getSettings: vi.fn().mockResolvedValue([]), upsertSetting: vi.fn().mockResolvedValue({}) }))
vi.mock('@/api/operationsBoard', async (orig) => {
  const real = await orig<typeof import('@/api/operationsBoard')>()
  const fns = Object.fromEntries(Object.entries(real).map(([k, v]) => [k, typeof v === 'function' ? vi.fn() : v]))
  return { ...fns, apiError: real.apiError }
})
vi.mock('@/api/operationsReports', async (orig) => {
  const real = await orig<typeof import('@/api/operationsReports')>()
  return { ...real, listReports: vi.fn(), takeReport: vi.fn() }
})

function show() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } })
  return render(
    <MemoryRouter>
      <QueryClientProvider client={qc}><ThemeProvider theme={theme}><OperationsBoard /></ThemeProvider></QueryClientProvider>
    </MemoryRouter>, { wrapper: ({ children }) => <>{children}</> })
}

const report = (over: Partial<ReportKind>): ReportKind => ({
  key: 'response', title: 'The response to each incident',
  holds: 'One row for each incident opened in the period: when somebody first acted on it, and when it was resolved.',
  needs: ['response:read', 'incident:read'],
  columns: ['Opened at (Asia/Singapore)', 'Site', 'Incident', 'Seconds until acted on'], periodic: true, may: true,
  why_not: null, ...over })
const NOTE = 'Each report is made now, from the records as they are. Times are where the organisation is. Taking one out is written in the audit log.'
const LIST: ReportList = {
  reports: [
    report({}),
    report({ key: 'device-health', title: 'Device health, as it is read now', holds: 'One row for each device with how it is read now and why.',
             needs: ['asset:read'], columns: ['Kind', 'Device', 'Read as'], periodic: false }),
    report({ key: 'visitors', title: 'Visitor and contractor authorisations', holds: 'One row for each authorisation asked for in the period.',
             needs: ['visitorauth:read'], may: false, why_not: 'Its records are read under visitorauth:read, which you do not hold.' }),
  ],
  periods: [1, 7, 30, 90], max_rows: 10000, timezone: 'Asia/Singapore', note: NOTE,
}
const SITES = { sites: [{ id: 's1', name: 'Factory A', is_active: true, client: null, figures: {} },
                        { id: 's2', name: 'Factory B', is_active: true, client: null, figures: {} }], clients: [] }
const PATIENT = { timeout: 8000 }
const refusal = (detail: unknown, status = 403) => Object.assign(new Error('x'), { response: { status, data: { detail } } })

function signIn(roleId = 2) {
  vi.mocked(useAuthStore).mockImplementation(((sel: (s: unknown) => unknown) =>
    sel({ user: { id: 'me', tenantId: 't1', roleId }, accessToken: 'tok', permissions: null })) as never)
}

beforeEach(() => {
  vi.clearAllMocks()
  signIn()
  vi.mocked(board.getBoard).mockResolvedValue({ sections: [], not_read: [], scope: { site: null, client: null, sites: 2, every_site: true },
                                               period: { days: 1, from: '', to: '' }, as_at: '2026-10-08T04:00:00Z',
                                               clocks_on_since: null, advice: null, note: '' } as never)
  vi.mocked(board.getBoardSites).mockResolvedValue(SITES as never)
  vi.mocked(board.listBriefings).mockResolvedValue({ items: [], can_manage: true, max_days_back: 31 })
  vi.mocked(api.listReports).mockResolvedValue(LIST)
  vi.mocked(api.takeReport).mockResolvedValue({ filename: 'operations-response-20261008.csv', rows: 42, cut: false })
})

const reports = async () => {
  fireEvent.click(await screen.findByRole('tab', { name: 'Reports' }, PATIENT))
  return screen.findAllByTestId('report', {}, PATIENT)
}
const pick = async (label: string, option: string) => {
  fireEvent.mouseDown(screen.getByLabelText(label))
  fireEvent.click(await screen.findByRole('option', { name: option }))
}

describe('the operations reports', () => {
  it('says a report that was taken out in a line, and that it was cut when it was', () => {
    expect(takenLine({ filename: 'a.csv', rows: 42, cut: false }, 10000)).toBe('42 records were taken out as a.csv.')
    expect(takenLine({ filename: 'a.csv', rows: 1, cut: false }, 10000)).toBe('1 record was taken out as a.csv.')
    expect(takenLine({ filename: 'a.csv', rows: 0, cut: false }, 10000)).toBe('0 records were taken out as a.csv.')
    // The count is the server's to give; without it the line does not invent one.
    expect(takenLine({ filename: 'a.csv', rows: null, cut: false }, 10000)).toBe('The file was taken out as a.csv.')
    expect(takenLine({ filename: 'a.csv', rows: 10000, cut: true }, 10000)).toBe(
      '10,000 records were taken out as a.csv. It was cut at 10,000: narrow the period or choose one site to have the rest.')
  })

  it('lists each report with what it holds and what it is read under, and says a file is audited', async () => {
    show()
    const rows = await reports()
    expect(rows.map((r) => r.getAttribute('data-key'))).toEqual(['response', 'device-health', 'visitors'])
    expect(rows[0]).toHaveTextContent('The response to each incident')
    expect(rows[0]).toHaveTextContent('One row for each incident opened in the period')
    expect(rows[0]).toHaveTextContent('For the period chosen · 4 columns · read under response:read and incident:read')
    // A report of how things are now says it has no period.
    expect(rows[1]).toHaveTextContent('As things are now — it has no period · 3 columns · read under asset:read')
    const note = screen.getByTestId('reports-note')
    expect(note).toHaveTextContent('Taking one out is written in the audit log.')
    expect(note).toHaveTextContent('Times are as in Asia/Singapore. A file holds at most 10,000 records, and says so when it was cut.')
    // The columns are shown when asked for, with the time zone a column of times is in.
    expect(within(rows[0]).queryByTestId('columns')).not.toBeInTheDocument()
    fireEvent.click(within(rows[0]).getByRole('button', { name: 'Show the columns' }))
    expect(within(rows[0]).getByTestId('columns')).toHaveTextContent('Opened at (Asia/Singapore) · Site · Incident · Seconds until acted on')
  })

  it('takes a report out for the period and the site chosen, and says what was taken', async () => {
    show()
    const rows = await reports()
    fireEvent.click(within(rows[0]).getByRole('button', { name: 'Take it out' }))
    await waitFor(() => expect(api.takeReport).toHaveBeenCalledWith('response', { site_id: undefined, days: 7 }))
    expect(await within(rows[0]).findByText('42 records were taken out as operations-response-20261008.csv.')).toBeInTheDocument()
    await pick('Period', 'The last 90 days')
    await pick('Site', 'Factory B')
    fireEvent.click(within(rows[1]).getByRole('button', { name: 'Take it out' }))
    await waitFor(() => expect(api.takeReport).toHaveBeenLastCalledWith('device-health', { site_id: 's2', days: 90 }))
    fireEvent.mouseDown(screen.getByLabelText('Period'))
    expect((await screen.findAllByRole('option')).map((o) => o.textContent)).toEqual([
      'The last 24 hours', 'The last 7 days', 'The last 30 days', 'The last 90 days'])
  })

  it('does not offer a report whose records the reader may not read, and says why', async () => {
    show()
    const rows = await reports()
    expect(within(rows[2]).getByRole('button', { name: 'Take it out' })).toBeDisabled()
    expect(rows[2]).toHaveTextContent('Its records are read under visitorauth:read, which you do not hold.')
    expect(within(rows[0]).getByRole('button', { name: 'Take it out' })).toBeEnabled()
    fireEvent.click(within(rows[2]).getByRole('button', { name: 'Take it out' }))
    expect(api.takeReport).not.toHaveBeenCalled()
  })

  it('says when a file was cut, and gives the reason when one is refused', async () => {
    vi.mocked(api.takeReport).mockResolvedValueOnce({ filename: 'operations-response-20261008.csv', rows: 10000, cut: true })
    show()
    const rows = await reports()
    fireEvent.click(within(rows[0]).getByRole('button', { name: 'Take it out' }))
    expect(await within(rows[0]).findByText(/It was cut at 10,000: narrow the period or choose one site/)).toBeInTheDocument()
    vi.mocked(api.takeReport).mockRejectedValueOnce(
      refusal("A report is taken out by the organisation's own staff, not from a support session."))
    fireEvent.click(within(rows[1]).getByRole('button', { name: 'Take it out' }))
    expect(await within(rows[1]).findByText(/not from a support session/)).toBeInTheDocument()
  })

  it('shows the reports to whoever may take one out, and to nobody else', async () => {
    signIn(4)
    show()
    expect((await screen.findAllByRole('tab', {}, PATIENT)).map((t) => t.textContent)).toEqual([
      'Board', 'Sites and customers', 'Daily briefing'])
    expect(api.listReports).not.toHaveBeenCalled()
    signIn(3)
    show()
    await waitFor(() => expect(screen.getAllByRole('tab', { name: 'Reports' })).toHaveLength(1), PATIENT)
  })
})

describe('reading the reason out of a refused file', () => {
  it('reads the server\'s words when the refusal came back as a file', async () => {
    const real = await vi.importActual<typeof import('@/api/operationsReports')>('@/api/operationsReports')
    const { apiClient } = await import('@/api/client')
    const blob = new Blob([JSON.stringify({ detail: 'There is no such report' })], { type: 'application/json' })
    const get = vi.spyOn(apiClient, 'get').mockRejectedValue(Object.assign(new Error('Request failed'), { response: { status: 404, data: blob } }))
    try {
      await expect(real.takeReport('payroll', { days: 7 })).rejects.toSatisfy((e) => real.apiError(e) === 'There is no such report')
      expect(get).toHaveBeenCalledWith('/api/v1/operations-reports/payroll', { params: { days: 7 }, responseType: 'blob' })
    } finally {
      get.mockRestore()
    }
  })
})
