import type { User, EmployeeDocument } from '@/types/api'
import { apiClient } from './client'

export const getUsers = () =>
  apiClient.get<User[]>('/api/v1/users').then((r) => r.data)

export const createUser = (data: { email: string; password: string; role_id: number; full_name?: string }) =>
  apiClient.post<User>('/api/v1/users', data).then((r) => r.data)

export const getUser = (userId: string) =>
  apiClient.get<User>(`/api/v1/users/${userId}`).then((r) => r.data)

export interface UserUpdateData {
  full_name?: string
  role_id?: number
  is_active?: boolean
  nric_fin?: string
  date_of_birth?: string
  nationality?: string
  phone?: string
  address?: string
  work_pass_type?: 'citizen' | 'pr' | 'ep' | 'sp' | 'wp'
  work_pass_expiry?: string
  employment_type?: 'full_time' | 'part_time' | 'contract'
  designation?: string
  department?: string
  date_joined?: string
  bank_name?: string
  bank_account_number?: string
  emergency_contact_name?: string
  emergency_contact_phone?: string
  hourly_rate?: number
  daily_rate?: number
  monthly_salary?: number
}

export const updateUser = (userId: string, data: UserUpdateData) =>
  apiClient.put<Partial<User>>(`/api/v1/users/${userId}`, data).then((r) => r.data)

export const deactivateUser = (userId: string) =>
  apiClient.delete(`/api/v1/users/${userId}`).then((r) => r.data)

// ── Site assignments (Gap 81 — site-scoped access) ──────────────────────────

export interface UserSite {
  site_id: string
  site_name: string
}

export const getUserSites = (userId: string) =>
  apiClient.get<UserSite[]>(`/api/v1/users/${userId}/sites`).then((r) => r.data)

export const setUserSites = (userId: string, siteIds: string[]) =>
  apiClient.put(`/api/v1/users/${userId}/sites`, { site_ids: siteIds }).then((r) => r.data)

// ── Employee documents (ShiftSecure Phase 1) ─────────────────────────────────

export const getEmployeeDocuments = (userId: string) =>
  apiClient.get<EmployeeDocument[]>(`/api/v1/users/${userId}/documents`).then((r) => r.data)

export interface UploadEmployeeDocumentData {
  document_type: 'passport' | 'work_pass' | 'certification' | 'other'
  document_number?: string
  issuing_body?: string
  issue_date?: string
  expiry_date?: string
  notes?: string
  file?: File
}

export const uploadEmployeeDocument = (userId: string, data: UploadEmployeeDocumentData) => {
  const fd = new FormData()
  fd.append('document_type', data.document_type)
  if (data.document_number) fd.append('document_number', data.document_number)
  if (data.issuing_body) fd.append('issuing_body', data.issuing_body)
  if (data.issue_date) fd.append('issue_date', data.issue_date)
  if (data.expiry_date) fd.append('expiry_date', data.expiry_date)
  if (data.notes) fd.append('notes', data.notes)
  if (data.file) fd.append('file', data.file)
  return apiClient
    .post<EmployeeDocument>(`/api/v1/users/${userId}/documents`, fd, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
    .then((r) => r.data)
}

export const deleteEmployeeDocument = (userId: string, docId: string) =>
  apiClient.delete(`/api/v1/users/${userId}/documents/${docId}`).then((r) => r.data)

export const employeeDocumentFileUrl = (userId: string, docId: string) =>
  `/api/v1/users/${userId}/documents/${docId}/file`

/** Replace a guard's permanent profile photo. Multipart; the server stores one
 *  file per user under a fixed name, so re-uploading overwrites rather than
 *  accumulating orphans. */
export const uploadProfilePhoto = (userId: string, file: File) => {
  const form = new FormData()
  form.append('file', file)
  return apiClient
    .post<{ profile_photo_path: string }>(`/api/v1/users/${userId}/photo`, form)
    .then((r) => r.data)
}
