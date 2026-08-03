import { useState } from 'react'
import {
  Box,
  Typography,
  Tabs,
  Tab,
  Button,
  Chip,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  TextField,
  Alert,
  IconButton,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Paper,
  Tooltip,
  CircularProgress,
  Collapse,
  InputAdornment,
  Select,
  MenuItem,
  FormControl,
  InputLabel,
  Switch,
  FormControlLabel,
  Skeleton,
} from '@mui/material'
import Stack from '@/components/common/Stack'
import {
  Add as AddIcon,
  QrCode2 as QrCodeIcon,
  Email as EmailIcon,
  Login as LoginIcon,
  Logout as LogoutIcon,
  Search as SearchIcon,
  Refresh as RefreshIcon,
  CheckCircle as CheckCircleIcon,
  Schedule as ScheduleIcon,
  PersonOff as PersonOffIcon,
  ContentCopy as CopyIcon,
  Edit as EditIcon,
  Delete as DeleteIcon,
  DynamicForm as DynamicFormIcon,
} from '@mui/icons-material'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { GlassCard } from '@/components/common/GlassCard'
import { usePermission } from '@/hooks/usePermission'
import {
  listVisitors, getUpcomingVisitors, listVisitorLogs,
  createVisitor, checkinVisitor, checkoutVisitor, sendVisitorQR,
  getVisitorQRUrl, deactivateVisitor,
  type Visitor, type VisitorCreate,
} from '@/api/visitors'
import {
  listFormFields, createFormField, updateFormField, deleteFormField,
  FIELD_TYPE_LABELS, OPTION_FIELD_TYPES,
  type VisitorFormField, type VisitorFormFieldInput, type VisitorFieldType,
} from '@/api/vms'
import { getSites } from '@/api/sites'

// ── helpers ───────────────────────────────────────────────────────────────────

function fmtDateTime(s?: string) {
  if (!s) return '—'
  return new Date(s).toLocaleString()
}

function StatusChipV({ status }: { status: string }) {
  const map: Record<string, { label: string; color: 'default' | 'success' | 'info' | 'warning' | 'error' }> = {
    pending: { label: 'Pending', color: 'info' },
    arrived: { label: 'Arrived', color: 'success' },
    departed: { label: 'Departed', color: 'default' },
    cancelled: { label: 'Cancelled', color: 'error' },
    expired: { label: 'Expired', color: 'warning' },
  }
  const cfg = map[status] ?? { label: status, color: 'default' }
  return <Chip label={cfg.label} color={cfg.color} size="small" />
}

// ── QR Popup ──────────────────────────────────────────────────────────────────

function QRDialog({ visitor, onClose }: { visitor: Visitor; onClose: () => void }) {
  const [sending, setSending] = useState(false)
  const [sent, setSent] = useState(false)
  const qryClient = useQueryClient()
  const qrUrl = getVisitorQRUrl(visitor.id)

  const handleSendEmail = async () => {
    setSending(true)
    try {
      await sendVisitorQR(visitor.id)
      setSent(true)
      qryClient.invalidateQueries({ queryKey: ['visitors'] })
    } finally {
      setSending(false)
    }
  }

  return (
    <Dialog open onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle>QR Code — {visitor.full_name}</DialogTitle>
      <DialogContent>
        <Box sx={{ textAlign: 'center', py: 1 }}>
          <Box
            component="img"
            src={qrUrl}
            alt="Visitor QR"
            sx={{ width: 200, height: 200, border: '4px solid white', borderRadius: 2 }}
          />
          <Typography variant="caption" display="block" sx={{ mt: 1, opacity: 0.6 }}>
            Token: {visitor.qr_token.slice(0, 12)}…
          </Typography>
          {visitor.visitor_email && (
            <Typography variant="body2" sx={{ mt: 1 }}>
              Email: {visitor.visitor_email}
            </Typography>
          )}
          {visitor.qr_email_sent_at && (
            <Typography variant="caption" color="success.main" display="block">
              Email sent {fmtDateTime(visitor.qr_email_sent_at)}
            </Typography>
          )}
          {sent && <Alert severity="success" sx={{ mt: 1 }}>QR sent to {visitor.visitor_email}</Alert>}
        </Box>
      </DialogContent>
      <DialogActions>
        {visitor.visitor_email && (
          <Button
            startIcon={sending ? <CircularProgress size={14} /> : <EmailIcon />}
            onClick={handleSendEmail}
            disabled={sending || sent}
          >
            {sent ? 'Sent!' : 'Email QR'}
          </Button>
        )}
        <Button onClick={onClose}>Close</Button>
      </DialogActions>
    </Dialog>
  )
}

