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
  detail: string
}

export interface PayrollRunDetail extends PayrollRun {
  payslips: Payslip[]
  warnings?: string[]
  pwm_exceptions?: PwmException[]
}

export interface PwmComplianceRow {
  user_id: string
  full_name: string | null
  employee_code: string | null
  pwm_grade: string | null
  employment_type: string | null
  verdict: PwmVerdict
  floor_applied: number | null
  actual: number | null
  basis: string | null
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
