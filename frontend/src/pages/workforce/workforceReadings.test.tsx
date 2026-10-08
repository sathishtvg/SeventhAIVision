import type { ReactNode } from 'react'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ThemeProvider } from '@mui/material'
import { render, screen, fireEvent, waitFor, within } from '@/test/utils'
import { theme } from '@/theme/glassmorphism'
import { useAuthStore } from '@/store/auth'
import * as api from '@/api/workforce'
import type { Figures, GivenAnswer, Reading, Readings, Recommendation, Recommendations, Section } from '@/api/workforce'
import {
  ANSWER_LABEL, COLUMNS, VIOLATION_LABEL, about, answerLine, givenAbout, lines, named, of, periodLabel,
} from '@/components/workforce/workforceFormat'
import WorkforceReadings, { MyReading } from './WorkforceReadings'

vi.mock('@/store/auth', () => ({ useAuthStore: vi.fn() }))
vi.mock('@/api/sites', () => ({ getSites: vi.fn().mockResolvedValue([
  { id: 's1', name: 'Factory A' }, { id: 's2', name: 'Factory B' }]) }))
// PageHeader reads the tenant's page names from the settings.
vi.mock('@/api/settings', () => ({ getSettings: vi.fn().mockResolvedValue([]), upsertSetting: vi.fn().mockResolvedValue({}) }))
vi.mock('@/api/workforce', async (orig) => {
  const real = await orig<typeof import('@/api/workforce')>()
  const fns = Object.fromEntries(Object.entries(real).map(([k, v]) => [k, typeof v === 'function' ? vi.fn() : v]))
  return { ...fns, apiError: real.apiError }
})

function show(el: ReactNode = <WorkforceReadings />) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } })
  return render(
    <MemoryRouter>
      <QueryClientProvider client={qc}><ThemeProvider theme={theme}>{el}</ThemeProvider></QueryClientProvider>
    </MemoryRouter>, { wrapper: ({ children }) => <>{children}</> })
}

const NOTE = "Counts of what is recorded of each guard's work at the sites shown, each beside how much there was to do. It is not an appraisal, it ranks nobody, and it decides nothing about anybody's employment."
const ADVICE_NOTE = 'Recommendations for a manager, made of what is recorded over the last 4 weeks. Each is advice: accepting one assigns no course and changes no roster, and none is a decision about anybody\'s employment.'
const MEI: Required<Figures> = {
  SHIFTS: { shifts: 4, worked: 3, late: 1, late_minutes: 12, not_started: 1 },
  PATROLS: { tours_done: 2, tours_missed: 3, walked: 2, checkpoints_scanned: 16, checkpoints_total: 20 },
  RESPONSES: { sent: 3, accepted: 2, declined: 1, arrived: 2, arrive_seconds: 480 },
  VIOLATIONS: { recorded: 3, waived: 1, disputed: 1,
                by_type: { no_show: 0, late_checkin: 1, geofence_failure: 1, early_departure: 0, manual: 0 } },
  TRAINING: { completed: 1, courses_lapsed_now: 1, courses_lapsing_now: 1, certificates_lapsed_now: 1,
              certificates_lapsing_now: 1, shifts_at_risk_now: 2 },
  HANDOVERS: { given: 2, accepted: 1, disputed: 1 },
}
const NOTHING: Required<Figures> = {
  SHIFTS: { shifts: 0, worked: 0, late: 0, late_minutes: 0, not_started: 0 },
  PATROLS: { tours_done: 0, tours_missed: 0, walked: 0, checkpoints_scanned: 0, checkpoints_total: 0 },
  RESPONSES: { sent: 0, accepted: 0, declined: 0, arrived: 0, arrive_seconds: null },
  VIOLATIONS: { recorded: 0, waived: 0, disputed: 0,
                by_type: { no_show: 0, late_checkin: 0, geofence_failure: 0, early_departure: 0, manual: 0 } },
  TRAINING: { completed: 0, courses_lapsed_now: 0, courses_lapsing_now: 0, certificates_lapsed_now: 0,
              certificates_lapsing_now: 0, shifts_at_risk_now: 0 },
  HANDOVERS: { given: 0, accepted: 0, disputed: 0 },
}
const TITLES = { SHIFTS: 'Shifts', PATROLS: 'Patrols', RESPONSES: 'Responses', VIOLATIONS: 'Violations',
                 TRAINING: 'Training and certifications', HANDOVERS: 'Handovers' } as const
