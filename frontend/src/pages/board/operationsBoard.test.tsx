import type { ReactNode } from 'react'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ThemeProvider } from '@mui/material'
import { render, screen, fireEvent, waitFor, within } from '@/test/utils'
import { theme } from '@/theme/glassmorphism'
import { useAuthStore } from '@/store/auth'
import * as api from '@/api/operationsBoard'
import type { Board, BoardSites, Briefing, BriefingSummary, Figures } from '@/api/operationsBoard'
import {
  AS_AT_LABEL, COLUMNS, DEVICE_LABEL, STATE_LABEL, briefingFor, briefingLine, cell, clocksMissed, dayBefore, lasting,
  over, patrolLine, patrolsNotDone, periodLabel,
} from '@/components/board/boardFormat'
import OperationsBoard from './OperationsBoard'

vi.mock('@/store/auth', () => ({ useAuthStore: vi.fn() }))
// PageHeader reads the tenant's page names from the settings.
vi.mock('@/api/settings', () => ({ getSettings: vi.fn().mockResolvedValue([]), upsertSetting: vi.fn().mockResolvedValue({}) }))
vi.mock('@/api/operationsBoard', async (orig) => {
  const real = await orig<typeof import('@/api/operationsBoard')>()
  const fns = Object.fromEntries(Object.entries(real).map(([k, v]) => [k, typeof v === 'function' ? vi.fn() : v]))
  return { ...fns, apiError: real.apiError }
})

function show(el: ReactNode = <OperationsBoard />) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } })
  return render(
    <MemoryRouter>
      <QueryClientProvider client={qc}><ThemeProvider theme={theme}>{el}</ThemeProvider></QueryClientProvider>
    </MemoryRouter>, { wrapper: ({ children }) => <>{children}</> })
}

const NOTE = 'Counts of what is recorded, for the period and the sites shown. Figures marked “now” are as things stand at the moment of asking. Nothing here is a score or a forecast.'
const A: Required<Figures> = {
  INCIDENTS: { opened: 4, by_severity: { critical: 1, high: 2, medium: 1, low: 0 }, resolved: 2, opened_still_open: 3, open_now: 5 },
  RESPONSE: { opened: 4, acknowledged: 3, acknowledge_seconds: 240, resolved: 1, resolve_seconds: 600, sent: 2, arrived: 1,
              declined: 1, arrive_seconds: 360, missed: { acknowledge: 1, arrival: 0, resolve: 1 } },
  PATROLS: { tours: { scheduled: 3, done: 1, partial: 0, missed: 1, failed: 0, open: 1, cancelled: 0 },
             virtual: { scheduled: 4, done: 1, partial: 1, missed: 1, failed: 0, open: 1, cancelled: 1 },
             drone: null },
  GUARDS: { on_shift_now: 6, due_not_started_now: 1, shifts: 14, worked: 12, late: 2, not_started: 1 },
  DEVICES: { devices: 42, by_state: { OK: 36, DEGRADED: 2, DOWN: 3, NOT_KNOWN: 1, OFF: 0 } },
  VISITORS: { on_site_now: 7, arrived: 23, departed: 16, refused: 1, waiting_now: 2 },
  MAINTENANCE: { suggested_now: 1, open_now: 2, in_progress_now: 1, overdue_now: 1, raised: 2, done: 1 },
}
const B: Figures = { ...A, INCIDENTS: { ...A.INCIDENTS, opened: 1, open_now: 1 }, GUARDS: { ...A.GUARDS, on_shift_now: 2 } }
const TITLES = { INCIDENTS: 'Incidents', RESPONSE: 'Response', PATROLS: 'Patrols', GUARDS: 'Guards on shift',
                 DEVICES: 'Devices', VISITORS: 'Visitors', MAINTENANCE: 'Maintenance' } as const
