import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ThemeProvider } from '@mui/material'
import { render, screen, fireEvent, waitFor, within } from '@/test/utils'
import { theme } from '@/theme/glassmorphism'
import { useAuthStore } from '@/store/auth'
import * as api from '@/api/dataGovernance'
import type { OptionalKind, PeriodInForce, Statement, SubjectReport } from '@/api/dataGovernance'
import {
  between, heldCount, holdLine, lineWords, matchLine, optionalLine, periodLine, searchesLine, siteLine, subjectLine,
  totalsLine,
} from '@/components/governance/governanceFormat'
import DataRetention from './DataRetention'

vi.mock('@/store/auth', () => ({ useAuthStore: vi.fn() }))
// PageHeader reads the tenant's page names from the settings.
vi.mock('@/api/settings', () => ({ getSettings: vi.fn().mockResolvedValue([]), upsertSetting: vi.fn().mockResolvedValue({}) }))
vi.mock('@/api/dataGovernance', async (orig) => {
  const real = await orig<typeof import('@/api/dataGovernance')>()
  const fns = Object.fromEntries(Object.entries(real).map(([k, v]) => [k, typeof v === 'function' ? vi.fn() : v]))
  return { ...fns, apiError: real.apiError, alreadyOlder: real.alreadyOlder }
})

function show() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } })
  return render(
    <MemoryRouter>
      <QueryClientProvider client={qc}><ThemeProvider theme={theme}><DataRetention /></ThemeProvider></QueryClientProvider>
    </MemoryRouter>, { wrapper: ({ children }) => <>{children}</> })
}

const NOTE = 'The job that applies this period runs in a service of its own. If the installation gives that service a different default, the job\'s is the one in force.'
const NOT_LAW = 'This says what is configured, not what the law requires. How long a record ought to be kept is the organisation\'s to decide.'
const WHAT = 'This says where a person appears and how often, not what each record says. Each record is read on its own screen, under its own permission, by somebody who can judge what of it is that person\'s.'
const TEXT_IS_TEXT = 'What is found is text that matches what was typed. It does not identify anybody.'
const period = (over: Partial<PeriodInForce> & { key: string; label: string }): PeriodInForce => ({
  tables: ['evidence'], names_people: true,
  period: { amount: 30, unit: 'days', source: 'TENANT_SETTING', source_words: 'The organisation\'s setting', set_at: '2026-09-01T02:00:00Z', note: null },
  setting_key: 'evidence.retention_days', counted_from: 'when it was captured', removed_by: 'The scheduler, once a day',
  how: 'The file is deleted, then its record', hold_stops_it: true, held_now: 2, kept_past: null, per_organisation: true, ...over })
const kind = (over: Partial<OptionalKind> & { key: string; label: string }): OptionalKind => ({
  setting_key: 'retention.closed_cases_days', tables: [{ name: 'case_files', names_people: true }], days: null, set_at: null,
  counted_from: 'when its closing was approved', removes: 'The case with its people, tasks, notes, history, links and the people and vehicles named in it',
  kept_whatever: 'A case that is open, or waiting for approval to close', removed_by: 'The scheduler, once a day', taken_away: {}, ...over })
