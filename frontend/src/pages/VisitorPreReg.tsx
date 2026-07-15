import { useState } from 'react'
import {
  Box, Typography, Tabs, Tab, Button, Chip, Dialog, DialogTitle,
  DialogContent, DialogActions, TextField, Alert, IconButton,
  Table, TableBody, TableCell, TableContainer, TableHead, TableRow,
  Paper, Tooltip, CircularProgress, Collapse, Stack, InputAdornment,
} from '@mui/material'
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
            InputLabelProps={{ shrink: true }}
          />
          <TextField
            label="Expected Until"
            type="datetime-local"
            value={form.expected_until ?? ''}
            onChange={set('expected_until')}
            InputLabelProps={{ shrink: true }}
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
        </Tabs>
      </GlassCard>

      <GlassCard sx={{ p: 2 }}>
        {tab === 0 && <UpcomingTab />}
        {tab === 1 && <TodayLogTab />}
        {tab === 2 && <AllVisitorsTab />}
      </GlassCard>
    </Box>
  )
}
