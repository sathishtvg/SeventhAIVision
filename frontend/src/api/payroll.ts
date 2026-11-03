import { apiClient } from './client'

export interface PayrollRun {
  id: string
  period_start: string
  period_end: string
  status: 'draft' | 'finalized'
  generated_at: string
  finalized_at: string | null
}

export interface Payslip {
  id: string
  guard_user_id: string
  guard_name: string
  regular_hours: number
  overtime_hours: number
  days_worked: number
  base_pay: number
  overtime_pay: number
  gross_pay: number
  cpf_employee: number
  cpf_employer: number
  net_pay: number
  unpaid_leave_days: number
  /** Since 3 Nov 2026 (migration 0175): what the CPF was worked out on and from. Null on older payslips. */
  ordinary_wage?: number | null
  additional_wage?: number
  ow_subject_to_cpf?: number | null
  aw_subject_to_cpf?: number | null
  aw_ceiling_left?: number | null
  cpf_scheme?: string | null
  cpf_scheme_label?: string | null
  cpf_age_band_label?: string | null
  cpf_rates_from?: string | null
  /** Why CPF was not worked out (no date of birth, no PR date, no rate table), or null. */
  cpf_note?: string | null
}

export type PwmVerdict = 'compliant' | 'below_floor' | 'not_assessed'

/** A payslip whose BASIC wage sits below the Progressive Wage Model floor.
 *  Reported, never blocking: payroll says what happened, and a run that refuses
 *  to finalise means guards are not paid on time. The wage is refused earlier,
 *  when it is set. */
export interface PwmException {
  user_id: string
  full_name: string | null
  pwm_grade: string
  floor_applied: number | null
  actual: number | null
  basis: string | null
  source?: string | null
  detail: string
}

export interface PayrollRunDetail extends PayrollRun {
  payslips: Payslip[]
  warnings?: string[]
  pwm_exceptions?: PwmException[]
  recalculated_at?: string | null
  /** Additional pay changed since the payslips were worked out: Recalculate to apply it. */
  needs_recalculation?: boolean
  cpf_rates_from?: string | null
}

export type AdditionalWageKind = 'bonus' | 'aws' | 'leave_encashment' | 'incentive' | 'other'

export const ADDITIONAL_WAGE_KINDS: { value: AdditionalWageKind; label: string }[] = [
  { value: 'bonus', label: 'Bonus' },
  { value: 'aws', label: '13th month (AWS)' },
  { value: 'leave_encashment', label: 'Leave encashment' },
  { value: 'incentive', label: 'Incentive' },
  { value: 'other', label: 'Other' },
]

/** Bonus-type pay on a draft run: CPF's Additional Wages, capped by the year's AW ceiling. */
export interface AdditionalWage {
  id: string
  guard_user_id: string
  guard_name?: string | null
  kind: AdditionalWageKind
  kind_label: string
  amount: number
  note: string | null
  created_at: string
  updated_at: string
}

export interface AwCeilingRow {
  guard_user_id: string
  full_name: string | null
  ow_subject_to_cpf: number
  additional_wages: number
  aw_ceiling: number
  aw_contributed_on: number
  aw_due_on: number
  difference: number
  verdict: 'over' | 'under' | 'right'
}

export interface AwCeilingCheck {
  year: number
  annual_wage_ceiling: number | null
  employees: AwCeilingRow[]
  summary: { checked: number; over: number; under: number }
}

export interface CpfPreviewInput {
  work_pass_type: 'citizen' | 'pr' | 'ep' | 'sp' | 'wp'
  date_of_birth?: string
  pr_since?: string
  cpf_rate_type?: 'graduated' | 'fg' | 'full'
  monthly_salary: number
  month?: string
}

/** What CPF a salary carries, from the same rules payroll uses (services/cpf.py). */
export interface CpfPreview {
  eligible: boolean
  monthly_salary: number
  note: string | null
  month: string
  scheme?: string
  scheme_label?: string
  age_band_label?: string
  employer_percent?: string
  employee_percent?: string
  total_percent?: string
  employee_cpf?: number
  employer_cpf?: number
  total_cpf?: number
  take_home?: number
  ow_ceiling?: number
  rates_from?: string
  below_full_rates?: boolean
}

export const recalculatePayrollRun = (id: string) =>
  apiClient.post<PayrollRunDetail>(`/api/v1/payroll/runs/${id}/recalculate`).then((r) => r.data)

export const listAdditionalWages = (runId: string) =>
  apiClient.get<AdditionalWage[]>(`/api/v1/payroll/runs/${runId}/additional-wages`).then((r) => r.data)

