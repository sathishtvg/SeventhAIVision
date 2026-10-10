import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { act, renderHook } from '@testing-library/react'
import app from '@/App.tsx?raw'
import shell from '@/components/layout/AppShell.tsx?raw'
import entry from '@/main.tsx?raw'
import { theme } from '@/theme/glassmorphism'
import {
  MOTION_ATTRIBUTE, NEW_ITEM_MS, appear, applyMotionPreference, countOf, duration, easing, enterSx, fadeUpSx,
  newItemProps, pageEnterClass, pageKindOf, reducedMotion, stagger, staggerDelay, toneOf, transitionOf, useAnnouncer,
  useCountUp, useNewItems, useReducedMotion,
} from '@/motion'
import * as old from '@/lib/motion'

// The style sheet as it is written. (Imported as a module it would be empty: the tests run with CSS switched off.)
const css = readFileSync(resolve(dirname(fileURLToPath(import.meta.url)), './motion.css'), 'utf8').replace(/\r\n/g, '\n')

/** The value of a CSS variable in motion.css, in milliseconds. */
const cssMs = (name: string) => Number(new RegExp(`${name}:\\s*(\\d+)ms;`).exec(css)?.[1])
/** One rule of motion.css, by its selector's opening words. */
const rule = (selector: string) => css.slice(css.indexOf(selector), css.indexOf('}', css.indexOf(selector)) + 1)

/** The system's own setting, as a test sets it. */
function systemAsksForLess(asks: boolean) {
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: (query: string) => ({
      matches: asks && query.includes('reduce'), media: query, onchange: null,
      addListener: () => {}, removeListener: () => {}, addEventListener: () => {}, removeEventListener: () => {},
      dispatchEvent: () => false,
    }),
  })
}

afterEach(() => {
  document.documentElement.removeAttribute(MOTION_ATTRIBUTE)
  systemAsksForLess(false)
  useAnnouncer.setState({ text: '', count: 0 })
})

describe('timing is said once', () => {
  it('is the same in the tokens, the style sheet and the theme', () => {
    expect({
      fast: cssMs('--d-fast'), standard: cssMs('--d-standard'), panel: cssMs('--d-panel'), exit: cssMs('--d-exit'),
      page: cssMs('--d-page'), slow: cssMs('--d-slow'),
    }).toEqual(duration)
    expect(cssMs('--d-appear-delay')).toBe(appear.delay)
    expect(cssMs('--d-stagger')).toBe(stagger.step)
    expect(cssMs('--d-new')).toBe(NEW_ITEM_MS)
    expect(css).toContain(`--ease-out:    ${easing.out};`)
    expect(css).toContain(`--ease-in-out: ${easing.inOut};`)
    expect(css).toContain(`--ease-in:     ${easing.in};`)

    const t = theme.transitions
    expect({ shorter: t.duration.shorter, standard: t.duration.standard, complex: t.duration.complex,
      entering: t.duration.enteringScreen, leaving: t.duration.leavingScreen })
      .toEqual({ shorter: duration.fast, standard: duration.standard, complex: duration.slow,
        entering: duration.panel, leaving: duration.exit })
    expect(t.easing).toMatchObject({ easeOut: easing.out, easeInOut: easing.inOut, easeIn: easing.in, sharp: easing.sharp })
  })

  it('has leaving quicker than arriving, and turns short enough to keep a list brisk', () => {
    // About two thirds: closing something should never feel like waiting for it.
    expect(duration.exit / duration.panel).toBeGreaterThanOrEqual(0.6)
    expect(duration.exit / duration.panel).toBeLessThanOrEqual(0.7)
    expect(stagger.step).toBeGreaterThanOrEqual(30)
    expect(stagger.step).toBeLessThanOrEqual(50)
    expect(Object.values(duration).every((ms) => ms >= 100 && ms <= 400)).toBe(true)
  })

  it('writes a transition for the properties named and no others', () => {
    expect(transitionOf('opacity')).toBe(`opacity ${duration.standard}ms ${easing.inOut}`)
    expect(transitionOf(['border-color', 'box-shadow'], duration.fast, easing.out))
      .toBe(`border-color 150ms ${easing.out}, box-shadow 150ms ${easing.out}`)
    expect(transitionOf('opacity')).not.toContain('all')
  })

  it('still gives the two helpers from where pages have always imported them', () => {
    expect(old.fadeUpSx).toBe(fadeUpSx)
    expect(old.useCountUp).toBe(useCountUp)
  })
})

