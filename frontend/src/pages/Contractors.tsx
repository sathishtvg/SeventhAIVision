import { useState } from 'react'
import {
  Box, Typography, Grid, Paper, Chip, Button, Tabs, Tab,
  Table, TableHead, TableRow, TableCell, TableBody, IconButton,
  Dialog, DialogTitle, DialogContent, DialogActions, TextField,
  MenuItem, Select, FormControl, InputLabel, Tooltip, CircularProgress,
} from '@mui/material'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import CheckCircleIcon from '@mui/icons-material/CheckCircle'
import BlockIcon from '@mui/icons-material/Block'
import LocalShippingIcon from '@mui/icons-material/LocalShipping'
import EngineeringIcon from '@mui/icons-material/Engineering'
import AssignmentIcon from '@mui/icons-material/Assignment'
import AddIcon from '@mui/icons-material/Add'
import ThumbUpIcon from '@mui/icons-material/ThumbUp'
import ThumbDownIcon from '@mui/icons-material/ThumbDown'
import DoneAllIcon from '@mui/icons-material/DoneAll'
import MoveToInboxIcon from '@mui/icons-material/MoveToInbox'
import CheckIcon from '@mui/icons-material/Check'
import {
  getContractorsDashboard, listContractors, createContractor, vetContractor,
  listWorkPermits, createWorkPermit, approvePermit, rejectPermit, completePermit,
  listDeliveries, createDelivery, receiveDelivery, collectDelivery,
} from '@/api/contractors'
import type { Contractor, Delivery } from '@/api/contractors'
import { FilterRail, type FilterGroup } from '@/components/common/FilterRail'
import { PageHeader } from '@/components/common/PageHeader'

// ── KPI Card ────────────────────────────────────────────────────────────────

function KpiCard({ label, value, icon, color }: { label: string; value: number; icon: React.ReactNode; color: string }) {
  return (
    <Paper sx={{ p: 2, display: 'flex', alignItems: 'center', gap: 2, borderRadius: 2 }}>
      <Box sx={{
        width: 44, height: 44, borderRadius: '12px', display: 'flex',
        alignItems: 'center', justifyContent: 'center',
        background: `${color}22`, color,
      }}>
        {icon}
      </Box>
      <Box>
        <Typography variant="h5" sx={{ fontWeight: 700 }}>{value ?? 0}</Typography>
        <Typography variant="caption" color="text.secondary">{label}</Typography>
      </Box>
    </Paper>
  )
}

// ── Vetting status chip ──────────────────────────────────────────────────────

const VETTING_COLOR: Record<string, 'default' | 'warning' | 'success' | 'error'> = {
  pending: 'warning', approved: 'success', suspended: 'error', rejected: 'error',
}

function VettingChip({ status }: { status: string }) {
  return <Chip label={status} color={VETTING_COLOR[status] ?? 'default'} size="small" />
}

const PERMIT_COLOR: Record<string, 'default' | 'warning' | 'success' | 'error' | 'info'> = {
  pending: 'warning', approved: 'success', rejected: 'error',
  active: 'info', completed: 'default', cancelled: 'default',
}

function PermitChip({ status }: { status: string }) {
  return <Chip label={status} color={PERMIT_COLOR[status] ?? 'default'} size="small" />
}

const DELIVERY_COLOR: Record<string, 'default' | 'warning' | 'success' | 'error' | 'info'> = {
  pending: 'warning', received: 'info', collected: 'success', rejected: 'error', returned: 'default',
}

function DeliveryChip({ status }: { status: string }) {
  return <Chip label={status} color={DELIVERY_COLOR[status] ?? 'default'} size="small" />
}

// ── Add Contractor Dialog ─────────────────────────────────────────────────────

function AddContractorDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const qc = useQueryClient()
  const [form, setForm] = useState({
    company_name: '', registration_number: '', contact_name: '',
    contact_phone: '', contact_email: '', address: '', specialization: '',
  })
  const mut = useMutation({
    mutationFn: () => createContractor(form),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['contractors'] }); onClose() },
  })
  const set = (k: string) => (e: React.ChangeEvent<HTMLInputElement>) => setForm(f => ({ ...f, [k]: e.target.value }))
  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>Register Contractor</DialogTitle>
      <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 2 }}>
        <TextField label="Company Name *" value={form.company_name} onChange={set('company_name')} size="small" fullWidth />
        <TextField label="Registration Number" value={form.registration_number} onChange={set('registration_number')} size="small" fullWidth />
        <TextField label="Contact Name" value={form.contact_name} onChange={set('contact_name')} size="small" fullWidth />
        <TextField label="Contact Phone" value={form.contact_phone} onChange={set('contact_phone')} size="small" fullWidth />
        <TextField label="Contact Email" value={form.contact_email} onChange={set('contact_email')} size="small" fullWidth />
        <TextField label="Address" value={form.address} onChange={set('address')} size="small" fullWidth multiline rows={2} />
        <TextField label="Specialization" value={form.specialization} onChange={set('specialization')} size="small" fullWidth />
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" disabled={!form.company_name || mut.isPending} onClick={() => mut.mutate()}>
          {mut.isPending ? <CircularProgress size={18} /> : 'Register'}
        </Button>
      </DialogActions>
    </Dialog>
  )
}

// ── Vet Contractor Dialog ─────────────────────────────────────────────────────

function VetDialog({ contractor, onClose }: { contractor: Contractor; onClose: () => void }) {
  const qc = useQueryClient()
  const [status, setStatus] = useState<string>('approved')
  const [notes, setNotes] = useState('')
  const mut = useMutation({
    mutationFn: () => vetContractor(contractor.id, status, notes),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['contractors'] }); onClose() },
  })
  return (
    <Dialog open onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle>Vet Contractor — {contractor.company_name}</DialogTitle>
      <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 2 }}>
        <FormControl size="small" fullWidth>
          <InputLabel>Vetting Status</InputLabel>
          <Select value={status} label="Vetting Status" onChange={e => setStatus(e.target.value)}>
            <MenuItem value="approved">Approved</MenuItem>
            <MenuItem value="suspended">Suspended</MenuItem>
            <MenuItem value="rejected">Rejected</MenuItem>
          </Select>
        </FormControl>
        <TextField label="Notes" value={notes} onChange={e => setNotes(e.target.value)} size="small" fullWidth multiline rows={3} />
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" disabled={mut.isPending} onClick={() => mut.mutate()}>
          {mut.isPending ? <CircularProgress size={18} /> : 'Save'}
        </Button>
      </DialogActions>
    </Dialog>
  )
}

// ── Create Work Permit Dialog ─────────────────────────────────────────────────

