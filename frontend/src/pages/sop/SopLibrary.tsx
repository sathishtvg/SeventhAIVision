/**
 * The SOP library: the procedures in force, the ones being written, and
 * finding the passage on something.
 *
 * "Ask" returns passages of approved procedures exactly as they were approved,
 * each with the procedure, the version and who approved it. It is not an
 * answer and the screen never presents it as one: nothing is composed, and
 * when no procedure uses the words asked it says so.
 *
 * Post orders (the existing SOP screen) are as they were; this is beside them.
 */
import { useState } from 'react'
import {
  Alert, Box, Button, Chip, MenuItem, Skeleton, Table, TableBody, TableCell, TableContainer, TableHead, TableRow,
  TextField, Typography,
} from '@mui/material'
import AddIcon from '@mui/icons-material/Add'
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import Stack from '@/components/common/Stack'
import { GlassCard } from '@/components/common/GlassCard'
import { PageHeader } from '@/components/common/PageHeader'
import { apiError, askLibrary, getLibrary } from '@/api/sop'
import type { DocumentState } from '@/api/sop'
import { ProcedureDialog, WriteDialog } from '@/components/sop/SopDialogs'
import { STATE_COLOUR, STATE_LABEL, label, matched, source, standing } from '@/components/sop/sopFormat'
import { ErrorState } from '@/components/states'

type Shown = DocumentState | 'AWAITING' | ''
const shrunk = { select: { displayEmpty: true }, inputLabel: { shrink: true } }

