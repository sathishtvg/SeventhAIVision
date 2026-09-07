import { useMemo, useState } from 'react'
import {
  Alert, Box, Button, Dialog, DialogActions, DialogContent, DialogTitle,
  IconButton, MenuItem, Select, Skeleton, TextField, ToggleButton,
  ToggleButtonGroup, Tooltip, Typography,
} from '@mui/material'
import Stack from '@/components/common/Stack'
import ChevronLeftIcon from '@mui/icons-material/ChevronLeft'
import LibraryAddIcon from '@mui/icons-material/LibraryAdd'
import ChevronRightIcon from '@mui/icons-material/ChevronRight'
import DeleteIcon from '@mui/icons-material/Delete'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import {
  assignGridCell, bulkAssignShifts, clearGridCell, getRosterGrid, listShiftDefinitions,
  type BulkAssignResult, type DayPattern, type GridCell, type GridEmployee,
  type ShiftDefinition,
} from '@/api/roster'
import { getSites } from '@/api/sites'
import { GlassCard } from '@/components/common/GlassCard'
import { usePermission } from '@/hooks/usePermission'

/** Colours for the grid and its legend, matching the Shifts page. */
const TYPE_COLOUR: Record<string, string> = {
  day: '#F5A524',
  night: '#6C63FF',
  general: '#00D9C0',
  split: '#FF9800',
}
const LEAVE_COLOUR = '#00A97F'
const OFF_COLOUR = '#5A6178'

function hexToRgb(hex: string) {
  const h = hex.replace('#', '')
  return `${parseInt(h.slice(0, 2), 16)},${parseInt(h.slice(2, 4), 16)},${parseInt(h.slice(4, 6), 16)}`
}