const KEYS = Object.keys(TITLES) as (keyof typeof TITLES)[]
const PERIOD = { days: 1, from: '2026-10-07T04:00:00Z', to: '2026-10-08T04:00:00Z' }
const ACME = { id: 'c1', name: 'Acme Properties' }
const NOT_READ = [{ key: 'drone', title: 'Drone patrols', needs: 'drone:read', reason: 'Read under drone:read, which you do not hold.' }]
const BOARD: Board = {
  period: PERIOD, as_at: '2026-10-08T04:00:00Z', scope: { site: null, client: null, sites: 2, every_site: true },
  sections: KEYS.map((key) => ({ key, title: TITLES[key], counted_from: `${TITLES[key]} are counted from their own records.`,
                                 figures: A[key] })),
  not_read: NOT_READ, clocks_on_since: '2026-10-01T02:00:00Z',
  advice: { weeks: 4, standing: 3, by_level: { HIGH: 0, MEDIUM: 2, LOW: 1 }, note: 'A pattern that recurred is not a forecast.' },
  note: NOTE,
}
const SITES: BoardSites = {
  period: PERIOD, as_at: BOARD.as_at, client: null, sections: KEYS.map((key) => ({ key, title: TITLES[key] })),
  sites: [{ id: 's1', name: 'Factory A', is_active: true, client: ACME, figures: A },
          { id: 's2', name: 'Factory B', is_active: true, client: ACME, figures: B },
          { id: 's3', name: 'Old yard', is_active: false, client: null, figures: { GUARDS: A.GUARDS } }],
  no_site: { INCIDENTS: { ...A.INCIDENTS, opened: 9 } },
  clients: [{ ...ACME, sites: 2, figures: { ...A, RESPONSE: { ...A.RESPONSE, acknowledge_seconds: null } } }],
  total: A, not_read: NOT_READ, clocks_on_since: BOARD.clocks_on_since, note: NOTE,
}
const DRAFT: BriefingSummary = {
  id: 'b1', site: { id: 's1', name: 'Factory A' }, briefing_date: '2026-10-07', revision: 1, state: 'DRAFT', replaced_by: null,
  period: { from: '2026-10-06T16:00:00Z', to: '2026-10-07T16:00:00Z', timezone: 'Asia/Singapore', whole_day: true },
  drafted_at: '2026-10-08T01:00:00Z', drafted_by_name: 'Siti Rahman', published_at: null, published_by_name: null,
  note: null, left_out: [{ key: 'VISITORS', title: 'Visitors' }],
  may: { edit: true, recount: true, publish: true, discard: true, correct: false },
}
const SECTIONS: Briefing['sections'] = [
  { key: 'INCIDENTS', title: 'Incidents', left_out: false, lines: [
    { text: '4 incidents were opened: 1 critical, 2 high and 1 medium.', as_at: 'PERIOD' },
    { text: '3 of those opened are still open.', as_at: 'DRAFTING' }] },
  { key: 'VISITORS', title: 'Visitors', left_out: true, lines: [
    { text: '23 arrivals and 16 departures were logged at the gate; 1 visitor was refused.', as_at: 'PERIOD' }] },
  { key: 'ADVICE', title: 'What stands out', left_out: false, note: 'A pattern that recurred is not a forecast: nothing here says what will happen.', lines: [
    { text: '87% of the missed patrols of the last 4 weeks fell between 08:00 and 12:00 (13 of 15). Rests on 15 records over 4 weeks.', as_at: 'WEEKS' }] },
]
const BRIEFING: Briefing = {
  ...DRAFT, sections: SECTIONS, not_read: [{ key: 'drone', title: 'Drone patrols', needs: 'drone:read' }],
  drafting_note: 'As things stood when this was drafted, not at the end of the day.',
}
const OUT: BriefingSummary = {
  ...DRAFT, id: 'b0', briefing_date: '2026-10-06', state: 'PUBLISHED', published_at: '2026-10-07T01:10:00Z',
  published_by_name: 'Siti Rahman', note: 'Gate 2 is closed for works until Friday.',
  may: { edit: false, recount: false, publish: false, discard: false, correct: true },
}
const PUBLISHED: Briefing = { ...BRIEFING, ...OUT, sections: SECTIONS.filter((s) => !s.left_out) }
const refusal = (detail: unknown, status = 409) => Object.assign(new Error('x'), { response: { status, data: { detail } } })
const PATIENT = { timeout: 8000 }

