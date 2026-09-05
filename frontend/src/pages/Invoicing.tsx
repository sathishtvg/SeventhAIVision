/**
 * Client Billing / Invoicing (ShiftSecure final phase) — invoices for
 * guard services delivered at a billing_client's sites, computed from the
 * same shift-hours signals payroll.py uses (actual_start/actual_end +
 * overtime_minutes), grouped by site instead of by guard. Distinct from
 * billing.py's Stripe SaaS subscription billing.
 */
import { useMemo, useState } from 'react'
import {
  Box,
  Typography,
  Chip,
  Skeleton,
  Button,
  Divider,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  TextField,
  IconButton,
  Tabs,
  Tab,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  MenuItem,
} from '@mui/material'
import Stack from '@/components/common/Stack'
import ReceiptLongIcon from '@mui/icons-material/ReceiptLong'
import AccountBalanceIcon from '@mui/icons-material/AccountBalance'
import DomainIcon from '@mui/icons-material/Domain'
import WarningAmberIcon from '@mui/icons-material/WarningAmber'
import AddIcon from '@mui/icons-material/Add'
import EditIcon from '@mui/icons-material/Edit'
import ArrowBackIcon from '@mui/icons-material/ArrowBack'
import DownloadIcon from '@mui/icons-material/Download'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  listClients, createClient, updateClient, createInvoice, listInvoices, getInvoice,
  finalizeInvoice, markInvoicePaid, voidInvoice, deleteInvoice, invoicePdfUrl,
  type BillingClient, type Invoice,
} from '@/api/invoicing'
import { GlassCard } from '@/components/common/GlassCard'
import { PageHeader } from '@/components/common/PageHeader'
import { PermissionGuard } from '@/components/common/PermissionGuard'
import { usePermission } from '@/hooks/usePermission'
import { fadeUpSx, useCountUp } from '@/lib/motion'
import { useAuthStore } from '@/store/auth'

function hexToRgb(hex: string) {
  const m = hex.replace('#', '').match(/.{2}/g)
  return m ? m.map((v) => parseInt(v, 16)).join(',') : '108,99,255'
}

function KpiCard({ label, value, icon, color, isMoney }: {
  label: string; value: number | undefined; icon: React.ReactNode; color: string; isMoney?: boolean
}) {
  const rgb = hexToRgb(color)
  const animatedValue = useCountUp(value)
  return (
    <GlassCard variant="glow" sx={{
      p: 2.5, position: 'relative', overflow: 'hidden',
      borderColor: `rgba(${rgb},0.18)`,
      '&:hover': { borderColor: `rgba(${rgb},0.35)` },
      '&::before': {
        content: '""', position: 'absolute', top: 0, left: 0, right: 0, height: '2px',
        background: `linear-gradient(90deg, transparent 0%, ${color} 50%, transparent 100%)`,
        opacity: 0.7,
      },
    }}>
      <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 1 }}>
        <Box sx={{ minWidth: 0 }}>
          <Typography sx={{
            color: `rgba(${rgb},0.8)`, textTransform: 'uppercase', letterSpacing: '0.1em',
            fontSize: '0.62rem', fontWeight: 700, mb: 0.75,
          }}>
            {label}
          </Typography>
          {value === undefined ? (
            <Skeleton width={60} height={44} sx={{ bgcolor: `rgba(${rgb},0.08)` }} />
          ) : (
            <Typography sx={{
              fontWeight: 800, lineHeight: 1.1, fontSize: '1.7rem', color,
              fontFamily: '"Fira Code", monospace', letterSpacing: '-0.02em',
            }}>
              {isMoney ? `$${animatedValue.toLocaleString()}` : animatedValue.toLocaleString()}
            </Typography>
          )}
        </Box>
        <Box sx={{
          width: 44, height: 44, borderRadius: '12px', background: `rgba(${rgb},0.12)`,
          border: `1px solid rgba(${rgb},0.22)`, display: 'flex', alignItems: 'center',
          justifyContent: 'center', flexShrink: 0, color, boxShadow: `0 0 16px rgba(${rgb},0.2)`,
          '& svg': { fontSize: 22 },
        }}>
          {icon}
        </Box>
      </Box>
    </GlassCard>
  )
}

