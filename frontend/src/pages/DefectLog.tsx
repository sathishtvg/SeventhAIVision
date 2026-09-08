/**
 * Facility Defect Log — what your officers found, and what happened about it.
 *
 * The page is a chasing list, not an archive. Open items sort first and worst
 * first within that, and the two numbers a supervisor gets asked about — open
 * safety hazards, and anything still open after a fortnight — sit in the
 * header rather than needing to be counted off the list.
 *
 * Referring is a first-class action because that is where the work actually
 * goes: a guard does not fix a lift, they hand it to the building and then
 * chase it using the building's own reference number.
 */
import { useEffect, useMemo, useState } from 'react'
import {
  Alert, Box, Button, Chip, Dialog, DialogActions, DialogContent, DialogTitle,
  IconButton, MenuItem, Skeleton, TextField, Tooltip, Typography,
} from '@mui/material'
import Stack from '@/components/common/Stack'
import AddIcon from '@mui/icons-material/Add'
import BuildIcon from '@mui/icons-material/Build'
import ForwardToInboxIcon from '@mui/icons-material/ForwardToInbox'
import CheckCircleOutlineIcon from '@mui/icons-material/CheckCircleOutlineOutlined'
import PhotoCameraIcon from '@mui/icons-material/PhotoCamera'
import EditIcon from '@mui/icons-material/Edit'
import WarningAmberIcon from '@mui/icons-material/WarningAmber'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import {
  fetchDefectPhoto, getDefectSummary, listDefects, referDefect, reportDefect,
  resolveDefect, updateDefect, uploadDefectPhoto,
  DEFECT_CATEGORIES, DEFECT_SEVERITIES, type Defect,
} from '@/api/defects'
import { getSites } from '@/api/sites'
import { GlassCard } from '@/components/common/GlassCard'
import { PageHeader } from '@/components/common/PageHeader'
import { FilterRail } from '@/components/common/FilterRail'
import { useAuthStore } from '@/store/auth'

const CAN_MANAGE = new Set([1, 2, 3, 8])
const CLIENT_ROLE = 7

const SEVERITY_META: Record<string, { label: string; colour: string }> = {
  safety_hazard: { label: 'Safety hazard', colour: '#FF4560' },
  high: { label: 'High', colour: '#F5A524' },
  medium: { label: 'Medium', colour: '#6C63FF' },
  low: { label: 'Low', colour: '#8892A6' },
}

const STATUS_META: Record<string, { label: string; colour: string }> = {
  open: { label: 'Open', colour: '#FF4560' },
  reported: { label: 'Reported', colour: '#F5A524' },
  in_progress: { label: 'In progress', colour: '#6C63FF' },
  resolved: { label: 'Resolved', colour: '#00D97E' },
  closed: { label: 'Closed', colour: '#8892A6' },
}

const ACTIVE = new Set(['open', 'reported', 'in_progress'])

function fmtDate(ts: string | null) {
  if (!ts) return '—'
  return new Date(ts).toLocaleDateString(undefined, {
    day: '2-digit', month: 'short', year: 'numeric',
  })
}

function apiError(e: unknown, fallback: string) {
  const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
  return detail || fallback
}

function tidy(value: string) {
  return value.replace(/_/g, ' ')
}

// ── Photo ────────────────────────────────────────────────────────────────────

function DefectPhoto({ defectId }: { defectId: string }) {
  const [url, setUrl] = useState<string | null>(null)

  useEffect(() => {
    let created: string | null = null
    let cancelled = false
    fetchDefectPhoto(defectId)
      .then((objectUrl) => {
        if (cancelled) { URL.revokeObjectURL(objectUrl); return }
        created = objectUrl
        setUrl(objectUrl)
      })
      .catch(() => undefined)
    return () => {
      cancelled = true
      if (created) URL.revokeObjectURL(created)
    }
  }, [defectId])

  if (!url) return <Skeleton variant="rounded" height={130} />
  return (
    <Box
      component="img" src={url} alt="The defect as reported"
      sx={{ width: '100%', height: 130, objectFit: 'cover', borderRadius: '8px' }}
    />
  )
}

// ── Card ─────────────────────────────────────────────────────────────────────