const STATEMENT: Statement = {
  read_at: '2026-10-09T02:00:00Z',
  optional: [
    kind({ key: 'CASES', label: 'Closed cases' }),
    kind({ key: 'VISITOR_AUTHORIZATIONS', label: 'Authorisations of visits and work', days: 365, set_at: '2026-10-01T02:00:00Z',
           counted_from: 'the end of the period it was for', kept_whatever: null,
           taken_away: { visitor_authorizations: 'An authorisation goes with its visitor when a data-subject erasure removes the visitor' } }),
  ],
  optional_note: 'These are kept until the organisation sets a period for them. Once one is set, what is over and older than it is removed for good, a day at a time.',
  least_days: 30,
  may_set: true,
  periods: [
    period({ key: 'EVIDENCE', label: 'Pictures and clips kept as evidence' }),
    period({ key: 'RECORDINGS', label: 'Continuous recordings', setting_key: 'recording.retention_days', held_now: 0,
             period: { amount: 7, unit: 'days', source: 'INSTALLATION', source_words: 'The installation\'s default, as this service reads it', set_at: null, note: NOTE } }),
    period({ key: 'DRONE_FOOTAGE', label: 'Drone footage', held_now: 0,
             kept_past: 'Footage of an event that became an incident, of an event that was confirmed and is still open, and of a flight still in progress.' }),
    period({ key: 'DRONE_RECEIPTS', label: 'Receipts of what a site gateway sent', setting_key: null, hold_stops_it: false, held_now: null,
             names_people: false, period: { amount: 1, unit: 'days', source: 'FIXED', source_words: 'Fixed in the code', set_at: null, note: null } }),
    period({ key: 'AUDIT', label: 'The audit log', setting_key: null, hold_stops_it: false, held_now: null, per_organisation: false,
             how: 'Moved out of the log into a file, a month at a time, once every entry of that month is old enough. Not deleted',
             period: { amount: 7, unit: 'years', source: 'INSTALLATION', source_words: 'The installation\'s default, as this service reads it', set_at: null, note: NOTE } }),
  ],
  sites: [
    { site_id: 's1', site_name: 'Factory A', in_use: true, recording_days: 3, source: 'SITE_POLICY', source_words: 'This site\'s recording policy',
      keeps_none: false, at_the_site_days: 2, set_at: '2026-09-02T02:00:00Z' },
    { site_id: 's2', site_name: 'Factory B', in_use: true, recording_days: 7, source: 'INSTALLATION',
      source_words: 'The installation\'s default, as this service reads it', keeps_none: false, at_the_site_days: null, set_at: null },
    { site_id: 's3', site_name: 'Gatehouse', in_use: false, recording_days: 0, source: 'SITE_POLICY', source_words: 'This site\'s recording policy',
      keeps_none: true, at_the_site_days: 1, set_at: null },
  ],
  holds: { in_force: 3, by_kind: { SNAPSHOT: 2, DRONE_MEDIA: 1 },
           words: 'A hold keeps one picture, clip, recording or piece of drone footage past its period until a person releases it, with a reason.' },
  kept: [
    { key: 'CASES', label: 'Cases: their people, tasks, history, linked records and the people and vehicles named in them',
      tables: [{ name: 'case_files', names_people: true }, { name: 'case_parties', names_people: true }], taken_away: {} },
    { key: 'SOP', label: 'Procedures, their versions and passages',
      tables: [{ name: 'sop_passages', names_people: false }, { name: 'sop_incident_types', names_people: false }],
      taken_away: { sop_incident_types: 'The kinds of incident a procedure is for are replaced when they are set again' } },
  ],
  everything_else: 'Every other record is removed by no job. It is kept until a person removes it on its own screen, a data-subject erasure is carried out, or the organisation is removed.',
  erasure: ['A face on the watchlist, and the match kept on what the cameras saw of it', 'A visitor. Their visits stay, without the visitor; the authorisations of their visits go with them'],
  not_law: NOT_LAW,
  sources: { SITE_POLICY: 'a', TENANT_SETTING: 'b', INSTALLATION: 'c', FIXED: 'd' },
}
const STAFF: SubjectReport = {
  subject: { kind: 'STAFF', id: 'u1', name: 'Mei Lin', role: 'Security Guard', in_use: true },
  held: [
    { table: 'incident_responses', label: 'Guards sent to incidents', lines: [
      { column: 'guard_user_id', part: 'ABOUT', words: 'Was sent to the incident', count: 3, first: '2026-09-01T08:00:00Z', last: '2026-10-04T08:00:00Z' }] },
    { table: 'case_tasks', label: 'Tasks on cases', lines: [
      { column: 'assigned_to_user_id', part: 'ABOUT', words: 'Was given the task', count: 1, first: '2026-10-02T08:00:00Z', last: '2026-10-02T08:00:00Z' },
      { column: 'done_by_user_id', part: 'BY', words: 'Finished or dropped the task', count: 1, first: '2026-10-03T08:00:00Z', last: '2026-10-03T08:00:00Z' }] },
  ],
  nothing_in: ['Investigations', 'Daily briefings'],
  totals: { about: 4, by: 1, kinds_of_record: 2 },
  searches: { count: 2, by_people: 2, first: '2026-09-20T08:00:00Z', last: '2026-10-05T08:00:00Z',
              words: 'Investigation searches that asked about them. Who searched, and when, is in the audit log.' },
  parts: { ABOUT: 'Records that concern them', BY: 'Steps they took' },
  what_it_is: WHAT,
  not_read: ['The account, its sessions and its audit entries: those are in the existing data-subject export.', 'A name written in a title or a note.'],
}
const WRITTEN: SubjectReport = {
  subject: { kind: 'TEXT', text: 'tan wei ming', as_plate: 'TANWEIMING' },
  held: [{ table: 'case_parties', label: 'People and vehicles named in cases', count: 2, distinct: 1, matches: [
    { text: 'Tan Wei Ming', count: 2, kind: 'PERSON', cases: 2, taken_off: 1, first: '2026-09-01T08:00:00Z', last: '2026-10-04T08:00:00Z' }] }],
  nothing_in: ['Work orders given to somebody by name'],
  totals: { matches: 2, kinds_of_record: 1 },
  searches: { count: 0, by_people: 0, first: null, last: null, words: 'Investigation searches that asked about them. Who searched, and when, is in the audit log.' },
  text_is_text: TEXT_IS_TEXT, what_it_is: WHAT, not_read: ['A name written in a title or a note.'], matches_shown: 20,
}
const refusal = (detail: unknown, status = 403) => Object.assign(new Error('x'), { response: { status, data: { detail } } })
const PATIENT = { timeout: 8000 }
/** Words that would turn where somebody appears into a finding about them. */
const ACCUSING = /suspect|offender|culprit|accused|guilty|identified as|is the person/i

