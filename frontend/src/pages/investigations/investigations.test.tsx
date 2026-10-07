import type { ReactNode } from 'react'
import { Route, Routes, MemoryRouter, useLocation } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ThemeProvider } from '@mui/material'
import { render, screen, fireEvent, waitFor, within } from '@/test/utils'
import { theme } from '@/theme/glassmorphism'
import { useAuthStore } from '@/store/auth'
import * as api from '@/api/investigations'
import type { Entry, Found, InvestigationFile, InvestigationRow, SearchAnswer, Sources, Trail } from '@/api/investigations'
import {
  distance, followable, fromLocalInput, gap, home, refOf, subject, toLocalInput,
} from '@/components/investigations/investigationFormat'
import InvestigationSearch from './InvestigationSearch'
import Investigations from './Investigations'
import Investigation from './Investigation'

vi.mock('@/store/auth', () => ({ useAuthStore: vi.fn() }))
vi.mock('@/api/sites', () => ({ getSites: vi.fn().mockResolvedValue([{ id: 's1', name: 'Factory A' }, { id: 's2', name: 'Factory B' }]) }))
vi.mock('@/api/cameras', () => ({ getCameras: vi.fn().mockResolvedValue([
  { id: 'c1', name: 'North Gate', site_id: 's1' }, { id: 'c2', name: 'Loading Bay', site_id: 's2' }]) }))
vi.mock('@/api/incidents', () => ({ getIncidents: vi.fn().mockResolvedValue({
  items: [{ id: 'inc1', title: 'Zone breach at the fence', created_at: '2026-10-05T17:10:00Z' }], has_more: false }) }))
// PageHeader reads the tenant's page names from the settings.
vi.mock('@/api/settings', () => ({ getSettings: vi.fn().mockResolvedValue([]), upsertSetting: vi.fn().mockResolvedValue({}) }))
vi.mock('@/api/evidencePackages', async (orig) => {
  const real = await orig<typeof import('@/api/evidencePackages')>()
  return { ...real, listPackages: vi.fn().mockResolvedValue({ items: [], total: 0, limit: 50, offset: 0, has_more: false }),
           createPackage: vi.fn().mockResolvedValue({ id: 'pkg9', package_number: 'EVP-20261006-0009' }) }
})
vi.mock('@/api/investigations', async (orig) => {
  const real = await orig<typeof import('@/api/investigations')>()
  const fns = Object.fromEntries(Object.entries(real).map(([k, v]) => [k, typeof v === 'function' ? vi.fn() : v]))
  return { ...fns, apiError: real.apiError }
})

const ADMIN = 2, OPERATOR = 4, GUARD = 5, VIEWER = 6

function asRole(roleId: number) {
  vi.mocked(useAuthStore).mockImplementation(((sel: (s: unknown) => unknown) =>
    sel({ user: { id: 'u1', tenantId: 't1', roleId }, accessToken: 'tok', permissions: null })) as never)
}

function Where() {
  const { pathname } = useLocation()
  return <div data-testid="went-to">{pathname}</div>
}

function renderAt(path: string, pattern: string, el: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } })
  return render(
    <MemoryRouter initialEntries={[path]}>
      <QueryClientProvider client={qc}>
        <ThemeProvider theme={theme}>
          <Routes><Route path={pattern} element={el} /><Route path="*" element={<Where />} /></Routes>
        </ThemeProvider>
      </QueryClientProvider>
    </MemoryRouter>, { wrapper: ({ children }) => <>{children}</> })
}

const record = (over: Partial<Found>): Found => ({
  kind: 'ALERT', id: 'a1', occurred_at: '2026-10-05T17:17:04Z', site_id: 's1', site_name: 'Factory A',
  camera_id: 'c1', camera_name: 'North Gate', event_type: 'intrusion', title: 'Zone breach', summary: 'Somebody crossed the line',
  severity: 'critical', status: 'open', subject_kind: null, subject_ref: null, subject_label: null, confidence: null,
  risk_level: null, staff_user_id: null, detection_id: null, latitude: 1.3, longitude: 103.8, ...over,
})

