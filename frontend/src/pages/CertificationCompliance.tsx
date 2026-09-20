/**
 * Certification compliance — who is rostered without what the site requires.
 *
 * NOTHING ON THIS PAGE BLOCKS ANYTHING. Requirements are reported against, not
 * enforced at rostering: an ops manager covering a 2am no-show must not be
 * stopped by the roster tool, or they stop using the roster tool and the record
 * leaves with them. So this page tells you what is wrong and leaves the
 * decision where it belongs.
 *
 * Two tabs because two audiences. "At risk" is the operations view, read with
 * training:read and site-scoped, so a supervisor sees their own sites. Setting
 * requirements needs certification:enforce — seeing where the roster falls
 * short should not come with the ability to quietly lower the bar.
 *
 * The empty state is load-bearing. Zero findings can mean the roster is clean
 * OR that nobody has said what to check, and those look identical until the
 * page says which. /summary returns requirements_configured for exactly that.
 */
import { useState } from 'react'
import {
  Alert, Box, Button, Chip, Dialog, DialogActions, DialogContent, DialogTitle,
  IconButton, MenuItem, Skeleton, Tab, Table, TableBody, TableCell,
  TableContainer, TableHead, TableRow, Tabs, TextField, Tooltip, Typography,
} from '@mui/material'
import Stack from '@/components/common/Stack'
import AddIcon from '@mui/icons-material/Add'
import DeleteIcon from '@mui/icons-material/Delete'
import BadgeIcon from '@mui/icons-material/Badge'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import {
  addRequirement, deleteRequirement, getSummary, listFindings, listRequirements,
  type FindingStatus,
} from '@/api/certificationCompliance'
import { getSites } from '@/api/sites'
import { usePermission } from '@/hooks/usePermission'

/** Severity order, matching the service. Worst first. */
const STATUS_STYLE: Record<FindingStatus, { color: 'error' | 'warning'; label: string; help: string }> = {
  MISSING: { color: 'error', label: 'Not held', help: 'No certification of this type is on file for this guard.' },
  REVOKED: { color: 'error', label: 'Revoked', help: 'On file, but marked no longer valid.' },
  EXPIRED: { color: 'error', label: 'Expired', help: 'Held, but not valid on the day of this shift.' },
  EXPIRING: { color: 'warning', label: 'Expiring', help: 'Valid on the day, but inside the warning window — a renewal takes weeks.' },
}

/** Common types, offered as suggestions rather than enforced as a list: an
 *  agency may track something nobody anticipated, and a closed dropdown would
 *  simply stop them recording it. */
const SUGGESTIONS = [
  'security_officer_license', 'first_aid', 'cpr', 'fire_warden',
  'workplace_safety', 'traffic_marshal',
]

export default function CertificationCompliancePage() {
  const [tab, setTab] = useState(0)
  const canEnforce = usePermission('certification:enforce')

  return (
    <Box>
      <Typography variant="h4" gutterBottom>Certification Compliance</Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
        Upcoming shifts whose guard does not hold what the site requires —
        checked against the day they actually work, not today
      </Typography>

      <Tabs value={tab} onChange={(_, v) => setTab(v)} sx={{ mb: 2 }}>
        <Tab label="At risk" />
        <Tab label="Requirements" />
      </Tabs>

      {tab === 0 ? <AtRisk /> : <Requirements canEnforce={canEnforce} />}
    </Box>
  )
}