const SECTIONS: Section[] = (Object.keys(TITLES) as (keyof typeof TITLES)[]).map((key) => ({
  key, title: TITLES[key], counted_from: `${TITLES[key]} are counted from their own records.`, personal: key === 'TRAINING' }))
const PERIOD = { days: 28, from: '2026-09-10T04:00:00Z', to: '2026-10-08T04:00:00Z' }
const { TRAINING: _person, ...AT_SITE } = MEI
void _person
const READINGS: Readings = {
  period: PERIOD, site: null, sections: SECTIONS, note: NOTE, not_read: [],
  // As the server gives them: in order of name.
  guards: [{ id: 'g0', name: 'Asha Rao', role_id: 5, is_active: true, figures: NOTHING },
           { id: 'g1', name: 'Mei Lin', role_id: 5, is_active: true, figures: MEI },
           { id: 'g2', name: 'Zul Hakim', role_id: 5, is_active: false, figures: { SHIFTS: { ...MEI.SHIFTS, worked: 4, late: 0 } } }],
  sites: [{ id: 's1', name: 'Factory A', figures: AT_SITE }, { id: 's2', name: 'Factory B', figures: { SHIFTS: NOTHING.SHIFTS } }],
  no_site: null, total: AT_SITE,
}
const rec = (over: Partial<Recommendation>): Recommendation => ({
  key: 'MISSED_TOURS:g1', kind: 'TRAINING', code: 'MISSED_TOURS',
  statement: 'Mei Lin missed 3 of the 5 tours assigned to them that fell due in the last 4 weeks.',
  consider: 'Whether the route can be walked in the time given, and whether they have been shown it. Courses filed under security: Security patrol basics.',
  subject: { user_id: 'g1', name: 'Mei Lin' }, site: null, rests_on: { missed: 3, due: 5 }, is_advisory: true, is_decision: false,
  answer: null, may_answer: true, ...over })
const LAPSED = rec({
  key: 'COURSE:g1:c1', code: 'COURSE_LAPSED', statement: "Mei Lin's pass in First aid lapsed on 28 Sep 2026.",
  consider: 'Assigning the course again. It is assigned in Training.',
  answer: { answer: 'NOT_ACCEPTED', reason: 'She is booked on the November course.', answered_at: '2026-10-07T02:00:00Z',
            answered_by_name: 'Siti Rahman', said_then: "Mei Lin's pass in First aid lapses on 28 Sep 2026." } })
const COVER = rec({
  key: 'HOURS_COVER:s1:00', kind: 'COVERAGE', code: 'HOURS_COVER', subject: null, site: { id: 's1', name: 'Factory A' },
  statement: '80% of the incidents at Factory A in the last 4 weeks fell between 00:00 and 04:00 (12 of 15). 0 guard-hours were rostered in those hours, against 8 for an average four hours of its day.',
  consider: 'Whether those hours have the people the rest of the day has. The roster is changed in the roster, by a person.' })
const READING: Reading = {
  period: PERIOD, guard: { id: 'g1', name: 'Mei Lin', role_id: 5, is_active: true }, sections: SECTIONS, figures: MEI,
  by_site: [{ id: 's1', name: 'Factory A', figures: AT_SITE }, { id: 's2', name: 'Factory B', figures: { SHIFTS: { ...NOTHING.SHIFTS, shifts: 1, worked: 1 } } }],
  not_read: [], note: NOTE, recommendations: [rec({}), LAPSED], recommendations_note: ADVICE_NOTE,
}
const ADVICE: Recommendations = {
  weeks: 4, site: null, training: [rec({}), LAPSED], coverage: [COVER], not_read: [], can_answer: true, note: ADVICE_NOTE }
