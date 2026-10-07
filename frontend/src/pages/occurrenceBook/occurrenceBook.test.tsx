import type { ReactNode } from 'react'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ThemeProvider } from '@mui/material'
import { render, screen, fireEvent, waitFor, within } from '@/test/utils'
import { theme } from '@/theme/glassmorphism'
import { useAuthStore } from '@/store/auth'
import { getShifts } from '@/api/guards'
import * as api from '@/api/occurrenceBook'
import type { Entry, EntryDetail, Instruction, Kinds, ShiftSummary } from '@/api/occurrenceBook'
import { correctionMark, kindLabel, since, standsUntil, summaryState } from '@/components/occurrenceBook/bookFormat'
import OccurrenceBook from './OccurrenceBook'

vi.mock('@/store/auth', () => ({ useAuthStore: vi.fn() }))
vi.mock('@/api/sites', () => ({ getSites: vi.fn().mockResolvedValue([
  { id: 's1', name: 'Factory A' }, { id: 's2', name: 'Factory B' }]) }))
vi.mock('@/api/guards', () => ({ getShifts: vi.fn() }))
// PageHeader reads the tenant's page names from the settings.
vi.mock('@/api/settings', () => ({ getSettings: vi.fn().mockResolvedValue([]), upsertSetting: vi.fn().mockResolvedValue({}) }))
vi.mock('@/api/occurrenceBook', async (orig) => {
  const real = await orig<typeof import('@/api/occurrenceBook')>()
  const fns = Object.fromEntries(Object.entries(real).map(([k, v]) => [k, typeof v === 'function' ? vi.fn() : v]))
  return { ...fns, apiError: real.apiError }
})

function show(el: ReactNode = <OccurrenceBook />) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } })
  return render(
    <MemoryRouter>
      <QueryClientProvider client={qc}><ThemeProvider theme={theme}>{el}</ThemeProvider></QueryClientProvider>
    </MemoryRouter>, { wrapper: ({ children }) => <>{children}</> })
}

const KINDS: Kinds = {
  kinds: [{ key: 'general', label: 'General' }, { key: 'unusual_activity', label: 'Unusual activity' },
          { key: 'delivery', label: 'Delivery' }],
  review_states: ['unreviewed', 'noted', 'follow_up', 'closed'], can_write: true, can_review: true,
  can_read_handovers: true, can_manage_handovers: true,
}
const READER: Kinds = { ...KINDS, review_states: [], can_write: false, can_review: false, can_read_handovers: false,
                        can_manage_handovers: false }
const entry = (over: Partial<Entry>): Entry => ({
  id: 'e1', entry_type: 'general', body: 'All quiet on the north side.', severity: null, occurred_at: '2026-10-07T02:00:00Z',
  site_id: 's1', site_name: 'Factory A', shift_id: null, author_user_id: 'g1', author_name: 'Tan Wei Ming',
  corrects_entry_id: null, correction_reason: null, corrected_by_entry_id: null,
  review: { state: 'unreviewed', note: null, reviewed_at: null, reviewed_by_name: null }, mine: false, ...over })
const VAN = entry({ id: 'e2', entry_type: 'unusual_activity', severity: 'high', body: 'Van at the east fence for twenty minutes.' })
const LIGHT = entry({ id: 'e3', body: 'Fence light out by bay 4.',
                      review: { state: 'follow_up', note: 'Raise a defect.', reviewed_at: '2026-10-07T02:30:00Z',
                                reviewed_by_name: 'Lim Mei Ling' } })
const WRONG = entry({ id: 'e4', entry_type: 'delivery', body: 'Three pallets for Bay 4.', corrected_by_entry_id: 'e5',
                      review: { state: 'noted', note: null, reviewed_at: '2026-10-07T02:40:00Z', reviewed_by_name: 'Lim Mei Ling' } })
const RIGHT = entry({ id: 'e5', entry_type: 'delivery', body: 'Five pallets for Bay 4.', corrects_entry_id: 'e4',
                      correction_reason: 'Miscounted.' })
