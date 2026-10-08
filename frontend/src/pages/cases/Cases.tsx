/**
 * Security cases: the cases the reader may read, opening one, and working one.
 *
 * A case is opened from an incident, an investigation or an evidence package —
 * or from nothing — and refers to those records without copying them. It is
 * worked by the people on it and closed by two.
 */
import { useState } from 'react'
import {
  Alert, Box, Button, Chip, FormControlLabel, MenuItem, Skeleton, Switch, Table, TableBody, TableCell, TableContainer,
  TableHead, TableRow, TextField, Typography,
} from '@mui/material'
import AddIcon from '@mui/icons-material/Add'
import { keepPreviousData, useQuery, useQueryClient } from '@tanstack/react-query'
import Stack from '@/components/common/Stack'
import { GlassCard } from '@/components/common/GlassCard'
import { PageHeader } from '@/components/common/PageHeader'
import { getSites } from '@/api/sites'
import { apiError, getCaseOptions, listCases } from '@/api/cases'
import type { CaseFile, Status } from '@/api/cases'
import { CaseDialog, OpenCaseDialog } from '@/components/cases/CaseDialogs'
import { PRIORITY_LABEL, STATUS_COLOUR, fmt } from '@/components/cases/caseFormat'

const shrunk = { select: { displayEmpty: true }, inputLabel: { shrink: true } }

export default function Cases() {
  const qc = useQueryClient()
  const [status, setStatus] = useState<Status | ''>('')
  const [mine, setMine] = useState(false)
  const [open, setOpen] = useState<string | null>(null)
  const [opening, setOpening] = useState(false)
  const { data, isLoading, error } = useQuery({
    queryKey: ['cases', status, mine], queryFn: () => listCases({ status: status || undefined, mine: mine || undefined }),
    placeholderData: keepPreviousData,
  })
  const { data: sites } = useQuery({ queryKey: ['sites'], queryFn: () => getSites(true) })
  const options = useQuery({ queryKey: ['case-options'], queryFn: getCaseOptions, enabled: opening })
  const again = () => qc.invalidateQueries({ queryKey: ['cases'] })
  const opened = (c: CaseFile) => { setOpening(false); again(); setOpen(c.id) }
  return (
    <Box sx={{ p: 3 }}>
      <PageHeader title="Cases" subtitle="A case refers to what it is about, is worked by the people on it, and is closed by two" />
      <GlassCard sx={{ p: 2, mb: 2 }}>
        <Stack direction="row" sx={{ gap: 1.5, flexWrap: 'wrap', alignItems: 'center' }}>
          <TextField select size="small" label="Status" value={status} sx={{ minWidth: 230 }} slotProps={shrunk}
                     onChange={(e) => setStatus(e.target.value as Status | '')}>
            <MenuItem value="">Every status</MenuItem>
            <MenuItem value="OPEN">Open</MenuItem>
            <MenuItem value="AWAITING_APPROVAL">Waiting for approval to close</MenuItem>
            <MenuItem value="CLOSED">Closed</MenuItem>
          </TextField>
          <FormControlLabel label="Mine" control={<Switch size="small" checked={mine} onChange={(e) => setMine(e.target.checked)} />} />
          <Typography variant="caption" color="text.secondary" sx={{ flex: 1, minWidth: 200 }}>
            “Mine” is the cases you lead, investigate, or have a task on.
          </Typography>
          {data?.can_open && (
            <Button variant="contained" startIcon={<AddIcon />} onClick={() => setOpening(true)}>Open a case</Button>)}
        </Stack>
      </GlassCard>
      {!!error && <Alert severity="error" sx={{ mb: 2 }}>{apiError(error)}</Alert>}
      {isLoading && <Skeleton height={240} />}
      {data && (
        <GlassCard sx={{ p: 0 }}>
          <TableContainer>
            <Table size="small" data-testid="cases-table" aria-label="Cases, the newest first">
              <TableHead>
                <TableRow>
                  <TableCell>Case</TableCell><TableCell>Status</TableCell><TableCell>Kind</TableCell><TableCell>Site</TableCell>
                  <TableCell>Lead</TableCell><TableCell>Opened</TableCell><TableCell align="right">Tasks to do</TableCell>
                  <TableCell align="right">Linked records</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {data.items.map((c) => (
                  <TableRow key={c.id} hover data-testid="case-row" sx={{ cursor: 'pointer' }} onClick={() => setOpen(c.id)}>
                    <TableCell sx={{ minWidth: 220 }}>
                      <Typography variant="body2" sx={{ fontWeight: 600 }}>{c.case_number} — {c.title}</Typography>
                      <Typography variant="caption" color="text.secondary">{PRIORITY_LABEL[c.priority]}</Typography>
                    </TableCell>
                    <TableCell><Chip size="small" color={STATUS_COLOUR[c.status]} label={c.status_label} /></TableCell>
                    <TableCell>{c.category_label}</TableCell>
                    <TableCell>{c.site_name ?? 'No one site'}</TableCell>
                    <TableCell>{c.lead_name ?? 'Nobody'}</TableCell>
                    <TableCell>{fmt(c.opened_at)}</TableCell>
                    <TableCell align="right">{c.tasks_open}</TableCell>
                    <TableCell align="right">{c.links}</TableCell>
                  </TableRow>))}
                {!data.items.length && (
                  <TableRow><TableCell colSpan={8}>{mine || status ? 'No case matches.' : 'No case has been opened yet.'}</TableCell></TableRow>)}
              </TableBody>
            </Table>
          </TableContainer>
        </GlassCard>)}
      <OpenCaseDialog open={opening} onClose={() => setOpening(false)} onOpened={opened} sites={sites ?? []} options={options.data} />
      <CaseDialog id={open} onClose={() => setOpen(null)} onChanged={again} />
    </Box>
  )
}
