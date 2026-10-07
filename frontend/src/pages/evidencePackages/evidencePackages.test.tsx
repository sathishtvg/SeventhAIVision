import type { ReactNode } from 'react'
import { Route, Routes, MemoryRouter, useLocation } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ThemeProvider } from '@mui/material'
import { render, screen, fireEvent, waitFor, within } from '@/test/utils'
import { theme } from '@/theme/glassmorphism'
import { useAuthStore } from '@/store/auth'
import * as api from '@/api/evidencePackages'
import type { Candidates, Custody, Hold, PackageDetail, PackageItem, PackageListRow, Thing } from '@/api/evidencePackages'
import * as investigations from '@/api/investigations'
import { length, roleName, said, short, size } from '@/components/evidence/evidenceFormat'
import Investigation from '@/pages/investigations/Investigation'
import EvidencePackages from './EvidencePackages'
import EvidencePackage from './EvidencePackage'
import EvidenceHolds from './EvidenceHolds'

vi.mock('@/store/auth', () => ({ useAuthStore: vi.fn() }))
vi.mock('@/components/common/EvidenceThumb', () => ({
  EvidenceThumb: ({ frameEvidenceId }: { frameEvidenceId: string }) => <div data-testid="thumb">{frameEvidenceId}</div> }))
vi.mock('@/api/sites', () => ({ getSites: vi.fn().mockResolvedValue([{ id: 's1', name: 'Factory A' }]) }))
vi.mock('@/api/incidents', () => ({ getIncidents: vi.fn().mockResolvedValue({
  items: [{ id: 'inc1', title: 'Zone breach at the fence', created_at: '2026-10-05T17:10:00Z' }], has_more: false }) }))
// PageHeader reads the tenant's page names from the settings.
vi.mock('@/api/settings', () => ({ getSettings: vi.fn().mockResolvedValue([]), upsertSetting: vi.fn().mockResolvedValue({}) }))
vi.mock('@/api/investigations', async (orig) => {
  const real = await orig<typeof import('@/api/investigations')>()
  const fns = Object.fromEntries(Object.entries(real).map(([k, v]) => [k, typeof v === 'function' ? vi.fn() : v]))
  return { ...fns, apiError: real.apiError }
})
vi.mock('@/api/evidencePackages', async (orig) => {
  const real = await orig<typeof import('@/api/evidencePackages')>()
  const fns = Object.fromEntries(Object.entries(real).map(([k, v]) => [k, typeof v === 'function' ? vi.fn() : v]))
  return { ...fns, apiError: real.apiError }
})

const ADMIN = 2, SUPERVISOR = 3, OPERATOR = 4, GUARD = 5, VIEWER = 6
const SHA = (c: string) => c.repeat(64)

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

const thing = (over: Partial<Thing>): Thing => ({
  kind: 'SNAPSHOT', id: 'ev1', what: 'Frame at the detection', captured_at: '2026-10-05T17:12:40Z', media_type: 'image',
  site_id: 's1', site_name: 'Factory A', camera_id: 'c1', camera_name: 'North Gate', checksum_sha256: SHA('a'),
  kept: 'central', size_bytes: null, duration_seconds: null, needs: 'evidence:read', may_open: true,
  served_at: { path: '/api/v1/evidence/ev1/image', token_in_query: true }, ...over,
})
const FRAME = thing({ goes_with: { kind: 'PLATE_READ', id: 'p1' } })
const RECORDING = thing({ kind: 'RECORDING', id: 'rec1', what: 'Recording of North Gate', media_type: 'video',
                          checksum_sha256: SHA('b'), size_bytes: 64 * 1024 * 1024, duration_seconds: 3600,
                          needs: 'recording:read', offset_seconds: 780, goes_with: { kind: 'ALERT', id: 'a1' } })
const CLIP = thing({ kind: 'CLIP', id: 'ev2', what: 'Clip of the detection — kept with the incident', media_type: 'video',
                     checksum_sha256: null })

const item = (over: Partial<PackageItem>, of: Thing = FRAME): PackageItem => ({
  id: `item-${of.id}`, kind: of.kind, ref_id: of.id, captured_at: of.captured_at, site_id: 's1', camera_id: 'c1',
  checksum_sha256: of.checksum_sha256, note: null, added_by_user_id: 'u1', added_by_name: 'Priya Nair',
  added_at: '2026-10-05T18:05:00Z', state: 'SHOWN', thing: of, held: false, checksum_changed: false, ...over,
})