function isoDay(d: Date) {
  // Built from local parts rather than toISOString, which converts to UTC and
  // would shift the whole grid by a day for anyone east of Greenwich.
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

function monthBounds(anchor: Date) {
  const start = new Date(anchor.getFullYear(), anchor.getMonth(), 1)
  const end = new Date(anchor.getFullYear(), anchor.getMonth() + 1, 0)
  return { start: isoDay(start), end: isoDay(end) }
}

// ── Stats ────────────────────────────────────────────────────────────────────

function StatBlock({ value, label, colour }: { value: string | number; label: string; colour?: string }) {
  return (
    <Box sx={{ px: 2, py: 1, minWidth: 96, borderRight: '1px solid rgba(255,255,255,0.07)' }}>
      <Typography sx={{
        fontFamily: 'monospace', fontWeight: 800, fontSize: '1.15rem', lineHeight: 1.15,
        color: colour ?? 'text.primary',
      }}>
        {value}
      </Typography>
      <Typography variant="caption" sx={{
        display: 'block', fontSize: '0.58rem', letterSpacing: '0.09em',
        color: 'text.disabled', textTransform: 'uppercase',
      }}>
        {label}
      </Typography>
    </Box>
  )
}

// ── Assign / clear one cell ──────────────────────────────────────────────────

function CellDialog({ open, employee, day, existing, definitions, sites, onClose }: {
  open: boolean
  employee: GridEmployee | null
  day: string | null
  existing: GridCell[]
  definitions: ShiftDefinition[]
  sites: { id: string; name: string }[]
  onClose: () => void
}) {
  const qc = useQueryClient()
  const [definitionId, setDefinitionId] = useState('')
  const [siteId, setSiteId] = useState('')

  const invalidate = () => qc.invalidateQueries({ queryKey: ['roster-grid'] })

  const { mutate: assign, isPending, error, reset } = useMutation({
    mutationFn: () => assignGridCell({
      guard_user_id: employee!.guard_user_id,
      site_id: siteId,
      shift_definition_id: definitionId,
      on_date: day!,
    }),
    onSuccess: () => { invalidate(); setDefinitionId(''); onClose() },
  })

  const { mutate: clear } = useMutation({
    mutationFn: (shiftId: string) => clearGridCell(shiftId),
    onSuccess: invalidate,
  })

  // The API refuses leave clashes, overlaps and started shifts by name;
  // repeating its reason beats a dialog that just fails to close.
  const message = (error as { response?: { data?: { detail?: string } } } | null)
    ?.response?.data?.detail

  if (!employee || !day) return null

  const onLeave = employee.leave_days.includes(day)
  const dayLabel = new Date(`${day}T00:00:00`).toLocaleDateString(undefined, {
    weekday: 'long', day: 'numeric', month: 'long',
  })

  return (
    <Dialog open={open} onClose={() => { reset(); onClose() }} maxWidth="xs" fullWidth>
      <DialogTitle sx={{ pb: 0.5 }}>
        <Typography variant="subtitle1" sx={{ fontWeight: 800 }}>{employee.full_name}</Typography>
        <Typography variant="caption" color="text.secondary">{dayLabel}</Typography>
      </DialogTitle>
      <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 1.75, pt: 1.5 }}>
        {message && <Alert severity="warning">{message}</Alert>}

        {onLeave && (
          <Alert severity="info">
            On approved leave this day. Assigning a shift is blocked until the leave is removed.
          </Alert>
        )}

        {existing.length > 0 && (
          <Box>
            <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 0.75 }}>
              Already rostered
            </Typography>
            <Stack spacing={0.75}>
              {existing.map((c) => (
                <Stack
                  key={c.shift_id} direction="row" alignItems="center" spacing={1}
                  sx={{
                    px: 1.25, py: 0.75, borderRadius: '8px',
                    background: `rgba(${hexToRgb(c.colour || TYPE_COLOUR[c.shift_type] || OFF_COLOUR)},0.12)`,
                  }}
                >
                  <Box sx={{ flex: 1, minWidth: 0 }}>
                    <Typography variant="body2" sx={{ fontWeight: 600 }}>
                      {c.shift_name ?? `${new Date(c.start).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })} shift`}
                    </Typography>
                    <Typography variant="caption" color="text.secondary">
                      {c.site_name ?? 'No site'}
                    </Typography>
                  </Box>
                  <Tooltip title={c.status === 'scheduled' ? 'Remove from roster' : `Cannot remove a ${c.status} shift`}>
                    <span>
                      <IconButton
                        size="small" color="error" disabled={c.status !== 'scheduled'}
                        onClick={() => clear(c.shift_id)}
                        aria-label="Remove shift"
                      >
                        <DeleteIcon fontSize="small" />
                      </IconButton>
                    </span>
                  </Tooltip>
                </Stack>
              ))}
            </Stack>
          </Box>
        )}

        <Box>
          <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 0.75 }}>
            Assign a shift
          </Typography>
          <Stack spacing={1.25}>
            <Select
              size="small" displayEmpty value={definitionId} disabled={onLeave}
              onChange={(e) => setDefinitionId(e.target.value)}
              renderValue={(v) => {
                const d = definitions.find((x) => x.id === v)
                return d ? `${d.name} · ${d.start_time}–${d.end_time}` : 'Shift…'
              }}
            >
              {definitions.map((d) => (
                <MenuItem key={d.id} value={d.id}>
                  {d.name} · {d.start_time}–{d.end_time}{d.crosses_midnight ? ' +1' : ''}
                </MenuItem>
              ))}
            </Select>
            <Select
              size="small" displayEmpty value={siteId} disabled={onLeave}
              onChange={(e) => setSiteId(e.target.value)}
              renderValue={(v) => sites.find((s) => s.id === v)?.name ?? 'Site…'}
            >
              {sites.map((s) => <MenuItem key={s.id} value={s.id}>{s.name}</MenuItem>)}
            </Select>
          </Stack>
        </Box>
      </DialogContent>
      <DialogActions>
        <Button onClick={() => { reset(); onClose() }}>Close</Button>
        <Button
          variant="contained" disabled={isPending || onLeave || !definitionId || !siteId}
          onClick={() => assign()}
        >
          Assign
        </Button>
      </DialogActions>
    </Dialog>
  )
}


