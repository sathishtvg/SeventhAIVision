/**
 * Did a check-in or check-out go through when the phone was not told?
 *
 * Owner's phone test, 26 October 2026: the server saved the check-in (the selfie matched), but the phone's
 * connection dropped before the answer came back, so the phone said "Failed to submit check-in" and the guard tried
 * again and again. Now, when the answer is lost or the server says the shift is no longer waiting for this step, the
 * phone asks the server for the shift before it says anything; if the step went through, the guard is told so.
 * The server also answers a repeat from the same guard within 10 minutes with `already_done` (backend
 * routers/shifts.py, REPEAT_WINDOW_MINUTES).
 */
import { apiClient } from './client'
import { endShift, startShift, type Shift } from './patrols'

export type Step = 'start' | 'end'

export interface StepResult {
  /** The step was saved earlier, and this phone had not been told. */
  recovered: boolean
}

/** One shift as the server has it now. */
export const getShift = (shiftId: string) =>
  apiClient.get<Shift>(`/api/v1/shifts/${shiftId}`).then((r) => r.data)

/** Whether a failure leaves it unknown if the step was saved: no answer at all (dropped, timed out), "not waiting
 *  for this step" (404), or a server fault. A refusal with its own reason (422 liveness, 403 fake GPS, 503 face check
 *  unavailable) is an answer: nothing was saved. */
export function worthRechecking(err: unknown): boolean {
  const status = (err as { response?: { status?: number } } | null)?.response?.status
  if (status === undefined) return true
  return status === 404 || (status >= 500 && status !== 503)
}

/** Whether the shift as it is now shows the step done. */
export function stepWentThrough(step: Step, shift: Pick<Shift, 'status' | 'auto_closed_at'> | null | undefined): boolean {
  if (!shift) return false
  if (step === 'start') return shift.status === 'active' || (shift.status === 'completed' && !shift.auto_closed_at)
  return shift.status === 'completed' && !shift.auto_closed_at
}

/**
 * Send the step; when the answer is lost, ask the server whether it was saved before calling it a failure.
 * Rejects with the original error when the step was not saved (or the re-check itself could not be made).
 */
export async function sendStep(
  step: Step,
  shiftId: string,
  photoUri: string,
  latitude?: number,
  longitude?: number,
): Promise<StepResult> {
  try {
    const answer = step === 'start'
      ? await startShift(shiftId, photoUri, false, latitude, longitude)
      : await endShift(shiftId, photoUri, false, latitude, longitude)
    return { recovered: Boolean((answer as { already_done?: boolean } | undefined)?.already_done) }
  } catch (err) {
    if (worthRechecking(err)) {
      const shift = await getShift(shiftId).catch(() => null)
      if (stepWentThrough(step, shift)) return { recovered: true }
    }
    throw err
  }
}

/** What the guard is told when a step was saved earlier without the phone being told. */
export function savedMessage(step: Step): { title: string; body: string } {
  return step === 'start'
    ? { title: "You're checked in", body: 'Your check-in was saved. The answer from the server was lost on the way, so there is no need to try again.' }
    : { title: "You're checked out", body: 'Your check-out was saved. The answer from the server was lost on the way, so there is no need to try again.' }
}