function AddPermitDialog({ open, onClose, contractors }: {
  open: boolean; onClose: () => void; contractors: Contractor[]
}) {
  const qc = useQueryClient()
  const approvedContractors = contractors.filter(c => c.vetting_status === 'approved')
  const [form, setForm] = useState({
    contractor_id: '', work_description: '', work_type: '',
    requested_by_name: '', workers_count: '1', vehicles_count: '0',
    start_at: '', end_at: '',
  })
  const set = (k: string) => (e: React.ChangeEvent<HTMLInputElement>) => setForm(f => ({ ...f, [k]: e.target.value }))
  const mut = useMutation({
    mutationFn: () => createWorkPermit({
      ...form,
      workers_count: parseInt(form.workers_count) || 1,
      vehicles_count: parseInt(form.vehicles_count) || 0,
    }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['work-permits'] }); onClose() },
  })
  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>New Work Permit</DialogTitle>
      <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 2 }}>
        <FormControl size="small" fullWidth>
          <InputLabel>Contractor *</InputLabel>
          <Select value={form.contractor_id} label="Contractor *"
            onChange={e => setForm(f => ({ ...f, contractor_id: e.target.value as string }))}>
            {approvedContractors.map(c => (
              <MenuItem key={c.id} value={c.id}>{c.company_name}</MenuItem>
            ))}
          </Select>
        </FormControl>
        <TextField label="Work Description *" value={form.work_description} onChange={set('work_description')} size="small" fullWidth multiline rows={2} />
        <TextField label="Work Type" value={form.work_type} onChange={set('work_type')} size="small" fullWidth />
        <TextField label="Requested By" value={form.requested_by_name} onChange={set('requested_by_name')} size="small" fullWidth />
        <Grid container spacing={1}>
          <Grid size={6}><TextField label="Workers Count" type="number" value={form.workers_count} onChange={set('workers_count')} size="small" fullWidth /></Grid>
          <Grid size={6}><TextField label="Vehicles Count" type="number" value={form.vehicles_count} onChange={set('vehicles_count')} size="small" fullWidth /></Grid>
          <Grid size={6}><TextField label="Start Date/Time *" type="datetime-local" value={form.start_at} onChange={set('start_at')} size="small" fullWidth slotProps={{ inputLabel: { shrink: true } }} /></Grid>
          <Grid size={6}><TextField label="End Date/Time *" type="datetime-local" value={form.end_at} onChange={set('end_at')} size="small" fullWidth slotProps={{ inputLabel: { shrink: true } }} /></Grid>
        </Grid>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" disabled={!form.contractor_id || !form.work_description || !form.start_at || !form.end_at || mut.isPending} onClick={() => mut.mutate()}>
          {mut.isPending ? <CircularProgress size={18} /> : 'Create'}
        </Button>
      </DialogActions>
    </Dialog>
  )
}

// ── Log Delivery Dialog ───────────────────────────────────────────────────────

function AddDeliveryDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const qc = useQueryClient()
  const [form, setForm] = useState({
    recipient_name: '', tracking_number: '', carrier: '',
    sender_name: '', sender_company: '', recipient_department: '', description: '',
  })
  const set = (k: string) => (e: React.ChangeEvent<HTMLInputElement>) => setForm(f => ({ ...f, [k]: e.target.value }))
  const mut = useMutation({
    mutationFn: () => createDelivery(form),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['deliveries'] }); onClose() },
  })
  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>Log Delivery</DialogTitle>
      <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 2 }}>
        <TextField label="Recipient Name *" value={form.recipient_name} onChange={set('recipient_name')} size="small" fullWidth />
        <TextField label="Recipient Department" value={form.recipient_department} onChange={set('recipient_department')} size="small" fullWidth />
        <TextField label="Tracking Number" value={form.tracking_number} onChange={set('tracking_number')} size="small" fullWidth />
        <TextField label="Carrier" value={form.carrier} onChange={set('carrier')} size="small" fullWidth />
        <TextField label="Sender Name" value={form.sender_name} onChange={set('sender_name')} size="small" fullWidth />
        <TextField label="Sender Company" value={form.sender_company} onChange={set('sender_company')} size="small" fullWidth />
        <TextField label="Description" value={form.description} onChange={set('description')} size="small" fullWidth multiline rows={2} />
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" disabled={!form.recipient_name || mut.isPending} onClick={() => mut.mutate()}>
          {mut.isPending ? <CircularProgress size={18} /> : 'Log'}
        </Button>
      </DialogActions>
    </Dialog>
  )
}

// ── Collect Delivery Dialog ───────────────────────────────────────────────────

