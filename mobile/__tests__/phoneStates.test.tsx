/**
 * What a phone screen shows when it is not simply showing its content - the
 * phone's copies of the web's loading, failed, refreshing, arrived and done
 * states. These hold:
 *
 *   - a failure says which kind it was and can be asked again; "not there" is
 *     an answer, not a failure;
 *   - a placeholder says it is busy, and appears only after a moment;
 *   - a row that arrived is marked, and the first rows a list shows never are;
 *   - a camera's view says "Connecting" or "No picture", and its page asks for
 *     the address it was given and nothing else;
 *   - "done" is said only for as long as it is true, and a figure that counts
 *     up gives a screen reader the figure itself;
 *   - nothing moves on a phone that asked for less motion, or before that is
 *     known.
 */
import React from 'react'
import { AccessibilityInfo, Text } from 'react-native'
import { act, fireEvent, render, renderHook } from '@testing-library/react-native'

import {
  AnimatedNumber, DetailSkeleton, ErrorState, FadeInView, ListSkeleton, NewMark, PICTURE_WATCH_SCRIPT, RefreshingLine,
  StatsSkeleton, SuccessTick, TILE_STATE_HTML, TILE_WATCH_SCRIPT, VideoLoadingState, errorText, isNotFound,
  pictureNews, tileStateCss, useBriefWord,
} from '@/components/states'
import { useMotionStore } from '@/motion/motionStore'
import { NEW_ITEM_MS, countOf, toneOf, useNewItems } from '@/motion/useNewItems'

const network = Object.assign(new Error('Network Error'), { code: 'ERR_NETWORK' })
const status = (code: number, detail?: unknown) =>
  Object.assign(new Error(`Request failed with status code ${code}`), { response: { status: code, data: { detail } } })

afterEach(() => {
  act(() => { useMotionStore.setState({ reduced: null, wanted: null }) })
  jest.useRealTimers()
})

describe('a failure', () => {
  it('is said in words that tell a broken connection from a refusal and from a server fault', () => {
    expect(errorText(network)).toBe('The server could not be reached. Check the connection.')
    expect(errorText(Object.assign(new Error('timeout of 15000ms exceeded'), { code: 'ECONNABORTED' })))
      .toBe('The server took too long to answer.')
    expect(errorText(status(429))).toMatch(/Too many requests/)
    expect(errorText(status(500, 'Traceback (most recent call last)'))).not.toMatch(/Traceback/)
    expect(errorText(status(403, 'Your role cannot see this site.'))).toBe('Your role cannot see this site.')
  })

  it('"not there" is the server answering, and is not taken for a failure', () => {
    expect(isNotFound(status(404))).toBe(true)
    expect(isNotFound(status(500))).toBe(false)
    expect(isNotFound(network)).toBe(false)
    expect(isNotFound(undefined)).toBe(false)
  })

  it('is shown with its reason, is told to a screen reader, and can be asked again', () => {
    const retry = jest.fn()
    const s = render(<ErrorState title="Alerts could not be loaded" error={network} onRetry={retry} />)
    expect(s.getByText('Alerts could not be loaded')).toBeTruthy()
    expect(s.getByText('The server could not be reached. Check the connection.')).toBeTruthy()
    expect(s.getByTestId('error-state').props.accessibilityRole).toBe('alert')
    fireEvent.press(s.getByText('Try again'))
    expect(retry).toHaveBeenCalledTimes(1)
  })

  it('a screen with its own words for the failure keeps them', () => {
    const s = render(<ErrorState error={status(403)} words="Your role cannot see AI situations." />)
    expect(s.getByText('Your role cannot see AI situations.')).toBeTruthy()
    // Nothing to ask again from here: no button that would do nothing.
    expect(s.queryByText('Try again')).toBeNull()
  })
})

describe('a placeholder', () => {
  it('is shaped like the screen it stands for and says it is busy', () => {
    for (const shape of [<ListSkeleton key="l" />, <DetailSkeleton key="d" />, <StatsSkeleton key="s" />]) {
      const s = render(shape)
      const group = s.getByTestId('skeleton', { includeHiddenElements: true })
      expect(group.props.accessibilityState).toEqual(expect.objectContaining({ busy: true }))
      s.unmount()
    }
  })
})

