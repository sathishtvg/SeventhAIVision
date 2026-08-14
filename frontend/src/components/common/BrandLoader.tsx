import { Box, Typography, useTheme } from '@mui/material'

/**
 * The in-app counterpart to index.html's boot splash — same radar-sweep mark,
 * same wordmark — so a slow page inside the app looks like the same product
 * that just launched, not like a stray spinner.
 *
 * Theme-aware, unlike the boot splash: by the time this renders a user is
 * known and their light/dark choice is in effect.
 */
export function BrandLoader({
  label = 'Loading',
  /** `full` fills the viewport (route-level); `inline` sits inside a card. */
  variant = 'inline',
  /** Hide the wordmark for small inline slots where it would crowd. */
  showName = true,
}: {
  label?: string
  variant?: 'full' | 'inline'
  showName?: boolean
}) {
  const theme = useTheme()
  const primary = theme.palette.primary.main
  const secondary = theme.palette.secondary?.main ?? '#00D9C0'
  const size = variant === 'full' ? 108 : 68

  return (
    <Box
      role="status"
      aria-label={label}
      sx={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        gap: 2,
        ...(variant === 'full'
          ? { minHeight: '60vh', width: '100%' }
          : { py: 5, width: '100%' }),
      }}
    >
      <Box sx={{ position: 'relative', width: size, height: size, display: 'grid', placeItems: 'center' }}>
        {/* Expanding rings — the "listening" half of the mark. */}
        {[0, 1].map((i) => (
          <Box
            key={i}
            sx={{
              position: 'absolute',
              inset: 0,
              borderRadius: '50%',
              border: `1px solid ${primary}`,
              opacity: 0,
              animation: 'brand-loader-pulse 2.4s cubic-bezier(0,0,0.2,1) infinite',
              animationDelay: `${i * 0.8}s`,
              '@keyframes brand-loader-pulse': {
                '0%': { transform: 'scale(0.5)', opacity: 0 },
                '35%': { opacity: 0.7 },
                '100%': { transform: 'scale(1.15)', opacity: 0 },
              },
              '@media (prefers-reduced-motion: reduce)': {
                animation: 'none',
                opacity: 0.25,
              },
            }}
          />
        ))}
        {/* Rotating sweep, masked to a ring so the centre stays clear. */}
        <Box
          sx={{
            position: 'absolute',
            inset: '5%',
            borderRadius: '50%',
            background: `conic-gradient(from 0deg,
              transparent 0deg, transparent 250deg,
              ${secondary}47 330deg, ${primary}8c 360deg)`,
            WebkitMask: 'radial-gradient(circle, transparent 34%, #000 36%)',
            mask: 'radial-gradient(circle, transparent 34%, #000 36%)',
            animation: 'brand-loader-spin 2.2s linear infinite',
            '@keyframes brand-loader-spin': { to: { transform: 'rotate(360deg)' } },
            '@media (prefers-reduced-motion: reduce)': { animation: 'none', opacity: 0.5 },
          }}
        />
        <Box
          component="img"
          src="/favicon.svg"
          alt=""
          sx={{
            width: size * 0.34,
            height: 'auto',
            filter: `drop-shadow(0 0 12px ${primary}80)`,
          }}
        />
      </Box>

      {showName && (
        <Box sx={{ textAlign: 'center' }}>
          <Typography
            sx={{
              fontWeight: 800,
              fontSize: variant === 'full' ? '1.15rem' : '0.9rem',
              letterSpacing: '0.16em',
              textTransform: 'uppercase',
              background: `linear-gradient(100deg,
                ${theme.palette.text.primary} 0%, ${theme.palette.text.primary} 38%,
                ${primary} 50%, ${secondary} 58%,
                ${theme.palette.text.primary} 70%, ${theme.palette.text.primary} 100%)`,
              backgroundSize: '300% 100%',
              WebkitBackgroundClip: 'text',
              backgroundClip: 'text',
              color: 'transparent',
              WebkitTextFillColor: 'transparent',
              animation: 'brand-loader-shimmer 2.8s linear infinite',
              '@keyframes brand-loader-shimmer': {
                from: { backgroundPosition: '150% 0' },
                to: { backgroundPosition: '-150% 0' },
              },
              // Same fallback the PageHeader gradient carries: without
              // background-clip:text this would paint nothing at all.
              '@supports not ((-webkit-background-clip: text) or (background-clip: text))': {
                color: 'text.primary',
                WebkitTextFillColor: 'currentColor',
                background: 'none',
              },
              '@media (prefers-reduced-motion: reduce)': { animation: 'none' },
            }}
          >
            7<span style={{ fontSize: '0.6em', verticalAlign: 'super' }}>th</span> AI Vision
          </Typography>
          <Typography variant="caption" color="text.secondary" sx={{ letterSpacing: '0.18em' }}>
            {label.toUpperCase()}
          </Typography>
        </Box>
      )}
    </Box>
  )
}

export default BrandLoader