// ── Pre-register dialog ───────────────────────────────────────────────────────

function PreRegisterDialog({ onClose }: { onClose: () => void }) {
  const qryClient = useQueryClient()
  const [form, setForm] = useState<VisitorCreate>({ full_name: '' })
  const [error, setError] = useState('')

  const mut = useMutation({
    mutationFn: createVisitor,
    onSuccess: () => {
      qryClient.invalidateQueries({ queryKey: ['visitors'] })
      qryClient.invalidateQueries({ queryKey: ['upcoming-visitors'] })
      onClose()
    },
    onError: (e: any) => setError(e.response?.data?.detail ?? 'Failed to register visitor'),
  })

  const set = (field: keyof VisitorCreate) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setForm(p => ({ ...p, [field]: e.target.value || undefined }))

  return (
    <Dialog open onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>Pre-Register Visitor</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ pt: 1 }}>
          {error && <Alert severity="error">{error}</Alert>}
          <TextField label="Full Name *" value={form.full_name} onChange={set('full_name')} required />
          <TextField label="ID Number" value={form.id_number ?? ''} onChange={set('id_number')} />
          <TextField label="Company" value={form.company ?? ''} onChange={set('company')} />
          <TextField label="Host Name" value={form.host_name ?? ''} onChange={set('host_name')} />
          <TextField label="Purpose of Visit" value={form.purpose ?? ''} onChange={set('purpose')} />
          <TextField label="Vehicle Plate" value={form.vehicle_plate ?? ''} onChange={set('vehicle_plate')} />
          <TextField
            label="Visitor Email (for QR delivery)"
            type="email"
            value={form.visitor_email ?? ''}
            onChange={set('visitor_email')}
          />
          <TextField
            label="Expected From"
            type="datetime-local"
            value={form.expected_from ?? ''}
            onChange={set('expected_from')}
            slotProps={{ inputLabel: { shrink: true } }}
          />
          <TextField
            label="Expected Until"
            type="datetime-local"
            value={form.expected_until ?? ''}
            onChange={set('expected_until')}
            slotProps={{ inputLabel: { shrink: true } }}
          />
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button
          variant="contained"
          onClick={() => mut.mutate(form)}
          disabled={!form.full_name || mut.isPending}
        >
          {mut.isPending ? <CircularProgress size={20} /> : 'Register & Generate QR'}
        </Button>
      </DialogActions>
    </Dialog>
  )
}

// ── Upcoming tab ──────────────────────────────────────────────────────────────

