import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ThemeProvider } from '@mui/material'
import { render, screen, fireEvent, waitFor, within } from '@/test/utils'
import { theme } from '@/theme/glassmorphism'
import * as api from '@/api/platformAiLicense'
import type { AiLicense } from '@/api/platformAiLicense'
import { aiBodyOf, aiDraftOf, aiDraftProblem, toggled } from '@/components/platform/aiLicenseFormat'
import { AiLicensePanel } from './AiLicensePanel'

vi.mock('@/api/platformAiLicense', async (orig) => {
  const real = await orig<typeof import('@/api/platformAiLicense')>()
  return { ...real, getAiLicense: vi.fn(), putAiLicense: vi.fn() }
})

function show() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}><ThemeProvider theme={theme}><AiLicensePanel tenantId="t1" /></ThemeProvider></QueryClientProvider>,
    { wrapper: ({ children }) => <>{children}</> })
}

const CATALOGUE: AiLicense['catalogue'] = [
  { key: 'late_risk', title: 'Late and no-show warnings', group: 'learning', description: 'Shifts likely to start late.', ready: false, phase: 2 },
  { key: 'false_alarm', title: 'False-alarm learning', group: 'learning', description: 'Alerts likely to be false.', ready: false, phase: 3 },
  { key: 'assistant', title: 'AI assistant', group: 'assistant', description: 'Ask anything.', ready: false, phase: 6 },
]
const OFF: AiLicense = {
  tenant_id: 't1', tenant_name: 'Acme Security', tenant_slug: 'acme', licensed: false,
  reason: 'The AI module is not licensed for this organisation.', is_enabled: false, licensed_at: null, expires_at: null,
  features: [], assistant_provider: 'LOCAL', monthly_reply_limit: null, notes: null, updated_at: null, catalogue: CATALOGUE,
}
const ON: AiLicense = {
  ...OFF, licensed: true, reason: null, is_enabled: true, licensed_at: '2026-10-26T02:00:00Z',
  features: ['assistant', 'late_risk'], assistant_provider: 'HOSTED', monthly_reply_limit: 2000,
  updated_at: '2026-10-26T02:00:00Z',
}
const PATIENT = { timeout: 8000 }

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(api.getAiLicense).mockResolvedValue(OFF)
  vi.mocked(api.putAiLicense).mockResolvedValue(ON)
})

describe('the words of an AI licence', () => {
  it('needs a monthly limit for the hosted model, and none for the free one', () => {
    const d = aiDraftOf(OFF)
    expect(aiDraftProblem(d)).toBeNull()
    expect(aiDraftProblem({ ...d, provider: 'HOSTED' })).toMatch('set a monthly limit')
    expect(aiDraftProblem({ ...d, provider: 'HOSTED', limit: '500' })).toBeNull()
    expect(aiDraftProblem({ ...d, limit: 'many' })).toMatch('whole number')
    expect(aiBodyOf({ ...aiDraftOf(ON), notes: ' x ' })).toEqual({
      is_enabled: true, expires_at: null, features: ['assistant', 'late_risk'], assistant_provider: 'HOSTED',
      monthly_reply_limit: 2000, notes: 'x' })
    expect(toggled(['late_risk'], 'assistant')).toEqual(['assistant', 'late_risk'])
    expect(toggled(['assistant', 'late_risk'], 'assistant')).toEqual(['late_risk'])
  })
})

describe('the AI licence panel', () => {
  it('shows an organisation that is not licensed, every feature offered, and nothing to save yet', async () => {
    show()
    const panel = await screen.findByTestId('ai-license', {}, PATIENT)
    expect(api.getAiLicense).toHaveBeenCalledWith('t1')
    expect(panel).toHaveTextContent('Not licensed')
    expect(screen.getByTestId('ai-license-reason')).toHaveTextContent('not licensed for this organisation')
    for (const f of CATALOGUE) expect(screen.getByRole('checkbox', { name: f.title })).not.toBeChecked()
    expect(panel).toHaveTextContent('Coming')
    expect(screen.getByRole('button', { name: 'Save the licence' })).toBeDisabled()
  })

  it('switches it on with features, the hosted model and a limit, saved as a whole', async () => {
    show()
    await screen.findByTestId('ai-license', {}, PATIENT)
    fireEvent.click(screen.getByRole('switch'))
    fireEvent.click(screen.getByRole('checkbox', { name: 'Late and no-show warnings' }))
    fireEvent.click(screen.getByRole('checkbox', { name: 'AI assistant' }))
    fireEvent.mouseDown(screen.getByLabelText('Model'))
    fireEvent.click(within(await screen.findByRole('listbox')).getByText('Hosted (Claude)'))
    const save = screen.getByRole('button', { name: 'Save the licence' })
    expect(await screen.findByText('The hosted model is paid per reply: set a monthly limit.')).toBeInTheDocument()
    expect(save).toBeDisabled()
    fireEvent.change(screen.getByLabelText('Replies a month'), { target: { value: '2000' } })
    await waitFor(() => expect(save).toBeEnabled(), PATIENT)
    fireEvent.click(save)
    await waitFor(() => expect(api.putAiLicense).toHaveBeenCalledWith('t1', {
      is_enabled: true, expires_at: null, features: ['assistant', 'late_risk'], assistant_provider: 'HOSTED',
      monthly_reply_limit: 2000, notes: null }), PATIENT)
    expect(await screen.findByText('Saved.', {}, PATIENT)).toBeInTheDocument()
    expect(screen.getByTestId('ai-license')).toHaveTextContent('Licensed')
  })

  it('shows the platform\'s own organisation as not licensable, in the server\'s words', async () => {
    vi.mocked(api.getAiLicense).mockRejectedValue(Object.assign(new Error('x'), {
      response: { status: 422, data: { detail: 'The platform tenant does not use the AI module; license it to a customer tenant.' } } }))
    show()
    expect(await screen.findByTestId('ai-license-refused', {}, PATIENT)).toHaveTextContent('The platform tenant does not use the AI module')
    expect(screen.queryByRole('button', { name: 'Save the licence' })).not.toBeInTheDocument()
  })
})