const ROW: PackageListRow = {
  id: 'pkg1', package_number: 'EVP-20261006-0001', title: 'Evidence of the lorry', purpose: 'For the client’s insurer.',
  status: 'DRAFT', site_id: 's1', site_name: 'Factory A', investigation_id: 'inv1',
  investigation_number: 'INV-20261006-0001', incident_id: null, created_by_user_id: 'u1', created_by_name: 'Priya Nair',
  created_at: '2026-10-05T18:00:00Z', sealed_by_user_id: null, sealed_by_name: null, sealed_at: null,
  manifest_sha256: null, updated_at: '2026-10-05T18:00:00Z', items: 2, holds_in_force: 0,
}

const DRAFT: PackageDetail = {
  ...ROW, intact: null, items: [item({ note: 'The lorry, as it came in.' }), item({}, RECORDING)],
  counts: { items: 2, held: 0, not_shown: 0, without_checksum: 0 },
  can: { manage: true, export: true, hold: true, custody: true }, max_items: 200,
}
const SEALED: PackageDetail = {
  ...DRAFT, status: 'SEALED', sealed_at: '2026-10-06T02:00:00Z', sealed_by_name: 'Tan Wei Ming',
  manifest_sha256: SHA('c'), intact: true, items: DRAFT.items.map((i) => ({ ...i, held: true })),
  counts: { items: 2, held: 2, not_shown: 0, without_checksum: 0 },
}
const OFFERED: Candidates = { records: 4, items: [CLIP], already_in: 2, not_looked_for: [] }

const step = (over: Partial<Custody['chain'][number]>): Custody['chain'][number] => ({
  step: 'CAPTURED', at: '2026-10-05T17:12:40Z', kind: 'SNAPSHOT', ref_id: 'ev1', actor_name: null, actor_role: null,
  reason: null, detail: {}, source: 'the item itself', ...over,
})
const CUSTODY: Custody = {
  package_number: 'EVP-20261006-0001', status: 'SEALED', manifest_sha256: SHA('c'),
  note: 'Shared and released are a person’s statement of what was done with an export: the platform sends nothing to anybody.',
  chain: [
    step({}),
    step({ step: 'COLLECTED', at: '2026-10-05T18:05:00Z', actor_name: 'Priya Nair', actor_role: 4 }),
    step({ step: 'ACCESSED', at: '2026-10-05T18:30:00Z', actor_name: 'Lim Wei', actor_role: 6,
           detail: { how: 'Opened', checksum_verified: null } }),
    step({ step: 'SEALED', at: '2026-10-06T02:00:00Z', kind: null, ref_id: null, actor_name: 'Tan Wei Ming',
           actor_role: 3, detail: { items: 2 } }),
    step({ step: 'EXPORTED', at: '2026-10-06T03:00:00Z', kind: null, ref_id: null, actor_name: 'Tan Wei Ming',
           actor_role: 3, reason: 'Requested by the insurer’s assessor.',
           detail: { included: 2, verified: 1, mismatched: 1, left_out: 0 } }),
    step({ step: 'RELEASED', at: '2026-10-06T04:00:00Z', kind: null, ref_id: null, actor_name: 'Aisha Rahman',
           actor_role: 8, reason: 'Claim 4471.', detail: { recipient: 'Acme Insurance', organisation: 'Claims' } }),
    step({ step: 'UNLOCKED', at: '2026-10-07T04:00:00Z', actor_name: null, actor_role: 2, reason: 'The claim is settled.' }),
  ],
}

const HOLD: Hold = {
  id: 'h1', kind: 'RECORDING', ref_id: 'rec1', site_id: 's1', site_name: 'Factory A', package_id: 'pkg1',
  package_number: 'EVP-20261006-0001', reason: 'Sealed in evidence package EVP-20261006-0001.',
  placed_by_name: 'Tan Wei Ming', placed_at: '2026-10-06T02:00:00Z', released_by_name: null, released_at: null,
  release_reason: null,
}

const EXPORT = { file: new Blob(['zip']), filename: 'EVP-20261006-0001.zip', included: 2, left_out: 0, verified: 2,
                 mismatched: 0, unverifiable: 0 }

