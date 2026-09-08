/**
 * Handovers — the moment responsibility transfers, and whether anybody agreed.
 *
 * The page opens on the queue of handovers that still need somebody: submitted
 * and awaiting acceptance, or disputed and awaiting a supervisor. Everything
 * accepted is history and sorts below.
 *
 * A disputed handover is the reason the feature exists. The incoming guard
 * counted eleven keys against a handover claiming twelve; without a way to say
 * so they either sign for something they do not believe or the discrepancy
 * vanishes. So the dispute reason is shown at the top of the card, in red,
 * until a supervisor closes it out.
 *
 * The second tab maintains the checklists themselves — one company default,
 * plus per-site exceptions.
 */
import { useMemo, useState } from 'react'
import {
  Alert, Box, Button, Checkbox, Chip, Dialog, DialogActions, DialogContent,
  DialogTitle, IconButton, MenuItem, Skeleton, Switch, Tab, Tabs, TextField,
  Tooltip, Typography,
} from '@mui/material'
import Stack from '@/components/common/Stack'
import AddIcon from '@mui/icons-material/Add'
import DeleteIcon from '@mui/icons-material/Delete'
import SwapHorizIcon from '@mui/icons-material/SwapHoriz'
import GavelIcon from '@mui/icons-material/Gavel'
import CheckCircleOutlineIcon from '@mui/icons-material/CheckCircleOutlineOutlined'
import ReportProblemIcon from '@mui/icons-material/ReportProblem'
import ChecklistIcon from '@mui/icons-material/Checklist'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import {
  acceptHandover, addChecklistItem, createTemplate, deleteChecklistItem,
  disputeHandover, getHandover, listHandovers, listTemplates, resolveHandover,
  updateChecks, updateTemplate,
  COUNT_SOURCES, COUNT_SOURCE_LABELS,
  type Handover, type HandoverCheck, type ChecklistTemplate,
} from '@/api/handover'
import { getSites } from '@/api/sites'
import { GlassCard } from '@/components/common/GlassCard'
import { PageHeader } from '@/components/common/PageHeader'
import { FilterRail } from '@/components/common/FilterRail'
import { useAuthStore } from '@/store/auth'

const CAN_MANAGE = new Set([1, 2, 3, 8])

const STATUS_META: Record<string, { label: string; colour: string }> = {
  submitted: { label: 'Awaiting acceptance', colour: '#F5A524' },
  accepted: { label: 'Accepted', colour: '#00D97E' },
  disputed: { label: 'Disputed', colour: '#FF4560' },
  resolved: { label: 'Resolved', colour: '#6C63FF' },
}

const OPEN = new Set(['submitted', 'disputed'])

function fmt(ts: string | null) {
  if (!ts) return '—'
  return new Date(ts).toLocaleString(undefined, {
    day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit',
  })
}

function apiError(e: unknown, fallback: string) {
  const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
  return detail || fallback
}

// ── Card ─────────────────────────────────────────────────────────────────────

