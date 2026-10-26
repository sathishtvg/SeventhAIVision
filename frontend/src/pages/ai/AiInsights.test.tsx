import { render, screen, fireEvent, waitFor, within } from '@/test/utils'
import * as api from '@/api/ai'
import type { AiInsight, AiLearning } from '@/api/ai'
import { useAuthStore } from '@/store/auth'
import { barWords, howSure, rankWords, statusWords } from './aiInsightsFormat'
import AiInsights from './AiInsights'

vi.mock('@/api/ai', async (orig) => {
  const real = await orig<typeof import('@/api/ai')>()
  return { ...real, getAiInsights: vi.fn(), decideAiInsight: vi.fn(), getAiLearning: vi.fn(), runAiLearning: vi.fn() }
})

const LATE: AiInsight = {
  id: 'w1', feature_key: 'late_risk', feature_title: 'Late and no-show warnings', subject_type: 'shift',
  title: 'Late Lim may be late or absent for the 22:00 shift on Wed 10 Jun at Jurong',
  detail: 'Consider lining up a standby or calling the guard ahead of the shift.', score: 0.71, threshold: 0.4,
  reasons: [{ factor: 'guard_late_rate', words: 'late or absent for 8 of their last 10 shifts', push: 1.9 }],
  evidence: {}, site_id: 's1', site_name: 'Jurong', person_user_id: 'g1', person_name: 'Late Lim', status: 'OPEN',
  decided_by_name: null, decided_at: null, decision_note: null, outcome: null, valid_until: '2026-06-10T14:00:00Z',
  created_at: '2026-06-10T00:00:00Z', updated_at: '2026-06-10T00:00:00Z',
}
const LEARNING: AiLearning = {
  key: 'late_risk', title: 'Late and no-show warnings', description: 'Shifts likely to start late.', sensitivity: 'MEDIUM',
  learning_enabled: true, threshold: 0.4, threshold_reason: 'Set by the sensitivity (Medium); not enough decisions yet to adjust it (0 of 8).',
  model: { status: 'READY', version: 3, trained_at: '2026-06-10T00:00:00Z', rows_used: 48, positives: 10,
           metrics: { auc_on_newest: 0.86 }, note: 'Learned from 48 finished shifts, 10 of them late or missed.' },
  learned: [{ factor: 'guard_late_rate', title: "The guard's own recent lateness", weight: 1.4, raises: true },
            { factor: 'rest_hours', title: 'Rest since the previous shift', weight: -0.4, raises: false }],
  last_30_days: { open: 1, accepted: 4, dismissed: 1, came_true: 3, did_not: 1 },
}
const PATIENT = { timeout: 8000 }

beforeEach(() => {
  vi.clearAllMocks()
  useAuthStore.setState({ permissions: null, user: { id: 'u1', tenantId: 't1', roleId: 3 } as never })
  vi.mocked(api.getAiInsights).mockResolvedValue({ features: ['late_risk', 'overtime_fatigue'], items: [LATE] })
  vi.mocked(api.getAiLearning).mockResolvedValue({ features: [LEARNING] })
  vi.mocked(api.decideAiInsight).mockResolvedValue({ ...LATE, status: 'ACCEPTED' })
})

