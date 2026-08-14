import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ColorModeProvider } from '@/context/ColorMode'
import { initAxiosInterceptors, restoreSession } from '@/store/auth'
import './i18n'
import App from './App'

initAxiosInterceptors()
restoreSession()

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 30_000, retry: 1 } },
})

/** Splash floor. React mounts in well under 100ms on a warm cache, and a
 *  splash that appears and vanishes inside one frame reads as a glitch. */
const MIN_SPLASH_MS = 900

/**
 * Hand the screen over from index.html's boot splash to the app.
 *
 * Deliberately built on timers and a DOM check rather than
 * requestAnimationFrame: rAF is throttled to zero in a tab that isn't
 * rendering, so an app launched into a background tab would sit on the splash
 * until the crude safety net in index.html fired. Timers keep running there.
 *
 * Two conditions, both required — the floor above, and React having actually
 * committed. On a cold, slow start the floor can elapse before anything is
 * painted, and dropping the splash then would expose an empty page.
 */
function dismissBootSplash() {
  const el = document.getElementById('boot-splash')
  if (!el) return
  if (performance.now() < MIN_SPLASH_MS || !document.getElementById('root')?.firstElementChild) {
    window.setTimeout(dismissBootSplash, 60)
    return
  }
  el.classList.add('bs-done')
  // Matches the CSS fade, so the node is gone once it is invisible rather
  // than lingering as an inert full-screen layer over the app.
  window.setTimeout(() => el.remove(), 500)
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      <QueryClientProvider client={queryClient}>
        <ColorModeProvider>
          <App />
        </ColorModeProvider>
      </QueryClientProvider>
    </BrowserRouter>
  </StrictMode>,
)

dismissBootSplash()
