import type { ReactNode } from 'react'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ThemeProvider } from '@mui/material'
import { render, screen, fireEvent, waitFor, within } from '@/test/utils'
import { theme } from '@/theme/glassmorphism'
import { useAuthStore } from '@/store/auth'
import * as api from '@/api/securityAdvice'
import type { Advice, Finding, GivenAnswer, PatternSource, Patterns } from '@/api/securityAdvice'
import { LEVEL_LABEL, answerLine, cellTitle, peak, step, stepRange, totalLine, weeksLabel } from '@/components/risk/riskFormat'
import RiskAdvice from './RiskAdvice'

vi.mock('@/store/auth', () => ({ useAuthStore: vi.fn() }))
vi.mock('@/api/sites', () => ({ getSites: vi.fn().mockResolvedValue([
  { id: 's1', name: 'Factory A' }, { id: 's2', name: 'Factory B' }]) }))
// PageHeader reads the tenant's page names from the settings.
vi.mock('@/api/settings', () => ({ getSettings: vi.fn().mockResolvedValue([]), upsertSetting: vi.fn().mockResolvedValue({}) }))
vi.mock('@/api/securityAdvice', async (orig) => {
  const real = await orig<typeof import('@/api/securityAdvice')>()
  const fns = Object.fromEntries(Object.entries(real).map(([k, v]) => [k, typeof v === 'function' ? vi.fn() : v]))
  return { ...fns, apiError: real.apiError }
})

function show(el: ReactNode = <RiskAdvice />) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } })
  return render(
    <MemoryRouter>
      <QueryClientProvider client={qc}><ThemeProvider theme={theme}>{el}</ThemeProvider></QueryClientProvider>
    </MemoryRouter>, { wrapper: ({ children }) => <>{children}</> })
}

const NOTE = 'Counts of what was recorded in the period, and where they gather. A pattern that recurred is not a forecast: nothing here says what will happen.'
const CONFIDENCE = 'Confidence says how much history a statement rests on. It is not a probability.'
const ONE_SITE = 'Advice is answered for one site. Choose the site it is about.'
const PERIOD = { weeks: 4, from: '2026-09-10T04:00:00Z', to: '2026-10-08T04:00:00Z', timezone: 'Asia/Singapore' }
const WEEKDAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
const quiet = () => Array.from({ length: 7 }, () => Array<number>(24).fill(0))
const busy = () => {
  const g = quiet()
  g[5][22] = 8          // Saturday, 22:00
  g[5][23] = 4
  g[2][15] = 1          // Wednesday, 15:00
  return g
}
const source = (over: Partial<PatternSource>): PatternSource => ({
  source: 'INCIDENT', label: 'Incidents', counted_from: 'Every incident, at the site of its camera, when it was opened.',
  total: 13, grid: busy(), by_week: [3, 3, 3, 4],
  places: [{ key: 'c1', name: 'Loading bay', count: 10, share: 77 }, { key: 'c2', name: 'Gate 1', count: 3, share: 23 }],
  cut_at: null, ...over })
const PATTERNS: Patterns = {
  period: PERIOD, site: { id: 's1', name: 'Factory A' }, weekdays: WEEKDAYS, band_hours: 4, note: NOTE, is_forecast: false,
  sources: [source({}),
            source({ source: 'ACCESS', label: 'Doors refused, forced or tampered with', total: 0, grid: quiet(),
                     by_week: [0, 0, 0, 0], places: [], counted_from: 'Door events recorded as denied, forced or tamper.' }),
            source({ source: 'DEVICE', label: 'Devices going down', total: 116, by_week: [26, 8, 2, 80],
                     places: [{ key: 'CAMERA:1', name: 'Car park', count: 21, share: 18 }] })],
}
const finding = (over: Partial<Finding>): Finding => ({
  key: 'RECURRING_HOURS:INCIDENT:s1:20', code: 'RECURRING_HOURS', source: 'INCIDENT', source_label: 'Incidents',
  statement: '92% of the incidents of the last 4 weeks fell between 20:00 and 00:00 (12 of 13).',
  consider: 'Whether those hours have the people, patrols and attention the rest of the day has.',
  rests_on: { weeks: 4, in_band: 12, of: 13 },
  confidence: { level: 'MEDIUM', why: 'Rests on 13 records over 4 weeks; the same held within 4 of those weeks.',
                records: 13, weeks: 4, held_in_weeks: 4 },
  is_advisory: true, is_forecast: false, answer: null, may_answer: true, ...over })
