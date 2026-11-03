/**
 * The CPF a salary carries, shown while an employee is created or edited (3 November 2026; Phase 1 of closing the
 * known limits). The figures come from the server, from the same rules payroll uses (services/cpf.py: CPF Board's
 * table for the month, the age band, the PR year, the wage bands and CPF's rounding), so this card and the payslip
 * cannot disagree.
 */
import { Alert, Box, Typography } from '@mui/material'
import Stack from '@/components/common/Stack'
import { useQuery } from '@tanstack/react-query'

import { getCpfPreview, type CpfPreviewInput } from '@/api/payroll'
import { errorText } from '@/components/states'
import { missingForCpf } from './cpfResidency'

const money = (n: number | undefined) =>
  n === undefined ? '—' : `$${n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`

export function CpfPreviewCard({ workPassType, dateOfBirth, prSince, cpfRateType, monthlySalary }: {
  workPassType: string
  dateOfBirth: string
  prSince: string
  cpfRateType: string
  monthlySalary: string
}) {
  const waiting = missingForCpf({ workPassType, dateOfBirth, prSince, monthlySalary })
  const input: CpfPreviewInput | null = waiting ? null : {
    work_pass_type: workPassType as CpfPreviewInput['work_pass_type'],
    date_of_birth: dateOfBirth || undefined,
    pr_since: prSince || undefined,
    cpf_rate_type: (cpfRateType || undefined) as CpfPreviewInput['cpf_rate_type'],
    monthly_salary: Number(monthlySalary) || 0,
  }
  const { data, isFetching, isError, error } = useQuery({
    queryKey: ['cpf-preview', input],
    queryFn: () => getCpfPreview(input!),
    enabled: input !== null,
    staleTime: 60_000,
  })

  if (waiting) {
    return <Typography variant="caption" color="text.secondary">{waiting}</Typography>
  }
  if (isError) return <Alert severity="warning">{`The CPF could not be worked out. ${errorText(error)}`}</Alert>
  if (!data) {
    return <Typography variant="caption" color="text.secondary">{isFetching ? 'Working out the CPF…' : ''}</Typography>
  }
  if (!data.eligible) return <Alert severity="info">{data.note ?? 'No CPF for foreign employees.'}</Alert>
  if (!data.scheme) return <Alert severity="warning">{data.note ?? 'CPF cannot be worked out yet.'}</Alert>

  return (
    <Box
      aria-label="CPF for this salary"
      sx={{ p: 1.5, borderRadius: '10px', border: 1, borderColor: 'divider', bgcolor: 'action.hover' }}
    >
      <Typography variant="subtitle2" sx={{ fontWeight: 700, mb: 0.75 }}>CPF for this salary</Typography>
      <Stack direction="row" spacing={2} sx={{ flexWrap: 'wrap', rowGap: 0.75 }}>
        <Figure label={`Employee CPF ${data.employee_percent}`} value={money(data.employee_cpf)} />
        <Figure label={`Employer CPF ${data.employer_percent}`} value={money(data.employer_cpf)} />
        <Figure label={`Total ${data.total_percent}`} value={money(data.total_cpf)} />
        <Figure label="Take-home before other deductions" value={money(data.take_home)} strong />
      </Stack>
      <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 0.75 }}>
        {`CPF rates from ${data.rates_from ? new Date(`${data.rates_from}T00:00:00`).toLocaleDateString() : '—'}`}
        {` · ${data.scheme_label} · ${data.age_band_label}`}
        {` · Ordinary Wage ceiling ${money(data.ow_ceiling)} a month`}
      </Typography>
      {data.below_full_rates && (
        <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
          Up to $750 a month the employee&apos;s share is phased in, so it is less than the full percentage.
        </Typography>
      )}
    </Box>
  )
}

function Figure({ label, value, strong }: { label: string; value: string; strong?: boolean }) {
  return (
    <Box sx={{ minWidth: 120 }}>
      <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>{label}</Typography>
      <Typography variant="body2" sx={{ fontWeight: strong ? 800 : 600 }}>{value}</Typography>
    </Box>
  )
}
