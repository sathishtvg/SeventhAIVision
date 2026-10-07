import type { ReactNode } from 'react'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ThemeProvider } from '@mui/material'
import { render, screen, fireEvent, waitFor, within } from '@/test/utils'
import { theme } from '@/theme/glassmorphism'
import { useAuthStore } from '@/store/auth'
import * as api from '@/api/visitorAuth'
import type { Authorisation, AuthorisationDetail, Listed, Movement, Options, ToReview } from '@/api/visitorAuth'
import { about, against, door, iso, local, who } from '@/components/visitorAuth/authFormat'
import VisitorAuthorisations from './VisitorAuthorisations'

vi.mock('@/store/auth', () => ({ useAuthStore: vi.fn() }))
vi.mock('@/api/sites', () => ({ getSites: vi.fn().mockResolvedValue([
  { id: 's1', name: 'Factory A' }, { id: 's2', name: 'Factory B' }]) }))
// PageHeader reads the tenant's page names from the settings.
vi.mock('@/api/settings', () => ({ getSettings: vi.fn().mockResolvedValue([]), upsertSetting: vi.fn().mockResolvedValue({}) }))
vi.mock('@/api/visitorAuth', async (orig) => {
  const real = await orig<typeof import('@/api/visitorAuth')>()
  const fns = Object.fromEntries(Object.entries(real).map(([k, v]) => [k, typeof v === 'function' ? vi.fn() : v]))
  return { ...fns, apiError: real.apiError }
})

function show(el: ReactNode = <VisitorAuthorisations />) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } })
  return render(
    <MemoryRouter>
      <QueryClientProvider client={qc}><ThemeProvider theme={theme}>{el}</ThemeProvider></QueryClientProvider>
    </MemoryRouter>, { wrapper: ({ children }) => <>{children}</> })
}

const NOBODY = { approve: false, decline: false, cancel: false, extend: false, places: false, escort: false,
                 id_seen: false, review_movements: false }
const auth = (over: Partial<Authorisation>): Authorisation => ({
  id: 'a1', site_id: 's1', site_name: 'Factory A', purpose: 'Lift servicing', state: 'APPROVED', standing: 'VALID',
  says: ['Approved by Tan Wei Ming. Valid until 17:00.', 'No escort is asked for.', 'Nobody has recorded seeing an ID.',
         'Authorised for: Block A.'],
  valid_from: '2026-10-07T01:00:00Z', valid_until: '2026-10-07T09:00:00Z', host_user_id: 'u1', host_name: 'Tan Wei Ming',
  escort_required: false, escort_user_id: null, escort_name: null, escort_note: null, id_document_kind: null,
  id_checked_at: null, id_checked_by_name: null, requested_by_name: 'Ong Bee Lian', requested_at: '2026-10-07T00:40:00Z',
  decided_by_name: 'Tan Wei Ming', decided_at: '2026-10-07T00:50:00Z', decision_note: null, extended_at: null,
  extended_by_name: null, extend_reason: null, cancelled_at: null, cancelled_by_name: null, cancel_reason: null,
  is_latest: true, subject: { kind: 'visit', id: 'v1', name: 'Lim Mei Ling', company: 'Acme Lifts', detail: 'walk_in',
                              status: 'arrived' },
  places: [{ id: 'p1', name: 'Block A', kind: 'BUILDING', part_of: null, is_active: true }],
  asked_of_me: false, asked_by_me: false, may: { ...NOBODY }, ...over })
const WAITING = auth({
  id: 'a2', state: 'REQUESTED', standing: 'AWAITING_HOST', decided_by_name: null, decided_at: null, asked_of_me: true,
  says: ['Asked of Tan Wei Ming at 08:40. Not yet answered.'], places: [],
  subject: { kind: 'visit', id: 'v2', name: 'Goh Kim Huat', company: null, detail: 'walk_in', status: 'pending' },
  may: { ...NOBODY, approve: true, decline: true, cancel: true, places: true, escort: true, id_seen: true } })
