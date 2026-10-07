/**
 * Visitor authorisations: who said a visit may happen, for where and for how
 * long — asked for at the gate or the desk, answered by the host.
 *
 * It informs the gate; it does not check anybody in or refuse anybody.
 * Registering visitors, checking them in and work permits are on the screens
 * that have always had them, unchanged.
 *
 * "Door events to look at" lists where a visitor's badge was used outside what
 * the visit was authorised for. They are for a person to look at, not findings.
 */
import { useState } from 'react'
import {
  Alert, Box, Button, Chip, FormControlLabel, MenuItem, Skeleton, Switch, Table, TableBody, TableCell, TableContainer,
  TableHead, TableRow, TextField, Typography,
} from '@mui/material'
import AddIcon from '@mui/icons-material/Add'
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import Stack from '@/components/common/Stack'
import { GlassCard } from '@/components/common/GlassCard'
import { PageHeader } from '@/components/common/PageHeader'
import { getSites } from '@/api/sites'
import { apiError, approveAuthorisation, listAuthorisations, movementsToReview, waitingForMe } from '@/api/visitorAuth'
import type { Standing, SubjectKind } from '@/api/visitorAuth'
import { AskDialog, AuthorisationDialog } from '@/components/visitorAuth/AuthDialogs'
import {
  STANDINGS, STANDING_COLOUR, STANDING_LABEL, about, against, door, fmt, period, who,
} from '@/components/visitorAuth/authFormat'

const shrunk = { select: { displayEmpty: true }, inputLabel: { shrink: true } }

