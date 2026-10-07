import type { ReactNode } from 'react'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ThemeProvider } from '@mui/material'
import { render, screen, fireEvent, waitFor, within } from '@/test/utils'
import { theme } from '@/theme/glassmorphism'
import { useAuthStore } from '@/store/auth'
import * as api from '@/api/sop'
import type { Asked, Library, Passage, Procedure, ProcedureDetail, Relevant, Version } from '@/api/sop'
import { ProcedureForIncident, ProcedureForSituation } from '@/components/sop/SopDialogs'
import { matched, source, standing, typedKinds, versionLine } from '@/components/sop/sopFormat'
import SopLibrary from './SopLibrary'

vi.mock('@/store/auth', () => ({ useAuthStore: vi.fn() }))
vi.mock('@/api/sites', () => ({ getSites: vi.fn().mockResolvedValue([
  { id: 's1', name: 'Factory A' }, { id: 's2', name: 'Factory B' }]) }))
// PageHeader reads the tenant's page names from the settings.
vi.mock('@/api/settings', () => ({ getSettings: vi.fn().mockResolvedValue([]), upsertSetting: vi.fn().mockResolvedValue({}) }))
vi.mock('@/api/sop', async (orig) => {
  const real = await orig<typeof import('@/api/sop')>()
  const fns = Object.fromEntries(Object.entries(real).map(([k, v]) => [k, typeof v === 'function' ? vi.fn() : v]))
  return { ...fns, apiError: real.apiError }
})

function show(el: ReactNode = <SopLibrary />) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } })
  return render(
    <MemoryRouter>
      <QueryClientProvider client={qc}><ThemeProvider theme={theme}>{el}</ThemeProvider></QueryClientProvider>
    </MemoryRouter>, { wrapper: ({ children }) => <>{children}</> })
}

const FIRE_TEXT = '## Evacuation\nOpen Gate 1 for the fire engines.\n\nDo not let anybody back in.'
const procedure = (over: Partial<Procedure>): Procedure => ({
  id: 'd1', code: 'SOP-0001', title: 'Fire alarm', category: 'fire', site_id: 's1', site_name: 'Factory A',
  state: 'IN_FORCE', is_retired: false, in_force_version_id: 'v1', in_force_version_no: 1,
  effective_from: '2026-10-01T00:00:00Z', effective_until: null, version_count: 1, incident_types: ['fire_smoke'],
  open_version: null, ...over })
const version = (over: Partial<Version>): Version => ({
  id: 'v1', document_id: 'd1', version_no: 1, body: FIRE_TEXT, change_note: null, state: 'APPROVED',
  drafted_by_name: 'Ahmad Faizal', drafted_at: '2026-09-28T00:00:00Z', submitted_at: '2026-09-29T00:00:00Z',
  decided_by_name: 'Lim Mei Ling', decided_at: '2026-10-01T00:00:00Z', decision_note: null,
  effective_from: '2026-10-01T00:00:00Z', effective_until: null, attachment_name: null, has_attachment: false,
  in_force: true, may: { edit: false, submit: false, withdraw: false, decide: false }, ...over })
const DRAFT = version({ id: 'v2', version_no: 2, state: 'DRAFT', in_force: false, decided_by_name: null, decided_at: null,
                        submitted_at: null, effective_from: null, body: FIRE_TEXT,
                        may: { edit: true, submit: true, withdraw: false, decide: false } })
const SUBMITTED = version({ ...DRAFT, state: 'SUBMITTED', change_note: 'Gate 3 as well.', submitted_at: '2026-10-06T00:00:00Z',
                            may: { edit: false, submit: false, withdraw: true, decide: true } })