describe('the words of AI insights', () => {
  it('says how sure each kind of warning is, and where the bar is, on its own scale', () => {
    expect(howSure({ subject_type: 'shift', score: 0.714 })).toBe('71% chance')
    expect(howSure({ subject_type: 'guard_month', score: 1.05 })).toBe('105% of the overtime limit')
    expect(howSure({ subject_type: 'guard_week', score: 1 })).toBe('By your roster rules')
    expect(barWords({ key: 'late_risk', threshold: 0.4 })).toBe('Warns at a 40% chance or more')
    expect(barWords({ key: 'overtime_fatigue', threshold: 0.9 })).toBe('Warns at 90% of the monthly overtime limit')
    expect(statusWords({ status: 'ACCEPTED', outcome: 'HAPPENED' })).toBe('Accepted · it came true')
    expect(statusWords({ status: 'EXPIRED', outcome: null })).toBe('Expired')
    expect(rankWords(null)).toBeNull()
    expect(rankWords(0.86)).toMatch('ranked what happened first well (0.86')
    expect(howSure({ subject_type: 'alert', score: 0.81 })).toBe('81% chance it is false')
    expect(howSure({ subject_type: 'camera_month', score: 0.8 })).toBe('80% of its alerts were false')
    expect(howSure({ subject_type: 'device_week', score: 0.63 })).toBe('63% chance')
    expect(howSure({ subject_type: 'site_month', score: 0.3 })).toBe('30 points more problems than patrols')
    expect(howSure({ subject_type: 'guard_trend', score: 0.31 })).toBe('A clear drop')
    expect(barWords({ key: 'patrol_timing', threshold: 0.25 })).toBe('Warns at a gap of 25 points between problems and patrols')
    expect(barWords({ key: 'guard_trends', threshold: 0.196 })).toMatch('(test 1.96)')
    expect(rankWords(0.55)).toMatch('little better than guessing')
  })
})

describe('the AI Insights page', () => {
  it('shows each warning with how sure it is and why, and what each feature has learned', async () => {
    render(<AiInsights />)
    const card = await screen.findByTestId('ai-warning-w1', {}, PATIENT)
    expect(card).toHaveTextContent('Late Lim may be late or absent')
    expect(card).toHaveTextContent('71% chance')
    expect(card).toHaveTextContent('late or absent for 8 of their last 10 shifts')
    const learned = await screen.findByTestId('ai-learning-late_risk', {}, PATIENT)
    expect(learned).toHaveTextContent('Learned from 48 finished shifts')
    expect(learned).toHaveTextContent('Warns at a 40% chance or more')
    expect(learned).toHaveTextContent("The guard's own recent lateness (raises the chance)")
    expect(learned).toHaveTextContent('Rest since the previous shift (lowers the chance)')
    expect(learned).toHaveTextContent('4 accepted, 1 dismissed, 3 came true')
    expect(within(learned).queryByRole('button', { name: 'Learn now' })).not.toBeInTheDocument()
  })

  it('accepts a warning with a note, and the decision is sent as it was made', async () => {
    render(<AiInsights />)
    const card = await screen.findByTestId('ai-warning-w1', {}, PATIENT)
    fireEvent.change(within(card).getByLabelText('Note (optional)'), { target: { value: ' Standby lined up ' } })
    fireEvent.click(within(card).getByRole('button', { name: 'Accept' }))
    await waitFor(() => expect(api.decideAiInsight).toHaveBeenCalledWith('w1', { decision: 'ACCEPT', note: 'Standby lined up' }), PATIENT)
  })

  it('tells a role nobody chose that its administrator decides who sees them', async () => {
    vi.mocked(api.getAiInsights).mockResolvedValue({ features: [], items: [] })
    vi.mocked(api.getAiLearning).mockResolvedValue({ features: [] })
    render(<AiInsights />)
    expect(await screen.findByText(/No AI feature is switched on for your role/, {}, PATIENT)).toBeInTheDocument()
  })

  it('offers Learn now to an administrator', async () => {
    useAuthStore.setState({ permissions: null, user: { id: 'u1', tenantId: 't1', roleId: 2 } as never })
    vi.mocked(api.runAiLearning).mockResolvedValue({ model: 'READY' })
    render(<AiInsights />)
    const learned = await screen.findByTestId('ai-learning-late_risk', {}, PATIENT)
    fireEvent.click(within(learned).getByRole('button', { name: 'Learn now' }))
    await waitFor(() => expect(api.runAiLearning).toHaveBeenCalledWith('late_risk'), PATIENT)
  })
})
