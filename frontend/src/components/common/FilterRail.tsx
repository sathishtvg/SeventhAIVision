/**
 * FilterRail — page filters as a collapsing left rail instead of chip rows
 * stacked above the content.
 *
 * WHY THIS EXISTS
 * Filters were rendered as wrapping rows of chips at the top of each page. On
 * Alerts that is three rows — status, site, module — and the module row alone
 * carries eleven chips, which wrap. Measured on the running app at 1280px:
 * the alerts table began 246px down the page. With the rail it begins at 89px.
 * The filters are set once and then ignored, but they were charging rent on
 * the most valuable space on the screen, permanently.
 *
 * So the rail: collapsed it is a ~44px strip on the RIGHT edge of the content
 * carrying a funnel icon and a badge with the number of active filters.
 *
 * IT NEVER REFLOWS THE PAGE. The strip holds a fixed 44px of layout at all
 * times and the expanded panel is absolutely positioned, floating leftward
 * over the grid. Opening, closing and pinning all leave the table exactly
 * where it was — a filter panel that shoves the content sideways every time
 * you open it is just the original problem rotated ninety degrees. The strip
 * stays in the layout rather than floating too, specifically so it cannot sit
 * on top of the row-action buttons that live at the right end of these
 * tables.
 *
 * AUTO-HIDE, AND THE PIN THAT DEFEATS IT
 * Unpinned (the default) the panel closes as soon as you pick something, and
 * on click-away or Escape: open, choose, gone. Someone doing a long triage
 * session can pin it, which only stops the auto-hide — a pinned panel still
 * overlays rather than pushing, because "never disturb the main content"
 * holds in both states. The pin is persisted per page key, since whether you
 * want it is a property of how you work, not of this particular visit.
 *
 * The badge matters more than it looks: with filters hidden, "why is this
 * list empty?" is otherwise unanswerable without opening the panel. The count
 * is visible at all times, and clearing everything is one click.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Badge, Box, Button, Chip, Divider, IconButton, Tooltip, Typography, useTheme,
} from '@mui/material'
import Stack from '@/components/common/Stack'
import FilterListIcon from '@mui/icons-material/FilterList'
import PushPinIcon from '@mui/icons-material/PushPin'
import PushPinOutlinedIcon from '@mui/icons-material/PushPinOutlined'
import CloseIcon from '@mui/icons-material/Close'

export interface FilterOption {
  value: string
  label: string
}

export interface FilterGroup {
  /** Stable key, used for React keys only. */
  key: string
  label: string
  options: FilterOption[]
  value: string
  onChange: (value: string) => void
  /** The value meaning "no filter applied". Defaults to ''. A group sitting
   * at this value is not counted as an active filter and is not listed in the
   * summary — that is what makes the badge mean "things I have narrowed". */
  allValue?: string
}

interface FilterRailProps {
  groups: FilterGroup[]
  /** Distinguishes persisted pin state between pages. */
  storageKey: string
}

const RAIL_WIDTH = 44
const PANEL_WIDTH = 268
const PIN_PREFIX = 'filterRail.pinned.'

