import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { act, fireEvent, render } from '@testing-library/react'
import { PageScene } from '@/components/scenes/PageScene'
import { SceneEmblem } from '@/components/scenes/SceneEmblem'
import {
  DEFAULT_SCENE, PAGE_SCENES, SCENE_EXIT_MS, SCENE_MS, sceneOf, setPageScenes,
} from '@/motion'
import type { SceneMotif } from '@/motion'

// Each page's own scene: that every page has one, that each is really drawn, and that it never gets in the way.

const here = dirname(fileURLToPath(import.meta.url))
const text = (path: string) => readFileSync(resolve(here, path), 'utf8').replace(/\r\n/g, '\n')
const css = text('./motion.css')
const routes = text('../App.tsx')
const shell = text('../components/layout/AppShell.tsx')
const emblems = text('../components/scenes/SceneEmblem.tsx')

const fetching = vi.hoisted(() => ({ count: 0, asked: undefined as undefined | { predicate?: (query: unknown) => boolean } }))
vi.mock('@tanstack/react-query', async (original) => ({
  ...(await original<typeof import('@tanstack/react-query')>()),
  useIsFetching: (filters?: { predicate?: (query: unknown) => boolean }) => { fetching.asked = filters; return fetching.count },
}))

/** The system's own setting, as a test sets it. */
function systemAsksForLess(asks: boolean) {
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: (query: string) => ({
      matches: asks && query.includes('reduce'), media: query, onchange: null,
      addListener: () => {}, removeListener: () => {}, addEventListener: () => {}, removeEventListener: () => {},
    }),
  })
}

const MOTIFS = [...new Set([...Object.values(PAGE_SCENES).map((s) => s.motif), DEFAULT_SCENE.motif])].sort() as SceneMotif[]
const LOOPS = ['sc-spin', 'sc-sweep', 'sc-ring', 'sc-scan', 'sc-pulse', 'sc-swing', 'sc-bob', 'sc-lift', 'sc-trace']
const ENTRANCES = ['sc-draw', 'sc-pop', 'sc-rise']

beforeEach(() => {
  fetching.count = 0
  systemAsksForLess(false)
  localStorage.clear()
  vi.useFakeTimers()
})
afterEach(() => vi.useRealTimers())

describe('every page has a scene of its own', () => {
  // The pages inside the shell, as the route table has them.
  const inShell = routes.slice(routes.indexOf('<ShellOrPortal />'), routes.indexOf('<Route path="*"'))
  const paths = [
    '/',
    ...[...inShell.matchAll(/<Route path="([^"]+)"/g)].map(([, path]) => `/${path.replace(/:\w+/g, 'x1')}`),
  ]

  it('is true of every page in the route table, and none is left with the one for a page nobody listed', () => {
    expect(paths.length).toBeGreaterThanOrEqual(107)
    const without = paths.filter((path) => sceneOf(path) === DEFAULT_SCENE)
    expect(without).toEqual([])
    for (const path of paths) {
      const scene = sceneOf(path)
      expect([path, scene.name.length > 1, scene.loading.length > 6]).toEqual([path, true, true])
    }
  })

  it('names no page that is not there, and no two pages alike', () => {
    const real = new Set(paths)
    expect(Object.keys(PAGE_SCENES).filter((path) => !real.has(path))).toEqual([])
    const names = Object.values(PAGE_SCENES).map((s) => s.name)
    expect(names.filter((name, at) => names.indexOf(name) !== at)).toEqual([])
  })

  it('gives a page that is added later the product’s own mark until it is given its own', () => {
    expect(sceneOf('/something-new')).toBe(DEFAULT_SCENE)
    expect(sceneOf('/alerts/')).toBe(PAGE_SCENES['/alerts'])
  })

  it('draws what the page is about', () => {
    const drawn = (path: string) => sceneOf(path).motif
    expect([drawn('/live'), drawn('/map'), drawn('/drones'), drawn('/alerts'), drawn('/analytics'), drawn('/playback')])
      .toEqual(['wall', 'radar', 'drone', 'bell', 'chart', 'film'])
    expect([drawn('/drone-patrols/x1'), drawn('/investigations/x1'), drawn('/situations/x1')])
      .toEqual(['drone', 'casefile', 'shield'])
  })

  it('never covers a page where seconds matter', () => {
    for (const path of ['/man-down', '/emergency', '/alarms', '/response-desk', '/action-center', '/drone-patrols/x1']) {
      expect([path, sceneOf(path).urgent]).toEqual([path, true])
    }
    expect(sceneOf('/alerts').urgent).toBeUndefined()
  })
})

