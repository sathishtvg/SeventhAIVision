import { useState } from 'react'
import {
  Box,
  Typography,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Chip,
  IconButton,
  Button,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  TextField,
  MenuItem,
  Select,
  FormControl,
  InputLabel,
  Switch,
  FormControlLabel,
  Skeleton,
  Tooltip,
  Alert,
  Paper,
} from '@mui/material'
import Stack from '@/components/common/Stack'
import AddIcon from '@mui/icons-material/Add'
import EditIcon from '@mui/icons-material/Edit'
import DeleteIcon from '@mui/icons-material/Delete'
import ToggleOnIcon from '@mui/icons-material/ToggleOn'
import ToggleOffIcon from '@mui/icons-material/ToggleOff'
import FilterAltIcon from '@mui/icons-material/FilterAlt'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { GlassCard } from '@/components/common/GlassCard'
import { PermissionGuard } from '@/components/common/PermissionGuard'
import {
  listDedupRules, createDedupRule, updateDedupRule, deleteDedupRule,
  type DedupRule, type DedupRuleBody,
} from '@/api/alertDedup'
import { getCameras } from '@/api/cameras'
import type { Camera } from '@/types/api'
import { PageHeader } from '@/components/common/PageHeader'

const MODULE_TYPES = [
  'lpr', 'face', 'intrusion', 'ppe', 'crowd', 'fire_smoke',
  'weapon', 'behavior', 'tampering', 'abandoned', 'fall',
]

const MODULE_LABELS: Record<string, string> = {
  lpr: 'LPR', face: 'Face', intrusion: 'Intrusion', ppe: 'PPE',
  crowd: 'Crowd', fire_smoke: 'Fire/Smoke', weapon: 'Weapon',
  behavior: 'Behavior', tampering: 'Tampering', abandoned: 'Abandoned', fall: 'Fall',
}

function fmtWindow(sec: number): string {
  if (sec < 60) return `${sec}s`
  if (sec < 3600) return `${Math.round(sec / 60)}m`
  const h = Math.floor(sec / 3600)
  const m = Math.round((sec % 3600) / 60)
  return m ? `${h}h ${m}m` : `${h}h`
}

function fmtDate(d: string | null) {
  if (!d) return '—'
  return new Date(d).toLocaleString()
}

function scopeLabel(rule: DedupRule): React.ReactNode {
  const hasCamera = rule.camera_id != null
  const hasModule = rule.module_type != null

  if (!hasCamera && !hasModule) {
    return <Chip label="Global wildcard" size="small" sx={{ bgcolor: 'rgba(108,99,255,0.18)', color: '#a99eff' }} />
  }
  if (hasCamera && !hasModule) {
    return (
      <Stack direction="row" spacing={0.5} alignItems="center">
        <Chip label={rule.camera_name ?? rule.camera_id?.slice(0, 8)} size="small" color="primary" variant="outlined" />
        <Typography variant="caption" color="text.secondary">· all modules</Typography>
      </Stack>
    )
  }
  if (!hasCamera && hasModule) {
    return (
      <Stack direction="row" spacing={0.5} alignItems="center">
        <Chip label={MODULE_LABELS[rule.module_type!] ?? rule.module_type} size="small" color="secondary" variant="outlined" />
        <Typography variant="caption" color="text.secondary">· all cameras</Typography>
      </Stack>
    )
  }
  return (
    <Stack direction="row" spacing={0.5} alignItems="center">
      <Chip label={rule.camera_name ?? rule.camera_id?.slice(0, 8)} size="small" color="primary" variant="outlined" />
      <Typography variant="caption" color="text.disabled">+</Typography>
      <Chip label={MODULE_LABELS[rule.module_type!] ?? rule.module_type} size="small" color="secondary" variant="outlined" />
    </Stack>
  )
}

// ── Rule dialog (shared for add & edit) ──────────────────────────────────────

interface RuleDialogProps {
  open: boolean
  onClose: () => void
  initial?: DedupRule | null
  cameras: Camera[]
}

const BLANK: DedupRuleBody = { module_type: null, camera_id: null, window_seconds: 300, is_active: true }