const GIVEN: GivenAnswer = {
  id: 'a1', kind: 'TRAINING', code: 'COURSE_LAPSED', statement: "Mei Lin's pass in First aid lapses on 28 Sep 2026.",
  answer: 'NOT_ACCEPTED', reason: 'She is booked on the November course.', answered_at: '2026-10-07T02:00:00Z',
  answered_by_name: 'Siti Rahman', guard_name: 'Mei Lin', site_name: null }
const refusal = (detail: unknown, status = 409) => Object.assign(new Error('x'), { response: { status, data: { detail } } })
const PATIENT = { timeout: 8000 }
/** Words that would turn a count into a judgement of a person. */
const JUDGING = /poor|bad|worst|best|underperform|disciplin|warning|score|rank|rating|unreliable/i

function signIn(roleId = 2) {
  vi.mocked(useAuthStore).mockImplementation(((sel: (s: unknown) => unknown) =>
    sel({ user: { id: 'me', tenantId: 't1', roleId }, accessToken: 'tok', permissions: null })) as never)
}

beforeEach(() => {
  vi.clearAllMocks()
  signIn()
  vi.mocked(api.getReadings).mockResolvedValue(READINGS)
  vi.mocked(api.getReading).mockResolvedValue(READING)
  vi.mocked(api.getMyReading).mockResolvedValue({ ...READING, recommendations: READING.recommendations.map((r) => ({ ...r, may_answer: false, answer: null })) })
  vi.mocked(api.getRecommendations).mockResolvedValue(ADVICE)
  vi.mocked(api.getAnswers).mockResolvedValue([GIVEN])
  vi.mocked(api.answerRecommendation).mockResolvedValue(rec({}))
})

const pick = async (label: string, option: string) => {
  fireEvent.mouseDown(screen.getByLabelText(label))
  fireEvent.click(await screen.findByRole('option', { name: option }))
}
const rows = (table: HTMLElement) =>
  within(table).getAllByTestId('figures-row').map((r) => within(r).getAllByRole('cell').map((c) => c.textContent))
const openTab = async (name: string) => fireEvent.click(await screen.findByRole('tab', { name }, PATIENT))