const detail = (over: Partial<ProcedureDetail> = {}): ProcedureDetail => {
  const { version_count: _count, ...base } = procedure({})
  return { ...base, versions: [version({})], can_write: true, can_approve: true, ...over }
}
const LIBRARY: Library = {
  categories: ['general', 'emergency', 'fire', 'incident_response'], can_write: true, can_approve: true,
  items: [procedure({}),
          procedure({ id: 'd2', code: 'SOP-0002', title: 'Intruder', category: 'incident_response', site_id: null,
                      site_name: null, incident_types: ['intrusion', 'weapon'], in_force_version_no: 3,
                      open_version: { id: 'v9', version_no: 4, state: 'SUBMITTED' } }),
          procedure({ id: 'd3', code: 'SOP-0003', title: 'Bomb threat', state: 'NOT_YET_APPROVED', in_force_version_id: null,
                      in_force_version_no: null, incident_types: [], open_version: { id: 'v5', version_no: 1, state: 'DRAFT' } }),
          procedure({ id: 'd4', code: 'SOP-0004', title: 'Lift entrapment', state: 'EXPIRED', effective_until: '2026-09-01T00:00:00Z' })],
}
const passage = (over: Partial<Passage>): Passage => ({
  id: 'p1', heading: 'Evacuation', text: 'Open Gate 1 for the fire engines.', matched_words: ['evacuating', 'fire'],
  matched: 2, of: 2, procedure: { id: 'd1', code: 'SOP-0001', title: 'Fire alarm', category: 'fire', site_name: 'Factory A' },
  version: { id: 'v1', version_no: 1, approved_at: '2026-10-01T00:00:00Z', approved_by_name: 'Lim Mei Ling',
             effective_from: '2026-10-01T00:00:00Z', effective_until: null }, ...over })
const ASKED: Asked = {
  question: 'What do I do when evacuating for a fire?', words: ['evacuating', 'fire'], is_an_answer: false,
  note: 'These are passages of approved procedures in force, exactly as they were approved. Nothing here was written in answer to the question.',
  passages: [passage({}), passage({ id: 'p2', heading: null, text: 'Dial 995.', matched: 1, matched_words: ['fire'] })],
}
const refusal = (detail: unknown, status = 422) => Object.assign(new Error('x'), { response: { status, data: { detail } } })
const PATIENT = { timeout: 8000 }

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(useAuthStore).mockImplementation(((sel: (s: unknown) => unknown) =>
    sel({ user: { id: 'me', tenantId: 't1', roleId: 2 }, accessToken: 'tok', permissions: null })) as never)
  vi.mocked(api.getLibrary).mockResolvedValue(LIBRARY)
  vi.mocked(api.getProcedure).mockResolvedValue(detail())
  vi.mocked(api.askLibrary).mockResolvedValue(ASKED)
  vi.mocked(api.getIncidentTypes).mockResolvedValue(['fire_smoke', 'intrusion', 'weapon'])
  vi.mocked(api.writeProcedure).mockResolvedValue(detail({ id: 'd9' }))
  for (const fn of [api.approveVersion, api.rejectVersion, api.draftNextVersion, api.retireProcedure, api.restoreProcedure,
                    api.setIncidentTypes]) vi.mocked(fn).mockResolvedValue(detail())
  for (const fn of [api.changeVersion, api.submitVersion, api.withdrawVersion]) vi.mocked(fn).mockResolvedValue(version({}))
  vi.mocked(api.attachDocument).mockResolvedValue({ attachment_name: 'plan.pdf', attachment_sha256: 'ab', bytes: 3 })
})

const rows = () => screen.findAllByTestId('procedure-row', {}, PATIENT)
const open = async (index: number) => {
  fireEvent.click(within((await rows())[index]).getByRole('button', { name: 'Open' }))
  return screen.findByRole('dialog')
}

