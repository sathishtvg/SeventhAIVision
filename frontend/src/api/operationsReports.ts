/**
 * The operations reports (backend/app/routers/operations_reports.py).
 *
 * A report is records the platform already keeps, taken out as a CSV file. It
 * is made when it is asked for; what is kept of it is a line in the audit log.
 * Taking one out needs `opsreport:export` and the permission its own records
 * are read under — the list says which, and whether the reader holds it.
 */
import { apiClient } from './client'

const BASE = '/api/v1/operations-reports'

export interface ReportKind {
  key: string
  title: string
  /** What a row of it is, in the server's words. */
  holds: string
  needs: string[]
  /** The file's headings. A column of times says its time zone. */
  columns: string[]
  /** False for a report of how things are now: it has no period. */
  periodic: boolean
  may: boolean
  /** Why the reader may not have it, when they may not. */
  why_not: string | null
}

export interface ReportList {
  reports: ReportKind[]
  periods: number[]
  max_rows: number
  timezone: string
  note: string
}

export interface Taken { filename: string; rows: number | null; cut: boolean }

export const listReports = () => apiClient.get<ReportList>(BASE).then((r) => r.data)

/** A refusal comes back as a file too: read the server's reason out of it. */
async function reasonIn(err: unknown): Promise<never> {
  const e = err as { response?: { data?: unknown } }
  const data = e?.response?.data
  if (data instanceof Blob) {
    try {
      e.response!.data = JSON.parse(await data.text())
    } catch {
      e.response!.data = undefined
    }
  }
  throw err
}

/** Take one report out: the file is handed to the browser to save. */
export async function takeReport(key: string, params: { site_id?: string; client_id?: string; days: number }): Promise<Taken> {
  const response = await apiClient.get(`${BASE}/${key}`, { params, responseType: 'blob' }).catch(reasonIn)
  const said = response.headers['content-disposition'] as string | undefined
  const filename = said?.match(/filename="([^"]+)"/)?.[1] ?? `operations-${key}.csv`
  const href = URL.createObjectURL(response.data as Blob)
  const anchor = document.createElement('a')
  anchor.href = href
  anchor.download = filename
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(href)
  const rows = Number(response.headers['x-report-rows'])
  return { filename, rows: Number.isFinite(rows) ? rows : null, cut: response.headers['x-report-cut'] === 'true' }
}

/** The server's reason, in its own words. */
export function apiError(err: unknown): string {
  const e = err as { response?: { status?: number; data?: { detail?: unknown } }; message?: string }
  if (e?.response?.status === 429) return 'Too many requests in a short time. Wait a minute and try again.'
  const d = e?.response?.data?.detail
  if (typeof d === 'string') return d
  if (Array.isArray(d)) return d.map((x: { msg?: string }) => x.msg ?? String(x)).join('; ')
  return e?.message ?? 'Something went wrong.'
}