function signIn(roleId = 2) {
  vi.mocked(useAuthStore).mockImplementation(((sel: (s: unknown) => unknown) =>
    sel({ user: { id: 'me', tenantId: 't1', roleId }, accessToken: 'tok', permissions: null })) as never)
}

beforeEach(() => {
  vi.clearAllMocks()
  signIn()
  vi.mocked(api.getBoard).mockResolvedValue(BOARD)
  vi.mocked(api.getBoardSites).mockResolvedValue(SITES)
  vi.mocked(api.listBriefings).mockResolvedValue({ items: [DRAFT, OUT], can_manage: true, max_days_back: 31 })
  vi.mocked(api.getBriefing).mockImplementation(async (id) => (id === 'b0' ? PUBLISHED : BRIEFING))
  for (const fn of [api.draftBriefing, api.reviewBriefing, api.recountBriefing, api.publishBriefing, api.discardBriefing]) {
    vi.mocked(fn).mockResolvedValue(BRIEFING)
  }
})

const pick = async (label: string, option: string) => {
  fireEvent.mouseDown(screen.getByLabelText(label))
  fireEvent.click(await screen.findByRole('option', { name: option }))
}
const tiles = (section: HTMLElement) => within(section).getAllByTestId('tile').map((t) => t.textContent)
/** A button once it can be pressed: while something is being kept, the others wait. */
const ready = async (name: string) => {
  const button = await screen.findByRole('button', { name }, PATIENT)
  await waitFor(() => expect(button).toBeEnabled(), PATIENT)
  return button
}
const openTab = async (name: string) => fireEvent.click(await screen.findByRole('tab', { name }, PATIENT))

describe('the words', () => {
  it('says a period, a length of time and a figure that is not read', () => {
    expect([1, 7, 30].map(periodLabel)).toEqual(['The last 24 hours', 'The last 7 days', 'The last 30 days'])
    expect([null, 0, 59, 60, 240, 3599, 4800, 97200].map(lasting)).toEqual([
      '—', 'under a minute', 'under a minute', '1 minute', '4 minutes', '1 hour', '1 hour 20 minutes', '1 day 3 hours'])
    // Nothing measured is a dash and not zero; so is a section that is not read.
    expect([cell(null), cell(0), cell(1532)]).toEqual(['—', '0', '1,532'])
    expect(DEVICE_LABEL.NOT_KNOWN).toBe('No reading')
    expect(STATE_LABEL).toEqual({ DRAFT: 'Draft', PUBLISHED: 'Published', DISCARDED: 'Set aside' })
    expect(AS_AT_LABEL).toEqual({ PERIOD: null, DRAFTING: 'when drafted', WEEKS: 'the 4 weeks before' })
  })

  it('says a kind of patrol in a line', () => {
    const none = { scheduled: 0, done: 0, partial: 0, missed: 0, failed: 0, open: 0, cancelled: 0 }
    expect(patrolLine(none)).toBe('None fell due')
    expect(patrolLine({ ...none, scheduled: 2, open: 2 })).toBe('2 still to be done; none is over yet')
    expect(patrolLine(A.PATROLS.tours!)).toBe('1 done of 2 over; 1 missed; 1 still to be done')
    expect(patrolLine(A.PATROLS.virtual!)).toBe('1 done of 3 over; 1 done in part, 1 missed; 1 still to be done')
    expect(patrolLine({ ...none, scheduled: 5, done: 5 })).toBe('5 done of 5 over')
    expect(over(A.PATROLS.virtual!)).toBe(3)
  })

  it('takes one figure from each section for the table, and none from a section that is not read', () => {
    expect(COLUMNS.map((c) => c.of(A))).toEqual([4, 5, 2, 2, 6, 3, 7, 1])
    expect(COLUMNS.map((c) => c.of({}))).toEqual(COLUMNS.map(() => null))
    // A kind of patrol that is not read adds nothing; patrols not read at all is not a zero.
    expect([patrolsNotDone(A), patrolsNotDone({}), clocksMissed({})]).toEqual([2, null, null])
    for (const column of COLUMNS) expect(column.label).not.toMatch(/score|rating|rank|grade|risk/i)
  })

  it('says whose a briefing is, and a day as the server takes it', () => {
    expect([briefingFor(DRAFT), briefingFor({ site: null })]).toEqual(['Factory A', 'Every site together'])
    expect(briefingLine(DRAFT)).toMatch(/^Drafted by Siti Rahman, /)
    expect(briefingLine(OUT)).toMatch(/^Published by Siti Rahman, /)
    expect(briefingLine({ ...OUT, published_by_name: null })).toMatch(/^Published by somebody no longer on the system, /)
    const noon = new Date(2026, 9, 8, 12)
    expect([dayBefore(0, noon), dayBefore(1, noon), dayBefore(8, noon)]).toEqual(['2026-10-08', '2026-10-07', '2026-09-30'])
  })
})