describe('the words', () => {
  it('never says a count without how much there was to do', () => {
    expect(of(3, 4)).toBe('3 of 4')
    expect(lines('SHIFTS', MEI)).toEqual(['3 of 4 shifts worked', '1 of 3 started late — 12 minutes in all', '1 of 4 not started'])
    expect(lines('PATROLS', MEI)).toEqual(['2 of 5 assigned tours done; 3 missed', '2 patrols walked: 16 of 20 checkpoints scanned'])
    expect(lines('RESPONSES', MEI)).toEqual(['Sent 3 times: 2 accepted, 1 declined, 2 arrived', 'Half arrived within 8 minutes'])
    expect(lines('HANDOVERS', MEI)).toEqual(['2 given: 1 accepted, 1 disputed by the incoming guard'])
    expect(lines('TRAINING', MEI)).toEqual([
      '1 course passed in the period', 'Courses, today: 1 lapsed, 1 lapsing within 30 days',
      'Certificates, today: 1 lapsed, 1 lapsing within 30 days', '2 rostered shifts need a certificate not held'])
    expect(COLUMNS.map((c) => c.of(MEI))).toEqual(['3 of 4', '1 of 3', '1 of 4', '2 of 5', '2 of 3', '2 of 3'])
    // A section that is not read is a dash, not a nought.
    expect(COLUMNS.map((c) => c.of({}))).toEqual(COLUMNS.map(() => '—'))
  })

  it('says of a violation what a reviewer and the guard have said of it', () => {
    expect(lines('VIOLATIONS', MEI)).toEqual([
      '3 recorded: 1 waived by a reviewer, 1 disputed by the guard', 'Not waived: 1 late check-in, 1 outside the geofence'])
    expect(lines('VIOLATIONS', { VIOLATIONS: { ...NOTHING.VIOLATIONS, recorded: 2, waived: 2 } })).toEqual([
      '2 recorded: 2 waived by a reviewer, 0 disputed by the guard', 'All of them were waived'])
    // What was waived is not in the column that is counted.
    expect(COLUMNS.find((c) => c.key === 'violations')!.label).toBe('Violations not waived')
    expect(Object.keys(VIOLATION_LABEL)).toHaveLength(5)
  })

  it('says nothing recorded as that, and judges nobody', () => {
    expect(lines('SHIFTS', NOTHING)).toEqual(['No shifts were due to begin'])
    expect(lines('PATROLS', NOTHING)).toEqual(['No assigned tour fell due', 'No patrol walked'])
    expect(lines('RESPONSES', NOTHING)).toEqual(['Not sent to an incident'])
    expect(lines('VIOLATIONS', NOTHING)).toEqual(['None recorded'])
    expect(lines('HANDOVERS', NOTHING)).toEqual(['No handover given'])
    expect(lines('TRAINING', NOTHING)[3]).toBe('No rostered shift needs a certificate not held')
    expect(lines('RESPONSES', { RESPONSES: { ...NOTHING.RESPONSES, sent: 1, declined: 1 } })).toEqual([
      'Sent 1 time: 0 accepted, 1 declined, 0 arrived', 'No arrival to time'])
    const said = [...(Object.keys(TITLES) as (keyof typeof TITLES)[]).flatMap((k) => [...lines(k, MEI), ...lines(k, NOTHING)]),
                  ...COLUMNS.map((c) => c.label), ...Object.values(VIOLATION_LABEL)]
    for (const line of said) expect(line).not.toMatch(JUDGING)
    expect([periodLabel(7), periodLabel(28), periodLabel(90)]).toEqual(['The last 7 days', 'The last 4 weeks', 'The last 90 days'])
  })

  it('says who a recommendation is about, and an answer in a line', () => {
    expect([about(rec({})), about(COVER)]).toEqual(['Mei Lin', 'Factory A'])
    expect([named('Mei Lin'), named(null)]).toEqual(['Mei Lin', 'Somebody no longer on the system'])
    expect(answerLine(LAPSED.answer!)).toMatch(/^Not accepted by Siti Rahman, .*: She is booked on the November course\.$/)
    expect(ANSWER_LABEL).toEqual({ ACCEPTED: 'Accepted', NOT_ACCEPTED: 'Not accepted' })
    expect([givenAbout(GIVEN), givenAbout({ ...GIVEN, guard_name: null, site_name: 'Factory A' })]).toEqual(['Mei Lin', 'Factory A'])
  })
})