const STATUS_META: Record<Invoice['status'], { label: string; color: string }> = {
  draft: { label: 'Draft', color: '#FF9800' },
  finalized: { label: 'Finalized', color: '#00D9C0' },
  paid: { label: 'Paid', color: '#00E396' },
  void: { label: 'Void', color: '#94A3B8' },
}

function InvoiceStatusChip({ status }: { status: Invoice['status'] }) {
  const meta = STATUS_META[status]
  const rgb = hexToRgb(meta.color)
  return (
    <Chip label={meta.label} size="small" sx={{
      color: meta.color, backgroundColor: `rgba(${rgb},0.14)`, border: `1px solid rgba(${rgb},0.3)`, fontWeight: 700,
    }} />
  )
}

// ── Clients tab ──────────────────────────────────────────────────────────

function ClientDialog({ open, client, onClose }: { open: boolean; client?: BillingClient; onClose: () => void }) {
  const qc = useQueryClient()
  const isEdit = Boolean(client)
  const [name, setName] = useState(client?.name ?? '')
  const [contactName, setContactName] = useState(client?.contact_name ?? '')
  const [contactEmail, setContactEmail] = useState(client?.contact_email ?? '')
  const [contactPhone, setContactPhone] = useState(client?.contact_phone ?? '')
  const [billingAddress, setBillingAddress] = useState(client?.billing_address ?? '')

  const mutation = useMutation({
    mutationFn: () => {
      const data = {
        name,
        contact_name: contactName || undefined,
        contact_email: contactEmail || undefined,
        contact_phone: contactPhone || undefined,
        billing_address: billingAddress || undefined,
      }
      return isEdit ? updateClient(client!.id, data) : createClient(data)
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['billing-clients'] })
      onClose()
    },
  })

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>{isEdit ? 'Edit Billing Client' : 'Add Billing Client'}</DialogTitle>
      <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: '12px !important' }}>
        <TextField label="Client Name" value={name} onChange={(e) => setName(e.target.value)} required autoFocus fullWidth />
        <TextField label="Contact Name" value={contactName} onChange={(e) => setContactName(e.target.value)} fullWidth />
        <Stack direction="row" spacing={1.5}>
          <TextField label="Contact Email" value={contactEmail} onChange={(e) => setContactEmail(e.target.value)} sx={{ flex: 1 }} />
          <TextField label="Contact Phone" value={contactPhone} onChange={(e) => setContactPhone(e.target.value)} sx={{ flex: 1 }} />
        </Stack>
        <TextField label="Billing Address" value={billingAddress} onChange={(e) => setBillingAddress(e.target.value)} multiline rows={2} fullWidth />
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" disabled={!name.trim() || mutation.isPending} onClick={() => mutation.mutate()}>
          {isEdit ? 'Save' : 'Create'}
        </Button>
      </DialogActions>
    </Dialog>
  )
}