function DefectCard({ row, canManage, readOnly, onRefer, onResolve, onEdit, onPhoto }: {
  row: Defect
  canManage: boolean
  readOnly: boolean
  onRefer: () => void
  onResolve: () => void
  onEdit: () => void
  onPhoto: () => void
}) {
  const severity = SEVERITY_META[row.severity] ?? SEVERITY_META.medium
  const statusMeta = STATUS_META[row.status] ?? STATUS_META.open
  const active = ACTIVE.has(row.status)
  const days = Math.floor(Number(row.days_open) || 0)
  const ageing = active && days >= 14

  return (
    <GlassCard sx={{
      p: 1.25,
      border: row.severity === 'safety_hazard' && active
        ? '1px solid rgba(255,69,96,0.42)'
        : ageing ? '1px solid rgba(245,165,36,0.38)' : undefined,
      opacity: active ? 1 : 0.72,
    }}>
      {row.has_photo && <Box sx={{ mb: 1 }}><DefectPhoto defectId={row.id} /></Box>}

      <Stack direction="row" alignItems="center" spacing={0.75} sx={{ mb: 0.5 }}>
        <Chip
          size="small" label={statusMeta.label}
          sx={{
            height: 17, fontSize: '0.58rem', fontWeight: 700,
            bgcolor: `${statusMeta.colour}22`, color: statusMeta.colour,
          }}
        />
        <Chip
          size="small" variant="outlined" label={severity.label}
          sx={{ height: 17, fontSize: '0.58rem', color: severity.colour,
                borderColor: `${severity.colour}66` }}
        />
        <Box sx={{ flex: 1 }} />
        {ageing && (
          <Tooltip title="Still open after two weeks">
            <Chip size="small" label={`${days}d`}
                  sx={{ height: 17, fontSize: '0.58rem',
                        bgcolor: 'rgba(245,165,36,0.18)', color: '#F5A524' }} />
          </Tooltip>
        )}
        {row.severity === 'safety_hazard' && active && (
          <WarningAmberIcon sx={{ fontSize: 16, color: '#FF4560' }} />
        )}
      </Stack>

      <Typography variant="body2" sx={{ fontWeight: 600, fontSize: '0.82rem', mb: 0.4 }}>
        {row.description}
      </Typography>
      <Typography variant="caption" sx={{ display: 'block', color: 'text.secondary', fontSize: '0.63rem' }}>
        {tidy(row.category)} · {row.site_name}
        {row.location ? ` · ${row.location}` : ''}
      </Typography>
      <Typography variant="caption" sx={{ display: 'block', color: 'text.secondary', fontSize: '0.63rem' }}>
        Reported {fmtDate(row.reported_at)}
        {row.reported_by_name ? ` by ${row.reported_by_name}` : ''}
      </Typography>

      {row.referred_to && (
        <Box sx={{
          mt: 0.9, px: 1, py: 0.6, borderRadius: '6px',
          background: 'rgba(108,99,255,0.09)', border: '1px solid rgba(108,99,255,0.26)',
        }}>
          <Typography variant="caption" sx={{ display: 'block', fontWeight: 700, fontSize: '0.63rem' }}>
            With {row.referred_to}
          </Typography>
          <Typography variant="caption" sx={{ display: 'block', color: 'text.secondary', fontSize: '0.6rem' }}>
            {[row.reference_no && `ref ${row.reference_no}`,
              row.referred_at && `since ${fmtDate(row.referred_at)}`,
            ].filter(Boolean).join(' · ')}
          </Typography>
        </Box>
      )}

      {!active && (
        <Typography variant="caption" sx={{ display: 'block', mt: 0.9, color: 'text.secondary', fontSize: '0.63rem' }}>
          {row.status === 'closed' ? 'Closed' : 'Fixed'} {fmtDate(row.resolved_at)}
          {row.resolved_by_name ? ` by ${row.resolved_by_name}` : ''}
          {row.resolution_notes ? ` — ${row.resolution_notes}` : ''}
        </Typography>
      )}

      {active && !readOnly && (
        <Stack direction="row" spacing={0.5} sx={{ mt: 1 }}>
          {canManage && (
            <>
              <Button size="small" variant="outlined" fullWidth
                      startIcon={<ForwardToInboxIcon sx={{ fontSize: 15 }} />} onClick={onRefer}>
                {row.referred_to ? 'Re-refer' : 'Refer'}
              </Button>
              <Tooltip title="Resolve or close">
                <IconButton size="small" onClick={onResolve} aria-label="Resolve this defect">
                  <CheckCircleOutlineIcon sx={{ fontSize: 17 }} />
                </IconButton>
              </Tooltip>
            </>
          )}
          <Tooltip title={row.has_photo ? 'Replace photo' : 'Add photo'}>
            <IconButton size="small" onClick={onPhoto} aria-label="Photograph this defect">
              <PhotoCameraIcon sx={{ fontSize: 17 }} />
            </IconButton>
          </Tooltip>
          <Tooltip title="Edit">
            <IconButton size="small" onClick={onEdit} aria-label="Edit this defect">
              <EditIcon sx={{ fontSize: 17 }} />
            </IconButton>
          </Tooltip>
        </Stack>
      )}
    </GlassCard>
  )
}

