/**
 * Shared components of the intelligence screens.
 *
 * One rule runs through all of them: what the layer SUGGESTS and what a person
 * DECIDED never look alike. A suggestion is a dashed, violet-edged card marked
 * "AI suggestion — not a decision". A decision is a solid, green-edged card
 * that names the person. What the platform then did is a third thing, listed
 * under the decision it belongs to. None of them is ever drawn as another.
 */
import type { ReactNode } from 'react'
import { Box, Chip, LinearProgress, Table, TableBody, TableCell, TableRow, Tooltip, Typography } from '@mui/material'
import AutoAwesomeIcon from '@mui/icons-material/AutoAwesome'
import PersonIcon from '@mui/icons-material/Person'
import type {
  ActionRow, AssessmentConfidence, Decision, DecisionStatus, Factor, Recommendation, RiskLevel,
} from '@/api/securityIntelligence'
import {
  AI_COLOR, BASIS_LABEL, DECISION_LABEL, HUMAN_COLOR, RISK_COLOR, ROLE_LABEL, STATE_LABEL, STATUS_LABEL,
  STEP_LABEL, fmtTime, pct,
} from './intelFormat'

type ChipColor = 'default' | 'success' | 'error' | 'warning' | 'info' | 'primary' | 'secondary'

/** The layer's risk, or that there is none yet — never a guess. */
export function RiskChip({ level, score }: { level: RiskLevel | null | undefined; score?: number | null }) {
  if (!level) return <Chip size="small" variant="outlined" label="Not assessed yet" />
  return (
    <Chip size="small" label={score != null ? `${level} · ${score}` : level}
          sx={{ bgcolor: `${RISK_COLOR[level]}22`, color: RISK_COLOR[level], border: `1px solid ${RISK_COLOR[level]}66`,
                fontWeight: 700 }} />
  )
}

const STATUS_COLOR: Record<DecisionStatus, ChipColor> = {
  AWAITING: 'error', ACKNOWLEDGED: 'warning', IN_HAND: 'info', PENDING_APPROVAL: 'warning',
  ASSISTANCE_REQUESTED: 'error', RESOLVED: 'success', FALSE_POSITIVE: 'default',
}

/** Where a situation stands with the people responsible for it. */
export function DecisionStatusChip({ status }: { status: DecisionStatus }) {
  return <Chip size="small" color={STATUS_COLOR[status] ?? 'default'} label={STATUS_LABEL[status] ?? status} />
}

/** The mark on everything the layer says. */
export function AiMark({ children = 'AI' }: { children?: ReactNode }) {
  return (
    <Box component="span" sx={{ display: 'inline-flex', alignItems: 'center', gap: 0.5, color: AI_COLOR,
                                fontSize: '0.68rem', fontWeight: 700, letterSpacing: '0.06em', textTransform: 'uppercase' }}>
      <AutoAwesomeIcon sx={{ fontSize: 14 }} />{children}
    </Box>
  )
}

/** The mark on everything a person did. */
export function HumanMark({ children = 'Human decision' }: { children?: ReactNode }) {
  return (
    <Box component="span" sx={{ display: 'inline-flex', alignItems: 'center', gap: 0.5, color: HUMAN_COLOR,
                                fontSize: '0.68rem', fontWeight: 700, letterSpacing: '0.06em', textTransform: 'uppercase' }}>
      <PersonIcon sx={{ fontSize: 14 }} />{children}
    </Box>
  )
}

function ConfidenceRow({ label, value, hint }: { label: string; value: number | null | undefined; hint: string }) {
  return (
    <Tooltip title={hint} placement="left">
      <Box sx={{ display: 'grid', gridTemplateColumns: '210px 1fr 48px', alignItems: 'center', gap: 1, py: 0.4 }}>
        <Typography variant="body2">{label}</Typography>
        {value == null
          ? <Typography variant="caption" color="text.secondary">not given by any source</Typography>
          : <LinearProgress variant="determinate" value={Math.round(value * 100)} sx={{ height: 6, borderRadius: 3 }} />}
        <Typography variant="body2" sx={{ fontWeight: 700, textAlign: 'right' }}>{pct(value)}</Typography>
      </Box>
    </Tooltip>
  )
}