describe('what arrives in a list', () => {
  type Row = { id: string }
  const rows = (...ids: string[]): Row[] => ids.map((id) => ({ id }))

  it('the first rows are never new; a row that arrives afterwards is, for a few seconds, and is said', () => {
    jest.useFakeTimers()
    const said = jest.spyOn(AccessibilityInfo, 'announceForAccessibility').mockImplementation(() => undefined)
    const { result, rerender } = renderHook(
      ({ items }: { items: Row[] }) => useNewItems(items, (r) => r.id, { say: (n) => countOf(n, 'new alert') }),
      { initialProps: { items: rows('a', 'b') } },
    )
    expect(result.current.size).toBe(0)
    rerender({ items: rows('c', 'a', 'b') })
    expect([...result.current]).toEqual(['c'])
    expect(said).toHaveBeenCalledWith('1 new alert')
    act(() => { jest.advanceTimersByTime(NEW_ITEM_MS + 1) })
    expect(result.current.size).toBe(0)
    said.mockRestore()
  })

  it('a different filter is a different list, and rows kept from the last one are not compared', () => {
    const { result, rerender } = renderHook(
      ({ items, scope, settled }: { items: Row[]; scope: string; settled: boolean }) =>
        useNewItems(items, (r) => r.id, { scope, settled }),
      { initialProps: { items: rows('a'), scope: 'open', settled: true } },
    )
    // The last list's rows, kept while the other is fetched: nothing is new.
    rerender({ items: rows('a'), scope: 'closed', settled: false })
    expect(result.current.size).toBe(0)
    // The other list arrives: its first rows are not new either.
    rerender({ items: rows('x', 'y'), scope: 'closed', settled: true })
    expect(result.current.size).toBe(0)
  })

  it('is marked in the colour of how serious it is, and only while it is new', () => {
    expect(toneOf('CRITICAL')).toBe('critical')
    expect(toneOf('p2')).toBe('high')
    expect(toneOf(undefined)).toBe('info')
    const s = render(<NewMark isNew tone="critical"><Text>Fence breach</Text></NewMark>)
    expect(s.getByTestId('new-mark')).toBeTruthy()
    s.rerender(<NewMark isNew={false} tone="critical"><Text>Fence breach</Text></NewMark>)
    expect(s.queryByTestId('new-mark')).toBeNull()
    expect(s.getByText('Fence breach')).toBeTruthy()
  })

  it('a list kept on screen while the next is fetched is said to be refreshing', () => {
    const s = render(<RefreshingLine />)
    const line = s.getByTestId('refreshing-line')
    expect(line.props.accessibilityLabel).toBe('Refreshing the list')
    expect(line.props.accessibilityState).toEqual({ busy: true })
  })
})

describe("a camera's view", () => {
  it('says what it is doing, and only a failed view offers to try again', () => {
    const retry = jest.fn()
    const s = render(<VideoLoadingState state="connecting" />)
    expect(s.getByText('Connecting')).toBeTruthy()
    expect(s.queryByText('Try again')).toBeNull()
    s.rerender(<VideoLoadingState state="failed" detail="Check the camera's connection." onRetry={retry} />)
    expect(s.getByText('No picture')).toBeTruthy()
    expect(s.getByText("Check the camera's connection.")).toBeTruthy()
    fireEvent.press(s.getByText('Try again'))
    expect(retry).toHaveBeenCalledTimes(1)
  })

  it('its page tells the screen only "playing" or "failed", and anything else it hears is ignored', () => {
    expect(pictureNews('playing')).toBe('playing')
    expect(pictureNews('failed')).toBe('failed')
    expect(pictureNews('navigate:https://elsewhere.example')).toBeNull()
    expect(pictureNews(undefined)).toBeNull()
  })

  it('its page looks only at whether the picture is there - it asks for no other address and reads no pixel', () => {
    for (const script of [PICTURE_WATCH_SCRIPT, TILE_WATCH_SCRIPT, TILE_STATE_HTML]) {
      expect(script).not.toMatch(/fetch\(|XMLHttpRequest|WebSocket|getImageData|canvas|https?:\/\//)
    }
    // "Try again" on a tile asks for the tile's own address again.
    expect(TILE_WATCH_SCRIPT).toContain("fresh.src = old.getAttribute('src')")
    // A failure that came before the page's script ran is still found.
    expect(PICTURE_WATCH_SCRIPT).toContain('img.complete')
    expect(TILE_WATCH_SCRIPT).toContain('img.complete')
  })

  it('the ring in a connecting tile turns only on a phone that has not asked for less motion', () => {
    expect(tileStateCss(false)).toContain('animation: turn')
    expect(tileStateCss(true)).toContain('animation: none')
  })
})

describe('"done"', () => {
  it('is said for a few seconds and then goes by itself; saying it again shows it again', () => {
    jest.useFakeTimers()
    const { result } = renderHook(() => useBriefWord(3000))
    expect(result.current[0]).toBeNull()
    act(() => { result.current[1]('Checkpoint recorded') })
    expect(result.current[0]).toBe('Checkpoint recorded')
    act(() => { jest.advanceTimersByTime(2000) })
    act(() => { result.current[1]('Checkpoint recorded') })
    act(() => { jest.advanceTimersByTime(2000) })
    // The second saying is shown for its own three seconds.
    expect(result.current[0]).toBe('Checkpoint recorded')
    act(() => { jest.advanceTimersByTime(1001) })
    expect(result.current[0]).toBeNull()
  })

  it('is shown only while it is true, and is told to a screen reader', () => {
    const s = render(<SuccessTick show={false} label="Snapshot saved" />)
    expect(s.queryByTestId('success-tick')).toBeNull()
    s.rerender(<SuccessTick show label="Snapshot saved" />)
    expect(s.getByTestId('success-tick').props.accessibilityLabel).toBe('Snapshot saved')
  })
})

describe('figures and cards', () => {
  it('a figure gives a screen reader the value itself, never a number on its way', () => {
    act(() => { useMotionStore.setState({ reduced: false, wanted: true }) })
    const s = render(<AnimatedNumber value={1234} format={(n) => String(n)} />)
    const figure = s.getByLabelText('1234')
    expect(figure).toBeTruthy()
  })

  it('on a phone that asked for less motion - or before that is known - the figure is simply there', () => {
    const s = render(<AnimatedNumber value={42} />)
    expect(s.getByText('42')).toBeTruthy()
  })

  it('a card is there at once on a still phone, and shows what it holds', () => {
    const s = render(<FadeInView index={3}><Text>Open alerts</Text></FadeInView>)
    expect(s.getByText('Open alerts')).toBeTruthy()
  })
})
