/**
 * The AI assistant, as a panel over any page: ask, and the reply is written as it arrives.
 *
 * Shown only to people the assistant is switched on for (AssistantButton asks /ai/status). It reads the app with
 * the person's own permissions; it never changes anything by itself.
 *
 * People who may create staff, and for whom IC reading is switched on, can scan a new staff member's IC
 * (IcScanDialog); the details they checked are then given to the assistant, which asks for the rest.
 */
import { useEffect, useRef, useState } from 'react'
import {
  Alert, Box, Button, Chip, Drawer, Fab, IconButton, List, ListItemButton, ListItemText, TextField, Tooltip,
  Typography,
} from '@mui/material'
import AutoAwesomeIcon from '@mui/icons-material/AutoAwesome'
import CloseIcon from '@mui/icons-material/Close'
import HistoryIcon from '@mui/icons-material/History'
import AddCommentIcon from '@mui/icons-material/AddComment'
import BadgeIcon from '@mui/icons-material/Badge'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import Stack from '@/components/common/Stack'
import { getAiStatus } from '@/api/ai'
import {
  cancelAction, confirmAction, getActions, getAssistantUsage, getConversation, getConversations, streamChat,
} from '@/api/aiAssistant'
import type { AssistantAction, AssistantEvent } from '@/api/aiAssistant'
import { apiError } from '@/api/platformDroneLicense'
import { usePermission } from '@/hooks/usePermission'
import IcScanDialog from './IcScanDialog'

interface Line { role: 'user' | 'assistant' | 'card'; text: string; pending?: boolean; action?: AssistantAction }

const shown = (v: unknown) => (v === null || v === undefined ? '' : typeof v === 'object' ? JSON.stringify(v) : String(v))

/** A change the assistant prepared: exactly what will be sent, and nothing until Confirm. */
function ActionCard({ action, busy, onDecided }: { action: AssistantAction; busy: boolean; onDecided: (a: AssistantAction) => void }) {
  const [working, setWorking] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const decide = async (how: 'confirm' | 'cancel') => {
    setWorking(true)
    setError(null)
    try {
      onDecided(how === 'confirm' ? await confirmAction(action.id) : await cancelAction(action.id))
    } catch (e) {
      setError(apiError(e))
    } finally {
      setWorking(false)
    }
  }
  const done = action.status !== 'PENDING'
  return (
    <Box sx={{ border: 1, borderColor: done ? 'divider' : 'primary.main', borderRadius: 2, p: 1.5, mb: 1 }}
         data-testid={`assistant-card-${action.id}`}>
      <Typography variant="subtitle2" sx={{ fontWeight: 600 }}>{action.title}</Typography>
      <Box component="dl" sx={{ m: 0, mt: 0.5, display: 'grid', gridTemplateColumns: 'auto 1fr', columnGap: 1.5, rowGap: 0.25 }}>
        {action.fields.map((f) => (
          <Box key={f.name} sx={{ display: 'contents' }}>
            <Typography component="dt" variant="caption" color="text.secondary">{f.name}</Typography>
            <Typography component="dd" variant="caption" sx={{ m: 0, wordBreak: 'break-word' }}>{shown(f.value)}</Typography>
          </Box>
        ))}
      </Box>
      {action.temporary_password && (
        <Alert severity="info" sx={{ mt: 1 }}>
          Temporary password, shown this once: <b>{action.temporary_password}</b>. Give it to the person; it is not kept.
        </Alert>
      )}
      {error && <Alert severity="error" sx={{ mt: 1 }}>{error}</Alert>}
      {done ? (
        <Typography variant="caption" color="text.secondary" component="div" sx={{ mt: 1 }}>
          {action.status === 'CONFIRMED' ? 'Done.' : action.status === 'CANCELLED' ? 'Cancelled; nothing was changed.'
            : action.status === 'EXPIRED' ? 'Expired; nothing was changed.'
              : `Not done: ${shown((action.result as { answer?: unknown } | null)?.answer)}`}
        </Typography>
      ) : (
        <Stack direction="row" sx={{ gap: 1, mt: 1 }}>
          <Button size="small" variant="contained" disabled={busy || working} onClick={() => void decide('confirm')}>Confirm</Button>
          <Button size="small" disabled={busy || working} onClick={() => void decide('cancel')}>Cancel</Button>
        </Stack>
      )}
    </Box>
  )
}