const MINE = entry({ id: 'e6', body: 'Walked the site with the client.', mine: true, author_name: 'Me' })
const page = (items: Entry[], over = {}) => ({ items, limit: 50, offset: 0, has_more: false, can_review: true, can_write: true, ...over })
const DETAIL: EntryDetail = {
  ...WRONG, corrects: null, corrected_by: [RIGHT],
  reviews: [{ id: 'r1', outcome: 'NOTED', note: null, reviewed_at: '2026-10-07T02:40:00Z', reviewed_by_name: 'Lim Mei Ling' }],
}
const instruction = (over: Partial<Instruction>): Instruction => ({
  id: 'n1', site_id: 's1', site_name: 'Factory A', body: 'Gate 3 stays locked until Friday.', issued_by_name: 'Lim Mei Ling',
  issued_at: '2026-10-05T02:00:00Z', expires_at: null, closed_at: null, closed_by_name: null, close_note: null,
  in_force: true, reads: 2, read_by_me: false, ...over })
const summary = (over: Partial<ShiftSummary>): ShiftSummary => ({
  id: 'm1', shift_id: 'sh1', site_name: 'Factory A', guard_name: 'Tan Wei Ming', period_start: '2026-10-06T23:00:00Z',
  period_end: '2026-10-07T11:00:00Z', drafted_text: 'Shift summary: Factory A\n\nOCCURRENCE BOOK\n3 entries: general 3.',
  final_text: 'Shift summary: Factory A\n\nOCCURRENCE BOOK\n3 entries: general 3.', method: 'TEMPLATE', state: 'DRAFT',
  drafted_by_name: 'Tan Wei Ming', drafted_at: '2026-10-07T10:50:00Z', confirmed_by_name: null, confirmed_at: null,
  handover_id: null, edited: false,
  note: 'Drafted by the platform from what was recorded during the shift. It counts and quotes; it does not interpret. Check it and correct it before you confirm it.',
  may: { edit: true, confirm: true, discard: true }, ...over })
const refusal = (detail: unknown, status = 422) => Object.assign(new Error('x'), { response: { status, data: { detail } } })

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(useAuthStore).mockImplementation(((sel: (s: unknown) => unknown) =>
    sel({ user: { id: 'me', tenantId: 't1', roleId: 3 }, accessToken: 'tok', permissions: null })) as never)
  vi.mocked(api.getKinds).mockResolvedValue(KINDS)
  vi.mocked(api.searchEntries).mockResolvedValue(page([VAN, entry({}), LIGHT, WRONG, RIGHT, MINE]))
  vi.mocked(api.getEntry).mockResolvedValue(DETAIL)
  vi.mocked(api.reviewEntry).mockResolvedValue(VAN)
  vi.mocked(api.reviewEntries).mockResolvedValue({ reviewed: ['e2', 'e1', 'e5'], left: [] })
  vi.mocked(api.correctEntry).mockResolvedValue(RIGHT)
  vi.mocked(api.listInstructions).mockResolvedValue({ can_issue: true, items: [
    instruction({}), instruction({ id: 'n2', site_name: 'Factory B', body: 'No hot work.', read_by_me: true, reads: 1,
                                   expires_at: '2026-10-09T10:00:00Z' })] })
  for (const fn of [api.issueInstruction, api.readInstruction, api.closeInstruction]) vi.mocked(fn).mockResolvedValue(instruction({}))
  vi.mocked(api.listSummaries).mockResolvedValue({ can_manage: true, items: [
    summary({}), summary({ id: 'm2', guard_name: 'Raj Kumar', state: 'CONFIRMED', confirmed_by_name: 'Raj Kumar',
                           confirmed_at: '2026-10-06T11:05:00Z', handover_id: 'h1', edited: true,
                           final_text: 'Corrected words.', may: { edit: false, confirm: false, discard: false } })] })
  vi.mocked(api.draftSummary).mockResolvedValue({ ...summary({ id: 'm3' }), drafted_again: false })
  vi.mocked(api.editSummary).mockResolvedValue(summary({}))
  vi.mocked(api.confirmSummary).mockResolvedValue(summary({ state: 'CONFIRMED' }))
  vi.mocked(api.discardSummary).mockResolvedValue({ state: 'DISCARDED' })
  vi.mocked(getShifts).mockResolvedValue([
    { id: 'sh1', guard_user_id: 'me', guard_name: 'Me', site_name: 'Factory A', scheduled_start: '2026-10-07T00:00:00Z',
      actual_start: '2026-10-07T00:02:00Z', actual_end: null, status: 'active' },
    { id: 'sh2', guard_user_id: 'g1', guard_name: 'Tan Wei Ming', site_name: 'Factory B', scheduled_start: '2026-10-06T12:00:00Z',
      actual_start: '2026-10-06T12:00:00Z', actual_end: '2026-10-07T00:00:00Z', status: 'completed' },
    { id: 'sh3', guard_user_id: 'g1', guard_name: 'Tan Wei Ming', site_name: 'Factory A', scheduled_start: '2026-10-08T00:00:00Z',
      actual_start: null, actual_end: null, status: 'scheduled' }])
})