describe('each drawing', () => {
  it.each(MOTIFS)('%s draws itself in, and has a part that goes on moving while the page loads', (motif) => {
    const { container } = render(<SceneEmblem motif={motif} />)
    const svg = container.querySelector('svg')!
    expect(svg.getAttribute('aria-hidden')).toBe('true')
    expect(svg.querySelectorAll(ENTRANCES.map((name) => `.${name}`).join(',')).length).toBeGreaterThan(0)
    expect(svg.querySelectorAll(LOOPS.map((name) => `.${name}`).join(',')).length).toBeGreaterThan(0)
    // A stroke that draws itself has its length called 1, or the one rule that draws them all would not.
    for (const part of svg.querySelectorAll('.sc-draw, .sc-trace')) expect(part.getAttribute('pathLength')).toBe('1')
  })

  it('is one of twenty-four, and every way a part moves has its rule in the style sheet', () => {
    expect(MOTIFS).toHaveLength(24)
    const used = new Set([...emblems.matchAll(/className[=:] ?["']([\w -]+)["']/g)].flatMap(([, names]) => names.split(' ')))
    for (const name of [...used].filter((n) => n.startsWith('sc-'))) expect([name, css.includes(`.${name} `)]).toEqual([name, true])
    for (const name of [...ENTRANCES, ...LOOPS]) expect([name, used.has(name)]).toEqual([name, true])
  })
})

describe('the scene as a page is entered', () => {
  const scene = (container: HTMLElement) => container.querySelector('.sav-scene')
  const played = () => act(() => { vi.advanceTimersByTime(SCENE_MS + SCENE_EXIT_MS) })

  it('draws the page’s emblem and its name over the page area, then clears and is gone', () => {
    const { container } = render(<PageScene pathname="/alerts" />)
    expect(scene(container)).toHaveClass('sav-scene--cover')
    expect(scene(container)).toHaveAttribute('aria-hidden', 'true')
    expect(container.querySelector('.sav-scene__cover svg')).toHaveAttribute('data-motif', 'bell')
    expect(container.querySelector('.sav-scene__cover')).toHaveTextContent('Alerts')

    played()
    expect(container.querySelector('.sav-scene__cover')).toBeNull()
    expect(container.querySelector('.sav-scene__top')).toBeNull()
    // The page's own arrival was timed against it and is left to finish.
    expect(scene(container)).toHaveClass('sav-scene--cover')
  })

  it('goes on, small, as the page’s loading while the page is still fetching - and only that long', () => {
    fetching.count = 2
    const { container, rerender } = render(<PageScene pathname="/alerts" />)
    expect(container.querySelector('.sav-scene__top')).toBeNull()

    played()
    const top = container.querySelector('.sav-scene__top')!
    expect(top).toHaveTextContent('Loading alerts…')
    expect(top).toHaveClass('sav-waits')
    expect(top.querySelector('svg')).toHaveAttribute('data-motif', 'bell')

    fetching.count = 0
    rerender(<PageScene pathname="/alerts" />)
    expect(container.querySelector('.sav-scene__top')).toBeNull()

    // What the page fetches after that is its own business: a later refresh is not "loading the page".
    fetching.count = 1
    rerender(<PageScene pathname="/alerts" />)
    expect(container.querySelector('.sav-scene__top')).toBeNull()
  })

  it('counts only what is fetched for the first time: a refresh of what is on screen is not the page loading', () => {
    render(<PageScene pathname="/alerts" />)
    const counts = fetching.asked!.predicate!
    expect(counts({ state: { data: undefined } })).toBe(true)
    expect(counts({ state: { data: [] } })).toBe(false)
    expect(counts({ state: { data: { items: [] } } })).toBe(false)
  })

  it('is ended at once by a click or a key, and the page is shown', () => {
    const { container } = render(<PageScene pathname="/alerts" />)
    fireEvent.keyDown(window, { key: 'Tab' })
    expect(container.querySelector('.sav-scene__cover')).toBeNull()
    expect(scene(container)).toHaveClass('sav-scene--cut')

    const again = render(<PageScene pathname="/cameras" />)
    fireEvent.pointerDown(window)
    expect(again.container.querySelector('.sav-scene__cover')).toBeNull()
    expect(scene(again.container)).toHaveClass('sav-scene--cut')
  })

  it('does not cover a page where seconds matter: the emblem plays at the top while the page is in view', () => {
    fetching.count = 1
    const { container, rerender } = render(<PageScene pathname="/man-down" />)
    expect(scene(container)).toHaveClass('sav-scene--top')
    expect(container.querySelector('.sav-scene__cover')).toBeNull()
    expect(container.querySelector('.sav-scene__top')).toHaveTextContent('Man Down')

    played()
    expect(container.querySelector('.sav-scene__top')).toHaveTextContent('Loading man-down events…')
    fetching.count = 0
    rerender(<PageScene pathname="/man-down" />)
    expect(container.querySelector('.sav-scene__top')).toBeNull()
  })

  it('does not cover a screen that is in full-screen focus', () => {
    const { container } = render(<PageScene pathname="/live" docked />)
    expect(scene(container)).toHaveClass('sav-scene--top')
    expect(container.querySelector('.sav-scene__cover')).toBeNull()
    expect(container.querySelector('.sav-scene__top')).toHaveTextContent('Live Wall')
  })

  it('draws nothing for somebody who asked for less motion, or switched page animations off', () => {
    systemAsksForLess(true)
    expect(scene(render(<PageScene pathname="/alerts" />).container)).toBeNull()

    systemAsksForLess(false)
    setPageScenes(null, false)
    expect(scene(render(<PageScene pathname="/alerts" />).container)).toBeNull()
    act(() => setPageScenes(null, true))
    expect(scene(render(<PageScene pathname="/alerts" />).container)).not.toBeNull()
  })

  it('stops its timer and stops listening when the page is left', () => {
    const { unmount } = render(<PageScene pathname="/alerts" />)
    unmount()
    expect(vi.getTimerCount()).toBe(0)
  })
})

describe('the scene in the style sheet and the shell', () => {
  const ms = (name: string) => Number(new RegExp(`${name}:\\s*(\\d+)ms;`).exec(css)?.[1])
  const rule = (selector: string) => css.slice(css.indexOf(selector), css.indexOf('}', css.indexOf(selector)) + 1)

  it('is timed once: the code and the style sheet say the same', () => {
    expect([ms('--d-scene'), ms('--d-scene-exit')]).toEqual([SCENE_MS, SCENE_EXIT_MS])
    // Leaving is quicker than arriving.
    expect(SCENE_EXIT_MS).toBeLessThan(SCENE_MS * 0.7)
  })

  it('takes no click and no key, and ends hidden without anything having to hear that it ended', () => {
    expect(rule('.sav-scene {')).toContain('pointer-events: none;')
    expect(rule('.sav-scene__cover {')).toContain('animation: sav-scene-clear var(--d-scene-exit) var(--ease-in) var(--d-scene) forwards;')
    expect(rule('@keyframes sav-scene-clear')).toContain('visibility: hidden;')
  })

  it('has the page arrive as the scene clears, and not wait for a scene that is not covering it', () => {
    expect(rule('.sav-scene--cover + .page-enter {')).toContain('animation-delay: var(--d-scene);')
    expect(css).not.toMatch(/\.sav-scene--(top|cut) \+ \.page-enter/)
    // Which is why the scene must be the element just before the page area.
    expect(shell).toMatch(/<PageScene key=\{`scene:\$\{location\.pathname\}`\} pathname=\{location\.pathname\} docked=\{isFocusMode\} \/>\s*<Box\s+key=\{location\.pathname\}\s+className=\{pageEnterClass\(location\.pathname\)\}/)
  })

  it('is not there at all with reduced motion', () => {
    expect(css).toContain(":root:not([data-motion='full']) .sav-scene { display: none; }")
    expect(css).toContain(":root[data-motion='reduced'] .sav-scene { display: none; }")
  })
})