const ANSWERED = finding({
  key: 'RECURRING_PLACE:INCIDENT:s1:c1', code: 'RECURRING_PLACE',
  statement: 'Loading bay accounts for 77% of the incidents of the last 4 weeks (10 of 13).',
  consider: 'What is at that place, and whether what watches it or what is done there needs to change.',
  answer: { answer: 'NOT_ACCEPTED', reason: 'The bay is where every lorry is checked.', answered_at: '2026-10-07T02:00:00Z',
            answered_by_name: 'Siti Rahman', said_then: 'Loading bay accounts for 70% of the incidents of the last 4 weeks (7 of 10).' } })
const UNSURE = finding({
  key: 'FIRST_RECORDED:SLA:s1:RECENT', code: 'FIRST_RECORDED', source: 'SLA', source_label: 'Response clocks missed',
  statement: '5 missed clocks in the last 2 weeks; none were recorded in the 2 weeks before. Whether that is a change, or only when recording began, cannot be told from the counts.',
  consider: 'Whether anything was being recorded before.',
  confidence: { level: 'LOW', why: 'Rests on 5 records over 4 weeks.', records: 5, weeks: 4, held_in_weeks: null } })
const ADVICE: Advice = {
  period: PERIOD, site: { id: 's1', name: 'Factory A' }, findings: [finding({}), ANSWERED, UNSURE], counted: [],
  can_answer: true, answer_note: null, note: NOTE, confidence_note: CONFIDENCE, is_forecast: false,
}
const GIVEN: GivenAnswer = {
  id: 'x1', site_name: 'Factory A', source_label: 'Incidents',
  statement: 'Loading bay accounts for 70% of the incidents of the last 4 weeks (7 of 10).', confidence: 'MEDIUM',
  period_weeks: 4, answer: 'NOT_ACCEPTED', reason: 'The bay is where every lorry is checked.',
  answered_at: '2026-10-07T02:00:00Z', answered_by_name: 'Siti Rahman' }
const refusal = (detail: unknown, status = 422) => Object.assign(new Error('x'), { response: { status, data: { detail } } })
const PATIENT = { timeout: 8000 }

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(useAuthStore).mockImplementation(((sel: (s: unknown) => unknown) =>
    sel({ user: { id: 'me', tenantId: 't1', roleId: 2 }, accessToken: 'tok', permissions: null })) as never)
  vi.mocked(api.getAdvice).mockResolvedValue(ADVICE)
  vi.mocked(api.getPatterns).mockResolvedValue(PATTERNS)
  vi.mocked(api.getAnswers).mockResolvedValue([GIVEN])
  vi.mocked(api.answerAdvice).mockResolvedValue(finding({}))
})

const findings = () => screen.findAllByTestId('finding', {}, PATIENT)
const pick = async (label: string, option: string) => {
  fireEvent.mouseDown(screen.getByLabelText(label))
  fireEvent.click(await screen.findByRole('option', { name: option }))
}