export default function VisitorAuthorisations() {
  const qc = useQueryClient()
  const [siteId, setSiteId] = useState('')
  const [standing, setStanding] = useState<Standing | ''>('')
  const [subject, setSubject] = useState<SubjectKind | ''>('')
  const [typed, setTyped] = useState('')
  const [q, setQ] = useState('')
  const [history, setHistory] = useState(false)
  const [asking, setAsking] = useState(false)
  const [openId, setOpenId] = useState<string | null>(null)
  const { data: sites } = useQuery({ queryKey: ['sites'], queryFn: () => getSites(true) })
  const { data, isLoading, error } = useQuery({
    queryKey: ['visitor-auths', siteId, standing, subject, q, history],
    queryFn: () => listAuthorisations({ site_id: siteId || undefined, standing: standing || undefined,
                                        subject: subject || undefined, q: q || undefined, history: history || undefined }),
    // What was there stays on screen while a narrower list is fetched, so the filters do not flicker away.
    placeholderData: keepPreviousData,
  })
  const { data: mine } = useQuery({ queryKey: ['visitor-auths-mine'], queryFn: waitingForMe })
  const { data: review } = useQuery({
    queryKey: ['visitor-auths-review'], queryFn: () => movementsToReview(), enabled: !!data?.can_manage })
  const again = () => Promise.all(['visitor-auths', 'visitor-auths-mine', 'visitor-auths-review']
    .map((key) => qc.invalidateQueries({ queryKey: [key] })))
  const approve = useMutation({ mutationFn: (id: string) => approveAuthorisation(id).then(again) })
  const items = data?.items ?? []
  return (
    <Box sx={{ p: 3 }}>
      <PageHeader title="Visitor Authorisations"
                  subtitle="Who said a visit may happen, for where and for how long. It informs the gate; it checks nobody in and refuses nobody"
                  action={data?.can_ask ? (
                    <Button variant="contained" startIcon={<AddIcon />} onClick={() => setAsking(true)}>
                      Ask for an authorisation</Button>) : undefined} />

      {!!mine?.length && (
        <GlassCard sx={{ p: 2, mb: 2 }} data-testid="waiting">
          <Typography variant="subtitle1" sx={{ fontWeight: 700, mb: 1 }}>Waiting for your answer</Typography>
          {mine.map((a) => (
            <Stack key={a.id} direction="row" data-testid="waiting-row"
                   sx={{ gap: 1.5, py: 1, alignItems: 'center', flexWrap: 'wrap', borderTop: 1, borderColor: 'divider' }}>
              <Box sx={{ flex: 1, minWidth: 240 }}>
                <Typography variant="body2" sx={{ fontWeight: 600 }}>{who(a)}</Typography>
                <Typography variant="caption" color="text.secondary">
                  {a.site_name} · {period(a)}{about(a) ? ` · ${about(a)}` : ''}
                  {a.asked_of_me ? '' : ' · no host is named'}
                </Typography>
              </Box>
              <Button size="small" variant="contained" color="success" disabled={approve.isPending}
                      onClick={() => approve.mutate(a.id)}>Approve</Button>
              <Button size="small" onClick={() => setOpenId(a.id)}>Open</Button>
            </Stack>))}
          {approve.isError && <Alert severity="error" sx={{ mt: 1 }}>{apiError(approve.error)}</Alert>}
        </GlassCard>)}

      {data?.can_manage && !!review?.items.length && (
        <GlassCard sx={{ p: 2, mb: 2 }} data-testid="to-review">
          <Typography variant="subtitle1" sx={{ fontWeight: 700 }}>Door events to look at</Typography>
          <Typography variant="caption" color="text.secondary">{review.note}</Typography>
          {review.items.map((m) => (
            <Stack key={`${m.authorization_id}-${m.access_event_id}`} direction="row" data-testid="review-row"
                   sx={{ gap: 1.5, py: 1, mt: 0.5, alignItems: 'center', flexWrap: 'wrap', borderTop: 1, borderColor: 'divider' }}>
              <Box sx={{ flex: 1, minWidth: 240 }}>
                <Typography variant="body2" sx={{ fontWeight: 600 }}>{m.subject_name ?? 'A visitor'} — {door(m)}</Typography>
                <Typography variant="caption" color="text.secondary">
                  {m.site_name} · {fmt(m.occurred_at)} · {against(m).text}
                </Typography>
              </Box>
              <Button size="small" onClick={() => setOpenId(m.authorization_id)}>Open the authorisation</Button>
            </Stack>))}
        </GlassCard>)}

      <GlassCard sx={{ p: 2, mb: 2 }}>
        <Stack direction="row" sx={{ gap: 1.5, flexWrap: 'wrap', alignItems: 'center' }} component="form"
               onSubmit={(e: React.FormEvent) => { e.preventDefault(); setQ(typed.trim()) }}>
          <TextField size="small" label="Visitor, company or permit" value={typed} sx={{ minWidth: 220 }}
                     onChange={(e) => setTyped(e.target.value)} slotProps={{ htmlInput: { maxLength: 200 } }} />
          <Button type="submit" variant="outlined">Search</Button>
          <TextField select size="small" label="Site" value={siteId} sx={{ minWidth: 180 }} slotProps={shrunk}
                     onChange={(e) => setSiteId(e.target.value)}>
            <MenuItem value="">Every site</MenuItem>
            {(sites ?? []).map((s) => <MenuItem key={s.id} value={s.id}>{s.name}</MenuItem>)}
          </TextField>
          <TextField select size="small" label="Standing" value={standing} sx={{ minWidth: 200 }} slotProps={shrunk}
                     onChange={(e) => setStanding(e.target.value as Standing | '')}>
            <MenuItem value="">Any</MenuItem>
            {STANDINGS.map((s) => <MenuItem key={s} value={s}>{STANDING_LABEL[s]}</MenuItem>)}
          </TextField>
          <TextField select size="small" label="For" value={subject} sx={{ minWidth: 170 }} slotProps={shrunk}
                     onChange={(e) => setSubject(e.target.value as SubjectKind | '')}>
            <MenuItem value="">Visits and permits</MenuItem>
            <MenuItem value="visit">Visits</MenuItem>
            <MenuItem value="work_permit">Work permits</MenuItem>
          </TextField>
          <FormControlLabel label="Earlier ones too" sx={{ ml: 0 }}
                            control={<Switch size="small" checked={history} onChange={(e) => setHistory(e.target.checked)} />} />
        </Stack>
      </GlassCard>

      <GlassCard sx={{ p: 2 }}>
        {!!error && <Alert severity="error">{apiError(error)}</Alert>}
        {isLoading ? <Skeleton height={200} /> : !items.length && !error ? (
          <Alert severity="info">
            {data?.can_ask ? 'No authorisation matches. Ask for one for a visit that is expected, or for a work permit.'
              : 'No authorisation matches.'}
          </Alert>
        ) : (
          <TableContainer>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>For</TableCell><TableCell>Where and when</TableCell><TableCell>What stands</TableCell>
                  <TableCell />
                </TableRow>
              </TableHead>
              <TableBody>
                {items.map((a) => (
                  <TableRow key={a.id} data-testid="auth-row" hover sx={{ opacity: a.is_latest ? 1 : 0.6 }}>
                    <TableCell>
                      <Typography variant="body2" sx={{ fontWeight: 600 }}>{who(a)}</Typography>
                      <Typography variant="caption" color="text.secondary">{about(a)}</Typography>
                    </TableCell>
                    <TableCell>
                      <Typography variant="body2">{a.site_name}</Typography>
                      <Typography variant="caption" color="text.secondary">{period(a)}</Typography>
                    </TableCell>
                    <TableCell>
                      <Chip size="small" color={STANDING_COLOUR[a.standing]} label={STANDING_LABEL[a.standing]} />
                      <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>{a.says[0]}</Typography>
                    </TableCell>
                    <TableCell align="right"><Button size="small" onClick={() => setOpenId(a.id)}>Open</Button></TableCell>
                  </TableRow>))}
              </TableBody>
            </Table>
          </TableContainer>
        )}
        {data?.has_more && (
          <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1 }}>
            The newest {data.limit} are shown. Narrow the list to find an earlier one.</Typography>)}
      </GlassCard>
      <AskDialog open={asking} onClose={() => setAsking(false)}
                 onDone={(made) => { void again().then(() => setOpenId(made.id)) }} />
      <AuthorisationDialog id={openId} onClose={() => setOpenId(null)} onChanged={again} />
    </Box>
  )
}
