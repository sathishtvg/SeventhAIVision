import { useState } from 'react'
import {
  Alert, Autocomplete, Box, Button, Chip, Dialog, DialogActions, DialogContent,
  DialogTitle, IconButton, Skeleton, TextField, Tooltip, Typography,
} from '@mui/material'
// Not MUI's Stack: v9 dropped alignItems/justifyContent/flexWrap/gap as direct
// props, and this wrapper folds them back into sx. Importing the raw one is
// what broke CI here — `tsc -b` catches it, `tsc -p` does not.
import Stack from '@/components/common/Stack'
import AddIcon from '@mui/icons-material/Add'
import DeleteIcon from '@mui/icons-material/Delete'
import LightModeIcon from '@mui/icons-material/LightMode'
import DarkModeIcon from '@mui/icons-material/DarkMode'
import WarningAmberIcon from '@mui/icons-material/WarningAmber'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import {
  addDutyAssignment, getDutyAssignments, getDutyOverview, removeDutyAssignment,
  type DutyAssignment, type DutyOverviewRow,
} from '@/api/sites'
import { getUsers } from '@/api/users'
import { GlassCard } from '@/components/common/GlassCard'
import { PageHeader } from '@/components/common/PageHeader'

/** Same set the roster auto-scheduler treats as schedulable. */
const GUARD_ROLES = new Set([3, 4, 5, 8])

const SHIFTS = [
  { key: 'day' as const, label: 'Day duty', icon: <LightModeIcon sx={{ fontSize: 15 }} />, color: '#F5A524' },
  { key: 'night' as const, label: 'Night duty', icon: <DarkModeIcon sx={{ fontSize: 15 }} />, color: '#6C63FF' },
]

function hexToRgb(hex: string) {
  const h = hex.replace('#', '')
  return `${parseInt(h.slice(0, 2), 16)},${parseInt(h.slice(2, 4), 16)},${parseInt(h.slice(4, 6), 16)}`
}

// ── Site tile ────────────────────────────────────────────────────────────────

/** One shift type's strength on the overview tile. */
function StrengthBar({ label, icon, color, required, assigned }: {
  label: string; icon: React.ReactNode; color: string; required: number; assigned: number
}) {
  const short = assigned < required
  // A site that runs no night shift is not short of night guards. Saying so
  // would put a permanent warning on half the estate.
  const notRun = required === 0
  return (
    <Box sx={{
      flex: 1, minWidth: 0, px: 1.25, py: 0.9, borderRadius: '8px',
      background: short ? 'rgba(255,69,96,0.10)' : `rgba(${hexToRgb(color)},0.08)`,
      border: `1px solid ${short ? 'rgba(255,69,96,0.45)' : `rgba(${hexToRgb(color)},0.28)`}`,
    }}>
      <Stack direction="row" spacing={0.5} alignItems="center" sx={{ color: short ? '#FF4560' : color, mb: 0.3 }}>
        {icon}
        <Typography variant="caption" sx={{ fontWeight: 700, fontSize: '0.62rem', letterSpacing: '0.04em' }}>
          {label.toUpperCase()}
        </Typography>
      </Stack>
      <Typography sx={{ fontWeight: 800, fontSize: '1.05rem', lineHeight: 1.2, fontVariantNumeric: 'tabular-nums' }}>
        {assigned}<Typography component="span" sx={{ color: 'text.secondary', fontWeight: 600, fontSize: '0.8rem' }}>
          {' / '}{required}
        </Typography>
      </Typography>
      <Typography variant="caption" sx={{
        display: 'block', fontSize: '0.6rem',
        color: notRun ? 'text.disabled' : short ? '#FF4560' : 'text.secondary',
      }}>
        {notRun ? 'not staffed' : short ? `${required - assigned} short` : 'team complete'}
      </Typography>
    </Box>
  )
}

