/**
 * The payroll page's Singapore rules (3 November 2026, Phase 1 of the known limits): bonus-type pay on a draft run,
 * Recalculate, the CPF table each payslip used, and the year-end Additional Wage ceiling check.
 *
 * What these pin: a run whose additional pay changed cannot be finalised until it is recalculated (the server says
 * so too; the page must not offer a button that only fails), a bonus line is added for a guard on the run, a payslip
 * says which CPF table it used or why it has no CPF, and the ceiling check says over/under with the amount.
 */
import { render, screen, fireEvent, waitFor, within } from '@/test/utils'
import PayrollPage from '@/pages/Payroll'
import {
  listPayrollRuns, getPayrollRun, recalculatePayrollRun, listAdditionalWages, addAdditionalWage,
  getAwCeilingCheck,
} from '@/api/payroll'
import { useAuthStore } from '@/store/auth'
import type { PayrollRunDetail } from '@/api/payroll'

vi.mock('@/api/payroll', async (importOriginal) => {
  const real = await importOriginal<typeof import('@/api/payroll')>()
  return {
    ADDITIONAL_WAGE_KINDS: real.ADDITIONAL_WAGE_KINDS,
    createPayrollRun: vi.fn(), listPayrollRuns: vi.fn(), getPayrollRun: vi.fn(), finalizePayrollRun: vi.fn(),
    payslipPdfUrl: vi.fn(() => null), getIr8aSummary: vi.fn(), ir8aPdfUrl: vi.fn(() => null),
    getPwmCompliance: vi.fn(), getOvertimeProjection: vi.fn(), recalculatePayrollRun: vi.fn(),
    listAdditionalWages: vi.fn(), addAdditionalWage: vi.fn(), removeAdditionalWage: vi.fn(),
    getAwCeilingCheck: vi.fn(),
  }
})
vi.mock('@/hooks/usePageLabels', () => ({ usePageLabelOverrides: () => ({}) }))
vi.mock('@/store/auth', () => ({ useAuthStore: vi.fn() }))

const payslip = (over: object) => ({
  id: 'p1', guard_user_id: 'g1', guard_name: 'Rajesh Kumar', regular_hours: 160, overtime_hours: 0,
  days_worked: 20, base_pay: 3000, overtime_pay: 0, gross_pay: 3000, cpf_employee: 600, cpf_employer: 510,
  net_pay: 2400, unpaid_leave_days: 0, additional_wage: 0, cpf_scheme: 'full',
  cpf_scheme_label: 'Full rates', cpf_age_band_label: '55 and below', cpf_note: null,
  ...over,
})

const run = (over: Partial<PayrollRunDetail> = {}): PayrollRunDetail => ({
  id: 'r1', period_start: '2026-10-01', period_end: '2026-10-31', status: 'draft',
  generated_at: '2026-11-01T00:00:00Z', finalized_at: null, warnings: [], pwm_exceptions: [],
  needs_recalculation: false,
  payslips: [
    payslip({}),
    payslip({
      id: 'p2', guard_user_id: 'g2', guard_name: 'David Lim', cpf_scheme: null, cpf_scheme_label: null,
      cpf_note: 'no date of birth on file', cpf_employee: 0, cpf_employer: 0, net_pay: 3000,
    }),
  ],
  ...over,
} as PayrollRunDetail)

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(useAuthStore).mockImplementation((sel: any) =>
    sel({ user: { id: 'me', tenantId: 't1', roleId: 2 }, accessToken: 'tok', permissions: null })
  )
  vi.mocked(listPayrollRuns).mockResolvedValue([run()])
  vi.mocked(getPayrollRun).mockResolvedValue(run())
  vi.mocked(listAdditionalWages).mockResolvedValue([])
})

async function openRun() {
  render(<PayrollPage />)
  fireEvent.click(await screen.findByText(/2026-10-01/))
  await screen.findByText('Rajesh Kumar')
}

describe('Payroll — Singapore CPF', { timeout: 60_000 }, () => {
  it('says which CPF table each payslip used, or why it has none', async () => {
    await openRun()
    expect(screen.getByText('CPF: Full rates, 55 and below')).toBeInTheDocument()
    expect(screen.getByText('CPF not worked out: no date of birth on file')).toBeInTheDocument()
  })

  it('holds Finalize until a changed run is recalculated', async () => {
    vi.mocked(getPayrollRun).mockResolvedValue(run({ needs_recalculation: true }))
    vi.mocked(recalculatePayrollRun).mockResolvedValue(run({ needs_recalculation: false }))
    await openRun()
    expect(screen.getByText(/additional pay changed since these payslips were worked out/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /finalize run/i })).toBeDisabled()

    fireEvent.click(screen.getByRole('button', { name: /^recalculate$/i }))
    await waitFor(() => expect(recalculatePayrollRun).toHaveBeenCalledWith('r1'))
  })

  it('adds a bonus for a guard on the run', async () => {
    vi.mocked(addAdditionalWage).mockResolvedValue({} as any)
    await openRun()
    expect(await screen.findByText('No additional pay on this run.')).toBeInTheDocument()

    fireEvent.mouseDown(screen.getByRole('combobox', { name: 'Employee' }))
    fireEvent.click(within(await screen.findByRole('listbox')).getByText('David Lim'))
    fireEvent.change(screen.getByLabelText(/amount/i), { target: { value: '1500' } })
    fireEvent.click(screen.getByRole('button', { name: /^add$/i }))

    await waitFor(() => expect(addAdditionalWage).toHaveBeenCalled())
    expect(vi.mocked(addAdditionalWage).mock.calls[0]).toEqual(['r1', {
      guard_user_id: 'g2', kind: 'bonus', amount: 1500, note: undefined,
    }])
  })

  it('offers no additional-pay form on a finalised run', async () => {
    vi.mocked(getPayrollRun).mockResolvedValue(run({ status: 'finalized', finalized_at: '2026-11-02T00:00:00Z' }))
    await openRun()
    expect(screen.queryByRole('button', { name: /^add$/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^recalculate$/i })).not.toBeInTheDocument()
  })

  it('shows the year-end bonus ceiling check, over and under with the amount', async () => {
    vi.mocked(getAwCeilingCheck).mockResolvedValue({
      year: 2026, annual_wage_ceiling: 102000, summary: { checked: 2, over: 1, under: 1 },
      employees: [
        { guard_user_id: 'g1', full_name: 'Rajesh Kumar', ow_subject_to_cpf: 96000, additional_wages: 9000,
          aw_ceiling: 6000, aw_contributed_on: 9000, aw_due_on: 6000, difference: 3000, verdict: 'over' },
        { guard_user_id: 'g2', full_name: 'David Lim', ow_subject_to_cpf: 40000, additional_wages: 5000,
          aw_ceiling: 62000, aw_contributed_on: 4000, aw_due_on: 5000, difference: -1000, verdict: 'under' },
      ],
    })
    render(<PayrollPage />)
    fireEvent.click(screen.getByRole('tab', { name: /bonus cpf ceiling/i }))
    expect(await screen.findByText('Over by $3000.00')).toBeInTheDocument()
    expect(screen.getByText('Under by $1000.00')).toBeInTheDocument()
  })
})