function HandoverCard({ row, onOpen }: { row: Handover; onOpen: () => void }) {
  const meta = STATUS_META[row.status] ?? STATUS_META.submitted
  const open = OPEN.has(row.status)
  return (
    <GlassCard
      onClick={onOpen}
      sx={{
        p: 1.25, cursor: 'pointer', transition: 'transform .15s',
        border: row.status === 'disputed' ? '1px solid rgba(255,69,96,0.45)' : undefined,
        opacity: open ? 1 : 0.75,
        '&:hover': { transform: 'translateY(-2px)' },
      }}
    >
      <Stack direction="row" alignItems="center" spacing={0.75} sx={{ mb: 0.5 }}>
        <Chip
          size="small" label={meta.label}
          sx={{
            height: 17, fontSize: '0.58rem', fontWeight: 700,
            bgcolor: `${meta.colour}22`, color: meta.colour,
          }}
        />
        <Box sx={{ flex: 1 }} />
        {row.keys_overdue > 0 && (
          <Tooltip title={`${row.keys_overdue} keys past their return time`}>
            <Chip size="small" label={`${row.keys_overdue} late`}
                  sx={{ height: 17, fontSize: '0.58rem',
                        bgcolor: 'rgba(255,69,96,0.18)', color: '#FF4560' }} />
          </Tooltip>
        )}
      </Stack>

      <Typography variant="subtitle2" noWrap sx={{ fontWeight: 800, fontSize: '0.85rem' }}>
        {row.site_name || 'No site'}
      </Typography>
      <Typography variant="caption" sx={{ display: 'block', color: 'text.secondary', fontSize: '0.63rem' }}>
        {fmt(row.created_at)}
        {row.outgoing_guard_name ? ` · from ${row.outgoing_guard_name}` : ''}
      </Typography>

      {row.status === 'disputed' && row.dispute_reason && (
        <Box sx={{
          mt: 0.9, px: 1, py: 0.6, borderRadius: '6px',
          background: 'rgba(255,69,96,0.10)', border: '1px solid rgba(255,69,96,0.3)',
        }}>
          <Typography variant="caption" sx={{ display: 'block', color: '#FF4560', fontSize: '0.63rem' }}>
            {row.dispute_reason}
          </Typography>
        </Box>
      )}

      <Stack direction="row" spacing={0.5} sx={{ mt: 1, flexWrap: 'wrap', gap: 0.5 }}>
        {[
          ['keys out', row.keys_outstanding],
          ['held', row.lost_found_held],
          ['kit out', row.equipment_out_count],
          ['defects', row.open_defects_count],
        ].filter(([, n]) => Number(n) > 0).map(([label, n]) => (
          <Chip key={String(label)} size="small" variant="outlined"
                label={`${n} ${label}`} sx={{ height: 17, fontSize: '0.58rem' }} />
        ))}
      </Stack>

      {row.status === 'accepted' && (
        <Typography variant="caption" sx={{ display: 'block', mt: 0.9, color: 'text.secondary', fontSize: '0.63rem' }}>
          Accepted {fmt(row.accepted_at)}
          {row.accepted_by_name ? ` by ${row.accepted_by_name}` : ''}
        </Typography>
      )}
      {row.status === 'resolved' && (
        <Typography variant="caption" sx={{ display: 'block', mt: 0.9, color: 'text.secondary', fontSize: '0.63rem' }}>
          Resolved {fmt(row.resolved_at)}
          {row.resolved_by_name ? ` by ${row.resolved_by_name}` : ''}
        </Typography>
      )}
    </GlassCard>
  )
}

// ── Detail ───────────────────────────────────────────────────────────────────

function CheckRow({ check, editable, onChange }: {
  check: HandoverCheck
  editable: boolean
  onChange: (patch: { checked?: boolean; counted_value?: number | null }) => void
}) {
  return (
    <Stack
      direction="row" alignItems="center" spacing={1}
      sx={{
        px: 1, py: 0.6, borderRadius: '6px',
        background: check.mismatch ? 'rgba(255,69,96,0.10)' : 'rgba(255,255,255,0.03)',
        border: `1px solid ${check.mismatch ? 'rgba(255,69,96,0.35)' : 'rgba(255,255,255,0.07)'}`,
      }}
    >
      <Checkbox
        size="small" checked={check.checked} disabled={!editable}
        onChange={(e) => onChange({ checked: e.target.checked })}
        sx={{ p: 0.25 }}
        slotProps={{ input: { 'aria-label': check.label } }}
      />
      <Box sx={{ flex: 1, minWidth: 0 }}>
        <Typography variant="body2" sx={{ fontSize: '0.8rem' }}>
          {check.label}
          {!check.is_required && (
            <Typography component="span" variant="caption"
                        sx={{ color: 'text.disabled', ml: 0.75, fontSize: '0.6rem' }}>
              optional
            </Typography>
          )}
        </Typography>
        {check.expected_value !== null && (
          <Typography variant="caption" sx={{ color: 'text.secondary', fontSize: '0.6rem' }}>
            register says {check.expected_value}
          </Typography>
        )}
      </Box>
      {check.requires_count && (
        <TextField
          size="small" type="number" disabled={!editable}
          value={check.counted_value ?? ''}
          onChange={(e) => onChange({
            counted_value: e.target.value === '' ? null : Number(e.target.value),
          })}
          sx={{ width: 86 }}
          slotProps={{ htmlInput: { min: 0, 'aria-label': `Count for ${check.label}` } }}
        />
      )}
      {check.mismatch && (
        <Tooltip title="This count does not match the register">
          <ReportProblemIcon sx={{ fontSize: 17, color: '#FF4560' }} />
        </Tooltip>
      )}
    </Stack>
  )
}

