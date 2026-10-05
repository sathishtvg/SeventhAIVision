/**
 * Security Intelligence — one situation, and the place where a person decides.
 *
 * Left: what happened and why the layer thinks what it thinks — the summary,
 * every risk factor, the confidences each under its own name, what was not
 * known, the events and why each is here, the cameras worth opening.
 *
 * Right: what the layer SUGGESTS (violet, dashed, marked as a suggestion), then
 * what a PERSON may decide and has decided (green, solid, named), and under
 * each decision what the platform then DID. The three are never drawn alike.
 *
 * A drone looks only when a person decides it should and says how — hold the
 * flight that saw it, or start a mission the site already has. What it then
 * sees comes back as an event here, and the situation is assessed again.
 */
import { useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { useParams } from 'react-router-dom'
import {
  Alert, Box, Button, Chip, Dialog, DialogActions, DialogContent, DialogTitle, Divider, FormControlLabel, Grid, List,
  ListItemButton, ListItemText, MenuItem, Radio, RadioGroup, Skeleton, Table, TableBody, TableCell, TableHead, TableRow,
  TextField, Tooltip, Typography,
} from '@mui/material'
import VideocamIcon from '@mui/icons-material/Videocam'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import Stack from '@/components/common/Stack'
import { GlassCard } from '@/components/common/GlassCard'
import { PageHeader } from '@/components/common/PageHeader'
import { HlsPlayer } from '@/components/common/HlsPlayer'
import { usePermission } from '@/hooks/usePermission'
import { useAuthStore } from '@/store/auth'
import { apiClient } from '@/api/client'
import { getStreams } from '@/api/cameras'
import {
  apiError, decide, getAuthority, getObservations, getRecommendations, getResponders, getSituation,
  getSituationDrone, getTrail, recordReview,
} from '@/api/securityIntelligence'
import type {
  Authority, AuthorityAction, DecisionAction, DroneLook, DronePicture, ReasonCode, RecommendedCamera, Recommendations,
  SituationDetail, SituationEvent, Trail,
} from '@/api/securityIntelligence'
import {
  AiMark, Confidences, DecisionCard, DecisionStatusChip, FactorTable, HumanMark, RiskChip, SuggestionCard,
} from '@/components/intel/intelUi'
import {
  AI_COLOR, DECISION_LABEL, HUMAN_COLOR, INCIDENT_LABEL, LOOK_LABEL, ROLE_LABEL, SOURCE_LABEL, STATUS_LABEL,
  STEP_LABEL, fmt, fmtTime, newClientRef, pct, pretty, useIntelRealtime,
} from '@/components/intel/intelFormat'
import { IntelNav, IntelStatusBanner } from './IntelNav'

export default function Situation() {
  const { id } = useParams()
  const canSeeSuggestions = usePermission('intel:recommendation:read')
  const canDecide = usePermission('intel:decide')
  const { data: situation, isLoading, error } = useQuery({
    queryKey: ['intel-situation', id], queryFn: () => getSituation(id!), refetchInterval: 20_000 })
  const { data: recs } = useQuery({
    queryKey: ['intel-recommendations', id], queryFn: () => getRecommendations(id!), enabled: canSeeSuggestions })
  const { data: trail } = useQuery({ queryKey: ['intel-trail', id], queryFn: () => getTrail(id!) })
  const { data: authority } = useQuery({
    queryKey: ['intel-authority', id], queryFn: () => getAuthority(id!), enabled: canDecide })
  // While a drone is holding to look, ask more often: the answer is seconds away.
  const { data: drone } = useQuery({
    queryKey: ['intel-drone', id], queryFn: () => getSituationDrone(id!),
    refetchInterval: (q) => (lookingNow(q.state.data) ? 5_000 : 20_000) })
  useIntelRealtime([['intel-situation', id], ['intel-recommendations', id], ['intel-trail', id], ['intel-authority', id],
                    ['intel-observations', id], ['intel-drone', id]],
                   (_t, p) => p.situation_id === id || p.id === id)

  // That this officer has looked at what was suggested — once per assessment.
  const assessmentId = recs?.assessment?.id
  useEffect(() => {
    if (canSeeSuggestions && id && assessmentId) recordReview(id).catch(() => { /* looking is not blocked by this */ })
  }, [canSeeSuggestions, id, assessmentId])

  if (isLoading) return <Box sx={{ p: 3 }}><Skeleton height={500} /></Box>
  if (error || !situation) {
    return <Box sx={{ p: 3 }}><Alert severity="error">{error ? apiError(error) : 'Situation not found.'}</Alert></Box>
  }
  const a = situation.assessment
  const first = recs?.recommendations.find((r) => r.available)
  return (
    <Box sx={{ p: 3 }}>
      <PageHeader title={a?.label ?? situation.title}
                  subtitle={`${situation.situation_number} · ${situation.site_name ?? 'No site'}`
                    + `${situation.primary_camera_name ? ` · ${situation.primary_camera_name}` : ''} · started ${fmt(situation.started_at)}`}
                  action={<Stack direction="row" sx={{ gap: 1 }}><RiskChip level={situation.risk_level} score={situation.risk_score} />
                    <DecisionStatusChip status={situation.decision_status} /></Stack>} />
      <IntelNav />
      <IntelStatusBanner />
      <Grid container spacing={2}>
        <Grid size={{ xs: 12, lg: 7 }}>
          <Summary situation={situation} recs={recs} trail={trail} />
          <Why situation={situation} recommendationConfidence={first?.recommendation_confidence} />
          <Events situation={situation} />
          <Cameras situation={situation} recs={recs} />
          <Drones picture={drone} />
        </Grid>
        <Grid size={{ xs: 12, lg: 5 }}>
          {situation.reassessed_since_decision && (
            <Alert severity="info" data-testid="reassessed" sx={{ mb: 2 }}>
              Assessed again since the last decision: new events, or what a drone saw, changed the picture.
              The decision stands until a person decides again.
            </Alert>)}
          {canSeeSuggestions && <Suggestions recs={recs} />}
          <Decide situationId={situation.id} authority={authority} canDecide={canDecide}
                  seenAssessmentId={a?.id} closed={situation.closed_at != null} drone={drone} />
          <Ground situationId={situation.id} />
          <History trail={trail} />
        </Grid>
      </Grid>
    </Box>
  )
}

// ── The situation in one card ────────────────────────────────────────────────

function Line({ label, children }: { label: string; children: ReactNode }) {
  return (
    <Box sx={{ display: 'grid', gridTemplateColumns: '150px 1fr', gap: 1, py: 0.5, alignItems: 'baseline' }}>
      <Typography variant="caption" color="text.secondary" sx={{ textTransform: 'uppercase', letterSpacing: '0.06em' }}>
        {label}</Typography>
      <Box>{children}</Box>
    </Box>
  )
}

function Summary({ situation, recs, trail }: { situation: SituationDetail; recs?: Recommendations; trail?: Trail }) {
  const a = situation.assessment
  const first = recs?.recommendations.find((r) => r.available)
  const last = trail?.decisions[trail.decisions.length - 1]
  return (
    <GlassCard sx={{ p: 2, mb: 2 }}>
      <Typography variant="subtitle1" sx={{ fontWeight: 600, mb: 1 }}>Security situation</Typography>
      <Line label="Risk"><RiskChip level={situation.risk_level} score={situation.risk_score} /></Line>
      <Line label="Location">
        <Typography variant="body2">{[situation.site_name, situation.primary_camera_name ?? situation.location_label]
          .filter(Boolean).join(' · ') || 'Not recorded'}</Typography></Line>
      <Line label="Started"><Typography variant="body2">{fmt(situation.started_at)}</Typography></Line>
      <Line label="Sources">
        <Stack direction="row" sx={{ gap: 0.5, flexWrap: 'wrap' }}>
          {situation.source_types.map((t) => <Chip key={t} size="small" variant="outlined" label={SOURCE_LABEL[t] ?? t} />)}
        </Stack></Line>
      <Divider sx={{ my: 1 }} />
      <Line label="AI assessment">
        {a ? <><AiMark /> <Typography component="span" variant="body2" sx={{ ml: 0.5 }}>{a.label}</Typography></>
          : <Typography variant="body2" color="text.secondary">Not assessed yet.</Typography>}</Line>
      {recs && (
        <Line label="AI recommendation">
          {first ? <><AiMark>Suggests</AiMark> <Typography component="span" variant="body2" sx={{ ml: 0.5 }}>
            {DECISION_LABEL[first.action]}</Typography></>
            : <Typography variant="body2" color="text.secondary">Nothing suggested yet.</Typography>}</Line>)}
      <Line label="Human decision">
        {last ? <><HumanMark>{last.state === 'PENDING_APPROVAL' ? 'Proposed' : 'Decided'}</HumanMark>{' '}
          <Typography component="span" variant="body2" sx={{ ml: 0.5 }}>
            {DECISION_LABEL[last.action]} — {last.decided_by.name ?? 'a former user'}, {fmtTime(last.decided_at)}</Typography></>
          : <Typography variant="body2" sx={{ fontWeight: 700 }}>Pending — nobody has decided yet</Typography>}</Line>
      <Line label="Incident">
        <Typography variant="body2">{INCIDENT_LABEL[situation.incident.state]}</Typography></Line>
    </GlassCard>
  )
}

// ── Why this alert, why this risk ────────────────────────────────────────────

function Why({ situation, recommendationConfidence }: {
  situation: SituationDetail; recommendationConfidence?: number
}) {
  const a = situation.assessment
  if (!a) {
    return (
      <GlassCard sx={{ p: 2, mb: 2 }}>
        <Typography variant="subtitle1" sx={{ fontWeight: 600, mb: 1 }}>Why this risk</Typography>
        <Typography variant="body2" color="text.secondary">
          The situation has not been assessed yet. Its events are below; the most severe is {situation.severity}.
        </Typography>
      </GlassCard>
    )
  }
  return (
    <GlassCard sx={{ p: 2, mb: 2 }}>
      <Stack direction="row" sx={{ alignItems: 'center', gap: 1, mb: 1, flexWrap: 'wrap' }}>
        <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>Why this risk</Typography>
        <RiskChip level={a.risk_level} score={a.risk_score} />
        <AiMark>AI-assisted assessment</AiMark>
      </Stack>
      <Typography variant="body2" sx={{ mb: 1 }}>{a.summary}</Typography>
      <FactorTable factors={a.risk_factors} />
      <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1 }}>
        {a.anomaly_score == null ? a.normality_basis
          : `How unusual for this place and hour: ${a.anomaly_score} of 100. ${a.normality_basis ?? ''}`}
      </Typography>

      <Typography variant="subtitle2" sx={{ fontWeight: 600, mt: 2, mb: 0.5 }}>How sure — four different things</Typography>
      <Confidences confidence={a.confidence} recommendation={recommendationConfidence} />

      {a.unknowns.length > 0 && (
        <>
          <Typography variant="subtitle2" sx={{ fontWeight: 600, mt: 2, mb: 0.5 }}>
            Not known ({a.unknowns.length}) — each lowers the risk confidence, none adds to the risk</Typography>
          {a.unknowns.map((u, i) => <Typography key={i} variant="body2" color="text.secondary">• {u}</Typography>)}
        </>)}

      {!!a.statements?.length && (
        <>
          <Typography variant="subtitle2" sx={{ fontWeight: 600, mt: 2, mb: 0.5 }}>What was known, and from where</Typography>
          <Table size="small">
            <TableBody>
              {a.statements.map((s, i) => (
                <TableRow key={i}>
                  <TableCell sx={{ pl: 0 }}><Typography variant="body2">{s.text}</Typography></TableCell>
                  <TableCell align="right" sx={{ pr: 0 }}>
                    <Typography variant="caption" color="text.secondary">{s.source}</Typography></TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </>)}
      <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1 }}>
        Assessment {a.sequence} · {fmt(a.assessed_at)} · {a.engine_version}. Made of recorded facts only.
      </Typography>
    </GlassCard>
  )
}