function CollectDialog({ delivery, onClose }: { delivery: Delivery; onClose: () => void }) {
  const qc = useQueryClient()
  const [name, setName] = useState('')
  const mut = useMutation({
    mutationFn: () => collectDelivery(delivery.id, name),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['deliveries'] }); onClose() },
  })
  return (
    <Dialog open onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle>Mark Collected — {delivery.recipient_name}</DialogTitle>
      <DialogContent sx={{ pt: 2 }}>
        <TextField label="Collected By *" value={name} onChange={e => setName(e.target.value)} size="small" fullWidth />
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" disabled={!name || mut.isPending} onClick={() => mut.mutate()}>
          {mut.isPending ? <CircularProgress size={18} /> : 'Confirm'}
        </Button>
      </DialogActions>
    </Dialog>
  )
}

// ── Contractors tab ───────────────────────────────────────────────────────────

function ContractorsTab() {
  const [addOpen, setAddOpen] = useState(false)
  const [vetTarget, setVetTarget] = useState<Contractor | null>(null)

  const { data: contractors = [], isLoading } = useQuery({
    queryKey: ['contractors'],
    queryFn: () => listContractors(),
    refetchInterval: 30000,
  })

  return (
    <Box>
      <Box sx={{ display: 'flex', justifyContent: 'flex-end', mb: 2 }}>
        <Button variant="contained" size="small" startIcon={<AddIcon />} onClick={() => setAddOpen(true)}>
          Register Contractor
        </Button>
      </Box>

      {isLoading ? (
        <Box sx={{ display: 'flex', justifyContent: 'center', py: 6 }}><CircularProgress /></Box>
      ) : (
        <Paper>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Company</TableCell>
                <TableCell>Reg. No.</TableCell>
                <TableCell>Contact</TableCell>
                <TableCell>Specialization</TableCell>
                <TableCell>Vetting</TableCell>
                <TableCell>Permits</TableCell>
                <TableCell>Docs</TableCell>
                <TableCell align="right">Actions</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {contractors.map((c) => (
                <TableRow key={c.id} hover>
                  <TableCell>
                    <Typography variant="body2" sx={{ fontWeight: 600 }}>{c.company_name}</Typography>
                    {c.contact_email && (
                      <Typography variant="caption" color="text.secondary">{c.contact_email}</Typography>
                    )}
                  </TableCell>
                  <TableCell>
                    <Typography variant="caption">{c.registration_number || '—'}</Typography>
                  </TableCell>
                  <TableCell>
                    <Typography variant="body2">{c.contact_name || '—'}</Typography>
                    {c.contact_phone && (
                      <Typography variant="caption" color="text.secondary">{c.contact_phone}</Typography>
                    )}
                  </TableCell>
                  <TableCell>
                    <Typography variant="caption">{c.specialization || '—'}</Typography>
                  </TableCell>
                  <TableCell><VettingChip status={c.vetting_status} /></TableCell>
                  <TableCell>
                    <Typography variant="body2">{c.active_permits ?? 0}</Typography>
                  </TableCell>
                  <TableCell>
                    <Typography variant="body2">{c.accreditation_count ?? 0}</Typography>
                  </TableCell>
                  <TableCell align="right">
                    <Tooltip title="Vet / Update Status">
                      <IconButton size="small" onClick={() => setVetTarget(c)}>
                        <CheckCircleIcon fontSize="small" />
                      </IconButton>
                    </Tooltip>
                  </TableCell>
                </TableRow>
              ))}
              {contractors.length === 0 && (
                <TableRow>
                  <TableCell colSpan={8} align="center" sx={{ py: 4, color: 'text.secondary' }}>
                    No contractors registered yet
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </Paper>
      )}

      <AddContractorDialog open={addOpen} onClose={() => setAddOpen(false)} />
      {vetTarget && <VetDialog contractor={vetTarget} onClose={() => setVetTarget(null)} />}
    </Box>
  )
}

// ── Work Permits tab ──────────────────────────────────────────────────────────

function WorkPermitsTab() {
  const [addOpen, setAddOpen] = useState(false)
  const [statusFilter, setStatusFilter] = useState('')
  const qc = useQueryClient()

  const { data: _permData, isLoading } = useQuery({
    queryKey: ['work-permits', statusFilter],
    queryFn: () => listWorkPermits(statusFilter ? { status: statusFilter } : undefined),
    refetchInterval: 30000,
  })
  const permits = _permData?.items ?? []

  const { data: contractors = [] } = useQuery({
    queryKey: ['contractors'],
    queryFn: () => listContractors(),
  })

  const approveMut = useMutation({
    mutationFn: (id: string) => approvePermit(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['work-permits'] }),
  })
  const rejectMut = useMutation({
    mutationFn: (id: string) => rejectPermit(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['work-permits'] }),
  })
  const completeMut = useMutation({
    mutationFn: (id: string) => completePermit(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['work-permits'] }),
  })

  const fmtDate = (d?: string) => d ? new Date(d).toLocaleString() : '—'

  const filterGroups: FilterGroup[] = [{
    key: 'status',
    label: 'Status',
    value: statusFilter,
    onChange: setStatusFilter,
    options: [
      { value: '', label: 'All' },
      ...['pending', 'approved', 'active', 'completed', 'rejected', 'cancelled'].map((v) => ({
        value: v, label: v.charAt(0).toUpperCase() + v.slice(1),
      })),
    ],
  }]

  return (
    <Box sx={{ display: 'flex', alignItems: 'flex-start', gap: 2 }}>
      <Box sx={{ flex: 1, minWidth: 0 }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, mb: 2 }}>
        <Box sx={{ flex: 1 }} />
        <Button variant="contained" size="small" startIcon={<AddIcon />} onClick={() => setAddOpen(true)}>
          New Permit
        </Button>
      </Box>

      {isLoading ? (
        <Box sx={{ display: 'flex', justifyContent: 'center', py: 6 }}><CircularProgress /></Box>
      ) : (
        <Paper>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Permit #</TableCell>
                <TableCell>Contractor</TableCell>
                <TableCell>Description</TableCell>
                <TableCell>Workers</TableCell>
                <TableCell>Start</TableCell>
                <TableCell>End</TableCell>
                <TableCell>Status</TableCell>
                <TableCell>Briefing</TableCell>
                <TableCell align="right">Actions</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {permits.map((p) => (
                <TableRow key={p.id} hover>
                  <TableCell>
                    <Typography variant="caption" sx={{ fontFamily: "monospace" }}>{p.permit_number || p.id.slice(0, 8)}</Typography>
                  </TableCell>
                  <TableCell>
                    <Typography variant="body2">{p.company_name || '—'}</Typography>
                  </TableCell>
                  <TableCell sx={{ maxWidth: 220 }}>
                    <Typography variant="body2" noWrap>{p.work_description}</Typography>
                    {p.work_type && <Typography variant="caption" color="text.secondary">{p.work_type}</Typography>}
                  </TableCell>
                  <TableCell>
                    <Typography variant="body2">{p.workers_count}</Typography>
                  </TableCell>
                  <TableCell>
                    <Typography variant="caption">{fmtDate(p.start_at)}</Typography>
                  </TableCell>
                  <TableCell>
                    <Typography variant="caption">{fmtDate(p.end_at)}</Typography>
                  </TableCell>
                  <TableCell><PermitChip status={p.status} /></TableCell>
                  <TableCell>
                    {p.safety_briefing_done
                      ? <CheckIcon fontSize="small" color="success" />
                      : <BlockIcon fontSize="small" color="disabled" />}
                  </TableCell>
                  <TableCell align="right">
                    {p.status === 'pending' && (
                      <>
                        <Tooltip title="Approve">
                          <IconButton size="small" color="success" onClick={() => approveMut.mutate(p.id)}>
                            <ThumbUpIcon fontSize="small" />
                          </IconButton>
                        </Tooltip>
                        <Tooltip title="Reject">
                          <IconButton size="small" color="error" onClick={() => rejectMut.mutate(p.id)}>
                            <ThumbDownIcon fontSize="small" />
                          </IconButton>
                        </Tooltip>
                      </>
                    )}
                    {(p.status === 'approved' || p.status === 'active') && (
                      <Tooltip title="Mark Complete">
                        <IconButton size="small" color="primary" onClick={() => completeMut.mutate(p.id)}>
                          <DoneAllIcon fontSize="small" />
                        </IconButton>
                      </Tooltip>
                    )}
                  </TableCell>
                </TableRow>
              ))}
              {permits.length === 0 && (
                <TableRow>
                  <TableCell colSpan={9} align="center" sx={{ py: 4, color: 'text.secondary' }}>
                    No work permits found
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </Paper>
      )}

      <AddPermitDialog open={addOpen} onClose={() => setAddOpen(false)} contractors={contractors} />
      </Box>

      <FilterRail groups={filterGroups} storageKey="contractor-permits" />
    </Box>
  )
}

