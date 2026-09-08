/**
 * Key Register — the cabinet, and what is currently out of it.
 *
 * The page opens on the exception, not the inventory. A supervisor checking
 * this at 07:00 wants "what is still out and who has it", and that answer is
 * three keys out of ninety — so the outstanding list sits at the top and the
 * cabinet grid below it. Sorting the cabinet alphabetically and asking
 * somebody to spot the issued ones is how a paper book already works.
 */
import { useMemo, useState } from 'react'
import {
  Alert, Box, Button, Chip, Dialog, DialogActions, DialogContent, DialogTitle,
  IconButton, MenuItem, Skeleton, Tab, Tabs, TextField, Tooltip, Typography,
} from '@mui/material'
import Stack from '@/components/common/Stack'
import AddIcon from '@mui/icons-material/Add'
import VpnKeyIcon from '@mui/icons-material/VpnKey'
import LoginIcon from '@mui/icons-material/Login'
import LogoutIcon from '@mui/icons-material/Logout'
import EditIcon from '@mui/icons-material/Edit'
import HistoryIcon from '@mui/icons-material/History'
import WarningAmberIcon from '@mui/icons-material/WarningAmber'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import {
  createKey, getOutstandingKeys, issueKey, listKeyTransactions, listKeys,
  returnKey, updateKey, type OutstandingKey, type SiteKey,
} from '@/api/guardhouse'
import { getSites } from '@/api/sites'
import { GlassCard } from '@/components/common/GlassCard'
import { PageHeader } from '@/components/common/PageHeader'
import { FilterRail } from '@/components/common/FilterRail'
import { useAuthStore } from '@/store/auth'

const CAN_MANAGE = new Set([1, 2, 3, 8])

function fmt(ts: string | null) {
  if (!ts) return '—'
  return new Date(ts).toLocaleString(undefined, {
    day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit',
  })
}

function outFor(hours: number) {
  if (hours < 1) return `${Math.max(1, Math.round(hours * 60))} min`
  if (hours < 48) return `${hours.toFixed(1)} h`
  return `${Math.floor(hours / 24)} days`
}

// ── Outstanding ──────────────────────────────────────────────────────────────

function OutstandingRow({ row, onReturn }: { row: OutstandingKey; onReturn: () => void }) {
  return (
    <Stack
      direction="row" alignItems="center" spacing={1.25}
      sx={{
        px: 1.25, py: 0.9, borderRadius: '8px',
        background: row.is_overdue ? 'rgba(255,69,96,0.10)' : 'rgba(255,255,255,0.03)',
        border: `1px solid ${row.is_overdue ? 'rgba(255,69,96,0.42)' : 'rgba(255,255,255,0.07)'}`,
      }}
    >
      <VpnKeyIcon sx={{ fontSize: 17, color: row.is_overdue ? '#FF4560' : 'text.secondary' }} />
      <Box sx={{ minWidth: 96 }}>
        <Typography variant="body2" sx={{ fontWeight: 800, fontSize: '0.8rem' }}>
          {row.key_code}
        </Typography>
        <Typography variant="caption" noWrap sx={{ color: 'text.secondary', fontSize: '0.63rem' }}>
          {row.label}
        </Typography>
      </Box>
      <Box sx={{ flex: 1, minWidth: 0 }}>
        <Typography variant="body2" noWrap sx={{ fontSize: '0.8rem' }}>
          {row.held_by_name || 'Unnamed holder'}
          {row.held_by_company ? ` · ${row.held_by_company}` : ''}
        </Typography>
        <Typography variant="caption" noWrap sx={{ display: 'block', color: 'text.secondary', fontSize: '0.63rem' }}>
          {row.site_name} · out {outFor(Number(row.hours_out))}
          {row.purpose ? ` · ${row.purpose}` : ''}
        </Typography>
      </Box>
      {row.is_overdue && (
        <Chip
          size="small" label={`due ${fmt(row.expected_return_at)}`}
          sx={{ height: 18, fontSize: '0.6rem', bgcolor: 'rgba(255,69,96,0.18)', color: '#FF4560' }}
        />
      )}
      <Button size="small" variant="outlined" startIcon={<LoginIcon sx={{ fontSize: 15 }} />}
              onClick={onReturn}>
        Receive
      </Button>
    </Stack>
  )
}