function ClientsTab() {
  const { data: clients = [], isLoading } = useQuery({ queryKey: ['billing-clients'], queryFn: () => listClients() })
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editClient, setEditClient] = useState<BillingClient | undefined>()

  return (
    <Box sx={{ p: 2 }}>
      <Stack direction="row" justifyContent="flex-end" sx={{ mb: 2 }}>
        <PermissionGuard permission="invoicing:manage">
          <Button
            variant="contained" size="small" startIcon={<AddIcon />}
            onClick={() => { setEditClient(undefined); setDialogOpen(true) }}
          >
            Add Client
          </Button>
        </PermissionGuard>
      </Stack>
      {isLoading ? (
        <Skeleton height={80} />
      ) : clients.length === 0 ? (
        <Typography color="text.secondary" sx={{ textAlign: 'center', py: 3 }}>No billing clients yet.</Typography>
      ) : (
        <TableContainer>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Client</TableCell>
                <TableCell>Contact</TableCell>
                <TableCell>Email</TableCell>
                <TableCell>Phone</TableCell>
                <TableCell>Status</TableCell>
                <TableCell align="right">Actions</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {clients.map((c) => (
                <TableRow key={c.id}>
                  <TableCell sx={{ fontWeight: 600 }}>{c.name}</TableCell>
                  <TableCell>{c.contact_name ?? '—'}</TableCell>
                  <TableCell>{c.contact_email ?? '—'}</TableCell>
                  <TableCell>{c.contact_phone ?? '—'}</TableCell>
                  <TableCell>
                    <Chip label={c.is_active ? 'Active' : 'Inactive'} size="small" color={c.is_active ? 'success' : 'default'} />
                  </TableCell>
                  <TableCell align="right">
                    <PermissionGuard permission="invoicing:manage">
                      <IconButton size="small" onClick={() => { setEditClient(c); setDialogOpen(true) }}>
                        <EditIcon fontSize="small" />
                      </IconButton>
                    </PermissionGuard>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      )}
      <ClientDialog key={editClient?.id ?? 'new'} open={dialogOpen} client={editClient} onClose={() => setDialogOpen(false)} />
    </Box>
  )
}

// ── Invoices tab ─────────────────────────────────────────────────────────

function NewInvoiceDialog({ open, onClose, onCreated }: { open: boolean; onClose: () => void; onCreated: (id: string) => void }) {
  const qc = useQueryClient()
  const { data: clients = [] } = useQuery({ queryKey: ['billing-clients'], queryFn: () => listClients() })
  const [clientId, setClientId] = useState('')
  const [periodStart, setPeriodStart] = useState('')
  const [periodEnd, setPeriodEnd] = useState('')
  const [taxRate, setTaxRate] = useState('9')
  const [dueDate, setDueDate] = useState('')

  const { mutate: submit, isPending } = useMutation({
    mutationFn: () => createInvoice({
      client_id: clientId,
      period_start: periodStart,
      period_end: periodEnd,
      tax_rate: taxRate !== '' ? Number(taxRate) / 100 : undefined,
      due_date: dueDate || undefined,
    }),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ['invoices'] })
      setClientId(''); setPeriodStart(''); setPeriodEnd(''); setTaxRate('9'); setDueDate('')
      onCreated(data.id)
    },
  })

  return (
    <Dialog open={open} onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle>New Invoice</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ mt: 1 }}>
          <TextField select size="small" label="Billing Client" fullWidth value={clientId} onChange={(e) => setClientId(e.target.value)}>
            {clients.map((c) => <MenuItem key={c.id} value={c.id}>{c.name}</MenuItem>)}
          </TextField>
          <TextField
            size="small" label="Period Start" type="date" fullWidth
            slotProps={{ inputLabel: { shrink: true } }} value={periodStart} onChange={(e) => setPeriodStart(e.target.value)}
          />
          <TextField
            size="small" label="Period End" type="date" fullWidth
            slotProps={{ inputLabel: { shrink: true } }} value={periodEnd} onChange={(e) => setPeriodEnd(e.target.value)}
          />
          <TextField size="small" label="Tax Rate (%)" type="number" fullWidth value={taxRate} onChange={(e) => setTaxRate(e.target.value)} slotProps={{ htmlInput: { min: 0, step: 0.1 } }} />
          <TextField
            size="small" label="Due Date (optional)" type="date" fullWidth
            slotProps={{ inputLabel: { shrink: true } }} value={dueDate} onChange={(e) => setDueDate(e.target.value)}
          />
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" disabled={!clientId || !periodStart || !periodEnd || isPending} onClick={() => submit()}>
          Generate
        </Button>
      </DialogActions>
    </Dialog>
  )
}