// ── Related events ───────────────────────────────────────────────────────────

const text = (v: unknown) => (typeof v === 'string' && v.trim() ? v : null)

/** What the source itself recorded about an event, where that says more than the title. */
function EventDetail({ e }: { e: SituationEvent }) {
  const a = e.attributes ?? {}
  if (e.event_type === 'vpatrol.exception') {
    const lines = [
      text(a.question) && `Asked: ${a.question}`,
      text(a.answer) && `Answered: ${a.answer}`,
      text(a.exception_reason) && `Why it is an exception: ${a.exception_reason}`,
      text(a.observation) && `Officer's note on the camera: ${a.observation}`,
      a.has_snapshot === true && `A snapshot was kept at the check${text(a.snapshot_taken_at)
        ? `, ${fmtTime(a.snapshot_taken_at as string)}` : ''}.`,
    ].filter(Boolean) as string[]
    if (!lines.length) return null
    return (
      <Box data-testid="patrol-finding" sx={{ mt: 0.5 }}>
        <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
          Virtual patrol {text(a.patrol_number) ?? ''} — as the officer recorded it</Typography>
        {lines.map((l, i) => <Typography key={i} variant="caption" sx={{ display: 'block' }}>{l}</Typography>)}
      </Box>
    )
  }
  if (e.event_type === 'drone.verification') {
    const added = typeof a.detections_added === 'number' ? a.detections_added : 0
    return (
      <Box data-testid="drone-look-result" sx={{ mt: 0.5 }}>
        <Typography variant="caption" sx={{ display: 'block' }}>
          {typeof a.hold_seconds === 'number' ? `Held for ${a.hold_seconds} s. ` : ''}
          {added > 0 ? `${added} more detection(s) of the same thing.` : 'Nothing more seen.'}
          {text(a.risk_before) && text(a.drone_risk_level)
            ? ` The drone module's own risk: ${pretty(a.risk_before as string)} → ${pretty(a.drone_risk_level as string)}.`
            : ''}
        </Typography>
      </Box>
    )
  }
  return null
}