export const addAdditionalWage = (runId: string, data: {
  guard_user_id: string; kind: AdditionalWageKind; amount: number; note?: string
}) => apiClient.post<AdditionalWage>(`/api/v1/payroll/runs/${runId}/additional-wages`, data).then((r) => r.data)

export const removeAdditionalWage = (runId: string, lineId: string) =>
  apiClient.delete(`/api/v1/payroll/runs/${runId}/additional-wages/${lineId}`).then((r) => r.data)

export const getAwCeilingCheck = (year: number) =>
  apiClient.get<AwCeilingCheck>('/api/v1/payroll/aw-ceiling-check', { params: { year } }).then((r) => r.data)

export const getCpfPreview = (data: CpfPreviewInput) =>
  apiClient.post<CpfPreview>('/api/v1/payroll/cpf-preview', data).then((r) => r.data)

export interface PwmComplianceRow {
  user_id: string
  full_name: string | null
  employee_code: string | null
  pwm_grade: string | null
  employment_type: string | null
  employment_arrangement?: 'outsourced' | 'in_house'
  verdict: PwmVerdict
  floor_applied: number | null
  actual: number | null
  basis: string | null
  /** Which of MOM's schedules the guard was judged against, in words, and MOM's page. */
  basis_label?: string
  source?: string | null
  detail: string
}

export interface PwmComplianceReport {
  as_of: string
  /** not_assessed is its own count, never folded into compliant: "0 below
   *  floor" must not hide "nobody has been graded yet". */
  summary: { total: number; compliant: number; below_floor: number; not_assessed: number }
  guards: PwmComplianceRow[]
}

export interface Ir8aRow {
  guard_user_id: string
  full_name: string | null
  nric_fin: string | null
  annual_gross: number
  annual_employer_cpf: number
  annual_additional_wages?: number | null
  annual_employee_cpf?: number | null
}

export const createPayrollRun = (data: { period_start: string; period_end: string }) =>
  apiClient.post<PayrollRunDetail>('/api/v1/payroll/runs', data).then((r) => r.data)

export const listPayrollRuns = () =>
  apiClient.get<PayrollRun[]>('/api/v1/payroll/runs').then((r) => r.data)

export const getPayrollRun = (id: string) =>
  apiClient.get<PayrollRunDetail>(`/api/v1/payroll/runs/${id}`).then((r) => r.data)

export const finalizePayrollRun = (id: string) =>
  apiClient.put(`/api/v1/payroll/runs/${id}/finalize`).then((r) => r.data)

// Query-param-JWT URL — a PDF download link can't carry an Authorization
// header, mirrors checkinPhotoUrl's pattern in api/attendance.ts.
export const payslipPdfUrl = (payslipId: string, token: string | null) =>
  token ? `${apiClient.defaults.baseURL}/api/v1/payroll/payslips/${payslipId}/pdf?token=${token}` : null

export const getIr8aSummary = (year: number) =>
  apiClient.get<Ir8aRow[]>('/api/v1/payroll/ir8a', { params: { year } }).then((r) => r.data)

export const ir8aPdfUrl = (year: number, token: string | null) =>
  token ? `${apiClient.defaults.baseURL}/api/v1/payroll/ir8a/pdf?year=${year}&token=${token}` : null

export const getPwmCompliance = (asOf?: string) =>
  apiClient
    .get<PwmComplianceReport>('/api/v1/payroll/pwm-compliance', {
      params: asOf ? { as_of: asOf } : undefined,
    })
    .then((r) => r.data)

/**
 * Accrued and projected are separate fields on purpose. One is what payroll
 * will bill from completed shifts; the other is an estimate of shifts nobody
 * has worked yet. Summing them away would hide which half is a fact from the
 * person deciding whether to move a shift.
 */
export interface OvertimeProjectionRow {
  guard_user_id: string
  guard_name: string
  accrued_ot_hours: number
  projected_ot_hours: number
  total_ot_hours: number
  cap_hours: number
  over_by_hours: number
  status: 'OVER_CAP' | 'APPROACHING' | 'WITHIN'
  scheduled_shifts_remaining: number
}

export interface OvertimeProjectionReport {
  month: string
  cap_hours: number
  normal_hours_per_day: number
  over_cap: number
  approaching: number
  guards_assessed: number
  /** Only the guards worth acting on; WITHIN is counted, not listed. */
  guards: OvertimeProjectionRow[]
}

export const getOvertimeProjection = (month?: string) =>
  apiClient
    .get<OvertimeProjectionReport>('/api/v1/payroll/overtime-projection', {
      params: month ? { month } : undefined,
    })
    .then((r) => r.data)