describe('the board', () => {
  it('shows each section as counts, and marks what is as things stand now', async () => {
    show()
    const incidents = await screen.findByTestId('section-INCIDENTS', {}, PATIENT)
    expect(tiles(incidents)).toEqual(['4Opened', '2Resolved', '3Of those opened, still open · now', '5Open in all · now'])
    expect(incidents).toHaveTextContent('1 critical2 high1 medium')
    expect(incidents).toHaveTextContent('Incidents are counted from their own records.')
    expect(tiles(screen.getByTestId('section-GUARDS'))).toEqual([
      '6On shift · now', '1Due, not started · now', '14Shifts due to begin12 worked', '2Started late', '1Not started'])
    expect(tiles(screen.getByTestId('section-VISITORS'))).toEqual([
      '7On site · now', '2Visits waiting for a decision · now', '23Arrivals logged', '16Departures logged', '1Refused'])
    expect(tiles(screen.getByTestId('section-MAINTENANCE'))).toEqual([
      '1Suggestions waiting for a person · now', '3Orders in hand · now1 overdue', '2Raised', '1Completed'])
    const devices = screen.getByTestId('section-DEVICES')
    expect(tiles(devices)).toEqual(['42Devices · now'])
    // What needs somebody first; nothing in a state is not shown as a zero chip.
    expect(devices).toHaveTextContent('Down: 3Degraded: 2No reading: 1Working: 36')
    expect(devices).not.toHaveTextContent('Switched off')
    expect(screen.getByText(NOTE)).toBeInTheDocument()
    expect(screen.getByTestId('scope')).toHaveTextContent(/^Every site · 2 sites · counted /)
    expect(screen.getByText(/Counts of what is recorded — not a score/)).toBeInTheDocument()
    expect(api.getBoard).toHaveBeenCalledWith({ site_id: undefined, client_id: undefined, days: 1 })
  })

  it('gives a time as the middle one with what it was measured from, and the clocks as they are', async () => {
    show()
    const response = await screen.findByTestId('section-RESPONSE', {}, PATIENT)
    expect(tiles(response)).toEqual([
      '3 of 4Acted onhalf within 4 minutes', '1 of 4Resolvedhalf within 10 minutes', '2Guards sent1 arrived, 1 declined',
      '6 minutesSent to arrivedthe middle time', '2Response clocks missed'])
    expect(screen.getByTestId('clocks')).toHaveTextContent('Clocks missed: 1 to acknowledge, 0 to arrive, 1 to resolve.')
  })

  it('does not call clocks that are switched off "none missed", or an unmeasured time zero', async () => {
    vi.mocked(api.getBoard).mockResolvedValue({
      ...BOARD, clocks_on_since: null,
      sections: BOARD.sections.map((s) => (s.key === 'RESPONSE'
        ? { ...s, figures: { ...A.RESPONSE, acknowledged: 0, acknowledge_seconds: null, arrive_seconds: null } } : s)) })
    show()
    const response = await screen.findByTestId('section-RESPONSE', {}, PATIENT)
    expect(tiles(response)).toEqual([
      '0 of 4Acted onhalf within —', '1 of 4Resolvedhalf within 10 minutes', '2Guards sent1 arrived, 1 declined',
      '—Sent to arrivedthe middle time'])
    expect(screen.getByTestId('clocks')).toHaveTextContent('The response clocks are switched off, so nothing is measured against them. That is not the same as none being missed.')
  })

  it('names what is not shown, and says so in the section it belongs to', async () => {
    show()
    expect(await screen.findByTestId('not-read', {}, PATIENT)).toHaveTextContent('Not shown to you: Drone patrols (read under drone:read).')
    expect(screen.getAllByTestId('patrol-kind').map((p) => p.textContent)).toEqual([
      'Guard tours: 1 done of 2 over; 1 missed; 1 still to be done',
      'Virtual patrols: 1 done of 3 over; 1 done in part, 1 missed; 1 still to be done',
      'Drone patrols: not shown to you'])
    // A part of visitors that is not read is not a tile with a zero in it.
    vi.mocked(api.getBoard).mockResolvedValue({
      ...BOARD, not_read: [], sections: BOARD.sections.filter((s) => s.key === 'VISITORS')
        .map((s) => ({ ...s, figures: { ...A.VISITORS, waiting_now: null } })) })
    show()
    await waitFor(() => expect(screen.getAllByTestId('section-VISITORS')).toHaveLength(2), PATIENT)
    expect(tiles(screen.getAllByTestId('section-VISITORS')[1])).toEqual([
      '7On site · now', '23Arrivals logged', '16Departures logged', '1Refused'])
  })

  it('says how much advice stands, as counts of the weeks before and not a forecast', async () => {
    show()
    const advice = await screen.findByTestId('advice-standing', {}, PATIENT)
    expect(advice).toHaveTextContent('3 pieces of advice stand for these sites over the last 4 weeks.')
    expect(advice).toHaveTextContent('A pattern that recurred is not a forecast.')
    expect(within(advice).getByRole('link', { name: 'Read it in Risk & Advice' })).toHaveAttribute('href', '/risk-advice')
    vi.mocked(api.getBoard).mockResolvedValue({ ...BOARD, advice: null })
    show()
    await waitFor(() => expect(screen.getAllByTestId('section-INCIDENTS')).toHaveLength(2), PATIENT)
    expect(screen.getAllByTestId('advice-standing')).toHaveLength(1)
  })

  it('asks again for another period, a site or a customer', async () => {
    show()
    await screen.findByTestId('section-INCIDENTS', {}, PATIENT)
    await pick('Period', 'The last 7 days')
    await waitFor(() => expect(api.getBoard).toHaveBeenLastCalledWith({ site_id: undefined, client_id: undefined, days: 7 }))
    await pick('Site', 'Factory B')
    await waitFor(() => expect(api.getBoard).toHaveBeenLastCalledWith({ site_id: 's2', client_id: undefined, days: 7 }))
    // Choosing a customer is choosing its sites: the one site is let go.
    await pick('Customer', 'Acme Properties')
    await waitFor(() => expect(api.getBoard).toHaveBeenLastCalledWith({ site_id: undefined, client_id: 'c1', days: 7 }))
    fireEvent.mouseDown(screen.getByLabelText('Site'))
    expect((await screen.findAllByRole('option')).map((o) => o.textContent)).toEqual(['Every site', 'Factory A', 'Factory B'])
  })

  it('offers no customer to choose where there is none, and gives the reason when it cannot be read', async () => {
    vi.mocked(api.getBoardSites).mockResolvedValue({ ...SITES, clients: [] })
    vi.mocked(api.getBoard).mockRejectedValue(refusal('Site not found', 404))
    show()
    expect(await screen.findByText('Site not found', {}, PATIENT)).toBeInTheDocument()
    expect(screen.queryByLabelText('Customer')).not.toBeInTheDocument()
  })
})