describe('the words and the shading', () => {
  it('calls confidence what it is: how much history, never how likely', () => {
    expect(LEVEL_LABEL).toEqual({ HIGH: 'Rests on much history', MEDIUM: 'Rests on some history', LOW: 'Rests on little history' })
    for (const label of Object.values(LEVEL_LABEL)) expect(label).not.toMatch(/likely|probab|certain|sure|risk/i)
    expect([weeksLabel(1), weeksLabel(8)]).toEqual(['The last week', 'The last 8 weeks'])
  })

  it('shades an hour in four steps, and gives nothing no shade', () => {
    expect(peak(busy())).toBe(8)
    expect(peak(quiet())).toBe(0)
    expect([0, 1, 2, 3, 4, 6, 8].map((n) => step(n, 8))).toEqual([0, 1, 1, 2, 2, 3, 4])
    expect(step(5, 0)).toBe(0)
    // The key says in counts what each shade stands for, and leaves out a shade no count falls on.
    expect([1, 2, 3, 4].map((n) => stepRange(n, 8))).toEqual(['1–2', '3–4', '5–6', '7–8'])
    expect([1, 2, 3, 4].map((n) => stepRange(n, 2))).toEqual([null, '1', null, '2'])
    expect([1, 2, 3, 4].map((n) => stepRange(n, 1))).toEqual([null, null, null, '1'])
    for (const most of [1, 2, 3, 5, 8, 13]) {
      for (let count = 1; count <= most; count += 1) expect(stepRange(step(count, most), most)).not.toBeNull()
    }
  })

  it('says a cell, an answer and a total in words', () => {
    expect(cellTitle('Saturday', 22, 8)).toBe('Saturday 22:00–23:00: 8')
    expect(cellTitle('Sunday', 23, 0)).toBe('Sunday 23:00–00:00: 0')
    expect(answerLine(ANSWERED.answer!)).toMatch(/^Not accepted by Siti Rahman, .*: The bay is where every lorry is checked\.$/)
    expect(answerLine({ answer: 'ACCEPTED', reason: null, answered_at: '2026-10-07T02:00:00Z', answered_by_name: null, said_then: null }))
      .toMatch(/^Accepted by somebody no longer on the system, /)
    expect(totalLine(source({ total: 1 }))).toBe('1 in the period')
    expect(totalLine(source({ total: 1532 }))).toBe('1,532 in the period')
    expect(totalLine(source({ total: 20000, cut_at: 20000 }))).toBe('More than 20,000 — the first 20,000 are counted')
  })
})