// ── Bulk assign ──────────────────────────────────────────────────────────────

const DAY_PATTERNS: { value: DayPattern; label: string; hint: string }[] = [
  { value: 'all', label: 'All days', hint: 'Every day in the range' },
  { value: 'weekdays', label: 'Weekdays only', hint: 'Monday to Friday' },
  { value: 'alternate', label: 'Alternate days', hint: 'Every other day from the start date' },
]

function BulkAssignDialog({ open, definitions, sites, defaultStart, defaultEnd, onClose }: {
  open: boolean
  definitions: ShiftDefinition[]
  sites: { id: string; name: string }[]
  defaultStart: string
  defaultEnd: string
  onClose: () => void
}) {
  const qc = useQueryClient()
  const [definitionId, setDefinitionId] = useState('')
  const [siteId, setSiteId] = useState('')
  const [from, setFrom] = useState(defaultStart)
  const [to, setTo] = useState(defaultEnd)
  const [pattern, setPattern] = useState<DayPattern>('all')
  const [result, setResult] = useState<BulkAssignResult | null>(null)

  const { mutate: apply, isPending, error, reset } = useMutation({
    mutationFn: () => bulkAssignShifts({
      shift_definition_id: definitionId,
      site_id: siteId,
      start_date: from,
      end_date: to,
      day_pattern: pattern,
    }),
    onSuccess: (res) => {
      setResult(res)
      qc.invalidateQueries({ queryKey: ['roster-grid'] })
    },
  })

  const message = (error as { response?: { data?: { detail?: string } } } | null)
    ?.response?.data?.detail

  const close = () => { reset(); setResult(null); onClose() }
  const skipped = result ? result.skipped_on_leave + result.skipped_clash : 0

  return (
    <Dialog open={open} onClose={close} maxWidth="xs" fullWidth>
      <DialogTitle sx={{ pb: 0.5 }}>
        Bulk assign
        <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
          Apply one shift to all staff at once
        </Typography>
      </DialogTitle>

      <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 1.5 }}>
        {message && <Alert severity="warning">{message}</Alert>}

        {result ? (
          /* The outcome is the point of this dialog: a planner needs to know
             what actually landed before they trust the grid behind it. */
          <Box>
            <Alert severity={result.created ? 'success' : 'info'} sx={{ mb: 1.5 }}>
              {result.created} shift{result.created === 1 ? '' : 's'} added
              {skipped > 0 && `, ${skipped} skipped`}
            </Alert>
            <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1 }}>
              {result.shift_name} at {result.site_name} · {result.days_targeted} day
              {result.days_targeted === 1 ? '' : 's'} × {result.staff_targeted} staff
            </Typography>
            <Stack spacing={0.5}>
              {result.per_guard.map((g) => (
                <Stack key={g.guard_user_id} direction="row" alignItems="center" spacing={1}
                       sx={{ px: 1, py: 0.5, borderRadius: '6px', background: 'rgba(255,255,255,0.03)' }}>
                  <Typography variant="body2" sx={{ flex: 1, minWidth: 0, fontSize: '0.8rem' }} noWrap>
                    {g.full_name}
                  </Typography>
                  <Typography variant="caption" sx={{ color: g.created ? '#00E396' : 'text.disabled' }}>
                    +{g.created}
                  </Typography>
                  {g.skipped_on_leave > 0 && (
                    <Tooltip title="Skipped: on approved leave">
                      <Typography variant="caption" sx={{ color: LEAVE_COLOUR }}>
                        {g.skipped_on_leave} leave
                      </Typography>
                    </Tooltip>
                  )}
                  {g.skipped_clash > 0 && (
                    <Tooltip title="Skipped: already rostered on an overlapping shift">
                      <Typography variant="caption" sx={{ color: '#F5A524' }}>
                        {g.skipped_clash} busy
                      </Typography>
                    </Tooltip>
                  )}
                </Stack>
              ))}
            </Stack>
          </Box>
        ) : (
          <>
            <Box>
              <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 0.5 }}>
                SHIFT
              </Typography>
              <Select
                size="small" fullWidth displayEmpty value={definitionId}
                onChange={(e) => setDefinitionId(e.target.value)}
                renderValue={(v) => {
                  const d = definitions.find((x) => x.id === v)
                  return d ? `${d.name} (${d.start_time}–${d.end_time})` : 'Choose a shift…'
                }}
              >
                {definitions.map((d) => (
                  <MenuItem key={d.id} value={d.id}>
                    {d.name} ({d.start_time}–{d.end_time}{d.crosses_midnight ? ' +1' : ''})
                  </MenuItem>
                ))}
              </Select>
            </Box>

            <Box>
              <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 0.5 }}>
                SITE
              </Typography>
              <Select
                size="small" fullWidth displayEmpty value={siteId}
                onChange={(e) => setSiteId(e.target.value)}
                renderValue={(v) => sites.find((s) => s.id === v)?.name ?? 'Choose a site…'}
              >
                {sites.map((s) => <MenuItem key={s.id} value={s.id}>{s.name}</MenuItem>)}
              </Select>
            </Box>

            <Box>
              <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 0.5 }}>
                DATE RANGE
              </Typography>
              <Stack direction="row" spacing={1.5}>
                <TextField
                  type="date" size="small" value={from} onChange={(e) => setFrom(e.target.value)}
                  sx={{ flex: 1 }} slotProps={{ inputLabel: { shrink: true } }}
                />
                <TextField
                  type="date" size="small" value={to} onChange={(e) => setTo(e.target.value)}
                  sx={{ flex: 1 }} slotProps={{ inputLabel: { shrink: true } }}
                />
              </Stack>
            </Box>

            <Box>
              <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 0.5 }}>
                DAY PATTERN
              </Typography>
              <ToggleButtonGroup
                size="small" exclusive value={pattern} fullWidth
                onChange={(_, v) => v && setPattern(v)}
              >
                {DAY_PATTERNS.map((p) => (
                  <ToggleButton key={p.value} value={p.value} sx={{ fontSize: '0.7rem', py: 0.6 }}>
                    {p.label}
                  </ToggleButton>
                ))}
              </ToggleButtonGroup>
              <Typography variant="caption" color="text.disabled" sx={{ display: 'block', mt: 0.5 }}>
                {DAY_PATTERNS.find((p) => p.value === pattern)?.hint}
              </Typography>
            </Box>

            {/* Said before the click, not after: a planner about to apply a
                month to a whole team should know the collisions are handled. */}
            <Alert severity="info" sx={{ py: 0.5 }}>
              Anyone on approved leave, or already rostered on an overlapping shift,
              is skipped — the rest still apply.
            </Alert>
          </>
        )}
      </DialogContent>

      <DialogActions>
        <Button onClick={close}>{result ? 'Done' : 'Cancel'}</Button>
        {!result && (
          <Button
            variant="contained" onClick={() => apply()}
            disabled={isPending || !definitionId || !siteId || !from || !to}
          >
            {isPending ? 'Applying…' : 'Apply to all staff'}
          </Button>
        )}
      </DialogActions>
    </Dialog>
  )
}