// ── Cabinet ──────────────────────────────────────────────────────────────────

function KeyTile({ row, canManage, onIssue, onReturn, onEdit, onHistory }: {
  row: SiteKey
  canManage: boolean
  onIssue: () => void
  onReturn: () => void
  onEdit: () => void
  onHistory: () => void
}) {
  return (
    <GlassCard sx={{
      p: 1.25,
      border: row.is_overdue
        ? '1px solid rgba(255,69,96,0.42)'
        : row.is_out ? '1px solid rgba(245,165,36,0.38)' : undefined,
      opacity: row.is_active ? 1 : 0.55,
    }}>
      <Stack direction="row" alignItems="center" spacing={0.75} sx={{ mb: 0.75 }}>
        <Typography variant="subtitle2" sx={{ fontWeight: 800, fontSize: '0.85rem' }}>
          {row.key_code}
        </Typography>
        {row.cabinet_position && (
          <Chip size="small" variant="outlined" label={row.cabinet_position}
                sx={{ height: 17, fontSize: '0.58rem' }} />
        )}
        <Box sx={{ flex: 1 }} />
        {!row.is_active && (
          <Chip size="small" label="retired" sx={{ height: 17, fontSize: '0.58rem' }} />
        )}
        {row.is_overdue && (
          <Tooltip title={`Due back ${fmt(row.expected_return_at)}`}>
            <WarningAmberIcon sx={{ fontSize: 16, color: '#FF4560' }} />
          </Tooltip>
        )}
      </Stack>

      <Typography variant="body2" noWrap sx={{ fontSize: '0.78rem', mb: 0.25 }}>
        {row.label}
      </Typography>
      <Typography variant="caption" noWrap sx={{ display: 'block', color: 'text.secondary', fontSize: '0.63rem', mb: 1 }}>
        {row.site_name}
      </Typography>

      {row.is_out ? (
        <Box sx={{
          px: 1, py: 0.6, mb: 1, borderRadius: '6px',
          background: 'rgba(245,165,36,0.10)', border: '1px solid rgba(245,165,36,0.28)',
        }}>
          <Typography variant="caption" sx={{ display: 'block', fontWeight: 700, fontSize: '0.63rem' }}>
            {row.held_by_name}
          </Typography>
          <Typography variant="caption" sx={{ display: 'block', color: 'text.secondary', fontSize: '0.6rem' }}>
            since {fmt(row.issued_at)}
          </Typography>
        </Box>
      ) : (
        <Typography variant="caption" sx={{ display: 'block', color: 'text.secondary', fontSize: '0.63rem', mb: 1 }}>
          In the cabinet
        </Typography>
      )}

      <Stack direction="row" spacing={0.5}>
        {row.is_out ? (
          <Button size="small" variant="outlined" fullWidth
                  startIcon={<LoginIcon sx={{ fontSize: 15 }} />} onClick={onReturn}>
            Receive
          </Button>
        ) : (
          <Button size="small" variant="outlined" fullWidth disabled={!row.is_active}
                  startIcon={<LogoutIcon sx={{ fontSize: 15 }} />} onClick={onIssue}>
            Issue
          </Button>
        )}
        <Tooltip title="History">
          <IconButton size="small" onClick={onHistory} aria-label={`History for key ${row.key_code}`}>
            <HistoryIcon sx={{ fontSize: 17 }} />
          </IconButton>
        </Tooltip>
        {canManage && (
          <Tooltip title="Edit">
            <IconButton size="small" onClick={onEdit} aria-label={`Edit key ${row.key_code}`}>
              <EditIcon sx={{ fontSize: 17 }} />
            </IconButton>
          </Tooltip>
        )}
      </Stack>
    </GlassCard>
  )
}

