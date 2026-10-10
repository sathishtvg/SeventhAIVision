import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { ThemeProvider, CssBaseline } from '@mui/material'
import { createGlassTheme } from '@/theme/glassmorphism'
import { useAuthStore } from '@/store/auth'
import { applyMotionPreference, isMotionPreference } from '@/motion/preference'
import type { MotionPreference } from '@/motion/preference'

type ColorMode = 'light' | 'dark'

interface ColorModeContextValue {
  mode: ColorMode
  toggle: () => void
  brandColor: string
  setBrandColor: (color: string) => void
  /** Back to the shipped defaults for this user. */
  reset: () => void
  /** How much the app moves for this person: the system's setting, less, or all of it. */
  motion: MotionPreference
  setMotion: (motion: MotionPreference) => void
}

export const DEFAULT_BRAND = '#6C63FF'

/** Presets that all clear contrast against both themes. A free colour picker
 *  is offered too, but these are the ones known to stay readable. */
export const BRAND_PRESETS: { name: string; hex: string }[] = [
  { name: 'Violet (default)', hex: DEFAULT_BRAND },
  { name: 'Teal', hex: '#00A8A8' },
  { name: 'Blue', hex: '#2F80ED' },
  { name: 'Emerald', hex: '#12A150' },
  { name: 'Amber', hex: '#B45309' },
  { name: 'Rose', hex: '#E11D48' },
  { name: 'Slate', hex: '#475569' },
]

const ColorModeContext = createContext<ColorModeContextValue>({
  mode: 'dark',
  toggle: () => {},
  brandColor: DEFAULT_BRAND,
  setBrandColor: () => {},
  reset: () => {},
  motion: 'system',
  setMotion: () => {},
})

export function useColorMode() {
  return useContext(ColorModeContext)
}

/**
 * Preferences are stored per user, not per browser.
 *
 * A control room is a shared machine: an operator picking their own accent
 * must not repaint the app for whoever signs in on the same PC next. Keying
 * on the user id keeps each person's choice to themselves, which is exactly
 * the "only for each user" requirement — everyone else keeps the usual
 * colours until they choose otherwise.
 *
 * Signed-out users share one bucket, so the login screen still has a
 * consistent look.
 */
const prefsKey = (userId?: string | null) => `seventh-ai-ui-prefs:${userId ?? 'anon'}`

interface StoredPrefs { mode?: ColorMode; brandColor?: string; motion?: MotionPreference }

const motionOf = (p: StoredPrefs): MotionPreference => (isMotionPreference(p.motion) ? p.motion : 'system')

function loadPrefs(userId?: string | null): StoredPrefs {
  try {
    const raw = localStorage.getItem(prefsKey(userId))
    if (raw) return JSON.parse(raw) as StoredPrefs
    // One-time carry-over of the old browser-wide mode so switching to
    // per-user storage does not silently flip everyone back to dark.
    const legacy = localStorage.getItem('seventh-ai-color-mode-v2')
    return legacy === 'light' ? { mode: 'light' } : {}
  } catch {
    return {}
  }
}

function savePrefs(userId: string | null | undefined, prefs: StoredPrefs) {
  try {
    localStorage.setItem(prefsKey(userId), JSON.stringify(prefs))
  } catch {
    // Private-browsing or a full quota. A preference that fails to persist
    // is not worth breaking the render over — it just won't survive reload.
  }
}

export function ColorModeProvider({ children }: { children: ReactNode }) {
  const userId = useAuthStore((s) => s.user?.id ?? null)

  const [mode, setMode] = useState<ColorMode>(() => loadPrefs(userId).mode ?? 'dark')
  const [brandColor, setBrandColorState] = useState<string>(
    () => loadPrefs(userId).brandColor ?? DEFAULT_BRAND,
  )
  const [motion, setMotionState] = useState<MotionPreference>(() => motionOf(loadPrefs(userId)))

  // Re-read when the signed-in user changes: sign in, sign out, or one
  // operator handing the desk to another. Without this the previous user's
  // accent would stay on screen for the next one.
  useEffect(() => {
    const p = loadPrefs(userId)
    setMode(p.mode ?? 'dark')
    setBrandColorState(p.brandColor ?? DEFAULT_BRAND)
    setMotionState(motionOf(p))
  }, [userId])

  // Written on the page's root, where the style sheet and the code that moves
  // things read it. Here and not in the setter, so it also follows a change of
  // user and is right from the first frame after a reload.
  useEffect(() => { applyMotionPreference(motion) }, [motion])

  const toggle = useCallback(() => {
    setMode((prev) => {
      const next = prev === 'dark' ? 'light' : 'dark'
      savePrefs(userId, { mode: next, brandColor, motion })
      return next
    })
  }, [userId, brandColor, motion])

  const setBrandColor = useCallback((color: string) => {
    setBrandColorState(color)
    savePrefs(userId, { mode, brandColor: color, motion })
  }, [userId, mode, motion])

  const reset = useCallback(() => {
    setBrandColorState(DEFAULT_BRAND)
    savePrefs(userId, { mode, brandColor: DEFAULT_BRAND, motion })
  }, [userId, mode, motion])

  const setMotion = useCallback((next: MotionPreference) => {
    setMotionState(next)
    savePrefs(userId, { mode, brandColor, motion: next })
  }, [userId, mode, brandColor])

  const theme = useMemo(() => createGlassTheme(mode, brandColor), [mode, brandColor])
  const value = useMemo(
    () => ({ mode, toggle, brandColor, setBrandColor, reset, motion, setMotion }),
    [mode, toggle, brandColor, setBrandColor, reset, motion, setMotion],
  )

  return (
    <ColorModeContext.Provider value={value}>
      <ThemeProvider theme={theme}>
        <CssBaseline />
        {children}
      </ThemeProvider>
    </ColorModeContext.Provider>
  )
}
