import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ThemeProvider } from '@mui/material'
import { render, screen, fireEvent, waitFor, within } from '@/test/utils'
import { theme } from '@/theme/glassmorphism'
import { useAuthStore } from '@/store/auth'
import * as api from '@/api/cases'
import type { CaseFile, CaseList, CaseOptions, CaseRow, Link, Task } from '@/api/cases'
import { PRIORITY_LABEL, TASK_LABEL, entryLine, linkLine, named, standing, taskLine } from '@/components/cases/caseFormat'
import Cases from './Cases'

vi.mock('@/store/auth', () => ({ useAuthStore: vi.fn() }))
vi.mock('@/api/sites', () => ({ getSites: vi.fn().mockResolvedValue([
  { id: 's1', name: 'Factory A' }, { id: 's2', name: 'Factory B' }]) }))
// PageHeader reads the tenant's page names from the settings.
vi.mock('@/api/settings', () => ({ getSettings: vi.fn().mockResolvedValue([]), upsertSetting: vi.fn().mockResolvedValue({}) }))
vi.mock('@/api/cases', async (orig) => {
  const real = await orig<typeof import('@/api/cases')>()
  const fns = Object.fromEntries(Object.entries(real).map(([k, v]) => [k, typeof v === 'function' ? vi.fn() : v]))
  return { ...fns, apiError: real.apiError }
})

function show() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } })
  return render(
    <MemoryRouter>
      <QueryClientProvider client={qc}><ThemeProvider theme={theme}><Cases /></ThemeProvider></QueryClientProvider>
    </MemoryRouter>, { wrapper: ({ children }) => <>{children}</> })
}

const PARTY_NOTE = 'Being named in a case is not an accusation. A person or a vehicle is recorded with how it is connected, by whoever added it.'
const TWO = 'Closing a case takes two people: whoever asked for it to be closed does not approve it.'
const row = (over: Partial<CaseRow>): CaseRow => ({
  id: 'c1', case_number: 'CASE-0001', title: 'Forced gate, Factory A', category: 'TRESPASS', category_label: 'Trespass',
  priority: 'HIGH', status: 'OPEN', status_label: 'Open', site_id: 's1', site_name: 'Factory A', lead_name: 'Siti Rahman',
  opened_at: '2026-10-07T02:00:00Z', closed_at: null, tasks_open: 1, links: 3, ...over })
const LIST: CaseList = {
  items: [row({}), row({ id: 'c2', case_number: 'CASE-0002', title: 'Lost keys', category: 'THEFT', category_label: 'Theft or loss',
                         priority: 'NORMAL', status: 'CLOSED', status_label: 'Closed', site_id: null, site_name: null,
                         lead_name: null, tasks_open: 0, links: 0 })],
  total: 2, can_open: true, can_manage: true }
const task = (over: Partial<Task>): Task => ({
  id: 't1', title: 'Ask the night guard what they saw', detail: null, assigned_to_user_id: 'u2', assigned_to_name: 'Arun Kumar',
  due_at: null, state: 'OPEN', created_by_name: 'Siti Rahman', done_by_name: null, done_note: null, dropped_reason: null,
  may_finish: true, ...over })
const link = (over: Partial<Link>): Link => ({
  id: 'l1', kind: 'INCIDENT', kind_label: 'Incident', state: 'SHOWN', needs: 'incident:read', ref_id: 'i1', label: 'Forced gate',
  detail: 'high, open', note: 'What this case was opened from.', linked_at: '2026-10-07T02:00:00Z', linked_by_name: 'Siti Rahman', ...over })