function UpcomingTab() {
  const [qrVisitor, setQrVisitor] = useState<Visitor | null>(null)
  const [search, setSearch] = useState('')
  const canManage = usePermission('visitor:manage')
  const qryClient = useQueryClient()

  const { data: visitors = [], isLoading, refetch } = useQuery({
    queryKey: ['upcoming-visitors'],
    queryFn: () => getUpcomingVisitors(24),
    refetchInterval: 30000,
  })

  const checkinMut = useMutation({
    mutationFn: ({ id }: { id: string }) => checkinVisitor(id, {}),
    onSuccess: () => {
      qryClient.invalidateQueries({ queryKey: ['upcoming-visitors'] })
      qryClient.invalidateQueries({ queryKey: ['visitor-logs'] })
    },
  })

  const checkoutMut = useMutation({
    mutationFn: ({ id }: { id: string }) => checkoutVisitor(id, {}),
    onSuccess: () => {
      qryClient.invalidateQueries({ queryKey: ['upcoming-visitors'] })
      qryClient.invalidateQueries({ queryKey: ['visitor-logs'] })
    },
  })

  const filtered = visitors.filter(v =>
    v.full_name.toLowerCase().includes(search.toLowerCase()) ||
    (v.company ?? '').toLowerCase().includes(search.toLowerCase())
  )

  return (
    <Box>
      <Stack direction="row" spacing={1} sx={{ mb: 2 }}>
        <TextField
          size="small"
          placeholder="Search visitors…"
          value={search}
          onChange={e => setSearch(e.target.value)}
          InputProps={{ startAdornment: <InputAdornment position="start"><SearchIcon fontSize="small" /></InputAdornment> }}
          sx={{ flex: 1 }}
        />
        <IconButton onClick={() => refetch()} size="small"><RefreshIcon /></IconButton>
      </Stack>

      {isLoading ? (
        <Box sx={{ textAlign: 'center', py: 4 }}><CircularProgress /></Box>
      ) : filtered.length === 0 ? (
        <Box sx={{ textAlign: 'center', py: 6, opacity: 0.5 }}>
          <ScheduleIcon sx={{ fontSize: 48, mb: 1 }} />
          <Typography>No upcoming visitors in the next 24 hours</Typography>
        </Box>
      ) : (
        <TableContainer component={Paper} sx={{ background: 'transparent' }}>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Visitor</TableCell>
                <TableCell>Host</TableCell>
                <TableCell>Expected</TableCell>
                <TableCell>Status</TableCell>
                <TableCell align="right">Actions</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {filtered.map(v => (
                <TableRow key={v.id} hover>
                  <TableCell>
                    <Typography variant="body2" fontWeight={600}>{v.full_name}</Typography>
                    <Typography variant="caption" color="text.secondary">{v.company}</Typography>
                  </TableCell>
                  <TableCell>{v.host_name ?? '—'}</TableCell>
                  <TableCell>
                    <Typography variant="caption">{fmtDateTime(v.expected_from)}</Typography>
                  </TableCell>
                  <TableCell><StatusChipV status={v.status} /></TableCell>
                  <TableCell align="right">
                    <Stack direction="row" spacing={0.5} justifyContent="flex-end">
                      <Tooltip title="View QR Code">
                        <IconButton size="small" onClick={() => setQrVisitor(v)}>
                          <QrCodeIcon fontSize="small" />
                        </IconButton>
                      </Tooltip>
                      {v.status === 'pending' && canManage && (
                        <Tooltip title="Manual Check-in">
                          <IconButton
                            size="small"
                            color="success"
                            onClick={() => checkinMut.mutate({ id: v.id })}
                          >
                            <LoginIcon fontSize="small" />
                          </IconButton>
                        </Tooltip>
                      )}
                      {v.status === 'arrived' && canManage && (
                        <Tooltip title="Check Out">
                          <IconButton
                            size="small"
                            color="warning"
                            onClick={() => checkoutMut.mutate({ id: v.id })}
                          >
                            <LogoutIcon fontSize="small" />
                          </IconButton>
                        </Tooltip>
                      )}
                    </Stack>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      )}
      {qrVisitor && <QRDialog visitor={qrVisitor} onClose={() => setQrVisitor(null)} />}
    </Box>
  )
}

// ── Today's Log tab ───────────────────────────────────────────────────────────

function TodayLogTab() {
  const { data: logs = [], isLoading, refetch } = useQuery({
    queryKey: ['visitor-logs'],
    queryFn: () => listVisitorLogs(),
    refetchInterval: 15000,
  })

  const today = new Date().toDateString()
  const todayLogs = logs.filter(l => new Date(l.occurred_at).toDateString() === today)

  return (
    <Box>
      <Stack direction="row" spacing={1} sx={{ mb: 2 }}>
        <Typography variant="h6" sx={{ flex: 1 }}>Today's Visitor Log</Typography>
        <IconButton onClick={() => refetch()} size="small"><RefreshIcon /></IconButton>
      </Stack>
      {isLoading ? (
        <Box sx={{ textAlign: 'center', py: 4 }}><CircularProgress /></Box>
      ) : todayLogs.length === 0 ? (
        <Box sx={{ textAlign: 'center', py: 6, opacity: 0.5 }}>
          <PersonOffIcon sx={{ fontSize: 48, mb: 1 }} />
          <Typography>No visitor activity today</Typography>
        </Box>
      ) : (
        <TableContainer component={Paper} sx={{ background: 'transparent' }}>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Time</TableCell>
                <TableCell>Visitor</TableCell>
                <TableCell>Event</TableCell>
                <TableCell>Method</TableCell>
                <TableCell>Badge</TableCell>
                <TableCell>Notes</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {todayLogs.map(l => (
                <TableRow key={l.id} hover>
                  <TableCell>
                    <Typography variant="caption">{fmtDateTime(l.occurred_at)}</Typography>
                  </TableCell>
                  <TableCell>
                    <Typography variant="body2">{l.visitor_name ?? l.visitor_id.slice(0, 8)}</Typography>
                  </TableCell>
                  <TableCell>
                    {l.event_type === 'arrival'
                      ? <Chip icon={<LoginIcon />} label="Arrival" color="success" size="small" />
                      : <Chip icon={<LogoutIcon />} label="Departure" color="default" size="small" />}
                  </TableCell>
                  <TableCell>
                    <Chip
                      label={l.checkin_method === 'qr_scan' ? 'QR Scan' : 'Manual'}
                      size="small"
                      variant="outlined"
                      color={l.checkin_method === 'qr_scan' ? 'primary' : 'default'}
                    />
                  </TableCell>
                  <TableCell>{l.badge_number ?? '—'}</TableCell>
                  <TableCell>
                    <Typography variant="caption" color="text.secondary">{l.notes ?? '—'}</Typography>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      )}
    </Box>
  )
}

// ── All Visitors tab ──────────────────────────────────────────────────────────

function AllVisitorsTab() {
  const [qrVisitor, setQrVisitor] = useState<Visitor | null>(null)
  const [search, setSearch] = useState('')
  const [addOpen, setAddOpen] = useState(false)
  const canManage = usePermission('visitor:manage')
  const qryClient = useQueryClient()

  const { data: visitors = [], isLoading, refetch } = useQuery({
    queryKey: ['visitors'],
    queryFn: listVisitors,
  })

  const deactivateMut = useMutation({
    mutationFn: deactivateVisitor,
    onSuccess: () => qryClient.invalidateQueries({ queryKey: ['visitors'] }),
  })

  const filtered = visitors.filter(v =>
    v.full_name.toLowerCase().includes(search.toLowerCase()) ||
    (v.company ?? '').toLowerCase().includes(search.toLowerCase()) ||
    (v.id_number ?? '').toLowerCase().includes(search.toLowerCase())
  )

  return (
    <Box>
      <Stack direction="row" spacing={1} sx={{ mb: 2 }}>
        <TextField
          size="small"
          placeholder="Search by name, company or ID…"
          value={search}
          onChange={e => setSearch(e.target.value)}
          InputProps={{ startAdornment: <InputAdornment position="start"><SearchIcon fontSize="small" /></InputAdornment> }}
          sx={{ flex: 1 }}
        />
        <IconButton onClick={() => refetch()} size="small"><RefreshIcon /></IconButton>
        {canManage && (
          <Button variant="contained" size="small" startIcon={<AddIcon />} onClick={() => setAddOpen(true)}>
            Pre-Register
          </Button>
        )}
      </Stack>

      {isLoading ? (
        <Box sx={{ textAlign: 'center', py: 4 }}><CircularProgress /></Box>
      ) : (
        <TableContainer component={Paper} sx={{ background: 'transparent' }}>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Visitor</TableCell>
                <TableCell>Host</TableCell>
                <TableCell>Expected</TableCell>
                <TableCell>Status</TableCell>
                <TableCell>Email</TableCell>
                <TableCell align="right">Actions</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {filtered.map(v => (
                <TableRow key={v.id} hover>
                  <TableCell>
                    <Typography variant="body2" fontWeight={600}>{v.full_name}</Typography>
                    <Typography variant="caption" color="text.secondary">
                      {v.company} {v.id_number ? `· ID: ${v.id_number}` : ''}
                    </Typography>
                  </TableCell>
                  <TableCell>{v.host_name ?? '—'}</TableCell>
                  <TableCell>
                    <Typography variant="caption">{fmtDateTime(v.expected_from)}</Typography>
                  </TableCell>
                  <TableCell><StatusChipV status={v.status} /></TableCell>
                  <TableCell>
                    {v.visitor_email
                      ? <Typography variant="caption">{v.visitor_email}</Typography>
                      : <Typography variant="caption" color="text.secondary">—</Typography>}
                  </TableCell>
                  <TableCell align="right">
                    <Stack direction="row" spacing={0.5} justifyContent="flex-end">
                      <Tooltip title="View QR Code">
                        <IconButton size="small" onClick={() => setQrVisitor(v)}>
                          <QrCodeIcon fontSize="small" />
                        </IconButton>
                      </Tooltip>
                      {canManage && v.is_active && (
                        <Tooltip title="Cancel / Deactivate">
                          <IconButton
                            size="small"
                            color="error"
                            onClick={() => deactivateMut.mutate(v.id)}
                          >
                            <PersonOffIcon fontSize="small" />
                          </IconButton>
                        </Tooltip>
                      )}
                    </Stack>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      )}

      {qrVisitor && <QRDialog visitor={qrVisitor} onClose={() => setQrVisitor(null)} />}
      {addOpen && <PreRegisterDialog onClose={() => setAddOpen(false)} />}
    </Box>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

// ── Form Builder ──────────────────────────────────────────────────────────────

/** field_key is the JSON key the answer is stored under in
 * visitors.custom_fields, so it must satisfy the backend's ^[a-z][a-z0-9_]*$
 * and — critically — must never change after creation, or every answer already
 * recorded under the old key becomes unreadable. The backend enforces this by
 * simply not accepting field_key on update; the UI mirrors it by locking the
 * input once the field exists. */
function slugify(label: string): string {
  return label.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '').slice(0, 50)
}

const EMPTY_FIELD: VisitorFormFieldInput = {
  field_key: '', label: '', field_type: 'text', options: [],
  is_required: false, placeholder: '', help_text: '', sort_order: 0,
}

function FormBuilderTab() {
  const qc = useQueryClient()
  const canManage = usePermission('visitor:manage')
  const [open, setOpen] = useState(false)
  const [editing, setEditing] = useState<VisitorFormField | null>(null)
  const [form, setForm] = useState<VisitorFormFieldInput>(EMPTY_FIELD)
  // One option per line — clearer for an admin typing "Delivery / Meeting /
  // Maintenance" than a comma-separated string they have to escape.
  const [optionText, setOptionText] = useState('')
  const [error, setError] = useState<string | null>(null)

  const { data: fields = [], isLoading, isError, refetch } = useQuery({
    queryKey: ['visitor-form-fields'], queryFn: () => listFormFields(),
  })
  const { data: sites = [] } = useQuery({ queryKey: ['sites'], queryFn: () => getSites() })
  const siteName = (id: string | null) =>
    id ? (sites.find((s: any) => s.id === id)?.name ?? 'Unknown site') : 'All sites'

  const invalidate = () => qc.invalidateQueries({ queryKey: ['visitor-form-fields'] })

  const save = useMutation({
    mutationFn: () => {
      const options = OPTION_FIELD_TYPES.includes(form.field_type)
        ? optionText.split('\n').map((o) => o.trim()).filter(Boolean)
        : []
      const payload = { ...form, options }
      return editing
        ? updateFormField(editing.id, {
            label: payload.label, field_type: payload.field_type, options,
            is_required: payload.is_required, placeholder: payload.placeholder,
            help_text: payload.help_text, sort_order: payload.sort_order,
          })
        : createFormField(payload)
    },
    onSuccess: () => { invalidate(); setOpen(false); setEditing(null) },
    onError: (e: any) => setError(e?.response?.data?.detail ?? 'Could not save field'),
  })

  const remove = useMutation({ mutationFn: deleteFormField, onSuccess: invalidate })

  const openAdd = () => {
    setEditing(null); setForm(EMPTY_FIELD); setOptionText(''); setError(null); setOpen(true)
  }
  const openEdit = (f: VisitorFormField) => {
    setEditing(f)
    setForm({
      field_key: f.field_key, label: f.label, field_type: f.field_type,
      options: f.options, is_required: f.is_required,
      placeholder: f.placeholder ?? '', help_text: f.help_text ?? '',
      sort_order: f.sort_order, site_id: f.site_id,
    })
    setOptionText((f.options ?? []).join('\n'))
    setError(null)
    setOpen(true)
  }
  const set = <K extends keyof VisitorFormFieldInput>(k: K, v: VisitorFormFieldInput[K]) =>
    setForm((f) => ({ ...f, [k]: v }))

  const needsOptions = OPTION_FIELD_TYPES.includes(form.field_type)
  const optionCount = optionText.split('\n').filter((o) => o.trim()).length

  return (
    <Box>
      <Alert severity="info" sx={{ mb: 2 }}>
        These fields appear on the visitor form — both the one an operator fills in when the
        entry camera reads a plate, and the manual entry form. Fields with no site apply
        everywhere; a site-specific field is added on top for that site only.
      </Alert>

      <Box sx={{ display: 'flex', justifyContent: 'flex-end', mb: 2 }}>
        {canManage && (
          <Button startIcon={<AddIcon />} variant="contained" size="medium" onClick={openAdd}>
            Add Field
          </Button>
        )}
      </Box>

      <TableContainer component={Paper} elevation={0} sx={{ background: 'transparent' }}>
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell>Label</TableCell>
              <TableCell>Key</TableCell>
              <TableCell>Type</TableCell>
              <TableCell>Choices</TableCell>
              <TableCell>Scope</TableCell>
              <TableCell>Required</TableCell>
              <TableCell align="right">Actions</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {isLoading && Array.from({ length: 3 }).map((_, i) => (
              <TableRow key={i}>
                {Array.from({ length: 7 }).map((__, j) => <TableCell key={j}><Skeleton /></TableCell>)}
              </TableRow>
            ))}
            {/* A failed fetch must NOT render as "no fields configured" — an admin
                would reasonably conclude the form is empty and start re-adding
                fields that already exist, hitting duplicate-key errors. Observed
                for real when a proxy hiccup returned 502 on load. */}
            {isError && (
              <TableRow>
                <TableCell colSpan={7}>
                  <Alert
                    severity="error"
                    action={<Button size="small" onClick={() => refetch()}>Retry</Button>}
                  >
                    Could not load the form fields. They may still exist — do not re-add them
                    until this loads.
                  </Alert>
                </TableCell>
              </TableRow>
            )}
            {!isLoading && !isError && fields.length === 0 && (
              <TableRow>
                <TableCell colSpan={7}>
                  <Typography color="text.secondary" sx={{ py: 3, textAlign: 'center' }}>
                    No custom fields yet. The visitor form still asks for name, company, ID,
                    host and purpose — add fields here for anything else this site needs.
                  </Typography>
                </TableCell>
              </TableRow>
            )}
            {fields.map((f) => (
              <TableRow key={f.id} hover sx={{ opacity: f.is_active ? 1 : 0.5 }}>
                <TableCell>
                  <Typography variant="body2" fontWeight={600}>{f.label}</Typography>
                  {f.help_text && (
                    <Typography variant="caption" color="text.secondary">{f.help_text}</Typography>
                  )}
                </TableCell>
                <TableCell>
                  <Typography variant="caption" sx={{ fontFamily: 'monospace' }}>{f.field_key}</Typography>
                </TableCell>
                <TableCell>
                  <Chip size="small" label={FIELD_TYPE_LABELS[f.field_type] ?? f.field_type} />
                </TableCell>
                <TableCell>
                  <Typography variant="caption" color="text.secondary">
                    {OPTION_FIELD_TYPES.includes(f.field_type)
                      ? (f.options ?? []).join(', ') || '—'
                      : '—'}
                  </Typography>
                </TableCell>
                <TableCell>
                  <Chip
                    size="small" variant="outlined"
                    label={siteName(f.site_id)}
                    color={f.site_id ? 'primary' : 'default'}
                  />
                </TableCell>
                <TableCell>
                  {f.is_required
                    ? <Chip size="small" color="warning" label="Required" />
                    : <Typography variant="caption" color="text.secondary">Optional</Typography>}
                </TableCell>
                <TableCell align="right">
                  {canManage && (
                    <>
                      <Tooltip title="Edit">
                        <IconButton size="small" onClick={() => openEdit(f)}>
                          <EditIcon fontSize="small" />
                        </IconButton>
                      </Tooltip>
                      <Tooltip title="Deactivate — existing answers are kept">
                        <IconButton size="small" color="error" onClick={() => remove.mutate(f.id)}>
                          <DeleteIcon fontSize="small" />
                        </IconButton>
                      </Tooltip>
                    </>
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </TableContainer>

      <Dialog open={open} onClose={() => setOpen(false)} maxWidth="sm" fullWidth>
        <DialogTitle>{editing ? `Edit “${editing.label}”` : 'Add Visitor Form Field'}</DialogTitle>
        <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: '12px !important' }}>
          {error && <Alert severity="error" onClose={() => setError(null)}>{error}</Alert>}
          <TextField
            label="Label" value={form.label} size="small" fullWidth autoFocus
            onChange={(e) => {
              set('label', e.target.value)
              // Auto-derive the key while creating; never touch it when editing.
              if (!editing) set('field_key', slugify(e.target.value))
            }}
            helperText="What the operator sees on the form"
          />
          <TextField
            label="Field key" value={form.field_key} size="small" fullWidth
            onChange={(e) => set('field_key', slugify(e.target.value))}
            disabled={!!editing}
            helperText={editing
              ? 'Cannot be changed — answers already recorded are stored under this key'
              : 'Lowercase letters, digits and underscores'}
            inputProps={{ style: { fontFamily: 'monospace' } }}
          />
          <Box sx={{ display: 'flex', gap: 2 }}>
            <FormControl size="small" sx={{ flex: 1 }}>
              <InputLabel>Type</InputLabel>
              <Select
                label="Type" value={form.field_type}
                onChange={(e) => set('field_type', e.target.value as VisitorFieldType)}
              >
                {(Object.keys(FIELD_TYPE_LABELS) as VisitorFieldType[]).map((t) => (
                  <MenuItem key={t} value={t}>{FIELD_TYPE_LABELS[t]}</MenuItem>
                ))}
              </Select>
            </FormControl>
            <FormControl size="small" sx={{ flex: 1 }} disabled={!!editing}>
              <InputLabel>Applies to</InputLabel>
              <Select
                label="Applies to" value={form.site_id ?? ''}
                onChange={(e) => set('site_id', e.target.value || null)}
              >
                <MenuItem value="">All sites</MenuItem>
                {sites.map((s: any) => <MenuItem key={s.id} value={s.id}>{s.name}</MenuItem>)}
              </Select>
            </FormControl>
          </Box>

          {needsOptions && (
            <TextField
              label="Choices — one per line" value={optionText} size="small" fullWidth
              multiline minRows={3}
              onChange={(e) => setOptionText(e.target.value)}
              placeholder={'Delivery\nMeeting\nMaintenance'}
              error={optionCount === 0}
              helperText={optionCount === 0
                ? 'A dropdown needs at least one choice, or it is a dead control on the operator’s screen'
                : `${optionCount} choice${optionCount === 1 ? '' : 's'}`}
            />
          )}

          <Box sx={{ display: 'flex', gap: 2 }}>
            <TextField label="Placeholder" value={form.placeholder ?? ''} size="small" sx={{ flex: 1 }}
              onChange={(e) => set('placeholder', e.target.value)} />
            <TextField label="Sort order" type="number" value={form.sort_order ?? 0}
              size="small" sx={{ width: 130 }}
              onChange={(e) => set('sort_order', Number(e.target.value))} />
          </Box>
          <TextField label="Help text" value={form.help_text ?? ''} size="small" fullWidth
            onChange={(e) => set('help_text', e.target.value)} />
          <FormControlLabel
            control={
              <Switch checked={form.is_required ?? false}
                onChange={(e) => set('is_required', e.target.checked)} />
            }
            label="Required — the operator cannot check the visitor in without it"
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setOpen(false)}>Cancel</Button>
          <Button
            variant="contained"
            disabled={
              !form.label.trim() || !form.field_key.trim() ||
              (needsOptions && optionCount === 0) || save.isPending
            }
            onClick={() => save.mutate()}
          >
            {editing ? 'Save' : 'Add Field'}
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  )
}

export default function VisitorPreRegPage() {
  const [tab, setTab] = useState(0)

  return (
    <Box sx={{ p: 3 }}>
      <Typography variant="h4" fontWeight={700} gutterBottom>
        Visitor Pre-Registration
      </Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
        Pre-register visitors, generate QR codes, and track arrivals and departures.
      </Typography>

      <GlassCard sx={{ mb: 3 }}>
        <Tabs value={tab} onChange={(_, v) => setTab(v)}>
          <Tab label="Upcoming" icon={<ScheduleIcon />} iconPosition="start" />
          <Tab label="Today's Log" icon={<LoginIcon />} iconPosition="start" />
          <Tab label="All Visitors" icon={<QrCodeIcon />} iconPosition="start" />
          <Tab label="Form Builder" icon={<DynamicFormIcon />} iconPosition="start" />
        </Tabs>
      </GlassCard>

      <GlassCard sx={{ p: 2 }}>
        {tab === 0 && <UpcomingTab />}
        {tab === 1 && <TodayLogTab />}
        {tab === 2 && <AllVisitorsTab />}
        {tab === 3 && <FormBuilderTab />}
      </GlassCard>
    </Box>
  )
}