function AtRisk() {
  const [days, setDays] = useState(45)

  const { data: summary } = useQuery({
    queryKey: ['cert-summary', days],
    queryFn: () => getSummary({ days }),
  })
  const { data: findings = [], isLoading } = useQuery({
    queryKey: ['cert-findings', days],
    queryFn: () => listFindings({ days }),
  })

  if (isLoading) return <Skeleton variant="rounded" height={280} />

  // The distinction the whole feature rests on. A tenant that has configured
  // nothing must not be shown the same green page as one that is genuinely
  // compliant.
  if (summary && summary.requirements_configured === 0) {
    return (
      <Alert severity="info" icon={<BadgeIcon />}>
        <Typography variant="subtitle2">Nothing is being checked yet</Typography>
        <Typography variant="body2">
          No certification requirements have been set, so no shift can be
          assessed. Add the licence your officers must hold on the Requirements
          tab — a tenant-wide rule covers every site at once.
        </Typography>
      </Alert>
    )
  }

  return (
    <Stack spacing={2}>
      <Stack direction="row" spacing={2} alignItems="center">
        <TextField
          select size="small" label="Looking ahead" value={days}
          onChange={(e) => setDays(Number(e.target.value))} sx={{ width: 180 }}
        >
          <MenuItem value={7}>7 days</MenuItem>
          <MenuItem value={14}>14 days</MenuItem>
          <MenuItem value={45}>45 days</MenuItem>
          <MenuItem value={90}>90 days</MenuItem>
        </TextField>
        {summary && (
          <Stack direction="row" spacing={1}>
            {(Object.keys(STATUS_STYLE) as FindingStatus[])
              .filter((s) => summary.by_status[s])
              .map((s) => (
                <Chip key={s} size="small" color={STATUS_STYLE[s].color}
                      label={`${summary.by_status[s]} ${STATUS_STYLE[s].label.toLowerCase()}`} />
              ))}
          </Stack>
        )}
      </Stack>

      {findings.length === 0 ? (
        <Alert severity="success">
          No shift in the next {days} days has a guard missing a required
          certification.
        </Alert>
      ) : (
        <TableContainer>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Guard</TableCell>
                <TableCell>Site</TableCell>
                <TableCell>Shift date</TableCell>
                <TableCell>Requires</TableCell>
                <TableCell>Status</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {findings.map((f) => (
                <TableRow key={f.id} hover>
                  <TableCell>{f.guard_name}</TableCell>
                  <TableCell>{f.site_name ?? '—'}</TableCell>
                  <TableCell>{f.shift_date}</TableCell>
                  <TableCell>
                    <code>{f.certification_type}</code>
                  </TableCell>
                  <TableCell>
                    <Tooltip title={STATUS_STYLE[f.status].help}>
                      <Chip size="small" color={STATUS_STYLE[f.status].color}
                            label={STATUS_STYLE[f.status].label} />
                    </Tooltip>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      )}
    </Stack>
  )
}

function Requirements({ canEnforce }: { canEnforce: boolean }) {
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const [form, setForm] = useState({ certification_type: '', site_id: '', warn_days_before: 30 })
  const [error, setError] = useState<string | null>(null)

  const { data: requirements = [], isLoading } = useQuery({
    queryKey: ['cert-requirements'],
    queryFn: () => listRequirements(),
  })
  const { data: sites = [] } = useQuery({ queryKey: ['sites'], queryFn: () => getSites() })

  const create = useMutation({
    mutationFn: () =>
      addRequirement({
        certification_type: form.certification_type.trim(),
        site_id: form.site_id || null,
        warn_days_before: form.warn_days_before,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['cert-requirements'] })
      qc.invalidateQueries({ queryKey: ['cert-summary'] })
      setOpen(false)
      setForm({ certification_type: '', site_id: '', warn_days_before: 30 })
      setError(null)
    },
    // The API answers 409 with a sentence when the requirement already exists;
    // showing it beats a generic failure the user cannot act on.
    onError: (e: any) =>
      setError(e?.response?.data?.detail ?? 'Could not add that requirement.'),
  })

  const remove = useMutation({
    mutationFn: (id: string) => deleteRequirement(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['cert-requirements'] })
      qc.invalidateQueries({ queryKey: ['cert-summary'] })
    },
  })

  if (isLoading) return <Skeleton variant="rounded" height={240} />

  return (
    <Stack spacing={2}>
      <Stack direction="row" justifyContent="space-between" alignItems="center">
        <Typography variant="body2" color="text.secondary">
          A requirement with no site applies everywhere — use it for the licence
          every officer must hold. A site requirement is a client's own term.
        </Typography>
        {canEnforce && (
          <Button startIcon={<AddIcon />} variant="contained"
                  onClick={() => setOpen(true)}>
            Add requirement
          </Button>
        )}
      </Stack>

      {requirements.length === 0 ? (
        <Alert severity="info">
          Nothing is required yet, so no shift is being checked.
        </Alert>
      ) : (
        <TableContainer>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Applies to</TableCell>
                <TableCell>Certification</TableCell>
                <TableCell>Warn before expiry</TableCell>
                {canEnforce && <TableCell align="right" />}
              </TableRow>
            </TableHead>
            <TableBody>
              {requirements.map((r) => (
                <TableRow key={r.id} hover>
                  <TableCell>
                    {r.site_id
                      ? r.site_name
                      : <Chip size="small" label="Every site" color="primary" variant="outlined" />}
                  </TableCell>
                  <TableCell><code>{r.certification_type}</code></TableCell>
                  <TableCell>{r.warn_days_before} days</TableCell>
                  {canEnforce && (
                    <TableCell align="right">
                      <IconButton size="small" onClick={() => remove.mutate(r.id)}>
                        <DeleteIcon fontSize="small" />
                      </IconButton>
                    </TableCell>
                  )}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      )}

      <Dialog open={open} onClose={() => setOpen(false)} fullWidth maxWidth="sm">
        <DialogTitle>Add a certification requirement</DialogTitle>
        <DialogContent>
          <Stack spacing={2} sx={{ mt: 1 }}>
            {error && <Alert severity="error">{error}</Alert>}
            <TextField
              label="Certification type" fullWidth
              value={form.certification_type}
              onChange={(e) => setForm({ ...form, certification_type: e.target.value })}
              helperText="Must match how it is recorded against the guard, e.g. security_officer_license"
            />
            <Stack direction="row" spacing={1} flexWrap="wrap">
              {SUGGESTIONS.map((s) => (
                <Chip key={s} size="small" label={s} variant="outlined"
                      onClick={() => setForm({ ...form, certification_type: s })} />
              ))}
            </Stack>
            <TextField
              select label="Applies to" fullWidth value={form.site_id}
              onChange={(e) => setForm({ ...form, site_id: e.target.value })}
            >
              <MenuItem value="">Every site (tenant baseline)</MenuItem>
              {sites.map((s: any) => (
                <MenuItem key={s.id} value={s.id}>{s.name}</MenuItem>
              ))}
            </TextField>
            <TextField
              type="number" label="Warn this many days before expiry" fullWidth
              value={form.warn_days_before}
              onChange={(e) => setForm({ ...form, warn_days_before: Number(e.target.value) })}
              helperText="A renewal takes weeks, so a warning on the day it lapses arrives too late to act on"
            />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setOpen(false)}>Cancel</Button>
          <Button variant="contained" disabled={!form.certification_type.trim() || create.isPending}
                  onClick={() => create.mutate()}>
            Add
          </Button>
        </DialogActions>
      </Dialog>
    </Stack>
  )
}
