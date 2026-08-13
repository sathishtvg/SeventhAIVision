import { Box, Typography } from '@mui/material'

interface PageHeaderProps {
  title: string
  subtitle?: string
  action?: React.ReactNode
}

/**
 * The title block on every page.
 *
 * The title used to be painted with a hardcoded gradient starting at #F1F5F9
 * and `WebkitTextFillColor: transparent`. That reads beautifully on the dark
 * theme and is very nearly invisible on the light one — near-white text on a
 * near-white page — which is what made page titles disappear in light mode.
 *
 * It now derives from the theme instead of hardcoding, so both modes stay
 * legible and the accent follows whatever brand colour the signed-in user has
 * chosen. The gradient's second stop is the only decorative part; the first
 * stop is always the theme's own primary text colour, which is what guarantees
 * the contrast rather than hoping a fixed hex works on both backgrounds.
 */
export function PageHeader({ title, subtitle, action }: PageHeaderProps) {
  return (
    <Box sx={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', mb: 3 }}>
      <Box>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
          <Box
            sx={(theme) => ({
              width: 3,
              height: 28,
              borderRadius: 2,
              background: `linear-gradient(180deg, ${theme.palette.primary.main} 0%, ${theme.palette.secondary.main} 100%)`,
              // The glow is pure decoration on dark; on light it just smears
              // the accent bar into the page, so it is dropped there.
              boxShadow: theme.palette.mode === 'dark'
                ? `0 0 12px ${theme.palette.primary.main}b3`
                : 'none',
              flexShrink: 0,
            })}
          />
          <Typography
            variant="h5"
            sx={(theme) => ({
              fontWeight: 800,
              letterSpacing: '-0.02em',
              background: `linear-gradient(135deg, ${theme.palette.text.primary} 30%, ${theme.palette.primary.main} 100%)`,
              WebkitBackgroundClip: 'text',
              WebkitTextFillColor: 'transparent',
              backgroundClip: 'text',
              // Fallback for anything that cannot clip a background to text —
              // without it those renderers get transparent text and the title
              // vanishes completely rather than merely losing its gradient.
              '@supports not ((-webkit-background-clip: text) or (background-clip: text))': {
                background: 'none',
                WebkitTextFillColor: 'initial',
                color: theme.palette.text.primary,
              },
            })}
          >
            {title}
          </Typography>
        </Box>
        {subtitle && (
          <Typography
            variant="body2"
            color="text.secondary"
            sx={{ mt: 0.5, ml: 3.25, fontSize: '0.82rem' }}
          >
            {subtitle}
          </Typography>
        )}
      </Box>
      {action && <Box sx={{ flexShrink: 0 }}>{action}</Box>}
    </Box>
  )
}