export default function SopLibrary() {
  const qc = useQueryClient()
  const [question, setQuestion] = useState('')
  const [typed, setTyped] = useState('')
  const [q, setQ] = useState('')
  const [category, setCategory] = useState('')
  const [state, setState] = useState<Shown>('')
  const [writing, setWriting] = useState(false)
  const [openId, setOpenId] = useState<string | null>(null)
  const { data, isLoading, error, refetch: refetchData } = useQuery({
    queryKey: ['sop-library', q, category, state],
    queryFn: () => getLibrary({ q: q || undefined, category: category || undefined, state: state || undefined }),
    // What was there stays on screen while a narrower list is fetched, so the filters do not flicker away.
    placeholderData: keepPreviousData,
  })
  const ask = useMutation({ mutationFn: () => askLibrary(question.trim()) })
  const again = () => qc.invalidateQueries({ queryKey: ['sop-library'] })
  const items = data?.items ?? []
  return (
    <Box sx={{ p: 3 }}>
      <PageHeader title="SOP Library"
                  subtitle="Procedures in versions, approved before they are in force — and the passage on something, in the procedure's own words"
                  action={data?.can_write ? (
                    <Button variant="contained" startIcon={<AddIcon />} onClick={() => setWriting(true)}>
                      Write a procedure</Button>) : undefined} />
      <GlassCard sx={{ p: 2, mb: 2 }}>
        <Typography variant="subtitle1" sx={{ fontWeight: 700, mb: 1 }}>Find the procedure on something</Typography>
        <Stack direction="row" sx={{ gap: 1.5, flexWrap: 'wrap', alignItems: 'flex-start' }} component="form"
               onSubmit={(e: React.FormEvent) => { e.preventDefault(); if (question.trim().length >= 2) ask.mutate() }}>
          <TextField size="small" label="What do you want the procedure for?" value={question} sx={{ flex: 1, minWidth: 280 }}
                     placeholder="fire alarm in the warehouse" onChange={(e) => setQuestion(e.target.value)}
                     slotProps={{ htmlInput: { maxLength: 300 } }} />
          <Button type="submit" variant="contained" disabled={question.trim().length < 2 || ask.isPending}>Find it</Button>
        </Stack>
        {ask.isError && <Alert severity="error" sx={{ mt: 1.5 }}>{apiError(ask.error)}</Alert>}
        {ask.data && (
          <Box sx={{ mt: 1.5 }} data-testid="asked">
            <Alert severity="info" sx={{ mb: 1.5 }}>{ask.data.note}</Alert>
            {ask.data.nothing && <Alert severity="warning">{ask.data.nothing}</Alert>}
            {ask.data.words.length > 0 && (
              <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1 }}>
                Looked for: {ask.data.words.join(', ')}</Typography>)}
            {ask.data.passages.map((p) => (
              <Box key={p.id} data-testid="passage" sx={{ p: 1.5, mb: 1, border: 1, borderColor: 'divider', borderRadius: 1.5 }}>
                {p.heading && <Typography variant="subtitle2" sx={{ fontWeight: 700 }}>{p.heading}</Typography>}
                <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap' }}>{p.text}</Typography>
                <Stack direction="row" sx={{ gap: 1, alignItems: 'center', flexWrap: 'wrap', mt: 0.75 }}>
                  <Typography variant="caption" color="text.secondary">{source(p)}</Typography>
                  <Button size="small" onClick={() => setOpenId(p.procedure.id)}>Open the procedure</Button>
                </Stack>
                <Typography variant="caption" color="text.secondary">{matched(p)}</Typography>
              </Box>))}
          </Box>)}
      </GlassCard>

      <GlassCard sx={{ p: 2, mb: 2 }}>
        <Stack direction="row" sx={{ gap: 1.5, flexWrap: 'wrap', alignItems: 'center' }} component="form"
               onSubmit={(e: React.FormEvent) => { e.preventDefault(); setQ(typed.trim()) }}>
          <TextField size="small" label="Title or code" value={typed} sx={{ minWidth: 220 }}
                     onChange={(e) => setTyped(e.target.value)} slotProps={{ htmlInput: { maxLength: 200 } }} />
          <Button type="submit" variant="outlined">Search</Button>
          <TextField select size="small" label="Category" value={category} sx={{ minWidth: 180 }} slotProps={shrunk}
                     onChange={(e) => setCategory(e.target.value)}>
            <MenuItem value="">Every category</MenuItem>
            {(data?.categories ?? []).map((c) => <MenuItem key={c} value={c}>{label(c)}</MenuItem>)}
          </TextField>
          {data?.can_write && (
            <TextField select size="small" label="Standing" value={state} sx={{ minWidth: 200 }} slotProps={shrunk}
                       onChange={(e) => setState(e.target.value as Shown)}>
              <MenuItem value="">Any</MenuItem>
              <MenuItem value="IN_FORCE">In force</MenuItem>
              <MenuItem value="AWAITING">Awaiting approval</MenuItem>
              <MenuItem value="NOT_YET_APPROVED">Not yet approved</MenuItem>
              <MenuItem value="EXPIRED">Run out</MenuItem>
              <MenuItem value="RETIRED">Retired</MenuItem>
            </TextField>)}
        </Stack>
      </GlassCard>

      <GlassCard sx={{ p: 2 }}>
        {!!error && <ErrorState compact error={error} onRetry={refetchData} />}
        {isLoading ? <Skeleton height={200} /> : !items.length && !error ? (
          <Alert severity="info">
            {data?.can_write ? 'No procedure matches. Write one, submit it, and have somebody else approve it.'
              : 'No procedure is in force.'}
          </Alert>
        ) : (
          <TableContainer>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>Procedure</TableCell><TableCell>For</TableCell><TableCell>Standing</TableCell><TableCell />
                </TableRow>
              </TableHead>
              <TableBody>
                {items.map((p) => (
                  <TableRow key={p.id} data-testid="procedure-row" hover sx={{ opacity: p.state === 'RETIRED' ? 0.55 : 1 }}>
                    <TableCell>
                      <Typography variant="body2" sx={{ fontWeight: 600 }}>{p.code} · {p.title}</Typography>
                      <Typography variant="caption" color="text.secondary">{label(p.category)}</Typography>
                    </TableCell>
                    <TableCell>
                      <Typography variant="body2">{p.site_name ?? 'Every site'}</Typography>
                      <Stack direction="row" sx={{ gap: 0.5, flexWrap: 'wrap' }}>
                        {p.incident_types.map((k) => <Chip key={k} size="small" variant="outlined" label={k} />)}
                      </Stack>
                    </TableCell>
                    <TableCell>
                      <Chip size="small" color={STATE_COLOUR[p.state]} label={STATE_LABEL[p.state]} />
                      <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>{standing(p)}</Typography>
                    </TableCell>
                    <TableCell align="right"><Button size="small" onClick={() => setOpenId(p.id)}>Open</Button></TableCell>
                  </TableRow>))}
              </TableBody>
            </Table>
          </TableContainer>
        )}
      </GlassCard>
      <WriteDialog open={writing} categories={data?.categories ?? []} onClose={() => setWriting(false)}
                   onDone={(made) => { void again().then(() => setOpenId(made.id)) }} />
      <ProcedureDialog id={openId} onClose={() => setOpenId(null)} onChanged={again} />
    </Box>
  )
}
