import userEvent from '@testing-library/user-event'
import { render, screen, waitFor } from '@/test/utils'
import Login from '@/pages/Login'
import { useAuthStore } from '@/store/auth'

vi.mock('@/store/auth', () => ({
  useAuthStore: vi.fn(),
}))

const mockLogin = vi.fn()

beforeEach(() => {
  mockLogin.mockReset()
  vi.mocked(useAuthStore).mockImplementation((sel: any) =>
    sel({ login: mockLogin, accessToken: null, user: null })
  )
})

describe('Login', () => {
  it('renders all form fields and the sign-in button', () => {
    render(<Login />)
    expect(screen.getByLabelText(/organisation slug/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/email/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/password/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /sign in/i })).toBeInTheDocument()
  })

  it('shows error alert when login fails', async () => {
    mockLogin.mockRejectedValue(new Error('Unauthorized'))
    render(<Login />)

    const user = userEvent.setup()
    await user.type(screen.getByLabelText(/organisation slug/i), 'acme')
    await user.type(screen.getByLabelText(/email/i), 'admin@example.com')
    await user.type(screen.getByLabelText(/password/i), 'wrongpass')
    await user.click(screen.getByRole('button', { name: /sign in/i }))

    expect(await screen.findByText(/invalid credentials/i)).toBeInTheDocument()
  })

  it('disables the button while the login request is in flight', async () => {
    mockLogin.mockImplementation(() => new Promise<void>(() => {})) // never resolves
    render(<Login />)

    const user = userEvent.setup()
    await user.type(screen.getByLabelText(/organisation slug/i), 'acme')
    await user.type(screen.getByLabelText(/email/i), 'admin@example.com')
    await user.type(screen.getByLabelText(/password/i), 'pass')

    // Save reference before click: once login is in-flight the button shows
    // CircularProgress, so its accessible name changes and a new getByRole query
    // for /sign in/ would fail. The DOM node itself is the same and becomes disabled.
    const submitBtn = screen.getByRole('button', { name: /sign in/i })
    await user.click(submitBtn)

    await waitFor(() => {
      expect(submitBtn).toBeDisabled()
    })
  })

  it('calls login with trimmed slug and email but raw password', async () => {
    mockLogin.mockResolvedValue(undefined)
    render(<Login />)

    const user = userEvent.setup()
    await user.type(screen.getByLabelText(/organisation slug/i), '  acme  ')
    await user.type(screen.getByLabelText(/email/i), '  admin@acme.com  ')
    await user.type(screen.getByLabelText(/password/i), 'pass123')
    await user.click(screen.getByRole('button', { name: /sign in/i }))

    await waitFor(() => {
      expect(mockLogin).toHaveBeenCalledWith('acme', 'admin@acme.com', 'pass123')
    })
  })

  it('clears the error message on a new submit attempt', async () => {
    mockLogin.mockRejectedValueOnce(new Error('fail')).mockResolvedValue(undefined)
    render(<Login />)

    const user = userEvent.setup()
    await user.type(screen.getByLabelText(/organisation slug/i), 'acme')
    await user.type(screen.getByLabelText(/email/i), 'a@b.com')
    await user.type(screen.getByLabelText(/password/i), 'bad')
    await user.click(screen.getByRole('button', { name: /sign in/i }))

    expect(await screen.findByText(/invalid credentials/i)).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /sign in/i }))
    // Error disappears while the second request is in flight
    await waitFor(() => {
      expect(screen.queryByText(/invalid credentials/i)).not.toBeInTheDocument()
    })
  })
})
