/**
 * Evidence packages due for removal, and those removed (3 November 2026, Phase 2 of the known limits).
 *
 * What these pin: with no period nothing is due and the screen says where a period is set; a package is removed
 * only after a reason and its own number are typed, and the request carries both; the record of a removed package
 * is listed, and its chain of custody is offered only to somebody who may read custody.
 */
import { render, screen, fireEvent, waitFor, within } from '@/test/utils'
import { DueForRemoval, RemovedPackages } from '@/components/evidence/EvidenceDisposal'
import { getDueForRemoval, listRemoved, removePackage, getRemovedCustody } from '@/api/evidencePackages'
import { optionalLine } from '@/components/governance/governanceFormat'
import type { OptionalKind } from '@/api/dataGovernance'
import { useAuthStore } from '@/store/auth'

vi.mock('@/api/evidencePackages', () => ({
  getDueForRemoval: vi.fn(), listRemoved: vi.fn(), removePackage: vi.fn(), getRemovedCustody: vi.fn(),
}))
vi.mock('@/store/auth', () => ({ useAuthStore: vi.fn() }))

const NOTE = 'Removing a package removes it and its list of items for good.'
const DUE = {
  id: 'p1', package_number: 'EVP-2025-0007', title: 'East gate, 3 March', status: 'SEALED' as const,
  site_id: 's1', site_name: 'Factory A', sealed_at: '2025-03-04T02:00:00Z', updated_at: '2025-03-04T02:00:00Z',
  investigation_id: 'v1', investigation_number: 'INV-0003', incident_id: null, items: 4, due_since: '2026-03-04T02:00:00Z',
}
const REMOVED = {
  id: 'd1', package_id: 'p0', package_number: 'EVP-2024-0001', title: 'Car park', site_id: 's1', site_name: 'Factory A',
  was_sealed: true, sealed_at: '2024-05-01T00:00:00Z', manifest_sha256: 'ab'.repeat(32), items: 2,
  investigation_id: null, investigation_number: null, incident_id: null, period_days: 365,
  reason: 'The claim was settled.', removed_by_user_id: 'u1', removed_by_name: 'Tan Wei Ming',
  removed_at: '2026-11-03T03:00:00Z',
}

function as(roleId: number) {
  vi.mocked(useAuthStore).mockImplementation((sel: any) =>
    sel({ user: { id: 'me', tenantId: 't1', roleId }, accessToken: 'tok', permissions: null }))
}

beforeEach(() => {
  vi.clearAllMocks()
  as(8)
})

describe('Evidence packages due for removal', { timeout: 60_000 }, () => {
  it('says that nothing is due without a period, and where one is set', async () => {
    vi.mocked(getDueForRemoval).mockResolvedValue({ period_days: null, packages: [], note: 'No period is set.' })
    render(<DueForRemoval />)
    const said = await screen.findByTestId('no-period')
    expect(said).toHaveTextContent('No period is set.')
    expect(within(said).getByRole('link', { name: /data retention/i })).toHaveAttribute('href', '/data-retention')
  })

  it('removes one only after a reason and its own number, and sends both', async () => {
    vi.mocked(getDueForRemoval).mockResolvedValue({ period_days: 365, packages: [DUE], note: NOTE })
    vi.mocked(removePackage).mockResolvedValue({ ...REMOVED, note: NOTE })
    render(<DueForRemoval />)
    expect(await screen.findByText('EVP-2025-0007')).toBeInTheDocument()
    expect(screen.getByText(/keeps evidence packages for 365 days/i)).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /remove…/i }))
    const dialog = await screen.findByRole('dialog')
    const go = within(dialog).getByRole('button', { name: /remove the package/i })
    expect(go).toBeDisabled()
    fireEvent.change(within(dialog).getByLabelText(/why it is removed/i), { target: { value: 'The claim was settled.' } })
    expect(go).toBeDisabled()
    fireEvent.change(within(dialog).getByLabelText(/type EVP-2025-0007 to confirm/i), { target: { value: 'EVP-2025-0006' } })
    expect(go).toBeDisabled()
    fireEvent.change(within(dialog).getByLabelText(/type EVP-2025-0007 to confirm/i), { target: { value: 'evp-2025-0007' } })
    expect(go).toBeEnabled()
    fireEvent.click(go)
    await waitFor(() => expect(removePackage).toHaveBeenCalled())
    expect(vi.mocked(removePackage).mock.calls[0][0]).toEqual({
      package_id: 'p1', package_number: 'evp-2025-0007', reason: 'The claim was settled.' })
  })

  it('lists what is kept of a removed package, and its custody only to whoever may read custody', async () => {
    vi.mocked(listRemoved).mockResolvedValue({ items: [REMOVED], total: 1, limit: 25, offset: 0, has_more: false })
    vi.mocked(getRemovedCustody).mockResolvedValue({ record: REMOVED, chain: [
      { step: 'SEALED', at: '2024-05-01T00:00:00Z', kind: null, ref_id: null, actor_name: 'Lim', actor_role: 2,
        reason: null, detail: {} },
      { step: 'DISPOSED', at: '2026-11-03T03:00:00Z', kind: null, ref_id: null, actor_name: 'Tan Wei Ming',
        actor_role: 8, reason: 'The claim was settled.', detail: {} },
    ] })
    const { unmount } = render(<RemovedPackages />)
    expect(await screen.findByText('EVP-2024-0001')).toBeInTheDocument()
    expect(screen.getByText(/Tan Wei Ming · kept 365 days/)).toBeInTheDocument()
    expect(screen.getByText(`${'ab'.repeat(6)}…`)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /chain of custody/i }))
    expect(await screen.findByText('Package removed')).toBeInTheDocument()
    unmount()

    as(4) // an operator manages packages but does not read custody
    render(<RemovedPackages />)
    await screen.findByText('EVP-2024-0001')
    expect(screen.queryByRole('button', { name: /chain of custody/i })).not.toBeInTheDocument()
  })

  it('says a package period makes them due, not removed', () => {
    const kind = { days: 365, counted_from: 'when it was sealed', by_person: true } as OptionalKind
    expect(optionalLine(kind)).toBe('Due for removal 365 days after when it was sealed')
    expect(optionalLine({ ...kind, by_person: false })).toBe('Removed 365 days after when it was sealed')
  })
})