beforeEach(() => {
  vi.clearAllMocks()
  asRole(SUPERVISOR)
  vi.mocked(api.listPackages).mockResolvedValue({ items: [ROW], total: 1, limit: 25, offset: 0, has_more: false })
  vi.mocked(api.getPackage).mockResolvedValue(DRAFT)
  vi.mocked(api.getCandidates).mockResolvedValue(OFFERED)
  vi.mocked(api.getCustody).mockResolvedValue(CUSTODY)
  vi.mocked(api.createPackage).mockResolvedValue({ id: 'pkg9', package_number: 'EVP-20261006-0009' })
  vi.mocked(api.addItems).mockResolvedValue({ added: [{ kind: 'CLIP', id: 'ev2' }], already_in: [] })
  vi.mocked(api.sealPackage).mockResolvedValue({ status: 'SEALED', manifest_sha256: SHA('c'), items: 2,
                                                 without_checksum: 0, holds: 2 })
  vi.mocked(api.exportPackage).mockResolvedValue(EXPORT)
  vi.mocked(api.downloadOriginal).mockResolvedValue(new Blob(['x']))
  vi.mocked(api.listHolds).mockResolvedValue({ items: [HOLD], total: 1, limit: 25, offset: 0, has_more: false })
  for (const fn of [api.removeItem, api.recordDisclosure, api.releaseHold]) vi.mocked(fn).mockResolvedValue({})
  vi.mocked(api.releasePackageHolds).mockResolvedValue({ released: 2 })
  vi.mocked(investigations.listInvestigations).mockResolvedValue({
    items: [{ id: 'inv1', investigation_number: 'INV-20261006-0001', title: 'Lorry at the back fence' } as never],
    total: 1, limit: 100, offset: 0, has_more: false })
})

const page = () => renderAt('/evidence-packages/pkg1', '/evidence-packages/:id', <EvidencePackage />)

