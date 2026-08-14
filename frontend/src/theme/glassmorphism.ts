import { createTheme } from '@mui/material/styles'

// ── Colour helpers ────────────────────────────────────────────────────────────

function hex2rgb(hex: string): [number, number, number] {
  const h = hex.replace('#', '')
  const full = h.length === 3 ? h.split('').map((c) => c + c).join('') : h
  return [
    parseInt(full.slice(0, 2), 16),
    parseInt(full.slice(2, 4), 16),
    parseInt(full.slice(4, 6), 16),
  ]
}

function hex2rgba(hex: string, alpha: number): string {
  const [r, g, b] = hex2rgb(hex)
  return `rgba(${r},${g},${b},${alpha})`
}

function lighten(hex: string, amount: number): string {
  const [r, g, b] = hex2rgb(hex)
  const l = (c: number) => Math.min(255, c + Math.round(255 * amount)).toString(16).padStart(2, '0')
  return `#${l(r)}${l(g)}${l(b)}`
}

function darken(hex: string, amount: number): string {
  const [r, g, b] = hex2rgb(hex)
  const d = (c: number) => Math.max(0, c - Math.round(255 * amount)).toString(16).padStart(2, '0')
  return `#${d(r)}${d(g)}${d(b)}`
}

// ── Theme factory ─────────────────────────────────────────────────────────────

