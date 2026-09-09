/**
 * Invoices, payments and what is still owed.
 *
 * The order of operations here is the design. An invoice is raised as a DRAFT
 * from the customer's actual usage, somebody reads it, and only then is it
 * issued — after which it cannot be edited, by the API or by anyone with a
 * database client. Correcting one that has been sent means a credit note, so
 * the correction is a document rather than a silent change.
 *
 * Marking something paid is not a button. Payments are recorded, and an
 * invoice becomes paid when they sum to its total — which means a part payment
 * shows as a part payment and a refund shows as a refund, instead of a status
 * somebody set once and forgot.
 *
 * The preview before raising is deliberate too: seeing the bill while it is
 * still free to fix is the cheapest place to catch a mistake.
 */
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Alert, Box, Button, Chip, Dialog, DialogActions, DialogContent, DialogTitle,
  Divider, FormControl, InputLabel, MenuItem, Select, Skeleton, Table,
  TableBody, TableCell, TableContainer, TableHead, TableRow, TextField,
  ToggleButton, ToggleButtonGroup, Tooltip, Typography,
} from '@mui/material'
import Stack from '@/components/common/Stack'
import AddIcon from '@mui/icons-material/Add'
import { GlassCard } from '@/components/common/GlassCard'
import { PageHeader } from '@/components/common/PageHeader'
import {
  createInvoice, creditInvoice, getInvoice, getInvoices, getOutstanding,
  previewPrice, recordPayment, setInvoiceStatus,
} from '@/api/platformBilling'
import { getTenantUsage } from '@/api/platform'

const STATUS_COLOUR: Record<string, string> = {
  draft: '#8B85FF',
  issued: '#6C63FF',
  paid: '#00D9C0',
  overdue: '#FF4560',
  cancelled: '#7A7A8C',
  credited: '#FFB020',
}

const METHODS = ['bank_transfer', 'card', 'cheque', 'cash', 'stripe', 'other']

function money(v: string | number | null | undefined, currency = 'USD') {
  if (v === null || v === undefined) return '—'
  return Number(v).toLocaleString(undefined,
    { style: 'currency', currency, minimumFractionDigits: 2 })
}

function when(iso: string | null) {
  return iso ? new Date(iso).toLocaleDateString() : '—'
}

function StatusChip({ status }: { status: string }) {
  const colour = STATUS_COLOUR[status] ?? '#8B85FF'
  return <Chip label={status} size="small"
               sx={{ height: 19, fontSize: '0.62rem', color: colour,
                     bgcolor: `${colour}22` }} />
}