// The page asks what kinds there are before it asks for the book: two round trips before the first row,
// which on a busy machine is more than the second a query waits by default.
const PATIENT = { timeout: 8000 }
const rows = () => screen.findAllByTestId('entry-row', {}, PATIENT)
const tab = async (name: string) => fireEvent.click(await screen.findByRole('tab', { name }, PATIENT))

describe('The book', () => {
  it('lists the entries with their kind, who wrote them, where their review stands and how they stand to each other', async () => {
    show()
    const r = await rows()
    expect(api.searchEntries).toHaveBeenCalledWith(expect.objectContaining({
      q: undefined, entry_type: undefined, site_id: undefined, review: undefined, limit: 50, offset: 0 }))
    expect(vi.mocked(api.searchEntries).mock.calls[0][0]?.date_from).toMatch(/^\d{4}-\d\d-\d\dT/)   // the last 24 hours
    expect(r[0]).toHaveTextContent('Unusual activity')
    expect(r[0]).toHaveTextContent('high')
    expect(r[0]).toHaveTextContent('Van at the east fence for twenty minutes.')
    expect(r[0]).toHaveTextContent('Tan Wei Ming')
    expect(r[0]).toHaveTextContent('Not reviewed')
    expect(r[2]).toHaveTextContent('To be followed up')
    expect(r[2]).toHaveTextContent('Raise a defect.')
    expect(r[3]).toHaveTextContent('Corrected by a later entry')
    expect(r[4]).toHaveTextContent('A correction')
  })

  it('is searched by words, kind and where the review stands', async () => {
    show()
    await rows()
    fireEvent.change(screen.getByLabelText('Words in an entry'), { target: { value: '  lorry ' } })
    fireEvent.click(screen.getByRole('button', { name: 'Search' }))
    await waitFor(() => expect(api.searchEntries).toHaveBeenLastCalledWith(expect.objectContaining({ q: 'lorry' })))
    fireEvent.mouseDown(screen.getByLabelText('Kind'))
    fireEvent.click(await screen.findByRole('option', { name: 'Delivery' }))
    await waitFor(() => expect(api.searchEntries).toHaveBeenLastCalledWith(expect.objectContaining({
      q: 'lorry', entry_type: ['delivery'] })))
    fireEvent.mouseDown(screen.getByLabelText('Review'))
    fireEvent.click(await screen.findByRole('option', { name: 'To be followed up' }))
    await waitFor(() => expect(api.searchEntries).toHaveBeenLastCalledWith(expect.objectContaining({ review: 'follow_up' })))
    fireEvent.mouseDown(screen.getByLabelText('Written'))
    fireEvent.click(await screen.findByRole('option', { name: 'Any time' }))
    await waitFor(() => expect(vi.mocked(api.searchEntries).mock.lastCall?.[0]?.date_from).toBeUndefined())
  })

  it('a reviewer notes an entry or says what is to be followed up, and never reviews their own', async () => {
    vi.mocked(api.reviewEntry).mockRejectedValueOnce(refusal('An entry is reviewed by somebody other than who wrote it.', 403))
    show()
    const r = await rows()
    expect(within(r[5]).queryByRole('button', { name: 'Review' })).not.toBeInTheDocument()   // their own entry
    expect(within(r[3]).queryByRole('button', { name: 'Review' })).not.toBeInTheDocument()   // already noted
    fireEvent.click(within(r[0]).getByRole('button', { name: 'Review' }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText(/Not by a client/)).toBeInTheDocument()
    const record = within(dialog).getByRole('button', { name: 'Record the review' })
    expect(record).toBeEnabled()                                    // noting needs no words
    fireEvent.mouseDown(within(dialog).getByLabelText('What you make of it'))
    fireEvent.click(await screen.findByRole('option', { name: 'To be followed up' }))
    expect(record).toBeDisabled()                                   // following up says what
    fireEvent.change(within(dialog).getByLabelText('What is to be done'), { target: { value: ' Check the CCTV. ' } })
    fireEvent.click(record)
    expect(await within(dialog).findByText('An entry is reviewed by somebody other than who wrote it.')).toBeInTheDocument()
    fireEvent.click(record)
    await waitFor(() => expect(api.reviewEntry).toHaveBeenLastCalledWith('e2', 'FOLLOW_UP', 'Check the CCTV.'))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })

  it('a follow-up is closed by saying what was done', async () => {
    show()
    fireEvent.click(within((await rows())[2]).getByRole('button', { name: 'Close follow-up' }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText(/to follow up: Raise a defect\./)).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: 'Record the review' })).toBeDisabled()
    fireEvent.change(within(dialog).getByLabelText('What was done'), { target: { value: 'Lamp replaced.' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Record the review' }))
    await waitFor(() => expect(api.reviewEntry).toHaveBeenCalledWith('e3', 'CLOSED', 'Lamp replaced.'))
  })

  it('a page of unreviewed entries is noted at once, leaving out the reviewer’s own', async () => {
    show()
    await rows()
    expect(screen.getByText('3 on this page have not been reviewed.')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Note all 3 as read' }))
    await waitFor(() => expect(api.reviewEntries).toHaveBeenCalledWith(['e2', 'e1', 'e5']))
    await waitFor(() => expect(api.searchEntries).toHaveBeenCalledTimes(2))
  })

  it('an entry is corrected by writing another, and the screen says both stay', async () => {
    show()
    fireEvent.click(within((await rows())[0]).getByRole('button', { name: 'Correct' }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText(/An entry is never changed\..*Both stay in the book\./)).toBeInTheDocument()
    const write = within(dialog).getByRole('button', { name: 'Write the correction' })
    expect(write).toBeDisabled()
    fireEvent.change(within(dialog).getByLabelText('Why it is being corrected'), { target: { value: 'It was a lorry.' } })
    expect(write).toBeDisabled()                                    // it says the same thing as before
    fireEvent.change(within(dialog).getByLabelText('What is right'), { target: { value: ' Lorry at the east fence. ' } })
    fireEvent.click(write)
    await waitFor(() => expect(api.correctEntry).toHaveBeenCalledWith('e2', 'Lorry at the east fence.', 'It was a lorry.'))
    expect(screen.queryByRole('button', { name: /edit|delete|remove/i })).not.toBeInTheDocument()
  })

  it('opening an entry shows what corrects it and every review of it', async () => {
    show()
    fireEvent.click(within((await rows())[3]).getByRole('button', { name: 'Open' }))
    const dialog = await screen.findByRole('dialog')
    expect(api.getEntry).toHaveBeenCalledWith('e4')
    expect(await within(dialog).findByTestId('corrected-by')).toHaveTextContent(
      /by Tan Wei Ming: “Five pallets for Bay 4\.”\. Why: Miscounted\./)
    expect(within(dialog).getByText('Three pallets for Bay 4.')).toBeInTheDocument()   // as it was written
    expect(within(dialog).getByTestId('review-line')).toHaveTextContent(/Lim Mei Ling · noted$/)
  })

  it('somebody who reads the book and does not keep it sees the entries and nothing of the reviews', async () => {
    vi.mocked(api.getKinds).mockResolvedValue(READER)
    vi.mocked(api.searchEntries).mockResolvedValue(page([{ ...VAN, review: null }], { can_review: false, can_write: false }))
    show()
    const r = await rows()
    expect(r[0]).toHaveTextContent('Van at the east fence for twenty minutes.')
    expect(screen.queryByText('Review')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Review')).not.toBeInTheDocument()
    for (const name of ['Review', 'Correct', /Note .* as read/]) {
      expect(screen.queryByRole('button', { name })).not.toBeInTheDocument()
    }
    expect(screen.getAllByRole('tab').map((t) => t.textContent)).toEqual(['The book'])
  })

  it('pages through the book, and says where entries are written when nothing matches', async () => {
    vi.mocked(api.searchEntries).mockResolvedValue(page([VAN], { has_more: true }))
    show()
    await rows()
    fireEvent.click(screen.getByRole('button', { name: 'Older' }))
    await waitFor(() => expect(api.searchEntries).toHaveBeenLastCalledWith(expect.objectContaining({ offset: 50 })))
    vi.mocked(api.searchEntries).mockResolvedValue(page([]))
    fireEvent.change(screen.getByLabelText('Words in an entry'), { target: { value: 'zebra' } })
    fireEvent.click(screen.getByRole('button', { name: 'Search' }))
    expect(await screen.findByText(/No entry matches\. Entries are written from Guard Ops, or on the phone\./)).toBeInTheDocument()
    expect(api.searchEntries).toHaveBeenLastCalledWith(expect.objectContaining({ q: 'zebra', offset: 0 }))
  })
})

describe('Instructions in force', () => {
  it('are listed with who issued them, how many have read them and until when they stand', async () => {
    show()
    await tab('Instructions in force')
    const items = await screen.findAllByTestId('instruction')
    expect(api.listInstructions).toHaveBeenCalledWith({ site_id: undefined, state: 'in_force' })
    expect(items[0]).toHaveTextContent('Gate 3 stays locked until Friday.')
    expect(items[0]).toHaveTextContent('Lim Mei Ling')
    expect(items[0]).toHaveTextContent('Read by 2 people')
    expect(items[0]).toHaveTextContent('In force until it is closed')
    expect(items[1]).toHaveTextContent('Read by 1 person')
    expect(items[1]).toHaveTextContent(/In force until \S/)
    expect(items[1]).not.toHaveTextContent('In force until it is closed')
    expect(within(items[1]).getByText('You have read it')).toBeInTheDocument()
    fireEvent.click(within(items[0]).getByRole('button', { name: 'I have read it' }))
    await waitFor(() => expect(api.readInstruction).toHaveBeenCalledWith('n1'))
  })

  it('one is issued for a site, with or without a date it runs out on', async () => {
    show()
    await tab('Instructions in force')
    fireEvent.click(await screen.findByRole('button', { name: 'Issue an instruction' }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText(/The guards on shift at the site are told now/)).toBeInTheDocument()
    const issue = within(dialog).getByRole('button', { name: 'Issue it' })
    expect(issue).toBeDisabled()
    fireEvent.mouseDown(within(dialog).getByLabelText('Site'))
    fireEvent.click(await screen.findByRole('option', { name: 'Factory B' }))
    fireEvent.change(within(dialog).getByLabelText('The instruction'), { target: { value: ' No hot work. ' } })
    fireEvent.click(issue)
    await waitFor(() => expect(api.issueInstruction).toHaveBeenCalledWith({ site_id: 's2', body: 'No hot work.', expires_at: null }))
  })

  it('one is closed with a reason, and the ended ones are kept', async () => {
    show()
    await tab('Instructions in force')
    fireEvent.click(within((await screen.findAllByTestId('instruction'))[0]).getByRole('button', { name: 'Close' }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByRole('button', { name: 'Close it' })).toBeDisabled()
    fireEvent.change(within(dialog).getByLabelText('Why it no longer stands'), { target: { value: 'The gate was repaired.' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Close it' }))
    await waitFor(() => expect(api.closeInstruction).toHaveBeenCalledWith('n1', 'The gate was repaired.'))
    vi.mocked(api.listInstructions).mockResolvedValue({ can_issue: true, items: [
      instruction({ in_force: false, closed_at: '2026-10-07T03:00:00Z', closed_by_name: 'Lim Mei Ling',
                    close_note: 'The gate was repaired.' })] })
    fireEvent.click(screen.getByRole('button', { name: 'Ended' }))
    await waitFor(() => expect(api.listInstructions).toHaveBeenLastCalledWith({ site_id: undefined, state: 'ended' }))
    const ended = await screen.findByText(/by Lim Mei Ling: The gate was repaired\./)
    expect(ended).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'I have read it' })).not.toBeInTheDocument()
  })

  it('somebody who may not issue one reads them and is offered nothing that changes them', async () => {
    vi.mocked(api.listInstructions).mockResolvedValue({ can_issue: false, items: [instruction({})] })
    show()
    await tab('Instructions in force')
    await screen.findAllByTestId('instruction')
    expect(screen.queryByRole('button', { name: 'Issue an instruction' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Close' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'I have read it' })).toBeInTheDocument()
  })
})

describe('Shift summaries', () => {
  it('are listed with where each stands and by whose hand', async () => {
    show()
    await tab('Shift summaries')
    const items = await screen.findAllByTestId('summary')
    expect(items[0]).toHaveTextContent('Factory A · Tan Wei Ming')
    expect(items[0]).toHaveTextContent('A draft, not yet confirmed')
    expect(within(items[0]).getByRole('button', { name: 'Read and correct' })).toBeInTheDocument()
    expect(items[1]).toHaveTextContent('Handed over')
    expect(items[1]).toHaveTextContent(/Confirmed by Raj Kumar, .* corrected before it was confirmed/)
    expect(within(items[1]).getByRole('button', { name: 'Read' })).toBeInTheDocument()
  })

  it('a draft is corrected and confirmed, and what is on the screen is what is confirmed', async () => {
    show()
    await tab('Shift summaries')
    fireEvent.click(within((await screen.findAllByTestId('summary'))[0]).getByRole('button', { name: 'Read and correct' }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText('Drafted by the platform, from fixed sentences')).toBeInTheDocument()
    expect(within(dialog).getByText(/It counts and quotes; it does not interpret/)).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: 'Save' })).toBeDisabled()
    const words = within(dialog).getByLabelText('The summary')
    fireEvent.change(words, { target: { value: `${summary({}).final_text}\n\nThe van's plate was SGX1234A.` } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Confirm it' }))
    await waitFor(() => expect(api.confirmSummary).toHaveBeenCalledWith('m1'))
    expect(api.editSummary).toHaveBeenCalledWith('m1', expect.stringMatching(/The van's plate was SGX1234A\.$/))
    // Saved first, then confirmed.
    expect(vi.mocked(api.editSummary).mock.invocationCallOrder[0])
      .toBeLessThan(vi.mocked(api.confirmSummary).mock.invocationCallOrder[0])
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })

  it('a confirmed summary is read, with what the platform drafted beside it, and cannot be changed', async () => {
    show()
    await tab('Shift summaries')
    fireEvent.click(within((await screen.findAllByTestId('summary'))[1]).getByRole('button', { name: 'Read' }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByTestId('summary-text')).toHaveTextContent('Corrected words.')
    expect(within(dialog).queryByLabelText('The summary')).not.toBeInTheDocument()
    for (const name of ['Confirm it', 'Save', 'Set it aside']) {
      expect(within(dialog).queryByRole('button', { name })).not.toBeInTheDocument()
    }
    fireEvent.click(within(dialog).getByRole('button', { name: 'Show it as the platform drafted it' }))
    expect(within(dialog).getByTestId('summary-text')).toHaveTextContent('3 entries: general 3.')
  })

  it('a draft is set aside, and a refusal is shown in the server’s words', async () => {
    vi.mocked(api.discardSummary).mockRejectedValueOnce(refusal('This summary has been confirmed. It stands as it is.', 409))
    show()
    await tab('Shift summaries')
    fireEvent.click(within((await screen.findAllByTestId('summary'))[0]).getByRole('button', { name: 'Read and correct' }))
    const dialog = await screen.findByRole('dialog')
    fireEvent.click(within(dialog).getByRole('button', { name: 'Set it aside' }))
    expect(await within(dialog).findByText('This summary has been confirmed. It stands as it is.')).toBeInTheDocument()
    fireEvent.click(within(dialog).getByRole('button', { name: 'Set it aside' }))
    await waitFor(() => expect(api.discardSummary).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })

  it('somebody who manages handovers drafts for any shift that has started; anybody else, for their own', async () => {
    show()
    await tab('Shift summaries')
    fireEvent.click(await screen.findByRole('button', { name: 'Draft a summary' }))
    let dialog = await screen.findByRole('dialog')
    fireEvent.mouseDown(await within(dialog).findByLabelText('Shift'))
    let options = (await screen.findAllByRole('option')).map((o) => o.textContent ?? '')
    expect(options).toHaveLength(2)                                  // not the one that has not started
    expect(options[0]).toMatch(/^Me · Factory A · .*\(still running\)$/)
    expect(options[1]).toMatch(/^Tan Wei Ming · Factory B/)
    fireEvent.click(screen.getAllByRole('option')[1])
    fireEvent.click(within(dialog).getByRole('button', { name: 'Draft it' }))
    await waitFor(() => expect(api.draftSummary).toHaveBeenCalledWith('sh2'))
    await waitFor(() => expect(screen.queryByText("Draft a shift's summary")).not.toBeInTheDocument())

    vi.mocked(api.getKinds).mockResolvedValue({ ...KINDS, can_manage_handovers: false, can_review: false })
    document.body.innerHTML = ''
    show()
    await tab('Shift summaries')
    fireEvent.click(await screen.findByRole('button', { name: 'Draft a summary' }))
    dialog = await screen.findByRole('dialog')
    fireEvent.mouseDown(await within(dialog).findByLabelText('Shift'))
    options = (await screen.findAllByRole('option')).map((o) => o.textContent ?? '')
    expect(options).toHaveLength(1)
    expect(options[0]).toMatch(/^Me · Factory A/)
  })
})

describe('the words the occurrence book screens share', () => {
  it('how an entry stands to another', () => {
    expect(correctionMark({ corrects_entry_id: null, corrected_by_entry_id: null })).toBeNull()
    expect(correctionMark({ corrects_entry_id: 'a', corrected_by_entry_id: null })).toBe('A correction')
    expect(correctionMark({ corrects_entry_id: null, corrected_by_entry_id: 'b' })).toBe('Corrected by a later entry')
    expect(correctionMark({ corrects_entry_id: 'a', corrected_by_entry_id: 'b' })).toBe('A correction, itself corrected')
    expect(kindLabel('unusual_activity')).toBe('Unusual activity')
  })

  it('until when an instruction stands, and where a summary stands', () => {
    expect(standsUntil(instruction({}))).toBe('In force until it is closed')
    expect(standsUntil(instruction({ in_force: false, expires_at: '2026-10-06T00:00:00Z' }))).toMatch(/^Ran out /)
    expect(standsUntil(instruction({ in_force: false, closed_at: '2026-10-06T00:00:00Z', closed_by_name: null, close_note: 'Done' })))
      .toMatch(/^Closed .*: Done$/)
    expect(summaryState(summary({ edited: true }))).toBe('A draft, corrected, not yet confirmed')
    expect(summaryState(summary({ state: 'CONFIRMED', confirmed_by_name: null, confirmed_at: '2026-10-07T11:00:00Z' })))
      .toMatch(/^Confirmed by somebody no longer on the system, .* as the platform drafted it$/)
  })

  it('a period is turned into the moment it starts', () => {
    const now = new Date('2026-10-07T03:00:00Z')
    expect(since('all', now)).toBeUndefined()
    expect(since('day', now)).toBe('2026-10-06T03:00:00.000Z')
    expect(since('week', now)).toBe('2026-09-30T03:00:00.000Z')
    expect(new Date(since('today', now)!).getTime()).toBeLessThanOrEqual(now.getTime())
    expect(api.apiError(refusal('Nothing here.', 404))).toBe('Nothing here.')
  })
})