function Events({ situation }: { situation: SituationDetail }) {
  return (
    <GlassCard sx={{ p: 2, mb: 2 }}>
      <Typography variant="subtitle1" sx={{ fontWeight: 600, mb: 1 }}>
        Related events ({situation.events.length})</Typography>
      <Table size="small">
        <TableHead><TableRow><TableCell>Time</TableCell><TableCell>Source</TableCell><TableCell>What</TableCell>
          <TableCell>Why it is here</TableCell></TableRow></TableHead>
        <TableBody>
          {situation.events.map((e) => (
            <TableRow key={e.id} sx={{ opacity: e.is_duplicate ? 0.6 : 1 }}>
              <TableCell sx={{ whiteSpace: 'nowrap' }}>{fmtTime(e.occurred_at)}</TableCell>
              <TableCell>{SOURCE_LABEL[e.source_type] ?? e.source_type}
                {(e.camera_name || e.location_label) && (
                  <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
                    {e.camera_name ?? e.location_label}</Typography>)}</TableCell>
              <TableCell><Typography variant="body2">{e.title}</Typography>
                {e.confidence != null && (
                  <Typography variant="caption" color="text.secondary">Detection confidence {pct(e.confidence)}</Typography>)}
                <EventDetail e={e} />
              </TableCell>
              <TableCell><Typography variant="body2">{e.reason}</Typography>
                {e.is_duplicate && <Typography variant="caption" color="text.secondary">A repeat — folded, not dropped</Typography>}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </GlassCard>
  )
}

// ── Recommended cameras ──────────────────────────────────────────────────────

const CAMERA_STATE: Record<string, string> = {
  online: 'online', degraded: 'degraded', offline: 'not sending', disabled: 'switched off', not_known: 'state not known',
}

function LiveDialog({ camera, onClose }: { camera: RecommendedCamera; onClose: () => void }) {
  const token = useAuthStore((s) => s.accessToken)
  const { data: streams, isLoading, error } = useQuery({
    queryKey: ['camera-streams', camera.id], queryFn: () => getStreams(camera.id) })
  const stream = streams?.[0]
  return (
    <Dialog open onClose={onClose} maxWidth="md" fullWidth>
      <DialogTitle>{camera.name} · live</DialogTitle>
      <DialogContent>
        {isLoading ? <Skeleton height={320} />
          : error ? <Alert severity="error">{apiError(error)}</Alert>
            : stream && token ? (
              <HlsPlayer src={`${apiClient.defaults.baseURL ?? ''}/api/v1/cameras/${camera.id}/streams/${stream.id}`
                + `/hls/index.m3u8?token=${token}`} sx={{ width: '100%', aspectRatio: '16/9' }} />
            ) : <Typography variant="body2" color="text.secondary">This camera has no stream to open.</Typography>}
      </DialogContent>
      <DialogActions><Button onClick={onClose}>Close</Button></DialogActions>
    </Dialog>
  )
}

function Cameras({ situation, recs }: { situation: SituationDetail; recs?: Recommendations }) {
  const [open, setOpen] = useState<RecommendedCamera | null>(null)
  // The cameras the layer named; without the right to read suggestions, the ones that reported.
  const cameras = useMemo<RecommendedCamera[]>(() => {
    const named = recs?.recommendations.find((r) => r.action === 'VIEW_CAMERA')?.supporting.cameras
    if (named?.length) return named
    const seen = new Map<string, RecommendedCamera>()
    situation.events.forEach((e) => {
      if (e.camera_id && !seen.has(e.camera_id)) {
        seen.set(e.camera_id, { id: e.camera_id, name: e.camera_name ?? 'Camera', state: 'not_known', relation: 'reported' })
      }
    })
    return [...seen.values()]
  }, [recs, situation.events])
  return (
    <GlassCard sx={{ p: 2, mb: 2 }}>
      <Typography variant="subtitle1" sx={{ fontWeight: 600, mb: 1 }}>Recommended cameras</Typography>
      {!cameras.length ? (
        <Typography variant="body2" color="text.secondary">No camera is linked to this situation.</Typography>
      ) : (
        <List dense disablePadding>
          {cameras.map((c) => (
            <ListItemButton key={c.id} onClick={() => setOpen(c)} disabled={c.state === 'offline' || c.state === 'disabled'}>
              <VideocamIcon fontSize="small" sx={{ mr: 1.5, opacity: 0.7 }} />
              <ListItemText primary={c.name}
                            secondary={`${c.relation === 'reported' ? 'reported this' : 'next to a camera that did'} · `
                              + `${CAMERA_STATE[c.state] ?? c.state}`} />
            </ListItemButton>
          ))}
        </List>
      )}
      <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1 }}>
        Opening a camera is yours to do. The layer moves no camera and changes no view.
      </Typography>
      {open && <LiveDialog camera={open} onClose={() => setOpen(null)} />}
    </GlassCard>
  )
}

// ── Drones: what was asked of one, and what came back ────────────────────────

/** What was asked, said as an asking: whether it was done is on the line beside it. */
const ASKED_LABEL: Record<string, string> = {
  DRONE_HOLD: 'A flight to hold and look again', DRONE_LAUNCH: 'A mission to start',
}

/** A look is under way: asked, or holding. */
function lookingNow(p?: DronePicture): boolean {
  if (!p) return false
  const busy = (l: DroneLook | null) => !!l && (l.status === 'REQUESTED' || l.status === 'HOLDING')
  return p.asked.some((x) => busy(x.look)) || p.other_looks.some(busy)
}

function lookWords(l: DroneLook): string {
  if (l.status !== 'COMPLETED') {
    return `${LOOK_LABEL[l.status] ?? l.status}${l.result.reason ? ` — ${l.result.reason}` : ''}`
  }
  const added = l.result.detections_added ?? 0
  const risk = l.result.risk_before && l.result.risk_after
    ? ` The drone module's own risk: ${pretty(l.result.risk_before)} → ${pretty(l.result.risk_after)}.` : ''
  return `Looked for ${l.hold_seconds} s: ${added > 0 ? `${added} more detection(s)` : 'nothing more seen'}.${risk}`
}

/** Shown only where a drone has something to do with the situation. */
function Drones({ picture }: { picture?: DronePicture }) {
  if (!picture) return null
  const { sightings, asked, other_looks: others } = picture
  if (!sightings.length && !asked.length && !others.length) return null
  return (
    <GlassCard sx={{ p: 2, mb: 2 }} data-testid="drone-card">
      <Typography variant="subtitle1" sx={{ fontWeight: 600, mb: 0.5 }}>Drone</Typography>
      <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1 }}>
        A drone looks only when a person decides it should. What it then sees comes back as an event above,
        and the situation is assessed again.
      </Typography>
      {sightings.map((s) => (
        <Box key={s.drone_event_id} sx={{ py: 0.5 }}>
          <Typography variant="body2">{s.title}</Typography>
          <Typography variant="caption" color="text.secondary">
            {fmtTime(s.occurred_at)} · {s.drone_name ?? 'Drone'}
            {s.session_number ? ` · flight ${s.session_number} (${pretty(s.flight_status ?? 'not known')})` : ''}
            {' · '}{s.can_hold ? 'could be asked to hold and look again' : s.why_not}
          </Typography>
        </Box>
      ))}
      {(asked.length > 0 || others.length > 0) && <Divider sx={{ my: 1 }} />}
      {asked.map((x) => (
        <Box key={x.action_id} data-testid="drone-asked" sx={{ py: 0.5 }}>
          <Stack direction="row" sx={{ alignItems: 'center', gap: 1, flexWrap: 'wrap' }}>
            <HumanMark>Asked by a decision</HumanMark>
            <Typography variant="body2">{ASKED_LABEL[x.action] ?? x.action} · {fmtTime(x.asked_at)}</Typography>
            {x.result !== 'OK' && <Chip size="small" color="warning" variant="outlined" label="Not done" />}
          </Stack>
          {x.result !== 'OK' && <Typography variant="caption" sx={{ display: 'block' }}>{x.detail}</Typography>}
          {x.look && <Typography variant="caption" sx={{ display: 'block' }}>{lookWords(x.look)}</Typography>}
          {x.flight && (
            <Typography variant="caption" sx={{ display: 'block' }}>
              Flight {x.flight.session_number}{x.flight.mission_name ? ` of “${x.flight.mission_name}”` : ''}:
              {' '}{pretty(x.flight.status)}
              {x.flight.event_count ? ` · ${x.flight.event_count} event(s) raised` : ''}
            </Typography>)}
        </Box>
      ))}
      {others.map((l) => (
        <Box key={l.id} data-testid="drone-other-look" sx={{ py: 0.5 }}>
          <Typography variant="body2">Asked from the drone screens · {fmtTime(l.asked_at)}</Typography>
          <Typography variant="caption" sx={{ display: 'block' }}>{lookWords(l)}</Typography>
        </Box>
      ))}
    </GlassCard>
  )
}