describe('advice', () => {
  it('shows what stands out with what it rests on, and says it is not a forecast', async () => {
    show()
    const list = await findings()
    expect(list).toHaveLength(3)
    expect(list[0]).toHaveTextContent('92% of the incidents of the last 4 weeks fell between 20:00 and 00:00 (12 of 13).')
    expect(list[0]).toHaveTextContent('Rests on some history')
    expect(list[0]).toHaveTextContent('Rests on 13 records over 4 weeks; the same held within 4 of those weeks.')
    expect(list[0]).toHaveTextContent('To consider: Whether those hours have the people')
    expect(list[2]).toHaveTextContent('Rests on little history')
    expect(list[2]).toHaveTextContent('cannot be told from the counts')
    const card = screen.getByTestId('advice')
    expect(card).toHaveTextContent(NOTE)
    expect(card).toHaveTextContent(CONFIDENCE)
    expect(screen.getByText(/Counts over a period — not a forecast/)).toBeInTheDocument()
    // Nothing on the screen calls a count a likelihood.
    expect(card.textContent).not.toMatch(/likely|predict|expected to|chance of/i)
    expect(api.getAdvice).toHaveBeenCalledWith({ site_id: undefined, weeks: 4 })
  })

  it('shows a person\'s answer against what the advice said when it was answered', async () => {
    show()
    const list = await findings()
    const answer = within(list[1]).getByTestId('answer')
    expect(answer).toHaveTextContent('Not accepted by Siti Rahman')
    expect(answer).toHaveTextContent('The bay is where every lorry is checked.')
    expect(answer).toHaveTextContent('When that was said, it read: Loading bay accounts for 70% of the incidents')
    expect(within(list[0]).queryByTestId('answer')).not.toBeInTheDocument()
    // Somebody may change their mind: the button says it is a further answer.
    expect(within(list[1]).getByRole('button', { name: 'Accept it now' })).toBeInTheDocument()
  })

  it('accepts a piece of advice for the site that is chosen', async () => {
    show()
    await findings()
    await pick('Site', 'Factory A')
    await waitFor(() => expect(api.getAdvice).toHaveBeenLastCalledWith({ site_id: 's1', weeks: 4 }))
    fireEvent.click(within((await findings())[0]).getByRole('button', { name: 'Accept' }))
    await waitFor(() => expect(api.answerAdvice).toHaveBeenCalledWith({
      site_id: 's1', weeks: 4, key: 'RECURRING_HOURS:INCIDENT:s1:20', answer: 'ACCEPTED', reason: null }))
    // The advice and the answers are read again.
    await waitFor(() => expect(vi.mocked(api.getAnswers).mock.calls.length).toBeGreaterThan(2))
  })

  it('does not accept "not accepted" without a reason', async () => {
    show()
    await pick('Site', 'Factory A')
    const first = (await findings())[0]
    fireEvent.click(within(first).getByRole('button', { name: 'Not accepted' }))
    const record = within(first).getByRole('button', { name: 'Record it' })
    expect(record).toBeDisabled()
    fireEvent.change(within(first).getByLabelText('Why it is not accepted'), { target: { value: '   ' } })
    expect(record).toBeDisabled()
    fireEvent.change(within(first).getByLabelText('Why it is not accepted'), { target: { value: ' Night staffing is already doubled. ' } })
    fireEvent.click(record)
    await waitFor(() => expect(api.answerAdvice).toHaveBeenCalledWith({
      site_id: 's1', weeks: 4, key: 'RECURRING_HOURS:INCIDENT:s1:20', answer: 'NOT_ACCEPTED',
      reason: 'Night staffing is already doubled.' }))
  })

  it('offers no answering where the server does not, and says why for every site together', async () => {
    vi.mocked(api.getAdvice).mockResolvedValue({
      ...ADVICE, site: null, answer_note: ONE_SITE, findings: ADVICE.findings.map((f) => ({ ...f, may_answer: false })) })
    show()
    const list = await findings()
    for (const f of list) expect(within(f).queryByRole('button')).not.toBeInTheDocument()
    expect(screen.getByText(ONE_SITE)).toBeInTheDocument()
  })

  it('does not tell somebody who cannot answer to choose a site', async () => {
    vi.mocked(api.getAdvice).mockResolvedValue({
      ...ADVICE, site: null, answer_note: ONE_SITE, can_answer: false,
      findings: ADVICE.findings.map((f) => ({ ...f, may_answer: false })) })
    show()
    await findings()
    expect(screen.queryByText(ONE_SITE)).not.toBeInTheDocument()
  })

  it('says that nothing standing out is not nothing having happened', async () => {
    vi.mocked(api.getAdvice).mockResolvedValue({ ...ADVICE, findings: [] })
    show()
    expect(await screen.findByText(/That is not the same as nothing having happened/, {}, PATIENT)).toBeInTheDocument()
  })

  it('gives the server\'s reason when an answer is refused, and keeps what was typed', async () => {
    vi.mocked(api.answerAdvice).mockRejectedValue(refusal('That no longer stands for this site and period. Look again before answering.', 409))
    show()
    await pick('Site', 'Factory A')
    const first = (await findings())[0]
    fireEvent.click(within(first).getByRole('button', { name: 'Not accepted' }))
    fireEvent.change(within(first).getByLabelText('Why it is not accepted'), { target: { value: 'Already dealt with.' } })
    fireEvent.click(within(first).getByRole('button', { name: 'Record it' }))
    expect(await within(first).findByText(/no longer stands for this site and period/)).toBeInTheDocument()
    expect(within(first).getByLabelText('Why it is not accepted')).toHaveValue('Already dealt with.')
  })

  it('asks again for another period', async () => {
    show()
    await findings()
    await pick('Period', 'The last 8 weeks')
    await waitFor(() => expect(api.getAdvice).toHaveBeenLastCalledWith({ site_id: undefined, weeks: 8 }))
    expect(api.getPatterns).toHaveBeenLastCalledWith({ site_id: undefined, weeks: 8 })
  })
})