function signIn(roleId = 2) {
  vi.mocked(useAuthStore).mockImplementation(((sel: (s: unknown) => unknown) =>
    sel({ user: { id: 'me', tenantId: 't1', roleId }, accessToken: 'tok', permissions: null })) as never)
}

beforeEach(() => {
  vi.clearAllMocks()
  signIn()
  vi.mocked(api.getStatement).mockResolvedValue(STATEMENT)
  vi.mocked(api.findSubject).mockResolvedValue({ kind: 'STAFF', more: false, found: [
    { id: 'u1', name: 'Mei Lin', detail: 'Security Guard', in_use: true }, { id: 'u2', name: 'Mei Ling Tan', detail: 'Operator', in_use: false }] })
  vi.mocked(api.getStaffReport).mockResolvedValue(STAFF)
  vi.mocked(api.getVisitorReport).mockResolvedValue({ ...STAFF, subject: { kind: 'VISITOR', id: 'v1', name: 'Lim Mei Ling', company: 'Acme Lifts' }, searches: null })
  vi.mocked(api.getWrittenReport).mockResolvedValue(WRITTEN)
})

const pick = async (label: string, option: string) => {
  fireEvent.mouseDown(screen.getByLabelText(label))
  fireEvent.click(await screen.findByRole('option', { name: option }))
}
const openPerson = async () => fireEvent.click(await screen.findByRole('tab', { name: 'About a person' }, PATIENT))
const type = (label: string, said: string) => fireEvent.change(screen.getByLabelText(label), { target: { value: said } })
const press = async (name: string) => {
  const button = await screen.findByRole('button', { name }, PATIENT)
  await waitFor(() => expect(button).toBeEnabled(), PATIENT)
  fireEvent.click(button)
}

