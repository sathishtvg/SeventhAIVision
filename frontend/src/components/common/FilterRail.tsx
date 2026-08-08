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
 * So the rail: collapsed it is a ~44px strip carrying a funnel icon and a
 * badge with the number of active filters. The page content keeps its full
 * height. Expanding it does NOT reflow the page — the panel is absolutely
 * positioned and floats over the content — because a filter panel that
 * shoves the table sideways every time you open it is just the original
 * problem rotated ninety degrees.
 *
 * AUTO-HIDE, AND THE PIN THAT DEFEATS IT
 * Unpinned (the default) the panel closes as soon as you pick something, and
 * on click-away or Escape: open, choose, gone. Someone doing a long triage
 * session who wants the panel to stay put can pin it, which both keeps it
 * open and switches it to push mode so it never overlaps what they are
 * reading. The pin is persisted per page key, since whether you want it is a
 * property of how you work, not of this particular visit.
 *
 * The badge matters more than it looks: with filters hidden, "why is this
 * list empty?" is otherwise unanswerable without opening the panel. The count
 * is visible at all times, and clearing everything is one click.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Badge, Box, Button, Chip, Divider, IconButton, Tooltip, Typography,
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

  const setPinnedPersisted = (v: boolean) => {
    setPinned(v)
    try { localStorage.setItem(PIN_PREFIX + storageKey, v ? '1' : '0') } catch { /* private mode */ }
    if (v) setOpen(false) // pinned drives visibility from here on
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
        // Pinned reserves real width (push mode). Unpinned always occupies
        // just the rail, so opening the panel cannot move the content.
        width: pinned ? PANEL_WIDTH : RAIL_WIDTH,
        transition: 'width 0.2s ease',
        alignSelf: 'stretch',
      }}
    >
      {/* ── Collapsed rail ─────────────────────────────────────────────── */}
      {!pinned && (
        <Stack
          sx={{
            width: RAIL_WIDTH,
            alignItems: 'center',
            gap: 1,
            pt: 1,
            height: '100%',
            borderRight: '1px solid rgba(255,255,255,0.07)',
          }}
        >
          <Tooltip title={activeCount > 0 ? `Filters (${activeCount} active)` : 'Filters'} placement="right">
            <IconButton
              size="small"
              aria-label={activeCount > 0 ? `Filters, ${activeCount} active` : 'Filters'}
              aria-expanded={expanded}
              onClick={() => setOpen((v) => !v)}
              sx={{
                color: activeCount > 0 ? '#6C63FF' : 'text.secondary',
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
              color: 'text.disabled',
              fontSize: '0.6rem',
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
            position: pinned ? 'static' : 'absolute',
            top: 0,
            left: 0,
            zIndex: 30,
            width: PANEL_WIDTH,
            maxHeight: pinned ? 'none' : '78vh',
            overflowY: 'auto',
            p: 1.5,
            borderRadius: pinned ? 0 : '12px',
            border: '1px solid rgba(255,255,255,0.1)',
            borderTop: pinned ? 'none' : undefined,
            borderLeft: pinned ? 'none' : undefined,
            borderBottom: pinned ? 'none' : undefined,
            background: pinned ? 'transparent' : 'rgba(13,17,28,0.97)',
            backdropFilter: pinned ? 'none' : 'blur(14px)',
            boxShadow: pinned ? 'none' : '0 12px 34px rgba(0,0,0,0.6)',
          }}
        >
          <Stack direction="row" sx={{ alignItems: 'center', mb: 1 }}>
            <Typography variant="caption"
              sx={{ fontWeight: 700, letterSpacing: '0.1em', textTransform: 'uppercase', color: 'text.secondary', flex: 1 }}>
              Filters
            </Typography>
            <Tooltip title={pinned ? 'Unpin (auto-hide)' : 'Keep open'}>
              <IconButton size="small" aria-label={pinned ? 'Unpin filters' : 'Pin filters open'}
                onClick={() => setPinnedPersisted(!pinned)}>
                {pinned
                  ? <PushPinIcon sx={{ fontSize: 15, color: '#6C63FF' }} />
                  : <PushPinOutlinedIcon sx={{ fontSize: 15 }} />}
              </IconButton>
            </Tooltip>
            {!pinned && (
              <IconButton size="small" aria-label="Close filters" onClick={() => setOpen(false)}>
                <CloseIcon sx={{ fontSize: 15 }} />
              </IconButton>
            )}
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
                sx={{ display: 'block', mb: 0.6, color: 'text.disabled', fontSize: '0.66rem' }}>
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
                      sx={{ fontSize: '0.68rem', height: 24 }}
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