// ── What the layer suggests ──────────────────────────────────────────────────

function Suggestions({ recs }: { recs?: Recommendations }) {
  return (
    <GlassCard sx={{ p: 2, mb: 2, borderColor: `${AI_COLOR}55` }}>
      <Stack direction="row" sx={{ alignItems: 'center', gap: 1, mb: 1 }}>
        <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>AI recommendations</Typography>
        <Chip size="small" variant="outlined" label="Suggestions — not decisions"
              sx={{ borderColor: AI_COLOR, color: AI_COLOR, borderStyle: 'dashed' }} />
      </Stack>
      {!recs ? <Skeleton height={120} /> : !recs.recommendations.length ? (
        <Typography variant="body2" color="text.secondary">
          {recs.assessment ? 'Nothing has been suggested for this assessment yet.'
            : 'The situation has not been assessed yet, so nothing has been suggested.'}</Typography>
      ) : (
        <>
          {recs.recommendations.map((r, i) => <SuggestionCard key={r.id} rec={r} first={i === 0} />)}
          {!!recs.recommendations[0].supporting.rests_on?.length && (
            <Box sx={{ mt: 1 }}>
              <Typography variant="caption" sx={{ fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em',
                                                  color: 'text.secondary' }}>Why these are suggested</Typography>
              {recs.recommendations[0].supporting.rests_on.map((s, i) => (
                <Typography key={i} variant="body2" color="text.secondary">✓ {s}</Typography>))}
            </Box>)}
        </>
      )}
      <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1 }}>
        Nothing here has been done. A person decides below.
      </Typography>
    </GlassCard>
  )
}

