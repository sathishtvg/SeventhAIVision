/**
 * The standby switch — the control the Duty Teams refusal points at.
 *
 * Adding a guard to a second site now fails with "…or mark them standby if
 * they relieve across sites", and until this switch existed there was nowhere
 * to do that. These tests pin the three things that make the message true:
 * the switch is reachable, it sends the flag, and it is honest about the roles
 * it does not apply to.
 */
import { render, screen, fireEvent, waitFor, within } from '@/test/utils'
import Users from '@/pages/Users'
import {
  getUsers, updateUser, getUserSites, getEmployeeDocuments,
} from '@/api/users'
import { getPreferences } from '@/api/roster'
import { useAuthStore } from '@/store/auth'
import type { User } from '@/types/api'

vi.mock('@/api/users', () => ({
  getUsers: vi.fn(), createUser: vi.fn(), updateUser: vi.fn(),
  deactivateUser: vi.fn(), getUserSites: vi.fn(), setUserSites: vi.fn(),
  getEmployeeDocuments: vi.fn(), uploadEmployeeDocument: vi.fn(),
  deleteEmployeeDocument: vi.fn(), uploadProfilePhoto: vi.fn(),
}))
vi.mock('@/api/roster', () => ({ getPreferences: vi.fn(), setPreferences: vi.fn() }))
vi.mock('@/api/roles', () => ({ listRoles: vi.fn() }))
vi.mock('@/api/sites', () => ({ getSites: vi.fn() }))
vi.mock('@/api/attendance', () => ({ profilePhotoUrl: vi.fn(() => '') }))
vi.mock('@/api/sessions', () => ({
  getUserSessions: vi.fn(), revokeAllUserSessions: vi.fn(), unlockUserAccount: vi.fn(),
}))
vi.mock('@/store/auth', () => ({ useAuthStore: vi.fn() }))

function makeUser(over: Partial<User>): User {
  return {
    id: 'u1', tenant_id: 't1', role_id: 5, email: 'guard@demo.local',
    full_name: 'Rajesh Kumar', is_active: true, locale: 'en',
    last_login_at: null, created_at: '2026-01-01', updated_at: '2026-01-01',
    nric_fin: null, date_of_birth: null, nationality: null, phone: null,
    address: null, work_pass_type: null, work_pass_expiry: null,
    employment_type: 'full_time', designation: null, department: null,
    date_joined: null, bank_name: null, bank_account_number: null,
    emergency_contact_name: null, emergency_contact_phone: null,
    hourly_rate: 12, daily_rate: null, monthly_salary: null,
    is_standby: false,
    ...over,
  } as User
}

const guard = makeUser({ id: 'u1', full_name: 'Rajesh Kumar', role_id: 5 })
const relief = makeUser({
  id: 'u2', full_name: 'David Lim', email: 'david@demo.local',
  role_id: 5, is_standby: true,
})
const supervisor = makeUser({
  id: 'u3', full_name: 'Tan Wei Ming', email: 'tan@demo.local', role_id: 3,
})

/** MUI v9 renders Switch with role="switch" (a checkbox input underneath), so
 *  role: 'checkbox' finds nothing here. */
const standbySwitch = () =>
  screen.getByRole('switch', { name: /standby \/ relief officer/i })

async function openEmploymentTabFor(name: string) {
  const row = (await screen.findByText(name)).closest('tr')!
  fireEvent.click(within(row).getByRole('button', { name: /edit user/i }))
  fireEvent.click(await screen.findByRole('tab', { name: 'Employment' }))
}

beforeEach(() => {
  // Two tests here assert on mock.calls[0]; without this the second reads the
  // first's call and passes or fails for the wrong reason.
  vi.clearAllMocks()
  vi.mocked(useAuthStore).mockImplementation((sel: any) =>
    sel({ user: { id: 'me', tenantId: 't1', roleId: 2 }, accessToken: 'tok', permissions: null })
  )
  vi.mocked(getUsers).mockResolvedValue([guard, relief, supervisor])
  vi.mocked(getUserSites).mockResolvedValue([])
  vi.mocked(getEmployeeDocuments).mockResolvedValue([])
  vi.mocked(getPreferences).mockResolvedValue({
    preferred_shift_type: null, preferred_off_days: [],
  } as any)
  vi.mocked(updateUser).mockResolvedValue({} as User)
})

// Each test renders the whole Users page — 1100 lines, the grid, and a
// four-tab dialog — and twelve other files run alongside it. The suite-wide
// 20s is enough on an idle machine and not on a loaded one, and a timeout
// here would report as a logic failure rather than as load.
describe('Users — standby', { timeout: 60_000 }, () => {
  it('marks relief officers in the grid, and nobody else', async () => {
    render(<Users />)
    await screen.findByText('David Lim')
    // One chip, on the one standby officer — the point is that the answer to
    // "who can relieve tonight" is visible without opening anyone.
    expect(screen.getAllByText('standby')).toHaveLength(1)
  })

  it('shows the switch on the Employment tab, set from the user', async () => {
    render(<Users />)
    await openEmploymentTabFor('David Lim')
    const toggle = standbySwitch()
    expect(toggle).toBeChecked()
    expect(toggle).toBeEnabled()
  })

  it('sends is_standby when a guard is marked standby', async () => {
    render(<Users />)
    await openEmploymentTabFor('Rajesh Kumar')
    const toggle = standbySwitch()
    expect(toggle).not.toBeChecked()

    fireEvent.click(toggle)
    fireEvent.click(screen.getByRole('button', { name: /^save$/i }))

    await waitFor(() => expect(updateUser).toHaveBeenCalled())
    expect(vi.mocked(updateUser).mock.calls[0][1]).toMatchObject({ is_standby: true })
  })

  it('sends the flag as a real boolean so it can be turned OFF again', async () => {
    render(<Users />)
    await openEmploymentTabFor('David Lim')
    fireEvent.click(standbySwitch())
    fireEvent.click(screen.getByRole('button', { name: /^save$/i }))

    await waitFor(() => expect(updateUser).toHaveBeenCalled())
    // `is_standby: standby || undefined` would drop this and make revoking
    // standby impossible — the field has to travel as false.
    expect(vi.mocked(updateUser).mock.calls[0][1]).toMatchObject({ is_standby: false })
  })

  it('disables the switch for roles that already cover several sites', async () => {
    render(<Users />)
    await openEmploymentTabFor('Tan Wei Ming')
    expect(standbySwitch()).toBeDisabled()
    expect(screen.getByText(/already cover several sites/i)).toBeInTheDocument()
  })
})
