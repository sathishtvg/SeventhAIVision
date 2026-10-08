/**
 * The operations reports: the records the platform already keeps, taken out as
 * a CSV file for a period and a site.
 *
 * The list is the server's: what each report holds, its columns, and whether
 * the reader may have it. A report they may not have is shown with the reason
 * and cannot be asked for. Taking one out is written in the audit log, and the
 * screen says so before it is done.
 */
import { useState } from 'react'
import { Alert, Box, Button, MenuItem, Skeleton, TextField, Typography } from '@mui/material'
import DownloadIcon from '@mui/icons-material/Download'
import { useMutation, useQuery } from '@tanstack/react-query'
import Stack from '@/components/common/Stack'
import { GlassCard } from '@/components/common/GlassCard'
import { getBoardSites } from '@/api/operationsBoard'
import { apiError, listReports, takeReport } from '@/api/operationsReports'
import type { ReportKind } from '@/api/operationsReports'
import { periodLabel, takenLine } from './boardFormat'

const shrunk = { select: { displayEmpty: true }, inputLabel: { shrink: true } }

function ReportRow({ r, siteId, days, maxRows }: { r: ReportKind; siteId: string; days: number; maxRows: number }) {
  const [columns, setColumns] = useState(false)
  const take = useMutation({ mutationFn: () => takeReport(r.key, { site_id: siteId || undefined, days }) })
  return (
    <GlassCard sx={{ p: 2, mb: 1 }} data-testid="report" data-key={r.key}>
      <Stack direction="row" sx={{ gap: 2, alignItems: 'flex-start', flexWrap: 'wrap' }}>
        <Box sx={{ flex: 1, minWidth: 260 }}>
          <Typography variant="subtitle2" sx={{ fontWeight: 700 }}>{r.title}</Typography>
          <Typography variant="body2" color="text.secondary">{r.holds}</Typography>
          <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 0.5 }}>
            {r.periodic ? 'For the period chosen' : 'As things are now — it has no period'} · {r.columns.length} columns ·
            read under {r.needs.join(' and ')}
          </Typography>
          {columns && (
            <Typography variant="caption" sx={{ display: 'block', mt: 0.5 }} data-testid="columns">{r.columns.join(' · ')}</Typography>)}
        </Box>
        <Stack direction="row" sx={{ gap: 1, alignItems: 'center' }}>
          <Button size="small" onClick={() => setColumns((v) => !v)}>{columns ? 'Hide the columns' : 'Show the columns'}</Button>
          <Button size="small" variant="contained" startIcon={<DownloadIcon />} disabled={!r.may || take.isPending}
                  onClick={() => take.mutate()}>Take it out</Button>
        </Stack>
      </Stack>
      {!r.may && r.why_not && <Alert severity="info" sx={{ mt: 1 }}>{r.why_not}</Alert>}
      {take.isSuccess && (
        <Alert severity={take.data.cut ? 'warning' : 'success'} sx={{ mt: 1 }}>{takenLine(take.data, maxRows)}</Alert>)}
      {take.isError && <Alert severity="error" sx={{ mt: 1 }}>{apiError(take.error)}</Alert>}
    </GlassCard>
  )
}

export function ReportsTab() {
  const [siteId, setSiteId] = useState('')
  const [days, setDays] = useState(7)
  const { data, isLoading, error } = useQuery({ queryKey: ['operations-reports'], queryFn: listReports })
  const sites = useQuery({ queryKey: ['board-sites', '', 1], queryFn: () => getBoardSites({ days: 1 }) })
  return (
    <>
      <GlassCard sx={{ p: 2, mb: 2 }}>
        <Stack direction="row" sx={{ gap: 1.5, flexWrap: 'wrap', alignItems: 'center' }}>
          <TextField select size="small" label="Site" value={siteId} sx={{ minWidth: 190 }} slotProps={shrunk}
                     onChange={(e) => setSiteId(e.target.value)}>
            <MenuItem value="">Every site</MenuItem>
            {(sites.data?.sites ?? []).map((s) => <MenuItem key={s.id} value={s.id}>{s.name}</MenuItem>)}
          </TextField>
          <TextField select size="small" label="Period" value={days} sx={{ minWidth: 180 }}
                     onChange={(e) => setDays(Number(e.target.value))}>
            {(data?.periods ?? [7]).map((d) => <MenuItem key={d} value={d}>{periodLabel(d)}</MenuItem>)}
          </TextField>
          {data && (
            <Typography variant="caption" color="text.secondary" sx={{ flex: 1, minWidth: 260 }} data-testid="reports-note">
              {data.note} Times are as in {data.timezone}. A file holds at most {data.max_rows.toLocaleString('en')} records,
              and says so when it was cut.
            </Typography>)}
        </Stack>
      </GlassCard>
      {!!error && <Alert severity="error" sx={{ mb: 2 }}>{apiError(error)}</Alert>}
      {isLoading && <Skeleton height={240} />}
      {(data?.reports ?? []).map((r) => <ReportRow key={r.key} r={r} siteId={siteId} days={days} maxRows={data!.max_rows} />)}
    </>
  )
}