const PLATE = record({ kind: 'PLATE_READ', id: 'p1', event_type: 'lpr', title: 'SGA1234B', summary: 'entry · blue · lorry',
                       severity: null, status: 'block', subject_kind: 'VEHICLE', subject_ref: 'SGA1234B',
                       subject_label: 'SGA1234B', confidence: 0.91, detection_id: 'p1' })
const FACE = record({ kind: 'FACE_MATCH', id: 'f1', event_type: 'face', title: 'A face matched a watchlist entry', summary: null,
                      severity: null, status: 'block', subject_kind: 'PERSON', subject_ref: 'w1', subject_label: null,
                      confidence: 0.88 })
const SITUATION = record({ kind: 'SITUATION', id: 'sit1', event_type: null, title: 'Activity at the gate',
                           summary: 'SIT-20261006-0002', severity: 'high', risk_level: 'HIGH' })

const SOURCES: Sources = {
  sources: [
    { kind: 'ALERT', label: 'Alerts', permission: 'alert:read', may_search: true, asked_for: false,
      answers: { camera: true, event_type: true, severity: true, risk_level: false, plate: false, person: false, staff: true } },
    { kind: 'PLATE_READ', label: 'Number plate reads', permission: 'detection:read', may_search: true, asked_for: false,
      answers: { camera: true, event_type: true, severity: false, risk_level: false, plate: true, person: false, staff: false } },
    { kind: 'VISITOR', label: 'Visitor movements', permission: 'visitor:read', may_search: false, asked_for: false,
      answers: { camera: false, event_type: true, severity: false, risk_level: false, plate: true, person: true, staff: true } },
  ],
  severities: ['info', 'low', 'medium', 'high', 'critical'], risk_levels: ['INFO', 'LOW', 'MEDIUM', 'HIGH', 'CRITICAL'],
  max_days: 92, default_hours: 24,
  phrase: {
    periods: ['today', 'last night'], kinds: { ALERT: ['alert', 'alerts'], PLATE_READ: ['vehicle', 'vehicles'] },
    event_types: { intrusion: ['intrusion'] }, severities: ['critical'], plates: ['plate SGX1234A'],
    people: ['named Tan Wei Ming'], words: ['"blue lorry"'], places: 'The names of your own sites and cameras.',
    limits: 'Read by fixed rules, not understood: a word outside this vocabulary is listed as not understood and narrows nothing.',
  },
  note: 'A source listed as not searched was not looked in.',
}

const ANSWER: SearchAnswer = {
  query: { from: '2026-10-05T10:00:00Z', to: '2026-10-05T22:00:00Z', kinds: ['PLATE_READ'], site_ids: [], camera_ids: ['c1'],
           event_types: [], severities: [], risk_levels: [], plate: 'SGA1234B', person: null, staff_user_id: null, text: null },
  phrase: {
    text: 'suspicious vehicles SGA1234B at north gate last night',
    understood: [{ words: 'vehicles', field: 'kind', as: 'PLATE_READ' }, { words: 'north gate', field: 'camera', as: 'the camera North Gate' },
                 { words: 'SGA1234B', field: 'plate', as: 'the number plate SGA1234B' }],
    assumed: [], not_understood: ['suspicious'],
  },
  searched: ['PLATE_READ'],
  not_searched: [{ kind: 'ALERT', label: 'Alerts', reason: 'These records carry no number plate.' }],
  found: { PLATE_READ: 2, FACE_MATCH: 1 }, items: [PLATE, FACE, SITUATION], total: 3, limit: 50, offset: 0, has_more: false,
  note: 'A source listed as not searched was not looked in.',
}