// ── Dialogs ──────────────────────────────────────────────────────────────────

function DefectEditorDialog({ row, sites, onClose }: {
  row: Defect | null
  sites: { id: string; name: string }[]
  onClose: () => void
}) {
  const qc = useQueryClient()
  const [siteId, setSiteId] = useState(row?.site_id || '')
  const [description, setDescription] = useState(row?.description || '')
  const [category, setCategory] = useState(row?.category || 'other')
  const [location, setLocation] = useState(row?.location || '')
  const [severity, setSeverity] = useState(row?.severity || 'medium')
  const [error, setError] = useState('')

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ['defects'] })
    qc.invalidateQueries({ queryKey: ['defect-summary'] })
  }

  const save = useMutation({
    mutationFn: () => row
      ? updateDefect(row.id, {
          description: description.trim(), category,
          location: location.trim() || null, severity,
        })
      : reportDefect({
          site_id: siteId, description: description.trim(), category,
          location: location.trim() || null, severity,
        }),
    onSuccess: () => { invalidate(); onClose() },
    onError: (e) => setError(apiError(e, 'Could not save this defect')),
  })

  return (
    <Dialog open onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle>{row ? 'Edit defect' : 'Report a defect'}</DialogTitle>
      <DialogContent dividers>
        {error && <Alert severity="error" sx={{ mb: 1.5 }}>{error}</Alert>}
        <Stack spacing={1.5} sx={{ mt: 0.5 }}>
          {!row && (
            <TextField select size="small" label="Site" value={siteId}
                       onChange={(e) => setSiteId(e.target.value)}>
              {sites.map((s) => <MenuItem key={s.id} value={s.id}>{s.name}</MenuItem>)}
            </TextField>
          )}
          <TextField
            size="small" label="What is wrong" value={description} autoFocus multiline rows={2}
            onChange={(e) => setDescription(e.target.value)}
            helperText="Specific enough that somebody else can find it"
          />
          <TextField select size="small" label="Category" value={category}
                     onChange={(e) => setCategory(e.target.value)}>
            {DEFECT_CATEGORIES.map((c) => (
              <MenuItem key={c} value={c}>{tidy(c)}</MenuItem>
            ))}
          </TextField>
          <TextField
            size="small" label="Where" value={location}
            onChange={(e) => setLocation(e.target.value)}
            helperText="Stairwell B, level 4 landing"
          />
          <TextField select size="small" label="Severity" value={severity}
                     onChange={(e) => setSeverity(e.target.value as Defect['severity'])}>
            {DEFECT_SEVERITIES.map((s) => (
              <MenuItem key={s} value={s}>{SEVERITY_META[s].label}</MenuItem>
            ))}
          </TextField>
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button
          variant="contained"
          disabled={!description.trim() || (!row && !siteId) || save.isPending}
          onClick={() => { setError(''); save.mutate() }}
        >
          Save
        </Button>
      </DialogActions>
    </Dialog>
  )
}