// ── Where a person decides ───────────────────────────────────────────────────

/** The order an officer reads them in: respond, look, raise, close. */
const BUTTONS: DecisionAction[] = [
  'ACKNOWLEDGE', 'DISPATCH_GUARD', 'MONITOR', 'VERIFY_WITH_DRONE', 'VIEW_CAMERA', 'INVESTIGATE', 'ESCALATE',
  'VERIFY', 'CONTACT_SITE', 'CREATE_INCIDENT', 'CONFIRM_INCIDENT', 'REQUEST_ASSISTANCE', 'FALSE_POSITIVE', 'RESOLVE',
]

function Decide({ situationId, authority, canDecide, seenAssessmentId, closed, drone }: {
  situationId: string; authority?: Authority; canDecide: boolean; seenAssessmentId?: string; closed: boolean
  drone?: DronePicture
}) {
  const [choosing, setChoosing] = useState<AuthorityAction | null>(null)
  const by = useMemo(() => new Map((authority?.actions ?? []).map((x) => [x.action, x])), [authority])
  return (
    <GlassCard sx={{ p: 2, mb: 2, borderColor: `${HUMAN_COLOR}55` }}>
      <Stack direction="row" sx={{ alignItems: 'center', gap: 1, mb: 1 }}>
        <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>Human decision</Typography>
        <Chip size="small" variant="outlined" label="Yours to make" sx={{ borderColor: HUMAN_COLOR, color: HUMAN_COLOR }} />
      </Stack>
      {!canDecide ? (
        <Typography variant="body2" color="text.secondary">
          You can read this situation. Deciding on it needs the permission to decide.</Typography>
      ) : closed ? (
        <Typography variant="body2" color="text.secondary">
          This situation is closed. Nothing more can be decided on it.</Typography>
      ) : !authority ? <Skeleton height={140} /> : (
        <>
          {authority.policy.rule.alone == null && authority.policy.rule.with_approval == null && (
            <Alert severity="info" sx={{ mb: 1 }}>
              The decision policy does not let your role decide here. You can ask the command centre for help.
            </Alert>)}
          <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 1 }}>
            {BUTTONS.map((action) => {
              const x = by.get(action)
              if (!x) return null
              const suggested = x.basis === 'FOLLOWED'
              const button = (
                <Button key={action} size="small" disabled={!x.allowed} onClick={() => setChoosing(x)}
                        variant={suggested ? 'contained' : 'outlined'}
                        color={action === 'FALSE_POSITIVE' || action === 'RESOLVE' ? 'inherit' : 'primary'}>
                  {DECISION_LABEL[action]}{x.allowed && x.how === 'WITH_APPROVAL' ? ' · needs approval' : ''}
                </Button>
              )
              return x.why_not
                ? <Tooltip key={action} title={x.why_not}><span>{button}</span></Tooltip>
                : button
            })}
          </Box>
          <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1 }}>
            Filled buttons follow a suggestion. Any other is still yours to choose
            {authority.may_override ? ', with a reason.' : ' where you may override.'}
          </Typography>
        </>
      )}
      {choosing && authority && (
        <DecideDialog situationId={situationId} choice={choosing} authority={authority} drone={drone}
                      seenAssessmentId={seenAssessmentId} onClose={() => setChoosing(null)} />)}
    </GlassCard>
  )
}