export function AssistantPanel({ open, onClose, canReadIc = false }: { open: boolean; onClose: () => void; canReadIc?: boolean }) {
  const qc = useQueryClient()
  const [lines, setLines] = useState<Line[]>([])
  const [conversationId, setConversationId] = useState<string | null>(null)
  const [draft, setDraft] = useState('')
  const [status, setStatus] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [showHistory, setShowHistory] = useState(false)
  const [scanning, setScanning] = useState(false)
  // After a card is decided the assistant waits; this lets the person tell it to carry on in one press.
  const [carryOn, setCarryOn] = useState(false)
  const end = useRef<HTMLDivElement>(null)
  const usage = useQuery({ queryKey: ['ai-assistant-usage'], queryFn: getAssistantUsage, enabled: open, retry: false })
  const history = useQuery({ queryKey: ['ai-assistant-conversations'], queryFn: getConversations,
                             enabled: open && showHistory, retry: false })

  useEffect(() => { end.current?.scrollIntoView?.({ block: 'end' }) }, [lines, status])

  const fresh = () => {
    setLines([]); setConversationId(null); setError(null); setStatus(null); setShowHistory(false); setCarryOn(false)
  }

  const reopen = async (id: string) => {
    const [c, actions] = await Promise.all([getConversation(id), getActions(id)])
    setConversationId(id)
    setLines([...c.messages.map((m) => ({ role: m.role, text: m.text })),
              ...actions.filter((a) => a.status === 'PENDING').map((a) => ({ role: 'card' as const, text: '', action: a }))])
    setShowHistory(false)
    setError(null)
  }

  const send = async (text?: string) => {
    const message = (text ?? draft).trim()
    if (!message || busy) return
    if (text === undefined) setDraft('')
    setError(null)
    setCarryOn(false)
    setBusy(true)
    setLines((l) => [...l, { role: 'user', text: message }, { role: 'assistant', text: '', pending: true }])
    const onEvent = (e: AssistantEvent) => {
      if (e.kind === 'conversation') setConversationId(e.data.conversation_id)
      else if (e.kind === 'status') setStatus(e.data.text)
      else if (e.kind === 'confirm') {
        // The card goes before the reply that is still being written about it.
        setLines((l) => [...l.slice(0, -1), { role: 'card', text: '', action: e.data.action }, l[l.length - 1]])
      }
      else if (e.kind === 'delta') {
        setStatus(null)
        setLines((l) => l.map((x, i) => (i === l.length - 1 ? { ...x, text: x.text + e.data.text } : x)))
      } else if (e.kind === 'done') {
        setLines((l) => l.map((x, i) => (i === l.length - 1 ? { role: 'assistant', text: e.data.text } : x)))
      } else if (e.kind === 'error') {
        setError(e.data.message)
        setLines((l) => l.filter((x) => !x.pending || x.text))
      }
    }
    try {
      await streamChat({ message, conversation_id: conversationId }, onEvent)
    } catch {
      setError('The assistant could not be reached. Check the connection and try again.')
    } finally {
      setBusy(false)
      setStatus(null)
      setLines((l) => l.map((x) => ({ ...x, pending: false })))
      qc.invalidateQueries({ queryKey: ['ai-assistant-usage'] })
      qc.invalidateQueries({ queryKey: ['ai-assistant-conversations'] })
    }
  }

  return (
    <Drawer anchor="right" open={open} onClose={onClose}
            slotProps={{ paper: { sx: { width: { xs: '100%', sm: 440 }, display: 'flex', flexDirection: 'column' } } }}>
      <Stack direction="row" sx={{ alignItems: 'center', gap: 1, px: 2, py: 1.5, borderBottom: 1, borderColor: 'divider' }}>
        <AutoAwesomeIcon color="primary" />
        <Typography variant="h6" sx={{ flex: 1 }}>AI assistant</Typography>
        {canReadIc && (
          <Tooltip title="Scan a new staff member's IC">
            <IconButton aria-label="Scan an IC" onClick={() => setScanning(true)} disabled={busy}><BadgeIcon /></IconButton>
          </Tooltip>
        )}
        <Tooltip title="Earlier conversations">
          <IconButton aria-label="Earlier conversations" onClick={() => setShowHistory((v) => !v)}><HistoryIcon /></IconButton>
        </Tooltip>
        <Tooltip title="New conversation">
          <IconButton aria-label="New conversation" onClick={fresh}><AddCommentIcon /></IconButton>
        </Tooltip>
        <IconButton aria-label="Close" onClick={onClose}><CloseIcon /></IconButton>
      </Stack>
      {showHistory ? (
        <List sx={{ flex: 1, overflowY: 'auto' }} data-testid="assistant-history">
          {(history.data ?? []).map((c) => (
            <ListItemButton key={c.id} onClick={() => reopen(c.id)}>
              <ListItemText primary={c.title} secondary={new Date(c.updated_at).toLocaleString()} />
            </ListItemButton>
          ))}
          {history.data?.length === 0 && <Typography sx={{ p: 2 }} color="text.secondary">No conversations yet.</Typography>}
        </List>
      ) : (
        <Box sx={{ flex: 1, overflowY: 'auto', px: 2, py: 1.5 }} data-testid="assistant-messages">
          {lines.length === 0 && (
            <Typography variant="body2" color="text.secondary">
              Ask about your work in the app: who is on shift tonight, open incidents at a site, a guard&apos;s overtime.
              It works with your own permissions. When you ask for a change it shows you a card, and nothing
              changes until you press Confirm.
            </Typography>
          )}
          {lines.map((l, i) => (l.role === 'card' && l.action ? (
            <ActionCard key={l.action.id} action={l.action} busy={busy}
                        onDecided={(a) => {
                          setLines((all) => all.map((x) => (x.action?.id === a.id ? { ...x, action: a } : x)))
                          setCarryOn(true)
                        }} />
          ) : (
            <Box key={i} sx={{ display: 'flex', justifyContent: l.role === 'user' ? 'flex-end' : 'flex-start', mb: 1 }}>
              <Box sx={{ maxWidth: '85%', px: 1.5, py: 1, borderRadius: 2, whiteSpace: 'pre-wrap',
                         bgcolor: l.role === 'user' ? 'primary.main' : 'action.hover',
                         color: l.role === 'user' ? 'primary.contrastText' : 'text.primary' }}>
                <Typography variant="body2">{l.text || (l.pending ? '…' : '')}</Typography>
              </Box>
            </Box>
          )))}
          {status && <Chip size="small" label={status} sx={{ mb: 1 }} data-testid="assistant-status" />}
          <div ref={end} />
        </Box>
      )}
      <Box sx={{ p: 2, borderTop: 1, borderColor: 'divider' }}>
        {error && <Alert severity="warning" sx={{ mb: 1 }}>{error}</Alert>}
        {carryOn && !busy && (
          <Button size="small" variant="outlined" sx={{ mb: 1 }} onClick={() => void send('Carry on.')}>Carry on</Button>
        )}
        <Stack direction="row" sx={{ gap: 1, alignItems: 'flex-end' }}>
          <TextField fullWidth multiline maxRows={5} size="small" placeholder="Ask anything about your work"
                     value={draft} onChange={(e) => setDraft(e.target.value)}
                     onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); void send() } }}
                     slotProps={{ htmlInput: { maxLength: 4000, 'aria-label': 'Message to the assistant' } }} />
          <Button variant="contained" onClick={() => void send()} disabled={busy || !draft.trim()}>Send</Button>
        </Stack>
        {usage.data?.limit != null && (
          <Typography variant="caption" color="text.secondary" component="div" sx={{ mt: 0.75 }}>
            {usage.data.left} of {usage.data.limit} replies left this month for your organisation.
          </Typography>
        )}
      </Box>
      {canReadIc && (
        <IcScanDialog open={scanning} onClose={() => setScanning(false)} onUse={(message) => void send(message)} />
      )}
    </Drawer>
  )
}

/** The button that opens the assistant, on every page, for the people it is switched on for. */
export function AssistantButton() {
  const [open, setOpen] = useState(false)
  const status = useQuery({ queryKey: ['ai-status'], queryFn: getAiStatus, retry: false, staleTime: 60_000 })
  const mine = status.data?.features.some((f) => f.key === 'assistant' && f.mine)
  const mayCreateStaff = usePermission('user:create')
  const canReadIc = mayCreateStaff && !!status.data?.features.some((f) => f.key === 'ic_reading' && f.mine)
  if (!mine) return null
  return (
    <>
      <Tooltip title="Ask the AI assistant">
        <Fab color="primary" aria-label="Ask the AI assistant" onClick={() => setOpen(true)}
             sx={{ position: 'fixed', right: 24, bottom: 24, zIndex: (t) => t.zIndex.drawer - 1 }}>
          <AutoAwesomeIcon />
        </Fab>
      </Tooltip>
      <AssistantPanel open={open} onClose={() => setOpen(false)} canReadIc={canReadIc} />
    </>
  )
}
