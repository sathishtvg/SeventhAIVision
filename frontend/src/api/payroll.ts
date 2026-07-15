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

export interface PayrollRunDetail extends PayrollRun {
  payslips: Payslip[]
  warnings?: string[]
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
