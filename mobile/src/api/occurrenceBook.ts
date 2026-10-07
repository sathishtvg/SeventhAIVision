/**
 * What one shift hands the next, on the phone: the instructions in force at a
 * site, and the summary of the guard's own shift
 * (backend/app/routers/occurrence_book.py).
 *
 * Writing an entry in the book is not here — that is ./dob, as it always was.
 */
import { apiClient } from './client'

const BASE = '/api/v1/occurrence-book'

export interface Instruction {
  id: string
  site_id: string
  site_name: string
  body: string
  issued_by_name: string | null
  issued_at: string
  expires_at: string | null
  in_force: boolean
  read_by_me: boolean
}

export interface ShiftSummary {
  id: string
  shift_id: string
  site_name: string | null
  /** What the platform wrote, kept as written. */
  drafted_text: string
  /** What the person made of it. */
  final_text: string
  state: 'DRAFT' | 'CONFIRMED' | 'DISCARDED'
  confirmed_at: string | null
  edited: boolean
  note: string
  /** What this person may do with it. The server decides. */
  may: { edit: boolean; confirm: boolean; discard: boolean }
}

/** The instructions in force at the sites the caller may see, newest first. */
export const getInstructions = () =>
  apiClient.get<{ items: Instruction[] }>(`${BASE}/instructions`, { params: { state: 'in_force' } })
    .then((r) => r.data.items)

/** Say that the caller has read one. Said once; saying it again changes nothing. */
export const markInstructionRead = (id: string) =>
  apiClient.post<Instruction>(`${BASE}/instructions/${id}/read`).then((r) => r.data)

/** The summary of one shift, if it has one the caller may read. */
export const getShiftSummary = (shiftId: string) =>
  apiClient.get<{ items: ShiftSummary[] }>(`${BASE}/shift-summaries`, { params: { shift_id: shiftId } })
    .then((r) => r.data.items[0] ?? null)

/** Draft it from what was recorded — or draft it again, setting an earlier draft aside. */
export const draftShiftSummary = (shiftId: string) =>
  apiClient.post<ShiftSummary>(`${BASE}/shift-summaries`, { shift_id: shiftId }).then((r) => r.data)

export const editShiftSummary = (id: string, finalText: string) =>
  apiClient.patch<ShiftSummary>(`${BASE}/shift-summaries/${id}`, { final_text: finalText }).then((r) => r.data)

/** Confirm it as the summary of the shift. It cannot be changed after. */
export const confirmShiftSummary = (id: string) =>
  apiClient.post<ShiftSummary>(`${BASE}/shift-summaries/${id}/confirm`).then((r) => r.data)
