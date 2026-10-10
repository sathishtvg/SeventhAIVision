import userEvent from '@testing-library/user-event'
import { render, screen, waitFor } from '@/test/utils'
import Login from '@/pages/Login'
import { useAuthStore } from '@/store/auth'

vi.mock('@/store/auth', () => ({
  useAuthStore: vi.fn(),
}))

const mockLogin = vi.fn()
const mockComplete = vi.fn()

beforeEach(() => {
  mockLogin.mockReset()
  mockComplete.mockReset()
  vi.mocked(useAuthStore).mockImplementation((sel: any) =>
    sel({ login: mockLogin, completeTwoFactor: mockComplete, accessToken: null, user: null })
  )
})

const refused = (status: number, detail: unknown) =>
  Object.assign(new Error('refused'), { response: { status, data: { detail } } })

/** Fills in the credentials and presses Sign in. */
async function signIn(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText(/organisation slug/i), 'seventhaivision')
  await user.type(screen.getByLabelText(/email/i), 'owner@example.com')
  await user.type(screen.getByLabelText(/password/i), 'right-password')
  await user.click(screen.getByRole('button', { name: /^sign in$/i }))
}

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

// The platform owner's account has two-factor on, and this page had nowhere to
// put the code: a correct password was answered with "invalid credentials".
describe('Login with two-factor on', () => {
  it('asks for the authenticator code once the password is accepted, and not for the password again', async () => {
    mockLogin.mockResolvedValue('challenge-1')
    render(<Login />)
    const user = userEvent.setup()
    await signIn(user)

    expect(await screen.findByLabelText(/authenticator code/i)).toBeInTheDocument()
    expect(screen.getByText(/your password was accepted/i)).toBeInTheDocument()
    expect(screen.queryByText(/invalid credentials/i)).not.toBeInTheDocument()
    expect(screen.queryByLabelText(/password/i)).not.toBeInTheDocument()
    expect(screen.queryByLabelText(/email/i)).not.toBeInTheDocument()
    expect(mockComplete).not.toHaveBeenCalled()
  })

  it('takes six digits and nothing else, and sends them with the challenge', async () => {
    mockLogin.mockResolvedValue('challenge-1')
    mockComplete.mockResolvedValue(undefined)
    render(<Login />)
    const user = userEvent.setup()
    await signIn(user)

    const field = await screen.findByLabelText(/authenticator code/i)
    const verify = screen.getByRole('button', { name: /verify and sign in/i })
    expect(verify).toBeDisabled()
    await user.type(field, '12 34-5')
    expect(field).toHaveValue('12345')
    expect(verify).toBeDisabled()
    await user.type(field, 'x67')
    expect(field).toHaveValue('123456')
    await user.click(verify)

    await waitFor(() => expect(mockComplete).toHaveBeenCalledWith('challenge-1', '123456'))
  })

  it('says a code was not accepted and stays where the next one can be typed', async () => {
    mockLogin.mockResolvedValue('challenge-1')
    mockComplete.mockRejectedValue(refused(400, 'Invalid TOTP code'))
    render(<Login />)
    const user = userEvent.setup()
    await signIn(user)
    await user.type(await screen.findByLabelText(/authenticator code/i), '000000')
    await user.click(screen.getByRole('button', { name: /verify and sign in/i }))

    expect(await screen.findByText(/that code was not accepted/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/authenticator code/i)).toBeInTheDocument()
    expect(screen.queryByText(/invalid credentials/i)).not.toBeInTheDocument()
  })

  it('asks for the password again when the five minutes have run out', async () => {
    mockLogin.mockResolvedValue('challenge-1')
    mockComplete.mockRejectedValue(refused(401, 'Invalid or expired challenge token'))
    render(<Login />)
    const user = userEvent.setup()
    await signIn(user)
    await user.type(await screen.findByLabelText(/authenticator code/i), '123456')
    await user.click(screen.getByRole('button', { name: /verify and sign in/i }))

    expect(await screen.findByText(/longer than five minutes/i)).toBeInTheDocument()
    expect(screen.queryByLabelText(/authenticator code/i)).not.toBeInTheDocument()
    expect(screen.getByLabelText(/password/i)).toHaveValue('')
  })

  it('goes back to the credentials when asked to', async () => {
    mockLogin.mockResolvedValue('challenge-1')
    render(<Login />)
    const user = userEvent.setup()
    await signIn(user)
    await screen.findByLabelText(/authenticator code/i)
    await user.click(screen.getByRole('button', { name: /back to sign in/i }))

    expect(screen.queryByLabelText(/authenticator code/i)).not.toBeInTheDocument()
    expect(screen.getByLabelText(/email/i)).toHaveValue('owner@example.com')
    expect(screen.getByLabelText(/password/i)).toHaveValue('')
  })
})

describe('Login refused for a reason that is not the password', () => {
  it('gives the server\'s words for a locked account', async () => {
    mockLogin.mockRejectedValue(refused(403, 'Account locked. Try again after 08:18 UTC or contact your administrator.'))
    render(<Login />)
    const user = userEvent.setup()
    await signIn(user)

    expect(await screen.findByText(/account locked\. try again after 08:18 utc/i)).toBeInTheDocument()
    expect(screen.queryByText(/invalid credentials/i)).not.toBeInTheDocument()
  })

  it('says to wait when there have been too many attempts', async () => {
    mockLogin.mockRejectedValue(refused(429, { error: 'Rate limit exceeded' }))
    render(<Login />)
    const user = userEvent.setup()
    await signIn(user)

    expect(await screen.findByText(/too many sign-in attempts/i)).toBeInTheDocument()
  })
})