function RuleDialog({ open, onClose, initial, cameras }: RuleDialogProps) {
  const qc = useQueryClient()
  const isEdit = !!initial

  const [form, setForm] = useState<DedupRuleBody>(
    initial
      ? {
          module_type: initial.module_type,
          camera_id: initial.camera_id,
          window_seconds: initial.window_seconds,
          is_active: initial.is_active,
        }
      : BLANK
  )
  const [error, setError] = useState<string | null>(null)

  function reset() {
    setForm(BLANK)
    setError(null)
  }

  const { mutate: save, isPending } = useMutation({
    mutationFn: isEdit
      ? (body: DedupRuleBody) => updateDedupRule({ id: initial!.id, ...body })
      : createDedupRule,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['dedup-rules'] })
      reset()
      onClose()
    },
    onError: (err: unknown) => {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      setError(detail ?? 'Failed to save rule')
    },
  })

  function handleClose() {
    reset()
    onClose()
  }

  const winPreview = fmtWindow(form.window_seconds || 0)

  return (
    <Dialog open={open} onClose={handleClose} maxWidth="xs" fullWidth>
      <DialogTitle>{isEdit ? 'Edit Dedup Rule' : 'Add Dedup Rule'}</DialogTitle>
      <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: '16px !important' }}>
        {error && <Alert severity="error" onClose={() => setError(null)}>{error}</Alert>}

        <FormControl size="small" fullWidth>
          <InputLabel>Module type</InputLabel>
          <Select
            label="Module type"
            value={form.module_type ?? ''}
            onChange={(e) => setForm((f) => ({ ...f, module_type: e.target.value || null }))}
          >
            <MenuItem value=""><em>Any module (wildcard)</em></MenuItem>
            {MODULE_TYPES.map((m) => (
              <MenuItem key={m} value={m}>{MODULE_LABELS[m] ?? m}</MenuItem>
            ))}
          </Select>
        </FormControl>

        <FormControl size="small" fullWidth>
          <InputLabel>Camera</InputLabel>
          <Select
            label="Camera"
            value={form.camera_id ?? ''}
            onChange={(e) => setForm((f) => ({ ...f, camera_id: e.target.value || null }))}
          >
            <MenuItem value=""><em>Any camera (wildcard)</em></MenuItem>
            {cameras.map((c) => (
              <MenuItem key={c.id} value={c.id}>{c.name}</MenuItem>
            ))}
          </Select>
        </FormControl>

        <TextField
          label="Suppression window (seconds)"
          type="number"
          size="small"
          fullWidth
          value={form.window_seconds}
          onChange={(e) =>
            setForm((f) => ({ ...f, window_seconds: Math.max(1, Math.min(86400, Number(e.target.value))) }))
          }
          inputProps={{ min: 1, max: 86400 }}
          helperText={`≡ ${winPreview}  •  max 86400 (24 h)`}
        />

        <FormControlLabel
          control={
            <Switch
              checked={form.is_active}
              onChange={(e) => setForm((f) => ({ ...f, is_active: e.target.checked }))}
              color="success"
            />
          }
          label="Active"
        />
      </DialogContent>
      <DialogActions>
        <Button onClick={handleClose} disabled={isPending}>Cancel</Button>
        <Button
          variant="contained"
          onClick={() => save(form)}
          disabled={isPending}
        >
          {isEdit ? 'Save' : 'Add'}
        </Button>
      </DialogActions>
    </Dialog>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function AlertDedup() {
  const qc = useQueryClient()
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editing, setEditing] = useState<DedupRule | null>(null)

  const { data: rules = [], isLoading } = useQuery({
    queryKey: ['dedup-rules'],
    queryFn: listDedupRules,
  })

  const { data: cameras = [] } = useQuery({
    queryKey: ['cameras'],
    queryFn: getCameras,
  })

  const { mutate: remove } = useMutation({
    mutationFn: deleteDedupRule,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['dedup-rules'] }),
  })

  const { mutate: toggle } = useMutation({
    mutationFn: ({ rule, active }: { rule: DedupRule; active: boolean }) =>
      updateDedupRule({ id: rule.id, module_type: rule.module_type, camera_id: rule.camera_id, window_seconds: rule.window_seconds, is_active: active }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['dedup-rules'] }),
  })

  function openEdit(rule: DedupRule) {
    setEditing(rule)
    setDialogOpen(true)
  }

  function closeDialog() {
    setEditing(null)
    setDialogOpen(false)
  }

  const activeCount = rules.filter((r) => r.is_active).length

  return (
    <PermissionGuard permission="alert:dedup:manage">
      <PageHeader pageKey="alert-dedup" />
      <Box sx={{ p: 3 }}>
        {/* Header */}
        <Stack direction="row" alignItems="flex-start" justifyContent="space-between" mb={3}>
          <Stack direction="row" alignItems="center" gap={1.5}>
            <FilterAltIcon sx={{ color: 'primary.main', mt: 0.25 }} />
            <Box>
              <Typography variant="h6" fontWeight={700}>Alert Deduplication Rules</Typography>
              <Typography variant="caption" color="text.secondary">
                Suppress repeated alerts for the same camera+module within a time window
              </Typography>
            </Box>
          </Stack>
          <Button
            variant="contained"
            startIcon={<AddIcon />}
            size="small"
            onClick={() => { setEditing(null); setDialogOpen(true) }}
          >
            Add Rule
          </Button>
        </Stack>

        {/* Info banner */}
        <Paper
          variant="outlined"
          sx={{ p: 2, mb: 3, borderRadius: 2, borderColor: 'rgba(108,99,255,0.25)', bgcolor: 'rgba(108,99,255,0.05)' }}
        >
          <Typography variant="body2" color="text.secondary" lineHeight={1.7}>
            When a new alert is raised, the most specific matching rule wins:&nbsp;
            <strong>exact camera+module</strong> &gt; <strong>camera only</strong> &gt; <strong>module only</strong> &gt; <strong>global</strong>.
            If an open alert for the same (camera, module) already exists within the window, the new alert is suppressed.
            {activeCount > 0 && (
              <>&nbsp;<Chip label={`${activeCount} active`} size="small" color="success" sx={{ ml: 0.5, verticalAlign: 'middle' }} /></>
            )}
          </Typography>
        </Paper>

        {/* Table */}
        <GlassCard>
          <TableContainer>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>Scope</TableCell>
                  <TableCell>Window</TableCell>
                  <TableCell>Status</TableCell>
                  <TableCell>Created</TableCell>
                  <TableCell align="right">Actions</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {isLoading
                  ? Array.from({ length: 3 }).map((_, i) => (
                      <TableRow key={i}>
                        {Array.from({ length: 5 }).map((_, j) => (
                          <TableCell key={j}><Skeleton /></TableCell>
                        ))}
                      </TableRow>
                    ))
                  : rules.length === 0
                    ? (
                      <TableRow>
                        <TableCell colSpan={5} align="center" sx={{ py: 5, color: 'text.secondary' }}>
                          No deduplication rules defined. All duplicate alerts will be created.
                        </TableCell>
                      </TableRow>
                    )
                    : rules.map((rule) => (
                      <TableRow key={rule.id} hover sx={{ opacity: rule.is_active ? 1 : 0.5 }}>
                        <TableCell>{scopeLabel(rule)}</TableCell>
                        <TableCell>
                          <Typography variant="body2" fontFamily="monospace" fontWeight={600}>
                            {fmtWindow(rule.window_seconds)}
                          </Typography>
                          <Typography variant="caption" color="text.disabled">
                            ({rule.window_seconds}s)
                          </Typography>
                        </TableCell>
                        <TableCell>
                          <Chip
                            label={rule.is_active ? 'Active' : 'Disabled'}
                            size="small"
                            color={rule.is_active ? 'success' : 'default'}
                          />
                        </TableCell>
                        <TableCell sx={{ fontSize: '0.78rem', color: 'text.secondary' }}>
                          {fmtDate(rule.created_at)}
                        </TableCell>
                        <TableCell align="right">
                          <Stack direction="row" justifyContent="flex-end" gap={0.5}>
                            <Tooltip title="Edit rule">
                              <IconButton size="small" onClick={() => openEdit(rule)}>
                                <EditIcon fontSize="small" />
                              </IconButton>
                            </Tooltip>
                            <Tooltip title={rule.is_active ? 'Disable rule' : 'Enable rule'}>
                              <IconButton
                                size="small"
                                color={rule.is_active ? 'warning' : 'success'}
                                onClick={() => toggle({ rule, active: !rule.is_active })}
                              >
                                {rule.is_active
                                  ? <ToggleOffIcon fontSize="small" />
                                  : <ToggleOnIcon fontSize="small" />}
                              </IconButton>
                            </Tooltip>
                            <Tooltip title="Delete rule">
                              <IconButton
                                size="small"
                                color="error"
                                onClick={() => {
                                  if (confirm('Delete this dedup rule?')) remove(rule.id)
                                }}
                              >
                                <DeleteIcon fontSize="small" />
                              </IconButton>
                            </Tooltip>
                          </Stack>
                        </TableCell>
                      </TableRow>
                    ))}
              </TableBody>
            </Table>
          </TableContainer>
        </GlassCard>

        <RuleDialog
          open={dialogOpen}
          onClose={closeDialog}
          initial={editing}
          cameras={cameras}
        />
      </Box>
    </PermissionGuard>
  )
}
