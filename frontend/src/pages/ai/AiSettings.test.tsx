import { render, screen, fireEvent, waitFor, within } from '@/test/utils'
import * as api from '@/api/ai'
import type { AiFeatureSetting, AiSettings as Settings } from '@/api/ai'
import { draftOfSetting, featureLock, roleToggled } from './aiSettingsFormat'
import AiSettings from './AiSettings'

vi.mock('@/api/ai', async (orig) => {
  const real = await orig<typeof import('@/api/ai')>()
  return { ...real, getAiSettings: vi.fn(), putAiSetting: vi.fn() }
})
vi.mock('@/api/sites', () => ({ getSites: vi.fn().mockResolvedValue([{ id: 's1', name: 'Jurong' }, { id: 's2', name: 'Tuas' }]) }))

const feature = (over: Partial<AiFeatureSetting>): AiFeatureSetting => ({
  key: 'late_risk', title: 'Late and no-show warnings', group: 'learning', description: 'Shifts likely to start late.',
  ready: true, phase: 2, licensed: true, allowed_roles: [2, 8, 3, 4], enabled: false, role_ids: [2, 3, 8],
  site_ids: null, sensitivity: 'MEDIUM', learning_enabled: true, updated_at: null, ...over,
})
const SETTINGS: Settings = {
  licensed: true, reason: null, expires_at: null, assistant_provider: 'LOCAL', monthly_reply_limit: null,
  features: [
    feature({}),
    feature({ key: 'false_alarm', title: 'False-alarm learning', licensed: false }),
    feature({ key: 'assistant', title: 'AI assistant', group: 'assistant', ready: false, allowed_roles: [2, 8, 3, 4, 6, 5] }),
  ],
}
const PATIENT = { timeout: 8000 }

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(api.getAiSettings).mockResolvedValue(SETTINGS)
  vi.mocked(api.putAiSetting).mockImplementation(async (key, body) => ({ ...feature({ key }), ...body }))
})

describe('the words of AI settings', () => {
  it('says why a feature cannot be switched on, and keeps roles in order', () => {
    expect(featureLock({ licensed: false, ready: true })).toBe('Not in your licence')
    expect(featureLock({ licensed: true, ready: false })).toBe('Coming in a later release')
    expect(featureLock({ licensed: true, ready: true })).toBeNull()
    expect(roleToggled([2, 8], 3)).toEqual([2, 3, 8])
    expect(roleToggled([2, 3, 8], 3)).toEqual([2, 8])
    expect(draftOfSetting(feature({ role_ids: [8, 2] })).role_ids).toEqual([2, 8])
  })
})

describe('the AI Settings page', () => {
  it('shows the licence and each feature, with what stops one being switched on', async () => {
    render(<AiSettings />)
    expect(await screen.findByTestId('ai-licence-summary', {}, PATIENT)).toHaveTextContent('free built-in')
    expect(screen.getByTestId('ai-feature-false_alarm')).toHaveTextContent('Not in your licence')
    expect(screen.getByTestId('ai-feature-assistant')).toHaveTextContent('Coming in a later release')
    expect(screen.getByRole('switch', { name: 'False-alarm learning on or off' })).toBeDisabled()
    expect(screen.getByRole('switch', { name: 'Late and no-show warnings on or off' })).toBeEnabled()
  })

  it('switches a licensed feature on for chosen roles and saves it on its own', async () => {
    render(<AiSettings />)
    const card = await screen.findByTestId('ai-feature-late_risk', {}, PATIENT)
    fireEvent.click(screen.getByRole('switch', { name: 'Late and no-show warnings on or off' }))
    fireEvent.click(within(card).getByRole('checkbox', { name: 'Admin' }))
    const save = within(card).getByRole('button', { name: 'Save' })
    await waitFor(() => expect(save).toBeEnabled(), PATIENT)
    fireEvent.click(save)
    await waitFor(() => expect(api.putAiSetting).toHaveBeenCalledWith('late_risk', {
      enabled: true, role_ids: [3, 8], site_ids: null, sensitivity: 'MEDIUM', learning_enabled: true }), PATIENT)
    expect(await screen.findByText('Saved.', {}, PATIENT)).toBeInTheDocument()
  })

  it('says when the organisation is not licensed, in the server\'s words', async () => {
    vi.mocked(api.getAiSettings).mockResolvedValue({ ...SETTINGS, licensed: false,
      reason: 'The AI module is not licensed for this organisation. Ask your platform administrator to enable it.' })
    render(<AiSettings />)
    expect(await screen.findByTestId('ai-licence-summary', {}, PATIENT)).toHaveTextContent('Ask your platform administrator')
  })
})