export function createGlassTheme(mode: 'light' | 'dark', primaryHex = '#6C63FF') {
  const isDark = mode === 'dark'
  const p  = primaryHex                    // primary
  const pl = lighten(primaryHex, 0.12)     // primary light
  const pd = darken(primaryHex,  0.12)     // primary dark

  /* ── Skill-spec glass values: opacity 0.15, border 0.20, blur 16px ── */
  const glassBg     = isDark ? 'rgba(255,255,255,0.10)' : 'rgba(255,255,255,0.82)'
  const glassAlt    = isDark ? 'rgba(255,255,255,0.15)' : 'rgba(255,255,255,0.90)'
  const glassBorder = isDark ? '1px solid rgba(255,255,255,0.18)' : '1px solid rgba(0,0,0,0.10)'
  const glassBlur   = 'blur(16px) saturate(180%)'
  const glassBlurLg = 'blur(24px) saturate(200%)'
  void glassAlt // referenced in component overrides below as needed

  return createTheme({
    palette: {
      mode,
      background: {
        default: '#020617',
        paper:   glassBg,
      },
      primary:   { main: p, light: pl, dark: pd },
      secondary: { main: '#00D9C0', light: '#33E3D0', dark: '#00A896' },
      error:     { main: '#FF4560', light: '#FF7088', dark: '#CC2040' },
      warning:   { main: '#F59E0B', light: '#FBC14A', dark: '#C47D08' },
      success:   { main: '#22C55E', light: '#4DD87E', dark: '#16A34A' },
      info:      { main: '#38BDF8', light: '#7ED4FA', dark: '#0284C7' },
      divider:   isDark ? 'rgba(255,255,255,0.10)' : 'rgba(0,0,0,0.08)',
      text: {
        primary:   isDark ? '#F8FAFC' : '#0F172A',
        secondary: isDark ? 'rgba(248,250,252,0.60)' : '#475569',
        disabled:  isDark ? 'rgba(248,250,252,0.32)' : '#94A3B8',
      },
    },

    typography: {
      fontFamily: '"Fira Sans", "Inter", "Roboto", "Helvetica", sans-serif',
      h1: { fontFamily: '"Fira Code", monospace', fontWeight: 700, letterSpacing: '-0.03em' },
      h2: { fontFamily: '"Fira Code", monospace', fontWeight: 700, letterSpacing: '-0.025em' },
      h3: { fontFamily: '"Fira Code", monospace', fontWeight: 600, letterSpacing: '-0.02em' },
      h4: { fontFamily: '"Fira Code", monospace', fontWeight: 600, letterSpacing: '-0.015em' },
      h5: { fontFamily: '"Fira Code", monospace', fontWeight: 600, letterSpacing: '-0.01em' },
      h6: { fontFamily: '"Fira Code", monospace', fontWeight: 600, letterSpacing: '-0.005em' },
      subtitle1: { fontWeight: 600 },
      subtitle2: { fontWeight: 600, letterSpacing: '0.005em' },
      body1:     { fontFamily: '"Fira Sans", sans-serif', lineHeight: 1.65 },
      body2:     { fontFamily: '"Fira Sans", sans-serif', lineHeight: 1.6 },
      caption:   { fontFamily: '"Fira Sans", sans-serif', letterSpacing: '0.02em', lineHeight: 1.5 },
      overline:  { fontFamily: '"Fira Sans", sans-serif', letterSpacing: '0.12em', fontWeight: 700 },
      button:    { fontFamily: '"Fira Sans", sans-serif', fontWeight: 600, letterSpacing: '0.01em' },
    },

    shape: { borderRadius: 12 },

    transitions: {
      duration:  { shortest: 100, shorter: 150, short: 200, standard: 250, complex: 350 },
      easing: {
        easeInOut: 'cubic-bezier(0.4, 0, 0.2, 1)',
        easeOut:   'cubic-bezier(0.0, 0.0, 0.2, 1)',
        easeIn:    'cubic-bezier(0.4, 0, 1, 1)',
        sharp:     'cubic-bezier(0.4, 0, 0.6, 1)',
      },
    },

    components: {
      MuiCssBaseline: {
        styleOverrides: {
          '*, *::before, *::after': { boxSizing: 'border-box' },
          // Tells the browser how to paint the controls IT owns rather than
          // us: the date field's calendar button, select arrows, spinners,
          // scrollbars, and the native date/time popup itself. This was
          // pinned to `dark` in index.css, so those icons were painted for a
          // dark UI no matter which theme was active — invisible against a
          // light background, and unable to follow a theme switch at all.
          ':root': { colorScheme: isDark ? 'dark' : 'light' },
          // The calendar/clock buttons are browser-drawn glyphs, so they take
          // no theme colour of their own. colorScheme above gets them the
          // right polarity; this gets them a sensible size and a cursor that
          // says they are clickable.
          'input[type="date"]::-webkit-calendar-picker-indicator, input[type="time"]::-webkit-calendar-picker-indicator, input[type="datetime-local"]::-webkit-calendar-picker-indicator':
            {
              cursor: 'pointer',
              opacity: isDark ? 0.85 : 0.65,
              transition: 'opacity 0.15s',
              '&:hover': { opacity: 1 },
            },
          body: {
            fontFamily: '"Fira Sans", "Inter", "Roboto", sans-serif',
            background: isDark
              ? 'radial-gradient(ellipse 80% 55% at 10% 0%, #0d0a2e 0%, #020617 60%) fixed, radial-gradient(ellipse 55% 45% at 92% 95%, #041228 0%, #020617 60%) fixed'
              : 'linear-gradient(135deg, #f0f0ff 0%, #ede8ff 45%, #e8f0ff 100%)',
            backgroundAttachment: 'fixed',
            minHeight: '100vh',
          },
        },
      },

      MuiPaper: {
        styleOverrides: {
          root: {
            backgroundImage: 'none',
            backgroundColor: glassBg,
            backdropFilter: glassBlur,
            WebkitBackdropFilter: glassBlur,
            border: glassBorder,
            transition: 'border-color 0.22s, box-shadow 0.22s',
          },
        },
      },

      MuiCard: {
        styleOverrides: {
          root: {
            backgroundImage: 'none',
            backgroundColor: glassBg,
            backdropFilter: glassBlur,
            WebkitBackdropFilter: glassBlur,
            border: glassBorder,
            boxShadow: isDark
              ? '0 4px 24px rgba(0,0,0,0.45), inset 0 1px 0 rgba(255,255,255,0.10)'
              : '0 4px 24px rgba(0,0,0,0.07), inset 0 1px 0 rgba(255,255,255,1)',
            transition: 'border-color 0.22s, box-shadow 0.22s, transform 0.22s',
            '&:hover': {
              borderColor: isDark ? hex2rgba(p, 0.40) : hex2rgba(p, 0.45),
              boxShadow: isDark
                ? `0 8px 40px rgba(0,0,0,0.55), 0 0 0 1px ${hex2rgba(p, 0.20)}, inset 0 1px 0 rgba(255,255,255,0.14)`
                : `0 8px 40px ${hex2rgba(p, 0.14)}`,
            },
          },
        },
      },

      MuiAppBar: {
        styleOverrides: {
          root: {
            backgroundImage: 'none',
            backgroundColor: isDark ? 'rgba(2,6,23,0.85)' : 'rgba(255,255,255,0.92)',
            backdropFilter: glassBlurLg,
            WebkitBackdropFilter: glassBlurLg,
            border: 'none',
            borderBottom: isDark ? '1px solid rgba(255,255,255,0.10)' : '1px solid rgba(0,0,0,0.07)',
            boxShadow: isDark
              ? `0 1px 0 ${hex2rgba(p, 0.18)}, 0 4px 24px rgba(0,0,0,0.40)`
              : `0 1px 0 ${hex2rgba(p, 0.10)}, 0 4px 24px rgba(0,0,0,0.05)`,
          },
        },
      },

      MuiDrawer: {
        styleOverrides: {
          paper: {
            backgroundImage: 'none',
            backgroundColor: isDark ? 'rgba(2,6,23,0.95)' : 'rgba(252,250,255,0.97)',
            backdropFilter: glassBlurLg,
            WebkitBackdropFilter: glassBlurLg,
            border: 'none',
            borderRight: isDark ? '1px solid rgba(255,255,255,0.10)' : '1px solid rgba(0,0,0,0.07)',
            boxShadow: isDark ? '4px 0 48px rgba(0,0,0,0.65)' : '4px 0 32px rgba(0,0,0,0.07)',
          },
        },
      },

      MuiButton: {
        styleOverrides: {
          root: {
            textTransform: 'none',
            fontWeight: 600,
            fontFamily: '"Fira Sans", sans-serif',
            borderRadius: 10,
            transition: 'all 0.22s cubic-bezier(0.4, 0, 0.2, 1)',
            cursor: 'pointer',
          },
          // NOTE: MUI v9's Button overridesResolver only reads `styles.root` /
          // `styles[variant]` / `styles[size...]` — combined keys like
          // `containedPrimary`/`outlinedPrimary`/`containedError` are never
          // looked up and are silently dead. Color-specific styling must live
          // on the bare `contained`/`outlined` keys as ownerState-branching
          // callbacks instead.
          contained: ({ ownerState }: { ownerState: { color?: string } }) => {
            const byColor: Record<string, { base: string; hover: string; shadow: string; shadowHover: string; disabled: string }> = {
              primary: {
                base: `linear-gradient(135deg, ${p} 0%, ${pd} 100%)`,
                hover: `linear-gradient(135deg, ${pl} 0%, ${p} 100%)`,
                shadow: `0 4px 16px ${hex2rgba(p, 0.50)}`,
                shadowHover: `0 6px 24px ${hex2rgba(p, 0.70)}`,
                disabled: hex2rgba(p, 0.22),
              },
              error: {
                base: 'linear-gradient(135deg, #FF4560 0%, #CC2040 100%)',
                hover: 'linear-gradient(135deg, #FF6070 0%, #FF4560 100%)',
                shadow: '0 4px 16px rgba(255,69,96,0.45)',
                shadowHover: '0 6px 24px rgba(255,69,96,0.65)',
                disabled: 'rgba(255,69,96,0.22)',
              },
              success: {
                base: 'linear-gradient(135deg, #22C55E 0%, #16A34A 100%)',
                hover: 'linear-gradient(135deg, #34D06E 0%, #22C55E 100%)',
                shadow: '0 4px 16px rgba(34,197,94,0.45)',
                shadowHover: '0 6px 24px rgba(34,197,94,0.65)',
                disabled: 'rgba(34,197,94,0.22)',
              },
            }
            const c = byColor[ownerState.color ?? '']
            if (!c) return {}
            return {
              background: c.base,
              boxShadow: c.shadow,
              '&:hover': { background: c.hover, boxShadow: c.shadowHover, transform: 'translateY(-1px)' },
              '&:active': { transform: 'translateY(0)' },
              '&.Mui-disabled': { background: c.disabled, boxShadow: 'none' },
            }
          },
          outlined: ({ ownerState }: { ownerState: { color?: string } }) => {
            if (ownerState.color !== 'primary') return {}
            return {
              borderColor: hex2rgba(p, 0.50),
              backdropFilter: 'blur(8px)',
              '&:hover': {
                borderColor: p,
                backgroundColor: hex2rgba(p, 0.12),
                boxShadow: `0 0 16px ${hex2rgba(p, 0.25)}`,
              },
            }
          },
        },
      },

      MuiIconButton: {
        styleOverrides: {
          root: {
            borderRadius: 10,
            transition: 'all 0.18s ease',
            cursor: 'pointer',
            '&:hover': {
              backgroundColor: isDark ? 'rgba(255,255,255,0.10)' : 'rgba(0,0,0,0.06)',
              transform: 'scale(1.08)',
            },
            '&:active': { transform: 'scale(0.95)' },
          },
        },
      },

      MuiChip: {
        styleOverrides: {
          root: {
            fontWeight: 600,
            fontFamily: '"Fira Sans", sans-serif',
            backdropFilter: 'blur(8px)',
            transition: 'all 0.18s ease',
            cursor: 'default',
          },
          colorPrimary: {
            backgroundColor: hex2rgba(p, 0.18),
            color: pl,
            border: `1px solid ${hex2rgba(p, 0.38)}`,
          },
          colorSecondary: {
            backgroundColor: 'rgba(0,217,192,0.14)',
            color: '#33E3D0',
            border: '1px solid rgba(0,217,192,0.32)',
          },
          colorError: {
            backgroundColor: 'rgba(255,69,96,0.15)',
            color: '#FF8099',
            border: '1px solid rgba(255,69,96,0.32)',
          },
          colorWarning: {
            backgroundColor: 'rgba(245,158,11,0.14)',
            color: '#FBC14A',
            border: '1px solid rgba(245,158,11,0.32)',
          },
          colorSuccess: {
            backgroundColor: 'rgba(34,197,94,0.14)',
            color: '#4DD87E',
            border: '1px solid rgba(34,197,94,0.32)',
          },
          colorInfo: {
            backgroundColor: 'rgba(56,189,248,0.14)',
            color: '#7ED4FA',
            border: '1px solid rgba(56,189,248,0.32)',
          },
        },
      },

      MuiToggleButtonGroup: {
        styleOverrides: {
          root: {
            backgroundColor: isDark ? 'rgba(255,255,255,0.04)' : 'rgba(0,0,0,0.02)',
            borderRadius: 10,
            padding: 3,
            gap: 2,
          },
          grouped: {
            border: '0 !important',
            borderRadius: '8px !important',
            marginLeft: '0 !important',
          },
        },
      },

      MuiToggleButton: {
        styleOverrides: {
          root: {
            textTransform: 'none',
            fontWeight: 600,
            fontFamily: '"Fira Sans", sans-serif',
            fontSize: '0.8rem',
            color: isDark ? 'rgba(248,250,252,0.60)' : '#475569',
            padding: '5px 14px',
            transition: 'all 0.18s ease',
            cursor: 'pointer',
            '&:hover': {
              backgroundColor: isDark ? 'rgba(255,255,255,0.08)' : 'rgba(0,0,0,0.05)',
            },
            '&.Mui-selected': {
              color: '#fff',
              background: `linear-gradient(135deg, ${p} 0%, ${pd} 100%)`,
              boxShadow: `0 2px 10px ${hex2rgba(p, 0.45)}`,
              '&:hover': {
                background: `linear-gradient(135deg, ${pl} 0%, ${p} 100%)`,
              },
            },
          },
        },
      },

      MuiBadge: {
        styleOverrides: {
          badge: {
            fontWeight: 700,
            fontSize: '0.62rem',
            boxShadow: '0 0 8px rgba(255,69,96,0.65)',
          },
        },
      },

      MuiListItemButton: {
        styleOverrides: {
          root: {
            borderRadius: 10,
            transition: 'all 0.18s ease',
            cursor: 'pointer',
            '&.Mui-selected': {
              background: `linear-gradient(90deg, ${hex2rgba(p, 0.22)} 0%, ${hex2rgba(p, 0.06)} 100%)`,
              '&:hover': {
                background: `linear-gradient(90deg, ${hex2rgba(p, 0.30)} 0%, ${hex2rgba(p, 0.10)} 100%)`,
              },
            },
            '&:hover': {
              backgroundColor: isDark ? 'rgba(255,255,255,0.07)' : 'rgba(0,0,0,0.04)',
              transform: 'translateX(2px)',
            },
          },
        },
      },

      MuiTextField: {
        styleOverrides: {
          root: {
            '& .MuiOutlinedInput-root': {
              fontFamily: '"Fira Sans", sans-serif',
              backgroundColor: isDark ? 'rgba(255,255,255,0.05)' : 'rgba(0,0,0,0.02)',
              backdropFilter: 'blur(8px)',
              borderRadius: 10,
              transition: 'box-shadow 0.22s',
              '& fieldset': {
                borderColor: isDark ? 'rgba(255,255,255,0.18)' : 'rgba(0,0,0,0.18)',
                transition: 'border-color 0.22s',
              },
              '&:hover fieldset': {
                borderColor: isDark ? 'rgba(255,255,255,0.32)' : 'rgba(0,0,0,0.30)',
              },
              '&.Mui-focused': {
                boxShadow: `0 0 0 3px ${hex2rgba(p, 0.22)}`,
                '& fieldset': { borderColor: p, borderWidth: '1.5px' },
              },
            },
          },
        },
      },

      MuiTableContainer: {
        styleOverrides: {
          root: { backgroundImage: 'none', backgroundColor: 'transparent' },
        },
      },

      MuiTableCell: {
        styleOverrides: {
          head: {
            backgroundColor: isDark ? hex2rgba(p, 0.09) : hex2rgba(p, 0.05),
            fontWeight: 700,
            fontFamily: '"Fira Sans", sans-serif',
            fontSize: '0.68rem',
            textTransform: 'uppercase',
            letterSpacing: '0.10em',
            color: isDark ? 'rgba(248,250,252,0.52)' : 'rgba(0,0,0,0.50)',
            borderBottom: isDark ? `1px solid ${hex2rgba(p, 0.22)}` : `1px solid ${hex2rgba(p, 0.14)}`,
          },
          body: {
            fontFamily: '"Fira Sans", sans-serif',
            borderBottom: isDark ? '1px solid rgba(255,255,255,0.06)' : '1px solid rgba(0,0,0,0.05)',
            fontSize: '0.85rem',
          },
        },
      },

      MuiTableRow: {
        styleOverrides: {
          root: {
            transition: 'background-color 0.15s',
            '&:hover': {
              backgroundColor: isDark ? hex2rgba(p, 0.07) : hex2rgba(p, 0.04),
            },
            '&:last-child td': { borderBottom: 0 },
          },
        },
      },

      MuiDialog: {
        styleOverrides: {
          paper: {
            backgroundImage: 'none',
            backgroundColor: isDark ? 'rgba(8,12,30,0.96)' : 'rgba(255,255,255,0.98)',
            backdropFilter: glassBlurLg,
            WebkitBackdropFilter: glassBlurLg,
            border: isDark ? '1px solid rgba(255,255,255,0.18)' : '1px solid rgba(0,0,0,0.10)',
            boxShadow: isDark
              ? `0 24px 80px rgba(0,0,0,0.80), 0 0 0 1px ${hex2rgba(p, 0.15)}`
              : '0 24px 80px rgba(0,0,0,0.16)',
          },
          backdrop: {
            backdropFilter: 'blur(6px)',
            backgroundColor: 'rgba(2,6,23,0.72)',
          },
        },
      },

      MuiMenu: {
        styleOverrides: {
          paper: {
            backgroundImage: 'none',
            backgroundColor: isDark ? 'rgba(8,12,30,0.97)' : 'rgba(255,255,255,0.99)',
            backdropFilter: glassBlurLg,
            WebkitBackdropFilter: glassBlurLg,
            border: isDark ? '1px solid rgba(255,255,255,0.18)' : '1px solid rgba(0,0,0,0.10)',
            boxShadow: isDark ? '0 16px 48px rgba(0,0,0,0.70)' : '0 16px 48px rgba(0,0,0,0.14)',
          },
          list: { padding: '6px' },
        },
      },

      MuiMenuItem: {
        styleOverrides: {
          root: {
            borderRadius: 8,
            fontSize: '0.875rem',
            fontFamily: '"Fira Sans", sans-serif',
            transition: 'background 0.15s',
            margin: '1px 0',
            '&:hover': { backgroundColor: isDark ? hex2rgba(p, 0.12) : hex2rgba(p, 0.08) },
            '&.Mui-selected': {
              backgroundColor: isDark ? hex2rgba(p, 0.18) : hex2rgba(p, 0.10),
              '&:hover': { backgroundColor: isDark ? hex2rgba(p, 0.24) : hex2rgba(p, 0.16) },
            },
          },
        },
      },

      MuiTooltip: {
        styleOverrides: {
          tooltip: {
            backgroundColor: isDark ? 'rgba(8,12,30,0.96)' : 'rgba(15,23,42,0.94)',
            backdropFilter: 'blur(16px)',
            border: `1px solid ${hex2rgba(p, 0.28)}`,
            fontSize: '0.75rem',
            fontWeight: 500,
            fontFamily: '"Fira Sans", sans-serif',
            boxShadow: '0 8px 24px rgba(0,0,0,0.50)',
            borderRadius: 8,
          },
          arrow: { color: isDark ? 'rgba(8,12,30,0.96)' : 'rgba(15,23,42,0.94)' },
        },
      },

      MuiTabs: {
        styleOverrides: {
          root: {
            borderBottom: isDark ? '1px solid rgba(255,255,255,0.10)' : '1px solid rgba(0,0,0,0.08)',
            minHeight: 44,
          },
          indicator: {
            background: `linear-gradient(90deg, ${p} 0%, #00D9C0 100%)`,
            height: 2.5,
            borderRadius: 2,
            boxShadow: `0 0 12px ${hex2rgba(p, 0.75)}`,
          },
        },
      },

      MuiTab: {
        styleOverrides: {
          root: {
            textTransform: 'none',
            fontWeight: 600,
            fontFamily: '"Fira Sans", sans-serif',
            fontSize: '0.875rem',
            minHeight: 44,
            color: isDark ? 'rgba(248,250,252,0.45)' : 'rgba(0,0,0,0.45)',
            transition: 'color 0.2s',
            cursor: 'pointer',
            '&.Mui-selected': { color: isDark ? '#F8FAFC' : 'rgba(0,0,0,0.87)' },
          },
        },
      },

      MuiDivider: {
        styleOverrides: {
          root: { borderColor: isDark ? 'rgba(255,255,255,0.10)' : 'rgba(0,0,0,0.08)' },
        },
      },

      MuiLinearProgress: {
        styleOverrides: {
          root: {
            backgroundColor: isDark ? 'rgba(255,255,255,0.08)' : 'rgba(0,0,0,0.07)',
            borderRadius: 6,
          },
          bar: {
            background: `linear-gradient(90deg, ${p} 0%, #00D9C0 100%)`,
            borderRadius: 6,
          },
        },
      },

      MuiSwitch: {
        styleOverrides: {
          switchBase: {
            '&.Mui-checked + .MuiSwitch-track': {
              background: `linear-gradient(90deg, ${p}, #00D9C0)`,
              opacity: 1,
            },
          },
          track: {
            backgroundColor: isDark ? 'rgba(255,255,255,0.20)' : 'rgba(0,0,0,0.18)',
            opacity: 1,
          },
        },
      },

      MuiAlert: {
        styleOverrides: {
          root: {
            backdropFilter: 'blur(12px)',
            border: '1px solid',
            borderRadius: 12,
            fontFamily: '"Fira Sans", sans-serif',
          },
          // NOTE: like MuiButton above, MUI v9's Alert overridesResolver only
          // reads `styles.root` / `styles[variant]` — combined keys like
          // `standardError`/`standardWarning` are never looked up.
          standard: ({ ownerState }: { ownerState: { color?: string } }) => {
            const byColor: Record<string, { bg: string; border: string; color: string }> = {
              error:   { bg: 'rgba(255,69,96,0.12)',  border: 'rgba(255,69,96,0.30)',  color: '#FF8099' },
              warning: { bg: 'rgba(245,158,11,0.12)', border: 'rgba(245,158,11,0.30)', color: '#FBC14A' },
              success: { bg: 'rgba(34,197,94,0.12)',  border: 'rgba(34,197,94,0.30)',  color: '#4DD87E' },
              info:    { bg: 'rgba(56,189,248,0.12)', border: 'rgba(56,189,248,0.30)', color: '#7ED4FA' },
            }
            const c = byColor[ownerState.color ?? '']
            if (!c) return {}
            return { backgroundColor: c.bg, borderColor: c.border, color: c.color }
          },
        },
      },

      MuiSelect: {
        styleOverrides: {
          outlined: {
            fontFamily: '"Fira Sans", sans-serif',
            backgroundColor: isDark ? 'rgba(255,255,255,0.05)' : 'rgba(0,0,0,0.02)',
          },
        },
      },

      MuiSkeleton: {
        styleOverrides: {
          root: {
            backgroundColor: isDark ? 'rgba(255,255,255,0.08)' : 'rgba(0,0,0,0.06)',
            '&::after': {
              background: 'linear-gradient(90deg, transparent, rgba(255,255,255,0.06), transparent)',
            },
          },
        },
      },

      MuiAccordion: {
        styleOverrides: {
          root: {
            backgroundImage: 'none',
            backgroundColor: isDark ? 'rgba(255,255,255,0.05)' : 'rgba(255,255,255,0.82)',
            backdropFilter: 'blur(12px)',
            border: glassBorder,
            borderRadius: '12px !important',
            marginBottom: 8,
            '&::before': { display: 'none' },
            '&.Mui-expanded': { margin: '0 0 8px 0' },
          },
        },
      },
    },
  })
}

export const theme = createGlassTheme('dark')