export function FilterRail({ groups, storageKey }: FilterRailProps) {
  // The panel's surface is opaque and theme-dependent, so its text colours
  // have to be picked to contrast with THAT surface, not with the page. An
  // earlier version hardcoded a dark panel while taking text from the theme
  // tokens — correct in dark mode, dark-on-dark and unreadable in light.
  // Values below clear 4.5:1 against their own background in both modes.
  const isDark = useTheme().palette.mode === 'dark'
  const C = isDark
    ? { surface: 'rgba(13,17,28,0.98)', border: 'rgba(255,255,255,0.14)',
        heading: '#E6E9EF', label: '#B8BFD0', spine: '#9AA3B8' }
    : { surface: 'rgba(255,255,255,0.99)', border: 'rgba(0,0,0,0.16)',
        heading: '#0F172A', label: '#475569', spine: '#475569' }

  const [open, setOpen] = useState(false)
  const [pinned, setPinned] = useState(() => {
    try { return localStorage.getItem(PIN_PREFIX + storageKey) === '1' } catch { return false }
  })
  const wrapRef = useRef<HTMLDivElement | null>(null)

  const isActive = useCallback(
    (g: FilterGroup) => g.value !== (g.allValue ?? ''),
    [],
  )
  const activeGroups = useMemo(() => groups.filter(isActive), [groups, isActive])
  const activeCount = activeGroups.length

  const expanded = open || pinned

  /** Persist only. Kept free of visibility side-effects so the two callers
   * below can each decide what `open` should become — an earlier version had
   * unpin force open=true, which fought the close handler and left the panel
   * stuck open. */
  const persistPin = (v: boolean) => {
    setPinned(v)
    try { localStorage.setItem(PIN_PREFIX + storageKey, v ? '1' : '0') } catch { /* private mode */ }
  }

  const togglePin = () => {
    const next = !pinned
    persistPin(next)
    // Unpinning must not yank the panel away mid-use — keep it visible and
    // let the user close it (or let auto-hide take it on the next pick).
    if (!next) setOpen(true)
  }

  // The X closes regardless of pin. Clearing the pin at the same time is
  // required, not incidental — `expanded` is `open || pinned`, so closing a
  // pinned panel without unpinning would reopen it on the next render.
  const closePanel = () => {
    setOpen(false)
    if (pinned) persistPin(false)
  }

  // Escape closes, but only when floating — a pinned panel is part of the
  // layout and yanking it away on a stray keypress would be a surprise.
  useEffect(() => {
    if (!open || pinned) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, pinned])

  // Click-away, same reasoning as Escape.
  useEffect(() => {
    if (!open || pinned) return
    const onDown = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [open, pinned])

  const handlePick = (g: FilterGroup, value: string) => {
    g.onChange(value)
    // Auto-hide: the whole point of the rail. Pinned users opted out.
    if (!pinned) setOpen(false)
  }

  const clearAll = () => {
    groups.forEach((g) => { if (isActive(g)) g.onChange(g.allValue ?? '') })
  }

  return (
    <Box
      ref={wrapRef}
      sx={{
        position: 'relative',
        flexShrink: 0,
        // Always exactly the strip. Never widens, in any state — the panel
        // floats instead, so nothing the user does here moves the grid.
        width: RAIL_WIDTH,
        alignSelf: 'stretch',
      }}
    >
      {/* ── Collapsed rail ─────────────────────────────────────────────── */}
      {!expanded && (
        <Stack
          sx={{
            width: RAIL_WIDTH,
            alignItems: 'center',
            gap: 1,
            pt: 1,
            height: '100%',
            borderLeft: '1px solid rgba(255,255,255,0.07)',
          }}
        >
          <Tooltip title={activeCount > 0 ? `Filters (${activeCount} active)` : 'Filters'} placement="right">
            <IconButton
              size="small"
              aria-label={activeCount > 0 ? `Filters, ${activeCount} active` : 'Filters'}
              aria-expanded={expanded}
              onClick={() => setOpen((v) => !v)}
              sx={{
                color: activeCount > 0 ? '#6C63FF' : C.label,
                background: activeCount > 0 ? 'rgba(108,99,255,0.14)' : 'transparent',
                '&:hover': { background: 'rgba(108,99,255,0.2)' },
              }}
            >
              <Badge badgeContent={activeCount} color="primary"
                sx={{ '& .MuiBadge-badge': { fontSize: '0.6rem', height: 15, minWidth: 15 } }}>
                <FilterListIcon fontSize="small" />
              </Badge>
            </IconButton>
          </Tooltip>

          {/* Vertical "FILTERS" spine so the strip reads as a control and not
              as decorative furniture. */}
          <Typography
            variant="caption"
            sx={{
              writingMode: 'vertical-rl',
              textOrientation: 'mixed',
              color: C.spine,
              fontSize: '0.62rem',
              fontWeight: 600,
              letterSpacing: '0.14em',
              textTransform: 'uppercase',
              userSelect: 'none',
              mt: 0.5,
            }}
          >
            Filters
          </Typography>
        </Stack>
      )}

      {/* ── Panel ──────────────────────────────────────────────────────── */}
      {expanded && (
        <Box
          role="region"
          aria-label="Filters"
          sx={{
            // Anchored to the rail's right edge so it opens leftward across
            // the grid. Absolute in both pinned and unpinned states: pinning
            // changes only whether it auto-hides, never whether it displaces
            // the content behind it.
            position: 'absolute',
            top: 0,
            // Opens leftward, away from the edge it is attached to, so the
            // panel never lands on top of what the filter is filtering.
            right: 0,
            zIndex: 30,
            width: PANEL_WIDTH,
            maxHeight: '78vh',
            overflowY: 'auto',
            p: 1.5,
            borderRadius: '12px',
            border: `1px solid ${C.border}`,
            background: C.surface,
            backdropFilter: 'blur(14px)',
            boxShadow: '0 12px 34px rgba(0,0,0,0.6)',
          }}
        >
          <Stack direction="row" sx={{ alignItems: 'center', mb: 1 }}>
            <Typography variant="caption"
              sx={{ fontWeight: 700, letterSpacing: '0.1em', textTransform: 'uppercase', color: C.heading, flex: 1 }}>
              Filters
            </Typography>
            <Tooltip title={pinned ? 'Unpin (auto-hide)' : 'Keep open'}>
              <IconButton size="small" aria-label={pinned ? 'Unpin filters' : 'Pin filters open'}
                onClick={togglePin}>
                {pinned
                  ? <PushPinIcon sx={{ fontSize: 15, color: '#6C63FF' }} />
                  : <PushPinOutlinedIcon sx={{ fontSize: 15 }} />}
              </IconButton>
            </Tooltip>
            {/* Always available: a pinned panel overlays the grid, so there
                must be a way to dismiss it without unpinning first. */}
            <IconButton size="small" aria-label="Close filters" onClick={closePanel}>
              <CloseIcon sx={{ fontSize: 15 }} />
            </IconButton>
          </Stack>

          {activeCount > 0 && (
            <Button size="small" onClick={clearAll} sx={{ mb: 1, fontSize: '0.68rem' }}>
              Clear {activeCount} filter{activeCount === 1 ? '' : 's'}
            </Button>
          )}

          {groups.map((g, i) => (
            <Box key={g.key} sx={{ mb: 1.25 }}>
              {i > 0 && <Divider sx={{ mb: 1.25, borderColor: 'rgba(255,255,255,0.06)' }} />}
              <Typography variant="caption"
                sx={{ display: 'block', mb: 0.6, color: C.label, fontSize: '0.7rem', fontWeight: 600 }}>
                {g.label}
              </Typography>
              <Stack direction="row" spacing={0.6} sx={{ flexWrap: 'wrap', gap: 0.6 }}>
                {g.options.map((o) => {
                  const selected = g.value === o.value
                  return (
                    <Chip
                      key={o.value}
                      label={o.label}
                      size="small"
                      onClick={() => handlePick(g, selected && o.value !== (g.allValue ?? '')
                        ? (g.allValue ?? '')   // clicking the active one clears it
                        : o.value)}
                      variant={selected ? 'filled' : 'outlined'}
                      color={selected ? 'primary' : 'default'}
                      // MUI's default outlined chip sits at ~0.7 alpha with a
                      // very faint border; against this surface that reads as
                      // greyed-out/disabled rather than "available to pick".
                      sx={{
                        fontSize: '0.7rem',
                        height: 25,
                        ...(selected ? {} : {
                          color: C.heading,
                          borderColor: C.border,
                          '&:hover': { borderColor: '#6C63FF', color: '#6C63FF' },
                        }),
                      }}
                    />
                  )
                })}
              </Stack>
            </Box>
          ))}
        </Box>
      )}
    </Box>
  )
}
