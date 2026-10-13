import { act, render, screen, fireEvent } from '@/test/utils'
import { LiveImage } from '@/components/common/LiveImage'

// The frame-by-frame live view: an empty tile says why it is empty, and never says so over a picture.

const said = () => screen.queryByRole('status')?.textContent ?? screen.queryByRole('alert')?.textContent ?? null

afterEach(() => vi.useRealTimers())

describe('LiveImage', () => {
  it('says "Connecting" until the first frame, and nothing after it', () => {
    render(<LiveImage src="/live?x=1" alt="Gate 2" />)
    expect(screen.getByRole('status')).toHaveTextContent('Connecting')
    fireEvent.load(screen.getByAltText('Gate 2'))
    expect(said()).toBeNull()
  })

  it('finds the first frame by itself where the browser never says a stream has loaded', () => {
    vi.useFakeTimers()
    render(<LiveImage src="/live?x=1" alt="Gate 2" />)
    act(() => { vi.advanceTimersByTime(2000) })
    expect(said()).toBe('Connecting')

    Object.defineProperty(screen.getByAltText('Gate 2'), 'naturalWidth', { configurable: true, value: 1280 })
    act(() => { vi.advanceTimersByTime(400) })
    expect(said()).toBeNull()
    // Found, it is looked for no more.
    expect(vi.getTimerCount()).toBe(0)
  })

  it('says there is no picture when the stream could not be opened, and opens it afresh when asked', () => {
    render(<LiveImage src="/live?x=1" alt="Gate 2" />)
    const first = screen.getByAltText('Gate 2')
    fireEvent.error(first)
    expect(screen.getByRole('alert')).toHaveTextContent('No picture')

    fireEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(said()).toBe('Connecting')
    // A new element: the browser opens a new connection to the same address.
    expect(screen.getByAltText('Gate 2')).not.toBe(first)
    expect(screen.getByAltText('Gate 2')).toHaveAttribute('src', '/live?x=1')
  })

  it('shows a new address in the same element, so a wall does not blink when the token is renewed', () => {
    const { rerender } = render(<LiveImage src="/live?token=one" alt="Gate 2" />)
    const first = screen.getByAltText('Gate 2')
    fireEvent.load(first)

    rerender(<LiveImage src="/live?token=two" alt="Gate 2" />)
    expect(screen.getByAltText('Gate 2')).toBe(first)
    expect(first).toHaveAttribute('src', '/live?token=two')
    expect(said()).toBeNull()

    // And if the new one cannot be opened, that is said.
    fireEvent.error(first)
    expect(screen.getByRole('alert')).toHaveTextContent('No picture')
  })
})