describe('the words', () => {
  it('writes a period as it was read, and a hold beside what it holds', () => {
    expect(periodLine(STATEMENT.periods[0].period)).toBe('30 days')
    expect(periodLine(STATEMENT.periods[3].period)).toBe('1 day')
    expect(periodLine(STATEMENT.periods[4].period)).toBe('7 years')
    expect(holdLine(STATEMENT.periods[0])).toBe('A hold stops it — 2 are held now')
    expect(holdLine({ ...STATEMENT.periods[0], held_now: 1 })).toBe('A hold stops it — 1 is held now')
    expect(holdLine(STATEMENT.periods[1])).toBe('A hold stops it — none is held now')
    expect(holdLine(STATEMENT.periods[3])).toBe('No hold applies')
    expect(siteLine(STATEMENT.sites[0])).toBe('3 days')
    expect(siteLine(STATEMENT.sites[2])).toBe('Nothing is kept centrally')
  })

  it('says how often and between which dates, never what a record says', () => {
    const line = STAFF.held[0].lines![0]
    expect(lineWords(line)).toMatch(/^Was sent to the incident: 3, between .+ and .+$/)
    expect(between({ count: 1, first: '2026-10-02T08:00:00Z', last: '2026-10-02T08:00:00Z' })).toMatch(/^1, on .+$/)
    expect(between({ count: 4, first: null, last: null })).toBe('4')
    expect(heldCount(STAFF.held[1])).toBe('2 times')
    expect(heldCount(STAFF.held[0])).toBe('3 times')
    expect(heldCount(WRITTEN.held[0])).toBe('2 matches')
    expect(subjectLine(STAFF)).toBe('Mei Lin, Security Guard')
    expect(subjectLine({ ...STAFF, subject: { ...STAFF.subject, in_use: false } })).toBe('Mei Lin, Security Guard — no longer in use')
    expect(subjectLine(WRITTEN)).toBe('“tan wei ming”, as it was typed')
    expect(totalsLine(STAFF)).toBe('4 records concern them and 1 step was taken by them, in 2 kinds of record.')
    expect(totalsLine(WRITTEN)).toBe('2 matches in 1 kind of record.')
    expect(totalsLine({ ...WRITTEN, totals: { matches: 0, kinds_of_record: 0 } })).toBe('Nothing in these records names them.')
    expect(searchesLine(STAFF.searches!)).toMatch(/^2 investigation searches asked about them, by 2 people, between .+ and .+\.$/)
    expect(searchesLine(WRITTEN.searches!)).toBe('No investigation search asked about them.')
  })

  it('shows text that matched as text, and accuses nobody', () => {
    const said = matchLine(WRITTEN.held[0].matches![0])
    expect(said).toMatch(/^“Tan Wei Ming”, written in as a person in 2 cases: 2, between .+ \(1 taken off since\)$/)
    expect(matchLine({ text: 'Acme Engineering', count: 1, first: null, last: null })).toBe('“Acme Engineering”: 1')
    for (const words of [said, totalsLine(STAFF), totalsLine(WRITTEN), searchesLine(STAFF.searches!), subjectLine(WRITTEN)]) {
      expect(words).not.toMatch(ACCUSING)
    }
  })
})

