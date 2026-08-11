import { useEffect, useRef } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useFocusModeStore } from '@/store/focusMode'
import { FULLSCREEN_PARAM } from '@/lib/popoutWindow'

/**
 * Kiosk / fullscreen toggle shared by every page that offers a "Full
 * Screen" control-room mode (Live Wall, Attendance, Command Centre, Action
 * Center). Electron gets true kiosk mode; browsers get the Fullscreen API.
 * Both also flip focus-mode so AppShell hides its own sidebar/topbar —
 * fullscreening the window alone doesn't hide the app's own chrome.
 * Focus-mode is the primary, always-applied toggle; the Fullscreen API call
 * is best-effort on top of it (browsers can reject requestFullscreen for
 * reasons outside our control — e.g. missing transient user activation —
 * and that must not block hiding our own chrome, which is the actual ask).
 * Esc exits browser fullscreen (Electron handles Esc itself); the
 * fullscreenchange listener keeps focus-mode in sync when a real
 * fullscreen session ends.
 *
 * `kiosk` READS the focus-mode store rather than keeping its own copy. It
 * used to be local state, which silently drifted the moment anything other
 * than this hook turned focus mode off — AppShell's focus-mode strip owns
 * Exit now, so after exiting there the page still believed it was in kiosk
 * and kept its "Full Screen" button hidden, leaving no way back in. One
 * source of truth removes that class of bug entirely. Still keyed off our
 * own state and not document.fullscreenElement: if requestFullscreen() is
 * rejected, fullscreenElement stays null while we are already in focus
 * mode, and branching on it would re-enter instead of exit.
 */
export function useKioskToggle() {
  const kiosk = useFocusModeStore((s) => s.isFocusMode)
  const setFocusMode = useFocusModeStore((s) => s.setFocusMode)
  const [searchParams, setSearchParams] = useSearchParams()

  const toggleKiosk = async () => {
    if (window.electronAPI?.setKiosk) {
      const now = await window.electronAPI.setKiosk(!kiosk)
      setFocusMode(now)
      return
    }
    if (kiosk) {
      setFocusMode(false)
      if (document.fullscreenElement) {
        try { await document.exitFullscreen() } catch { /* already exiting */ }
      }
      return
    }
    setFocusMode(true)
    try { await document.documentElement.requestFullscreen() } catch { /* focus mode still applies */ }
  }

  useEffect(() => {
    const sync = () => {
      if (!document.fullscreenElement) setFocusMode(false)
    }
    document.addEventListener('fullscreenchange', sync)
    return () => document.removeEventListener('fullscreenchange', sync)
  }, [setFocusMode])

  // A window opened with ?fullscreen=1 (see popoutWindow.ts) puts itself into
  // full screen on arrival — that's how "new window AND into full screen"
  // becomes one action for the operator, since the opener can't fullscreen a
  // window it doesn't own. Guarded by a ref so it fires once per window even
  // if the effect re-runs, and the flag is stripped from the URL afterwards so
  // a manual Exit Full Screen isn't undone by a later refresh.
  const autoAppliedRef = useRef(false)
  useEffect(() => {
    if (autoAppliedRef.current) return
    if (searchParams.get(FULLSCREEN_PARAM) !== '1') return
    autoAppliedRef.current = true

    setFocusMode(true)
    if (window.electronAPI?.setKiosk) {
      void window.electronAPI.setKiosk(true)
    } else {
      // Browsers reject requestFullscreen without a user gesture, and a freshly
      // opened window has none. Focus-mode (our own chrome hiding) still
      // applies, which is the part that actually matters for a wall display.
      void document.documentElement.requestFullscreen().catch(() => {})
    }

    // Strip via the router, not history.replaceState — a raw replaceState is
    // invisible to React Router and gets clobbered by its next render, so the
    // flag would survive and re-trigger fullscreen on a later refresh.
    const next = new URLSearchParams(searchParams)
    next.delete(FULLSCREEN_PARAM)
    setSearchParams(next, { replace: true })
  }, [searchParams, setSearchParams, setFocusMode])

  return { kiosk, toggleKiosk }
}
