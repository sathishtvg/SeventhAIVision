/**
 * The rules of the phone's procedure card, apart from the card so they can be
 * tested with nothing mounted: where a procedure and a passage are from, and
 * when a question is worth sending.
 */
import type { Passage, RelevantProcedure } from '@/api/sop'

/** Which version it is and who approved it: what makes it the procedure and not somebody's opinion. */
export function procedureSource(p: Pick<RelevantProcedure, 'code' | 'version' | 'site_name'>): string {
  const by = p.version.approved_by_name ? `, approved by ${p.version.approved_by_name}` : ', approved'
  return `${p.code} · version ${p.version.version_no}${by} · ${p.site_name ?? 'every site'}`
}

/** Where a passage that was found is from. */
export function passageSource(p: Pick<Passage, 'procedure' | 'version'>): string {
  return `${p.procedure.code} ${p.procedure.title} · version ${p.version.version_no}`
}

/** A question is sent when it has something to look for: two characters or more. */
export function worthAsking(question: string): boolean {
  return question.trim().length >= 2
}

/** The line that says these are a procedure's words and not an answer. */
export const NOT_AN_ANSWER = 'These are the words of approved procedures, as approved. Nothing was written in answer.'