/** How the drone should look: `hold:<sighting>`, `launch:<mission>`, or `none` — a record only. */
function firstDroneChoice(p?: DronePicture): string {
  const sighting = p?.may.hold ? p.sightings.find((s) => s.can_hold) : undefined
  if (sighting) return `hold:${sighting.drone_event_id}`
  const mission = p?.may.launch ? p.missions.find((m) => m.can_launch) : undefined
  return mission ? `launch:${mission.mission_id}` : 'none'
}

/** A choice whose label runs to two lines keeps its button beside the first. */
const RADIO_TOP = { alignItems: 'flex-start', mb: 0.5, '& .MuiRadio-root': { pt: 0.25, pb: 0 } }

function DroneChoice({ picture, value, onChange, hold, onHold }: {
  picture: DronePicture; value: string; onChange: (v: string) => void; hold: number; onHold: (n: number) => void
}) {
  const { min, max } = picture.hold_seconds
  return (
    <Box sx={{ mb: 2 }} data-testid="drone-choice">
      <Typography variant="subtitle2" sx={{ fontWeight: 600 }}>How should the drone look?</Typography>
      <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 0.5 }}>
        Your choice. The layer chooses no flight and no mission, and steers nothing.
      </Typography>
      <RadioGroup value={value} onChange={(e) => onChange(e.target.value)}>
        {picture.sightings.map((s) => {
          const why = !picture.may.hold ? 'Asking a flight to hold needs the drone operator permission.' : s.why_not
          return (
            <FormControlLabel key={s.drone_event_id} value={`hold:${s.drone_event_id}`} control={<Radio size="small" />}
                              disabled={!!why} sx={RADIO_TOP}
                              label={<Box><Typography variant="body2">
                                Ask flight {s.session_number ?? ''} to hold and look again at “{s.title}”</Typography>
                                {why && <Typography variant="caption" color="text.secondary">{why}</Typography>}</Box>} />
          )
        })}
        {picture.missions.map((m) => {
          const why = !picture.licence.ok ? picture.licence.problem
            : !picture.may.launch ? 'Starting a mission needs the permission to start one.' : m.why_not
          return (
            <FormControlLabel key={m.mission_id} value={`launch:${m.mission_id}`} control={<Radio size="small" />}
                              disabled={!!why} sx={RADIO_TOP}
                              label={<Box><Typography variant="body2">
                                Start mission “{m.name}”{m.drone_name ? ` (${m.drone_name})` : ''}</Typography>
                                {why && <Typography variant="caption" color="text.secondary">{why}</Typography>}</Box>} />
          )
        })}
        <FormControlLabel value="none" control={<Radio size="small" />} sx={RADIO_TOP}
                          label={<Typography variant="body2">
                            Record only — I will fly it from the drone screens</Typography>} />
      </RadioGroup>
      {value.startsWith('hold:') && (
        <TextField type="number" size="small" label="Hold for (seconds)" value={hold} sx={{ mt: 1, width: 180 }}
                   onChange={(e) => onHold(Number(e.target.value))}
                   error={hold < min || hold > max} helperText={`${min} to ${max}`}
                   slotProps={{ htmlInput: { min, max } }} />)}
      <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 0.5 }}>
        {value.startsWith('hold:') ? picture.notes.hold : value.startsWith('launch:') ? picture.notes.launch : ''}
      </Typography>
    </Box>
  )
}