const PERMIT = auth({
  id: 'a3', state: 'REQUESTED', standing: 'AWAITING_HOST', host_user_id: null, host_name: null, site_name: 'Factory B',
  says: ['Asked of whoever manages visits at 08:40. Not yet answered.'], places: [], purpose: 'Chiller overhaul',
  subject: { kind: 'work_permit', id: 'w1', name: 'Coolair Services', company: 'Coolair Services',
             detail: 'Permit WP-0042: Chiller overhaul', status: 'approved', workers_count: 3 },
  may: { ...NOBODY, approve: true, decline: true, cancel: true } })
const DECLINED = auth({ id: 'a4', state: 'DECLINED', standing: 'DECLINED', says: ['Declined by Tan Wei Ming: Not expected today.'],
                        subject: { kind: 'visit', id: 'v4', name: 'Ravi Pillai', company: null, detail: 'delivery', status: 'pending' } })
const OLD = auth({ id: 'a5', standing: 'EXPIRED', is_latest: false, says: ['Approved by Siti Rahman. It ran out at 17:00.'] })
const LISTED: Listed = { items: [WAITING, PERMIT, auth({}), DECLINED], limit: 50, offset: 0, has_more: false,
                         can_ask: true, can_manage: true }
const NOTE = 'A door event outside the places or the period a visit is authorised for is something to look at, not a finding against anybody.'
const GATE = 'This is what stands on the record for this visit. It does not check anybody in and it does not refuse anybody.'
const move = (over: Partial<Movement>): Movement => ({
  authorization_id: 'a1', access_event_id: 'e1', occurred_at: '2026-10-07T03:10:00Z', event_type: 'granted',
  denial_reason: null, door_name: 'A2 east', badge: 'V-17', place_name: 'A2 east door', part_of: 'Level 2', within: true,
  in_period: true, why_not_known: null, to_look_at: false, review: null, ...over })
const OUTSIDE = move({ access_event_id: 'e2', door_name: 'B lobby', place_name: 'B lobby door', part_of: 'Block B',
                       within: false, to_look_at: true })
const UNMAPPED = move({ access_event_id: 'e3', door_name: 'Loading bay', place_name: null, part_of: null, within: null,
                        why_not_known: "This door is not a place on the site's map." })
const LOOKED = move({ access_event_id: 'e4', in_period: false, to_look_at: true,
                      review: { outcome: 'IN_ORDER', note: 'Badge handed in late.', reviewed_at: '2026-10-07T10:00:00Z',
                                reviewed_by_name: 'Siti Rahman' } })
const detail = (over: Partial<AuthorisationDetail> = {}): AuthorisationDetail => ({
  ...auth({ may: { ...NOBODY, cancel: true, extend: true, places: true, escort: true, id_seen: true, review_movements: true } }),
  note: GATE, movements: { available: true, why: null, badges: ['V-17'], note: NOTE,
                           items: [move({}), OUTSIDE, UNMAPPED, LOOKED] }, ...over })
const TO_REVIEW: ToReview = { days: 7, note: NOTE, items: [{ ...OUTSIDE, subject_name: 'Lim Mei Ling', site_name: 'Factory A',
                                                             valid_from: '2026-10-07T01:00:00Z', valid_until: '2026-10-07T09:00:00Z' }] }
