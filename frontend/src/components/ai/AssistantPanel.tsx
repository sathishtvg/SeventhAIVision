/**
 * The AI assistant, as a panel over any page: ask, and the reply is written as it arrives.
 *
 * Shown only to people the assistant is switched on for (AssistantButton asks /ai/status). It reads the app with
 * the person's own permissions; it never changes anything by itself.
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
import { useQuery, useQueryClient } from '@tanstack/react-query'
import Stack from '@/components/common/Stack'
import { getAiStatus } from '@/api/ai'
import { getAssistantUsage, getConversation, getConversations, streamChat } from '@/api/aiAssistant'
import type { AssistantEvent } from '@/api/aiAssistant'

interface Line { role: 'user' | 'assistant'; text: string; pending?: boolean }

export function AssistantPanel({ open, onClose }: { open: boolean; onClose: () => void }) {
  const qc = useQueryClient()
  const [lines, setLines] = useState<Line[]>([])
  const [conversationId, setConversationId] = useState<string | null>(null)
  const [draft, setDraft] = useState('')
  const [status, setStatus] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [showHistory, setShowHistory] = useState(false)
  const end = useRef<HTMLDivElement>(null)
  const usage = useQuery({ queryKey: ['ai-assistant-usage'], queryFn: getAssistantUsage, enabled: open, retry: false })
  const history = useQuery({ queryKey: ['ai-assistant-conversations'], queryFn: getConversations,
                             enabled: open && showHistory, retry: false })

  useEffect(() => { end.current?.scrollIntoView?.({ block: 'end' }) }, [lines, status])

  const fresh = () => { setLines([]); setConversationId(null); setError(null); setStatus(null); setShowHistory(false) }

  const reopen = async (id: string) => {
    const c = await getConversation(id)
    setConversationId(id)
    setLines(c.messages.map((m) => ({ role: m.role, text: m.text })))
    setShowHistory(false)
    setError(null)
  }

  const send = async () => {
    const message = draft.trim()
    if (!message || busy) return
    setDraft('')
    setError(null)
    setBusy(true)
    setLines((l) => [...l, { role: 'user', text: message }, { role: 'assistant', text: '', pending: true }])
    const onEvent = (e: AssistantEvent) => {
      if (e.kind === 'conversation') setConversationId(e.data.conversation_id)
      else if (e.kind === 'status') setStatus(e.data.text)
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
              It reads with your own permissions and never changes anything by itself.
            </Typography>
          )}
          {lines.map((l, i) => (
            <Box key={i} sx={{ display: 'flex', justifyContent: l.role === 'user' ? 'flex-end' : 'flex-start', mb: 1 }}>
              <Box sx={{ maxWidth: '85%', px: 1.5, py: 1, borderRadius: 2, whiteSpace: 'pre-wrap',
                         bgcolor: l.role === 'user' ? 'primary.main' : 'action.hover',
                         color: l.role === 'user' ? 'primary.contrastText' : 'text.primary' }}>
                <Typography variant="body2">{l.text || (l.pending ? '…' : '')}</Typography>
              </Box>
            </Box>
          ))}
          {status && <Chip size="small" label={status} sx={{ mb: 1 }} data-testid="assistant-status" />}
          <div ref={end} />
        </Box>
      )}
      <Box sx={{ p: 2, borderTop: 1, borderColor: 'divider' }}>
        {error && <Alert severity="warning" sx={{ mb: 1 }}>{error}</Alert>}
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
    </Drawer>
  )
}

/** The button that opens the assistant, on every page, for the people it is switched on for. */
export function AssistantButton() {
  const [open, setOpen] = useState(false)
  const status = useQuery({ queryKey: ['ai-status'], queryFn: getAiStatus, retry: false, staleTime: 60_000 })
  const mine = status.data?.features.some((f) => f.key === 'assistant' && f.mine)
  if (!mine) return null
  return (
    <>
      <Tooltip title="Ask the AI assistant">
        <Fab color="primary" aria-label="Ask the AI assistant" onClick={() => setOpen(true)}
             sx={{ position: 'fixed', right: 24, bottom: 24, zIndex: (t) => t.zIndex.drawer - 1 }}>
          <AutoAwesomeIcon />
        </Fab>
      </Tooltip>
      <AssistantPanel open={open} onClose={() => setOpen(false)} />
    </>
  )
}