// ── Dialogs ──────────────────────────────────────────────────────────────────

function IssueDialog({ row, onClose }: { row: SiteKey | null; onClose: () => void }) {
  const qc = useQueryClient()
  const [name, setName] = useState('')
  const [company, setCompany] = useState('')
  const [contact, setContact] = useState('')
  const [due, setDue] = useState('')
  const [purpose, setPurpose] = useState('')
  const [error, setError] = useState('')

  const issue = useMutation({
    mutationFn: () => issueKey({
      key_id: row!.id,
      issued_to_name: name.trim(),
      issued_to_company: company.trim() || null,
      issued_to_contact: contact.trim() || null,
      // datetime-local has no zone; the browser's own offset is the right one.
      expected_return_at: due ? new Date(due).toISOString() : null,
      purpose: purpose.trim() || null,
    }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['keys'] })
      qc.invalidateQueries({ queryKey: ['keys-outstanding'] })
      onClose()
    },
    onError: (e: { response?: { data?: { detail?: string } } }) =>
      setError(e.response?.data?.detail || 'Could not issue this key'),
  })

  if (!row) return null
  return (
    <Dialog open onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle sx={{ pb: 1 }}>
        <Typography variant="subtitle1" sx={{ fontWeight: 800 }}>
          Issue {row.key_code} — {row.label}
        </Typography>
        <Typography variant="caption" color="text.secondary">{row.site_name}</Typography>
      </DialogTitle>
      <DialogContent dividers>
        {error && <Alert severity="error" sx={{ mb: 1.5 }}>{error}</Alert>}
        <Stack spacing={1.5} sx={{ mt: 0.5 }}>
          <TextField
            size="small" label="Issued to" value={name} autoFocus
            onChange={(e) => setName(e.target.value)}
            helperText="The person taking the key — staff, contractor or tenant"
          />
          <TextField size="small" label="Company (optional)" value={company}
                     onChange={(e) => setCompany(e.target.value)} />
          <TextField size="small" label="Contact number (optional)" value={contact}
                     onChange={(e) => setContact(e.target.value)} />
          <TextField
            size="small" type="datetime-local" label="Expected back" value={due}
            onChange={(e) => setDue(e.target.value)}
            slotProps={{ inputLabel: { shrink: true } }}
            helperText="Leave blank if there is no agreed time"
          />
          <TextField size="small" label="Purpose (optional)" value={purpose}
                     onChange={(e) => setPurpose(e.target.value)} />
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" disabled={!name.trim() || issue.isPending}
                onClick={() => { setError(''); issue.mutate() }}>
          Issue key
        </Button>
      </DialogActions>
    </Dialog>
  )
}

function ReturnDialog({ transactionId, label, onClose }: {
  transactionId: string | null; label: string; onClose: () => void
}) {
  const qc = useQueryClient()
  const [notes, setNotes] = useState('')
  const [error, setError] = useState('')

  const give = useMutation({
    mutationFn: () => returnKey(transactionId!, notes.trim() || undefined),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['keys'] })
      qc.invalidateQueries({ queryKey: ['keys-outstanding'] })
      onClose()
    },
    onError: (e: { response?: { data?: { detail?: string } } }) =>
      setError(e.response?.data?.detail || 'Could not receive this key'),
  })

  if (!transactionId) return null
  return (
    <Dialog open onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle>Receive {label}</DialogTitle>
      <DialogContent dividers>
        {error && <Alert severity="error" sx={{ mb: 1.5 }}>{error}</Alert>}
        <TextField
          size="small" fullWidth multiline rows={2} label="Notes (optional)" value={notes}
          onChange={(e) => setNotes(e.target.value)} sx={{ mt: 0.5 }}
          helperText="Anything worth recording — damage, a late return, who brought it back"
        />
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" disabled={give.isPending}
                onClick={() => { setError(''); give.mutate() }}>
          Receive key
        </Button>
      </DialogActions>
    </Dialog>
  )
}