const TRAIL: Trail = {
  subject: { kind: 'VEHICLE', plate: 'SGA1234B' }, from: '2026-09-06T00:00:00Z', to: '2026-10-06T00:00:00Z',
  sightings: [PLATE, record({ ...PLATE, id: 'p2', camera_id: 'c2', camera_name: 'Loading Bay', occurred_at: '2026-10-05T17:21:04Z' }),
              record({ ...PLATE, id: 'p3', camera_id: 'c2', camera_name: 'Loading Bay', site_name: 'Factory B', occurred_at: '2026-10-05T19:26:04Z' })],
  legs: [{ from_id: 'p1', to_id: 'p2', seconds: 240, metres: 100, same_camera: false, same_site: true },
         { from_id: 'p2', to_id: 'p3', seconds: 7500, metres: null, same_camera: true, same_site: false }],
  summary: { sightings: 3, first_at: '2026-10-05T17:17:04Z', last_at: '2026-10-05T19:26:04Z', cameras: 2, sites: 2 },
  complete: true, searched: ['PLATE_READ'],
  not_searched: [{ kind: 'VISITOR', label: 'Visitor movements', reason: 'You do not hold the permission visitor:read.' }],
  basis: 'Each row is a read the plate recogniser made of this plate. A read says where the vehicle was, not who was driving it.',
  not_followed: 'A person who is on no watchlist cannot be followed from camera to camera.',
}

const ROW: InvestigationRow = {
  id: 'inv1', investigation_number: 'INV-20261006-0001', title: 'Lorry at the back fence', reason: 'Reported by the night supervisor.',
  status: 'OPEN', site_id: 's1', site_name: 'Factory A', incident_id: null, situation_id: null, opened_by_user_id: 'u1',
  opened_by_name: 'Priya Nair', opened_at: '2026-10-05T18:00:00Z', closed_by_user_id: null, closed_by_name: null,
  closed_at: null, closing_note: null, updated_at: '2026-10-05T18:00:00Z', records: 2,
}

const entry = (over: Partial<Entry>): Entry => ({
  id: 'e1', kind: 'PLATE_READ', label: 'Number plate reads', ref_id: 'p1', occurred_at: '2026-10-05T17:17:04Z', site_id: 's1',
  note: null, added_by_user_id: 'u1', added_by_name: 'Priya Nair', added_at: '2026-10-05T18:05:00Z', set_aside_at: null,
  set_aside_by_user_id: null, set_aside_by_name: null, set_aside_reason: null, state: 'SHOWN', record: PLATE, ...over,
})

const FILE: InvestigationFile = {
  ...ROW, incident_id: 'inc1',
  items: [
    entry({ note: 'The lorry, as it came in.' }),
    entry({ id: 'e2', kind: 'NOTE', label: 'Note', ref_id: null, state: 'NOTE', record: null, note: 'The gate was already open here.' }),
    entry({ id: 'e3', kind: 'VISITOR', label: 'Visitor movements', ref_id: 'v1', state: 'NOT_PERMITTED', record: null }),
    entry({ id: 'e4', kind: 'OCCURRENCE', label: 'Occurrence book', ref_id: 'o1', state: 'NOT_AVAILABLE', record: null }),
    entry({ id: 'e5', kind: 'SITUATION', label: 'Situations', ref_id: 'sit1', record: SITUATION, set_aside_at: '2026-10-05T19:00:00Z',
            set_aside_by_name: 'Tan Wei Ming', set_aside_reason: 'A different vehicle.' }),
  ],
  counts: { records: 3, notes: 1, set_aside: 1, not_shown: 2 }, can_manage: true,
}

beforeEach(() => {
  vi.clearAllMocks()
  asRole(OPERATOR)
  vi.mocked(api.getSources).mockResolvedValue(SOURCES)
  vi.mocked(api.search).mockResolvedValue(ANSWER)
  vi.mocked(api.getTrail).mockResolvedValue(TRAIL)
  vi.mocked(api.listInvestigations).mockResolvedValue({ items: [ROW], total: 1, limit: 25, offset: 0, has_more: false })
  vi.mocked(api.getInvestigation).mockResolvedValue(FILE)
  vi.mocked(api.addRecords).mockResolvedValue({ added: [{ kind: 'PLATE_READ', id: 'p1' }], already_filed: [{ kind: 'FACE_MATCH', id: 'f1' }] })
  vi.mocked(api.openInvestigation).mockResolvedValue({ id: 'inv9', investigation_number: 'INV-20261006-0009', records: 0 })
  for (const fn of [api.addNote, api.setAside, api.closeInvestigation, api.reopenInvestigation]) vi.mocked(fn).mockResolvedValue({})
})

const searchPage = () => renderAt('/investigate', '/investigate', <InvestigationSearch />)