describe('items take their turns', () => {
  it('waits a step each, and after the tenth they arrive together', () => {
    expect([0, 1, 2, 10, 11, 60].map((i) => staggerDelay(i))).toEqual([0, 40, 80, 400, 400, 400])
    expect(staggerDelay(-3)).toBe(0)
  })

  it('enters with a fade and a small rise, or a fade alone, written without the shorthand', () => {
    expect(enterSx(2)).toEqual({
      animationName: 'sav-fade-up', animationDuration: `${duration.page}ms`, animationTimingFunction: easing.out,
      animationFillMode: 'both', animationDelay: '80ms',
    })
    expect(enterSx(0, { rise: false })).toMatchObject({ animationName: 'sav-fade-in', animationDelay: '0ms' })
    // Emotion drops the whole shorthand when a variable is in it; neither helper uses it.
    expect(Object.keys(enterSx(0))).not.toContain('animation')
    expect(Object.keys(fadeUpSx(0))).not.toContain('animation')
    for (const name of ['sav-fade-in', 'sav-fade-up', 'fade-up', 'sav-tick', 'sav-new-fade']) {
      expect(css).toContain(`@keyframes ${name} {`)
    }
  })
})

describe('how a page arrives depends on what kind of page it is', () => {
  it('lets a screen somebody watches or works on simply appear', () => {
    for (const path of ['/live', '/playback', '/command-centre', '/action-center', '/my-patrols', '/response-desk', '/map']) {
      expect([path, pageKindOf(path)]).toEqual([path, 'operational'])
    }
    expect(pageEnterClass('/live')).toBe('page-enter page-enter--operational')
    // No movement there: its keyframes change nothing but opacity, and it takes the shortest time.
    expect(rule('.page-enter--operational')).toContain('sav-fade-in var(--d-fast)')
    expect(rule('@keyframes sav-fade-in')).not.toContain('transform')
  })

  it('knows a dashboard, a single record and everything else', () => {
    expect(['/', '/platform', '/analytics', '/drones'].map(pageKindOf)).toEqual(Array(4).fill('dashboard'))
    expect(['/situations/42', '/investigations/a1', '/drone-missions/7', '/platform/tenants/x'].map(pageKindOf))
      .toEqual(Array(4).fill('detail'))
    expect(['/alerts', '/users', '/settings', '/situations', '/platform/tenants', '/a-page-added-later'].map(pageKindOf))
      .toEqual(Array(6).fill('table'))
    expect(pageKindOf('/alerts/')).toBe('table')
    expect(pageKindOf('/live/')).toBe('operational')
    for (const kind of ['dashboard', 'detail', 'operational']) expect(css).toContain(`.page-enter--${kind} `)
  })

  it('names only paths the app has', () => {
    const paths = new Set(Array.from(app.matchAll(/<Route\s+path="([^"]+)"/g), (m) => `/${m[1].replace(/^\//, '')}`))
    const listed = ['/live', '/playback', '/recordings', '/command-centre', '/action-center', '/my-patrols', '/response-desk',
      '/operations-board', '/map', '/security-map', '/gps', '/vms-onsite', '/man-down', '/emergency', '/bwc', '/alarms',
      '/platform', '/platform/analytics', '/platform/billing', '/analytics', '/drones', '/drone-analytics',
      '/security-insight', '/guard-ops', '/heatmap', '/workforce-readings', '/risk-advice']
    for (const path of listed) {
      expect([path, paths.has(path)]).toEqual([path, true])
      expect(pageKindOf(path)).not.toBe('table')
    }
  })

  it('is what the shell puts on the page area, which is keyed by the path and not by its filters', () => {
    expect(shell).toContain('key={location.pathname}')
    expect(shell).toContain('className={pageEnterClass(location.pathname)}')
    expect(shell).toContain('<LiveAnnouncer />')
    expect(shell).toContain('<LoadFailureNotice />')
    // The style sheet these classes are in is loaded - which the one that used to hold them never was.
    expect(entry).toContain("import '@/motion/motion.css'")
    expect(entry).not.toContain('index.css')
    // No request for data fails without a word.
    expect(entry).toContain('queryCache: new QueryCache({')
    expect(entry).toContain('onError: (error) => useLoadFailures.getState().report(error)')
  })
})

describe('less motion, for whoever asks', () => {
  it('follows the system until the person chooses, and then follows the person', () => {
    expect(reducedMotion()).toBe(false)
    systemAsksForLess(true)
    expect(reducedMotion()).toBe(true)
    applyMotionPreference('full')
    expect(document.documentElement.getAttribute(MOTION_ATTRIBUTE)).toBe('full')
    expect(reducedMotion()).toBe(false)
    systemAsksForLess(false)
    applyMotionPreference('reduced')
    expect(reducedMotion()).toBe(true)
    applyMotionPreference('system')
    expect(document.documentElement.hasAttribute(MOTION_ATTRIBUTE)).toBe(false)
    expect(reducedMotion()).toBe(false)
  })

  it('redraws what was drawn with it when the setting changes', () => {
    const { result } = renderHook(() => useReducedMotion())
    expect(result.current).toBe(false)
    act(() => applyMotionPreference('reduced'))
    expect(result.current).toBe(true)
    act(() => applyMotionPreference('system'))
    expect(result.current).toBe(false)
  })

  it('gives a figure at once, without counting up to it', () => {
    applyMotionPreference('reduced')
    const { result, rerender } = renderHook(({ value }) => useCountUp(value), { initialProps: { value: undefined as number | undefined } })
    expect(result.current).toBe(0)
    rerender({ value: 128 })
    expect(result.current).toBe(128)
  })

  it('is one rule for the system and one for the app, and neither moves anything or makes it take turns', () => {
    const system = rule(":root:not([data-motion='full']) *,")
    const chosen = rule(":root[data-motion='reduced'] *,")
    for (const block of [system, chosen]) {
      expect(block).toContain('animation-duration: 0.01ms !important;')
      expect(block).toContain('animation-delay: 0s !important;')
      expect(block).toContain('animation-iteration-count: 1 !important;')
      expect(block).toContain('transition-duration: 0.01ms !important;')
      // A placeholder's wait is a delay on a transition, and is deliberately left.
      expect(block).not.toContain('transition-delay')
    }
    expect(css).toContain('@media (prefers-reduced-motion: reduce) {\n  :root:not([data-motion=\'full\']) *,')
    // The mark on what has just arrived stays put under both; an animation cut to nothing would end it at once.
    expect(css).toContain(":root[data-motion='reduced'] tr.sav-new > th { animation-name: none !important; }")
    expect(css).toContain(":root:not([data-motion='full']) tr.sav-new > th { animation-name: none !important; }")
  })
})

describe('a placeholder waits before it is seen, and the content never waits for it', () => {
  it('is in the page from the first frame and becomes visible after the delay', () => {
    const waits = rule('.MuiSkeleton-root,\n.MuiCircularProgress-indeterminate')
    expect(waits).toContain('.sav-waits {')
    expect(waits).toContain('transition: opacity var(--d-standard) var(--ease-out) var(--d-appear-delay);')
    expect(css).toMatch(/@starting-style \{\s+\.MuiSkeleton-root,\s+\.MuiCircularProgress-indeterminate,\s+\.MuiLinearProgress-indeterminate,\s+\.sav-waits \{ opacity: 0; \}/)
    // A bar that reports a real amount is not a placeholder and is not made to wait.
    expect(css).not.toContain('.MuiLinearProgress-determinate')
    expect(css).not.toContain('.MuiLinearProgress-root')
  })

  it('is the shimmer everywhere, which leaves the placeholder’s own opacity alone', () => {
    expect(theme.components?.MuiSkeleton?.defaultProps).toEqual({ animation: 'wave' })
  })
})

describe('what has just arrived is marked', () => {
  const rows = (...ids: number[]) => ids.map((id) => ({ id }))
  const idOf = (r: { id: number }) => r.id

  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  it('marks nothing when a list is first shown, and only what comes after', () => {
    const { result, rerender } = renderHook(({ items }) => useNewItems(items, idOf), {
      initialProps: { items: undefined as { id: number }[] | undefined },
    })
    expect(result.current.size).toBe(0)
    rerender({ items: rows(1, 2, 3) })
    expect(result.current.size).toBe(0)
    rerender({ items: rows(4, 1, 2, 3) })
    expect([...result.current]).toEqual(['4'])
    // The same rows again are not new again.
    rerender({ items: rows(4, 1, 2, 3) })
    expect([...result.current]).toEqual(['4'])
  })

  it('takes the mark off after a few seconds, each row in its own time', () => {
    const { result, rerender } = renderHook(({ items }) => useNewItems(items, idOf), { initialProps: { items: rows(1) } })
    rerender({ items: rows(2, 1) })
    act(() => { vi.advanceTimersByTime(NEW_ITEM_MS - 2000) })
    rerender({ items: rows(3, 2, 1) })
    expect([...result.current].sort()).toEqual(['2', '3'])
    act(() => { vi.advanceTimersByTime(2000) })
    expect([...result.current]).toEqual(['3'])
    act(() => { vi.advanceTimersByTime(NEW_ITEM_MS) })
    expect(result.current.size).toBe(0)
  })

  it('does not call a different list new: a changed filter starts again', () => {
    const { result, rerender } = renderHook(({ items, scope }) => useNewItems(items, idOf, { scope }), {
      initialProps: { items: rows(1, 2), scope: 'open' },
    })
    rerender({ items: rows(7, 8, 9), scope: 'closed' })
    expect(result.current.size).toBe(0)
    rerender({ items: rows(10, 7, 8, 9), scope: 'closed' })
    expect([...result.current]).toEqual(['10'])
  })

  it('marks nothing while the rows on screen are the last list’s, kept until this one’s arrive', () => {
    const { result, rerender } = renderHook(({ items, settled }) => useNewItems(items, idOf, { settled }), {
      initialProps: { items: rows(1, 2), settled: true },
    })
    rerender({ items: rows(1, 2), settled: false })
    rerender({ items: rows(5, 6), settled: true })
    expect(result.current.size).toBe(0)
  })

  it('tells a screen reader how many, in words, and only when asked to', () => {
    const say = (n: number) => countOf(n, 'new alert')
    const { rerender } = renderHook(({ items }) => useNewItems(items, idOf, { say }), { initialProps: { items: rows(1) } })
    expect(useAnnouncer.getState().count).toBe(0)
    rerender({ items: rows(2, 1) })
    expect(useAnnouncer.getState()).toMatchObject({ text: '1 new alert', count: 1 })
    rerender({ items: rows(4, 3, 2, 1) })
    expect(useAnnouncer.getState()).toMatchObject({ text: '2 new alerts', count: 2 })

    const quiet = renderHook(({ items }) => useNewItems(items, idOf), { initialProps: { items: rows(1) } })
    quiet.rerender({ items: rows(9, 1) })
    expect(useAnnouncer.getState().count).toBe(2)
  })

  it('stops its timers when the page is left', () => {
    const { rerender, unmount } = renderHook(({ items }) => useNewItems(items, idOf), { initialProps: { items: rows(1) } })
    rerender({ items: rows(2, 1) })
    unmount()
    expect(vi.getTimerCount()).toBe(0)
  })

  it('is a class and a tone beside whatever else the row has, and a colour for each tone', () => {
    expect(newItemProps(false, 'critical')).toEqual({})
    expect(newItemProps(true, 'critical')).toEqual({ className: 'sav-new', 'data-tone': 'critical' })
    expect(['critical', 'HIGH', 'medium', 'low', null, 'something else'].map(toneOf))
      .toEqual(['critical', 'high', 'medium', 'low', 'info', 'info'])
    for (const tone of ['critical', 'high', 'medium', 'low', 'success']) {
      expect(css).toContain(`.sav-new[data-tone='${tone}']`)
    }
    // The mark is there without any movement: a tint and a bar. The fade only takes it away.
    expect(rule('tr.sav-new > td,\ntr.sav-new > th,\n:not(tr).sav-new {')).toContain('background-color: color-mix(')
    expect(css).toContain('box-shadow: inset 3px 0 0 var(--sav-new);')
  })
})