describe('sites and customers', () => {
  it('gives each site a row, what is at no site apart, the sites together, and each customer', async () => {
    show()
    await openTab('Sites and customers')
    const table = await screen.findByTestId('sites-table', {}, PATIENT)
    expect(within(table).getAllByRole('columnheader').map((h) => h.textContent)).toEqual([
      'Site', 'Customer', 'Incidents opened', 'Open now', 'Clocks missed', 'Patrols not done', 'On shift now',
      'Devices down', 'Visitors on site', 'Orders overdue'])
    const rows = within(table).getAllByTestId('site-row').map((r) => within(r).getAllByRole('cell').map((c) => c.textContent))
    expect(rows).toEqual([
      ['Factory A', 'Acme Properties', '4', '5', '2', '2', '6', '3', '7', '1'],
      ['Factory B', 'Acme Properties', '1', '1', '2', '2', '2', '3', '7', '1'],
      // A site not in use is said to be; a section not counted there is a dash, not a zero.
      ['Old yard (not in use)', '', '—', '—', '—', '—', '6', '—', '—', '—'],
      ['At no site', "No camera, or the organisation's own", '9', '5', '—', '—', '—', '—', '—', '—'],
      ['Together', '', '4', '5', '2', '2', '6', '3', '7', '1']])
    const customers = screen.getByTestId('clients-table')
    expect(within(customers).getAllByTestId('site-row').map((r) => r.textContent)).toEqual(['Acme Properties245226371'])
    expect(screen.getByText(/a middle time is not a sum/)).toBeInTheDocument()
    expect(screen.getByTestId('not-read')).toHaveTextContent('Drone patrols')
  })

  it('opens the board for a site when its row is chosen, and asks again by customer', async () => {
    show()
    await openTab('Sites and customers')
    const table = await screen.findByTestId('sites-table', {}, PATIENT)
    await pick('Customer', 'Acme Properties')
    await waitFor(() => expect(api.getBoardSites).toHaveBeenLastCalledWith({ client_id: 'c1', days: 1 }))
    fireEvent.click(within(table).getAllByTestId('site-row')[1])
    await waitFor(() => expect(api.getBoard).toHaveBeenLastCalledWith({ site_id: 's2', client_id: undefined, days: 1 }))
    expect(await screen.findByTestId('section-INCIDENTS', {}, PATIENT)).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'Board' })).toHaveAttribute('aria-selected', 'true')
  })
})

