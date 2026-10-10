import { renderHook } from '@testing-library/react'
import { EVERY_MS, LEAST_GAP_MS, useFreshBuild } from '@/hooks/useFreshBuild'
import { reloadIfRebuilt } from '@/lib/freshBuild'

vi.mock('@/lib/freshBuild', () => ({
  reloadIfRebuilt: vi.fn(async () => 'current'),
}))

const asked = vi.mocked(reloadIfRebuilt)
let showing: DocumentVisibilityState

/** The tab is switched to, or away from. */
function tabIs(state: DocumentVisibilityState) {
  showing = state
  document.dispatchEvent(new Event('visibilitychange'))
  if (state === 'visible') window.dispatchEvent(new Event('focus'))
}

beforeEach(() => {
  vi.useFakeTimers()
  asked.mockClear()
  showing = 'visible'
  Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => showing })
})

afterEach(() => {
  vi.useRealTimers()
})

describe('useFreshBuild', () => {
  it('asks when the page opens', () => {
    renderHook(() => useFreshBuild(true))
    expect(asked).toHaveBeenCalledTimes(1)
  })

  it('asks once when the tab is come back to, though two things say so', () => {
    renderHook(() => useFreshBuild(true))
    tabIs('hidden')
    vi.advanceTimersByTime(LEAST_GAP_MS)
    tabIs('visible')
    expect(asked).toHaveBeenCalledTimes(2)
  })

  it('does not ask again within moments of having asked', () => {
    renderHook(() => useFreshBuild(true))
    vi.advanceTimersByTime(LEAST_GAP_MS - 1)
    tabIs('hidden')
    tabIs('visible')
    expect(asked).toHaveBeenCalledTimes(1)
  })

  it('goes on asking while the page is left in front', () => {
    renderHook(() => useFreshBuild(true))
    vi.advanceTimersByTime(EVERY_MS * 3)
    expect(asked).toHaveBeenCalledTimes(4)
  })

  it('asks nothing of a tab nobody is looking at', () => {
    showing = 'hidden'
    renderHook(() => useFreshBuild(true))
    vi.advanceTimersByTime(EVERY_MS * 3)
    expect(asked).not.toHaveBeenCalled()
  })

  it('asks nothing while somebody is in the middle of something', () => {
    const { rerender } = renderHook(({ idle }) => useFreshBuild(idle), { initialProps: { idle: false } })
    vi.advanceTimersByTime(EVERY_MS)
    tabIs('hidden')
    tabIs('visible')
    expect(asked).not.toHaveBeenCalled()
    rerender({ idle: true })
    vi.advanceTimersByTime(EVERY_MS)
    expect(asked).toHaveBeenCalledTimes(1)
  })

  it('lets a reload that was about to happen see that somebody has started something', () => {
    const { rerender } = renderHook(({ idle }) => useFreshBuild(idle), { initialProps: { idle: true } })
    const mayReload = asked.mock.calls[0][0]!.mayReload!
    expect(mayReload()).toBe(true)
    rerender({ idle: false })
    expect(mayReload()).toBe(false)
  })

  it('stops when the page is left', () => {
    const { unmount } = renderHook(() => useFreshBuild(true))
    unmount()
    vi.advanceTimersByTime(EVERY_MS * 2)
    tabIs('hidden')
    tabIs('visible')
    expect(asked).toHaveBeenCalledTimes(1)
  })
})