describe('the guards', () => {
  it('lists them in the order they are given, with a count beside what it is a count of, and no way to sort them', async () => {
    show()
    const table = await screen.findByTestId('figures-table', {}, PATIENT)
    expect(within(table).getAllByRole('columnheader').map((h) => h.textContent)).toEqual([
      'Guard', 'Shifts worked', 'Started late', 'Not started', 'Tours done', 'Arrived when sent', 'Violations not waived'])
    expect(rows(table)).toEqual([
      ['Asha Rao', '0 of 0', '0 of 0', '0 of 0', '0 of 0', '0 of 0', '0 of 0'],
      ['Mei Lin', '3 of 4', '1 of 3', '1 of 4', '2 of 5', '2 of 3', '2 of 3'],
      // Somebody no longer in use is said to be; a section that is not theirs to show is a dash.
      ['Zul Hakim (not in use)', '4 of 4', '0 of 4', '1 of 4', '—', '—', '—']])
    // Nothing ranks them: no heading sorts, and there is no total of a person.
    for (const heading of within(table).getAllByRole('columnheader')) {
      expect(heading).not.toHaveAttribute('aria-sort')
      expect(within(heading).queryByRole('button')).not.toBeInTheDocument()
    }
    expect(screen.getByTestId('readings-note')).toHaveTextContent(NOTE)
    expect(screen.getByText(/In order of name\. Each cell is a count beside how much there was to do/)).toBeInTheDocument()
    expect(screen.getByText(/not an appraisal, and nobody is ranked/)).toBeInTheDocument()
    expect(api.getReadings).toHaveBeenCalledWith({ site_id: undefined, days: 28 })
  })

  it('asks again for another period or site', async () => {
    show()
    await screen.findByTestId('figures-table', {}, PATIENT)
    await pick('Period', 'The last 7 days')
    await waitFor(() => expect(api.getReadings).toHaveBeenLastCalledWith({ site_id: undefined, days: 7 }))
    await pick('Site', 'Factory B')
    await waitFor(() => expect(api.getReadings).toHaveBeenLastCalledWith({ site_id: 's2', days: 7 }))
  })

  it('names a section that is not shown, and gives the reason when a reading cannot be read', async () => {
    vi.mocked(api.getReadings).mockResolvedValue({
      ...READINGS, not_read: [{ key: 'VIOLATIONS', title: 'Violations', needs: 'violation:read' }] })
    show()
    expect(await screen.findByTestId('not-read', {}, PATIENT)).toHaveTextContent('Not shown to you: Violations (read under violation:read).')
    vi.mocked(api.getReadings).mockRejectedValue(refusal("A person's reading is read by the organisation's own staff, not from a support session.", 403))
    show()
    expect(await screen.findByText(/not from a support session/, {}, PATIENT)).toBeInTheDocument()
  })

  it('opens one guard\'s whole reading: each section in lines, site by site, and what is recommended', async () => {
    show()
    const table = await screen.findByTestId('figures-table', {}, PATIENT)
    fireEvent.click(within(table).getAllByTestId('figures-row')[1])
    const reading = await screen.findByTestId('reading', {}, PATIENT)
    expect(api.getReading).toHaveBeenCalledWith('g1', 28)
    expect(reading).toHaveTextContent(NOTE)
    expect(within(within(reading).getByTestId('section-SHIFTS')).getAllByTestId('line').map((l) => l.textContent)).toEqual([
      '3 of 4 shifts worked', '1 of 3 started late — 12 minutes in all', '1 of 4 not started'])
    expect(within(reading).getByTestId('section-VIOLATIONS')).toHaveTextContent('1 waived by a reviewer, 1 disputed by the guard')
    expect(within(reading).getByTestId('section-TRAINING')).toHaveTextContent('Training and certifications are counted from their own records.')
    // Site by site, in a table of the same counts.
    expect(rows(within(reading).getByTestId('figures-table'))).toEqual([
      ['Factory A', '3 of 4', '1 of 3', '1 of 4', '2 of 5', '2 of 3', '2 of 3'],
      ['Factory B', '1 of 1', '0 of 1', '0 of 1', '—', '—', '—']])
    const recommended = within(reading).getByTestId('recommended')
    expect(recommended).toHaveTextContent(ADVICE_NOTE)
    expect(within(recommended).getAllByTestId('recommendation')).toHaveLength(2)
    expect(screen.getByRole('dialog')).toHaveTextContent('Mei Lin — the last 4 weeks')
  })
})

describe('the sites', () => {
  it('gives each site its counts and the sites together, and says what is not a site\'s', async () => {
    show()
    await openTab('Sites')
    const table = await screen.findByTestId('figures-table', {}, PATIENT)
    expect(within(table).getAllByRole('columnheader')[0]).toHaveTextContent('Site')
    expect(rows(table)).toEqual([
      ['Factory A', '3 of 4', '1 of 3', '1 of 4', '2 of 5', '2 of 3', '2 of 3'],
      ['Factory B', '0 of 0', '0 of 0', '0 of 0', '—', '—', '—'],
      ['Together', '3 of 4', '1 of 3', '1 of 4', '2 of 5', '2 of 3', '2 of 3']])
    expect(screen.getByText(/Training and certificates are a person's and are in no site's/)).toBeInTheDocument()
    // A site's row is not a way into anybody's reading.
    fireEvent.click(within(table).getAllByTestId('figures-row')[0])
    expect(api.getReading).not.toHaveBeenCalled()
  })

  it('shows what is at no site apart', async () => {
    vi.mocked(api.getReadings).mockResolvedValue({ ...READINGS, no_site: { SHIFTS: { ...NOTHING.SHIFTS, shifts: 2 } } })
    show()
    await openTab('Sites')
    const table = await screen.findByTestId('figures-table', {}, PATIENT)
    await waitFor(() => expect(rows(table).map((r) => r[0])).toEqual(['Factory A', 'Factory B', 'At no site', 'Together']))
  })
})

