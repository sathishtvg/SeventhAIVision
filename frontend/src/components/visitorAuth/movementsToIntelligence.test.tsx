import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ThemeProvider } from '@mui/material'
import { render, screen, fireEvent, waitFor } from '@/test/utils'
import { theme } from '@/theme/glassmorphism'
import { useAuthStore } from '@/store/auth'
import { getSettings, upsertSetting } from '@/api/settings'
import { MOVEMENTS_SETTING, MovementsToIntelligence } from './MovementsToIntelligence'

vi.mock('@/store/auth', () => ({ useAuthStore: vi.fn() }))
vi.mock('@/api/settings', () => ({ getSettings: vi.fn(), upsertSetting: vi.fn() }))

function show() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}><ThemeProvider theme={theme}><MovementsToIntelligence /></ThemeProvider></QueryClientProvider>,
    { wrapper: ({ children }) => <>{children}</> })
}
function signIn(roleId: number) {
  vi.mocked(useAuthStore).mockImplementation(((sel: (s: unknown) => unknown) =>
    sel({ user: { id: 'me', tenantId: 't1', roleId }, accessToken: 'tok', permissions: null })) as never)
}
const setting = (value: unknown) => [{ setting_key: MOVEMENTS_SETTING, setting_value: value, updated_by_user_id: 'u', updated_at: '2026-10-09T02:00:00Z' }]
const PATIENT = { timeout: 8000 }

beforeEach(() => {
  vi.clearAllMocks()
  signIn(2)
  vi.mocked(getSettings).mockResolvedValue([] as never)
  vi.mocked(upsertSetting).mockResolvedValue({} as never)
})

describe('handing door events to Security Intelligence', () => {
  it('is off until somebody switches it on, and says what is handed over', async () => {
    show()
    const card = await screen.findByTestId('movements-to-intelligence', {}, PATIENT)
    expect(MOVEMENTS_SETTING).toBe('visitor.movements_to_intelligence')
    expect(card).toHaveTextContent('Door events outside an authorisation are not handed to Security Intelligence')
    expect(card).toHaveTextContent('The event names nobody')
    expect(card).toHaveTextContent('It is something to look at, not a finding')
    expect(screen.getByRole('switch')).not.toBeChecked()
    fireEvent.click(screen.getByRole('switch'))
    await waitFor(() => expect(upsertSetting).toHaveBeenCalledWith(MOVEMENTS_SETTING, true), PATIENT)
  })

  it('shows it on, and switches it off again', async () => {
    vi.mocked(getSettings).mockResolvedValue(setting(true) as never)
    show()
    const card = await screen.findByTestId('movements-to-intelligence', {}, PATIENT)
    expect(card).toHaveTextContent('Door events outside an authorisation are handed to Security Intelligence')
    expect(screen.getByRole('switch')).toBeChecked()
    fireEvent.click(screen.getByRole('switch'))
    await waitFor(() => expect(upsertSetting).toHaveBeenCalledWith(MOVEMENTS_SETTING, false), PATIENT)
  })

  it('is read by a supervisor, who cannot switch it, and is not there for somebody who may not read settings', async () => {
    signIn(3)
    const first = show()
    await screen.findByTestId('movements-to-intelligence', {}, PATIENT)
    expect(screen.getByRole('switch')).toBeDisabled()
    first.unmount()
    signIn(4)
    show()
    await new Promise((r) => setTimeout(r, 50))
    expect(screen.queryByTestId('movements-to-intelligence')).not.toBeInTheDocument()
    expect(getSettings).toHaveBeenCalledTimes(1)
  })
})
