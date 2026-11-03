/**
 * Residency first when an employee is created (3 November 2026, Phase 1 of the known limits).
 *
 * The owner's ask: a new employee is asked Singaporean, PR or Foreigner; for a Singaporean or PR the salary shows the
 * employee and employer CPF straight away, from the same rules payroll uses. These tests pin that the question is
 * asked before an account payroll pays can be created, that the card shows what the server worked out (never a sum
 * of its own), and that a foreigner is told there is no CPF without asking the server.
 */
import { render, screen, fireEvent, waitFor } from '@/test/utils'
import Users from '@/pages/Users'
import { getUsers, createUser, getUserSites, getEmployeeDocuments } from '@/api/users'
import { getCpfPreview } from '@/api/payroll'
import { getPreferences } from '@/api/roster'
import { useAuthStore } from '@/store/auth'
import { missingForCpf, residencyOf } from '@/components/payroll/cpfResidency'
import type { User } from '@/types/api'

vi.mock('@/api/users', () => ({
  getUsers: vi.fn(), createUser: vi.fn(), updateUser: vi.fn(),
  deactivateUser: vi.fn(), getUserSites: vi.fn(), setUserSites: vi.fn(),
  getEmployeeDocuments: vi.fn(), uploadEmployeeDocument: vi.fn(),
  deleteEmployeeDocument: vi.fn(), uploadProfilePhoto: vi.fn(),
}))
vi.mock('@/api/payroll', () => ({ getCpfPreview: vi.fn() }))
vi.mock('@/api/roster', () => ({ getPreferences: vi.fn(), setPreferences: vi.fn() }))
vi.mock('@/api/roles', () => ({ listRoles: vi.fn() }))
vi.mock('@/api/sites', () => ({ getSites: vi.fn() }))
vi.mock('@/api/attendance', () => ({ profilePhotoUrl: vi.fn(() => '') }))
vi.mock('@/api/sessions', () => ({
  getUserSessions: vi.fn(), revokeAllUserSessions: vi.fn(), unlockUserAccount: vi.fn(),
}))
vi.mock('@/store/auth', () => ({ useAuthStore: vi.fn() }))

const PREVIEW = {
  eligible: true, monthly_salary: 3000, note: null, month: '2026-11-01',
  scheme: 'full', scheme_label: 'Full rates', age_band_label: '55 and below',
  employer_percent: '17%', employee_percent: '20%', total_percent: '37%',
  employee_cpf: 600, employer_cpf: 510, total_cpf: 1110, take_home: 2400,
  ow_ceiling: 8000, rates_from: '2026-01-01', below_full_rates: false,
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(useAuthStore).mockImplementation((sel: any) =>
    sel({ user: { id: 'me', tenantId: 't1', roleId: 2 }, accessToken: 'tok', permissions: null })
  )
  vi.mocked(getUsers).mockResolvedValue([] as User[])
  vi.mocked(getUserSites).mockResolvedValue([])
  vi.mocked(getEmployeeDocuments).mockResolvedValue([])
  vi.mocked(getPreferences).mockResolvedValue({ preferred_shift_type: null, preferred_off_days: [] } as any)
  vi.mocked(createUser).mockResolvedValue({} as User)
  vi.mocked(getCpfPreview).mockResolvedValue(PREVIEW)
})

async function openCreate() {
  render(<Users />)
  fireEvent.click(await screen.findByRole('button', { name: /add user/i }))
  fireEvent.change(screen.getByLabelText(/^email/i), { target: { value: 'new.guard@demo.local' } })
  fireEvent.change(screen.getByLabelText(/^password/i), { target: { value: 'Tq7!mR2#vLx9$kE4' } })
}
const createButton = () => screen.getByRole('button', { name: /^create$/i })
const type = (label: RegExp, value: string) => fireEvent.change(screen.getByLabelText(label), { target: { value } })

describe('residency helpers', () => {
  it('reads residency from the work pass the account keeps', () => {
    expect(residencyOf('citizen')).toBe('citizen')
    expect(residencyOf('pr')).toBe('pr')
    expect(residencyOf('wp')).toBe('foreigner')
    expect(residencyOf(null)).toBe('')
  })

  it('says what CPF still needs, and nothing for a foreigner', () => {
    expect(missingForCpf({ workPassType: '', dateOfBirth: '', prSince: '', monthlySalary: '' }))
      .toMatch(/residency/i)
    expect(missingForCpf({ workPassType: 'pr', dateOfBirth: '1990-01-01', prSince: '', monthlySalary: '3000' }))
      .toMatch(/PR since/)
    expect(missingForCpf({ workPassType: 'citizen', dateOfBirth: '1990-01-01', prSince: '', monthlySalary: '0' }))
      .toMatch(/monthly salary/)
    expect(missingForCpf({ workPassType: 'sp', dateOfBirth: '', prSince: '', monthlySalary: '' })).toBeNull()
  })
})

describe('Users — residency on create', { timeout: 60_000 }, () => {
  it('asks residency before a guard can be created', async () => {
    await openCreate()
    expect(screen.getByRole('button', { name: 'Singaporean' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'PR' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Foreigner' })).toBeInTheDocument()
    expect(createButton()).toBeDisabled()
  })

  it("shows the server's CPF for a Singaporean's salary and creates the account with it", async () => {
    await openCreate()
    fireEvent.click(screen.getByRole('button', { name: 'Singaporean' }))
    expect(screen.getByText(/enter the date of birth, monthly salary/i)).toBeInTheDocument()
    expect(createButton()).toBeDisabled()

    type(/date of birth/i, '1990-05-01')
    type(/monthly salary/i, '3000')

    expect(await screen.findByText('Employee CPF 20%')).toBeInTheDocument()
    expect(screen.getByText('Employer CPF 17%')).toBeInTheDocument()
    expect(screen.getByText('$2,400.00')).toBeInTheDocument()
    expect(vi.mocked(getCpfPreview).mock.calls.at(-1)![0]).toMatchObject({
      work_pass_type: 'citizen', date_of_birth: '1990-05-01', monthly_salary: 3000,
    })

    expect(createButton()).toBeEnabled()
    fireEvent.click(createButton())
    await waitFor(() => expect(createUser).toHaveBeenCalled())
    expect(vi.mocked(createUser).mock.calls[0][0]).toMatchObject({
      work_pass_type: 'citizen', date_of_birth: '1990-05-01', monthly_salary: 3000,
    })
    expect(vi.mocked(createUser).mock.calls[0][0].pr_since).toBeUndefined()
  })

  it('needs the PR date for a PR, and sends it', async () => {
    await openCreate()
    fireEvent.click(screen.getByRole('button', { name: 'PR' }))
    type(/date of birth/i, '1990-05-01')
    type(/monthly salary/i, '3000')
    expect(createButton()).toBeDisabled()

    type(/pr since/i, '2026-03-15')
    await screen.findByText('Employee CPF 20%')
    fireEvent.click(createButton())
    await waitFor(() => expect(createUser).toHaveBeenCalled())
    expect(vi.mocked(createUser).mock.calls[0][0]).toMatchObject({ work_pass_type: 'pr', pr_since: '2026-03-15' })
  })

  it('tells a foreigner there is no CPF without asking the server', async () => {
    await openCreate()
    fireEvent.click(screen.getByRole('button', { name: 'Foreigner' }))
    expect(screen.getByText('No CPF for foreign employees.')).toBeInTheDocument()
    expect(createButton()).toBeDisabled()   // which pass, first
    expect(getCpfPreview).not.toHaveBeenCalled()
  })
})