async function ask(words: string) {
  fireEvent.change(screen.getByLabelText('Ask in a few words'), { target: { value: words } })
  fireEvent.click(screen.getByRole('button', { name: 'Ask' }))
  await screen.findAllByTestId('found-row')
}

describe('Investigation search', () => {
  it('a typed phrase is sent as typed, and what was made of it is shown word for word', async () => {
    searchPage()
    expect(screen.getByText(/Every search is recorded in the audit log/)).toBeInTheDocument()
    await ask('suspicious vehicles SGA1234B at north gate last night')
    expect(api.search).toHaveBeenCalledWith({ phrase: 'suspicious vehicles SGA1234B at north gate last night', limit: 50, offset: 0 })
    const made = screen.getByTestId('phrase-made')
    expect(within(made).getByText('Kind: PLATE_READ')).toBeInTheDocument()
    expect(within(made).getByText('Camera: the camera North Gate')).toBeInTheDocument()
    expect(within(made).getByText('Plate: the number plate SGA1234B')).toBeInTheDocument()
    expect(within(made).getByText(/Not understood, and not used/)).toHaveTextContent('suspicious')
  })

  it('the filters are filled from the search that ran, so a phrase can be corrected', async () => {
    searchPage()
    await ask('vehicles SGA1234B at north gate')
    expect(screen.getByLabelText('Number plate')).toHaveValue('SGA1234B')
    expect(screen.getByLabelText('From')).toHaveValue(toLocalInput('2026-10-05T10:00:00Z'))
    fireEvent.change(screen.getByLabelText('Number plate'), { target: { value: 'SGA1234C' } })
    fireEvent.click(screen.getByRole('button', { name: 'Search' }))
    await waitFor(() => expect(api.search).toHaveBeenCalledTimes(2))
    const body = vi.mocked(api.search).mock.calls[1][0]
    expect(body.phrase).toBeUndefined()
    expect(body).toMatchObject({ plate: 'SGA1234C', kinds: ['PLATE_READ'], camera_ids: ['c1'], limit: 50, offset: 0,
                                 from: '2026-10-05T10:00:00.000Z', to: '2026-10-05T22:00:00.000Z' })
  })

  it('says how many of each kind there are, and names every source that was not searched', async () => {
    searchPage()
    await ask('vehicles')
    expect(screen.getByText('3 records')).toBeInTheDocument()
    expect(screen.getByText('Plate read 2')).toBeInTheDocument()
    expect(screen.getByText('Watchlist face match 1')).toBeInTheDocument()
    const left = screen.getByTestId('not-searched')
    expect(left).toHaveTextContent('nothing here says whether anything happened there')
    expect(left).toHaveTextContent('Alerts: These records carry no number plate.')
    expect(screen.getAllByText('A source listed as not searched was not looked in.').length).toBeGreaterThan(0)
  })

  it('a watchlist name is not shown to somebody it is not for, and the row says so', async () => {
    searchPage()
    await ask('faces')
    const rows = screen.getAllByTestId('found-row')
    expect(rows[1]).toHaveTextContent('A face matched a watchlist entry')
    expect(rows[1]).toHaveTextContent('the name is the watchlist keeper’s to see')
    expect(rows[1]).toHaveTextContent('Confidence 88%')
    expect(rows[0]).toHaveTextContent('entry · blue · lorry')
    expect(rows[2]).toHaveTextContent('Risk High')
  })

  it('a phrase that was not understood shows the server’s refusal and the words', async () => {
    vi.mocked(api.search).mockRejectedValue({ response: { status: 422, data: { detail: {
      message: 'Nothing in that was understood. Try a period, a kind of record, a number plate or the name of one of your sites or cameras — or use the filters.',
      not_understood: ['odd', 'going'] } } } })
    searchPage()
    fireEvent.change(screen.getByLabelText('Ask in a few words'), { target: { value: 'anything odd going on' } })
    fireEvent.click(screen.getByRole('button', { name: 'Ask' }))
    const refusal = await screen.findByRole('alert')
    expect(refusal).toHaveTextContent('Nothing in that was understood')
    expect(refusal).toHaveTextContent('Not understood: odd, going.')
    expect(screen.queryByTestId('found-row')).not.toBeInTheDocument()
  })

  it('says what can be typed, and that it is read by rules and not understood', async () => {
    searchPage()
    fireEvent.click(screen.getByRole('button', { name: 'What can I say?' }))
    const help = await screen.findByTestId('phrase-help')
    expect(help).toHaveTextContent('Read by fixed rules, not understood')
    expect(help).toHaveTextContent('last night')
    expect(help).toHaveTextContent('plate SGX1234A')
  })

  it('only the kinds this person may read are offered', async () => {
    searchPage()
    await waitFor(() => expect(api.getSources).toHaveBeenCalled())
    fireEvent.mouseDown(await screen.findByLabelText('Kinds of record'))
    expect(await screen.findByRole('option', { name: 'Alerts' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'Number plate reads' })).toBeInTheDocument()
    expect(screen.queryByRole('option', { name: 'Visitor movements' })).not.toBeInTheDocument()
  })

  it('records are picked and filed in an investigation as references', async () => {
    searchPage()
    await ask('vehicles')
    const file = screen.getByRole('button', { name: /File.*in an investigation/ })
    expect(file).toBeDisabled()
    fireEvent.click(screen.getByLabelText('Select Plate read SGA1234B'))
    fireEvent.click(screen.getByLabelText('Select Watchlist face match A face matched a watchlist entry'))
    expect(screen.getByRole('button', { name: 'File 2 in an investigation' })).toBeEnabled()
    fireEvent.click(screen.getByRole('button', { name: 'File 2 in an investigation' }))
    const dialog = await screen.findByRole('dialog')
    expect(dialog).toHaveTextContent('a reference to each record, not a copy')
    fireEvent.mouseDown(await within(dialog).findByLabelText('Investigation'))
    fireEvent.click(await screen.findByRole('option', { name: 'INV-20261006-0001 — Lorry at the back fence' }))
    fireEvent.change(within(dialog).getByLabelText('Why these matter (optional)'), { target: { value: ' The lorry. ' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'File' }))
    await waitFor(() => expect(api.addRecords).toHaveBeenCalledWith('inv1', [refOf(PLATE), refOf(FACE)], 'The lorry.'))
    expect(await screen.findByText(/1 record filed; 1 already in the investigation/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Open it' }))
    expect(await screen.findByTestId('went-to')).toHaveTextContent('/investigations/inv1')
  })

  it('a viewer searches and can file nothing', async () => {
    asRole(VIEWER)
    searchPage()
    await ask('vehicles')
    expect(screen.queryByRole('button', { name: /in an investigation/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: 'Where seen' })).toHaveLength(2)
  })

  it('where a plate was seen: each sighting, what lies between, and what a sighting is not', async () => {
    searchPage()
    await ask('vehicles')
    fireEvent.click(screen.getAllByRole('button', { name: 'Where seen' })[0])
    const dialog = await screen.findByRole('dialog')
    await waitFor(() => expect(api.getTrail).toHaveBeenCalledWith({ plate: 'SGA1234B' }))
    expect(await within(dialog).findByText('Where SGA1234B was seen')).toBeInTheDocument()
    expect(within(dialog).getAllByTestId('trail-sighting')).toHaveLength(3)
    const [first, second] = within(dialog).getAllByTestId('trail-leg')
    expect(first).toHaveTextContent('4 min later · 100 m')
    expect(second).toHaveTextContent('2 h 5 min later · the same camera · another site')
    expect(dialog).toHaveTextContent('3 sightings at 2 cameras on 2 sites')
    expect(dialog).toHaveTextContent('not who was driving it')
    expect(dialog).toHaveTextContent('cannot be followed from camera to camera')
    expect(dialog).toHaveTextContent('Not searched: Visitor movements. You do not hold the permission visitor:read.')
  })

  it('a watchlist entry is followed by its id, never by a name', async () => {
    searchPage()
    await ask('faces')
    fireEvent.click(screen.getAllByRole('button', { name: 'Where seen' })[1])
    await waitFor(() => expect(api.getTrail).toHaveBeenCalledWith({ watchlist_entry_id: 'w1' }))
  })

  it('a record opens where it lives', async () => {
    searchPage()
    await ask('everything today')
    const rows = screen.getAllByTestId('found-row')
    expect(within(rows[0]).getByText('In Detections')).toBeInTheDocument()
    fireEvent.click(within(rows[2]).getByText('Open'))
    expect(await screen.findByTestId('went-to')).toHaveTextContent('/situations/sit1')
  })
})

describe('Investigations', () => {
  const list = () => renderAt('/investigations', '/investigations', <Investigations />)

  it('lists the open investigations first, and a row opens the file', async () => {
    list()
    expect(await screen.findByText('Lorry at the back fence')).toBeInTheDocument()
    expect(api.listInvestigations).toHaveBeenCalledWith({ status: 'OPEN', site_id: undefined, mine: undefined, q: undefined, limit: 25, offset: 0 })
    expect(screen.getByText('INV-20261006-0001')).toBeInTheDocument()
    expect(screen.getByText('Priya Nair')).toBeInTheDocument()
    fireEvent.click(screen.getByText('Lorry at the back fence'))
    expect(await screen.findByTestId('went-to')).toHaveTextContent('/investigations/inv1')
  })

  it('opening one needs what it is about and why, and takes you to it', async () => {
    list()
    fireEvent.click(await screen.findByRole('button', { name: 'Open an investigation' }))
    const dialog = await screen.findByRole('dialog')
    const open = within(dialog).getByRole('button', { name: 'Open' })
    expect(open).toBeDisabled()
    fireEvent.change(within(dialog).getByLabelText('What it is about'), { target: { value: 'Missing pallets' } })
    expect(open).toBeDisabled()
    fireEvent.change(within(dialog).getByLabelText('Why it is being opened'), { target: { value: 'Stock count was short.' } })
    expect(dialog).toHaveTextContent('Kept with the investigation and never changed.')
    fireEvent.mouseDown(within(dialog).getByLabelText('Opened from an incident (optional)'))
    fireEvent.click(await screen.findByRole('option', { name: /Zone breach at the fence/ }))
    fireEvent.click(open)
    await waitFor(() => expect(api.openInvestigation).toHaveBeenCalledWith({
      title: 'Missing pallets', reason: 'Stock count was short.', site_id: undefined, incident_id: 'inc1' }))
    expect(await screen.findByTestId('went-to')).toHaveTextContent('/investigations/inv9')
  })

  it('a refusal to open one is shown in the server’s words', async () => {
    vi.mocked(api.openInvestigation).mockRejectedValue({ response: { status: 422, data: {
      detail: 'Choose the site this investigation is about: you are assigned to certain sites and cannot open one that spans them all.' } } })
    list()
    fireEvent.click(await screen.findByRole('button', { name: 'Open an investigation' }))
    const dialog = await screen.findByRole('dialog')
    fireEvent.change(within(dialog).getByLabelText('What it is about'), { target: { value: 'Missing pallets' } })
    fireEvent.change(within(dialog).getByLabelText('Why it is being opened'), { target: { value: 'Stock count was short.' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Open' }))
    expect(await within(dialog).findByText(/Choose the site this investigation is about/)).toBeInTheDocument()
  })

  it('a viewer sees the list and no way to open one; a guard has neither permission', async () => {
    asRole(VIEWER)
    list()
    expect(await screen.findByText('Lorry at the back fence')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Open an investigation' })).not.toBeInTheDocument()
  })

  it('says when nothing is open', async () => {
    vi.mocked(api.listInvestigations).mockResolvedValue({ items: [], total: 0, limit: 25, offset: 0, has_more: false })
    list()
    expect(await screen.findByText('No investigation is open.')).toBeInTheDocument()
  })
})

describe('One investigation', () => {
  const file = () => renderAt('/investigations/inv1', '/investigations/:id', <Investigation />)

  it('shows why it was opened and everything in it, each as this reader may see it', async () => {
    file()
    expect(await screen.findByText('Reported by the night supervisor.')).toBeInTheDocument()
    expect(screen.getByText('Opened from an incident')).toBeInTheDocument()
    const entries = screen.getAllByTestId('file-entry')
    expect(entries).toHaveLength(5)
    expect(entries[0]).toHaveTextContent('SGA1234B')
    expect(entries[0]).toHaveTextContent('The lorry, as it came in.')
    expect(entries[0]).toHaveTextContent('Filed')
    expect(entries[1]).toHaveTextContent('The gate was already open here.')
    expect(entries[1]).toHaveTextContent('Written')
    expect(entries[2]).toHaveTextContent('A record of a kind you may not read')
    expect(entries[3]).toHaveTextContent('no longer held, or is at a site you are not assigned to')
    expect(entries[4]).toHaveTextContent('Set aside')
    expect(entries[4]).toHaveTextContent('Tan Wei Ming: A different vehicle.')
    expect(screen.getByText(/2 records are in this investigation and not shown to you/)).toBeInTheDocument()
  })

  it('an entry is set aside with a reason, and one already set aside cannot be again', async () => {
    file()
    const entries = await screen.findAllByTestId('file-entry')
    expect(within(entries[4]).queryByRole('button', { name: 'Set aside' })).not.toBeInTheDocument()
    fireEvent.click(within(entries[0]).getByRole('button', { name: 'Set aside' }))
    const dialog = await screen.findByRole('dialog')
    expect(dialog).toHaveTextContent('It stays in the investigation, marked as set aside')
    const confirm = within(dialog).getByRole('button', { name: 'Set aside' })
    expect(confirm).toBeDisabled()
    fireEvent.change(within(dialog).getByLabelText('Why it is being set aside'), { target: { value: ' Another vehicle. ' } })
    fireEvent.click(confirm)
    await waitFor(() => expect(api.setAside).toHaveBeenCalledWith('inv1', 'e1', 'Another vehicle.'))
  })

  it('a note is added and the file is read again', async () => {
    file()
    fireEvent.click(await screen.findByRole('button', { name: 'Add a note' }))
    const dialog = await screen.findByRole('dialog')
    fireEvent.change(within(dialog).getByLabelText('Note'), { target: { value: 'Spoke to the driver.' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Add' }))
    await waitFor(() => expect(api.addNote).toHaveBeenCalledWith('inv1', 'Spoke to the driver.'))
    await waitFor(() => expect(api.getInvestigation).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })

  it('closing says what was found, and the server’s refusal is shown', async () => {
    vi.mocked(api.closeInvestigation).mockRejectedValueOnce({ response: { status: 409, data: {
      detail: 'This investigation is closed. Reopen it to change it.' } } })
    file()
    fireEvent.click(await screen.findByRole('button', { name: 'Close' }))
    const dialog = await screen.findByRole('dialog')
    const confirm = within(dialog).getByRole('button', { name: 'Close' })
    fireEvent.change(within(dialog).getByLabelText('What was found'), { target: { value: 'ok' } })
    expect(confirm).toBeDisabled()
    fireEvent.change(within(dialog).getByLabelText('What was found'), { target: { value: 'A contractor’s lorry, booked in late.' } })
    fireEvent.click(confirm)
    expect(await within(dialog).findByText('This investigation is closed. Reopen it to change it.')).toBeInTheDocument()
    fireEvent.click(confirm)
    await waitFor(() => expect(api.closeInvestigation).toHaveBeenLastCalledWith('inv1', 'A contractor’s lorry, booked in late.'))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })

  it('a closed investigation shows how it ended and offers only to reopen it', async () => {
    vi.mocked(api.getInvestigation).mockResolvedValue({
      ...FILE, status: 'CLOSED', closed_at: '2026-10-06T02:00:00Z', closed_by_name: 'Priya Nair',
      closing_note: 'A contractor’s lorry, booked in late.' })
    file()
    expect(await screen.findByText(/A contractor’s lorry, booked in late\./)).toBeInTheDocument()
    for (const name of ['Add a note', 'Close', 'Set aside', 'Search for records']) {
      expect(screen.queryByRole('button', { name })).not.toBeInTheDocument()
    }
    fireEvent.click(screen.getByRole('button', { name: 'Reopen' }))
    const dialog = await screen.findByRole('dialog')
    expect(dialog).toHaveTextContent('How it was closed stays in the investigation as a note.')
    fireEvent.change(within(dialog).getByLabelText('Why it is being reopened'), { target: { value: 'Seen again tonight.' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Reopen' }))
    await waitFor(() => expect(api.reopenInvestigation).toHaveBeenCalledWith('inv1', 'Seen again tonight.'))
  })

  it('somebody who may only read is offered nothing to change', async () => {
    asRole(VIEWER)
    vi.mocked(api.getInvestigation).mockResolvedValue({ ...FILE, can_manage: false })
    file()
    await screen.findAllByTestId('file-entry')
    for (const name of ['Add a note', 'Close', 'Reopen', 'Set aside', 'Search for records']) {
      expect(screen.queryByRole('button', { name })).not.toBeInTheDocument()
    }
  })

  it('an investigation that is not this reader’s says so and shows nothing', async () => {
    vi.mocked(api.getInvestigation).mockRejectedValue({ response: { status: 404, data: { detail: 'Investigation not found' } } })
    file()
    expect(await screen.findByText('Investigation not found')).toBeInTheDocument()
    expect(screen.queryByTestId('file-entry')).not.toBeInTheDocument()
  })

  it('a record in the file can be followed and opened where it lives', async () => {
    file()
    const entries = await screen.findAllByTestId('file-entry')
    expect(within(entries[0]).getByText('Find it in Detections')).toBeInTheDocument()
    fireEvent.click(within(entries[0]).getByText('Where it was seen'))
    await waitFor(() => expect(api.getTrail).toHaveBeenCalledWith({ plate: 'SGA1234B' }))
  })
})

describe('the words the investigation screens share', () => {
  it('the time and distance between sightings', () => {
    expect([gap(45), gap(240), gap(3600), gap(7500), gap(172800 * 2)]).toEqual(['45 s', '4 min', '1 h', '2 h 5 min', '4 days'])
    expect([distance(null), distance(0), distance(100), distance(1250)]).toEqual(
      ['distance not known', 'the same place', '100 m', '1.3 km'])
  })

  it('a time survives the trip through a date-and-time box', () => {
    const iso = '2026-10-05T17:17:00.000Z'
    expect(fromLocalInput(toLocalInput(iso))).toBe(iso)
    expect(toLocalInput(null)).toBe('')
    expect(toLocalInput('not a time')).toBe('')
    expect(fromLocalInput('')).toBeUndefined()
  })

  it('where each kind of record lives, and who it is about', () => {
    expect(home(SITUATION)).toEqual({ path: '/situations/sit1', screen: 'Situations', exact: true })
    expect(home(PLATE)).toEqual({ path: '/detections', screen: 'Detections', exact: false })
    expect(subject(PLATE)).toBe('SGA1234B')
    expect(subject(FACE)).toContain('the name is the watchlist keeper’s to see')
    expect(subject({ ...FACE, subject_label: 'Lim Ah Kow' })).toBe('Lim Ah Kow')
    expect(subject(SITUATION)).toBeNull()
    expect(followable(PLATE)).toEqual({ plate: 'SGA1234B' })
    expect(followable(FACE)).toEqual({ watchlist_entry_id: 'w1' })
    expect(followable(SITUATION)).toBeNull()
    expect(followable({ ...PLATE, kind: 'VISITOR' })).toBeNull()
    expect(refOf(PLATE)).toEqual({ kind: 'PLATE_READ', id: 'p1', occurred_at: '2026-10-05T17:17:04Z' })
  })

  it('a refusal is read in the server’s words, whatever shape it came in', () => {
    expect(api.apiError({ response: { status: 429 } })).toMatch(/Too many searches/)
    expect(api.apiError({ response: { data: { detail: 'The period ends before it starts.' } } })).toBe('The period ends before it starts.')
    expect(api.apiError({ response: { data: { detail: { message: 'Some of these records were not found.', not_found: [] } } } }))
      .toBe('Some of these records were not found.')
    expect(api.apiError({ response: { data: { detail: [{ msg: 'Field required' }] } } })).toBe('Field required')
    expect(api.apiError({ message: 'Network Error' })).toBe('Network Error')
  })

  it('a guard is not given the investigation screens’ permissions', () => {
    asRole(GUARD)
    renderAt('/investigations', '/investigations', <Investigations />)
    expect(screen.queryByRole('button', { name: 'Open an investigation' })).not.toBeInTheDocument()
    asRole(ADMIN)
  })
})