const ALL = { work: true, assign: true, request_close: true, approve_close: false, decline_close: false, reopen: false }
const NONE = { work: false, assign: false, request_close: false, approve_close: false, decline_close: false, reopen: false }
const CASE: CaseFile = {
  id: 'c1', case_number: 'CASE-0001', title: 'Forced gate, Factory A', summary: 'The north gate was forced on 7 October.',
  category: 'TRESPASS', category_label: 'Trespass', priority: 'HIGH', status: 'OPEN', status_label: 'Open',
  site: { id: 's1', name: 'Factory A' }, lead_user_id: 'u1', lead_name: 'Siti Rahman', opened_at: '2026-10-07T02:00:00Z',
  opened_by_name: 'Siti Rahman', outcome: null, close_requested_at: null, close_requested_by_name: null, closed_at: null,
  closed_by_name: null, investigators: [{ user_id: 'u2', name: 'Arun Kumar' }],
  tasks: [task({}), task({ id: 't2', title: 'Check the fence', state: 'DROPPED', dropped_reason: "The fence is the landlord's.", may_finish: false })],
  tasks_open: 1,
  links: [link({}),
          link({ id: 'l2', kind: 'INVESTIGATION', kind_label: 'Investigation', state: 'NOT_PERMITTED', needs: 'investigation:read',
                 ref_id: null, label: null, detail: null, note: null }),
          link({ id: 'l3', kind: 'EVIDENCE_PACKAGE', kind_label: 'Evidence package', state: 'NOT_AVAILABLE', needs: 'evidence:package:read',
                 ref_id: null, label: null, detail: null, note: null })],
  parties: [{ id: 'p1', kind: 'PERSON', label: 'Tan Wei', connection: 'WITNESS', connection_label: 'Saw or heard it',
              note: 'Was on the night shift.', added_by_name: 'Siti Rahman' }],
  entries: [{ id: 'e1', kind: 'OPENED', words: 'Opened the case', body: null, occurred_at: '2026-10-07T02:00:00Z', actor_name: 'Siti Rahman' },
            { id: 'e2', kind: 'NOTE', words: 'Wrote a note', body: 'CCTV shows the gate forced at 02:14.', occurred_at: '2026-10-07T03:00:00Z', actor_name: 'Arun Kumar' }],
  on_case: true, may: ALL, party_note: PARTY_NOTE, two_people_note: TWO,
}
const OPTIONS: CaseOptions = {
  categories: [{ key: 'THEFT', label: 'Theft or loss' }, { key: 'TRESPASS', label: 'Trespass' }, { key: 'OTHER', label: 'Other' }],
  priorities: ['LOW', 'NORMAL', 'HIGH'],
  connections: [{ key: 'REPORTED_IT', label: 'Reported it' }, { key: 'WITNESS', label: 'Saw or heard it' }, { key: 'NAMED', label: 'Is named in it' }],
  link_kinds: [{ key: 'INCIDENT', label: 'Incident', needs: 'incident:read', may: true },
               { key: 'INVESTIGATION', label: 'Investigation', needs: 'investigation:read', may: false },
               { key: 'EVIDENCE_PACKAGE', label: 'Evidence package', needs: 'evidence:package:read', may: true }],
  people: [{ id: 'u1', name: 'Siti Rahman' }, { id: 'u2', name: 'Arun Kumar' }, { id: 'u3', name: 'Mei Lin' }],
  recent: { INCIDENT: [{ id: 'i1', label: 'Forced gate', detail: 'high, open' }, { id: 'i2', label: 'Fence cut', detail: 'medium, open' }],
            INVESTIGATION: [{ id: 'n1', label: 'INV-0001 — Who forced the gate', detail: 'open' }],
            EVIDENCE_PACKAGE: [{ id: 'k1', label: 'EP-0001 — Forced gate, 7 October', detail: 'draft' }] },
  party_note: PARTY_NOTE,
}
const refusal = (detail: unknown, status = 409) => Object.assign(new Error('x'), { response: { status, data: { detail } } })
const PATIENT = { timeout: 8000 }
const ACCUSING = /suspect|offender|culprit|perpetrator|accused|guilty|intruder/i

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(useAuthStore).mockImplementation(((sel: (s: unknown) => unknown) =>
    sel({ user: { id: 'me', tenantId: 't1', roleId: 2 }, accessToken: 'tok', permissions: null })) as never)
  vi.mocked(api.listCases).mockResolvedValue(LIST)
  vi.mocked(api.getCaseOptions).mockResolvedValue(OPTIONS)
  vi.mocked(api.getCase).mockResolvedValue(CASE)
  vi.mocked(api.downloadReport).mockResolvedValue(undefined)
  for (const fn of [api.openCase, api.changeCase, api.setLead, api.addInvestigator, api.removeInvestigator, api.addNote, api.addTask,
                    api.finishTask, api.addLink, api.removeLink, api.addParty, api.removeParty, api.requestClose, api.approveClose,
                    api.declineClose, api.reopenCase]) {
    vi.mocked(fn).mockResolvedValue(CASE)
  }
})

