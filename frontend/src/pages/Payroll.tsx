/**
 * Payroll / CPF / IR8A (ShiftSecure Phase 5) — monthly pay runs computed
 * from shifts.actual_start/actual_end + overtime_minutes, Singapore CPF
 * contribution (standard full rates only — see services/payroll.py for
 * documented v1 limitations), and an annual IR8A summary for tax filing.
 */
import { useMemo, useState } from 'react'
import {
  Box, Typography, Chip, Select, MenuItem, FormControl, InputLabel, Skeleton,
  Stack, Button, Divider, Dialog, DialogTitle, DialogContent, DialogActions,
  TextField, IconButton, Tabs, Tab, Table, TableBody, TableCell, TableContainer,
  TableHead, TableRow,
} from '@mui/material'
import PaymentsIcon from '@mui/icons-material/Payments'
import AccountBalanceIcon from '@mui/icons-material/AccountBalance'
import GroupIcon from '@mui/icons-material/Group'
import WarningAmberIcon from '@mui/icons-material/WarningAmber'
import AddIcon from '@mui/icons-material/Add'
import ArrowBackIcon from '@mui/icons-material/ArrowBack'
import DownloadIcon from '@mui/icons-material/Download'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  createPayrollRun, listPayrollRuns, getPayrollRun, finalizePayrollRun,
  payslipPdfUrl, getIr8aSummary, ir8aPdfUrl,
} from '@/api/payroll'
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

const STATUS_META: Record<'draft' | 'finalized', { label: string; color: string }> = {
  draft: { label: 'Draft', color: '#FF9800' },
  finalized: { label: 'Finalized', color: '#00E396' },
}

function RunStatusChip({ status }: { status: 'draft' | 'finalized' }) {
  const meta = STATUS_META[status]
  const rgb = hexToRgb(meta.color)
  return (
    <Chip label={meta.label} size="small" sx={{
      color: meta.color, backgroundColor: `rgba(${rgb},0.14)`, border: `1px solid rgba(${rgb},0.3)`, fontWeight: 700,
    }} />
  )
}

function NewRunDialog({ open, onClose, onCreated }: { open: boolean; onClose: () => void; onCreated: (runId: string) => void }) {
  const qc = useQueryClient()
  const [periodStart, setPeriodStart] = useState('')
  const [periodEnd, setPeriodEnd] = useState('')

  const { mutate: submit, isPending } = useMutation({
    mutationFn: () => createPayrollRun({ period_start: periodStart, period_end: periodEnd }),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ['payroll-runs'] })
      setPeriodStart(''); setPeriodEnd('')
      onCreated(data.id)
    },
  })

  return (
    <Dialog open={open} onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle>New Payroll Run</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ mt: 1 }}>
          <TextField
            size="small" label="Period Start" type="date" fullWidth
            InputLabelProps={{ shrink: true }} value={periodStart} onChange={(e) => setPeriodStart(e.target.value)}
          />
          <TextField
            size="small" label="Period End" type="date" fullWidth
            InputLabelProps={{ shrink: true }} value={periodEnd} onChange={(e) => setPeriodEnd(e.target.value)}
          />
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" disabled={!periodStart || !periodEnd || isPending} onClick={() => submit()}>
          Generate
        </Button>
      </DialogActions>
    </Dialog>
  )
}