describe('Evidence packages — the list', () => {
  const list = () => renderAt('/evidence-packages', '/evidence-packages', <EvidencePackages />)

  it('lists the packages, and a row opens one', async () => {
    list()
    expect(await screen.findByText('Evidence of the lorry')).toBeInTheDocument()
    expect(screen.getByText('EVP-20261006-0001')).toBeInTheDocument()
    expect(screen.getByText('INV-20261006-0001')).toBeInTheDocument()
    expect(screen.getByText('Draft')).toBeInTheDocument()
    fireEvent.click(screen.getByText('Evidence of the lorry'))
    expect(await screen.findByTestId('went-to')).toHaveTextContent('/evidence-packages/pkg1')
  })

  it('a package is made for an investigation, and says what it is for', async () => {
    list()
    fireEvent.click(await screen.findByRole('button', { name: 'Put evidence together' }))
    const dialog = await screen.findByRole('dialog')
    expect(dialog).toHaveTextContent('A package starts empty.')
    const create = within(dialog).getByRole('button', { name: 'Create' })
    fireEvent.change(within(dialog).getByLabelText('What it is'), { target: { value: 'Evidence of the lorry' } })
    fireEvent.change(within(dialog).getByLabelText('What it is for'), { target: { value: ' For the insurer. ' } })
    expect(create).toBeDisabled()
    fireEvent.mouseDown(within(dialog).getByLabelText('Investigation'))
    fireEvent.click(await screen.findByRole('option', { name: 'INV-20261006-0001 — Lorry at the back fence' }))
    fireEvent.click(create)
    await waitFor(() => expect(api.createPackage).toHaveBeenCalledWith({
      title: 'Evidence of the lorry', purpose: 'For the insurer.', investigation_id: 'inv1' }))
    expect(await screen.findByTestId('went-to')).toHaveTextContent('/evidence-packages/pkg9')
  })

  it('or for an incident', async () => {
    list()
    fireEvent.click(await screen.findByRole('button', { name: 'Put evidence together' }))
    const dialog = await screen.findByRole('dialog')
    fireEvent.change(within(dialog).getByLabelText('What it is'), { target: { value: 'Evidence of the breach' } })
    fireEvent.change(within(dialog).getByLabelText('What it is for'), { target: { value: 'For the police.' } })
    fireEvent.mouseDown(within(dialog).getByLabelText('Evidence of'))
    fireEvent.click(await screen.findByRole('option', { name: 'An incident' }))
    fireEvent.mouseDown(await within(dialog).findByLabelText('Incident'))
    fireEvent.click(await screen.findByRole('option', { name: /Zone breach at the fence/ }))
    fireEvent.click(within(dialog).getByRole('button', { name: 'Create' }))
    await waitFor(() => expect(api.createPackage).toHaveBeenCalledWith({
      title: 'Evidence of the breach', purpose: 'For the police.', incident_id: 'inc1' }))
  })

  it('a viewer sees the list and no way to make one', async () => {
    asRole(VIEWER)
    list()
    expect(await screen.findByText('Evidence of the lorry')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Put evidence together' })).not.toBeInTheDocument()
  })
})

describe('One evidence package — a draft', () => {
  it('says what it is for, that it is a draft, and what is in it', async () => {
    page()
    expect(await screen.findByText('For the client’s insurer.')).toBeInTheDocument()
    expect(screen.getByText(/It holds references to evidence, not copies/)).toBeInTheDocument()
    const items = screen.getAllByTestId('package-item')
    expect(items).toHaveLength(2)
    expect(items[0]).toHaveTextContent('Frame at the detection')
    expect(items[0]).toHaveTextContent('North Gate')
    expect(items[0]).toHaveTextContent(`SHA-256 ${'a'.repeat(10)}…${'a'.repeat(6)}`)
    expect(items[0]).toHaveTextContent('The lorry, as it came in.')
    expect(within(items[0]).getByTestId('thumb')).toHaveTextContent('ev1')
    expect(items[1]).toHaveTextContent('Recording of North Gate')
    expect(items[1]).toHaveTextContent('64.0 MB · 60 min')
    expect(within(items[1]).queryByTestId('thumb')).not.toBeInTheDocument()
    expect(screen.queryByTestId('seal')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Export' })).not.toBeInTheDocument()
  })

  it('offers what belongs and is not in it yet, and adds what is picked', async () => {
    page()
    const offered = await screen.findAllByTestId('candidate')
    expect(offered).toHaveLength(1)
    expect(offered[0]).toHaveTextContent('Clip of the detection — kept with the incident')
    expect(screen.getByText(/Found under the 4 records of the investigation/)).toBeInTheDocument()
    const add = screen.getByRole('button', { name: /to the package/ })
    expect(add).toBeDisabled()
    fireEvent.click(screen.getByLabelText('Pick Clip of the detection — kept with the incident'))
    fireEvent.click(screen.getByRole('button', { name: 'Add 1 to the package' }))
    await waitFor(() => expect(api.addItems).toHaveBeenCalledWith('pkg1', [CLIP]))
    await waitFor(() => expect(api.getPackage).toHaveBeenCalledTimes(2))
  })

  it('says when a kind of evidence was not looked for, and when nothing belongs', async () => {
    vi.mocked(api.getCandidates).mockResolvedValue({
      records: 1, items: [], already_in: 0, not_looked_for: ['You do not hold the permission drone:event:read.'] })
    page()
    expect(await screen.findByText(/Some kinds of evidence were not looked for. You do not hold the permission drone:event:read./))
      .toBeInTheDocument()
    expect(screen.getByText('The platform kept nothing that belongs to these records.')).toBeInTheDocument()
  })

  it('an item is taken out of a draft', async () => {
    page()
    const items = await screen.findAllByTestId('package-item')
    fireEvent.click(within(items[1]).getByText('Take out'))
    await waitFor(() => expect(api.removeItem).toHaveBeenCalledWith('pkg1', 'item-rec1'))
  })

  it('sealing says what it does before it does it, and shows the server’s refusal', async () => {
    vi.mocked(api.sealPackage).mockRejectedValueOnce({ response: { status: 409, data: { detail: {
      message: 'Some items can no longer be read by you — no longer held, at a site you are not assigned to, or of a kind you may not open.',
      items: [{ kind: 'CLIP', id: 'ev2' }] } } } })
    page()
    fireEvent.click(await screen.findByRole('button', { name: 'Seal' }))
    const dialog = await screen.findByRole('dialog')
    expect(dialog).toHaveTextContent('A sealed package cannot be changed by anybody')
    expect(dialog).toHaveTextContent('Every item is placed under a hold')
    fireEvent.click(within(dialog).getByRole('button', { name: 'Seal it' }))
    expect(await within(dialog).findByText(/Some items can no longer be read by you/)).toBeInTheDocument()
    fireEvent.click(within(dialog).getByRole('button', { name: 'Seal it' }))
    await waitFor(() => expect(api.sealPackage).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })

  it('an item that is gone, or not this reader’s to open, says so and shows nothing of itself', async () => {
    vi.mocked(api.getPackage).mockResolvedValue({
      ...DRAFT,
      items: [item({ state: 'NOT_AVAILABLE', thing: null }), item({ state: 'NOT_PERMITTED', thing: null }, RECORDING),
              item({ checksum_changed: true, checksum_sha256: null }, CLIP)],
      counts: { items: 3, held: 0, not_shown: 2, without_checksum: 1 } })
    page()
    const items = await screen.findAllByTestId('package-item')
    expect(items[0]).toHaveTextContent('No longer held by the platform')
    expect(items[1]).toHaveTextContent('Evidence of a kind you may not open')
    expect(items[2]).toHaveTextContent('SHA-256 none recorded')
    expect(items[2]).toHaveTextContent('The platform now records a different checksum')
    expect(screen.getByText(/2 items are not shown to you/)).toBeInTheDocument()
    expect(screen.getByText(/1 item has no checksum recorded. It can be exported and cannot be verified./)).toBeInTheDocument()
    expect(screen.queryAllByTestId('thumb')).toHaveLength(0)
  })

  it('somebody who may only read is offered nothing that changes it', async () => {
    asRole(VIEWER)
    vi.mocked(api.getPackage).mockResolvedValue({
      ...DRAFT, can: { manage: false, export: false, hold: false, custody: false } })
    page()
    await screen.findAllByTestId('package-item')
    expect(api.getCandidates).not.toHaveBeenCalled()
    expect(api.getCustody).not.toHaveBeenCalled()
    for (const name of ['Seal', 'Export', 'Record who it went to', 'Lift the holds']) {
      expect(screen.queryByRole('button', { name })).not.toBeInTheDocument()
    }
    expect(screen.queryByText('Take out')).not.toBeInTheDocument()
    expect(screen.queryByText('Chain of custody')).not.toBeInTheDocument()
  })
})

describe('One evidence package — sealed', () => {
  // A block, not an expression: a function returned from beforeEach is run as its clean-up.
  beforeEach(() => { vi.mocked(api.getPackage).mockResolvedValue(SEALED) })

  it('shows the hash it was sealed with, that it is intact, and offers nothing that would change it', async () => {
    page()
    const seal = await screen.findByTestId('seal')
    expect(seal).toHaveTextContent('The manifest has the hash it was sealed with.')
    expect(seal).toHaveTextContent(`SHA-256 ${'c'.repeat(64)}`)
    expect(screen.getByText('2 held')).toBeInTheDocument()
    expect(api.getCandidates).not.toHaveBeenCalled()
    expect(screen.queryByRole('button', { name: 'Seal' })).not.toBeInTheDocument()
    expect(screen.queryByText('Take out')).not.toBeInTheDocument()
    expect(screen.queryByTestId('candidate')).not.toBeInTheDocument()
  })

  it('says so, loudly, when the manifest no longer matches its hash', async () => {
    vi.mocked(api.getPackage).mockResolvedValue({ ...SEALED, intact: false })
    page()
    expect(await screen.findByTestId('seal')).toHaveTextContent('The manifest does NOT have the hash it was sealed with.')
  })

  it('an export says why it is leaving, and what the archive was found to hold', async () => {
    page()
    fireEvent.click(await screen.findByRole('button', { name: 'Export' }))
    const dialog = await screen.findByRole('dialog')
    expect(dialog).toHaveTextContent('An original is never marked')
    const confirm = within(dialog).getByRole('button', { name: 'Export' })
    expect(confirm).toBeDisabled()
    fireEvent.change(within(dialog).getByLabelText('Why it is leaving the platform'),
                     { target: { value: ' Requested by the insurer’s assessor. ' } })
    fireEvent.click(confirm)
    await waitFor(() => expect(api.exportPackage).toHaveBeenCalledWith(
      'pkg1', 'EVP-20261006-0001', 'Requested by the insurer’s assessor.', true))
    await waitFor(() => expect(api.saveFile).toHaveBeenCalledWith(EXPORT.file, 'EVP-20261006-0001.zip'))
    const result = await screen.findByTestId('export-result')
    expect(result).toHaveTextContent('Exported 2 files: 2 matched their checksum')
    expect(result).not.toHaveTextContent('DID NOT MATCH')
  })

  it('an export in which a file did not match its checksum says so as an error', async () => {
    vi.mocked(api.exportPackage).mockResolvedValue({ ...EXPORT, verified: 1, mismatched: 1, left_out: 1, included: 2 })
    page()
    fireEvent.click(await screen.findByRole('button', { name: 'Export' }))
    const dialog = await screen.findByRole('dialog')
    fireEvent.click(within(dialog).getByRole('switch'))
    fireEvent.change(within(dialog).getByLabelText('Why it is leaving the platform'), { target: { value: 'For the police.' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Export' }))
    await waitFor(() => expect(api.exportPackage).toHaveBeenCalledWith('pkg1', 'EVP-20261006-0001', 'For the police.', false))
    const result = await screen.findByTestId('export-result')
    expect(result).toHaveTextContent('1 DID NOT MATCH')
    expect(result).toHaveTextContent('1 left out')
    expect(result.className).toMatch(/Error/i)
  })

  it('a refused export is shown in the server’s words', async () => {
    vi.mocked(api.exportPackage).mockRejectedValue({ response: { status: 409, data: {
      detail: 'This package’s manifest no longer has the hash it was sealed with. It is not exported as it is.' } } })
    page()
    fireEvent.click(await screen.findByRole('button', { name: 'Export' }))
    const dialog = await screen.findByRole('dialog')
    fireEvent.change(within(dialog).getByLabelText('Why it is leaving the platform'), { target: { value: 'For the police.' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Export' }))
    expect(await within(dialog).findByText(/no longer has the hash it was sealed with/)).toBeInTheDocument()
    expect(api.saveFile).not.toHaveBeenCalled()
  })

  it('one original is downloaded as the platform holds it', async () => {
    page()
    const items = await screen.findAllByTestId('package-item')
    fireEvent.click(within(items[0]).getByText('Download the original'))
    await waitFor(() => expect(api.downloadOriginal).toHaveBeenCalledWith('pkg1', 'item-ev1'))
    await waitFor(() => expect(api.saveFile).toHaveBeenCalledWith(expect.any(Blob), 'EVP-20261006-0001-snapshot-ev1'))
  })

  it('who it went to is recorded as a person’s statement', async () => {
    page()
    fireEvent.click(await screen.findByRole('button', { name: 'Record who it went to' }))
    const dialog = await screen.findByRole('dialog')
    expect(dialog).toHaveTextContent('The platform sends nothing to anybody.')
    const record = within(dialog).getByRole('button', { name: 'Record' })
    expect(record).toBeDisabled()
    fireEvent.mouseDown(within(dialog).getByLabelText('What was done'))
    fireEvent.click(await screen.findByRole('option', { name: /Released/ }))
    fireEvent.change(within(dialog).getByLabelText('Who it went to'), { target: { value: ' Acme Insurance ' } })
    fireEvent.change(within(dialog).getByLabelText('Why'), { target: { value: 'Claim 4471.' } })
    fireEvent.click(record)
    await waitFor(() => expect(api.recordDisclosure).toHaveBeenCalledWith('pkg1', {
      step: 'RELEASED', recipient: 'Acme Insurance', organisation: undefined, reason: 'Claim 4471.' }))
  })

  it('the holds are lifted by somebody who may, with a reason', async () => {
    page()
    fireEvent.click(await screen.findByRole('button', { name: 'Lift the holds' }))
    const dialog = await screen.findByRole('dialog')
    expect(dialog).toHaveTextContent('may be deleted')
    const lift = within(dialog).getByRole('button', { name: 'Lift the holds' })
    fireEvent.change(within(dialog).getByLabelText('Why they are being lifted'), { target: { value: 'ok' } })
    expect(lift).toBeDisabled()
    fireEvent.change(within(dialog).getByLabelText('Why they are being lifted'), { target: { value: 'The claim is settled.' } })
    fireEvent.click(lift)
    await waitFor(() => expect(api.releasePackageHolds).toHaveBeenCalledWith('pkg1', 'The claim is settled.'))
  })

  it('a supervisor exports and does not lift a hold; an operator does neither', async () => {
    vi.mocked(api.getPackage).mockResolvedValue({ ...SEALED, can: { manage: true, export: true, hold: false, custody: true } })
    const first = page()
    expect(await screen.findByRole('button', { name: 'Export' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Lift the holds' })).not.toBeInTheDocument()
    first.unmount()
    asRole(OPERATOR)
    vi.mocked(api.getPackage).mockResolvedValue({ ...SEALED, can: { manage: true, export: false, hold: false, custody: false } })
    page()
    await screen.findAllByTestId('package-item')
    expect(screen.queryByRole('button', { name: 'Export' })).not.toBeInTheDocument()
    expect(screen.queryByText('Download the original')).not.toBeInTheDocument()
  })

  it('the chain of custody: each step, who, in what role, and why', async () => {
    page()
    const steps = await screen.findAllByTestId('custody-step')
    expect(steps).toHaveLength(7)
    expect(steps[0]).toHaveTextContent('Captured — Frame at the detection')
    expect(steps[0]).toHaveTextContent('The platform')
    expect(steps[1]).toHaveTextContent('Added to the package — Frame at the detection')
    expect(steps[1]).toHaveTextContent('Priya Nair, Operator')
    expect(steps[2]).toHaveTextContent('Accessed — Frame at the detection')
    expect(steps[2]).toHaveTextContent('Lim Wei, Viewer · Opened')
    expect(steps[3]).toHaveTextContent('Sealed')
    expect(steps[3]).toHaveTextContent('Tan Wei Ming, Supervisor · 2 item(s)')
    expect(steps[4]).toHaveTextContent('2 file(s) · 1 matched their checksum · 1 DID NOT MATCH')
    expect(steps[4]).toHaveTextContent('Requested by the insurer’s assessor.')
    expect(steps[5]).toHaveTextContent('Released')
    expect(steps[5]).toHaveTextContent('Aisha Rahman, Manager · To Acme Insurance, Claims')
    expect(steps[5]).toHaveTextContent('Claim 4471.')
    expect(steps[6]).toHaveTextContent('Hold lifted')
    expect(steps[6]).toHaveTextContent('Somebody no longer on the system')
    expect(screen.getByText(/the platform sends nothing to anybody/)).toBeInTheDocument()
  })

  it('a package that is not this reader’s says so and shows nothing', async () => {
    vi.mocked(api.getPackage).mockRejectedValue({ response: { status: 404, data: { detail: 'Evidence package not found' } } })
    page()
    expect(await screen.findByText('Evidence package not found')).toBeInTheDocument()
    expect(screen.queryByTestId('package-item')).not.toBeInTheDocument()
  })
})

describe('Evidence holds', () => {
  const holds = () => renderAt('/evidence-holds', '/evidence-holds', <EvidenceHolds />)

  it('lists what is held and why, and a hold is lifted only by somebody who may, with a reason', async () => {
    const first = holds()
    const row = await screen.findByTestId('hold-row')
    expect(row).toHaveTextContent('Recording')
    expect(row).toHaveTextContent('Sealed in evidence package EVP-20261006-0001.')
    expect(api.listHolds).toHaveBeenCalledWith({ in_force: true, limit: 25, offset: 0 })
    expect(within(row).queryByRole('button', { name: 'Lift' })).not.toBeInTheDocument()
    first.unmount()

    asRole(ADMIN)
    holds()
    fireEvent.click(within(await screen.findByTestId('hold-row')).getByRole('button', { name: 'Lift' }))
    const dialog = await screen.findByRole('dialog')
    expect(dialog).toHaveTextContent('The hold stays on the record as lifted.')
    fireEvent.change(within(dialog).getByLabelText('Why it is being lifted'), { target: { value: 'The case is closed.' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Lift the hold' }))
    await waitFor(() => expect(api.releaseHold).toHaveBeenCalledWith('h1', 'The case is closed.'))
  })

  it('shows the holds that were lifted, with who lifted each and why', async () => {
    vi.mocked(api.listHolds).mockResolvedValue({ items: [{
      ...HOLD, released_at: '2026-10-07T04:00:00Z', released_by_name: 'Aisha Rahman', release_reason: 'The claim is settled.' }],
      total: 1, limit: 25, offset: 0, has_more: false })
    asRole(ADMIN)
    holds()
    await screen.findByTestId('hold-row')
    fireEvent.mouseDown(screen.getByLabelText('Holds'))
    fireEvent.click(await screen.findByRole('option', { name: 'Lifted' }))
    await waitFor(() => expect(api.listHolds).toHaveBeenLastCalledWith({ in_force: false, limit: 25, offset: 0 }))
    expect(await screen.findByText(/Aisha Rahman: The claim is settled./)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Lift' })).not.toBeInTheDocument()
  })

  it('says when nothing is held', async () => {
    vi.mocked(api.listHolds).mockResolvedValue({ items: [], total: 0, limit: 25, offset: 0, has_more: false })
    holds()
    expect(await screen.findByText('Nothing is under a hold.')).toBeInTheDocument()
  })
})

describe('An investigation’s evidence', () => {
  const FILE = {
    id: 'inv1', investigation_number: 'INV-20261006-0001', title: 'Lorry at the back fence',
    reason: 'Reported by the night supervisor.', status: 'OPEN', site_id: 's1', site_name: 'Factory A', incident_id: null,
    situation_id: null, opened_by_user_id: 'u1', opened_by_name: 'Priya Nair', opened_at: '2026-10-05T18:00:00Z',
    closed_by_user_id: null, closed_by_name: null, closed_at: null, closing_note: null, updated_at: '2026-10-05T18:00:00Z',
    items: [], counts: { records: 3, notes: 0, set_aside: 0, not_shown: 0 }, can_manage: true,
  }
  const file = () => renderAt('/investigations/inv1', '/investigations/:id', <Investigation />)

  it('lists the packages made for it, and makes one for it', async () => {
    vi.mocked(investigations.getInvestigation).mockResolvedValue(FILE as never)
    file()
    const card = await screen.findByTestId('evidence-of')
    await waitFor(() => expect(api.listPackages).toHaveBeenCalledWith({ investigation_id: 'inv1' }))
    expect(await within(card).findByText('EVP-20261006-0001 — Evidence of the lorry')).toBeInTheDocument()
    fireEvent.click(within(card).getByRole('button', { name: 'Put the evidence together' }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByLabelText('Investigation')).toHaveValue('INV-20261006-0001 — Lorry at the back fence')
    expect(within(dialog).getByLabelText('Investigation')).toBeDisabled()
    fireEvent.change(within(dialog).getByLabelText('What it is'), { target: { value: 'Evidence of the lorry' } })
    fireEvent.change(within(dialog).getByLabelText('What it is for'), { target: { value: 'For the insurer.' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Create' }))
    await waitFor(() => expect(api.createPackage).toHaveBeenCalledWith({
      title: 'Evidence of the lorry', purpose: 'For the insurer.', investigation_id: 'inv1' }))
    expect(await screen.findByTestId('went-to')).toHaveTextContent('/evidence-packages/pkg9')
  })

  it('an investigation with no records in it has no evidence to put together', async () => {
    vi.mocked(investigations.getInvestigation).mockResolvedValue({
      ...FILE, counts: { ...FILE.counts, records: 0 } } as never)
    vi.mocked(api.listPackages).mockResolvedValue({ items: [], total: 0, limit: 50, offset: 0, has_more: false })
    file()
    const card = await screen.findByTestId('evidence-of')
    expect(card).toHaveTextContent('No evidence package has been made for this investigation.')
    expect(within(card).getByRole('button', { name: 'Put the evidence together' })).toBeDisabled()
  })

  it('a guard is shown no evidence card', async () => {
    asRole(GUARD)
    vi.mocked(investigations.getInvestigation).mockResolvedValue({ ...FILE, can_manage: false } as never)
    file()
    expect(await screen.findByText('Reported by the night supervisor.')).toBeInTheDocument()
    expect(screen.queryByTestId('evidence-of')).not.toBeInTheDocument()
    expect(api.listPackages).not.toHaveBeenCalled()
  })
})

describe('the words the evidence screens share', () => {
  it('a checksum short enough to read, a size, a role', () => {
    expect(short(SHA('a'))).toBe(`${'a'.repeat(10)}…${'a'.repeat(6)}`)
    expect(short(null)).toBe('none recorded')
    expect([size(512), size(64 * 1024), size(5.5 * 1024 * 1024), size(2 * 1024 ** 3), size(null)]).toEqual(
      ['512 B', '64 KB', '5.5 MB', '2.00 GB', null])
    expect([roleName(3), roleName(8), roleName(42), roleName(null)]).toEqual(['Supervisor', 'Manager', 'Role 42', null])
    expect([length(45), length(900), length(3600), length(9000), length(null), length(0)]).toEqual(
      ['45 s', '15 min', '60 min', '2.5 h', null, null])
  })

  it('what a step says beyond who and when', () => {
    expect(said(step({ step: 'SHARED', detail: { recipient: 'Insp. Lee', organisation: null } }))).toBe('To Insp. Lee')
    expect(said(step({ step: 'EXPORTED', detail: { included: 5, verified: 5, mismatched: 0, left_out: 0 } })))
      .toBe('5 file(s) · 5 matched their checksum')
    expect(said(step({ step: 'ACCESSED', detail: { how: 'Exported', checksum_verified: false } })))
      .toBe('Exported · CHECKSUM DID NOT MATCH')
    expect(said(step({ step: 'ACCESSED', detail: { how: 'Downloaded', checksum_verified: true } })))
      .toBe('Downloaded · checksum matched')
    expect(said(step({ step: 'LOCKED' }))).toBeNull()
  })

  it('a refusal is read in the server’s words, whatever shape it came in', () => {
    expect(api.apiError({ response: { status: 429 } })).toMatch(/Too many exports/)
    expect(api.apiError({ response: { data: { detail: 'This package is sealed.' } } })).toBe('This package is sealed.')
    expect(api.apiError({ response: { data: { detail: { message: 'Nothing was added.', not_found: [] } } } }))
      .toBe('Nothing was added.')
    expect(api.apiError({ message: 'Network Error' })).toBe('Network Error')
  })
})