const openCase = async (index = 0) => {
  fireEvent.click((await screen.findAllByTestId('case-row', {}, PATIENT))[index])
  const view = await screen.findByTestId('case', {}, PATIENT)
  await within(view).findByTestId('standing', {}, PATIENT)
  return view
}
/** Write into one of the "something to write and a button" lines, and press its button. */
const write = async (scope: HTMLElement, label: string, said: string, button: string) => {
  fireEvent.change(await within(scope).findByLabelText(label, {}, PATIENT), { target: { value: said } })
  const press = within(scope).getByRole('button', { name: button })
  await waitFor(() => expect(press).toBeEnabled(), PATIENT)
  fireEvent.click(press)
}
/** A button once it can be pressed: while one step is being kept, the others wait. */
const ready = async (scope: HTMLElement, name: string) => {
  const button = await within(scope).findByRole('button', { name }, PATIENT)
  await waitFor(() => expect(button).toBeEnabled(), PATIENT)
  return button
}
const pick = async (scope: HTMLElement, label: string, option: string) => {
  fireEvent.mouseDown(within(scope).getByLabelText(label))
  fireEvent.click(await screen.findByRole('option', { name: option }))
}

describe('the words of a case', () => {
  it('says a linked record as the reader may see it, and nothing of one they may not read', () => {
    expect(linkLine(CASE.links[0])).toBe('Forced gate (high, open)')
    expect(linkLine(CASE.links[1])).toBe('An investigation you may not read — it is read under investigation:read')
    expect(linkLine(CASE.links[2])).toBe('An evidence package at a site you are not shown, or no longer there')
    expect(linkLine(link({ state: 'NOT_AVAILABLE', label: null }))).toBe('An incident at a site you are not shown, or no longer there')
  })

  it('says a task, a step and where a case stands', () => {
    expect(taskLine(CASE.tasks[0])).toBe('Ask the night guard what they saw — given to Arun Kumar')
    expect(taskLine(task({ assigned_to_name: null }))).toBe('Ask the night guard what they saw — given to nobody yet')
    expect(taskLine(task({ state: 'DONE', done_by_name: 'Arun Kumar', done_note: 'Saw a van leave.' }))).toBe(
      'Ask the night guard what they saw — done by Arun Kumar: Saw a van leave.')
    expect(taskLine(CASE.tasks[1])).toBe("Check the fence — dropped: The fence is the landlord's.")
    expect(entryLine(CASE.entries[0])).toMatch(/^Siti Rahman: opened the case, /)
    expect(entryLine({ ...CASE.entries[1], actor_name: null })).toMatch(/^Somebody no longer on the system: wrote a note, /)
    expect(standing(CASE)).toBe('Open, with 1 task to do.')
    expect(standing({ ...CASE, tasks_open: 0 })).toBe('Open.')
    expect(standing({ ...CASE, status: 'AWAITING_APPROVAL', close_requested_by_name: 'Siti Rahman', close_requested_at: '2026-10-08T02:00:00Z' }))
      .toMatch(/^Siti Rahman asked for it to be closed, .*\. Somebody else approves or declines\.$/)
    expect(standing({ ...CASE, status: 'CLOSED', closed_at: '2026-10-08T03:00:00Z', close_requested_by_name: 'Siti Rahman', closed_by_name: 'Mei Lin' }))
      .toMatch(/^Closed .*: asked for by Siti Rahman, approved by Mei Lin\.$/)
    expect(named(null)).toBe('Somebody no longer on the system')
    expect([TASK_LABEL.OPEN, PRIORITY_LABEL.HIGH]).toEqual(['To do', 'High priority'])
  })
})