describe('the retention statement', () => {
  it('gives every period with where it is set, what removes it and what a hold does', async () => {
    show()
    const table = await screen.findByTestId('periods', {}, PATIENT)
    expect(within(table).getAllByRole('columnheader').map((h) => h.textContent)).toEqual([
      'Kind of record', 'Kept for', 'Set by', 'Removed by', 'Holds'])
    const rows = within(table).getAllByTestId('period-row')
    expect(rows).toHaveLength(5)
    expect(rows[0]).toHaveTextContent('Pictures and clips kept as evidence')
    expect(rows[0]).toHaveTextContent('Counted from when it was captured')
    expect(rows[0]).toHaveTextContent('30 days')
    expect(rows[0]).toHaveTextContent('The organisation\'s setting')
    expect(rows[0]).toHaveTextContent('Setting: evidence.retention_days, set')
    expect(rows[0]).toHaveTextContent('The scheduler, once a day')
    expect(rows[0]).toHaveTextContent('A hold stops it — 2 are held now')
    expect(rows[0]).not.toHaveTextContent(NOTE)
    // A period that falls back says whose is the one in force.
    expect(rows[1]).toHaveTextContent('7 days')
    expect(rows[1]).toHaveTextContent('The installation\'s default, as this service reads it')
    expect(rows[1]).toHaveTextContent(NOTE)
    expect(rows[2]).toHaveTextContent('Also kept past its period: Footage of an event that became an incident')
    expect(rows[3]).toHaveTextContent('Fixed in the code')
    expect(rows[3]).toHaveTextContent('No hold applies')
    expect(rows[4]).toHaveTextContent('7 years')
    expect(rows[4]).toHaveTextContent('Not deleted')
    expect(rows[4]).toHaveTextContent('The same for every organisation of the installation.')
    expect(screen.getByTestId('not-law')).toHaveTextContent(NOT_LAW)
  })

  it('gives each site its own period, the holds in force, what has no period and what an erasure removes', async () => {
    show()
    const sites = within(await screen.findByTestId('sites', {}, PATIENT)).getAllByTestId('site-row')
    expect(sites.map((r) => within(r).getAllByRole('cell').map((c) => c.textContent).slice(0, 2))).toEqual([
      ['Factory A', '3 days'], ['Factory B', '7 days'], ['Gatehouse (not in use)', 'Nothing is kept centrally']])
    expect(sites[0]).toHaveTextContent('This site\'s recording policy, set')
    expect(sites[0]).toHaveTextContent('2 days')
    expect(sites[1]).toHaveTextContent('Not set')
    expect(sites[2]).toHaveTextContent('1 day')
    const holds = screen.getByTestId('holds')
    expect(holds).toHaveTextContent('3 in force: 2 on snapshot, 1 on drone media.')
    expect(holds).toHaveTextContent('until a person releases it, with a reason')
    const kept = screen.getByTestId('kept')
    expect(kept).toHaveTextContent('Every other record is removed by no job.')
    const groups = within(kept).getAllByTestId('kept-group')
    expect(groups[0]).toHaveTextContent('Cases: their people, tasks, history')
    expect(groups[0]).toHaveTextContent('Names people')
    expect(groups[1]).toHaveTextContent('Names nobody')
    expect(groups[1]).toHaveTextContent('The kinds of incident a procedure is for are replaced when they are set again.')
    expect(screen.getByTestId('erasure')).toHaveTextContent('the authorisations of their visits go with them')
    // The periods in force are read here, and changed where they have always been changed: none has a control.
    expect(within(screen.getByTestId('periods')).queryByRole('button')).not.toBeInTheDocument()
    expect(within(screen.getByTestId('sites')).queryByRole('button')).not.toBeInTheDocument()
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
  })

  it('says when no hold is in force, and shows the server\'s refusal', async () => {
    vi.mocked(api.getStatement).mockResolvedValue({ ...STATEMENT, sites: [], holds: { ...STATEMENT.holds, in_force: 0, by_kind: {} } })
    const first = show()
    expect(await screen.findByTestId('holds', {}, PATIENT)).toHaveTextContent('None is in force.')
    expect(screen.getByTestId('sites')).toHaveTextContent('No site is shown to you.')
    first.unmount()
    vi.mocked(api.getStatement).mockRejectedValue(refusal('Missing permission: retention:read'))
    show()
    expect(await screen.findByText('Missing permission: retention:read', {}, PATIENT)).toBeInTheDocument()
  })

  it('offers asking about a person only to whoever may', async () => {
    signIn(3)
    show()
    await screen.findByTestId('periods', {}, PATIENT)
    expect(screen.getByRole('tab', { name: 'Retention' })).toBeInTheDocument()
    expect(screen.queryByRole('tab', { name: 'About a person' })).not.toBeInTheDocument()
  })
})

