import { Box, Typography } from '@mui/material'
import { usePageLabelOverrides } from '@/hooks/usePageLabels'
import { resolvePageLabel, type PageKey } from '@/lib/pageLabels'
import { useFocusModeStore } from '@/store/focusMode'

interface PageHeaderProps {
  /** Look title and subtitle up from the shared registry, honouring any
   *  tenant rename. Prefer this over hardcoding copy into a page: it keeps
   *  the product's vocabulary in one reviewable place and is what the
   *  Settings > Page Names editor writes against. */
  pageKey?: PageKey
  /** Explicit copy, for headers that are not a whole page (dialogs, panels)
   *  or that need something dynamic. Wins over the registry when both given. */
  title?: string
  subtitle?: string
  action?: React.ReactNode
}

/**
 * The title block on every page.
 *
 * The title is deliberately solid text, not a gradient clipped to the glyphs.
 *
 * It was a gradient twice over: first hardcoded from #F1F5F9, which made
 * titles near-invisible in light mode, then theme-derived, which fixed light
 * mode but kept the underlying problem — a gradient ending in the accent
 * colour fades the back half of every word toward that accent, and the longer
 * the title the more of it is low-contrast. On a page of titles it reads as
 * washed out in both themes.
 *
 * The accent lives in the bar to the left instead, where it carries the brand
 * without being asked to also be readable text. Reported directly as titles
 * being hard to see across the app.
 */
export function PageHeader({ pageKey, title, subtitle, action }: PageHeaderProps) {
  // Called unconditionally — hooks cannot be skipped when pageKey is absent.
  const overrides = usePageLabelOverrides()
  const resolved = pageKey ? resolvePageLabel(pageKey, overrides) : undefined
  const shownTitle = title ?? resolved?.title ?? ''
  const shownSubtitle = subtitle ?? resolved?.subtitle

  /**
   * Full screen is a different job, so the header is a different size.
   *
   * At a desk the header orients you: large title, subtitle beneath it saying
   * what the page is for. Full screen the page is a wall display or a
   * gatehouse monitor that has been open all shift — nobody is reading the
   * subtitle by then, and every pixel it holds is one the board underneath
   * does not have. So the title and subtitle drop onto one line, a step
   * smaller, with the margin below cut to a third.
   *
   * It stays rather than disappearing because a wall display is often the
   * only thing on that screen, and an unlabelled board is unreadable to
   * anyone who did not open it.
   */
  const compact = useFocusModeStore((s) => s.isFocusMode)

  return (
    <Box sx={{
      display: 'flex',
      alignItems: compact ? 'center' : 'flex-start',
      justifyContent: 'space-between',
      gap: 1.5,
      mb: compact ? 1 : 3,
    }}>
      <Box sx={compact
        ? { display: 'flex', alignItems: 'baseline', gap: 1.25, minWidth: 0, flexWrap: 'wrap' }
        : undefined}>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, flexShrink: 0 }}>
          <Box
            sx={(theme) => ({
              width: 3,
              height: compact ? 20 : 28,
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
            variant={compact ? 'h6' : 'h5'}
            sx={{
              fontWeight: 800,
              letterSpacing: '-0.02em',
              color: 'text.primary',
              ...(compact ? { fontSize: '1.05rem', lineHeight: 1.2 } : null),
            }}
          >
            {shownTitle}
          </Typography>
        </Box>
        {shownSubtitle && (
          <Typography
            variant="body2"
            color="text.secondary"
            sx={compact
              // On the same line as the title, so no top margin and no indent
              // under the accent bar. Truncates rather than wrapping: a
              // subtitle that pushes the header to two lines has undone the
              // point of putting it on one.
              ? {
                  fontSize: '0.74rem',
                  minWidth: 0,
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                }
              : { mt: 0.5, ml: 3.25, fontSize: '0.82rem' }}
          >
            {shownSubtitle}
          </Typography>
        )}
      </Box>
      {action && <Box sx={{ flexShrink: 0 }}>{action}</Box>}
    </Box>
  )
}