function DecideDialog({ situationId, choice, authority, seenAssessmentId, drone, onClose }: {
  situationId: string; choice: AuthorityAction; authority: Authority; seenAssessmentId?: string
  drone?: DronePicture; onClose: () => void
}) {
  const qc = useQueryClient()
  // Made when the dialog opens and kept: a retry of the same press is the same decision.
  const [clientRef] = useState(newClientRef)
  const [reason, setReason] = useState<ReasonCode | ''>('')
  const [note, setNote] = useState('')
  const [guard, setGuard] = useState('')
  const [escalateTo, setEscalateTo] = useState('')
  const asksDrone = choice.action === 'VERIFY_WITH_DRONE' && !!drone
  const [how, setHow] = useState(() => (asksDrone ? firstDroneChoice(drone) : 'none'))
  const [hold, setHold] = useState(drone?.hold_seconds.default ?? 30)
  const holdOk = !how.startsWith('hold:')
    || (Number.isInteger(hold) && hold >= (drone?.hold_seconds.min ?? 5) && hold <= (drone?.hold_seconds.max ?? 120))
  const needsGuard = choice.needs.includes('guard_user_id')
  const needsSenior = choice.needs.includes('escalate_to_user_id')
  const { data: responders, error: respondersError } = useQuery({
    queryKey: ['intel-responders', situationId], queryFn: () => getResponders(situationId),
    enabled: needsGuard || needsSenior })
  const save = useMutation({
    mutationFn: () => decide(situationId, {
      action: choice.action, client_ref: clientRef, seen_assessment_id: seenAssessmentId,
      reason_code: reason || undefined, note: note.trim() || undefined,
      guard_user_id: guard || undefined, escalate_to_user_id: escalateTo || undefined,
      drone_event_id: how.startsWith('hold:') ? how.slice(5) : undefined,
      hold_seconds: how.startsWith('hold:') ? hold : undefined,
      drone_mission_id: how.startsWith('launch:') ? how.slice(7) : undefined }),
    onSuccess: () => {
      ['intel-situation', 'intel-trail', 'intel-authority', 'intel-recommendations', 'intel-drone'].forEach((k) =>
        qc.invalidateQueries({ queryKey: [k, situationId] }))
      qc.invalidateQueries({ queryKey: ['intel-situations'] })
      onClose()
    },
  })
  const ready = (!choice.needs_reason || !!reason) && (reason !== 'OTHER' || !!note.trim())
    && (!needsGuard || !!guard) && (!needsSenior || !!escalateTo) && holdOk
  return (
    <Dialog open onClose={save.isPending ? undefined : onClose} maxWidth="sm" fullWidth>
      <DialogTitle>Decide: {DECISION_LABEL[choice.action]}</DialogTitle>
      <DialogContent>
        <Typography variant="body2" sx={{ mb: 1 }}>
          {choice.basis === 'FOLLOWED' && 'This follows what the layer suggested.'}
          {choice.basis === 'OVERRIDE' && (authority.suggested_action
            ? `The layer suggested ${DECISION_LABEL[authority.suggested_action]} first, not this. `
              + 'You may still decide it: say why.'
            : 'The layer did not suggest this. You may still decide it: say why.')}
          {choice.basis === 'CLOSING' && 'This closes the situation. Say how it ended.'}
          {choice.basis === 'INDEPENDENT' && 'This is recorded as your own decision.'}
        </Typography>
        {choice.how === 'WITH_APPROVAL' && (
          <Alert severity="warning" sx={{ mb: 1 }}>
            Under the decision policy this waits for a second person's approval. Nothing is carried out until then.
          </Alert>)}
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
          {how.startsWith('hold:')
            ? 'The platform will then ask that flight to hold and look again — through the drone module, under your '
              + 'own drone permission. What it sees comes back here as an event.'
            : how.startsWith('launch:')
              ? 'The platform will then start that mission — through the drone module, after its own pre-flight '
                + 'checks. What the flight sees comes back here as events.'
              : choice.carries_out.length
                ? `The platform will then: ${choice.carries_out.map((s) => STEP_LABEL[s] ?? s).join(' → ')}.`
                : 'It is recorded as yours. Nothing is carried out by the platform.'}
        </Typography>
        {asksDrone && drone && <DroneChoice picture={drone} value={how} onChange={setHow} hold={hold} onHold={setHold} />}
        {needsGuard && (
          <TextField select fullWidth size="small" label="Guard to send" value={guard} sx={{ mb: 2 }}
                     onChange={(e) => setGuard(e.target.value)}
                     helperText="Your choice. Guards on shift at this site are listed first.">
            {(responders?.guards ?? []).map((g) => (
              <MenuItem key={g.user_id} value={g.user_id}>
                {g.name}{g.on_shift_here ? ' — on shift here' : g.on_shift ? ' — on shift elsewhere' : ' — not on shift'}
              </MenuItem>))}
          </TextField>)}
        {needsSenior && (
          <TextField select fullWidth size="small" label="Escalate to" value={escalateTo} sx={{ mb: 2 }}
                     onChange={(e) => setEscalateTo(e.target.value)}>
            {(responders?.escalation ?? []).map((u) => (
              <MenuItem key={u.user_id} value={u.user_id}>{u.name} — {ROLE_LABEL[u.role_id] ?? `Role ${u.role_id}`}</MenuItem>))}
          </TextField>)}
        {respondersError && <Alert severity="error" sx={{ mb: 2 }}>{apiError(respondersError)}</Alert>}
        {choice.needs_reason && (
          <TextField select fullWidth size="small" label="Reason" value={reason} sx={{ mb: 2 }}
                     onChange={(e) => setReason(e.target.value as ReasonCode)}>
            {authority.reasons.map((r) => <MenuItem key={r.code} value={r.code}>{r.label}</MenuItem>)}
          </TextField>)}
        <TextField fullWidth multiline minRows={2} size="small" value={note} onChange={(e) => setNote(e.target.value)}
                   label={reason === 'OTHER' ? 'What was it? (required)' : 'Note (optional)'}
                   slotProps={{ htmlInput: { maxLength: 2000 } }} />
        {save.error && <Alert severity="error" sx={{ mt: 2 }}>{apiError(save.error)}</Alert>}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={save.isPending}>Cancel</Button>
        <Button variant="contained" disabled={!ready || save.isPending} onClick={() => save.mutate()}>
          {save.isPending ? 'Recording…' : choice.how === 'WITH_APPROVAL' ? 'Propose for approval' : 'Record my decision'}
        </Button>
      </DialogActions>
    </Dialog>
  )
}

