/**
 * The decision card: what the AI is waiting on this person to decide, one at a time, beside the AI button.
 *
 * Design: docs/plans/2026-10-31-ai-corner-button-and-decision-popups-design.md (AI_MODULE.md, "Decisions that pop
 * up"). It asks GET /ai/waiting every minute and when the tab is shown again. A Confirm card the person left in the
 * chat comes first, as it expires; then the warnings they may decide, most important first. The buttons call the
 * same endpoints as the chat and AI Insights, so the same checks and audit log apply. Later puts one off for 4 hours
 * on this browser. Alert pop-ups (ToastContainer, z-index 2000) sit above it: it never covers an alert.
 */
import { useEffect, useState } from 'react'
import { Alert, Box, Button, Paper, Typography } from '@mui/material'
import AutoAwesomeIcon from '@mui/icons-material/AutoAwesome'
import { useNavigate } from 'react-router-dom'
import Stack from '@/components/common/Stack'
import { decideAiInsight } from '@/api/ai'
import type { AiInsight } from '@/api/ai'
import { cancelAction, confirmAction } from '@/api/aiAssistant'
import type { AssistantAction } from '@/api/aiAssistant'
import { apiError } from '@/api/platformDroneLicense'
import { decisionId } from './decisions'
import type { Decisions } from './decisions'

/** How long the answer to a decision stays before the next card. */
const NOTICE_MS = 4000

const shown = (v: unknown) => (v === null || v === undefined ? '' : typeof v === 'object' ? JSON.stringify(v) : String(v))

function confirmedText(a: AssistantAction): string {
  if (a.status === 'CONFIRMED') return `Done: ${a.title}.`
  if (a.status === 'CANCELLED') return 'Cancelled; nothing was changed.'
  if (a.status === 'EXPIRED') return 'Expired; nothing was changed.'
  return `Not done: ${shown(a.result?.answer)}`
}

interface Notice { text: string; ok: boolean; password?: string }

export function DecisionCard({ decisions, aboveButton }: { decisions: Decisions; aboveButton: boolean }) {
  const navigate = useNavigate()
  const [working, setWorking] = useState(false)
  const [notice, setNotice] = useState<Notice | null>(null)
  const item = decisions.current

  useEffect(() => {
    // A temporary password is shown until the person closes it: it is not kept anywhere.
    if (!notice || notice.password) return undefined
    const t = setTimeout(() => setNotice(null), NOTICE_MS)
    return () => clearTimeout(t)
  }, [notice])

  if (!item && !notice) return null

  const run = async (work: () => Promise<Notice>) => {
    setWorking(true)
    try {
      setNotice(await work())
    } catch (e) {
      // Someone else decided first, the card expired, or the person may no longer: the server says which.
      setNotice({ text: apiError(e), ok: false })
    } finally {
      setWorking(false)
      decisions.refresh()
    }
  }

  const confirm = (a: AssistantAction) => run(async () => {
    const out = await confirmAction(a.id)
    return { text: confirmedText(out), ok: out.status === 'CONFIRMED', password: out.temporary_password }
  })
  const cancel = (a: AssistantAction) => run(async () => ({ text: confirmedText(await cancelAction(a.id)), ok: true }))
  const decide = (x: AiInsight, decision: 'ACCEPT' | 'DISMISS') => run(async () => {
    await decideAiInsight(x.id, { decision })
    return { text: decision === 'ACCEPT' ? 'Accepted.' : 'Dismissed.', ok: true }
  })

  return (
    <Paper elevation={8} role="dialog" aria-label="The AI needs your decision" data-testid="ai-decision-card"
           sx={{ position: 'fixed', right: 24, bottom: aboveButton ? 96 : 24, width: 'min(360px, calc(100vw - 48px))',
                 p: 2, borderRadius: 2, border: 1, borderColor: 'primary.main',
                 zIndex: (t) => t.zIndex.drawer - 1 }}>
      {notice ? (
        <>
          <Alert severity={notice.ok ? 'success' : 'warning'} sx={{ mb: notice.password ? 1 : 0 }}>{notice.text}</Alert>
          {notice.password && (
            <>
              <Alert severity="info" sx={{ mb: 1 }}>
                Temporary password, shown this once: <b>{notice.password}</b>. Give it to the person; it is not kept.
              </Alert>
              <Button size="small" variant="contained" onClick={() => setNotice(null)}>Close</Button>
            </>
          )}
        </>
      ) : item && (
        <>
          <Stack direction="row" sx={{ alignItems: 'center', gap: 0.75, mb: 0.5 }}>
            <AutoAwesomeIcon color="primary" sx={{ fontSize: 18 }} />
            <Typography variant="caption" color="text.secondary">
              {item.kind === 'action' ? 'Waiting for your Confirm'
                : [item.warning.feature_title, item.warning.site_name].filter(Boolean).join(' · ')}
            </Typography>
          </Stack>
          <Typography variant="subtitle2" sx={{ fontWeight: 600 }}>
            {item.kind === 'action' ? item.action.title : item.warning.title}
          </Typography>
          {item.kind === 'action' ? (
            <Box component="dl" sx={{ m: 0, mt: 0.5, display: 'grid', gridTemplateColumns: 'auto 1fr', columnGap: 1.5, rowGap: 0.25 }}>
              {item.action.fields.slice(0, 5).map((f) => (
                <Box key={f.name} sx={{ display: 'contents' }}>
                  <Typography component="dt" variant="caption" color="text.secondary">{f.name}</Typography>
                  <Typography component="dd" variant="caption" sx={{ m: 0, wordBreak: 'break-word' }}>{shown(f.value)}</Typography>
                </Box>
              ))}
            </Box>
          ) : (
            <Box component="ul" sx={{ m: 0, mt: 0.5, pl: 2 }}>
              {(item.warning.reasons.length ? item.warning.reasons.slice(0, 3).map((r) => r.words)
                : item.warning.detail ? [item.warning.detail] : []).map((words) => (
                <Typography component="li" variant="caption" key={words}>{words}</Typography>
              ))}
            </Box>
          )}
          <Stack direction="row" sx={{ gap: 1, mt: 1.5, flexWrap: 'wrap' }}>
            {item.kind === 'action' ? (
              <>
                <Button size="small" variant="contained" disabled={working} onClick={() => void confirm(item.action)}>Confirm</Button>
                <Button size="small" disabled={working} onClick={() => void cancel(item.action)}>Cancel</Button>
              </>
            ) : (
              <>
                <Button size="small" variant="contained" disabled={working} onClick={() => void decide(item.warning, 'ACCEPT')}>Accept</Button>
                <Button size="small" disabled={working} onClick={() => void decide(item.warning, 'DISMISS')}>Dismiss</Button>
              </>
            )}
            <Button size="small" color="inherit" disabled={working} onClick={() => decisions.putOff(decisionId(item))}>Later</Button>
            {item.kind === 'warning' && (
              <Button size="small" color="inherit" onClick={() => navigate('/ai-insights')}>Open AI Insights</Button>
            )}
          </Stack>
          {decisions.more > 0 && (
            <Typography variant="caption" color="text.secondary" component="div" sx={{ mt: 1 }}>
              and {decisions.more} more waiting
            </Typography>
          )}
        </>
      )}
    </Paper>
  )
}
