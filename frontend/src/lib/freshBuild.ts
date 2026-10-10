/**
 * Whether the page in front of somebody is the one the server has now.
 *
 * On 10 October 2026 the platform owner could not sign in. The password was
 * right; the tab had been open since before the web app was rebuilt, and the
 * sign-in page in it had no two-factor step. A tab that is left open goes on
 * running the scripts it loaded: nothing the server sends afterwards reaches
 * it until it is reloaded, and nothing told anybody to reload it.
 *
 * So the page asks. A build names its scripts and styles after their contents,
 * so two pages that name the same files are the same build. When the server's
 * page names different ones, this tab is reloaded - once for that build, never
 * in a loop.
 *
 * It does nothing where the question has no answer: under the desktop app,
 * whose pages are a copy inside the app and not a server's; under the
 * development server, whose page names no built file; and whenever the server
 * cannot be reached or sends something that is not the page.
 */

/** sessionStorage: the build this tab has already reloaded itself for. */
export const RELOADED_FOR = 'sav.freshBuild.reloadedFor'

/**
 * current  - the tab is the build the server has
 * reloaded - it was not, and the tab has been told to load the page again
 * held     - it is not, and the tab was left alone: it has reloaded for this
 *            build once already, cannot remember that it has, or was busy
 * unknown  - there was no telling
 */
export type Freshness = 'current' | 'reloaded' | 'held' | 'unknown'

export interface FreshBuildOptions {
  /** The page that is running. */
  page?: Document
  protocol?: string
  /** The page the server sends now, as text; null when it did not send one. */
  fetchPage?: () => Promise<string | null>
  reload?: () => void
  storage?: Pick<Storage, 'getItem' | 'setItem'>
  /** Asked at the last moment: false leaves a page alone that somebody is in the middle of. */
  mayReload?: () => boolean
}

/**
 * The built files a page names, which is what tells one build from another.
 * null for a page that names none - the development server's, or an error page.
 */
export function buildOf(page: Document): string | null {
  const named = Array.from(page.querySelectorAll('script[src], link[href]'))
    .map((el) => el.getAttribute('src') ?? el.getAttribute('href') ?? '')
    .filter((url) => url.includes('/assets/'))
  return named.length ? Array.from(new Set(named)).sort().join(' ') : null
}

async function servedPage(): Promise<string | null> {
  // no-store: the answer must be the server's, not a copy this browser kept.
  const res = await fetch('/index.html', { cache: 'no-store', credentials: 'same-origin' })
  return res.ok ? res.text() : null
}

export async function reloadIfRebuilt(opts: FreshBuildOptions = {}): Promise<Freshness> {
  const {
    page = document,
    protocol = window.location.protocol,
    fetchPage = servedPage,
    reload = () => window.location.reload(),
    mayReload = () => true,
  } = opts

  if (protocol !== 'http:' && protocol !== 'https:') return 'unknown'
  const running = buildOf(page)
  if (!running) return 'unknown'

  let served: string | null
  try {
    const html = await fetchPage()
    served = html ? buildOf(new DOMParser().parseFromString(html, 'text/html')) : null
  } catch {
    return 'unknown'
  }
  if (!served) return 'unknown'
  if (served === running) return 'current'

  // Once for a build. If the reload brings the same old page back - a proxy
  // holding it, or two servers of different builds behind one address - a
  // second reload would not help and a third would be a loop.
  try {
    const storage = opts.storage ?? window.sessionStorage
    if (storage.getItem(RELOADED_FOR) === served || !mayReload()) return 'held'
    storage.setItem(RELOADED_FOR, served)
  } catch {
    // A tab that cannot remember it has reloaded is not reloaded at all.
    return 'held'
  }
  reload()
  return 'reloaded'
}