function HandoverDialog({ handoverId, canManage, onClose }: {
  handoverId: string; canManage: boolean; onClose: () => void
}) {
  const qc = useQueryClient()
  const [notes, setNotes] = useState('')
  const [disputeReason, setDisputeReason] = useState('')
  const [error, setError] = useState('')

  const { data, isLoading } = useQuery({
    queryKey: ['handover', handoverId],
    queryFn: () => getHandover(handoverId),
  })

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ['handover', handoverId] })
    qc.invalidateQueries({ queryKey: ['handovers'] })
  }

  const tick = useMutation({
    mutationFn: (patch: { id: string; checked: boolean; counted_value: number | null }) =>
      updateChecks(handoverId, [patch]),
    onSuccess: invalidate,
    onError: (e) => setError(apiError(e, 'Could not record that check')),
  })

  const accept = useMutation({
    mutationFn: () => acceptHandover(handoverId, notes.trim() || null),
    onSuccess: () => { invalidate(); onClose() },
    onError: (e) => setError(apiError(e, 'Could not accept this handover')),
  })

  const dispute = useMutation({
    mutationFn: () => disputeHandover(handoverId, disputeReason.trim()),
    onSuccess: () => { invalidate(); setDisputeReason('') },
    onError: (e) => setError(apiError(e, 'Could not record the dispute')),
  })

  const resolve = useMutation({
    mutationFn: () => resolveHandover(handoverId, notes.trim() || null),
    onSuccess: () => { invalidate(); onClose() },
    onError: (e) => setError(apiError(e, 'Could not resolve this handover')),
  })

  const editable = Boolean(data && OPEN.has(data.status))

  return (
    <Dialog open onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle sx={{ pb: 1 }}>
        <Typography variant="subtitle1" sx={{ fontWeight: 800 }}>
          {data?.site_name || 'Handover'}
        </Typography>
        <Typography variant="caption" color="text.secondary">
          {data ? `${fmt(data.created_at)} · from ${data.outgoing_guard_name || 'unknown'}` : ''}
        </Typography>
      </DialogTitle>
      <DialogContent dividers>
        {error && <Alert severity="error" sx={{ mb: 1.5 }}>{error}</Alert>}
        {isLoading || !data ? (
          <Stack spacing={1}>
            {[0, 1, 2, 3].map((i) => <Skeleton key={i} variant="rounded" height={40} />)}
          </Stack>
        ) : (
          <Stack spacing={1.5}>
            {data.status === 'disputed' && (
              <Alert severity="error">
                Disputed: {data.dispute_reason}
              </Alert>
            )}
            {data.mismatches > 0 && (
              <Alert severity="warning">
                {data.mismatches}{' '}
                {data.mismatches === 1 ? 'count does' : 'counts do'} not match the register.
              </Alert>
            )}

            <Stack direction="row" spacing={0.75} sx={{ flexWrap: 'wrap', gap: 0.5 }}>
              {[
                ['Keys out', data.keys_outstanding],
                ['Overdue keys', data.keys_overdue],
                ['Property held', data.lost_found_held],
                ['Kit signed out', data.equipment_out_count],
                ['Open defects', data.open_defects_count],
                ['Open incidents', data.open_incidents_count],
              ].map(([label, n]) => (
                <Chip key={String(label)} size="small" label={`${label}: ${n}`}
                      sx={{ height: 20, fontSize: '0.65rem' }} />
              ))}
            </Stack>

            {data.outgoing_notes && (
              <Box>
                <Typography variant="caption" sx={{ fontWeight: 800, letterSpacing: '0.04em' }}>
                  OUTGOING NOTES
                </Typography>
                <Typography variant="body2" sx={{ fontSize: '0.82rem' }}>
                  {data.outgoing_notes}
                </Typography>
              </Box>
            )}

            <Box>
              <Typography variant="caption" sx={{ fontWeight: 800, letterSpacing: '0.04em' }}>
                CHECKLIST
              </Typography>
              {data.checks.length === 0 ? (
                <Typography variant="body2" color="text.secondary" sx={{ fontSize: '0.8rem' }}>
                  No checklist is set up for this site. Add one on the Checklists tab.
                </Typography>
              ) : (
                <Stack spacing={0.5} sx={{ mt: 0.5 }}>
                  {data.checks.map((check) => (
                    <CheckRow
                      key={check.id} check={check} editable={editable}
                      onChange={(patch) => tick.mutate({
                        id: check.id,
                        checked: patch.checked ?? check.checked,
                        counted_value: patch.counted_value !== undefined
                          ? patch.counted_value
                          : check.counted_value,
                      })}
                    />
                  ))}
                </Stack>
              )}
            </Box>

            {editable && (
              <TextField
                size="small" multiline rows={2} label="Your notes" value={notes}
                onChange={(e) => setNotes(e.target.value)}
              />
            )}

            {editable && data.status === 'submitted' && (
              <Box>
                <Typography variant="caption" sx={{ fontWeight: 800, letterSpacing: '0.04em' }}>
                  NOT WHAT YOU WERE HANDED?
                </Typography>
                <Stack direction="row" spacing={1} sx={{ mt: 0.5 }}>
                  <TextField
                    size="small" sx={{ flex: 1 }} value={disputeReason}
                    onChange={(e) => setDisputeReason(e.target.value)}
                    placeholder="Eleven keys on the board, handover says twelve"
                  />
                  <Button
                    size="small" color="error" variant="outlined"
                    startIcon={<GavelIcon sx={{ fontSize: 15 }} />}
                    disabled={!disputeReason.trim() || dispute.isPending}
                    onClick={() => { setError(''); dispute.mutate() }}
                  >
                    Dispute
                  </Button>
                </Stack>
              </Box>
            )}

            {data.incoming_notes && !editable && (
              <Box>
                <Typography variant="caption" sx={{ fontWeight: 800, letterSpacing: '0.04em' }}>
                  INCOMING NOTES
                </Typography>
                <Typography variant="body2" sx={{ fontSize: '0.82rem' }}>
                  {data.incoming_notes}
                </Typography>
              </Box>
            )}
          </Stack>
        )}
      </DialogContent>
      <DialogActions>
        {data && data.status === 'disputed' && canManage && (
          <Button
            sx={{ mr: 'auto' }} variant="outlined" disabled={resolve.isPending}
            onClick={() => { setError(''); resolve.mutate() }}
          >
            Resolve dispute
          </Button>
        )}
        <Button onClick={onClose}>Close</Button>
        {data && editable && (
          <Tooltip title={data.unchecked_required > 0
            ? `${data.unchecked_required} required checks still outstanding`
            : ''}>
            <span>
              <Button
                variant="contained"
                startIcon={<CheckCircleOutlineIcon sx={{ fontSize: 17 }} />}
                disabled={accept.isPending || data.unchecked_required > 0}
                onClick={() => { setError(''); accept.mutate() }}
              >
                Accept
              </Button>
            </span>
          </Tooltip>
        )}
      </DialogActions>
    </Dialog>
  )
}