describe('where it gathers', () => {
  it('shows each kind with its count, and one kind as a week of hours', async () => {
    show()
    const chips = await screen.findAllByTestId('source-chip', {}, PATIENT)
    expect(chips.map((c) => c.textContent)).toEqual(['Incidents: 13', 'Doors refused, forced or tampered with: 0', 'Devices going down: 116'])
    const card = screen.getByTestId('patterns')
    expect(card).toHaveTextContent('Every incident, at the site of its camera, when it was opened. 13 in the period.')
    const grid = within(card).getByTestId('hour-grid')
    expect(grid).toHaveAccessibleName('Incidents: how many fell in each hour of each day')
    const cells = grid.querySelectorAll('td')
    expect(cells).toHaveLength(7 * 24)
    // Each cell says what it holds to somebody who cannot see its shade.
    expect(grid.querySelector('td[title="Saturday 22:00–23:00: 8"]')).toHaveAttribute('data-step', '4')
    expect(grid.querySelector('td[title="Saturday 23:00–00:00: 4"]')).toHaveAttribute('data-step', '2')
    expect(grid.querySelector('td[title="Wednesday 15:00–16:00: 1"]')).toHaveAttribute('data-step', '1')
    expect(grid.querySelector('td[title="Monday 00:00–01:00: 0"]')).toHaveAttribute('data-step', '0')
    expect(within(card).getByTestId('grid-key')).toHaveTextContent('In one hour of the week:1–23–45–67–8')
    expect(within(card).getAllByTestId('place').map((p) => p.textContent)).toEqual(['Loading bay — 10 (77%)', 'Gate 1 — 3 (23%)'])
    expect(within(card).getByTestId('weeks')).toHaveTextContent('3334')
  })

  it('shows the numbers in the cells when asked', async () => {
    show()
    const grid = await screen.findByTestId('hour-grid', {}, PATIENT)
    expect(grid.querySelector('td[title="Saturday 22:00–23:00: 8"]')).toHaveTextContent('')
    fireEvent.click(screen.getByLabelText('Show the numbers'))
    expect(grid.querySelector('td[title="Saturday 22:00–23:00: 8"]')).toHaveTextContent('8')
    expect(grid.querySelector('td[title="Monday 00:00–01:00: 0"]')).toHaveTextContent('')
  })

  it('changes to another kind, and says so when nothing of a kind was recorded', async () => {
    show()
    const chips = await screen.findAllByTestId('source-chip', {}, PATIENT)
    fireEvent.click(chips[1])
    const card = screen.getByTestId('patterns')
    expect(card).toHaveTextContent('Door events recorded as denied, forced or tamper. 0 in the period.')
    expect(within(card).getByTestId('grid-key')).toHaveTextContent('nothing was recorded')
    expect(card).toHaveTextContent('None recorded.')
    fireEvent.click(chips[2])
    expect(within(card).getByTestId('weeks')).toHaveTextContent('268280')
    expect(within(card).getAllByTestId('place')[0]).toHaveTextContent('Car park — 21 (18%)')
  })

  it('says so when the counts cannot be read', async () => {
    vi.mocked(api.getPatterns).mockRejectedValue(refusal('Site not found', 404))
    show()
    expect(await screen.findByText('Site not found', {}, PATIENT)).toBeInTheDocument()
  })
})

describe('answers given', () => {
  it('lists what was answered, by whom, and what the advice said then', async () => {
    show()
    const given = await screen.findAllByTestId('given', {}, PATIENT)
    expect(given[0]).toHaveTextContent('Not accepted by Siti Rahman')
    expect(given[0]).toHaveTextContent('The bay is where every lorry is checked.')
    expect(given[0]).toHaveTextContent('Factory A · Incidents · Loading bay accounts for 70% of the incidents')
  })

  it('says when none has been', async () => {
    vi.mocked(api.getAnswers).mockResolvedValue([])
    show()
    expect(await screen.findByText('No advice has been answered yet.', {}, PATIENT)).toBeInTheDocument()
  })
})