describe('a period the organisation may set', () => {
  it('shows each kind as kept until a period is set, and offers setting one only to whoever may', async () => {
    expect(optionalLine(STATEMENT.optional[0])).toBe('Kept: no period is set')
    expect(optionalLine(STATEMENT.optional[1])).toBe('Removed 365 days after the end of the period it was for')
    const first = show()
    const card = await screen.findByTestId('optional', {}, PATIENT)
    expect(card).toHaveTextContent('These are kept until the organisation sets a period for them.')
    const kinds = within(card).getAllByTestId('optional-kind')
    expect(kinds[0]).toHaveTextContent('Closed cases')
    expect(kinds[0]).toHaveTextContent('Kept: no period is set')
    expect(kinds[0]).toHaveTextContent('Kept whatever its age: A case that is open, or waiting for approval to close.')
    expect(within(kinds[0]).getByRole('button', { name: 'Set a period' })).toBeInTheDocument()
    expect(within(kinds[0]).queryByRole('button', { name: 'Take the period away' })).not.toBeInTheDocument()
    expect(kinds[1]).toHaveTextContent('Removed 365 days after the end of the period it was for')
    expect(kinds[1]).toHaveTextContent('An authorisation goes with its visitor when a data-subject erasure removes the visitor.')
    expect(within(kinds[1]).getByRole('button', { name: 'Change the period' })).toBeInTheDocument()
    expect(within(kinds[1]).getByRole('button', { name: 'Take the period away' })).toBeInTheDocument()
    first.unmount()
    vi.mocked(api.getStatement).mockResolvedValue({ ...STATEMENT, may_set: false })
    show()
    const read = await screen.findByTestId('optional', {}, PATIENT)
    expect(within(read).queryByRole('button')).not.toBeInTheDocument()
  })

  it('says how many are already older before a period is set, and sets it when that is confirmed', async () => {
    vi.mocked(api.setRetentionPeriod)
      .mockRejectedValueOnce(refusal({ message: '3 of these are already older than 365 days and will be removed for good when the scheduler next runs. Confirm to set the period.', already_older: 3, days: 365 }, 409))
      .mockResolvedValueOnce({ kind: 'CASES', label: 'Closed cases', days: 365, set_at: '2026-10-09T02:00:00Z', already_older: 3 })
    show()
    const card = await screen.findByTestId('optional', {}, PATIENT)
    fireEvent.click(within(within(card).getAllByTestId('optional-kind')[0]).getByRole('button', { name: 'Set a period' }))
    const dialog = await screen.findByRole('dialog', {}, PATIENT)
    expect(dialog).toHaveTextContent('A period for: closed cases')
    expect(dialog).toHaveTextContent('What is removed is removed for good.')
    const set = within(dialog).getByRole('button', { name: 'Set the period' })
    expect(set).toBeDisabled()
    fireEvent.change(within(dialog).getByLabelText('Days'), { target: { value: '7' } })
    expect(set).toBeDisabled()
    fireEvent.change(within(dialog).getByLabelText('Days'), { target: { value: '365' } })
    await waitFor(() => expect(set).toBeEnabled(), PATIENT)
    fireEvent.click(set)
    expect(await within(dialog).findByTestId('already-older', {}, PATIENT)).toHaveTextContent('3 of these are already older than 365 days')
    expect(api.setRetentionPeriod).toHaveBeenLastCalledWith('CASES', 365, undefined)
    const confirm = await within(dialog).findByRole('button', { name: 'Set it, and remove them' }, PATIENT)
    await waitFor(() => expect(confirm).toBeEnabled(), PATIENT)
    fireEvent.click(confirm)
    await waitFor(() => expect(api.setRetentionPeriod).toHaveBeenLastCalledWith('CASES', 365, 3), PATIENT)
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument(), PATIENT)
    await waitFor(() => expect(vi.mocked(api.getStatement).mock.calls.length).toBeGreaterThan(1), PATIENT)
  })

  it('takes a period away after saying that what was removed does not come back', async () => {
    vi.mocked(api.setRetentionPeriod).mockResolvedValue({ kind: 'VISITOR_AUTHORIZATIONS', label: 'x', days: null, set_at: null, already_older: 0 })
    show()
    const card = await screen.findByTestId('optional', {}, PATIENT)
    fireEvent.click(within(within(card).getAllByTestId('optional-kind')[1]).getByRole('button', { name: 'Take the period away' }))
    const dialog = await screen.findByRole('dialog', {}, PATIENT)
    expect(dialog).toHaveTextContent('What the period has already removed does not come back.')
    fireEvent.click(within(dialog).getByRole('button', { name: 'Take it away' }))
    await waitFor(() => expect(api.setRetentionPeriod).toHaveBeenCalledWith('VISITOR_AUTHORIZATIONS', null), PATIENT)
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument(), PATIENT)
  })
})

