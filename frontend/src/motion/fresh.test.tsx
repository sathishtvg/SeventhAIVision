import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { act, render, renderHook, fireEvent } from '@testing-library/react'
import { fadeImageProps, freshAt, markShown, useNow } from '@/motion'

// Nothing is drawn as live that is not, and a picture is not seen until it is there.

const here = dirname(fileURLToPath(import.meta.url))
const text = (path: string) => readFileSync(resolve(here, path), 'utf8').replace(/\r\n/g, '\n')
const css = text('./motion.css')

afterEach(() => vi.useRealTimers())

describe('whether something is still live', () => {
  const now = Date.parse('2026-10-14T08:00:00Z')

  it('is a matter of when it was last heard from, and nothing without a time is live', () => {
    expect(freshAt('2026-10-14T07:59:50Z', 20_000, now)).toBe(true)
    expect(freshAt('2026-10-14T07:59:30Z', 20_000, now)).toBe(false)
    expect(freshAt(null, 20_000, now)).toBe(false)
    expect(freshAt(undefined, 20_000, now)).toBe(false)
    expect(freshAt('not a time', 20_000, now)).toBe(false)
  })

  it('is read again as the clock moves, whether or not anything was fetched', () => {
    vi.useFakeTimers()
    vi.setSystemTime(now)
    const { result, unmount } = renderHook(() => freshAt('2026-10-14T07:59:55Z', 20_000, useNow(5000)))
    expect(result.current).toBe(true)
    act(() => { vi.advanceTimersByTime(10_000) })
    expect(result.current).toBe(true)
    // Nothing arrived, and it has gone stale by the clock alone.
    act(() => { vi.advanceTimersByTime(10_000) })
    expect(result.current).toBe(false)
    unmount()
    expect(vi.getTimerCount()).toBe(0)
  })

  it('is what the map and the drone screen go by', () => {
    const gps = text('../pages/GPS.tsx')
    // A vehicle pulses only while it is moving AND still being heard from; one that is not is drawn faint and says so.
    expect(gps).toContain("animation: v.current_status === 'moving' && heard ? 'sav-live-pulse 1.5s infinite' : 'none',")
    expect(gps).toContain('const heard = freshAt(v.last_position_at, POSITION_STALE_MS, now)')
    expect(gps).toContain('opacity: heard ? 1 : 0.5,')
    expect(gps).toContain('Not live: the last position it sent')
    expect(gps).not.toContain("'pulse 1.5s infinite'")

    const drone = text('../pages/drones/DronePatrol.tsx')
    expect(drone).toContain('{live && <Grid size={{ xs: 12 }}><StaleBadge at={p.recorded_at} staleAfterMs={TELEMETRY_STALE_MS} /></Grid>}')
    expect(drone).toContain('opacity={heard ? 1 : 0.45}')
    // The live view says it; a replay of a flight that is over does not.
    expect(drone).toContain('<Telemetry p={now} live />')
    expect(drone).toContain('<Telemetry p={cur} />')
  })
})

describe('a picture that fades in', () => {
  it('is out of sight until it is there, and is then marked for the style sheet to show', () => {
    expect(css).toContain('.sav-img:not([data-loaded]) { opacity: 0; }')
    const { container } = render(<img alt="thumb" src="/t.jpg" {...fadeImageProps} />)
    const img = container.querySelector('img')!
    expect(img).toHaveClass('sav-img')
    expect(img.dataset.loaded).toBeUndefined()
    fireEvent.load(img)
    expect(img.dataset.loaded).toBe('true')
  })

  it('shows a picture that could not be fetched as the browser shows it, and one that is already there at once', () => {
    const { container } = render(<img alt="thumb" src="/missing.jpg" {...fadeImageProps} />)
    const img = container.querySelector('img')!
    fireEvent.error(img)
    expect(img.dataset.loaded).toBe('true')

    const ready = document.createElement('img')
    Object.defineProperty(ready, 'complete', { value: true })
    Object.defineProperty(ready, 'naturalWidth', { value: 80 })
    fadeImageProps.ref(ready)
    expect(ready.dataset.loaded).toBe('true')
    const empty = document.createElement('img')
    markShown(null)
    fadeImageProps.ref(empty)
    expect(empty.dataset.loaded).toBeUndefined()
  })
})

describe('what was tidied', () => {
  const read = (path: string) => text(path)

  it('animates how a control is painted, never "all" of it', () => {
    for (const file of ['../theme/glassmorphism.ts', '../components/layout/Sidebar.tsx', '../components/layout/TopBar.tsx',
      '../pages/GPS.tsx', '../pages/IoT.tsx', '../pages/Tenants.tsx', '../pages/Login.tsx', '../pages/ForgotPassword.tsx', '../pages/ResetPassword.tsx']) {
      expect([file, /transition: ?['"`]all\b/.test(read(file))]).toEqual([file, false])
    }
    expect(read('../theme/glassmorphism.ts').match(/transition: paintOnly\(/g)).toHaveLength(5)
  })

  it('leaves a critical chip its glow and its pulsing dot, and no shadow that is animated', () => {
    const chip = read('../components/common/SeverityChip.tsx')
    expect(chip).not.toContain('glow-pulse-critical')
    expect(chip).toContain('boxShadow: cfg.glow,')
    expect(chip).toContain("animation: 'pulse-dot 1.6s ease-in-out infinite',")
  })

  it('asks for no animation by a name nothing defines', () => {
    expect(read('../pages/Dashboard.tsx')).not.toContain("animation: 'pulse ")
    expect(read('../pages/BWC.tsx')).not.toContain("animation: 'pulse ")
    expect(css).toContain('@keyframes sav-live-pulse {')
  })
})
