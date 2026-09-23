/**
 * Required answers on a virtual patrol.
 *
 * The officer is standing at the end of a camera with Save and Next Camera
 * greyed out; if this is wrong they either cannot finish a patrol they have
 * done, or they file one with the required questions blank. Neither shows up in
 * a screenshot.
 */
import { unansweredRequired } from './VirtualPatrolRunScreen'
import { VERDICT_OPTIONS, isNegative, type SessionQuestion } from '@/api/virtualPatrol'

const q = (over: Partial<SessionQuestion>): SessionQuestion => ({
  id: 'q1', question_text: 'All clear?', question_type: 'YES_NO',
  is_required: true, options: null, ...over,
})

describe('unansweredRequired', () => {
  it('has nothing to complain about with no questions', () => {
    expect(unansweredRequired([], {})).toEqual([])
  })

  it('reports a required question nobody has touched', () => {
    expect(unansweredRequired([q({ id: 'a' })], {})).toEqual(['a'])
  })

  it('ignores optional questions left blank', () => {
    expect(unansweredRequired([q({ id: 'a', is_required: false })], {})).toEqual([])
  })

  it('accepts "No" as an answer', () => {
    // false is an answer — a guard reporting a problem must not be blocked by a
    // check that treats "No" as unanswered.
    expect(unansweredRequired([q({ id: 'a' })], { a: false })).toEqual([])
  })

  it('accepts zero as an answer', () => {
    // Zero people in a restricted area is the answer you most want recorded.
    expect(unansweredRequired([q({ id: 'a', question_type: 'NUMBER' })], { a: 0 })).toEqual([])
  })

  it('does not accept whitespace as a text answer', () => {
    expect(unansweredRequired([q({ id: 'a', question_type: 'TEXT' })], { a: '   ' })).toEqual(['a'])
    expect(unansweredRequired([q({ id: 'a', question_type: 'TEXT' })], { a: 'clear' })).toEqual([])
  })

  it('lists every outstanding question, not just the first', () => {
    const questions = [q({ id: 'a' }), q({ id: 'b', is_required: false }), q({ id: 'c' })]
    expect(unansweredRequired(questions, { a: true })).toEqual(['c'])
    expect(unansweredRequired(questions, {})).toEqual(['a', 'c'])
  })
})


describe('answer values the server accepts', () => {
  it('sends YES/NO and PASS/FAIL as the strings validate_answer() checks', () => {
    // services/virtual_patrol.py: allowed = ("YES","NO") if YES_NO else ("PASS","FAIL"),
    // compared as str(answer).upper(). The first version of this screen used a
    // boolean and every save was refused with "Answer must be one of YES, NO."
    expect(VERDICT_OPTIONS.YES_NO).toEqual(['YES', 'NO'])
    expect(VERDICT_OPTIONS.PASS_FAIL).toEqual(['PASS', 'FAIL'])
    expect(VERDICT_OPTIONS.YES_NO).not.toContain(true as never)
  })

  it('knows which verdict reports a problem', () => {
    // Mirrors is_exception(): only these two types carry a wrong answer.
    expect(isNegative('YES_NO', 'NO')).toBe(true)
    expect(isNegative('PASS_FAIL', 'FAIL')).toBe(true)
    expect(isNegative('YES_NO', 'YES')).toBe(false)
    expect(isNegative('NUMBER', 0)).toBe(false)
  })
})

describe('multi-choice answers', () => {
  const multi = (over: Partial<SessionQuestion> = {}): SessionQuestion => ({
    id: 'm', question_text: 'Which doors were secure?', question_type: 'MULTI_CHOICE',
    is_required: true, options: ['North', 'South'], ...over,
  })

  it('counts an empty selection as unanswered', () => {
    // The server treats an empty list as empty, so the button must not enable.
    expect(unansweredRequired([multi()], { m: [] })).toEqual(['m'])
  })

  it('accepts a selection of one or more', () => {
    expect(unansweredRequired([multi()], { m: ['North'] })).toEqual([])
  })
})