function KeyEditorDialog({ row, sites, onClose }: {
  row: SiteKey | null
  sites: { id: string; name: string }[]
  onClose: () => void
}) {
  const qc = useQueryClient()
  const [siteId, setSiteId] = useState(row?.site_id || '')
  const [code, setCode] = useState(row?.key_code || '')
  const [label, setLabel] = useState(row?.label || '')
  const [position, setPosition] = useState(row?.cabinet_position || '')
  const [notes, setNotes] = useState(row?.notes || '')
  const [error, setError] = useState('')

  const save = useMutation({
    mutationFn: () => row
      ? updateKey(row.id, {
          key_code: code.trim(), label: label.trim(),
          cabinet_position: position.trim() || null, notes: notes.trim() || null,
        })
      : createKey({
          site_id: siteId, key_code: code.trim(), label: label.trim(),
          cabinet_position: position.trim() || null, notes: notes.trim() || null,
        }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['keys'] }); onClose() },
    onError: (e: { response?: { data?: { detail?: string } } }) =>
      setError(e.response?.data?.detail || 'Could not save this key'),
  })

  const retire = useMutation({
    mutationFn: () => updateKey(row!.id, { is_active: !row!.is_active }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['keys'] }); onClose() },
    onError: (e: { response?: { data?: { detail?: string } } }) =>
      setError(e.response?.data?.detail || 'Could not change this key'),
  })

  return (
    <Dialog open onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle>{row ? `Edit ${row.key_code}` : 'Add a key'}</DialogTitle>
      <DialogContent dividers>
        {error && <Alert severity="error" sx={{ mb: 1.5 }}>{error}</Alert>}
        <Stack spacing={1.5} sx={{ mt: 0.5 }}>
          {!row && (
            <TextField select size="small" label="Site" value={siteId}
                       onChange={(e) => setSiteId(e.target.value)}>
              {sites.map((s) => <MenuItem key={s.id} value={s.id}>{s.name}</MenuItem>)}
            </TextField>
          )}
          <TextField
            size="small" label="Key code" value={code} onChange={(e) => setCode(e.target.value)}
            helperText="Whatever is stencilled on the fob"
          />
          <TextField size="small" label="What it opens" value={label}
                     onChange={(e) => setLabel(e.target.value)} />
          <TextField
            size="small" label="Cabinet position" value={position}
            onChange={(e) => setPosition(e.target.value)}
            helperText="Where it hangs — how the next shift knows it is missing"
          />
          <TextField size="small" multiline rows={2} label="Notes" value={notes}
                     onChange={(e) => setNotes(e.target.value)} />
        </Stack>
      </DialogContent>
      <DialogActions>
        {row && (
          <Button color={row.is_active ? 'error' : 'primary'} sx={{ mr: 'auto' }}
                  disabled={retire.isPending} onClick={() => { setError(''); retire.mutate() }}>
            {row.is_active ? 'Retire' : 'Return to service'}
          </Button>
        )}
        <Button onClick={onClose}>Cancel</Button>
        <Button
          variant="contained"
          disabled={!code.trim() || !label.trim() || (!row && !siteId) || save.isPending}
          onClick={() => { setError(''); save.mutate() }}
        >
          Save
        </Button>
      </DialogActions>
    </Dialog>
  )
}

