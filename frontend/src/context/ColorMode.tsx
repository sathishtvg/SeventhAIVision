import { createContext, useCallback, useContext, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { ThemeProvider, CssBaseline } from '@mui/material'
import { createGlassTheme } from '@/theme/glassmorphism'

type ColorMode = 'light' | 'dark'

interface ColorModeContextValue {
  mode: ColorMode
  toggle: () => void
  brandColor: string
  setBrandColor: (color: string) => void
}

const ColorModeContext = createContext<ColorModeContextValue>({
  mode: 'dark',
  toggle: () => {},
  brandColor: '#6C63FF',
  setBrandColor: () => {},
})

export function useColorMode() {
  return useContext(ColorModeContext)
}

export function ColorModeProvider({ children }: { children: ReactNode }) {
  const [mode, setMode] = useState<ColorMode>(() => {
    const stored = localStorage.getItem('seventh-ai-color-mode-v2')
    return stored === 'light' ? 'light' : 'dark'
  })

  const [brandColor, setBrandColor] = useState('#6C63FF')

  const toggle = useCallback(() => {
    setMode((prev) => {
      const next = prev === 'dark' ? 'light' : 'dark'
      localStorage.setItem('seventh-ai-color-mode-v2', next)
      return next
    })
  }, [])

  const theme = useMemo(() => createGlassTheme(mode, brandColor), [mode, brandColor])
  const value = useMemo(() => ({ mode, toggle, brandColor, setBrandColor }), [mode, toggle, brandColor])

  return (
    <ColorModeContext.Provider value={value}>
      <ThemeProvider theme={theme}>
        <CssBaseline />
        {children}
      </ThemeProvider>
    </ColorModeContext.Provider>
  )
}