describe('recommendations', () => {
  const advice = async () => {
    await openTab('Recommendations')
    return screen.findByTestId('training', {}, PATIENT)
  }

  it('shows what is recommended for a guard and for a site, and says that accepting changes nothing', async () => {
    show()
    const training = await advice()
    expect(screen.getByTestId('advice-note')).toHaveTextContent('accepting one assigns no course and changes no roster')
    expect(screen.getByTestId('advice-note')).toHaveTextContent("none is a decision about anybody's employment")
    const recs = within(training).getAllByTestId('recommendation')
    expect(recs).toHaveLength(2)
    expect(recs[0]).toHaveTextContent('Mei Lin missed 3 of the 5 tours assigned to them that fell due in the last 4 weeks.')
    expect(recs[0]).toHaveTextContent('To consider: Whether the route can be walked in the time given')
    expect(recs[0]).toHaveTextContent('Courses filed under security: Security patrol basics.')
    const cover = within(screen.getByTestId('coverage')).getByTestId('recommendation')
    expect(cover).toHaveTextContent('Factory A')
    expect(cover).toHaveTextContent('The roster is changed in the roster, by a person.')
    for (const r of [...recs, cover]) expect(r.textContent).not.toMatch(JUDGING)
    expect(api.getRecommendations).toHaveBeenCalledWith(undefined)
    // They are of the last four weeks whatever a reading is for: there is no period to choose here, and it says so.
    expect(screen.queryByLabelText('Period')).not.toBeInTheDocument()
    expect(screen.getByText(/Made of the last 4 weeks' records, whatever period a reading is for/)).toBeInTheDocument()
    await pick('Site', 'Factory A')
    await waitFor(() => expect(api.getRecommendations).toHaveBeenLastCalledWith('s1'))
  })

  it('shows a manager\'s answer against what the recommendation said when it was answered', async () => {
    show()
    const recs = within(await advice()).getAllByTestId('recommendation')
    const answer = within(recs[1]).getByTestId('answer')
    expect(answer).toHaveTextContent('Not accepted by Siti Rahman')
    expect(answer).toHaveTextContent('She is booked on the November course.')
    expect(answer).toHaveTextContent("When that was said, it read: Mei Lin's pass in First aid lapses on 28 Sep 2026.")
    expect(within(recs[0]).queryByTestId('answer')).not.toBeInTheDocument()
    expect(within(recs[1]).getByRole('button', { name: 'Accept it now' })).toBeInTheDocument()
    const given = await screen.findAllByTestId('given', {}, PATIENT)
    expect(given[0]).toHaveTextContent('Not accepted by Siti Rahman')
    expect(given[0]).toHaveTextContent("Mei Lin · Mei Lin's pass in First aid lapses on 28 Sep 2026.")
  })

  it('accepts a recommendation, and does not take "not accepted" without a reason', async () => {
    show()
    const recs = within(await advice()).getAllByTestId('recommendation')
    fireEvent.click(within(recs[0]).getByRole('button', { name: 'Accept' }))
    await waitFor(() => expect(api.answerRecommendation).toHaveBeenCalledWith({ key: 'MISSED_TOURS:g1', answer: 'ACCEPTED', reason: null }))
    const cover = within(screen.getByTestId('coverage')).getByTestId('recommendation')
    fireEvent.click(await within(cover).findByRole('button', { name: 'Not accepted' }, PATIENT))
    const record = within(cover).getByRole('button', { name: 'Record it' })
    expect(record).toBeDisabled()
    fireEvent.change(within(cover).getByLabelText('Why it is not accepted'), { target: { value: '   ' } })
    expect(record).toBeDisabled()
    fireEvent.change(within(cover).getByLabelText('Why it is not accepted'), { target: { value: ' The night post opens in November. ' } })
    fireEvent.click(record)
    await waitFor(() => expect(api.answerRecommendation).toHaveBeenLastCalledWith({
      key: 'HOURS_COVER:s1:00', answer: 'NOT_ACCEPTED', reason: 'The night post opens in November.' }))
    // What was recommended and what was answered are read again.
    await waitFor(() => expect(vi.mocked(api.getAnswers).mock.calls.length).toBeGreaterThan(1))
  })

  it('offers no answering to somebody who may not, and gives the reason when one is refused', async () => {
    vi.mocked(api.getRecommendations).mockResolvedValue({
      ...ADVICE, can_answer: false, training: [rec({ may_answer: false })], coverage: [] })
    show()
    const training = await advice()
    expect(within(training).queryByRole('button')).not.toBeInTheDocument()
    expect(screen.getByTestId('coverage')).toHaveTextContent("Nothing is recommended for any site from the last 4 weeks' records.")
  })

  it('gives the reason when an answer is refused', async () => {
    vi.mocked(api.answerRecommendation).mockRejectedValue(refusal('That no longer stands. Look again before answering.'))
    show()
    const first = within(await advice()).getAllByTestId('recommendation')[0]
    fireEvent.click(within(first).getByRole('button', { name: 'Accept' }))
    expect(await within(first).findByText(/That no longer stands/, {}, PATIENT)).toBeInTheDocument()
  })

  it('says when no recommendation has been answered', async () => {
    vi.mocked(api.getAnswers).mockResolvedValue([])
    show()
    await advice()
    expect(await screen.findByText('No recommendation has been answered yet.', {}, PATIENT)).toBeInTheDocument()
  })
})

describe('whose reading somebody is shown', () => {
  it('shows a guard their own reading, whole, and nothing to answer', async () => {
    signIn(5)
    show(<MyReading />)
    const reading = await screen.findByTestId('reading', {}, PATIENT)
    expect(api.getMyReading).toHaveBeenCalledWith(28)
    expect(reading).toHaveTextContent(NOTE)
    expect(within(reading).getByTestId('section-RESPONSES')).toHaveTextContent('Half arrived within 8 minutes')
    expect(within(reading).getAllByTestId('recommendation')).toHaveLength(2)
    expect(within(reading).queryByRole('button', { name: 'Accept' })).not.toBeInTheDocument()
    expect(within(reading).queryByRole('button', { name: 'Not accepted' })).not.toBeInTheDocument()
    expect(screen.getByText(/It is yours to read; it is not an appraisal/)).toBeInTheDocument()
    await pick('Period', 'The last 90 days')
    await waitFor(() => expect(api.getMyReading).toHaveBeenLastCalledWith(90))
    // Nobody else's reading is asked for.
    expect(api.getReadings).not.toHaveBeenCalled()
    expect(api.getReading).not.toHaveBeenCalled()
  })

  it('shows a viewer no reading of their own, and a guard nobody else\'s', async () => {
    signIn(6)
    show(<MyReading />)
    expect(await screen.findByText('There is no reading of your own to show.', {}, PATIENT)).toBeInTheDocument()
    expect(api.getMyReading).not.toHaveBeenCalled()
    signIn(5)
    show()
    expect(await screen.findByText("Other people's readings are read by the people they answer to.", {}, PATIENT)).toBeInTheDocument()
    expect(screen.queryAllByRole('tab')).toHaveLength(0)
    expect(api.getReadings).not.toHaveBeenCalled()
    expect(api.getRecommendations).not.toHaveBeenCalled()
  })

  it('shows a supervisor the guards, the sites and the recommendations', async () => {
    signIn(3)
    show()
    expect((await screen.findAllByRole('tab', {}, PATIENT)).map((t) => t.textContent)).toEqual(['Guards', 'Sites', 'Recommendations'])
  })
})