function HistoryDialog({ row, onClose }: { row: SiteKey | null; onClose: () => void }) {
  const { data = [], isLoading } = useQuery({
    queryKey: ['key-transactions', row?.id],
    queryFn: () => listKeyTransactions({ key_id: row!.id }),
    enabled: Boolean(row),
  })
  if (!row) return null
  return (
    <Dialog open onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle sx={{ pb: 1 }}>
        <Typography variant="subtitle1" sx={{ fontWeight: 800 }}>
          {row.key_code} — {row.label}
        </Typography>
        <Typography variant="caption" color="text.secondary">
          Every time this key left the cabinet
        </Typography>
      </DialogTitle>
      <DialogContent dividers>
        {isLoading ? (
          <Stack spacing={1}>
            {[0, 1, 2].map((i) => <Skeleton key={i} variant="rounded" height={52} />)}
          </Stack>
        ) : data.length === 0 ? (
          <Typography color="text.secondary" variant="body2">
            This key has never been issued.
          </Typography>
        ) : (
          <Stack spacing={0.75}>
            {data.map((t) => (
              <Box key={t.id} sx={{
                px: 1.25, py: 0.8, borderRadius: '8px',
                background: 'rgba(255,255,255,0.03)',
                border: '1px solid rgba(255,255,255,0.07)',
              }}>
                <Stack direction="row" alignItems="center" spacing={1}>
                  <Typography variant="body2" sx={{ fontWeight: 700, fontSize: '0.8rem', flex: 1 }}>
                    {t.held_by_name}{t.held_by_company ? ` · ${t.held_by_company}` : ''}
                  </Typography>
                  <Chip
                    size="small"
                    label={t.returned_at ? 'returned' : 'still out'}
                    sx={{
                      height: 17, fontSize: '0.58rem',
                      bgcolor: t.returned_at ? undefined : 'rgba(245,165,36,0.18)',
                    }}
                  />
                </Stack>
                <Typography variant="caption" sx={{ display: 'block', color: 'text.secondary', fontSize: '0.63rem' }}>
                  Out {fmt(t.issued_at)} by {t.issued_by_name || 'unknown'}
                  {t.returned_at ? ` · back ${fmt(t.returned_at)} to ${t.received_by_name || 'unknown'}` : ''}
                </Typography>
                {(t.purpose || t.return_notes) && (
                  <Typography variant="caption" sx={{ display: 'block', color: 'text.secondary', fontSize: '0.63rem' }}>
                    {[t.purpose, t.return_notes].filter(Boolean).join(' · ')}
                  </Typography>
                )}
              </Box>
            ))}
          </Stack>
        )}
      </DialogContent>
      <DialogActions><Button onClick={onClose}>Close</Button></DialogActions>
    </Dialog>
  )
}

// ── Page ─────────────────────────────────────────────────────────────────────