function InvoiceDetail({ invoiceId, onBack }: { invoiceId: string; onBack: () => void }) {
  const qc = useQueryClient()
  const canManage = usePermission('invoicing:manage')
  const accessToken = useAuthStore((s) => s.accessToken)
  const { data: invoice, isLoading } = useQuery({
    queryKey: ['invoice', invoiceId],
    queryFn: () => getInvoice(invoiceId),
  })

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ['invoice', invoiceId] })
    qc.invalidateQueries({ queryKey: ['invoices'] })
  }
  const { mutate: finalize, isPending: finalizing } = useMutation({ mutationFn: () => finalizeInvoice(invoiceId), onSuccess: invalidate })
  const { mutate: markPaid, isPending: markingPaid } = useMutation({ mutationFn: () => markInvoicePaid(invoiceId), onSuccess: invalidate })
  const { mutate: voidInv, isPending: voiding } = useMutation({ mutationFn: () => voidInvoice(invoiceId), onSuccess: invalidate })
  const { mutate: remove, isPending: deleting } = useMutation({
    mutationFn: () => deleteInvoice(invoiceId),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['invoices'] }); onBack() },
  })

  const kpis = useMemo(() => ({
    subtotal: Math.round(invoice?.subtotal ?? 0),
    tax: Math.round(invoice?.tax_amount ?? 0),
    total: Math.round(invoice?.total_amount ?? 0),
    sitesSkipped: invoice?.warnings?.length ?? 0,
  }), [invoice])

  if (isLoading || !invoice) return <Box sx={{ p: 2 }}><Skeleton height={200} /></Box>

  return (
    <Box sx={{ p: 2 }}>
      <Stack direction="row" spacing={1.5} alignItems="center" sx={{ mb: 2, flexWrap: 'wrap' }}>
        <IconButton size="small" onClick={onBack}><ArrowBackIcon fontSize="small" /></IconButton>
        <Typography variant="subtitle1" sx={{ fontWeight: 700 }}>
          {invoice.invoice_number ?? 'Draft'} — {invoice.client_name}
        </Typography>
        <InvoiceStatusChip status={invoice.status} />
        <Box sx={{ flexGrow: 1 }} />
        <PermissionGuard permission="invoicing:manage">
          <Stack direction="row" spacing={1}>
            {invoice.status === 'draft' && (
              <>
                <Button variant="contained" size="small" disabled={finalizing} onClick={() => finalize()}>Finalize</Button>
                <Button variant="outlined" color="error" size="small" disabled={deleting} onClick={() => remove()}>Delete</Button>
              </>
            )}
            {invoice.status === 'finalized' && (
              <>
                <Button variant="contained" size="small" disabled={markingPaid} onClick={() => markPaid()}>Mark Paid</Button>
                <Button variant="outlined" color="error" size="small" disabled={voiding} onClick={() => voidInv()}>Void</Button>
              </>
            )}
            {invoice.status !== 'draft' && canManage && (
              <Button
                variant="outlined" size="small" startIcon={<DownloadIcon />}
                component="a" href={invoicePdfUrl(invoice.id, accessToken) ?? undefined} target="_blank" rel="noopener"
              >
                PDF
              </Button>
            )}
          </Stack>
        </PermissionGuard>
      </Stack>

      <Box sx={{ display: 'flex', gap: 2, mb: 2.5, flexWrap: 'wrap' }}>
        {[
          { label: 'Subtotal', value: kpis.subtotal, icon: <ReceiptLongIcon />, color: '#00D9C0', isMoney: true },
          { label: 'Tax', value: kpis.tax, icon: <AccountBalanceIcon />, color: '#6C63FF', isMoney: true },
          { label: 'Total', value: kpis.total, icon: <ReceiptLongIcon />, color: '#00E396', isMoney: true },
          { label: 'Sites Skipped', value: kpis.sitesSkipped, icon: <WarningAmberIcon />, color: '#FF9800' },
        ].map((kpi, i) => (
          <Box key={kpi.label} sx={{ flex: '1 1 160px', minWidth: 0, ...fadeUpSx(i) }}>
            <KpiCard {...kpi} />
          </Box>
        ))}
      </Box>

      {invoice.warnings && invoice.warnings.length > 0 && (
        <Box sx={{ p: 1.5, mb: 2, borderRadius: '8px', backgroundColor: 'rgba(255,152,0,0.1)', border: '1px solid rgba(255,152,0,0.3)' }}>
          <Typography variant="caption" sx={{ color: '#FF9800', fontWeight: 700, display: 'block', mb: 0.5 }}>
            Skipped sites (no bill rate configured)
          </Typography>
          {invoice.warnings.map((w, i) => (
            <Typography key={i} variant="caption" color="text.secondary" sx={{ display: 'block' }}>{w}</Typography>
          ))}
        </Box>
      )}

      <TableContainer>
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell>Site</TableCell>
              <TableCell align="right">Reg. Hrs</TableCell>
              <TableCell align="right">OT Hrs</TableCell>
              <TableCell align="right">Rate</TableCell>
              <TableCell align="right">Regular Amt</TableCell>
              <TableCell align="right">OT Amt</TableCell>
              <TableCell align="right">Line Total</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {invoice.line_items.map((li) => (
              <TableRow key={li.id}>
                <TableCell>{li.site_name}</TableCell>
                <TableCell align="right">{li.regular_hours.toFixed(1)}</TableCell>
                <TableCell align="right">{li.overtime_hours.toFixed(1)}</TableCell>
                <TableCell align="right">${li.bill_rate.toFixed(2)}</TableCell>
                <TableCell align="right">${li.regular_amount.toFixed(2)}</TableCell>
                <TableCell align="right">${li.overtime_amount.toFixed(2)}</TableCell>
                <TableCell align="right" sx={{ fontWeight: 700 }}>${li.line_total.toFixed(2)}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </TableContainer>
    </Box>
  )
}

function InvoicesTab() {
  const [dialogOpen, setDialogOpen] = useState(false)
  const [selected, setSelected] = useState<string | null>(null)
  const { data: invoices = [], isLoading } = useQuery({ queryKey: ['invoices'], queryFn: () => listInvoices() })

  if (selected) {
    return <InvoiceDetail invoiceId={selected} onBack={() => setSelected(null)} />
  }

  return (
    <Box sx={{ p: 2 }}>
      <Stack direction="row" justifyContent="flex-end" sx={{ mb: 2 }}>
        <PermissionGuard permission="invoicing:manage">
          <Button variant="contained" size="small" startIcon={<AddIcon />} onClick={() => setDialogOpen(true)}>
            New Invoice
          </Button>
        </PermissionGuard>
      </Stack>
      {isLoading ? (
        <Skeleton height={80} />
      ) : invoices.length === 0 ? (
        <Typography color="text.secondary" sx={{ textAlign: 'center', py: 3 }}>No invoices yet.</Typography>
      ) : (
        <Stack divider={<Divider />} spacing={1.5}>
          {invoices.map((inv) => (
            <Box
              key={inv.id}
              onClick={() => setSelected(inv.id)}
              sx={{
                display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 2, py: 0.5,
                cursor: 'pointer', borderRadius: '8px', '&:hover': { backgroundColor: 'rgba(255,255,255,0.04)' },
              }}
            >
              <Box>
                <Typography variant="body2" sx={{ fontWeight: 600 }}>
                  {inv.invoice_number ?? 'Draft'} — {inv.client_name}
                </Typography>
                <Typography variant="caption" color="text.secondary">
                  {inv.period_start} – {inv.period_end} · ${inv.total_amount.toFixed(2)}
                </Typography>
              </Box>
              <InvoiceStatusChip status={inv.status} />
            </Box>
          ))}
        </Stack>
      )}
      <NewInvoiceDialog open={dialogOpen} onClose={() => setDialogOpen(false)} onCreated={(id) => { setDialogOpen(false); setSelected(id) }} />
    </Box>
  )
}

export function InvoicingPage() {
  const [tab, setTab] = useState(0)

  return (
    <Box>
      <PageHeader pageKey="invoicing" />

      <GlassCard>
        <Box sx={{ borderBottom: 1, borderColor: 'rgba(255,255,255,0.1)' }}>
          <Tabs value={tab} onChange={(_, v) => setTab(v)}>
            <Tab label="Clients" icon={<DomainIcon fontSize="small" />} iconPosition="start" />
            <Tab label="Invoices" icon={<ReceiptLongIcon fontSize="small" />} iconPosition="start" />
          </Tabs>
        </Box>
        {tab === 0 && <ClientsTab />}
        {tab === 1 && <InvoicesTab />}
      </GlassCard>
    </Box>
  )
}

export default InvoicingPage