function ReferDialog({ row, onClose }: { row: Defect; onClose: () => void }) {
  const qc = useQueryClient()
  const [referredTo, setReferredTo] = useState(row.referred_to || '')
  const [reference, setReference] = useState(row.reference_no || '')
  const [inProgress, setInProgress] = useState(row.status === 'in_progress')
  const [error, setError] = useState('')

  const refer = useMutation({
    mutationFn: () => referDefect(row.id, {
      referred_to: referredTo.trim(),
      reference_no: reference.trim() || null,
      in_progress: inProgress,
    }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['defects'] })
      qc.invalidateQueries({ queryKey: ['defect-summary'] })
      onClose()
    },
    onError: (e) => setError(apiError(e, 'Could not record the referral')),
  })

  return (
    <Dialog open onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle sx={{ pb: 1 }}>
        <Typography variant="subtitle1" sx={{ fontWeight: 800 }}>Refer to the building</Typography>
        <Typography variant="caption" color="text.secondary">{row.description}</Typography>
      </DialogTitle>
      <DialogContent dividers>
        {error && <Alert severity="error" sx={{ mb: 1.5 }}>{error}</Alert>}
        <Stack spacing={1.5} sx={{ mt: 0.5 }}>
          <TextField
            size="small" label="Referred to" value={referredTo} autoFocus
            onChange={(e) => setReferredTo(e.target.value)}
            helperText="The FM desk, the managing agent, whoever owns the fix"
          />
          <TextField
            size="small" label="Their reference number" value={reference}
            onChange={(e) => setReference(e.target.value)}
            helperText="What you quote when you chase it"
          />
          <TextField
            select size="small" label="Where it stands"
            value={inProgress ? 'in_progress' : 'reported'}
            onChange={(e) => setInProgress(e.target.value === 'in_progress')}
          >
            <MenuItem value="reported">Told them</MenuItem>
            <MenuItem value="in_progress">They have accepted it</MenuItem>
          </TextField>
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" disabled={!referredTo.trim() || refer.isPending}
                onClick={() => { setError(''); refer.mutate() }}>
          Record referral
        </Button>
      </DialogActions>
    </Dialog>
  )
}

function ResolveDialog({ row, onClose }: { row: Defect; onClose: () => void }) {
  const qc = useQueryClient()
  const [notes, setNotes] = useState('')
  const [closeWithoutFix, setCloseWithoutFix] = useState(false)
  const [error, setError] = useState('')

  const resolve = useMutation({
    mutationFn: () => resolveDefect(row.id, {
      resolution_notes: notes.trim() || null,
      close_without_fix: closeWithoutFix,
    }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['defects'] })
      qc.invalidateQueries({ queryKey: ['defect-summary'] })
      onClose()
    },
    onError: (e) => setError(apiError(e, 'Could not close this defect')),
  })

  return (
    <Dialog open onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle sx={{ pb: 1 }}>
        <Typography variant="subtitle1" sx={{ fontWeight: 800 }}>Close this defect</Typography>
        <Typography variant="caption" color="text.secondary">{row.description}</Typography>
      </DialogTitle>
      <DialogContent dividers>
        {error && <Alert severity="error" sx={{ mb: 1.5 }}>{error}</Alert>}
        <Stack spacing={1.5} sx={{ mt: 0.5 }}>
          <TextField
            select size="small" label="Outcome"
            value={closeWithoutFix ? 'closed' : 'resolved'}
            onChange={(e) => setCloseWithoutFix(e.target.value === 'closed')}
          >
            <MenuItem value="resolved">Fixed</MenuItem>
            <MenuItem value="closed">Closing without a fix</MenuItem>
          </TextField>
          <TextField
            size="small" multiline rows={2} label="Notes" value={notes} autoFocus
            onChange={(e) => setNotes(e.target.value)}
            helperText={closeWithoutFix
              ? 'Required — the client will ask why this was closed'
              : 'What was done, and when it was checked'}
          />
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button
          variant="contained"
          disabled={resolve.isPending || (closeWithoutFix && !notes.trim())}
          onClick={() => { setError(''); resolve.mutate() }}
        >
          {closeWithoutFix ? 'Close' : 'Mark fixed'}
        </Button>
      </DialogActions>
    </Dialog>
  )
}