function SiteTile({ row, onOpen }: { row: DutyOverviewRow; onOpen: () => void }) {
  const dayShort = row.day_assigned < row.day_guards_required
  const nightShort = row.night_assigned < row.night_guards_required
  const short = dayShort || nightShort
  return (
    <GlassCard
      onClick={onOpen}
      sx={{
        p: 1.5, cursor: 'pointer', transition: 'transform .15s, box-shadow .15s',
        border: short ? '1px solid rgba(255,69,96,0.4)' : undefined,
        '&:hover': { transform: 'translateY(-2px)', boxShadow: '0 6px 20px rgba(0,0,0,0.25)' },
      }}
    >
      <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 1 }}>
        <Typography variant="subtitle2" noWrap sx={{ fontWeight: 800, flex: 1, minWidth: 0 }}>
          {row.site_name}
        </Typography>
        {short && (
          <Tooltip title="Fewer guards on the team than this site is staffed for">
            <WarningAmberIcon sx={{ fontSize: 17, color: '#FF4560' }} />
          </Tooltip>
        )}
      </Stack>
      <Stack direction="row" spacing={1}>
        <StrengthBar
          label="Day" icon={SHIFTS[0].icon} color={SHIFTS[0].color}
          required={row.day_guards_required} assigned={Number(row.day_assigned)}
        />
        <StrengthBar
          label="Night" icon={SHIFTS[1].icon} color={SHIFTS[1].color}
          required={row.night_guards_required} assigned={Number(row.night_assigned)}
        />
      </Stack>
    </GlassCard>
  )
}

// ── Team editor ──────────────────────────────────────────────────────────────

function TeamColumn({ siteId, shift, rows, guards }: {
  siteId: string
  shift: typeof SHIFTS[number]
  rows: DutyAssignment[]
  guards: { id: string; full_name: string | null; email: string }[]
}) {
  const qc = useQueryClient()
  const [picked, setPicked] = useState<{ id: string; label: string } | null>(null)

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ['duty-assignments', siteId] })
    qc.invalidateQueries({ queryKey: ['duty-overview'] })
  }

  const add = useMutation({
    mutationFn: (guardUserId: string) =>
      addDutyAssignment(siteId, { guard_user_id: guardUserId, shift_type: shift.key }),
    onSuccess: () => { setPicked(null); invalidate() },
  })

  const remove = useMutation({
    mutationFn: (assignmentId: string) => removeDutyAssignment(siteId, assignmentId),
    onSuccess: invalidate,
  })

  const onTeam = new Set(rows.map((r) => r.guard_user_id))
  const options = guards
    .filter((g) => !onTeam.has(g.id))
    .map((g) => ({ id: g.id, label: g.full_name || g.email }))

  return (
    <Box sx={{ flex: 1, minWidth: 0 }}>
      <Stack direction="row" spacing={0.75} alignItems="center" sx={{ mb: 1, color: shift.color }}>
        {shift.icon}
        <Typography variant="subtitle2" sx={{ fontWeight: 800 }}>{shift.label}</Typography>
        <Chip size="small" label={rows.length} sx={{ height: 18, fontSize: '0.62rem' }} />
      </Stack>

      <Stack spacing={0.75} sx={{ mb: 1.25 }}>
        {rows.length === 0 && (
          <Typography variant="caption" color="text.secondary">
            Nobody posted yet. The scheduler will draw from every guard until someone is.
          </Typography>
        )}
        {rows.map((r) => {
          // Not an error — a guard can be posted against their preference, and
          // sometimes has to be. Worth seeing, because the scheduler will keep
          // quietly working around it.
          const against = r.preferred_shift_type && r.preferred_shift_type !== shift.key
          return (
            <Stack
              key={r.id} direction="row" alignItems="center" spacing={1}
              sx={{
                px: 1, py: 0.6, borderRadius: '8px',
                background: 'rgba(255,255,255,0.03)',
                border: '1px solid rgba(255,255,255,0.07)',
              }}
            >
              <Box sx={{ flex: 1, minWidth: 0 }}>
                <Typography variant="body2" noWrap sx={{ fontWeight: 600, fontSize: '0.8rem' }}>
                  {r.full_name || r.email}
                </Typography>
                <Typography variant="caption" noWrap sx={{ display: 'block', color: 'text.secondary', fontSize: '0.63rem' }}>
                  {r.designation || 'Security Officer'}
                </Typography>
              </Box>
              {against && (
                <Tooltip title={`Prefers ${r.preferred_shift_type} shifts`}>
                  <Chip
                    size="small" variant="outlined" label={`prefers ${r.preferred_shift_type}`}
                    sx={{ height: 17, fontSize: '0.58rem' }}
                  />
                </Tooltip>
              )}
              <Tooltip title="Remove from this team">
                <IconButton
                  size="small" onClick={() => remove.mutate(r.id)} disabled={remove.isPending}
                  aria-label={`Remove ${r.full_name || r.email} from ${shift.label}`}
                >
                  <DeleteIcon sx={{ fontSize: 17 }} />
                </IconButton>
              </Tooltip>
            </Stack>
          )
        })}
      </Stack>

      <Stack direction="row" spacing={1}>
        <Autocomplete
          size="small" sx={{ flex: 1 }} options={options} value={picked}
          onChange={(_, v) => setPicked(v)}
          isOptionEqualToValue={(o, v) => o.id === v.id}
          renderInput={(params) => <TextField {...params} label={`Add to ${shift.label.toLowerCase()}`} />}
        />
        <Button
          variant="outlined" size="small" startIcon={<AddIcon />}
          disabled={!picked || add.isPending}
          onClick={() => picked && add.mutate(picked.id)}
        >
          Add
        </Button>
      </Stack>
    </Box>
  )
}

