import { act, render, screen, fireEvent } from '@/test/utils'
import { HlsPlayer } from '@/components/common/HlsPlayer'

// What a video tile says when it is not showing a picture - and that it stops saying it the moment it is.

const fake = vi.hoisted(() => {
  type Handler = (name: string, data: unknown) => void
  const made: FakeHls[] = []
  class FakeHls {
    static supported = true
    static isSupported() { return FakeHls.supported }
    static Events = { MANIFEST_PARSED: 'manifestParsed', ERROR: 'error' }
    static ErrorTypes = { NETWORK_ERROR: 'networkError', MEDIA_ERROR: 'mediaError', OTHER_ERROR: 'otherError' }
    handlers: Record<string, Handler> = {}
    loadSource = vi.fn()
    attachMedia = vi.fn()
    startLoad = vi.fn()
    recoverMediaError = vi.fn()
    destroy = vi.fn()
    constructor() { made.push(this) }
    on(name: string, handler: Handler) { this.handlers[name] = handler }
    emit(name: string, data: unknown) { this.handlers[name]?.(name, data) }
  }
  return { FakeHls, made }
})
vi.mock('hls.js', () => ({ default: fake.FakeHls }))

const video = (container: HTMLElement) => container.querySelector('video') as HTMLVideoElement
const happens = (el: HTMLElement, name: string) => fireEvent(el, new Event(name))
const fatal = (type: string) => ({ fatal: true, type })
const said = () => screen.queryByRole('status')?.textContent ?? screen.queryByRole('alert')?.textContent ?? null

beforeEach(() => {
  fake.made.length = 0
  fake.FakeHls.supported = true
  // jsdom has no player: play() is not there to be called.
  vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue(undefined)
})
afterEach(() => vi.restoreAllMocks())

describe('HlsPlayer', () => {
  it('says "Connecting" until there is a first frame, and nothing once there is', () => {
    const { container } = render(<HlsPlayer src="/a.m3u8" alt="Gate 2" />)
    expect(screen.getByRole('status')).toHaveTextContent('Connecting')
    expect(video(container)).toHaveAttribute('aria-label', 'Gate 2')
    expect(fake.made[0].loadSource).toHaveBeenCalledWith('/a.m3u8')

    // Waiting for data before there has been a picture is still connecting.
    happens(video(container), 'waiting')
    expect(said()).toBe('Connecting')

    happens(video(container), 'loadeddata')
    expect(said()).toBeNull()
  })

  it('says "Buffering" when a picture that was playing stops for more, and takes it away when it plays', () => {
    const { container } = render(<HlsPlayer src="/a.m3u8" />)
    happens(video(container), 'playing')
    expect(said()).toBeNull()
    happens(video(container), 'waiting')
    expect(screen.getByRole('status')).toHaveTextContent('Buffering')
    happens(video(container), 'playing')
    expect(said()).toBeNull()
  })

  it('says there is no picture when hls.js gives up, goes on trying as it did, and clears when the stream returns', () => {
    const { container } = render(<HlsPlayer src="/a.m3u8" />)
    const hls = fake.made[0]
    act(() => { hls.emit('error', { fatal: false, type: 'networkError' }) })
    expect(said()).toBe('Connecting')

    act(() => { hls.emit('error', fatal('networkError')) })
    expect(screen.getByRole('alert')).toHaveTextContent('No picture')
    expect(hls.startLoad).toHaveBeenCalledTimes(1)
    expect(hls.destroy).not.toHaveBeenCalled()

    happens(video(container), 'playing')
    expect(said()).toBeNull()
  })

  it('recovers a media error as before, without calling it a failure', () => {
    render(<HlsPlayer src="/a.m3u8" />)
    act(() => { fake.made[0].emit('error', fatal('mediaError')) })
    expect(fake.made[0].recoverMediaError).toHaveBeenCalledTimes(1)
    expect(said()).toBe('Connecting')
  })

  it('starts the stream afresh when asked to try again', () => {
    render(<HlsPlayer src="/a.m3u8" />)
    act(() => { fake.made[0].emit('error', fatal('otherError')) })
    expect(fake.made[0].destroy).toHaveBeenCalled()
    expect(screen.getByRole('alert')).toHaveTextContent('No picture')

    fireEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(fake.made).toHaveLength(2)
    expect(fake.made[1].loadSource).toHaveBeenCalledWith('/a.m3u8')
    expect(said()).toBe('Connecting')
  })

  it('is connecting again when it is given another stream', () => {
    const { container, rerender } = render(<HlsPlayer src="/a.m3u8" />)
    happens(video(container), 'playing')
    expect(said()).toBeNull()

    rerender(<HlsPlayer src="/b.m3u8" />)
    expect(said()).toBe('Connecting')
    expect(fake.made[0].destroy).toHaveBeenCalled()
    expect(fake.made[1].loadSource).toHaveBeenCalledWith('/b.m3u8')
  })

  it('plays through the browser where the browser can, and says so when that fails', () => {
    fake.FakeHls.supported = false
    vi.spyOn(HTMLMediaElement.prototype, 'canPlayType').mockReturnValue('maybe')
    const { container } = render(<HlsPlayer src="/a.m3u8" />)
    expect(fake.made).toHaveLength(0)
    expect(video(container).src).toContain('/a.m3u8')
    expect(said()).toBe('Connecting')

    happens(video(container), 'error')
    expect(screen.getByRole('alert')).toHaveTextContent('No picture')
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument()
  })

  it('says a browser that cannot play the stream cannot, and offers nothing that would not help', () => {
    fake.FakeHls.supported = false
    vi.spyOn(HTMLMediaElement.prototype, 'canPlayType').mockReturnValue('')
    render(<HlsPlayer src="/a.m3u8" />)
    expect(screen.getByRole('alert')).toHaveTextContent('No picture')
    expect(screen.getByRole('alert')).toHaveTextContent('This browser cannot play this stream')
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })
})