function PhotoDialog({ row, onClose }: { row: Defect; onClose: () => void }) {
  const qc = useQueryClient()
  const [file, setFile] = useState<File | null>(null)
  const [error, setError] = useState('')

  const preview = useMemo(() => (file ? URL.createObjectURL(file) : null), [file])
  useEffect(() => () => { if (preview) URL.revokeObjectURL(preview) }, [preview])

  const upload = useMutation({
    mutationFn: () => uploadDefectPhoto(row.id, file!),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['defects'] }); onClose() },
    onError: (e) => setError(apiError(e, 'Could not upload the photo')),
  })

  return (
    <Dialog open onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle>Photograph the defect</DialogTitle>
      <DialogContent dividers>
        {error && <Alert severity="error" sx={{ mb: 1.5 }}>{error}</Alert>}
        <Button component="label" variant="outlined" fullWidth
                startIcon={<PhotoCameraIcon />} sx={{ mb: 1.5 }}>
          {file ? file.name : 'Choose a photo'}
          <input hidden type="file" accept="image/jpeg,image/png"
                 onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
        </Button>
        {preview && (
          <Box component="img" src={preview} alt="Preview"
               sx={{ width: '100%', borderRadius: '8px', maxHeight: 260, objectFit: 'contain' }} />
        )}
        <Typography variant="caption" sx={{ display: 'block', mt: 1, color: 'text.secondary' }}>
          The difference between &ldquo;the light is out&rdquo; and a building manager
          knowing which light. JPEG or PNG.
        </Typography>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" disabled={!file || upload.isPending}
                onClick={() => { setError(''); upload.mutate() }}>
          Upload
        </Button>
      </DialogActions>
    </Dialog>
  )
}

// ── Page ─────────────────────────────────────────────────────────────────────

