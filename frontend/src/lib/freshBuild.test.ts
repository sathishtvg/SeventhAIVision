import { buildOf, reloadIfRebuilt, RELOADED_FOR } from '@/lib/freshBuild'

/** The page a build of the web app is, as the web server sends it. */
const built = (script: string, style = 'index-vh-t_kPv.css') => `<!doctype html><html lang="en"><head>
  <link rel="icon" type="image/svg+xml" href="/favicon.svg" />
  <script type="module" crossorigin src="/assets/${script}"></script>
  <link rel="stylesheet" crossorigin href="/assets/${style}">
</head><body><div id="root"></div><script>setTimeout(function () {}, 12000)</script></body></html>`

/** The same page under the development server: it names its source, not a built file. */
const DEV = `<!doctype html><html lang="en"><head><link rel="icon" href="/favicon.svg" /></head>
<body><div id="root"></div><script type="module" src="/src/main.tsx"></script></body></html>`

const OLD = 'index-CDSA7UER.js'
const NEW = 'index-Bq3xY_9k.js'

const pageOf = (html: string) => new DOMParser().parseFromString(html, 'text/html')

function memory(kept: Record<string, string> = {}) {
  return {
    kept,
    getItem: (key: string) => kept[key] ?? null,
    setItem: (key: string, value: string) => { kept[key] = value },
  }
}

/** A tab running `running`, asking a server that sends `served`. */
function tab(running: string, served: string | null | Error, storage = memory()) {
  const reload = vi.fn()
  const fetchPage = vi.fn(async () => {
    if (served instanceof Error) throw served
    return served
  })
  const opts = { page: pageOf(running), protocol: 'https:', fetchPage, reload, storage }
  return { reload, fetchPage, storage, opts }
}

describe('buildOf', () => {
  it('is the built files a page names, and nothing else it names', () => {
    expect(buildOf(pageOf(built(OLD)))).toBe('/assets/index-CDSA7UER.js /assets/index-vh-t_kPv.css')
  })

  it('is the same for the same files in another order', () => {
    const turned = '<html><head><link rel="stylesheet" href="/assets/index-vh-t_kPv.css">'
      + `<script type="module" src="/assets/${OLD}"></script></head></html>`
    expect(buildOf(pageOf(turned))).toBe(buildOf(pageOf(built(OLD))))
  })

  it('changes when only the styles do', () => {
    expect(buildOf(pageOf(built(OLD, 'index-AAAA1111.css')))).not.toBe(buildOf(pageOf(built(OLD))))
  })

  it('is nothing for a page that names no built file', () => {
    expect(buildOf(pageOf(DEV))).toBeNull()
    expect(buildOf(pageOf('<html><body><h1>502 Bad Gateway</h1></body></html>'))).toBeNull()
  })
})

