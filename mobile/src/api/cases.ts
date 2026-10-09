/**
 * Case tasks given to this person (backend/app/routers/cases.py).
 *
 * Somebody given a task on a case finishes that task, and does nothing else to
 * the case. So the phone shows a person's own tasks and lets them say what was
 * done, or why a task is dropped. The case itself — its people, its links, its
 * closing — is worked at a desk.
 */
import { apiClient } from './client'

const BASE = '/api/v1/cases'

export interface CaseSummary {
  id: string
  case_number: string
  title: string
  status: 'OPEN' | 'AWAITING_APPROVAL' | 'CLOSED'
  site_name: string | null
  tasks_open: number
}

export interface CaseTask {
  id: string
  title: string
  detail: string | null
  assigned_to_user_id: string | null
  due_at: string | null
  state: 'OPEN' | 'DONE' | 'DROPPED'
  created_by_name: string | null
  /** Whether this person may finish it. The server decides. */
  may_finish: boolean
}

/** One case, whole. It names its site as an object, where the list gives the name alone. */
export interface CaseDetail extends Omit<CaseSummary, 'site_name'> {
  site: { id: string; name: string } | null
  tasks: CaseTask[]
}

/** The cases this person leads, investigates or has a task on. */
export const listMyCases = () =>
  apiClient.get<{ items: CaseSummary[] }>(BASE, { params: { mine: true } }).then((r) => r.data.items)

export const getCase = (id: string) => apiClient.get<CaseDetail>(`${BASE}/${id}`).then((r) => r.data)

/** Done, with what was done; or dropped, with why. Either way it is said in words. */
export const finishTask = (caseId: string, taskId: string, how: 'done' | 'drop', note: string) =>
  apiClient.post<CaseDetail>(`${BASE}/${caseId}/tasks/${taskId}/${how}`, { note: note.trim() }).then((r) => r.data)