function RunDetail({ runId, onBack }: { runId: string; onBack: () => void }) {
  const qc = useQueryClient()
  const canManage = usePermission('payroll:manage')
  const accessToken = useAuthStore((s) => s.accessToken)
  const { data: run, isLoading } = useQuery({
    queryKey: ['payroll-run', runId],
    queryFn: () => getPayrollRun(runId),
  })

  const { mutate: finalize, isPending: finalizing } = useMutation({
    mutationFn: () => finalizePayrollRun(runId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['payroll-run', runId] })
      qc.invalidateQueries({ queryKey: ['payroll-runs'] })
    },
  })

  const kpis = useMemo(() => {
    const payslips = run?.payslips ?? []
    return {
      grossPayroll: Math.round(payslips.reduce((s, p) => s + p.gross_pay, 0)),
      totalCpf: Math.round(payslips.reduce((s, p) => s + p.cpf_employee + p.cpf_employer, 0)),
      guardsPaid: payslips.length,
      guardsSkipped: run?.warnings?.length ?? 0,
    }
  }, [run])

  if (isLoading || !run) return <Box sx={{ p: 2 }}><Skeleton height={200} /></Box>

  return (
    <Box sx={{ p: 2 }}>
      <Stack direction="row" spacing={1.5} alignItems="center" sx={{ mb: 2 }}>
        <IconButton size="small" onClick={onBack}><ArrowBackIcon fontSize="small" /></IconButton>
        <Typography variant="subtitle1" fontWeight={700}>
          {run.period_start} – {run.period_end}
        </Typography>
        <RunStatusChip status={run.status} />
        <Box sx={{ flexGrow: 1 }} />
        {canManage && run.status === 'draft' && (
          <Button variant="contained" size="small" disabled={finalizing} onClick={() => finalize()}>
            Finalize Run
          </Button>
        )}
      </Stack>

      <Box sx={{ display: 'flex', gap: 2, mb: 2.5, flexWrap: 'wrap' }}>
        {[
          { label: "Run's Gross Payroll", value: kpis.grossPayroll, icon: <PaymentsIcon />, color: '#00E396', isMoney: true },
          { label: 'Total CPF', value: kpis.totalCpf, icon: <AccountBalanceIcon />, color: '#6C63FF', isMoney: true },
          { label: 'Guards Paid', value: kpis.guardsPaid, icon: <GroupIcon />, color: '#00D9C0' },
          { label: 'Guards Skipped', value: kpis.guardsSkipped, icon: <WarningAmberIcon />, color: '#FF9800' },
        ].map((kpi, i) => (
          <Box key={kpi.label} sx={{ flex: '1 1 160px', minWidth: 0, ...fadeUpSx(i) }}>
            <KpiCard {...kpi} />
          </Box>
        ))}
      </Box>

      {run.warnings && run.warnings.length > 0 && (
        <Box sx={{ p: 1.5, mb: 2, borderRadius: '8px', backgroundColor: 'rgba(255,152,0,0.1)', border: '1px solid rgba(255,152,0,0.3)' }}>
          <Typography variant="caption" sx={{ color: '#FF9800', fontWeight: 700, display: 'block', mb: 0.5 }}>
            Skipped guards (no rate configured)
          </Typography>
          {run.warnings.map((w, i) => (
            <Typography key={i} variant="caption" color="text.secondary" sx={{ display: 'block' }}>{w}</Typography>
          ))}
        </Box>
      )}

      <TableContainer>
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell>Guard</TableCell>
              <TableCell align="right">Reg. Hrs</TableCell>
              <TableCell align="right">OT Hrs</TableCell>
              <TableCell align="right">Days</TableCell>
              <TableCell align="right">Gross</TableCell>
              <TableCell align="right">CPF (Emp.)</TableCell>
              <TableCell align="right">CPF (Empl.)</TableCell>
              <TableCell align="right">Net</TableCell>
              <TableCell align="right">Payslip</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {run.payslips.map((p) => (
              <TableRow key={p.id}>
                <TableCell>{p.guard_name}</TableCell>
                <TableCell align="right">{p.regular_hours.toFixed(1)}</TableCell>
                <TableCell align="right">{p.overtime_hours.toFixed(1)}</TableCell>
                <TableCell align="right">{p.days_worked.toFixed(1)}</TableCell>
                <TableCell align="right">${p.gross_pay.toFixed(2)}</TableCell>
                <TableCell align="right">${p.cpf_employee.toFixed(2)}</TableCell>
                <TableCell align="right">${p.cpf_employer.toFixed(2)}</TableCell>
                <TableCell align="right" sx={{ fontWeight: 700 }}>${p.net_pay.toFixed(2)}</TableCell>
                <TableCell align="right">
                  <IconButton
                    size="small"
                    component="a"
                    href={payslipPdfUrl(p.id, accessToken) ?? undefined}
                    target="_blank" rel="noopener"
                  >
                    <DownloadIcon fontSize="small" />
                  </IconButton>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </TableContainer>
    </Box>
  )
}

function RunsTab() {
  const [runOpen, setRunOpen] = useState(false)
  const [selectedRun, setSelectedRun] = useState<string | null>(null)
  const { data: runs = [], isLoading } = useQuery({ queryKey: ['payroll-runs'], queryFn: () => listPayrollRuns() })

  if (selectedRun) {
    return <RunDetail runId={selectedRun} onBack={() => setSelectedRun(null)} />
  }

  return (
    <Box sx={{ p: 2 }}>
      <Stack direction="row" justifyContent="flex-end" sx={{ mb: 2 }}>
        <PermissionGuard permission="payroll:manage">
          <Button variant="contained" size="small" startIcon={<AddIcon />} onClick={() => setRunOpen(true)}>
            New Payroll Run
          </Button>
        </PermissionGuard>
      </Stack>
      {isLoading ? (
        <Skeleton height={80} />
      ) : runs.length === 0 ? (
        <Typography color="text.secondary" sx={{ textAlign: 'center', py: 3 }}>No payroll runs yet.</Typography>
      ) : (
        <Stack divider={<Divider />} spacing={1.5}>
          {runs.map((r) => (
            <Box
              key={r.id}
              onClick={() => setSelectedRun(r.id)}
              sx={{
                display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 2, py: 0.5,
                cursor: 'pointer', borderRadius: '8px', '&:hover': { backgroundColor: 'rgba(255,255,255,0.04)' },
              }}
            >
              <Typography variant="body2" fontWeight={600}>{r.period_start} – {r.period_end}</Typography>
              <RunStatusChip status={r.status} />
            </Box>
          ))}
        </Stack>
      )}
      <NewRunDialog open={runOpen} onClose={() => setRunOpen(false)} onCreated={(id) => { setRunOpen(false); setSelectedRun(id) }} />
    </Box>
  )
}

function Ir8aTab() {
  const accessToken = useAuthStore((s) => s.accessToken)
  const [year, setYear] = useState(new Date().getFullYear())
  const { data: rows = [], isLoading } = useQuery({
    queryKey: ['ir8a', year],
    queryFn: () => getIr8aSummary(year),
  })

  return (
    <Box sx={{ p: 2 }}>
      <Stack direction="row" spacing={1.5} alignItems="center" sx={{ mb: 2 }}>
        <FormControl size="small" sx={{ minWidth: 120 }}>
          <InputLabel>Year</InputLabel>
          <Select value={year} label="Year" onChange={(e) => setYear(Number(e.target.value))}>
            {[year - 1, year, year + 1].map((y) => <MenuItem key={y} value={y}>{y}</MenuItem>)}
          </Select>
        </FormControl>
        <Box sx={{ flexGrow: 1 }} />
        <Button
          variant="outlined" size="small" startIcon={<DownloadIcon />}
          component="a" href={ir8aPdfUrl(year, accessToken) ?? undefined} target="_blank" rel="noopener"
        >
          Download IR8A PDF
        </Button>
      </Stack>
      {isLoading ? (
        <Skeleton height={80} />
      ) : rows.length === 0 ? (
        <Typography color="text.secondary" sx={{ textAlign: 'center', py: 3 }}>
          No finalized payroll runs found for {year}.
        </Typography>
      ) : (
        <TableContainer>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Name</TableCell>
                <TableCell>NRIC/FIN</TableCell>
                <TableCell align="right">Annual Gross</TableCell>
                <TableCell align="right">Annual Employer CPF</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {rows.map((r) => (
                <TableRow key={r.guard_user_id}>
                  <TableCell>{r.full_name ?? '—'}</TableCell>
                  <TableCell>{r.nric_fin ?? '—'}</TableCell>
                  <TableCell align="right">${r.annual_gross.toFixed(2)}</TableCell>
                  <TableCell align="right">${r.annual_employer_cpf.toFixed(2)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      )}
    </Box>
  )
}

export function PayrollPage() {
  const [tab, setTab] = useState(0)
  const canManage = usePermission('payroll:manage')

  return (
    <Box>
      <PageHeader title="Payroll" subtitle="Pay runs, CPF contributions, and annual IR8A reporting" />

      <GlassCard>
        <Box sx={{ borderBottom: 1, borderColor: 'rgba(255,255,255,0.1)' }}>
          <Tabs value={tab} onChange={(_, v) => setTab(v)}>
            <Tab label="Runs" />
            {canManage && <Tab label="IR8A" />}
          </Tabs>
        </Box>
        {tab === 0 && <RunsTab />}
        {tab === 1 && canManage && (
          <PermissionGuard permission="payroll:manage">
            <Ir8aTab />
          </PermissionGuard>
        )}
      </GlassCard>
    </Box>
  )
}

export default PayrollPage