export default function PlatformInvoices() {
  const queryClient = useQueryClient()
  const [filter, setFilter] = useState('all')
  const [openId, setOpenId] = useState<string | null>(null)
  const [raiseOpen, setRaiseOpen] = useState(false)
  const [tenantId, setTenantId] = useState('')
  const [taxRate, setTaxRate] = useState('0.09')
  const [discount, setDiscount] = useState('0')
  const [payAmount, setPayAmount] = useState('')
  const [payMethod, setPayMethod] = useState('bank_transfer')
  const [payReference, setPayReference] = useState('')
  const [error, setError] = useState('')

  const { data: invoices, isLoading } = useQuery({
    queryKey: ['platform-invoices', filter],
    queryFn: () => getInvoices(filter === 'all' ? {} : { status: filter }),
  })
  const { data: tenants } = useQuery({
    queryKey: ['platform-tenants'], queryFn: getTenantUsage,
  })
  const { data: owed } = useQuery({
    queryKey: ['platform-outstanding'], queryFn: getOutstanding,
  })
  const { data: detail } = useQuery({
    queryKey: ['platform-invoice', openId],
    queryFn: () => getInvoice(openId!),
    enabled: Boolean(openId),
  })
  // Priced before it is raised: seeing the bill while it is still free to fix
  // is the cheapest place to catch a mistake.
  const { data: quote } = useQuery({
    queryKey: ['platform-quote', tenantId, taxRate, discount],
    queryFn: () => previewPrice({ tenant_id: tenantId, tax_rate: taxRate,
                                  discount_amount: discount }),
    enabled: raiseOpen && Boolean(tenantId),
  })

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['platform-invoices'] })
    queryClient.invalidateQueries({ queryKey: ['platform-invoice'] })
    queryClient.invalidateQueries({ queryKey: ['platform-outstanding'] })
  }

  const raise = useMutation({
    mutationFn: () => createInvoice({ tenant_id: tenantId, tax_rate: taxRate,
                                      discount_amount: discount }),
    onSuccess: (inv) => {
      setRaiseOpen(false); setTenantId(''); setError('')
      setOpenId(inv.id); invalidate()
    },
    onError: (e: { response?: { data?: { detail?: string } } }) =>
      setError(e.response?.data?.detail || 'Could not raise the invoice'),
  })

  const changeStatus = useMutation({
    mutationFn: ({ id, status }: { id: string; status: 'issued' | 'cancelled' }) =>
      setInvoiceStatus(id, status),
    onSuccess: invalidate,
    onError: (e: { response?: { data?: { detail?: string } } }) =>
      setError(e.response?.data?.detail || 'Could not change the status'),
  })

  const pay = useMutation({
    mutationFn: () => recordPayment(openId!, {
      amount: payAmount, method: payMethod, reference: payReference || undefined,
    }),
    onSuccess: () => { setPayAmount(''); setPayReference(''); setError(''); invalidate() },
    onError: (e: { response?: { data?: { detail?: string } } }) =>
      setError(e.response?.data?.detail || 'Could not record the payment'),
  })

  const credit = useMutation({
    mutationFn: () => creditInvoice(openId!),
    onSuccess: () => { setOpenId(null); invalidate() },
    onError: (e: { response?: { data?: { detail?: string } } }) =>
      setError(e.response?.data?.detail || 'Could not raise a credit note'),
  })

  const totalOwed = (owed ?? []).reduce((sum, r) => sum + Number(r.outstanding), 0)

  return (
    <Box>
      <PageHeader
        title="Invoices"
        subtitle="Raised from what each customer actually runs"
        action={
          <Button variant="contained" size="small" startIcon={<AddIcon />}
                  onClick={() => { setError(''); setRaiseOpen(true) }}>
            Raise an invoice
          </Button>
        }
      />

      {owed && owed.length > 0 && (
        <Alert severity={totalOwed > 0 ? 'warning' : 'success'} sx={{ mb: 2 }}>
          {money(totalOwed)} outstanding across {owed.length}{' '}
          {owed.length === 1 ? 'customer' : 'customers'}
          {owed.some((r) => r.overdue > 0) &&
            ` — ${owed.reduce((n, r) => n + r.overdue, 0)} past due`}
        </Alert>
      )}

      <ToggleButtonGroup size="small" exclusive value={filter} sx={{ mb: 2 }}
                         onChange={(_, v) => v && setFilter(v)}>
        {['all', 'draft', 'issued', 'overdue', 'paid'].map((s) => (
          <ToggleButton key={s} value={s}>{s}</ToggleButton>
        ))}
      </ToggleButtonGroup>

      <GlassCard sx={{ p: 0 }}>
        {isLoading ? <Skeleton variant="rectangular" height={200} /> : !invoices?.length ? (
          <Typography variant="body2" color="text.secondary" sx={{ p: 2 }}>
            No invoices {filter === 'all' ? 'yet' : `in ${filter}`}.
          </Typography>
        ) : (
          <TableContainer sx={{ overflowX: 'auto' }}>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>Invoice</TableCell>
                  <TableCell>Customer</TableCell>
                  <TableCell>Issued</TableCell>
                  <TableCell>Due</TableCell>
                  <TableCell align="right">Total</TableCell>
                  <TableCell align="right">Paid</TableCell>
                  <TableCell align="right">Status</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {invoices.map((i) => (
                  <TableRow key={i.id} hover sx={{ cursor: 'pointer' }}
                            onClick={() => { setError(''); setOpenId(i.id) }}>
                    <TableCell>
                      <Typography variant="body2" sx={{ fontFamily: 'monospace' }}>
                        {i.invoice_number}
                      </Typography>
                    </TableCell>
                    <TableCell>{i.tenant_name}</TableCell>
                    <TableCell><Typography variant="caption">
                      {when(i.issued_at)}</Typography></TableCell>
                    <TableCell><Typography variant="caption">
                      {when(i.due_date)}</Typography></TableCell>
                    <TableCell align="right">
                      {money(i.total_amount, i.currency)}
                    </TableCell>
                    <TableCell align="right">
                      {money(i.amount_paid, i.currency)}
                    </TableCell>
                    <TableCell align="right"><StatusChip status={i.status} /></TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
        )}
      </GlassCard>

      {/* ── Raise ───────────────────────────────────────────────────────── */}
      <Dialog open={raiseOpen} onClose={() => setRaiseOpen(false)} maxWidth="sm" fullWidth>
        <DialogTitle>Raise an invoice</DialogTitle>
        <DialogContent dividers>
          <Stack spacing={2} sx={{ mt: 1 }}>
            {error && <Alert severity="warning">{error}</Alert>}
            <FormControl size="small" fullWidth>
              <InputLabel>Customer</InputLabel>
              <Select value={tenantId} label="Customer"
                      onChange={(e) => setTenantId(e.target.value)}>
                {(tenants ?? []).map((t) => (
                  <MenuItem key={t.id} value={t.id}>{t.name}</MenuItem>
                ))}
              </Select>
            </FormControl>
            <Stack direction="row" spacing={2}>
              <TextField label="Tax rate" size="small" value={taxRate} type="number"
                         onChange={(e) => setTaxRate(e.target.value)}
                         helperText="0.09 for 9% GST" />
              <TextField label="Discount" size="small" value={discount} type="number"
                         onChange={(e) => setDiscount(e.target.value)} />
            </Stack>

            {quote && (
              <>
                <Divider />
                <Typography variant="subtitle2">
                  {quote.plan} · {quote.usage.cameras} cameras, {quote.usage.sites} sites,{' '}
                  {quote.usage.users} users
                </Typography>
                <Table size="small">
                  <TableBody>
                    {quote.lines.map((l, i) => (
                      <TableRow key={i}>
                        <TableCell sx={{ border: 0, py: 0.25 }}>
                          <Typography variant="caption">{l.description}</Typography>
                        </TableCell>
                        <TableCell align="right" sx={{ border: 0, py: 0.25 }}>
                          <Typography variant="caption">{money(l.line_total)}</Typography>
                        </TableCell>
                      </TableRow>
                    ))}
                    <TableRow>
                      <TableCell sx={{ border: 0, pt: 1 }}>
                        <Typography variant="body2" sx={{ fontWeight: 700 }}>Total</Typography>
                      </TableCell>
                      <TableCell align="right" sx={{ border: 0, pt: 1 }}>
                        <Typography variant="body2" sx={{ fontWeight: 700 }}>
                          {money(quote.total_amount)}
                        </Typography>
                      </TableCell>
                    </TableRow>
                  </TableBody>
                </Table>
                <Alert severity="info">
                  Raised as a draft. Nothing reaches the customer until it is issued.
                </Alert>
              </>
            )}
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setRaiseOpen(false)}>Cancel</Button>
          <Button variant="contained" disabled={!tenantId || raise.isPending}
                  onClick={() => raise.mutate()}>
            Raise draft
          </Button>
        </DialogActions>
      </Dialog>

      {/* ── One invoice ─────────────────────────────────────────────────── */}
      <Dialog open={Boolean(openId)} onClose={() => setOpenId(null)}
              maxWidth="md" fullWidth>
        <DialogTitle>
          <Stack direction="row" spacing={1.5} alignItems="center">
            <span>{detail?.invoice_number ?? 'Invoice'}</span>
            {detail && <StatusChip status={detail.status} />}
          </Stack>
        </DialogTitle>
        <DialogContent dividers>
          {!detail ? <Skeleton height={260} /> : (
            <Stack spacing={2}>
              {error && <Alert severity="warning">{error}</Alert>}
              <Stack direction="row" spacing={3} sx={{ flexWrap: 'wrap', gap: 1 }}>
                <Box>
                  <Typography variant="caption" color="text.secondary">Customer</Typography>
                  <Typography variant="body2">{detail.tenant_name}</Typography>
                </Box>
                <Box>
                  <Typography variant="caption" color="text.secondary">Due</Typography>
                  <Typography variant="body2">{when(detail.due_date)}</Typography>
                </Box>
                <Box>
                  <Typography variant="caption" color="text.secondary">Outstanding</Typography>
                  <Typography variant="body2" sx={{ fontWeight: 700 }}>
                    {money(detail.amount_outstanding, detail.currency)}
                  </Typography>
                </Box>
              </Stack>

              <TableContainer>
                <Table size="small">
                  <TableHead>
                    <TableRow>
                      <TableCell>Line</TableCell>
                      <TableCell align="right">Qty</TableCell>
                      <TableCell align="right">Unit</TableCell>
                      <TableCell align="right">Total</TableCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {detail.items.map((it) => (
                      <TableRow key={it.id}>
                        <TableCell>{it.description}</TableCell>
                        <TableCell align="right">{Number(it.quantity)}</TableCell>
                        <TableCell align="right">
                          {money(it.unit_price, detail.currency)}
                        </TableCell>
                        <TableCell align="right">
                          {money(it.line_total, detail.currency)}
                        </TableCell>
                      </TableRow>
                    ))}
                    <TableRow>
                      <TableCell colSpan={3} align="right">Subtotal</TableCell>
                      <TableCell align="right">
                        {money(detail.subtotal, detail.currency)}
                      </TableCell>
                    </TableRow>
                    {Number(detail.discount_amount) > 0 && (
                      <TableRow>
                        <TableCell colSpan={3} align="right">Discount</TableCell>
                        <TableCell align="right">
                          −{money(detail.discount_amount, detail.currency)}
                        </TableCell>
                      </TableRow>
                    )}
                    <TableRow>
                      <TableCell colSpan={3} align="right">
                        Tax ({(Number(detail.tax_rate) * 100).toFixed(1)}%)
                      </TableCell>
                      <TableCell align="right">
                        {money(detail.tax_amount, detail.currency)}
                      </TableCell>
                    </TableRow>
                    <TableRow>
                      <TableCell colSpan={3} align="right">
                        <Typography variant="body2" sx={{ fontWeight: 700 }}>Total</Typography>
                      </TableCell>
                      <TableCell align="right">
                        <Typography variant="body2" sx={{ fontWeight: 700 }}>
                          {money(detail.total_amount, detail.currency)}
                        </Typography>
                      </TableCell>
                    </TableRow>
                  </TableBody>
                </Table>
              </TableContainer>

              {detail.payments.length > 0 && (
                <Box>
                  <Typography variant="subtitle2" sx={{ mb: 0.5 }}>Payments</Typography>
                  {detail.payments.map((p) => (
                    <Stack key={p.id} direction="row"
                           sx={{ justifyContent: 'space-between', py: 0.3 }}>
                      <Typography variant="caption" color="text.secondary">
                        {new Date(p.received_at).toLocaleString()} · {p.method}
                        {p.reference ? ` · ${p.reference}` : ''}
                      </Typography>
                      <Typography variant="caption"
                                  sx={{ fontWeight: 600,
                                        color: Number(p.amount) < 0 ? '#FFB020' : undefined }}>
                        {money(p.amount, detail.currency)}
                      </Typography>
                    </Stack>
                  ))}
                </Box>
              )}

              {['issued', 'overdue'].includes(detail.status) && (
                <>
                  <Divider />
                  <Typography variant="subtitle2">Record a payment</Typography>
                  <Stack direction="row" spacing={1.5} sx={{ flexWrap: 'wrap', gap: 1.5 }}>
                    <TextField label="Amount" size="small" type="number"
                               value={payAmount}
                               onChange={(e) => setPayAmount(e.target.value)}
                               helperText="Negative for a refund" />
                    <FormControl size="small" sx={{ minWidth: 150 }}>
                      <InputLabel>Method</InputLabel>
                      <Select value={payMethod} label="Method"
                              onChange={(e) => setPayMethod(e.target.value)}>
                        {METHODS.map((m) => (
                          <MenuItem key={m} value={m}>{m.replace('_', ' ')}</MenuItem>
                        ))}
                      </Select>
                    </FormControl>
                    <TextField label="Reference" size="small" value={payReference}
                               onChange={(e) => setPayReference(e.target.value)} />
                    <Button variant="outlined" disabled={!payAmount || pay.isPending}
                            onClick={() => pay.mutate()}>
                      Record
                    </Button>
                  </Stack>
                </>
              )}

              {detail.status !== 'draft' && (
                <Alert severity="info">
                  This invoice has been issued and cannot be edited. Correcting it
                  means a credit note, so the correction is visible rather than the
                  original quietly changing.
                </Alert>
              )}
            </Stack>
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setOpenId(null)}>Close</Button>
          {detail?.status === 'draft' && (
            <>
              <Button color="error" disabled={changeStatus.isPending}
                      onClick={() => changeStatus.mutate({ id: detail.id,
                                                           status: 'cancelled' })}>
                Cancel invoice
              </Button>
              <Button variant="contained" disabled={changeStatus.isPending}
                      onClick={() => changeStatus.mutate({ id: detail.id,
                                                           status: 'issued' })}>
                Issue
              </Button>
            </>
          )}
          {detail && ['issued', 'overdue', 'paid'].includes(detail.status) && (
            <Tooltip title="Reverses this invoice. Both documents stay in the ledger.">
              <Button color="warning" disabled={credit.isPending}
                      onClick={() => credit.mutate()}>
                Credit note
              </Button>
            </Tooltip>
          )}
        </DialogActions>
      </Dialog>
    </Box>
  )
}