function SiteTeamDialog({ row, onClose }: { row: DutyOverviewRow | null; onClose: () => void }) {
  const { data: rows = [], isLoading } = useQuery({
    queryKey: ['duty-assignments', row?.site_id],
    queryFn: () => getDutyAssignments(row!.site_id),
    enabled: Boolean(row),
  })
  const { data: users = [] } = useQuery({
    queryKey: ['users'], queryFn: getUsers, enabled: Boolean(row),
  })

  if (!row) return null
  const guards = users.filter((u: any) => GUARD_ROLES.has(u.role_id) && u.is_active)

  return (
    <Dialog open onClose={onClose} maxWidth="md" fullWidth>
      <DialogTitle sx={{ pb: 1 }}>
        <Typography variant="subtitle1" sx={{ fontWeight: 800 }}>{row.site_name}</Typography>
        <Typography variant="caption" color="text.secondary">
          Staffed for {row.day_guards_required} on day duty and {row.night_guards_required} on night duty.
          Change those numbers on the Sites page.
        </Typography>
      </DialogTitle>
      <DialogContent dividers>
        {isLoading ? (
          <Stack spacing={1}>
            {[0, 1, 2, 3].map((i) => <Skeleton key={i} variant="rounded" height={44} />)}
          </Stack>
        ) : (
          <Stack direction={{ xs: 'column', md: 'row' }} spacing={3}>
            {SHIFTS.map((shift) => (
              <TeamColumn
                key={shift.key} siteId={row.site_id} shift={shift} guards={guards}
                rows={rows.filter((r) => r.shift_type === shift.key)}
              />
            ))}
          </Stack>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Done</Button>
      </DialogActions>
    </Dialog>
  )
}

// ── Page ─────────────────────────────────────────────────────────────────────

export function DutyAssignmentsPage() {
  const [openSite, setOpenSite] = useState<DutyOverviewRow | null>(null)

  const { data: overview = [], isLoading } = useQuery({
    queryKey: ['duty-overview'],
    queryFn: getDutyOverview,
    refetchInterval: 120_000,
  })

  // Short sites first — the whole point of the page is finding the ones that
  // cannot fill their posts, and those are never the ones sorted alphabetically
  // to the top.
  const shortfall = (r: DutyOverviewRow) =>
    Math.max(r.day_guards_required - Number(r.day_assigned), 0) +
    Math.max(r.night_guards_required - Number(r.night_assigned), 0)

  const sorted = [...overview].sort(
    (a, b) => shortfall(b) - shortfall(a) || a.site_name.localeCompare(b.site_name),
  )
  const shortCount = overview.filter((r) => shortfall(r) > 0).length

  return (
    <Box>
      <PageHeader pageKey="duty-assignments" />

      {shortCount > 0 && (
        <Alert severity="warning" sx={{ mb: 2 }}>
          {shortCount} {shortCount === 1 ? 'site has' : 'sites have'} fewer guards on the team than
          they are staffed for. The auto-scheduler will fill those posts from the wider pool and
          flag them as off-team.
        </Alert>
      )}

      {isLoading ? (
        <Box sx={{
          display: 'grid', gap: 1.5,
          gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))',
        }}>
          {[0, 1, 2, 3, 4, 5].map((i) => <Skeleton key={i} variant="rounded" height={128} />)}
        </Box>
      ) : sorted.length === 0 ? (
        <GlassCard sx={{ p: 4, textAlign: 'center' }}>
          <Typography color="text.secondary">No active sites yet.</Typography>
        </GlassCard>
      ) : (
        <Box sx={{
          display: 'grid', gap: 1.5, alignItems: 'start',
          gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))',
        }}>
          {sorted.map((row) => (
            <SiteTile key={row.site_id} row={row} onOpen={() => setOpenSite(row)} />
          ))}
        </Box>
      )}

      <SiteTeamDialog row={openSite} onClose={() => setOpenSite(null)} />
    </Box>
  )
}

export default DutyAssignmentsPage