describe('asking about a person', () => {
  it('finds a member of staff by name and says where they appear, how often and who looked', async () => {
    show()
    await openPerson()
    expect(screen.getByRole('button', { name: 'Find' })).toBeDisabled()
    type('Their name, or part of it', 'me')
    expect(screen.getByRole('button', { name: 'Find' })).toBeDisabled()
    type('Their name, or part of it', ' mei ')
    await press('Find')
    const found = await screen.findByTestId('found', {}, PATIENT)
    expect(api.findSubject).toHaveBeenCalledWith('STAFF', 'mei')
    const people = within(found).getAllByTestId('found-row')
    expect(people[0]).toHaveTextContent('Mei Lin — Security Guard')
    expect(people[1]).toHaveTextContent('Mei Ling Tan — Operator (no longer in use)')
    fireEvent.click(within(people[0]).getByRole('button', { name: 'Where they appear' }))
    const report = await screen.findByTestId('report', {}, PATIENT)
    expect(api.getStaffReport).toHaveBeenCalledWith('u1')
    expect(report).toHaveTextContent('Mei Lin, Security Guard')
    expect(within(report).getByTestId('totals')).toHaveTextContent('4 records concern them and 1 step was taken by them, in 2 kinds of record.')
    expect(report).toHaveTextContent(WHAT)
    const held = within(report).getAllByTestId('held')
    expect(held[0]).toHaveTextContent('Guards sent to incidents — 3 times')
    expect(held[1]).toHaveTextContent('Tasks on cases — 2 times')
    const lines = within(report).getAllByTestId('line').map((l) => l.textContent)
    expect(lines[0]).toMatch(/^Was sent to the incident: 3, between /)
    expect(lines[0]).toMatch(/— concerns them$/)
    expect(lines[2]).toMatch(/^Finished or dropped the task: 1, on .+ — a step they took$/)
    expect(within(report).getByTestId('nothing-in')).toHaveTextContent('Nothing in: Investigations; Daily briefings.')
    expect(within(report).getByTestId('searches')).toHaveTextContent('2 investigation searches asked about them, by 2 people')
    expect(within(report).getByTestId('searches')).toHaveTextContent('Who searched, and when, is in the audit log.')
    expect(within(report).getByTestId('not-read')).toHaveTextContent('A name written in a title or a note.')
    expect(report).toHaveTextContent('That this report was asked for, and by whom, is written in the audit log.')
    expect(within(report).queryByTestId('text-is-text')).not.toBeInTheDocument()
    expect(report.textContent).not.toMatch(ACCUSING)
  })

  it('finds a visitor, and says when nobody is of that name or more match than are shown', async () => {
    vi.mocked(api.findSubject).mockResolvedValue({ kind: 'VISITOR', more: true, found: [{ id: 'v1', name: 'Lim Mei Ling', detail: 'Acme Lifts', in_use: true }] })
    show()
    await openPerson()
    await pick('Look for', 'A visitor')
    type('Their name, or part of it', 'mei ling')
    await press('Find')
    const found = await screen.findByTestId('found', {}, PATIENT)
    expect(api.findSubject).toHaveBeenCalledWith('VISITOR', 'mei ling')
    expect(found).toHaveTextContent('Lim Mei Ling — Acme Lifts')
    expect(found).toHaveTextContent('More match than are shown. Type more of the name.')
    fireEvent.click(within(found).getByRole('button', { name: 'Where they appear' }))
    const report = await screen.findByTestId('report', {}, PATIENT)
    expect(api.getVisitorReport).toHaveBeenCalledWith('v1')
    expect(report).toHaveTextContent('Lim Mei Ling, Acme Lifts — a visitor')
    expect(within(report).queryByTestId('searches')).not.toBeInTheDocument()

    vi.mocked(api.findSubject).mockResolvedValue({ kind: 'VISITOR', more: false, found: [] })
    type('Their name, or part of it', 'nobody at all')
    await press('Find')
    expect(await screen.findByText('Nobody of that name.', {}, PATIENT)).toBeInTheDocument()
  })

  it('looks for a name as it was typed, and says that what matched is text', async () => {
    show()
    await openPerson()
    await pick('Look for', 'A name or a number plate, as it was typed')
    expect(screen.getByText(/A member of staff or a visitor is better chosen by their record/)).toBeInTheDocument()
    type('The name or the plate', 'tan wei ming')
    await press('Look for it')
    const report = await screen.findByTestId('report', {}, PATIENT)
    expect(api.getWrittenReport).toHaveBeenCalledWith('tan wei ming')
    expect(api.findSubject).not.toHaveBeenCalled()
    expect(report).toHaveTextContent('“tan wei ming”, as it was typed')
    expect(within(report).getByTestId('text-is-text')).toHaveTextContent(TEXT_IS_TEXT)
    expect(within(report).getByTestId('match').textContent).toMatch(/^“Tan Wei Ming”, written in as a person in 2 cases: 2, between /)
    expect(within(report).getByTestId('searches')).toHaveTextContent('No investigation search asked about them.')
    expect(report.textContent).not.toMatch(ACCUSING)
    expect(screen.queryByTestId('found')).not.toBeInTheDocument()
  })

  it('shows the server\'s refusal in its own words', async () => {
    vi.mocked(api.getWrittenReport).mockRejectedValue(refusal('A subject report covers every site, so it is read by somebody who is not held to particular sites.'))
    show()
    await openPerson()
    await pick('Look for', 'A name or a number plate, as it was typed')
    type('The name or the plate', 'SGX1234A')
    await press('Look for it')
    expect(await screen.findByText(/A subject report covers every site/, {}, PATIENT)).toBeInTheDocument()
    expect(screen.queryByTestId('report')).not.toBeInTheDocument()
  })
})