describe('The SOP library', () => {
  it('lists each procedure with what it is for and where it stands', async () => {
    show()
    const r = await rows()
    expect(api.getLibrary).toHaveBeenCalledWith({ q: undefined, category: undefined, state: undefined })
    expect(r[0]).toHaveTextContent('SOP-0001 · Fire alarm')
    expect(r[0]).toHaveTextContent('Factory A')
    expect(r[0]).toHaveTextContent('fire_smoke')
    expect(r[0]).toHaveTextContent('Version 1 in force')
    expect(r[1]).toHaveTextContent('Every site')
    expect(r[1]).toHaveTextContent('Version 3 in force · version 4 awaiting approval')
    expect(r[2]).toHaveTextContent('Not yet approved')
    expect(r[2]).toHaveTextContent('No version has been approved · version 1 being written')
    expect(r[3]).toHaveTextContent('Run out')
    expect(r[3]).toHaveTextContent(/Version 1 ran out .*: nothing is in force/)
  })

  it('is narrowed by words, category and standing', async () => {
    show()
    await rows()
    fireEvent.change(screen.getByLabelText('Title or code'), { target: { value: ' fire ' } })
    fireEvent.click(screen.getByRole('button', { name: 'Search' }))
    await waitFor(() => expect(api.getLibrary).toHaveBeenLastCalledWith({ q: 'fire', category: undefined, state: undefined }))
    fireEvent.mouseDown(screen.getByLabelText('Standing'))
    fireEvent.click(await screen.findByRole('option', { name: 'Awaiting approval' }))
    await waitFor(() => expect(api.getLibrary).toHaveBeenLastCalledWith({ q: 'fire', category: undefined, state: 'AWAITING' }))
    fireEvent.mouseDown(screen.getByLabelText('Category'))
    fireEvent.click(await screen.findByRole('option', { name: 'Incident response' }))
    await waitFor(() => expect(api.getLibrary).toHaveBeenLastCalledWith({ q: 'fire', category: 'incident_response', state: 'AWAITING' }))
  })

  it('asking shows the passages as approved, with where each is from, and says it is not an answer', async () => {
    show()
    await rows()
    const find = screen.getByRole('button', { name: 'Find it' })
    expect(find).toBeDisabled()
    fireEvent.change(screen.getByLabelText('What do you want the procedure for?'), {
      target: { value: '  What do I do when evacuating for a fire? ' } })
    fireEvent.click(find)
    await waitFor(() => expect(api.askLibrary).toHaveBeenCalledWith('What do I do when evacuating for a fire?'))
    const asked = await screen.findByTestId('asked')
    expect(within(asked).getByText(/Nothing here was written in answer to the question\./)).toBeInTheDocument()
    expect(within(asked).getByText('Looked for: evacuating, fire')).toBeInTheDocument()
    const passages = within(asked).getAllByTestId('passage')
    expect(passages[0]).toHaveTextContent('Evacuation')
    expect(passages[0]).toHaveTextContent('Open Gate 1 for the fire engines.')
    expect(passages[0]).toHaveTextContent(/SOP-0001 Fire alarm · version 1, approved by Lim Mei Ling, \S/)
    expect(passages[0]).toHaveTextContent('Uses 2 of the 2 words looked for: evacuating, fire')
    expect(passages[1]).toHaveTextContent('Uses 1 of the 2 words looked for: fire')
    fireEvent.click(within(passages[0]).getByRole('button', { name: 'Open the procedure' }))
    await waitFor(() => expect(api.getProcedure).toHaveBeenCalledWith('d1'))
  })

  it('says so when no procedure uses the words asked, and offers nothing in its place', async () => {
    vi.mocked(api.askLibrary).mockResolvedValue({ ...ASKED, words: ['helicopter'], passages: [],
      nothing: 'No approved procedure in force uses those words. Nothing nearer was looked for.' })
    show()
    await rows()
    fireEvent.change(screen.getByLabelText('What do you want the procedure for?'), { target: { value: 'helicopter' } })
    fireEvent.click(screen.getByRole('button', { name: 'Find it' }))
    expect(await screen.findByText('No approved procedure in force uses those words. Nothing nearer was looked for.')).toBeInTheDocument()
    expect(screen.queryByTestId('passage')).not.toBeInTheDocument()
  })

  it('a procedure is written as a draft, with the kinds of incident it is for', async () => {
    vi.mocked(api.writeProcedure).mockRejectedValueOnce(refusal("'fire smoke!' is not a kind of incident"))
    show()
    await rows()
    fireEvent.click(screen.getByRole('button', { name: 'Write a procedure' }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText(/once you submit it and somebody else approves it/)).toBeInTheDocument()
    const save = within(dialog).getByRole('button', { name: 'Save the draft' })
    expect(save).toBeDisabled()
    fireEvent.change(within(dialog).getByLabelText('Title'), { target: { value: ' Bomb threat ' } })
    fireEvent.change(within(dialog).getByLabelText('The procedure'), { target: { value: 'Stay calm.\n\nCall 999.' } })
    fireEvent.change(within(dialog).getByLabelText('Kinds of incident it is for'), { target: { value: 'Weapon, alarm  weapon' } })
    fireEvent.mouseDown(within(dialog).getByLabelText('Category'))
    fireEvent.click(await screen.findByRole('option', { name: 'Emergency' }))
    fireEvent.click(save)
    expect(await within(dialog).findByText("'fire smoke!' is not a kind of incident")).toBeInTheDocument()
    fireEvent.click(save)
    await waitFor(() => expect(api.writeProcedure).toHaveBeenLastCalledWith({
      title: 'Bomb threat', category: 'emergency', site_id: null, body: 'Stay calm.\n\nCall 999.',
      incident_types: ['alarm', 'weapon'] }))
  })

  it('the version in force is read word for word and offers nothing that changes it', async () => {
    show()
    const dialog = await open(0)
    const panel = await within(dialog).findByTestId('version-panel')
    expect(within(panel).getByTestId('version-text').textContent).toBe(FIRE_TEXT)
    expect(panel).toHaveTextContent('In force')
    expect(panel).toHaveTextContent(/drafted by Ahmad Faizal, .* approved by Lim Mei Ling, .* in force/)
    expect(within(panel).queryByLabelText('The procedure')).not.toBeInTheDocument()
    for (const name of ['Save', 'Submit for approval', 'Approve…', 'Reject…']) {
      expect(within(panel).queryByRole('button', { name })).not.toBeInTheDocument()
    }
    fireEvent.click(within(dialog).getByRole('button', { name: 'Draft the next version' }))
    await waitFor(() => expect(api.draftNextVersion).toHaveBeenCalledWith('d1'))
  })

  it('a draft is corrected and submitted, and what is on the screen is what is submitted', async () => {
    vi.mocked(api.getProcedure).mockResolvedValue(detail({ versions: [DRAFT, version({})],
                                                           open_version: { id: 'v2', version_no: 2, state: 'DRAFT' } }))
    show()
    const dialog = await open(0)
    const panel = await within(dialog).findByTestId('version-panel')
    expect(panel).toHaveTextContent('Version 2')                    // the one being written, not the one in force
    expect(within(panel).getByText(/approved by somebody other than who drafted it/)).toBeInTheDocument()
    expect(within(panel).getByRole('button', { name: 'Save' })).toBeDisabled()
    fireEvent.change(within(panel).getByLabelText('The procedure'), { target: { value: `${FIRE_TEXT}\n\nOpen Gate 3 too.` } })
    fireEvent.change(within(panel).getByLabelText('What is different from the version before'), { target: { value: ' Gate 3 as well. ' } })
    fireEvent.click(within(panel).getByRole('button', { name: 'Submit for approval' }))
    await waitFor(() => expect(api.submitVersion).toHaveBeenCalledWith('v2'))
    expect(api.changeVersion).toHaveBeenCalledWith('v2', { body: `${FIRE_TEXT}\n\nOpen Gate 3 too.`, change_note: 'Gate 3 as well.' })
    expect(vi.mocked(api.changeVersion).mock.invocationCallOrder[0])
      .toBeLessThan(vi.mocked(api.submitVersion).mock.invocationCallOrder[0])
    expect(within(dialog).queryByRole('button', { name: 'Draft the next version' })).not.toBeInTheDocument()   // one is open
    // The version it would replace can still be read.
    fireEvent.click(within(dialog).getByText('Version 1 · in force'))
    expect(await within(dialog).findByTestId('version-text')).toHaveTextContent('Open Gate 1 for the fire engines.')
  })

  it('a submitted version is approved from a date, or rejected with a reason', async () => {
    vi.mocked(api.getProcedure).mockResolvedValue(detail({ versions: [SUBMITTED, version({})],
                                                           open_version: { id: 'v2', version_no: 2, state: 'SUBMITTED' } }))
    show()
    const dialog = await open(0)
    const panel = await within(dialog).findByTestId('version-panel')
    expect(panel).toHaveTextContent('What changed: Gate 3 as well.')
    fireEvent.click(within(panel).getByRole('button', { name: 'Approve…' }))
    expect(within(panel).getByText(/It cannot be changed afterwards\./)).toBeInTheDocument()
    fireEvent.change(within(panel).getByLabelText('Runs out'), { target: { value: '2027-10-01T00:00' } })
    fireEvent.click(within(panel).getByRole('button', { name: 'Approve version 2' }))
    await waitFor(() => expect(api.approveVersion).toHaveBeenCalledWith('v2', {
      effective_from: null, effective_until: new Date('2027-10-01T00:00').toISOString() }))

    // (The server would now show it as approved. Here it still awaits a decision: think again, and reject it.)
    fireEvent.click(within(dialog).getByRole('button', { name: 'Not yet' }))
    fireEvent.click(await within(dialog).findByRole('button', { name: 'Reject…' }))
    const reject = within(dialog).getByRole('button', { name: 'Reject it' })
    expect(reject).toBeDisabled()
    fireEvent.change(within(dialog).getByLabelText('Why it is rejected'), { target: { value: ' The assembly point moved. ' } })
    fireEvent.click(reject)
    await waitFor(() => expect(api.rejectVersion).toHaveBeenCalledWith('v2', 'The assembly point moved.'))
  })

  it('whoever drafted a version is not offered to approve it, and a refusal is shown in the server’s words', async () => {
    const mine = { ...SUBMITTED, may: { edit: false, submit: false, withdraw: true, decide: false } }
    vi.mocked(api.getProcedure).mockResolvedValue(detail({ versions: [mine, version({})],
                                                           open_version: { id: 'v2', version_no: 2, state: 'SUBMITTED' } }))
    vi.mocked(api.withdrawVersion).mockRejectedValueOnce(refusal('Only a version awaiting a decision is withdrawn.', 409))
    show()
    const dialog = await open(0)
    const panel = await within(dialog).findByTestId('version-panel')
    expect(within(panel).queryByRole('button', { name: 'Approve…' })).not.toBeInTheDocument()
    fireEvent.click(within(panel).getByRole('button', { name: 'Withdraw to correct' }))
    expect(await within(panel).findByText('Only a version awaiting a decision is withdrawn.')).toBeInTheDocument()
  })

  it('the kinds of incident a procedure is for are set afresh, and it is retired, never removed', async () => {
    show()
    const dialog = await open(0)
    await within(dialog).findByTestId('version-panel')
    fireEvent.click(within(dialog).getByRole('button', { name: 'Change' }))
    fireEvent.change(within(dialog).getByLabelText('Kinds of incident, separated by commas'), { target: { value: 'fire_smoke, Alarm' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(api.setIncidentTypes).toHaveBeenCalledWith('d1', ['alarm', 'fire_smoke']))
    fireEvent.click(within(dialog).getByRole('button', { name: 'Retire' }))
    await waitFor(() => expect(api.retireProcedure).toHaveBeenCalledWith('d1'))
    expect(screen.queryByRole('button', { name: /delete|remove/i })).not.toBeInTheDocument()
  })

  it('the document as issued is attached to a draft', async () => {
    vi.mocked(api.getProcedure).mockResolvedValue(detail({ versions: [DRAFT, version({})],
                                                           open_version: { id: 'v2', version_no: 2, state: 'DRAFT' } }))
    show()
    const dialog = await open(0)
    const file = new File(['%PDF'], 'plan.pdf', { type: 'application/pdf' })
    fireEvent.change(await within(dialog).findByTestId('attach'), { target: { files: [file] } })
    await waitFor(() => expect(api.attachDocument).toHaveBeenCalledWith('v2', file))
  })

  it('somebody who only reads sees what is in force and nothing that writes or decides', async () => {
    vi.mocked(api.getLibrary).mockResolvedValue({ ...LIBRARY, can_write: false, can_approve: false, items: [procedure({})] })
    vi.mocked(api.getProcedure).mockResolvedValue(detail({ can_write: false, can_approve: false }))
    show()
    await rows()
    expect(screen.queryByRole('button', { name: 'Write a procedure' })).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Standing')).not.toBeInTheDocument()
    const dialog = await open(0)
    expect(await within(dialog).findByTestId('version-text')).toHaveTextContent('Open Gate 1 for the fire engines.')
    for (const name of ['Retire', 'Draft the next version', 'Change']) {
      expect(within(dialog).queryByRole('button', { name })).not.toBeInTheDocument()
    }
  })
})

describe('the procedure beside an incident', () => {
  const RELEVANT: Relevant = { incident_types: ['fire_smoke'], why_none: null, procedures: [{
    id: 'd1', code: 'SOP-0001', title: 'Fire alarm', category: 'fire', site_name: 'Factory A', for_types: ['fire_smoke'],
    version: { id: 'v1', version_no: 2, approved_at: '2026-10-01T00:00:00Z', approved_by_name: 'Lim Mei Ling', has_attachment: false },
    text: FIRE_TEXT, passages: [] }] }

  it('is shown word for word with the version and who approved it', async () => {
    vi.mocked(api.getProceduresForIncident).mockResolvedValue(RELEVANT)
    show(<ProcedureForIncident incidentId="i1" />)
    const shown = await screen.findByTestId('procedure-for')
    expect(api.getProceduresForIncident).toHaveBeenCalledWith('i1')
    expect(shown).toHaveTextContent('SOP-0001 · Fire alarm')
    expect(shown).toHaveTextContent('version 2, approved by Lim Mei Ling · Factory A')
    expect(shown).toHaveTextContent('Do not let anybody back in.')
  })

  it('says why there is none, and shows nothing at all to somebody who may not read procedures', async () => {
    vi.mocked(api.getProceduresForIncident).mockResolvedValue({ incident_types: [], procedures: [],
      why_none: 'This incident was raised by hand and has no kind: no procedure can be put beside it.' })
    const first = show(<ProcedureForIncident incidentId="i2" />)
    expect(await screen.findByText(/raised by hand and has no kind/)).toBeInTheDocument()
    first.unmount()
    vi.mocked(api.getProceduresForIncident).mockRejectedValue(refusal('Missing permission: sop:read', 403))
    show(<ProcedureForIncident incidentId="i3" />)
    await waitFor(() => expect(api.getProceduresForIncident).toHaveBeenCalledWith('i3'))
    expect(screen.queryByTestId('procedure-for')).not.toBeInTheDocument()
    expect(screen.queryByText(/Missing permission/)).not.toBeInTheDocument()
  })

  it('on a situation it is a card that is there only when there is a procedure to show', async () => {
    vi.mocked(api.getProceduresForSituation).mockResolvedValue({ ...RELEVANT, incident_types: ['fire_smoke', 'alarm'] })
    const first = show(<ProcedureForSituation situationId="sit1" />)
    const card = await screen.findByTestId('procedure-for-situation')
    expect(api.getProceduresForSituation).toHaveBeenCalledWith('sit1')
    expect(card).toHaveTextContent('what this situation is made of (fire_smoke, alarm), word for word')
    expect(card).toHaveTextContent("It is the organisation's procedure, not a recommendation.")
    expect(card).toHaveTextContent('SOP-0001 · Fire alarm')
    expect(card).toHaveTextContent('Do not let anybody back in.')
    first.unmount()
    vi.mocked(api.getProceduresForSituation).mockResolvedValue({ incident_types: ['camera'], procedures: [], why_none: 'None.' })
    show(<ProcedureForSituation situationId="sit2" />)
    await waitFor(() => expect(api.getProceduresForSituation).toHaveBeenCalledWith('sit2'))
    expect(screen.queryByTestId('procedure-for-situation')).not.toBeInTheDocument()
  })
})

describe('the words the SOP screens share', () => {
  it('where a procedure stands', () => {
    expect(standing(procedure({}))).toBe('Version 1 in force')
    expect(standing(procedure({ effective_until: '2027-01-01T00:00:00Z' }))).toMatch(/^Version 1 in force until /)
    expect(standing(procedure({ state: 'RETIRED' }))).toBe('Retired: not in force')
    expect(standing(procedure({ state: 'NOT_YET_APPROVED', open_version: { id: 'x', version_no: 1, state: 'SUBMITTED' } })))
      .toBe('No version has been approved · version 1 awaiting approval')
  })

  it('where a passage is from, and which words it uses', () => {
    expect(source(passage({}))).toMatch(/^SOP-0001 Fire alarm · version 1, approved by Lim Mei Ling, \S/)
    expect(source(passage({ version: { ...passage({}).version, approved_by_name: null } }))).toMatch(/version 1, approved, \S/)
    expect(matched(passage({ matched: 1, of: 1, matched_words: ['fire'] }))).toBe('Uses 1 of the 1 word looked for: fire')
  })

  it('a version in a line, and the kinds of incident as typed', () => {
    expect(versionLine(DRAFT)).toMatch(/^drafted by Ahmad Faizal, [^·]+$/)
    expect(versionLine(SUBMITTED)).toMatch(/· submitted /)
    expect(versionLine(version({ in_force: false }))).toMatch(/· replaced$/)
    expect(versionLine(version({ in_force: false, effective_from: '2999-01-01T00:00:00Z' }))).toMatch(/comes into force /)
    expect(versionLine(version({ state: 'REJECTED', decision_note: 'Out of date.', decided_by_name: null })))
      .toMatch(/rejected by somebody no longer on the system: Out of date\.$/)
    expect(typedKinds(' Weapon, alarm  weapon,,')).toEqual(['alarm', 'weapon'])
    expect(typedKinds('')).toEqual([])
  })
})