// ── The grid ─────────────────────────────────────────────────────────────────

export function RosterGrid() {
  const canManage = usePermission('shift:manage')
  const [anchor, setAnchor] = useState(() => new Date())
  const [siteFilter, setSiteFilter] = useState('')
  const [cell, setCell] = useState<{ employee: GridEmployee; day: string } | null>(null)
  const [bulkOpen, setBulkOpen] = useState(false)

  const { start, end } = useMemo(() => monthBounds(anchor), [anchor])

  const { data: grid, isLoading } = useQuery({
    queryKey: ['roster-grid', start, end, siteFilter],
    queryFn: () => getRosterGrid(start, end, siteFilter || undefined),
  })
  const { data: definitions = [] } = useQuery({
    queryKey: ['shift-definitions', false],
    queryFn: () => listShiftDefinitions(false),
  })
  const { data: allSites = [] } = useQuery({ queryKey: ['sites'], queryFn: () => getSites() })

  const monthLabel = anchor.toLocaleDateString(undefined, { month: 'short', year: 'numeric' })
  const stats = grid?.stats
  const days = grid?.days ?? []

  const coverageColour = !stats ? undefined
    : stats.coverage_pct >= 90 ? '#00E396'
    : stats.coverage_pct >= 60 ? '#F5A524'
    : '#FF4560'

  return (
    <Box>
      {/* ── Controls ─────────────────────────────────────────────── */}
      <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 1.5, flexWrap: 'wrap', gap: 1 }}>
        <Select
          size="small" displayEmpty value={siteFilter}
          onChange={(e) => setSiteFilter(e.target.value)}
          renderValue={(v) => allSites.find((s: { id: string }) => s.id === v)?.name ?? 'All sites'}
          sx={{ minWidth: 170 }}
        >
          <MenuItem value="">All sites</MenuItem>
          {allSites.map((s: { id: string; name: string }) => (
            <MenuItem key={s.id} value={s.id}>{s.name}</MenuItem>
          ))}
        </Select>

        <IconButton size="small" aria-label="Previous month"
                    onClick={() => setAnchor(new Date(anchor.getFullYear(), anchor.getMonth() - 1, 1))}>
          <ChevronLeftIcon />
        </IconButton>
        <Typography sx={{ fontWeight: 700, minWidth: 96, textAlign: 'center' }}>{monthLabel}</Typography>
        <IconButton size="small" aria-label="Next month"
                    onClick={() => setAnchor(new Date(anchor.getFullYear(), anchor.getMonth() + 1, 1))}>
          <ChevronRightIcon />
        </IconButton>

        <Box sx={{ flex: 1 }} />

        {canManage && (
          <Button
            size="small" variant="outlined" startIcon={<LibraryAddIcon sx={{ fontSize: 16 }} />}
            onClick={() => setBulkOpen(true)} disabled={definitions.length === 0}
          >
            Bulk Assign
          </Button>
        )}
      </Stack>

      {/* ── Stats ────────────────────────────────────────────────── */}
      <GlassCard sx={{ mb: 1.5, overflowX: 'auto' }}>
        <Stack direction="row" sx={{ minWidth: 'max-content' }}>
          <StatBlock value={stats?.staff ?? '—'} label="Staff" />
          <StatBlock value={stats?.day_shifts ?? '—'} label="Day shifts" colour={TYPE_COLOUR.day} />
          <StatBlock value={stats?.night_shifts ?? '—'} label="Night shifts" colour={TYPE_COLOUR.night} />
          <StatBlock value={stats?.days_off ?? '—'} label="Days off" />
          <Tooltip title={stats ? `${stats.filled_posts} shifts against ${stats.required_posts} posts the sites are staffed for` : ''}>
            <Box><StatBlock value={stats ? `${stats.coverage_pct}%` : '—'} label="Coverage" colour={coverageColour} /></Box>
          </Tooltip>
          <StatBlock value={grid?.sites.length ?? '—'} label="Sites" />
        </Stack>
      </GlassCard>

      {/* ── Grid ─────────────────────────────────────────────────── */}
      {isLoading ? (
        <GlassCard sx={{ p: 2 }}>
          <Stack spacing={1}>
            {[0, 1, 2, 3].map((i) => <Skeleton key={i} variant="rounded" height={38} />)}
          </Stack>
        </GlassCard>
      ) : !grid || grid.employees.length === 0 ? (
        <GlassCard sx={{ p: 4, textAlign: 'center' }}>
          <Typography color="text.secondary">No schedulable staff yet.</Typography>
        </GlassCard>
      ) : (
        <GlassCard sx={{ p: 0, overflow: 'hidden' }}>
          <Box sx={{ overflowX: 'auto' }}>
            <Box sx={{
              display: 'grid',
              gridTemplateColumns: `220px repeat(${days.length}, 62px)`,
              minWidth: 'max-content',
            }}>
              {/* Header */}
              <Box sx={{
                position: 'sticky', left: 0, zIndex: 2, px: 1.5, py: 1,
                background: 'rgba(13,17,28,0.98)',
                borderBottom: '1px solid rgba(255,255,255,0.1)',
              }}>
                <Typography variant="caption" sx={{
                  fontWeight: 700, letterSpacing: '0.08em', color: 'text.disabled',
                }}>
                  EMPLOYEE
                </Typography>
              </Box>
              {days.map((d) => {
                const date = new Date(`${d}T00:00:00`)
                const weekend = date.getDay() === 0 || date.getDay() === 6
                return (
                  <Box key={d} sx={{
                    px: 0.5, py: 1, textAlign: 'center',
                    borderBottom: '1px solid rgba(255,255,255,0.1)',
                    background: weekend ? 'rgba(255,255,255,0.03)' : undefined,
                  }}>
                    <Typography sx={{ fontFamily: 'monospace', fontWeight: 700, fontSize: '0.78rem', lineHeight: 1.1 }}>
                      {String(date.getDate()).padStart(2, '0')}
                    </Typography>
                    <Typography variant="caption" sx={{ fontSize: '0.55rem', color: 'text.disabled' }}>
                      {date.toLocaleDateString(undefined, { weekday: 'short' }).toUpperCase()}
                    </Typography>
                  </Box>
                )
              })}

              {/* Rows */}
              {grid.employees.map((e) => (
                <Box key={e.guard_user_id} sx={{ display: 'contents' }}>
                  <Box sx={{
                    position: 'sticky', left: 0, zIndex: 1, px: 1.5, py: 0.75, minWidth: 0,
                    background: 'rgba(13,17,28,0.98)',
                    borderBottom: '1px solid rgba(255,255,255,0.05)',
                  }}>
                    <Typography variant="body2" noWrap sx={{ fontWeight: 600, fontSize: '0.8rem' }}>
                      {e.full_name}
                    </Typography>
                    <Stack direction="row" spacing={0.5} alignItems="center" sx={{ minWidth: 0 }}>
                      {e.preferred_shift_type && (
                        <Tooltip title={`Prefers ${e.preferred_shift_type} shifts`}>
                          <Box sx={{
                            width: 6, height: 6, borderRadius: '50%', flexShrink: 0,
                            background: TYPE_COLOUR[e.preferred_shift_type],
                          }} />
                        </Tooltip>
                      )}
                      {/* The site column: where this person actually works in
                          the period, which is the whole point of one grid
                          across every site rather than one grid per site. */}
                      <Typography variant="caption" noWrap sx={{ color: 'text.disabled', fontSize: '0.6rem' }}>
                        {e.site_names.length ? e.site_names.join(' · ') : (e.designation || 'Unassigned')}
                      </Typography>
                    </Stack>
                  </Box>

                  {days.map((d) => {
                    const cells = e.cells[d] ?? []
                    const onLeave = e.leave_days.includes(d)
                    const date = new Date(`${d}T00:00:00`)
                    const weekend = date.getDay() === 0 || date.getDay() === 6
                    const first = cells[0]
                    const colour = first
                      ? (first.colour || TYPE_COLOUR[first.shift_type] || OFF_COLOUR)
                      : onLeave ? LEAVE_COLOUR : null

                    return (
                      <Box
                        key={d}
                        onClick={canManage ? () => setCell({ employee: e, day: d }) : undefined}
                        sx={{
                          borderBottom: '1px solid rgba(255,255,255,0.05)',
                          borderLeft: '1px solid rgba(255,255,255,0.04)',
                          background: colour
                            ? `rgba(${hexToRgb(colour)},0.18)`
                            : weekend ? 'rgba(255,255,255,0.02)' : undefined,
                          cursor: canManage ? 'pointer' : 'default',
                          display: 'flex', alignItems: 'center', justifyContent: 'center',
                          minHeight: 44, px: 0.25,
                          '&:hover': canManage ? { background: colour
                            ? `rgba(${hexToRgb(colour)},0.3)`
                            : 'rgba(255,255,255,0.07)' } : undefined,
                        }}
                      >
                        {first ? (
                          <Tooltip title={`${first.shift_name ?? 'Shift'} · ${first.site_name ?? 'No site'}${cells.length > 1 ? ` (+${cells.length - 1} more)` : ''}`}>
                            <Box sx={{ textAlign: 'center', minWidth: 0 }}>
                              <Typography sx={{
                                fontSize: '0.6rem', fontWeight: 700, lineHeight: 1.15,
                                color: colour!, whiteSpace: 'nowrap', overflow: 'hidden',
                                textOverflow: 'ellipsis', maxWidth: 56,
                              }}>
                                {(first.shift_name ?? first.shift_type).slice(0, 8)}
                              </Typography>
                              <Typography sx={{
                                fontSize: '0.52rem', color: 'text.disabled', whiteSpace: 'nowrap',
                                overflow: 'hidden', textOverflow: 'ellipsis', maxWidth: 56,
                              }}>
                                {cells.length > 1 ? `+${cells.length - 1} more` : (first.site_name ?? '')}
                              </Typography>
                            </Box>
                          </Tooltip>
                        ) : onLeave ? (
                          <Typography sx={{ fontSize: '0.58rem', fontWeight: 700, color: LEAVE_COLOUR }}>
                            LEAVE
                          </Typography>
                        ) : (
                          <Typography sx={{ fontSize: '0.9rem', color: 'rgba(255,255,255,0.16)', lineHeight: 1 }}>
                            {canManage ? '+' : ''}
                          </Typography>
                        )}
                      </Box>
                    )
                  })}
                </Box>
              ))}
            </Box>
          </Box>
        </GlassCard>
      )}

      {/* ── Legend ───────────────────────────────────────────────── */}
      <Stack direction="row" spacing={1.5} sx={{ mt: 1.25, flexWrap: 'wrap', gap: 1 }}>
        {[
          ['Day shift', TYPE_COLOUR.day],
          ['Night shift', TYPE_COLOUR.night],
          ['General', TYPE_COLOUR.general],
          ['Leave', LEAVE_COLOUR],
        ].map(([label, colour]) => (
          <Stack key={label} direction="row" spacing={0.6} alignItems="center">
            <Box sx={{ width: 10, height: 10, borderRadius: '3px', background: `rgba(${hexToRgb(colour)},0.5)` }} />
            <Typography variant="caption" color="text.secondary" sx={{ fontSize: '0.66rem' }}>{label}</Typography>
          </Stack>
        ))}
        <Box sx={{ flex: 1 }} />
        {canManage && (
          <Typography variant="caption" color="text.disabled" sx={{ fontSize: '0.66rem' }}>
            Click any cell to assign
          </Typography>
        )}
      </Stack>

      <BulkAssignDialog
        open={bulkOpen}
        definitions={definitions}
        sites={(allSites as { id: string; name: string }[]).map((s) => ({ id: s.id, name: s.name }))}
        defaultStart={start}
        defaultEnd={end}
        onClose={() => setBulkOpen(false)}
      />

      <CellDialog
        open={cell !== null}
        employee={cell?.employee ?? null}
        day={cell?.day ?? null}
        existing={cell ? (cell.employee.cells[cell.day] ?? []) : []}
        definitions={definitions}
        sites={(allSites as { id: string; name: string }[]).map((s) => ({ id: s.id, name: s.name }))}
        onClose={() => setCell(null)}
      />

      {definitions.length === 0 && (
        <Alert severity="info" sx={{ mt: 1.5 }}>
          No shifts defined yet. Create them on the Shifts page first — a cell can only be filled
          with a shift the company actually runs.
        </Alert>
      )}
    </Box>
  )
}

export default RosterGrid