// ── From the ground ──────────────────────────────────────────────────────────

const GROUND_WORDS = { ACCEPTED: 'Accepted — on the way', ARRIVED: 'Arrived' }

/** What the person dealing with it reports. Shown apart from decisions: a report is not one. */
function Ground({ situationId }: { situationId: string }) {
  const { data } = useQuery({
    queryKey: ['intel-observations', situationId], queryFn: () => getObservations(situationId), refetchInterval: 20_000 })
  if (!data?.length) return null
  return (
    <GlassCard sx={{ p: 2, mb: 2 }}>
      <Typography variant="subtitle1" sx={{ fontWeight: 600, mb: 0.5 }}>From the ground</Typography>
      <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1 }}>
        Reports by the people dealing with it. A report is not a decision and changes nothing else.
      </Typography>
      {data.map((o) => (
        <Box key={o.id} data-testid="ground-report" sx={{ py: 0.5, borderBottom: '1px solid rgba(255,255,255,0.08)' }}>
          <Typography variant="caption" color="text.secondary">
            {fmtTime(o.observed_at)} · {o.name ?? 'A former user'} ({ROLE_LABEL[o.role_id] ?? `Role ${o.role_id}`})
            {o.via === 'mobile' ? ' · from the phone' : ''}{o.latitude != null ? ' · with position' : ''}
          </Typography>
          <Typography variant="body2">{o.kind === 'OBSERVATION' ? o.note : GROUND_WORDS[o.kind]}</Typography>
        </Box>
      ))}
    </GlassCard>
  )
}

// ── Decision history ─────────────────────────────────────────────────────────

function History({ trail }: { trail?: Trail }) {
  return (
    <GlassCard sx={{ p: 2 }}>
      <Typography variant="subtitle1" sx={{ fontWeight: 600, mb: 1 }}>Decision history</Typography>
      {!trail ? <Skeleton height={100} /> : (
        <>
          {trail.reviews.map((r, i) => (
            <Typography key={i} variant="caption" color="text.secondary" sx={{ display: 'block' }}>
              {fmtTime(r.viewed_at)} · {r.name ?? 'A former user'} ({ROLE_LABEL[r.role_id] ?? `Role ${r.role_id}`})
              {' '}looked at what was suggested</Typography>))}
          {!trail.decisions.length
            ? <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>Nobody has decided anything yet.</Typography>
            : <Box sx={{ mt: 1 }}>{trail.decisions.map((d) => <DecisionCard key={d.id} d={d} />)}</Box>}
          <Divider sx={{ my: 1 }} />
          <Typography variant="body2">Stands: {STATUS_LABEL[trail.decision_status]}
            {trail.closed_at ? ` · closed ${fmt(trail.closed_at)}` : ''}</Typography>
          <Typography variant="caption" color="text.secondary">{INCIDENT_LABEL[trail.incident.state]}
            {trail.incident.status ? ` · ${trail.incident.status}` : ''}</Typography>
        </>
      )}
    </GlassCard>
  )
}