// ── Checklists ───────────────────────────────────────────────────────────────

function AddItemRow({ templateId }: { templateId: string }) {
  const qc = useQueryClient()
  const [label, setLabel] = useState('')
  const [source, setSource] = useState('')
  const [required, setRequired] = useState(true)
  const [error, setError] = useState('')

  const add = useMutation({
    mutationFn: () => addChecklistItem(templateId, {
      label: label.trim(),
      requires_count: Boolean(source),
      expected_source: source || null,
      is_required: required,
      sort_order: 0,
    }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['handover-templates'] })
      setLabel(''); setSource('')
    },
    onError: (e) => setError(apiError(e, 'Could not add that item')),
  })

  return (
    <Box>
      {error && <Alert severity="error" sx={{ mb: 1 }}>{error}</Alert>}
      <Stack direction="row" spacing={1} alignItems="center">
        <TextField
          size="small" sx={{ flex: 1 }} value={label} placeholder="Add a check…"
          onChange={(e) => setLabel(e.target.value)}
        />
        <TextField
          select size="small" sx={{ width: 200 }} value={source} label="Count against"
          onChange={(e) => setSource(e.target.value)}
        >
          <MenuItem value="">Just a tick</MenuItem>
          {COUNT_SOURCES.map((s) => (
            <MenuItem key={s} value={s}>{COUNT_SOURCE_LABELS[s]}</MenuItem>
          ))}
        </TextField>
        <Tooltip title={required ? 'Required before acceptance' : 'Optional'}>
          <Switch size="small" checked={required}
                  onChange={(e) => setRequired(e.target.checked)} />
        </Tooltip>
        <Button size="small" variant="outlined" startIcon={<AddIcon />}
                disabled={!label.trim() || add.isPending}
                onClick={() => { setError(''); add.mutate() }}>
          Add
        </Button>
      </Stack>
    </Box>
  )
}

