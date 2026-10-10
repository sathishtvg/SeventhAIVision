import userEvent from '@testing-library/user-event'
import { render, screen, waitFor } from '@/test/utils'
import Login from '@/pages/Login'
import { useAuthStore } from '@/store/auth'
import { useFreshBuild } from '@/hooks/useFreshBuild'

// The sign-in page is the one page a tab can sit on for days, and the one where
// an old build cost a sign-in (10 October 2026). It keeps itself the build the
// server has - but never by reloading under somebody who is part-way through.

vi.mock('@/store/auth', () => ({
  useAuthStore: vi.fn(),
}))
vi.mock('@/hooks/useFreshBuild', () => ({
  useFreshBuild: vi.fn(),
}))

const mockLogin = vi.fn()
const idle = () => vi.mocked(useFreshBuild).mock.lastCall?.[0]

beforeEach(() => {
  mockLogin.mockReset()
  vi.mocked(useFreshBuild).mockClear()
  vi.mocked(useAuthStore).mockImplementation(((sel: (state: object) => unknown) =>
    sel({ login: mockLogin, completeTwoFactor: vi.fn(), accessToken: null, user: null })
  ) as never)
})

async function signIn(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText(/organisation slug/i), 'seventhaivision')
  await user.type(screen.getByLabelText(/email/i), 'owner@example.com')
  await user.type(screen.getByLabelText(/password/i), 'right-password')
  await user.click(screen.getByRole('button', { name: /^sign in$/i }))
}

describe('Login keeps itself the build the server has', () => {
  it('may be reloaded while nobody has started anything', () => {
    render(<Login />)
    expect(idle()).toBe(true)
  })

  it('may not while the sign-in is on its way', async () => {
    mockLogin.mockImplementation(() => new Promise<void>(() => {})) // never answers
    render(<Login />)
    await signIn(userEvent.setup())
    await waitFor(() => expect(idle()).toBe(false))
  })

  it('may not while the authenticator code is being asked for: a reload would throw the accepted password away', async () => {
    mockLogin.mockResolvedValue('challenge-token')
    render(<Login />)
    await signIn(userEvent.setup())
    expect(await screen.findByLabelText(/authenticator code/i)).toBeInTheDocument()
    expect(idle()).toBe(false)
  })

  it('may again once a refused sign-in has been answered', async () => {
    mockLogin.mockRejectedValue(new Error('Unauthorized'))
    render(<Login />)
    await signIn(userEvent.setup())
    expect(await screen.findByText(/invalid credentials/i)).toBeInTheDocument()
    expect(idle()).toBe(true)
  })
})