describe('the daily briefing', () => {
  const openBriefing = async (index: number) => {
    await openTab('Daily briefing')
    fireEvent.click((await screen.findAllByTestId('briefing-row', {}, PATIENT))[index])
    return screen.findByTestId('briefing', {}, PATIENT)
  }

  it('lists the briefings with what each is, and offers drafting to whoever manages them', async () => {
    show()
    await openTab('Daily briefing')
    const rows = await screen.findAllByTestId('briefing-row', {}, PATIENT)
    expect(rows).toHaveLength(2)
    expect(rows[0]).toHaveTextContent('Factory A')
    expect(rows[0]).toHaveTextContent('Draft')
    expect(rows[0]).toHaveTextContent('Drafted by Siti Rahman')
    expect(rows[1]).toHaveTextContent('Published by Siti Rahman')
    expect(screen.getByRole('button', { name: 'Draft a briefing' })).toBeInTheDocument()
    expect(screen.getByText(/The platform drafts it; a person reads it/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Show drafts set aside' }))
    await waitFor(() => expect(api.listBriefings).toHaveBeenLastCalledWith({ state: 'DISCARDED' }))
  })

  it('shows a reader what was published: its lines, what each is true of, what was left out and whose the note is', async () => {
    signIn(4)
    vi.mocked(api.listBriefings).mockResolvedValue({ items: [OUT], can_manage: false, max_days_back: 31 })
    vi.mocked(api.getBriefing).mockResolvedValue({ ...PUBLISHED, may: { ...OUT.may, correct: false } })
    show()
    const view = await openBriefing(0)
    expect(screen.queryByRole('button', { name: 'Draft a briefing' })).not.toBeInTheDocument()
    await within(view).findAllByTestId('briefing-section')
    expect(within(view).getAllByTestId('briefing-section').map((s) => s.getAttribute('data-key'))).toEqual(['INCIDENTS', 'ADVICE'])
    expect(within(view).getAllByTestId('briefing-line').map((l) => l.textContent)).toEqual([
      '4 incidents were opened: 1 critical, 2 high and 1 medium.',
      '3 of those opened are still open. (when drafted)',
      '87% of the missed patrols of the last 4 weeks fell between 08:00 and 12:00 (13 of 15). Rests on 15 records over 4 weeks. (the 4 weeks before)'])
    expect(view).toHaveTextContent('A pattern that recurred is not a forecast: nothing here says what will happen.')
    expect(view).toHaveTextContent('As things stood when this was drafted, not at the end of the day.')
    expect(within(view).getByTestId('left-out')).toHaveTextContent('Left out by the reviewer: Visitors.')
    expect(within(view).getByTestId('not-counted')).toHaveTextContent(
      'Not counted — whoever drafted it may not read them: Drone patrols (drone:read).')
    const note = within(view).getByTestId('reviewer-note')
    expect(note).toHaveTextContent('Note from Siti Rahman')
    expect(note).toHaveTextContent('Gate 2 is closed for works until Friday.')
    // Nothing of it is theirs to change.
    for (const name of ['Publish…', 'Count again', 'Set aside', 'Draft a correction', 'Keep the note']) {
      expect(screen.queryByRole('button', { name })).not.toBeInTheDocument()
    }
    expect(screen.queryByLabelText('Leave out Incidents')).not.toBeInTheDocument()
  })

  it('drafts a briefing for yesterday and every site unless told otherwise, and opens it', async () => {
    show()
    await openTab('Daily briefing')
    fireEvent.click(await screen.findByRole('button', { name: 'Draft a briefing' }, PATIENT))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByLabelText('Day')).toHaveValue(dayBefore(1))
    expect(within(dialog).getByLabelText('Day')).toHaveAttribute('max', dayBefore(0))
    expect(within(dialog).getByLabelText('Day')).toHaveAttribute('min', dayBefore(31))
    fireEvent.click(within(dialog).getByRole('button', { name: 'Draft it' }))
    await waitFor(() => expect(api.draftBriefing).toHaveBeenCalledWith({ site_id: null, briefing_date: dayBefore(1) }))
    expect(await screen.findByTestId('briefing', {}, PATIENT)).toBeInTheDocument()
    await waitFor(() => expect(api.getBriefing).toHaveBeenCalledWith('b1'))
  })

  it('gives the reason when a day cannot be drafted, and keeps the dialog', async () => {
    vi.mocked(api.draftBriefing).mockRejectedValue(refusal('That day already has a draft. Open it, or discard it first.'))
    show()
    await openTab('Daily briefing')
    fireEvent.click(await screen.findByRole('button', { name: 'Draft a briefing' }, PATIENT))
    const dialog = await screen.findByRole('dialog')
    fireEvent.mouseDown(within(dialog).getByLabelText('For'))
    fireEvent.click(await screen.findByRole('option', { name: 'Factory B' }))
    fireEvent.change(within(dialog).getByLabelText('Day'), { target: { value: '2026-10-05' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Draft it' }))
    expect(await within(dialog).findByText(/already has a draft/)).toBeInTheDocument()
    expect(api.draftBriefing).toHaveBeenCalledWith({ site_id: 's2', briefing_date: '2026-10-05' })
  })

  it('lets a reviewer leave a section out and write a note, and not edit a counted line', async () => {
    show()
    const view = await openBriefing(0)
    await within(view).findAllByTestId('briefing-section')
    expect(within(view).getAllByTestId('briefing-section')).toHaveLength(3)
    expect(view).toHaveTextContent('Only people who manage briefings can read it until it is published.')
    // The lines are text, not fields: the only things to type in are the note.
    expect(within(view).getAllByRole('textbox')).toHaveLength(1)
    expect(within(view).getByLabelText('Leave out Visitors')).toBeChecked()
    fireEvent.click(within(view).getByLabelText('Leave out Incidents'))
    await waitFor(() => expect(api.reviewBriefing).toHaveBeenCalledWith('b1', { left_out: ['VISITORS', 'INCIDENTS'] }))
    fireEvent.click(await within(view).findByLabelText('Leave out Visitors', {}, PATIENT))
    await waitFor(() => expect(api.reviewBriefing).toHaveBeenLastCalledWith('b1', { left_out: [] }))
    await within(view).findByLabelText('Leave out Visitors', {}, PATIENT)
    expect(within(view).getByRole('button', { name: 'Keep the note' })).toBeDisabled()
    fireEvent.change(within(view).getByLabelText('Your note'), { target: { value: 'Gate 2 is closed for works.' } })
    fireEvent.click(await ready('Keep the note'))
    await waitFor(() => expect(api.reviewBriefing).toHaveBeenLastCalledWith('b1', { note: 'Gate 2 is closed for works.' }))
    expect(view).toHaveTextContent('They are shown as yours, apart from what was counted.')
  })

  it('publishes only after saying that it is final, and counts again or sets aside when asked', async () => {
    show()
    await openBriefing(0)
    fireEvent.click(await ready('Count again'))
    await waitFor(() => expect(api.recountBriefing).toHaveBeenCalledWith('b1'))
    fireEvent.click(await ready('Publish…'))
    expect(api.publishBriefing).not.toHaveBeenCalled()
    expect(await screen.findByText(/Once published it is not changed, and everybody who may read briefings can read it/)).toBeInTheDocument()
    fireEvent.click(await ready('Publish it'))
    await waitFor(() => expect(api.publishBriefing).toHaveBeenCalledWith('b1'))
    fireEvent.click(await ready('Set aside'))
    await waitFor(() => expect(api.discardBriefing).toHaveBeenCalledWith('b1'))
    await waitFor(() => expect(screen.queryByTestId('briefing')).not.toBeInTheDocument())
  })

  it('gives the reason when a briefing is not changed', async () => {
    vi.mocked(api.publishBriefing).mockRejectedValue(refusal('There is nothing in it to publish: every section is left out and there is no note.', 422))
    show()
    const view = await openBriefing(0)
    fireEvent.click(await ready('Publish…'))
    fireEvent.click(await ready('Publish it'))
    expect(await within(view).findByText(/There is nothing in it to publish/, {}, PATIENT)).toBeInTheDocument()
  })

  it('corrects a published briefing with a new draft for the same site and day', async () => {
    show()
    const view = await openBriefing(1)
    expect(await within(view).findByText('Published')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Publish…' })).not.toBeInTheDocument()
    fireEvent.click(await ready('Draft a correction'))
    expect(await screen.findByLabelText('Day', {}, PATIENT)).toHaveValue('2026-10-06')
    expect(screen.queryByTestId('briefing')).not.toBeInTheDocument()
    fireEvent.click(await ready('Draft it'))
    await waitFor(() => expect(api.draftBriefing).toHaveBeenCalledWith({ site_id: 's1', briefing_date: '2026-10-06' }))
  })

  it('says of a replaced briefing that it is kept as it was', async () => {
    vi.mocked(api.getBriefing).mockResolvedValue({ ...PUBLISHED, replaced_by: 'b9', revision: 1, may: { ...OUT.may, correct: false } })
    vi.mocked(api.listBriefings).mockResolvedValue({
      items: [{ ...OUT, id: 'b9', revision: 2 }, { ...OUT, replaced_by: 'b9' }], can_manage: true, max_days_back: 31 })
    show()
    await openTab('Daily briefing')
    const rows = await screen.findAllByTestId('briefing-row', {}, PATIENT)
    expect(rows[0]).toHaveTextContent('Revision 2')
    expect(rows[1]).toHaveTextContent('Replaced by a later revision')
    fireEvent.click(rows[1])
    const view = await screen.findByTestId('briefing', {}, PATIENT)
    expect(await within(view).findByText(/A later revision of this day's briefing has been published. This one is kept as it was./)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Draft a correction' })).not.toBeInTheDocument()
  })
})

describe('who is shown which part', () => {
  it('shows nothing of it to a guard, who holds neither permission', async () => {
    signIn(5)
    show()
    expect(await screen.findByText('Operations Board', {}, PATIENT)).toBeInTheDocument()
    expect(screen.queryAllByRole('tab')).toHaveLength(0)
    expect(api.getBoard).not.toHaveBeenCalled()
    expect(api.listBriefings).not.toHaveBeenCalled()
  })

  it('shows an operator the board and the briefings', async () => {
    signIn(4)
    show()
    expect((await screen.findAllByRole('tab', {}, PATIENT)).map((t) => t.textContent)).toEqual([
      'Board', 'Sites and customers', 'Daily briefing'])
  })
})