function TemplateCard({ template, canManage }: {
  template: ChecklistTemplate; canManage: boolean
}) {
  const qc = useQueryClient()
  const remove = useMutation({
    mutationFn: (itemId: string) => deleteChecklistItem(itemId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['handover-templates'] }),
  })
  const retire = useMutation({
    mutationFn: () => updateTemplate(template.id, { is_active: !template.is_active }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['handover-templates'] }),
  })

  return (
    <GlassCard sx={{ p: 1.5, opacity: template.is_active ? 1 : 0.6 }}>
      <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 1 }}>
        <ChecklistIcon sx={{ fontSize: 18, color: 'text.secondary' }} />
        <Typography variant="subtitle2" sx={{ fontWeight: 800 }}>{template.name}</Typography>
        <Chip
          size="small" variant="outlined"
          label={template.site_name || 'Company default'}
          sx={{ height: 18, fontSize: '0.6rem' }}
        />
        <Box sx={{ flex: 1 }} />
        {canManage && (
          <Button size="small" color={template.is_active ? 'error' : 'primary'}
                  disabled={retire.isPending} onClick={() => retire.mutate()}>
            {template.is_active ? 'Retire' : 'Reactivate'}
          </Button>
        )}
      </Stack>

      <Stack spacing={0.5} sx={{ mb: canManage ? 1.25 : 0 }}>
        {template.items.length === 0 ? (
          <Typography variant="caption" color="text.secondary">
            No checks yet. A handover raised against this list would be unstructured.
          </Typography>
        ) : template.items.map((item) => (
          <Stack
            key={item.id} direction="row" alignItems="center" spacing={1}
            sx={{
              px: 1, py: 0.5, borderRadius: '6px',
              background: 'rgba(255,255,255,0.03)',
              border: '1px solid rgba(255,255,255,0.07)',
            }}
          >
            <Typography variant="body2" sx={{ flex: 1, fontSize: '0.8rem' }}>
              {item.label}
            </Typography>
            {item.expected_source && (
              <Chip size="small" variant="outlined"
                    label={COUNT_SOURCE_LABELS[item.expected_source] ?? item.expected_source}
                    sx={{ height: 17, fontSize: '0.58rem' }} />
            )}
            {!item.is_required && (
              <Chip size="small" label="optional" sx={{ height: 17, fontSize: '0.58rem' }} />
            )}
            {canManage && (
              <IconButton size="small" onClick={() => remove.mutate(item.id)}
                          aria-label={`Remove ${item.label}`}>
                <DeleteIcon sx={{ fontSize: 16 }} />
              </IconButton>
            )}
          </Stack>
        ))}
      </Stack>

      {canManage && template.is_active && <AddItemRow templateId={template.id} />}
    </GlassCard>
  )
}