// ── Deliveries tab ────────────────────────────────────────────────────────────

function DeliveriesTab() {
  const [addOpen, setAddOpen] = useState(false)
  const [statusFilter, setStatusFilter] = useState('')
  const [collectTarget, setCollectTarget] = useState<Delivery | null>(null)
  const qc = useQueryClient()

  const { data: deliveries = [], isLoading } = useQuery({
    queryKey: ['deliveries', statusFilter],
    queryFn: () => listDeliveries(statusFilter ? { status: statusFilter } : undefined),
    refetchInterval: 30000,
  })

  const receiveMut = useMutation({
    mutationFn: (id: string) => receiveDelivery(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['deliveries'] }),
  })

  const fmtDate = (d?: string) => d ? new Date(d).toLocaleString() : '—'

  const filterGroups: FilterGroup[] = [{
    key: 'status',
    label: 'Status',
    value: statusFilter,
    onChange: setStatusFilter,
    options: [
      { value: '', label: 'All' },
      ...['pending', 'received', 'collected', 'rejected', 'returned'].map((v) => ({
        value: v, label: v.charAt(0).toUpperCase() + v.slice(1),
      })),
    ],
  }]

  return (
    <Box sx={{ display: 'flex', alignItems: 'flex-start', gap: 2 }}>
      <Box sx={{ flex: 1, minWidth: 0 }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, mb: 2 }}>
        <Box sx={{ flex: 1 }} />
        <Button variant="contained" size="small" startIcon={<AddIcon />} onClick={() => setAddOpen(true)}>
          Log Delivery
        </Button>
      </Box>

      {isLoading ? (
        <Box sx={{ display: 'flex', justifyContent: 'center', py: 6 }}><CircularProgress /></Box>
      ) : (
        <Paper>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Tracking #</TableCell>
                <TableCell>Carrier</TableCell>
                <TableCell>Sender</TableCell>
                <TableCell>Recipient</TableCell>
                <TableCell>Dept</TableCell>
                <TableCell>Received At</TableCell>
                <TableCell>Collected By</TableCell>
                <TableCell>Status</TableCell>
                <TableCell align="right">Actions</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {deliveries.map((d) => (
                <TableRow key={d.id} hover>
                  <TableCell>
                    <Typography variant="caption" sx={{ fontFamily: "monospace" }}>{d.tracking_number || '—'}</Typography>
                  </TableCell>
                  <TableCell><Typography variant="body2">{d.carrier || '—'}</Typography></TableCell>
                  <TableCell>
                    <Typography variant="body2">{d.sender_name || '—'}</Typography>
                    {d.sender_company && <Typography variant="caption" color="text.secondary">{d.sender_company}</Typography>}
                  </TableCell>
                  <TableCell><Typography variant="body2" sx={{ fontWeight: 500 }}>{d.recipient_name}</Typography></TableCell>
                  <TableCell><Typography variant="caption">{d.recipient_department || '—'}</Typography></TableCell>
                  <TableCell><Typography variant="caption">{fmtDate(d.received_at)}</Typography></TableCell>
                  <TableCell>
                    {d.collected_by_name
                      ? <Typography variant="caption">{d.collected_by_name}</Typography>
                      : <Typography variant="caption" color="text.secondary">—</Typography>}
                  </TableCell>
                  <TableCell><DeliveryChip status={d.status} /></TableCell>
                  <TableCell align="right">
                    {d.status === 'pending' && (
                      <Tooltip title="Mark Received">
                        <IconButton size="small" color="info" onClick={() => receiveMut.mutate(d.id)}>
                          <MoveToInboxIcon fontSize="small" />
                        </IconButton>
                      </Tooltip>
                    )}
                    {d.status === 'received' && (
                      <Tooltip title="Mark Collected">
                        <IconButton size="small" color="success" onClick={() => setCollectTarget(d)}>
                          <CheckCircleIcon fontSize="small" />
                        </IconButton>
                      </Tooltip>
                    )}
                  </TableCell>
                </TableRow>
              ))}
              {deliveries.length === 0 && (
                <TableRow>
                  <TableCell colSpan={9} align="center" sx={{ py: 4, color: 'text.secondary' }}>
                    No deliveries found
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </Paper>
      )}

      <AddDeliveryDialog open={addOpen} onClose={() => setAddOpen(false)} />
      {collectTarget && <CollectDialog delivery={collectTarget} onClose={() => setCollectTarget(null)} />}
      </Box>

      <FilterRail groups={filterGroups} storageKey="contractor-deliveries" />
    </Box>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function ContractorsPage() {
  const [tab, setTab] = useState(0)

  const { data: dash } = useQuery({
    queryKey: ['contractors-dashboard'],
    queryFn: getContractorsDashboard,
    refetchInterval: 30000,
  })

  return (
    <Box sx={{ p: 3 }}>
      <PageHeader pageKey="contractors" />
      <Typography variant="h5" sx={{ fontWeight: 700, mb: 3 }}>
        Contractor &amp; Delivery Management
      </Typography>

      {/* KPI row */}
      <Grid container spacing={2} sx={{ mb: 3 }}>
        <Grid size={{ xs: 12, sm: 6, md: 3 }}>
          <KpiCard label="Total Contractors" value={dash?.total_contractors ?? 0} icon={<EngineeringIcon />} color="#6C63FF" />
        </Grid>
        <Grid size={{ xs: 12, sm: 6, md: 3 }}>
          <KpiCard label="Pending Vetting" value={dash?.pending_vetting ?? 0} icon={<AssignmentIcon />} color="#FF9800" />
        </Grid>
        <Grid size={{ xs: 12, sm: 6, md: 3 }}>
          <KpiCard label="Active Permits" value={dash?.active_permits ?? 0} icon={<CheckCircleIcon />} color="#00E396" />
        </Grid>
        <Grid size={{ xs: 12, sm: 6, md: 3 }}>
          <KpiCard label="Pending Deliveries" value={dash?.pending_deliveries ?? 0} icon={<LocalShippingIcon />} color="#00D9C0" />
        </Grid>
      </Grid>

      {/* Tabs */}
      <Tabs value={tab} onChange={(_, v) => setTab(v)} sx={{ mb: 2, borderBottom: 1, borderColor: 'divider' }}>
        <Tab label="Contractors" icon={<EngineeringIcon fontSize="small" />} iconPosition="start" />
        <Tab label="Work Permits" icon={<AssignmentIcon fontSize="small" />} iconPosition="start" />
        <Tab label="Deliveries" icon={<LocalShippingIcon fontSize="small" />} iconPosition="start" />
      </Tabs>

      {tab === 0 && <ContractorsTab />}
      {tab === 1 && <WorkPermitsTab />}
      {tab === 2 && <DeliveriesTab />}
    </Box>
  )
}