describe('reloadIfRebuilt', () => {
  it('leaves a tab alone that is the build the server has', async () => {
    const { reload, storage, opts } = tab(built(OLD), built(OLD))
    expect(await reloadIfRebuilt(opts)).toBe('current')
    expect(reload).not.toHaveBeenCalled()
    expect(storage.kept).toEqual({})
  })

  it('reloads a tab the server has a newer build than, and remembers which build that was', async () => {
    const { reload, storage, opts } = tab(built(OLD), built(NEW))
    expect(await reloadIfRebuilt(opts)).toBe('reloaded')
    expect(reload).toHaveBeenCalledTimes(1)
    expect(storage.kept[RELOADED_FOR]).toBe(buildOf(pageOf(built(NEW))))
  })

  it('reloads once for a build: an old page that comes back again is left where it is', async () => {
    const storage = memory()
    expect(await reloadIfRebuilt(tab(built(OLD), built(NEW), storage).opts)).toBe('reloaded')
    // The reload brought the old page back - something between the server and the tab is holding it.
    const again = tab(built(OLD), built(NEW), storage)
    expect(await reloadIfRebuilt(again.opts)).toBe('held')
    expect(again.reload).not.toHaveBeenCalled()
  })

  it('reloads again for the build after that one', async () => {
    const storage = memory()
    await reloadIfRebuilt(tab(built(OLD), built(NEW), storage).opts)
    const later = tab(built(NEW), built('index-Zz9_later.js'), storage)
    expect(await reloadIfRebuilt(later.opts)).toBe('reloaded')
    expect(later.reload).toHaveBeenCalledTimes(1)
  })

  it('does not reload a page somebody is in the middle of, and can when they are done', async () => {
    const storage = memory()
    const busy = tab(built(OLD), built(NEW), storage)
    expect(await reloadIfRebuilt({ ...busy.opts, mayReload: () => false })).toBe('held')
    expect(busy.reload).not.toHaveBeenCalled()
    expect(storage.kept).toEqual({})
    const done = tab(built(OLD), built(NEW), storage)
    expect(await reloadIfRebuilt({ ...done.opts, mayReload: () => true })).toBe('reloaded')
  })

  it('does not reload a tab that cannot remember it has', async () => {
    const forgetful = {
      getItem: () => null,
      setItem: () => { throw new Error('QuotaExceededError') },
    }
    const { reload, opts } = tab(built(OLD), built(NEW))
    expect(await reloadIfRebuilt({ ...opts, storage: forgetful })).toBe('held')
    const blocked = {
      getItem: () => { throw new Error('SecurityError') },
      setItem: () => {},
    }
    expect(await reloadIfRebuilt({ ...opts, storage: blocked })).toBe('held')
    expect(reload).not.toHaveBeenCalled()
  })

  it.each([
    ['the server cannot be reached', new Error('Failed to fetch')],
    ['the server did not send the page', null],
    ['what came back is not the page', '<html><body><h1>502 Bad Gateway</h1></body></html>'],
    ['what came back names no built file', DEV],
  ])('does nothing when %s', async (_why, served) => {
    const { reload, storage, opts } = tab(built(OLD), served)
    expect(await reloadIfRebuilt(opts)).toBe('unknown')
    expect(reload).not.toHaveBeenCalled()
    expect(storage.kept).toEqual({})
  })

  it('does not ask under the desktop app, whose pages are a copy inside it', async () => {
    const { reload, fetchPage, opts } = tab(built(OLD), built(NEW))
    expect(await reloadIfRebuilt({ ...opts, protocol: 'app:' })).toBe('unknown')
    expect(fetchPage).not.toHaveBeenCalled()
    expect(reload).not.toHaveBeenCalled()
  })

  it('does not ask under the development server', async () => {
    const { reload, fetchPage, opts } = tab(DEV, built(NEW))
    expect(await reloadIfRebuilt(opts)).toBe('unknown')
    expect(fetchPage).not.toHaveBeenCalled()
    expect(reload).not.toHaveBeenCalled()
  })
})

describe('reloadIfRebuilt, asking the server itself', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('asks for the page by its own name and takes no kept copy for an answer', async () => {
    const fetched = vi.fn(async () => new Response(built(NEW), { status: 200 }))
    vi.stubGlobal('fetch', fetched)
    const { reload, opts } = tab(built(OLD), null)
    expect(await reloadIfRebuilt({ ...opts, fetchPage: undefined })).toBe('reloaded')
    expect(fetched).toHaveBeenCalledWith('/index.html', { cache: 'no-store', credentials: 'same-origin' })
    expect(reload).toHaveBeenCalledTimes(1)
  })

  it('does nothing with an answer that is an error', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(built(NEW), { status: 503 })))
    const { reload, opts } = tab(built(OLD), null)
    expect(await reloadIfRebuilt({ ...opts, fetchPage: undefined })).toBe('unknown')
    expect(reload).not.toHaveBeenCalled()
  })

  it('is asleep in this test page, which is not a built one', async () => {
    const fetched = vi.fn()
    vi.stubGlobal('fetch', fetched)
    expect(await reloadIfRebuilt()).toBe('unknown')
    expect(fetched).not.toHaveBeenCalled()
  })
})