const OPTIONS: Options = {
  visits: [{ id: 'v9', name: 'Ahmad Faizal', company: 'Otis', status: 'pending', expected_from: '2026-10-07T06:00:00Z',
             expected_until: '2026-10-07T10:00:00Z', host_user_id: 'u1', host_name: 'Tan Wei Ming', purpose: 'Lift audit',
             site_id: 's1' },
           { id: 'v8', name: 'Roving Auditor', company: null, status: 'pending', expected_from: null, expected_until: null,
             host_user_id: null, host_name: 'Mr Lee of Finance', purpose: null, site_id: null }],
  permits: [{ id: 'w2', name: 'Coolair Services', permit_number: 'WP-0042', work_description: 'Chiller overhaul',
              status: 'approved', start_at: '2026-10-07T00:00:00Z', end_at: '2026-10-09T10:00:00Z', workers_count: 3 }],
  places: [{ id: 'p1', name: 'Block A', kind: 'BUILDING', part_of: null }, { id: 'p2', name: 'Level 2', kind: 'FLOOR', part_of: 'Block A' }],
  people: [{ id: 'u1', name: 'Tan Wei Ming' }, { id: 'u3', name: 'Kumar Raj' }],
  id_kinds: ['Passport', 'Work pass', 'Other'],
}
const refusal = (detail_: unknown, status = 422) => Object.assign(new Error('x'), { response: { status, data: { detail: detail_ } } })
const PATIENT = { timeout: 8000 }

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(useAuthStore).mockImplementation(((sel: (s: unknown) => unknown) =>
    sel({ user: { id: 'me', tenantId: 't1', roleId: 2 }, accessToken: 'tok', permissions: null })) as never)
  vi.mocked(api.listAuthorisations).mockResolvedValue(LISTED)
  vi.mocked(api.waitingForMe).mockResolvedValue([WAITING, PERMIT])
  vi.mocked(api.movementsToReview).mockResolvedValue(TO_REVIEW)
  vi.mocked(api.getAuthorisation).mockResolvedValue(detail())
  vi.mocked(api.getOptions).mockResolvedValue(OPTIONS)
  for (const fn of [api.askForAuthorisation, api.approveAuthorisation, api.declineAuthorisation, api.cancelAuthorisation,
                    api.extendAuthorisation, api.setPlaces, api.setEscort, api.recordIdSeen]) vi.mocked(fn).mockResolvedValue(auth({}))
  vi.mocked(api.reviewMovement).mockResolvedValue(LOOKED)
})

const rows = () => screen.findAllByTestId('auth-row', {}, PATIENT)
const open = async (index: number) => {
  show()
  fireEvent.click(within((await rows())[index]).getByRole('button', { name: 'Open' }))
  const dialog = await screen.findByRole('dialog')
  await within(dialog).findByTestId('says', {}, PATIENT)
  return dialog
}
const pick = async (scope: HTMLElement, label: string, option: string) => {
  fireEvent.mouseDown(within(scope).getByLabelText(label))
  fireEvent.click(await screen.findByRole('option', { name: option }))
}

describe('the words', () => {
  it('names a visitor with their company and a contractor by the permit', () => {
    expect(who(auth({}))).toBe('Lim Mei Ling (Acme Lifts)')
    expect(who(WAITING)).toBe('Goh Kim Huat')
    expect(who(PERMIT)).toBe('Coolair Services — work permit')
    expect(about(PERMIT)).toBe('Permit WP-0042: Chiller overhaul · 3 workers')
    expect(about(auth({}))).toBe('Lift servicing')
  })

  it('says of a door event only what can be said, and calls nothing a finding', () => {
    expect(against(move({}))).toEqual({ text: 'Within what the visit is authorised for', tone: 'ok' })
    expect(against(OUTSIDE)).toEqual({ text: 'To look at: outside the places it is authorised for', tone: 'look' })
    expect(against(move({ within: false, in_period: false })).text)
      .toBe('To look at: outside the places it is authorised for, and outside the period it is authorised for')
    expect(against(move({ in_period: false })).tone).toBe('look')
    // What is not known is given in the server's own words, not as a guess either way.
    expect(against(UNMAPPED)).toEqual({ text: "This door is not a place on the site's map.", tone: 'unknown' })
    for (const m of [move({}), OUTSIDE, UNMAPPED, move({ within: false, in_period: false })]) {
      expect(against(m).text).not.toMatch(/unauthori|violat|breach|intru|suspect|trespass/i)
    }
    expect(door(OUTSIDE)).toBe('B lobby door, Block B')
    expect(door(UNMAPPED)).toBe('Loading bay')
  })

  it('turns a moment into what a date field holds and back', () => {
    expect(local(null)).toBe('')
    expect(iso('')).toBeNull()
    const held = local('2026-10-07T06:00:00Z')
    expect(held).toMatch(/^2026-10-0[67]T\d\d:\d\d$/)
    expect(iso(held)).toBe('2026-10-07T06:00:00.000Z')
  })
})