/**
 * The confidences, each under its own name and on its own line. They are four
 * different things and are never added, averaged or shown as one number.
 */
export function Confidences({ confidence, recommendation }: {
  confidence: AssessmentConfidence; recommendation?: number | null
}) {
  return (
    <Box aria-label="Confidences">
      <ConfidenceRow label="Detection confidence" value={confidence.detection}
                     hint="How sure the detector was about what it saw. Copied from the model, unaltered." />
      <ConfidenceRow label="Correlation confidence" value={confidence.correlation}
                     hint="How firmly these events belong together: the weakest link between them." />
      <ConfidenceRow label="Risk confidence" value={confidence.risk}
                     hint="How complete the picture was when the risk was scored. Each thing not known lowers it." />
      {recommendation !== undefined && (
        <ConfidenceRow label="Recommendation confidence" value={recommendation}
                       hint="How sure the layer is of its first suggestion." />)}
    </Box>
  )
}

/** Every factor behind a score, with its points and its sentence. */
export function FactorTable({ factors }: { factors: Factor[] }) {
  if (!factors.length) return <Typography variant="body2" color="text.secondary">No factors recorded.</Typography>
  return (
    <Table size="small">
      <TableBody>
        {factors.map((f, i) => (
          <TableRow key={i}>
            <TableCell sx={{ pl: 0, width: 28, color: f.points < 0 ? 'success.main' : f.points > 0 ? 'warning.main' : 'text.secondary' }}>
              {f.points > 0 ? '▲' : f.points < 0 ? '▼' : '·'}</TableCell>
            <TableCell sx={{ pl: 0 }}><Typography variant="body2">{f.detail}</Typography></TableCell>
            <TableCell align="right" sx={{ pr: 0, fontWeight: 700, whiteSpace: 'nowrap',
                                           color: f.points < 0 ? 'success.main' : 'inherit' }}>
              {f.points > 0 ? `+${f.points}` : f.points}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}

const LIMIT_WORDS: Record<Recommendation['limited_by'], string> = {
  RULE: "the rule's own certainty", DETECTION: 'the detection confidence',
  CORRELATION: 'the correlation confidence', RISK: 'the risk confidence',
}

/** One thing the layer suggests. Dashed and violet: it has not happened. */
export function SuggestionCard({ rec, first = false, children }: {
  rec: Recommendation; first?: boolean; children?: ReactNode
}) {
  return (
    <Box data-testid="ai-suggestion"
         sx={{ p: 1.5, mb: 1, borderRadius: 2, border: `1px dashed ${AI_COLOR}99`, bgcolor: `${AI_COLOR}0f`,
               opacity: rec.available ? 1 : 0.6 }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 0.5, flexWrap: 'wrap' }}>
        <AiMark>{first ? 'AI suggests' : 'AI also suggests'}</AiMark>
        <Typography variant="subtitle2" sx={{ fontWeight: 700 }}>{DECISION_LABEL[rec.action]}</Typography>
        <Chip size="small" variant="outlined" label={rec.priority} sx={{ height: 18, fontSize: '0.62rem' }} />
        {!rec.available && <Chip size="small" color="default" label="Not possible now" sx={{ height: 18, fontSize: '0.62rem' }} />}
      </Box>
      <Typography variant="body2">{rec.available ? rec.reason : rec.unavailable_reason}</Typography>
      {!rec.available && <Typography variant="caption" color="text.secondary">Would have been suggested: {rec.reason}</Typography>}
      <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 0.5 }}>
        Recommendation confidence {pct(rec.recommendation_confidence)} — held to {LIMIT_WORDS[rec.limited_by]}.
        {' '}A suggestion, not a decision.
      </Typography>
      {children}
    </Box>
  )
}

const RESULT_COLOR: Record<ActionRow['result'], ChipColor> = {
  OK: 'success', FAILED: 'error', SKIPPED: 'default', RECORDED: 'default',
}
const RESULT_LABEL: Record<ActionRow['result'], string> = {
  OK: 'Done', FAILED: 'Failed', SKIPPED: 'Nothing to do', RECORDED: 'Recorded',
}

/** What the platform did for a decision. A third thing: neither suggested nor decided. */
export function ActionList({ actions }: { actions: ActionRow[] }) {
  if (!actions.length) {
    return <Typography variant="caption" color="text.secondary">Nothing has been carried out.</Typography>
  }
  return (
    <Box data-testid="actions-carried-out">
      {actions.map((a) => (
        <Box key={a.sequence} sx={{ display: 'flex', alignItems: 'baseline', gap: 1, py: 0.25, flexWrap: 'wrap' }}>
          <Chip size="small" color={RESULT_COLOR[a.result]} variant={a.result === 'OK' ? 'filled' : 'outlined'}
                label={RESULT_LABEL[a.result]} sx={{ height: 18, fontSize: '0.62rem' }} />
          {/* A decision that was only recorded says so once, in its own sentence. */}
          {a.action !== 'NONE' && <Typography variant="body2">{STEP_LABEL[a.action] ?? a.action}</Typography>}
          {a.detail && <Typography variant={a.action === 'NONE' ? 'body2' : 'caption'} color="text.secondary">
            {a.detail}</Typography>}
          <Typography variant="caption" color="text.secondary" sx={{ ml: 'auto' }}>{fmtTime(a.executed_at)}</Typography>
        </Box>
      ))}
    </Box>
  )
}

const personText = (p: { name: string | null; role_id: number }) =>
  `${p.name ?? 'A former user'} · ${ROLE_LABEL[p.role_id] ?? `Role ${p.role_id}`}`

/** One decision a person made. Solid and green, and it names them. */
export function DecisionCard({ d, children }: { d: Decision; children?: ReactNode }) {
  const waiting = d.state === 'PENDING_APPROVAL'
  return (
    <Box data-testid="human-decision"
         sx={{ p: 1.5, mb: 1, borderRadius: 2, border: `1px solid ${HUMAN_COLOR}99`, bgcolor: `${HUMAN_COLOR}0d` }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 0.5, flexWrap: 'wrap' }}>
        <HumanMark>{waiting ? 'Proposed' : 'Decided'}</HumanMark>
        <Typography variant="subtitle2" sx={{ fontWeight: 700 }}>{DECISION_LABEL[d.action]}</Typography>
        <Chip size="small" color={d.is_override ? 'warning' : 'default'} variant="outlined" label={BASIS_LABEL[d.basis]}
              sx={{ height: 18, fontSize: '0.62rem' }} />
        <Chip size="small" color={d.state === 'REJECTED' ? 'error' : waiting ? 'warning' : 'success'}
              variant="outlined" label={STATE_LABEL[d.state]} sx={{ height: 18, fontSize: '0.62rem' }} />
        <Typography variant="caption" color="text.secondary" sx={{ ml: 'auto' }}>{fmtTime(d.decided_at)}</Typography>
      </Box>
      <Typography variant="body2">{personText(d.decided_by)}{d.via === 'mobile' ? ' · from the phone' : ''}</Typography>
      {d.suggested_action && d.suggested_action !== d.action && (
        <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
          The layer had suggested {DECISION_LABEL[d.suggested_action]} first.</Typography>)}
      {d.reason && <Typography variant="body2" sx={{ mt: 0.5 }}>Reason: {d.reason}</Typography>}
      {d.note && <Typography variant="body2" color="text.secondary">“{d.note}”</Typography>}
      {d.decided_on_an_earlier_assessment && (
        <Typography variant="caption" color="warning.main" sx={{ display: 'block' }}>
          Decided on an earlier assessment than the latest.</Typography>)}
      {d.approval && (
        <Typography variant="body2" sx={{ mt: 0.5 }}>
          {d.approval.verdict === 'APPROVED' ? 'Approved' : 'Rejected'} by {personText(d.approval.by)}
          {d.approval.note ? ` — “${d.approval.note}”` : ''}</Typography>)}
      <Box sx={{ mt: 1, pt: 1, borderTop: '1px solid rgba(255,255,255,0.10)' }}>
        <Typography variant="caption" sx={{ fontWeight: 700, letterSpacing: '0.06em', textTransform: 'uppercase',
                                            color: 'text.secondary', fontSize: '0.62rem' }}>
          What the platform then did</Typography>
        <ActionList actions={d.actions} />
      </Box>
      {children}
    </Box>
  )
}