function NewTemplateDialog({ sites, onClose }: {
  sites: { id: string; name: string }[]; onClose: () => void
}) {
  const qc = useQueryClient()
  const [name, setName] = useState('')
  const [siteId, setSiteId] = useState('')
  const [error, setError] = useState('')

  const create = useMutation({
    mutationFn: () => createTemplate({ name: name.trim(), site_id: siteId || null }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['handover-templates'] }); onClose() },
    onError: (e) => setError(apiError(e, 'Could not create that checklist')),
  })

  return (
    <Dialog open onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle>New checklist</DialogTitle>
      <DialogContent dividers>
        {error && <Alert severity="error" sx={{ mb: 1.5 }}>{error}</Alert>}
        <Stack spacing={1.5} sx={{ mt: 0.5 }}>
          <TextField size="small" label="Name" value={name} autoFocus
                     onChange={(e) => setName(e.target.value)} />
          <TextField
            select size="small" label="Applies to" value={siteId}
            onChange={(e) => setSiteId(e.target.value)}
            helperText="The company default is used by any site without its own"
          >
            <MenuItem value="">Company default</MenuItem>
            {sites.map((s) => <MenuItem key={s.id} value={s.id}>{s.name}</MenuItem>)}
          </TextField>
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" disabled={!name.trim() || create.isPending}
                onClick={() => { setError(''); create.mutate() }}>
          Create
        </Button>
      </DialogActions>
    </Dialog>
  )
}

// ── Page ─────────────────────────────────────────────────────────────────────