describe('the list', () => {
  it('shows what stands for each in the server\'s own sentence', async () => {
    show()
    const list = await rows()
    expect(list).toHaveLength(4)
    expect(list[0]).toHaveTextContent('Goh Kim Huat')
    expect(list[0]).toHaveTextContent('Waiting for an answer')
    expect(list[0]).toHaveTextContent('Asked of Tan Wei Ming at 08:40. Not yet answered.')
    expect(list[1]).toHaveTextContent('Coolair Services — work permit')
    expect(list[1]).toHaveTextContent('Asked of whoever manages visits')
    expect(list[2]).toHaveTextContent('Valid')
    expect(list[3]).toHaveTextContent('Declined by Tan Wei Ming: Not expected today.')
    expect(screen.getByText(/It informs the gate; it checks nobody in and refuses nobody/)).toBeInTheDocument()
  })

  it('is narrowed by site, standing, kind, words and whether earlier ones are wanted', async () => {
    show()
    await rows()
    expect(api.listAuthorisations).toHaveBeenLastCalledWith({
      site_id: undefined, standing: undefined, subject: undefined, q: undefined, history: undefined })
    fireEvent.change(screen.getByLabelText('Visitor, company or permit'), { target: { value: '  lim ' } })
    fireEvent.click(screen.getByRole('button', { name: 'Search' }))
    await pick(document.body, 'Standing', 'Never answered')
    await pick(document.body, 'For', 'Work permits')
    await pick(document.body, 'Site', 'Factory B')
    fireEvent.click(screen.getByLabelText('Earlier ones too'))
    await waitFor(() => expect(api.listAuthorisations).toHaveBeenLastCalledWith({
      site_id: 's2', standing: 'LAPSED', subject: 'work_permit', q: 'lim', history: true }))
  })

  it('says so when nothing matches, and offers asking only to somebody who may', async () => {
    vi.mocked(api.listAuthorisations).mockResolvedValue({ ...LISTED, items: [], can_ask: false, can_manage: false })
    vi.mocked(api.waitingForMe).mockResolvedValue([])
    show()
    expect(await screen.findByText('No authorisation matches.', {}, PATIENT)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Ask for an authorisation' })).not.toBeInTheDocument()
    expect(screen.queryByTestId('waiting')).not.toBeInTheDocument()
    // Where a visitor went is not fetched for somebody who does not manage visits.
    expect(api.movementsToReview).not.toHaveBeenCalled()
    expect(screen.queryByTestId('to-review')).not.toBeInTheDocument()
  })

  it('marks an authorisation that a newer one has replaced, and says when the list is cut', async () => {
    vi.mocked(api.listAuthorisations).mockResolvedValue({ ...LISTED, items: [auth({}), OLD], has_more: true })
    show()
    const list = await rows()
    expect(list[1]).toHaveStyle({ opacity: '0.6' })
    expect(screen.getByText(/The newest 50 are shown/)).toBeInTheDocument()
  })

  it('shows the server\'s refusal when the list cannot be read', async () => {
    vi.mocked(api.listAuthorisations).mockRejectedValue(refusal('Site not found', 404))
    show()
    expect(await screen.findByText('Site not found', {}, PATIENT)).toBeInTheDocument()
  })
})

describe('waiting for an answer', () => {
  it('lists what waits for this person and approves one where it stands', async () => {
    show()
    const waiting = await screen.findAllByTestId('waiting-row', {}, PATIENT)
    expect(waiting).toHaveLength(2)
    expect(waiting[0]).toHaveTextContent('Goh Kim Huat')
    expect(waiting[0]).not.toHaveTextContent('no host is named')
    expect(waiting[1]).toHaveTextContent('Coolair Services — work permit')
    expect(waiting[1]).toHaveTextContent('no host is named')
    fireEvent.click(within(waiting[0]).getByRole('button', { name: 'Approve' }))
    await waitFor(() => expect(api.approveAuthorisation).toHaveBeenCalledWith('a2'))
    // The lists are read again: it no longer waits.
    await waitFor(() => expect(vi.mocked(api.waitingForMe).mock.calls.length).toBeGreaterThan(1))
  })

  it('says why when the answer is refused', async () => {
    vi.mocked(api.approveAuthorisation).mockRejectedValue(refusal('The time it was asked for has passed. It has to be asked for again.', 409))
    show()
    const waiting = await screen.findAllByTestId('waiting-row', {}, PATIENT)
    fireEvent.click(within(waiting[0]).getByRole('button', { name: 'Approve' }))
    expect(await screen.findByText(/It has to be asked for again/)).toBeInTheDocument()
  })
})

describe('door events to look at', () => {
  it('lists them for somebody who manages visits, with the note that they are not findings', async () => {
    show()
    const card = await screen.findByTestId('to-review', {}, PATIENT)
    expect(card).toHaveTextContent('not a finding against anybody')
    const row = within(card).getByTestId('review-row')
    expect(row).toHaveTextContent('Lim Mei Ling — B lobby door, Block B')
    expect(row).toHaveTextContent('To look at: outside the places it is authorised for')
    fireEvent.click(within(row).getByRole('button', { name: 'Open the authorisation' }))
    await screen.findByRole('dialog')
    expect(api.getAuthorisation).toHaveBeenCalledWith('a1')
  })
})

describe('one authorisation', () => {
  it('shows what stands as the server gave it, and only the buttons the server allows', async () => {
    const dialog = await open(2)
    const says = within(dialog).getByTestId('says')
    for (const line of detail().says) expect(says).toHaveTextContent(line)
    expect(dialog).toHaveTextContent(GATE)
    expect(dialog).toHaveTextContent('Asked for by Ong Bee Lian')
    for (const name of ['Extend', 'Places', 'Escort', 'ID seen', 'Cancel it']) {
      expect(within(dialog).getByRole('button', { name })).toBeInTheDocument()
    }
    // It is already answered: the server does not offer the answer again, so neither does the screen.
    expect(within(dialog).queryByRole('button', { name: 'Approve' })).not.toBeInTheDocument()
    expect(within(dialog).queryByRole('button', { name: 'Decline' })).not.toBeInTheDocument()
  })

  it('offers nothing to somebody who may only read, and no movements', async () => {
    vi.mocked(api.getAuthorisation).mockResolvedValue(detail({ may: { ...NOBODY }, movements: null }))
    const dialog = await open(2)
    expect(within(dialog).getAllByRole('button').map((b) => b.textContent)).toEqual(['Close'])
    expect(dialog).not.toHaveTextContent("Where the visitor's badge was used")
  })

  it('approves, and declines only with a reason', async () => {
    vi.mocked(api.getAuthorisation).mockResolvedValue(detail({ ...WAITING, movements: null, note: GATE }))
    const dialog = await open(0)
    fireEvent.click(within(dialog).getByRole('button', { name: 'Approve' }))
    await waitFor(() => expect(api.approveAuthorisation).toHaveBeenCalledWith('a2'))
    fireEvent.click(within(dialog).getByRole('button', { name: 'Decline' }))
    const no = within(dialog).getByRole('button', { name: 'Decline it' })
    expect(no).toBeDisabled()
    fireEvent.change(within(dialog).getByLabelText('Why it is declined'), { target: { value: '  Not expected today. ' } })
    fireEvent.click(no)
    await waitFor(() => expect(api.declineAuthorisation).toHaveBeenCalledWith('a2', 'Not expected today.'))
  })

  it('cancels and extends with a reason', async () => {
    const dialog = await open(2)
    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel it' }))
    expect(within(dialog).getByRole('button', { name: 'Cancel the authorisation' })).toBeDisabled()
    fireEvent.click(within(dialog).getByRole('button', { name: 'Not yet' }))
    fireEvent.click(within(dialog).getByRole('button', { name: 'Extend' }))
    const go = within(dialog).getByRole('button', { name: 'Extend it' })
    expect(go).toBeDisabled()
    fireEvent.change(within(dialog).getByLabelText('Valid until'), { target: { value: '2026-10-08T18:00' } })
    fireEvent.change(within(dialog).getByLabelText('Why it is extended'), { target: { value: ' Part delayed. ' } })
    fireEvent.click(go)
    await waitFor(() => expect(api.extendAuthorisation).toHaveBeenCalledWith('a1', iso('2026-10-08T18:00'), 'Part delayed.'))
    await waitFor(() => expect(within(dialog).queryByLabelText('Why it is extended')).not.toBeInTheDocument())
    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel it' }))
    fireEvent.change(within(dialog).getByLabelText('Why it is cancelled'), { target: { value: 'Visit moved.' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel the authorisation' }))
    await waitFor(() => expect(api.cancelAuthorisation).toHaveBeenCalledWith('a1', 'Visit moved.'))
  })

  it('sets the places from the site\'s map, starting from the ones it has', async () => {
    const dialog = await open(2)
    fireEvent.click(within(dialog).getByRole('button', { name: 'Places' }))
    const block = await within(dialog).findByLabelText('Block A')
    expect(block).toBeChecked()
    expect(within(dialog).getByLabelText('Level 2 (Block A)')).not.toBeChecked()
    expect(dialog).toHaveTextContent('This changes what was approved')
    fireEvent.click(block)
    fireEvent.click(within(dialog).getByLabelText('Level 2 (Block A)'))
    fireEvent.click(within(dialog).getByRole('button', { name: 'Set the places' }))
    await waitFor(() => expect(api.setPlaces).toHaveBeenCalledWith('a1', ['p2']))
    expect(api.getOptions).toHaveBeenCalledWith('s1')
  })

  it('asks for an escort and names one, and drops the name when none is asked for', async () => {
    const dialog = await open(2)
    fireEvent.click(within(dialog).getByRole('button', { name: 'Escort' }))
    fireEvent.click(within(dialog).getByLabelText('To be escorted while on site'))
    await screen.findByRole('dialog')
    await waitFor(() => expect(within(dialog).getByRole('button', { name: 'Record it' })).toBeEnabled())
    await pick(dialog, 'Escort', 'Kumar Raj')
    fireEvent.change(within(dialog).getByLabelText('About the escort'), { target: { value: ' Stay with the engineer ' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Record it' }))
    await waitFor(() => expect(api.setEscort).toHaveBeenCalledWith('a1', {
      escort_required: true, escort_user_id: 'u3', escort_note: 'Stay with the engineer' }))
  })

  it('records the kind of ID that was seen and has nowhere to put its number', async () => {
    const dialog = await open(2)
    fireEvent.click(within(dialog).getByRole('button', { name: 'ID seen' }))
    expect(dialog).toHaveTextContent('Its number is not taken.')
    const seen = within(dialog).getByRole('button', { name: 'I saw it' })
    expect(seen).toBeDisabled()
    expect(within(dialog).queryByRole('textbox')).not.toBeInTheDocument()
    await waitFor(() => expect(api.getOptions).toHaveBeenCalled())
    await pick(dialog, 'The kind of document you saw', 'Work pass')
    fireEvent.click(seen)
    await waitFor(() => expect(api.recordIdSeen).toHaveBeenCalledWith('a1', 'Work pass'))
  })

  it('says why when a change is refused, and keeps what was typed', async () => {
    vi.mocked(api.cancelAuthorisation).mockRejectedValue(refusal('There is nothing standing to cancel.', 409))
    const dialog = await open(2)
    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel it' }))
    fireEvent.change(within(dialog).getByLabelText('Why it is cancelled'), { target: { value: 'Visit moved.' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel the authorisation' }))
    expect(await within(dialog).findByText('There is nothing standing to cancel.')).toBeInTheDocument()
    expect(within(dialog).getByLabelText('Why it is cancelled')).toHaveValue('Visit moved.')
  })
})

describe('where a badge was used', () => {
  it('sets each door event against what was authorised, and asks only about the ones to look at', async () => {
    const dialog = await open(2)
    expect(dialog).toHaveTextContent(NOTE)
    const events = within(dialog).getAllByTestId('movement')
    expect(events).toHaveLength(4)
    expect(events[0]).toHaveTextContent('A2 east door, Level 2')
    expect(events[0]).toHaveTextContent('Within what the visit is authorised for')
    expect(within(events[0]).queryByRole('button')).not.toBeInTheDocument()
    expect(events[1]).toHaveTextContent('To look at: outside the places it is authorised for')
    expect(within(events[1]).getByRole('button', { name: 'It was in order' })).toBeInTheDocument()
    expect(events[2]).toHaveTextContent("This door is not a place on the site's map.")
    expect(within(events[2]).queryByRole('button')).not.toBeInTheDocument()
    // What a person already made of one is shown, and is not asked again.
    expect(events[3]).toHaveTextContent('In order, by Siti Rahman: Badge handed in late.')
    expect(within(events[3]).queryByRole('button')).not.toBeInTheDocument()
  })

  it('records that one was in order, or how it was followed up', async () => {
    const dialog = await open(2)
    const event = within(dialog).getAllByTestId('movement')[1]
    fireEvent.click(within(event).getByRole('button', { name: 'It was in order' }))
    await waitFor(() => expect(api.reviewMovement).toHaveBeenCalledWith('a1', 'e2', { outcome: 'IN_ORDER', note: null }))
    fireEvent.click(within(event).getByRole('button', { name: 'It was followed up' }))
    const record = within(event).getByRole('button', { name: 'Record it' })
    expect(record).toBeDisabled()
    fireEvent.change(within(event).getByLabelText('What was done about it'), { target: { value: ' Host reminded of the hours. ' } })
    fireEvent.click(record)
    await waitFor(() => expect(api.reviewMovement).toHaveBeenLastCalledWith('a1', 'e2', {
      outcome: 'FOLLOWED_UP', note: 'Host reminded of the hours.' }))
  })

  it('says why there are none to show, in the server\'s words', async () => {
    vi.mocked(api.getAuthorisation).mockResolvedValue(detail({ movements: {
      available: false, why: 'No badge number was recorded when this visitor was checked in.', badges: [], items: [], note: NOTE } }))
    const dialog = await open(2)
    expect(dialog).toHaveTextContent('No badge number was recorded when this visitor was checked in.')
    expect(within(dialog).queryByTestId('movement')).not.toBeInTheDocument()
  })

  it('leaves the buttons off for somebody who may not say what was made of one', async () => {
    vi.mocked(api.getAuthorisation).mockResolvedValue(detail({ may: { ...NOBODY } }))
    const dialog = await open(2)
    expect(within(within(dialog).getAllByTestId('movement')[1]).queryByRole('button')).not.toBeInTheDocument()
  })
})

describe('asking', () => {
  const ask = async () => {
    show()
    await rows()
    fireEvent.click(screen.getByRole('button', { name: 'Ask for an authorisation' }))
    const dialog = await screen.findByRole('dialog')
    await pick(dialog, 'Site', 'Factory A')
    await waitFor(() => expect(api.getOptions).toHaveBeenCalledWith('s1'))
    return dialog
  }

  it('takes the host, the period and the purpose from the visit', async () => {
    const dialog = await ask()
    expect(dialog).toHaveTextContent('it checks nobody in and refuses nobody')
    await pick(dialog, 'Visit', 'Ahmad Faizal (Otis)')
    expect(within(dialog).getByLabelText('Purpose')).toHaveValue('Lift audit')
    expect(within(dialog).getByLabelText('Valid until')).toHaveValue(local('2026-10-07T10:00:00Z'))
    fireEvent.click(within(dialog).getByLabelText('Level 2 (Block A)'))
    fireEvent.click(within(dialog).getByRole('button', { name: 'Ask for it' }))
    await waitFor(() => expect(api.askForAuthorisation).toHaveBeenCalledWith({
      visitor_id: 'v9', host_user_id: 'u1', purpose: 'Lift audit', valid_from: '2026-10-07T06:00:00.000Z',
      valid_until: '2026-10-07T10:00:00.000Z', escort_required: false, escort_user_id: null, escort_note: null,
      place_ids: ['p2'] }))
    // It is then opened, so whoever asked sees what stands.
    await waitFor(() => expect(api.getAuthorisation).toHaveBeenCalledWith('a1'))
  })

  it('says the site for a visit that names none, and asks for the period', async () => {
    const dialog = await ask()
    await pick(dialog, 'Visit', 'Roving Auditor')
    expect(dialog).toHaveTextContent('The visit names “Mr Lee of Finance”, who is not one of the organisation\'s people on the system.')
    const go = within(dialog).getByRole('button', { name: 'Ask for it' })
    expect(go).toBeDisabled()
    fireEvent.change(within(dialog).getByLabelText('Valid until'), { target: { value: '2026-10-07T18:00' } })
    fireEvent.click(within(dialog).getByLabelText('To be escorted while on site'))
    await pick(dialog, 'Escort', 'Kumar Raj')
    fireEvent.click(go)
    await waitFor(() => expect(api.askForAuthorisation).toHaveBeenCalledWith({
      visitor_id: 'v8', site_id: 's1', host_user_id: null, purpose: null, valid_from: null,
      valid_until: iso('2026-10-07T18:00'), escort_required: true, escort_user_id: 'u3', escort_note: null, place_ids: [] }))
  })

  it('asks for a contractor\'s work permit', async () => {
    const dialog = await ask()
    await pick(dialog, 'For', "A contractor's work permit")
    await pick(dialog, 'Work permit', 'Coolair Services — permit WP-0042: Chiller overhaul')
    expect(within(dialog).getByLabelText('Purpose')).toHaveValue('Chiller overhaul')
    fireEvent.click(within(dialog).getByRole('button', { name: 'Ask for it' }))
    await waitFor(() => expect(api.askForAuthorisation).toHaveBeenCalledWith(expect.objectContaining({
      work_permit_id: 'w2', host_user_id: null, valid_until: '2026-10-09T10:00:00.000Z' })))
    expect(vi.mocked(api.askForAuthorisation).mock.calls[0][0]).not.toHaveProperty('visitor_id')
  })

  it('says what to do first when the site has nothing to ask about, and shows a refusal', async () => {
    vi.mocked(api.getOptions).mockResolvedValue({ ...OPTIONS, visits: [], permits: [] })
    const dialog = await ask()
    expect(await within(dialog).findByText(/Register the visitor first/)).toBeInTheDocument()
    await pick(dialog, 'For', "A contractor's work permit")
    expect(await within(dialog).findByText(/Raise the permit first/)).toBeInTheDocument()
  })

  it('keeps the form and gives the server\'s reason when asking is refused', async () => {
    vi.mocked(api.askForAuthorisation).mockRejectedValue(refusal('An authorisation of this visit is already waiting for an answer.', 409))
    const dialog = await ask()
    await pick(dialog, 'Visit', 'Ahmad Faizal (Otis)')
    fireEvent.click(within(dialog).getByRole('button', { name: 'Ask for it' }))
    expect(await within(dialog).findByText(/already waiting for an answer/)).toBeInTheDocument()
    expect(within(dialog).getByLabelText('Purpose')).toHaveValue('Lift audit')
  })
})