export function KeyRegisterPage() {
  const roleId = useAuthStore((s) => s.user?.roleId ?? 0)
  const canManage = CAN_MANAGE.has(roleId)

  const [siteFilter, setSiteFilter] = useState('')
  const [tab, setTab] = useState(0)
  const [issuing, setIssuing] = useState<SiteKey | null>(null)
  const [returning, setReturning] = useState<{ id: string; label: string } | null>(null)
  const [editing, setEditing] = useState<SiteKey | null>(null)
  const [adding, setAdding] = useState(false)
  const [history, setHistory] = useState<SiteKey | null>(null)

  const { data: sites = [] } = useQuery({ queryKey: ['sites'], queryFn: () => getSites() })

  const { data: keys = [], isLoading } = useQuery({
    queryKey: ['keys', siteFilter, tab],
    queryFn: () => listKeys({
      site_id: siteFilter || undefined,
      include_inactive: tab === 1,
    }),
    refetchInterval: 60_000,
  })

  const { data: outstanding } = useQuery({
    queryKey: ['keys-outstanding', siteFilter],
    queryFn: () => getOutstandingKeys(siteFilter || undefined),
    refetchInterval: 60_000,
  })

  const siteOptions = useMemo(
    () => [{ value: '', label: 'All sites' },
           ...sites.map((s) => ({ value: s.id, label: s.name }))],
    [sites],
  )

  const overdue = outstanding?.overdue ?? 0

  return (
    <Box sx={{ display: 'flex', gap: 1.5, alignItems: 'flex-start' }}>
      <Box sx={{ flex: 1, minWidth: 0 }}>
        <PageHeader
          pageKey="key-register"
          action={canManage ? (
            <Button size="small" variant="contained" startIcon={<AddIcon />}
                    onClick={() => setAdding(true)}>
              Add key
            </Button>
          ) : undefined}
        />

        {overdue > 0 && (
          <Alert severity="warning" sx={{ mb: 1.5 }}>
            {overdue} {overdue === 1 ? 'key is' : 'keys are'} past the time they were due back.
          </Alert>
        )}

        {outstanding && outstanding.out > 0 && (
          <GlassCard sx={{ p: 1.5, mb: 1.5 }}>
            <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 1 }}>
              <LogoutIcon sx={{ fontSize: 17, color: '#F5A524' }} />
              <Typography variant="subtitle2" sx={{ fontWeight: 800 }}>
                Out of the cabinet
              </Typography>
              <Chip size="small" label={outstanding.out} sx={{ height: 18, fontSize: '0.62rem' }} />
            </Stack>
            <Stack spacing={0.75}>
              {outstanding.keys.map((k) => (
                <OutstandingRow
                  key={k.id} row={k}
                  onReturn={() => setReturning({ id: k.id, label: `${k.key_code} — ${k.label}` })}
                />
              ))}
            </Stack>
          </GlassCard>
        )}

        <Tabs value={tab} onChange={(_, v) => setTab(v)} sx={{ minHeight: 34, mb: 1 }}>
          <Tab label="In service" sx={{ minHeight: 34, fontSize: '0.78rem' }} />
          <Tab label="Including retired" sx={{ minHeight: 34, fontSize: '0.78rem' }} />
        </Tabs>

        {isLoading ? (
          <Box sx={{ display: 'grid', gap: 1.25, gridTemplateColumns: 'repeat(auto-fill, minmax(230px, 1fr))' }}>
            {[0, 1, 2, 3, 4, 5].map((i) => <Skeleton key={i} variant="rounded" height={150} />)}
          </Box>
        ) : keys.length === 0 ? (
          <GlassCard sx={{ p: 4, textAlign: 'center' }}>
            <VpnKeyIcon sx={{ fontSize: 34, color: 'text.disabled', mb: 1 }} />
            <Typography color="text.secondary">
              No keys in the cabinet yet.
              {canManage ? ' Add the ones this site actually holds.' : ''}
            </Typography>
          </GlassCard>
        ) : (
          <Box sx={{
            display: 'grid', gap: 1.25, alignItems: 'start',
            gridTemplateColumns: 'repeat(auto-fill, minmax(230px, 1fr))',
          }}>
            {keys.map((row) => (
              <KeyTile
                key={row.id} row={row} canManage={canManage}
                onIssue={() => setIssuing(row)}
                onReturn={() => row.open_transaction_id && setReturning({
                  id: row.open_transaction_id, label: `${row.key_code} — ${row.label}`,
                })}
                onEdit={() => setEditing(row)}
                onHistory={() => setHistory(row)}
              />
            ))}
          </Box>
        )}
      </Box>

      <FilterRail
        storageKey="key-register"
        groups={[{
          key: 'site', label: 'Site', options: siteOptions,
          value: siteFilter, onChange: setSiteFilter,
        }]}
      />

      {issuing && <IssueDialog row={issuing} onClose={() => setIssuing(null)} />}
      {returning && (
        <ReturnDialog
          transactionId={returning.id} label={returning.label}
          onClose={() => setReturning(null)}
        />
      )}
      {(adding || editing) && (
        <KeyEditorDialog
          row={editing} sites={sites}
          onClose={() => { setAdding(false); setEditing(null) }}
        />
      )}
      {history && <HistoryDialog row={history} onClose={() => setHistory(null)} />}
    </Box>
  )
}

export default KeyRegisterPage