export function HandoversPage() {
  const roleId = useAuthStore((s) => s.user?.roleId ?? 0)
  const canManage = CAN_MANAGE.has(roleId)

  const [tab, setTab] = useState(0)
  const [siteFilter, setSiteFilter] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const [openOnly, setOpenOnly] = useState(true)
  const [opened, setOpened] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)

  const { data: sites = [] } = useQuery({ queryKey: ['sites'], queryFn: () => getSites() })

  const { data: handovers = [], isLoading } = useQuery({
    queryKey: ['handovers', siteFilter, statusFilter, openOnly],
    queryFn: () => listHandovers({
      site_id: siteFilter || undefined,
      status_filter: statusFilter || undefined,
      open_only: openOnly && !statusFilter,
    }),
    refetchInterval: 60_000,
  })

  const { data: templates = [], isLoading: templatesLoading } = useQuery({
    queryKey: ['handover-templates'],
    queryFn: () => listTemplates(true),
    enabled: tab === 1,
  })

  const siteOptions = useMemo(
    () => [{ value: '', label: 'All sites' },
           ...sites.map((s) => ({ value: s.id, label: s.name }))],
    [sites],
  )

  const disputed = handovers.filter((h) => h.status === 'disputed').length

  return (
    <Box sx={{ display: 'flex', gap: 1.5, alignItems: 'flex-start' }}>
      <Box sx={{ flex: 1, minWidth: 0 }}>
        <PageHeader
          pageKey="handovers"
          action={tab === 1 && canManage ? (
            <Button size="small" variant="contained" startIcon={<AddIcon />}
                    onClick={() => setCreating(true)}>
              New checklist
            </Button>
          ) : undefined}
        />

        {tab === 0 && disputed > 0 && (
          <Alert severity="error" sx={{ mb: 1.5 }}>
            {disputed} {disputed === 1 ? 'handover is' : 'handovers are'} disputed — an incoming
            guard did not agree with what they were handed.
          </Alert>
        )}

        <Tabs value={tab} onChange={(_, v) => setTab(v)} sx={{ minHeight: 34, mb: 1 }}>
          <Tab label="Handovers" sx={{ minHeight: 34, fontSize: '0.78rem' }} />
          <Tab label="Checklists" sx={{ minHeight: 34, fontSize: '0.78rem' }} />
        </Tabs>

        {tab === 0 ? (
          <>
            <Stack direction="row" spacing={1} sx={{ mb: 1.5 }} alignItems="center">
              <Button
                size="small" variant={openOnly ? 'contained' : 'outlined'}
                startIcon={<SwapHorizIcon sx={{ fontSize: 15 }} />}
                onClick={() => setOpenOnly((v) => !v)}
              >
                {openOnly ? 'Needs action' : 'Everything'}
              </Button>
            </Stack>

            {isLoading ? (
              <Box sx={{ display: 'grid', gap: 1.25, gridTemplateColumns: 'repeat(auto-fill, minmax(250px, 1fr))' }}>
                {[0, 1, 2, 3].map((i) => <Skeleton key={i} variant="rounded" height={140} />)}
              </Box>
            ) : handovers.length === 0 ? (
              <GlassCard sx={{ p: 4, textAlign: 'center' }}>
                <SwapHorizIcon sx={{ fontSize: 34, color: 'text.disabled', mb: 1 }} />
                <Typography color="text.secondary">
                  Nothing waiting. Handovers raised at the end of a shift land here for the
                  incoming guard to accept.
                </Typography>
              </GlassCard>
            ) : (
              <Box sx={{
                display: 'grid', gap: 1.25, alignItems: 'start',
                gridTemplateColumns: 'repeat(auto-fill, minmax(250px, 1fr))',
              }}>
                {handovers.map((row) => (
                  <HandoverCard key={row.id} row={row} onOpen={() => setOpened(row.id)} />
                ))}
              </Box>
            )}
          </>
        ) : templatesLoading ? (
          <Stack spacing={1.5}>
            {[0, 1].map((i) => <Skeleton key={i} variant="rounded" height={160} />)}
          </Stack>
        ) : templates.length === 0 ? (
          <GlassCard sx={{ p: 4, textAlign: 'center' }}>
            <ChecklistIcon sx={{ fontSize: 34, color: 'text.disabled', mb: 1 }} />
            <Typography color="text.secondary">
              No checklists yet. Handovers still work without one — they are just unstructured.
            </Typography>
          </GlassCard>
        ) : (
          <Stack spacing={1.5}>
            {templates.map((t) => (
              <TemplateCard key={t.id} template={t} canManage={canManage} />
            ))}
          </Stack>
        )}
      </Box>

      <FilterRail
        storageKey="handovers"
        groups={[
          {
            key: 'status', label: 'Status',
            options: [
              { value: '', label: 'Any status' },
              { value: 'submitted', label: 'Awaiting acceptance' },
              { value: 'disputed', label: 'Disputed' },
              { value: 'accepted', label: 'Accepted' },
              { value: 'resolved', label: 'Resolved' },
            ],
            value: statusFilter, onChange: setStatusFilter,
          },
          {
            key: 'site', label: 'Site', options: siteOptions,
            value: siteFilter, onChange: setSiteFilter,
          },
        ]}
      />

      {opened && (
        <HandoverDialog
          handoverId={opened} canManage={canManage} onClose={() => setOpened(null)}
        />
      )}
      {creating && <NewTemplateDialog sites={sites} onClose={() => setCreating(false)} />}
    </Box>
  )
}

export default HandoversPage