export function DefectLogPage() {
  const roleId = useAuthStore((s) => s.user?.roleId ?? 0)
  const canManage = CAN_MANAGE.has(roleId)
  // A client reads the log at their own building and writes nothing.
  const readOnly = roleId === CLIENT_ROLE

  const [siteFilter, setSiteFilter] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const [categoryFilter, setCategoryFilter] = useState('')
  const [severityFilter, setSeverityFilter] = useState('')
  const [activeOnly, setActiveOnly] = useState(true)
  const [adding, setAdding] = useState(false)
  const [editing, setEditing] = useState<Defect | null>(null)
  const [referring, setReferring] = useState<Defect | null>(null)
  const [resolving, setResolving] = useState<Defect | null>(null)
  const [photographing, setPhotographing] = useState<Defect | null>(null)

  const { data: sites = [] } = useQuery({ queryKey: ['sites'], queryFn: () => getSites() })

  const { data: defects = [], isLoading } = useQuery({
    queryKey: ['defects', siteFilter, statusFilter, categoryFilter, severityFilter, activeOnly],
    queryFn: () => listDefects({
      site_id: siteFilter || undefined,
      status_filter: statusFilter || undefined,
      category: categoryFilter || undefined,
      severity: severityFilter || undefined,
      // A named status filter is more specific than "anything still live".
      active_only: activeOnly && !statusFilter,
    }),
    refetchInterval: 120_000,
  })

  const { data: summary } = useQuery({
    queryKey: ['defect-summary', siteFilter],
    queryFn: () => getDefectSummary(siteFilter || undefined),
    refetchInterval: 120_000,
  })

  const siteOptions = useMemo(
    () => [{ value: '', label: 'All sites' },
           ...sites.map((s) => ({ value: s.id, label: s.name }))],
    [sites],
  )

  return (
    <Box sx={{ display: 'flex', gap: 1.5, alignItems: 'flex-start' }}>
      <Box sx={{ flex: 1, minWidth: 0 }}>
        <PageHeader
          pageKey="defects"
          action={readOnly ? undefined : (
            <Button size="small" variant="contained" startIcon={<AddIcon />}
                    onClick={() => setAdding(true)}>
              Report defect
            </Button>
          )}
        />

        {summary && summary.open_safety_hazards > 0 && (
          <Alert severity="error" sx={{ mb: 1.5 }}>
            {summary.open_safety_hazards} safety{' '}
            {summary.open_safety_hazards === 1 ? 'hazard is' : 'hazards are'} still open.
          </Alert>
        )}
        {summary && summary.ageing > 0 && (
          <Alert severity="warning" sx={{ mb: 1.5 }}>
            {summary.ageing} {summary.ageing === 1 ? 'defect has' : 'defects have'} been open
            longer than two weeks. Chase the building using their reference number.
          </Alert>
        )}

        <Stack direction="row" spacing={1} sx={{ mb: 1.5 }} alignItems="center">
          <Button
            size="small" variant={activeOnly ? 'contained' : 'outlined'}
            onClick={() => setActiveOnly((v) => !v)}
            startIcon={<BuildIcon sx={{ fontSize: 15 }} />}
          >
            {activeOnly ? 'Still open' : 'Everything'}
          </Button>
          {summary && (
            <Stack direction="row" spacing={0.75}>
              <Chip size="small" label={`${summary.open} open`} sx={{ height: 20, fontSize: '0.65rem' }} />
              <Chip size="small" label={`${summary.reported + summary.in_progress} with the building`}
                    sx={{ height: 20, fontSize: '0.65rem' }} />
              <Chip size="small" label={`${summary.resolved} fixed`} sx={{ height: 20, fontSize: '0.65rem' }} />
            </Stack>
          )}
        </Stack>

        {isLoading ? (
          <Box sx={{ display: 'grid', gap: 1.25, gridTemplateColumns: 'repeat(auto-fill, minmax(250px, 1fr))' }}>
            {[0, 1, 2, 3, 4, 5].map((i) => <Skeleton key={i} variant="rounded" height={150} />)}
          </Box>
        ) : defects.length === 0 ? (
          <GlassCard sx={{ p: 4, textAlign: 'center' }}>
            <BuildIcon sx={{ fontSize: 34, color: 'text.disabled', mb: 1 }} />
            <Typography color="text.secondary">
              Nothing outstanding. Defects your officers find on patrol land here.
            </Typography>
          </GlassCard>
        ) : (
          <Box sx={{
            display: 'grid', gap: 1.25, alignItems: 'start',
            gridTemplateColumns: 'repeat(auto-fill, minmax(250px, 1fr))',
          }}>
            {defects.map((row) => (
              <DefectCard
                key={row.id} row={row} canManage={canManage} readOnly={readOnly}
                onRefer={() => setReferring(row)}
                onResolve={() => setResolving(row)}
                onEdit={() => setEditing(row)}
                onPhoto={() => setPhotographing(row)}
              />
            ))}
          </Box>
        )}
      </Box>

      <FilterRail
        storageKey="defects"
        groups={[
          {
            key: 'status', label: 'Status',
            options: [
              { value: '', label: 'Any status' },
              { value: 'open', label: 'Open' },
              { value: 'reported', label: 'Reported' },
              { value: 'in_progress', label: 'In progress' },
              { value: 'resolved', label: 'Resolved' },
              { value: 'closed', label: 'Closed' },
            ],
            value: statusFilter, onChange: setStatusFilter,
          },
          {
            key: 'severity', label: 'Severity',
            options: [{ value: '', label: 'Any severity' },
                      ...DEFECT_SEVERITIES.map((s) => ({ value: s, label: SEVERITY_META[s].label }))],
            value: severityFilter, onChange: setSeverityFilter,
          },
          {
            key: 'site', label: 'Site', options: siteOptions,
            value: siteFilter, onChange: setSiteFilter,
          },
          {
            key: 'category', label: 'Category',
            options: [{ value: '', label: 'All categories' },
                      ...DEFECT_CATEGORIES.map((c) => ({ value: c, label: tidy(c) }))],
            value: categoryFilter, onChange: setCategoryFilter,
          },
        ]}
      />

      {(adding || editing) && (
        <DefectEditorDialog
          row={editing} sites={sites}
          onClose={() => { setAdding(false); setEditing(null) }}
        />
      )}
      {referring && <ReferDialog row={referring} onClose={() => setReferring(null)} />}
      {resolving && <ResolveDialog row={resolving} onClose={() => setResolving(null)} />}
      {photographing && <PhotoDialog row={photographing} onClose={() => setPhotographing(null)} />}
    </Box>
  )
}

export default DefectLogPage