describe('the list of cases', () => {
  it('lists the cases with where each stands, and offers opening one to whoever may', async () => {
    show()
    const table = await screen.findByTestId('cases-table', {}, PATIENT)
    expect(within(table).getAllByRole('columnheader').map((h) => h.textContent)).toEqual([
      'Case', 'Status', 'Kind', 'Site', 'Lead', 'Opened', 'Tasks to do', 'Linked records'])
    const rows = within(table).getAllByTestId('case-row')
    expect(rows[0]).toHaveTextContent('CASE-0001 — Forced gate, Factory A')
    expect(rows[0]).toHaveTextContent('High priority')
    expect(within(rows[0]).getAllByRole('cell').slice(1, 5).map((c) => c.textContent)).toEqual(['Open', 'Trespass', 'Factory A', 'Siti Rahman'])
    expect(within(rows[1]).getAllByRole('cell').slice(1, 5).map((c) => c.textContent)).toEqual(['Closed', 'Theft or loss', 'No one site', 'Nobody'])
    expect(screen.getByRole('button', { name: 'Open a case' })).toBeInTheDocument()
    expect(screen.getByText(/is worked by the people on it, and is closed by two/)).toBeInTheDocument()
    expect(api.listCases).toHaveBeenCalledWith({ status: undefined, mine: undefined })
  })

  it('narrows to a status and to one\'s own, and offers no opening to somebody who may not', async () => {
    vi.mocked(api.listCases).mockResolvedValue({ ...LIST, items: [], total: 0, can_open: false })
    show()
    expect(await screen.findByText('No case has been opened yet.', {}, PATIENT)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Open a case' })).not.toBeInTheDocument()
    fireEvent.mouseDown(screen.getByLabelText('Status'))
    fireEvent.click(await screen.findByRole('option', { name: 'Waiting for approval to close' }))
    await waitFor(() => expect(api.listCases).toHaveBeenLastCalledWith({ status: 'AWAITING_APPROVAL', mine: undefined }))
    fireEvent.click(screen.getByLabelText('Mine'))
    await waitFor(() => expect(api.listCases).toHaveBeenLastCalledWith({ status: 'AWAITING_APPROVAL', mine: true }))
    expect(await screen.findByText('No case matches.')).toBeInTheDocument()
  })

  it('opens a case from a record the reader may read, and then shows it', async () => {
    show()
    fireEvent.click(await screen.findByRole('button', { name: 'Open a case' }, PATIENT))
    const dialog = await screen.findByRole('dialog')
    const go = within(dialog).getByRole('button', { name: 'Open it' })
    expect(go).toBeDisabled()
    fireEvent.change(within(dialog).getByLabelText('Title'), { target: { value: ' Forced gate ' } })
    fireEvent.change(within(dialog).getByLabelText('What it is about'), { target: { value: ' The north gate was forced. ' } })
    fireEvent.mouseDown(within(dialog).getByLabelText('Opened from'))
    // An investigation is not offered: the reader may not read investigations.
    await waitFor(() => expect(screen.getAllByRole('option')).toHaveLength(4), PATIENT)
    expect(screen.getAllByRole('option').map((o) => o.textContent)).toEqual([
      'Nothing — a case by itself', 'Incident: Forced gate', 'Incident: Fence cut', 'Evidence package: EP-0001 — Forced gate, 7 October'])
    fireEvent.click(screen.getByRole('option', { name: 'Incident: Fence cut' }))
    await pick(dialog, 'Kind', 'Trespass')
    expect(within(dialog).getByText(/It stays where it is\./)).toBeInTheDocument()
    expect(within(dialog).getByText(/You lead a case you open/)).toBeInTheDocument()
    fireEvent.click(go)
    await waitFor(() => expect(api.openCase).toHaveBeenCalledWith({
      title: 'Forced gate', summary: 'The north gate was forced.', site_id: null, category: 'TRESPASS', priority: 'NORMAL',
      from_kind: 'INCIDENT', from_id: 'i2' }))
    expect(await screen.findByTestId('case', {}, PATIENT)).toBeInTheDocument()
    await waitFor(() => expect(api.getCase).toHaveBeenCalledWith('c1'))
  })

  it('gives the reason when a case cannot be opened', async () => {
    vi.mocked(api.openCase).mockRejectedValue(refusal('Choose the site this case is about: you are assigned to certain sites and cannot open one that spans them all.', 422))
    show()
    fireEvent.click(await screen.findByRole('button', { name: 'Open a case' }, PATIENT))
    const dialog = await screen.findByRole('dialog')
    fireEvent.change(within(dialog).getByLabelText('Title'), { target: { value: 'Keys' } })
    fireEvent.change(within(dialog).getByLabelText('What it is about'), { target: { value: 'A set of keys is missing.' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Open it' }))
    expect(await within(dialog).findByText(/Choose the site this case is about/)).toBeInTheDocument()
    expect(api.openCase).toHaveBeenCalledWith({ title: 'Keys', summary: 'A set of keys is missing.', site_id: null, category: 'OTHER', priority: 'NORMAL' })
  })
})

describe('a case', () => {
  it('shows what it is about, who is on it, its tasks, its links, who is named and its history', async () => {
    show()
    const view = await openCase()
    expect(screen.getByRole('dialog')).toHaveTextContent('CASE-0001 — Forced gate, Factory A')
    expect(within(view).getByTestId('standing')).toHaveTextContent('Open, with 1 task to do.')
    expect(view).toHaveTextContent('The north gate was forced on 7 October.')
    expect(within(view).getByTestId('people')).toHaveTextContent('Lead: Siti Rahman. Opened by Siti Rahman.')
    expect(within(view).getAllByTestId('investigator').map((i) => i.textContent)).toEqual(['Investigator: Arun KumarTake off'])
    expect(within(view).getAllByTestId('task').map((t) => t.textContent)).toEqual([
      'To doAsk the night guard what they saw — given to Arun KumarDoneDrop', "DroppedCheck the fence — dropped: The fence is the landlord's."])
    const links = within(view).getAllByTestId('link').map((l) => l.textContent)
    expect(links[0]).toBe('IncidentForced gate (high, open) — What this case was opened from.Take off')
    // A record the reader may not read is a kind and why — no title, no detail.
    expect(links[1]).toBe('InvestigationAn investigation you may not read — it is read under investigation:readTake off')
    expect(links[2]).toBe('Evidence packageAn evidence package at a site you are not shown, or no longer thereTake off')
    expect(within(view).getByTestId('links')).toHaveTextContent('Referred to, not copied: each stays where it is, under its own permission.')
    const parties = within(view).getByTestId('parties')
    expect(parties).toHaveTextContent(PARTY_NOTE)
    expect(within(parties).getByTestId('party')).toHaveTextContent('PersonTan Wei — saw or heard it. Was on the night shift.')
    const entries = within(view).getAllByTestId('entry')
    expect(entries[0].textContent).toMatch(/^Siti Rahman: opened the case, /)
    expect(entries[1]).toHaveTextContent('CCTV shows the gate forced at 02:14.')
    expect(within(view).getByTestId('closing')).toHaveTextContent(TWO)
    expect(within(view).getByTestId('closing')).toHaveTextContent('1 task is still to do: finish or drop it first.')
    expect(view.textContent).not.toMatch(ACCUSING)
    // How somebody is connected is chosen from the server's words; none of them accuses.
    fireEvent.mouseDown(within(parties).getByLabelText('How it is connected'))
    const offered = (await screen.findAllByRole('option')).map((o) => o.textContent)
    expect(offered).toEqual(['Reported it', 'Saw or heard it', 'Is named in it'])
    for (const word of offered) expect(word).not.toMatch(ACCUSING)
  })

  it('adds a note, a task, a link and a name, each as the server keeps it', async () => {
    show()
    const view = await openCase()
    await write(within(view).getByTestId('history'), 'A note', '  Asked the haulier.  ', 'Add the note')
    await waitFor(() => expect(api.addNote).toHaveBeenCalledWith('c1', 'Asked the haulier.'))
    const tasks = within(view).getByTestId('tasks')
    await pick(tasks, 'Given to', 'Mei Lin')
    await write(tasks, 'A task', 'Walk the fence', 'Add the task')
    await waitFor(() => expect(api.addTask).toHaveBeenCalledWith('c1', { title: 'Walk the fence', assigned_to_user_id: 'u3' }))
    const links = within(view).getByTestId('links')
    await pick(links, 'A record to link', 'Evidence package: EP-0001 — Forced gate, 7 October')
    fireEvent.click(within(links).getByRole('button', { name: 'Link it' }))
    await waitFor(() => expect(api.addLink).toHaveBeenCalledWith('c1', { kind: 'EVIDENCE_PACKAGE', ref_id: 'k1' }))
    const parties = within(view).getByTestId('parties')
    await pick(parties, 'A person or a vehicle', 'Vehicle')
    await pick(parties, 'How it is connected', 'Is named in it')
    await write(parties, 'Plate', 'SGX1234A', 'Add')
    await waitFor(() => expect(api.addParty).toHaveBeenCalledWith('c1', { kind: 'VEHICLE', label: 'SGX1234A', connection: 'NAMED' }))
  })

  it('finishes a task, and takes a task, a link and a name off only with why', async () => {
    show()
    const view = await openCase()
    const first = within(view).getAllByTestId('task')[0]
    fireEvent.click(within(first).getByRole('button', { name: 'Done' }))
    await waitFor(() => expect(api.finishTask).toHaveBeenCalledWith('c1', 't1', 'done', null))
    fireEvent.click(await ready(first, 'Drop'))
    expect(api.finishTask).toHaveBeenCalledTimes(1)
    await write(first, 'Why it is dropped', 'The guard has left.', 'Drop it')
    await waitFor(() => expect(api.finishTask).toHaveBeenLastCalledWith('c1', 't1', 'drop', 'The guard has left.'))
    const link = within(view).getAllByTestId('link')[0]
    fireEvent.click(await ready(link, 'Take off'))
    expect(await within(link).findByRole('button', { name: 'Take it off' })).toBeDisabled()
    expect(link).toHaveTextContent('The record itself is not touched.')
    await write(link, 'Why it is taken off the case', 'Wrong incident.', 'Take it off')
    await waitFor(() => expect(api.removeLink).toHaveBeenCalledWith('c1', 'l1', 'Wrong incident.'))
    const party = within(view).getByTestId('party')
    fireEvent.click(await ready(party, 'Take off'))
    await write(party, 'Why it is taken off the case', 'He was not on shift.', 'Take it off')
    await waitFor(() => expect(api.removeParty).toHaveBeenCalledWith('c1', 'p1', 'He was not on shift.'))
  })

  it('lets whoever manages cases say who is on it', async () => {
    show()
    const view = await openCase()
    const people = within(view).getByTestId('people')
    expect(within(people).getByRole('button', { name: 'Put on the case' })).toBeDisabled()
    await pick(people, 'Somebody', 'Mei Lin')
    fireEvent.click(within(people).getByRole('button', { name: 'Put on the case' }))
    await waitFor(() => expect(api.addInvestigator).toHaveBeenCalledWith('c1', 'u3'))
    fireEvent.click(await ready(people, 'Make lead'))
    await waitFor(() => expect(api.setLead).toHaveBeenCalledWith('c1', 'u3'))
    fireEvent.click(await ready(people, 'Take off'))
    await waitFor(() => expect(api.removeInvestigator).toHaveBeenCalledWith('c1', 'u2'))
  })

  it('shows a reader the case and nothing to do to it but take its report', async () => {
    vi.mocked(api.getCase).mockResolvedValue({ ...CASE, on_case: false, may: NONE, tasks: [task({ may_finish: false })] })
    show()
    const view = await openCase()
    expect(view).toHaveTextContent('You are not on this case. It is worked on by its lead and its investigators, or by whoever manages cases.')
    expect(within(view).queryAllByRole('button')).toHaveLength(0)
    expect(within(view).queryAllByRole('textbox')).toHaveLength(0)
    fireEvent.click(screen.getByRole('button', { name: 'The report, as a PDF' }))
    await waitFor(() => expect(api.downloadReport).toHaveBeenCalledWith('c1', 'CASE-0001'))
  })

  it('lets somebody finish a task they were given on a case they are not on', async () => {
    vi.mocked(api.getCase).mockResolvedValue({ ...CASE, on_case: false, may: NONE, tasks: [task({})] })
    show()
    const view = await openCase()
    expect(within(view).getAllByRole('button').map((b) => b.textContent)).toEqual(['Done', 'Drop'])
  })
})

describe('closing a case', () => {
  it('asks for it to be closed with what was found', async () => {
    vi.mocked(api.getCase).mockResolvedValue({ ...CASE, tasks_open: 0 })
    show()
    const closing = within(await openCase()).getByTestId('closing')
    expect(closing).toHaveTextContent('Somebody else approves.')
    expect(within(closing).getByRole('button', { name: 'Ask for it to be closed' })).toBeDisabled()
    await write(closing, 'What was found, and what was done', 'Forced by a delivery van. Gate repaired.', 'Ask for it to be closed')
    await waitFor(() => expect(api.requestClose).toHaveBeenCalledWith('c1', 'Forced by a delivery van. Gate repaired.'))
  })

  it('does not offer approving to whoever asked, and says why', async () => {
    const waiting: CaseFile = { ...CASE, status: 'AWAITING_APPROVAL', status_label: 'Waiting for approval to close',
                                outcome: 'Forced by a delivery van. Gate repaired.', close_requested_by_name: 'Siti Rahman',
                                close_requested_at: '2026-10-08T02:00:00Z', tasks_open: 0,
                                may: { ...NONE, decline_close: true } }
    vi.mocked(api.getCase).mockResolvedValue(waiting)
    show()
    const view = await openCase()
    expect(within(view).getByTestId('outcome')).toHaveTextContent('What was found: Forced by a delivery van. Gate repaired.')
    const closing = within(view).getByTestId('closing')
    expect(closing).toHaveTextContent('You asked for it to be closed, so somebody else approves. You may decline it.')
    expect(within(closing).queryByRole('button', { name: 'Approve its closing' })).not.toBeInTheDocument()
    expect(within(view).queryByLabelText('A note')).not.toBeInTheDocument()
    await write(closing, 'Why its closing is declined', 'Say whether the van was identified.', 'Decline')
    await waitFor(() => expect(api.declineClose).toHaveBeenCalledWith('c1', 'Say whether the van was identified.'))
  })

  it('lets somebody else approve it, and gives the reason when that is refused', async () => {
    vi.mocked(api.getCase).mockResolvedValue({ ...CASE, status: 'AWAITING_APPROVAL', status_label: 'Waiting for approval to close',
                                               outcome: 'Found.', tasks_open: 0, may: { ...NONE, approve_close: true, decline_close: true } })
    vi.mocked(api.approveClose).mockRejectedValue(refusal(TWO))
    show()
    const view = await openCase()
    fireEvent.click(within(view).getByRole('button', { name: 'Approve its closing' }))
    await waitFor(() => expect(api.approveClose).toHaveBeenCalledWith('c1'))
    expect(await within(view).findAllByText(TWO, {}, PATIENT)).toHaveLength(2)
  })

  it('shows a closed case as closed, and reopens it only with why', async () => {
    vi.mocked(api.getCase).mockResolvedValue({ ...CASE, status: 'CLOSED', status_label: 'Closed', outcome: 'Found.', tasks_open: 0,
                                               closed_at: '2026-10-08T03:00:00Z', closed_by_name: 'Mei Lin',
                                               close_requested_by_name: 'Siti Rahman', may: { ...NONE, reopen: true },
                                               tasks: [task({ state: 'DONE', may_finish: false, done_by_name: 'Arun Kumar' })] })
    show()
    const view = await openCase()
    expect(within(view).getByTestId('standing').textContent).toMatch(/asked for by Siti Rahman, approved by Mei Lin\.$/)
    for (const name of ['Add the note', 'Add the task', 'Link it', 'Add', 'Take off', 'Done', 'Ask for it to be closed']) {
      expect(within(view).queryByRole('button', { name })).not.toBeInTheDocument()
    }
    const closing = within(view).getByTestId('closing')
    expect(within(closing).getByRole('button', { name: 'Reopen it' })).toBeDisabled()
    await write(closing, 'Why it is reopened', 'The insurer asks who drove the van.', 'Reopen it')
    await waitFor(() => expect(api.reopenCase).toHaveBeenCalledWith('c1', 'The insurer asks who drove the van.'))
  })
})
