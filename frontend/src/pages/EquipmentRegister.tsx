/**
 * Equipment & Uniform Register — company kit, and who is holding it.
 *
 * Two tabs because they are two different questions. Kit is serialised and
 * comes back: the tab opens on what is out. Uniform is quantity issued to a
 * person and mostly does not come back: that tab is organised by officer,
 * since "what does this person still have" is the only question anybody asks
 * of it — usually on their last day.
 */
import { useMemo, useState } from 'react'
import {
  Alert, Autocomplete, Box, Button, Chip, Dialog, DialogActions, DialogContent,
  DialogTitle, IconButton, MenuItem, Skeleton, Tab, Tabs, TextField, Tooltip,
  Typography,
} from '@mui/material'
import Stack from '@/components/common/Stack'
import AddIcon from '@mui/icons-material/Add'
import InventoryIcon from '@mui/icons-material/Inventory'
import LoginIcon from '@mui/icons-material/Login'
import LogoutIcon from '@mui/icons-material/Logout'
import EditIcon from '@mui/icons-material/Edit'
import CheckroomIcon from '@mui/icons-material/Checkroom'
import PersonSearchIcon from '@mui/icons-material/PersonSearch'
import WarningAmberIcon from '@mui/icons-material/WarningAmber'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import {
  createEquipment, getHeldByUser, getOutstandingEquipment, issueEquipment,
  issueUniform, listEquipment, listUniformIssues, receiveEquipment,
  returnUniform, updateEquipment,
  EQUIPMENT_CATEGORIES, EQUIPMENT_CONDITIONS, UNIFORM_TYPES,
  type EquipmentItem, type OutstandingEquipment, type UniformIssue,
} from '@/api/equipment'
import { getSites } from '@/api/sites'
import { getUsers } from '@/api/users'
import { GlassCard } from '@/components/common/GlassCard'
import { PageHeader } from '@/components/common/PageHeader'
import { FilterRail } from '@/components/common/FilterRail'
import { useAuthStore } from '@/store/auth'

const CAN_MANAGE = new Set([1, 2, 3, 8])
const CAN_ISSUE = new Set([1, 2, 3, 4, 8])

const CONDITION_COLOUR: Record<string, string> = {
  new: '#00D97E', good: '#00D97E', fair: '#F5A524',
  poor: '#F5A524', damaged: '#FF4560', lost: '#FF4560',
}

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

function apiError(e: unknown, fallback: string) {
  const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
  return detail || fallback
}

function tidy(value: string) {
  return value.replace(/_/g, ' ')
}

// ── Outstanding kit ──────────────────────────────────────────────────────────

function OutstandingRow({ row, canIssue, onReceive }: {
  row: OutstandingEquipment; canIssue: boolean; onReceive: () => void
}) {
  return (
    <Stack
      direction="row" alignItems="center" spacing={1.25}
      sx={{
        px: 1.25, py: 0.9, borderRadius: '8px',
        background: row.is_overdue ? 'rgba(255,69,96,0.10)' : 'rgba(255,255,255,0.03)',
        border: `1px solid ${row.is_overdue ? 'rgba(255,69,96,0.42)' : 'rgba(255,255,255,0.07)'}`,
      }}
    >
      <InventoryIcon sx={{ fontSize: 17, color: row.is_overdue ? '#FF4560' : 'text.secondary' }} />
      <Box sx={{ minWidth: 96 }}>
        <Typography variant="body2" sx={{ fontWeight: 800, fontSize: '0.8rem' }}>
          {row.asset_code}
        </Typography>
        <Typography variant="caption" noWrap sx={{ color: 'text.secondary', fontSize: '0.63rem' }}>
          {row.name}
        </Typography>
      </Box>
      <Box sx={{ flex: 1, minWidth: 0 }}>
        <Typography variant="body2" noWrap sx={{ fontSize: '0.8rem' }}>
          {row.held_by_name || 'Unknown officer'}
          {row.held_by_employee_code ? ` · ${row.held_by_employee_code}` : ''}
        </Typography>
        <Typography variant="caption" noWrap sx={{ display: 'block', color: 'text.secondary', fontSize: '0.63rem' }}>
          {row.site_name || 'Pool'} · out {outFor(Number(row.hours_out))}
          {row.purpose ? ` · ${row.purpose}` : ''}
        </Typography>
      </Box>
      {row.is_overdue && (
        <Chip size="small" label={`due ${fmt(row.expected_return_at)}`}
              sx={{ height: 18, fontSize: '0.6rem', bgcolor: 'rgba(255,69,96,0.18)', color: '#FF4560' }} />
      )}
      {canIssue && (
        <Button size="small" variant="outlined" startIcon={<LoginIcon sx={{ fontSize: 15 }} />}
                onClick={onReceive}>
          Receive
        </Button>
      )}
    </Stack>
  )
}

// ── Item tile ────────────────────────────────────────────────────────────────

function ItemTile({ row, canManage, canIssue, onIssue, onReceive, onEdit }: {
  row: EquipmentItem
  canManage: boolean
  canIssue: boolean
  onIssue: () => void
  onReceive: () => void
  onEdit: () => void
}) {
  const conditionColour = CONDITION_COLOUR[row.condition] ?? '#8892A6'
  const unusable = row.condition === 'damaged' || row.condition === 'lost'
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
          {row.asset_code}
        </Typography>
        <Chip size="small" variant="outlined" label={tidy(row.category)}
              sx={{ height: 17, fontSize: '0.58rem' }} />
        <Box sx={{ flex: 1 }} />
        {!row.is_active && <Chip size="small" label="retired" sx={{ height: 17, fontSize: '0.58rem' }} />}
        {row.is_overdue && (
          <Tooltip title={`Due back ${fmt(row.expected_return_at)}`}>
            <WarningAmberIcon sx={{ fontSize: 16, color: '#FF4560' }} />
          </Tooltip>
        )}
      </Stack>

      <Typography variant="body2" noWrap sx={{ fontSize: '0.78rem', mb: 0.25 }}>
        {row.name}
      </Typography>
      <Stack direction="row" spacing={0.75} alignItems="center" sx={{ mb: 1 }}>
        <Typography variant="caption" sx={{ color: conditionColour, fontSize: '0.63rem', fontWeight: 700 }}>
          {row.condition}
        </Typography>
        <Typography variant="caption" noWrap sx={{ color: 'text.secondary', fontSize: '0.63rem' }}>
          {row.site_name || 'Pool'}
          {row.serial_number ? ` · ${row.serial_number}` : ''}
        </Typography>
      </Stack>

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
          {unusable ? `In store, marked ${row.condition}` : 'In store'}
        </Typography>
      )}

      <Stack direction="row" spacing={0.5}>
        {row.is_out ? (
          <Button size="small" variant="outlined" fullWidth disabled={!canIssue}
                  startIcon={<LoginIcon sx={{ fontSize: 15 }} />} onClick={onReceive}>
            Receive
          </Button>
        ) : (
          <Button
            size="small" variant="outlined" fullWidth
            disabled={!canIssue || !row.is_active || unusable}
            startIcon={<LogoutIcon sx={{ fontSize: 15 }} />} onClick={onIssue}
          >
            Issue
          </Button>
        )}
        {canManage && (
          <Tooltip title="Edit">
            <IconButton size="small" onClick={onEdit} aria-label={`Edit ${row.asset_code}`}>
              <EditIcon sx={{ fontSize: 17 }} />
            </IconButton>
          </Tooltip>
        )}
      </Stack>
    </GlassCard>
  )
}

// ── Dialogs ──────────────────────────────────────────────────────────────────

type Person = { id: string; label: string }

function IssueDialog({ row, guards, onClose }: {
  row: EquipmentItem; guards: Person[]; onClose: () => void
}) {
  const qc = useQueryClient()
  const [picked, setPicked] = useState<Person | null>(null)
  const [due, setDue] = useState('')
  const [purpose, setPurpose] = useState('')
  const [error, setError] = useState('')

  const issue = useMutation({
    mutationFn: () => issueEquipment({
      item_id: row.id,
      assigned_to_user_id: picked!.id,
      expected_return_at: due ? new Date(due).toISOString() : null,
      purpose: purpose.trim() || null,
    }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['equipment'] })
      qc.invalidateQueries({ queryKey: ['equipment-outstanding'] })
      onClose()
    },
    onError: (e) => setError(apiError(e, 'Could not issue this item')),
  })

  return (
    <Dialog open onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle sx={{ pb: 1 }}>
        <Typography variant="subtitle1" sx={{ fontWeight: 800 }}>
          Issue {row.asset_code}
        </Typography>
        <Typography variant="caption" color="text.secondary">{row.name}</Typography>
      </DialogTitle>
      <DialogContent dividers>
        {error && <Alert severity="error" sx={{ mb: 1.5 }}>{error}</Alert>}
        <Stack spacing={1.5} sx={{ mt: 0.5 }}>
          <Autocomplete
            size="small" options={guards} value={picked} autoFocus
            onChange={(_, v) => setPicked(v)}
            isOptionEqualToValue={(o, v) => o.id === v.id}
            renderInput={(params) => <TextField {...params} label="Issue to" />}
          />
          <TextField
            size="small" type="datetime-local" label="Expected back" value={due}
            onChange={(e) => setDue(e.target.value)}
            slotProps={{ inputLabel: { shrink: true } }}
            helperText="Leave blank for kit held for the duration of a posting"
          />
          <TextField size="small" label="Purpose (optional)" value={purpose}
                     onChange={(e) => setPurpose(e.target.value)} />
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" disabled={!picked || issue.isPending}
                onClick={() => { setError(''); issue.mutate() }}>
          Issue
        </Button>
      </DialogActions>
    </Dialog>
  )
}

function ReceiveDialog({ assignmentId, label, onClose }: {
  assignmentId: string; label: string; onClose: () => void
}) {
  const qc = useQueryClient()
  const [condition, setCondition] = useState('good')
  const [notes, setNotes] = useState('')
  const [error, setError] = useState('')

  const receive = useMutation({
    mutationFn: () => receiveEquipment(assignmentId, {
      condition_on_return: condition,
      return_notes: notes.trim() || null,
    }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['equipment'] })
      qc.invalidateQueries({ queryKey: ['equipment-outstanding'] })
      onClose()
    },
    onError: (e) => setError(apiError(e, 'Could not receive this item')),
  })

  return (
    <Dialog open onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle>Receive {label}</DialogTitle>
      <DialogContent dividers>
        {error && <Alert severity="error" sx={{ mb: 1.5 }}>{error}</Alert>}
        <Stack spacing={1.5} sx={{ mt: 0.5 }}>
          <TextField
            select size="small" label="Condition it came back in" value={condition}
            onChange={(e) => setCondition(e.target.value)}
            helperText="This becomes the item's condition — the next officer sees it"
          >
            {EQUIPMENT_CONDITIONS.map((c) => <MenuItem key={c} value={c}>{c}</MenuItem>)}
          </TextField>
          <TextField size="small" multiline rows={2} label="Notes" value={notes}
                     onChange={(e) => setNotes(e.target.value)} />
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" disabled={receive.isPending}
                onClick={() => { setError(''); receive.mutate() }}>
          Receive
        </Button>
      </DialogActions>
    </Dialog>
  )
}

function ItemEditorDialog({ row, sites, onClose }: {
  row: EquipmentItem | null
  sites: { id: string; name: string }[]
  onClose: () => void
}) {
  const qc = useQueryClient()
  const [assetCode, setAssetCode] = useState(row?.asset_code || '')
  const [name, setName] = useState(row?.name || '')
  const [category, setCategory] = useState(row?.category || 'other')
  const [siteId, setSiteId] = useState(row?.site_id || '')
  const [serial, setSerial] = useState(row?.serial_number || '')
  const [condition, setCondition] = useState(row?.condition || 'good')
  const [notes, setNotes] = useState(row?.notes || '')
  const [error, setError] = useState('')

  const save = useMutation({
    mutationFn: () => row
      ? updateEquipment(row.id, {
          asset_code: assetCode.trim(), name: name.trim(), category,
          site_id: siteId || null, serial_number: serial.trim() || null,
          condition, notes: notes.trim() || null,
        })
      : createEquipment({
          asset_code: assetCode.trim(), name: name.trim(), category,
          site_id: siteId || null, serial_number: serial.trim() || null,
          condition, notes: notes.trim() || null,
        }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['equipment'] }); onClose() },
    onError: (e) => setError(apiError(e, 'Could not save this item')),
  })

  const retire = useMutation({
    mutationFn: () => updateEquipment(row!.id, { is_active: !row!.is_active }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['equipment'] }); onClose() },
    onError: (e) => setError(apiError(e, 'Could not change this item')),
  })

  return (
    <Dialog open onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle>{row ? `Edit ${row.asset_code}` : 'Add equipment'}</DialogTitle>
      <DialogContent dividers>
        {error && <Alert severity="error" sx={{ mb: 1.5 }}>{error}</Alert>}
        <Stack spacing={1.5} sx={{ mt: 0.5 }}>
          <TextField
            size="small" label="Asset code" value={assetCode} autoFocus
            onChange={(e) => setAssetCode(e.target.value)}
            helperText="Your own numbering — unique across the company"
          />
          <TextField size="small" label="What it is" value={name}
                     onChange={(e) => setName(e.target.value)} />
          <TextField select size="small" label="Category" value={category}
                     onChange={(e) => setCategory(e.target.value)}>
            {EQUIPMENT_CATEGORIES.map((c) => (
              <MenuItem key={c} value={c}>{tidy(c)}</MenuItem>
            ))}
          </TextField>
          <TextField select size="small" label="Site" value={siteId}
                     onChange={(e) => setSiteId(e.target.value)}>
            <MenuItem value="">Pool — not tied to a site</MenuItem>
            {sites.map((s) => <MenuItem key={s.id} value={s.id}>{s.name}</MenuItem>)}
          </TextField>
          <TextField size="small" label="Serial number" value={serial}
                     onChange={(e) => setSerial(e.target.value)} />
          <TextField select size="small" label="Condition" value={condition}
                     onChange={(e) => setCondition(e.target.value)}>
            {EQUIPMENT_CONDITIONS.map((c) => <MenuItem key={c} value={c}>{c}</MenuItem>)}
          </TextField>
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
        <Button variant="contained"
                disabled={!assetCode.trim() || !name.trim() || save.isPending}
                onClick={() => { setError(''); save.mutate() }}>
          Save
        </Button>
      </DialogActions>
    </Dialog>
  )
}

function UniformIssueDialog({ guards, onClose }: { guards: Person[]; onClose: () => void }) {
  const qc = useQueryClient()
  const [picked, setPicked] = useState<Person | null>(null)
  const [itemType, setItemType] = useState('shirt')
  const [size, setSize] = useState('')
  const [quantity, setQuantity] = useState('1')
  const [deposit, setDeposit] = useState('')
  const [notes, setNotes] = useState('')
  const [error, setError] = useState('')

  const issue = useMutation({
    mutationFn: () => issueUniform({
      user_id: picked!.id,
      item_type: itemType,
      size: size.trim() || null,
      quantity: Number(quantity) || 1,
      deposit_amount: deposit.trim() || null,
      notes: notes.trim() || null,
    }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['uniforms'] }); onClose() },
    onError: (e) => setError(apiError(e, 'Could not record this issue')),
  })

  const qty = Number(quantity)
  return (
    <Dialog open onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle>Issue uniform</DialogTitle>
      <DialogContent dividers>
        {error && <Alert severity="error" sx={{ mb: 1.5 }}>{error}</Alert>}
        <Stack spacing={1.5} sx={{ mt: 0.5 }}>
          <Autocomplete
            size="small" options={guards} value={picked}
            onChange={(_, v) => setPicked(v)}
            isOptionEqualToValue={(o, v) => o.id === v.id}
            renderInput={(params) => <TextField {...params} label="Issue to" />}
          />
          <Stack direction="row" spacing={1}>
            <TextField select size="small" label="Item" value={itemType} sx={{ flex: 1 }}
                       onChange={(e) => setItemType(e.target.value)}>
              {UNIFORM_TYPES.map((t) => <MenuItem key={t} value={t}>{tidy(t)}</MenuItem>)}
            </TextField>
            <TextField size="small" label="Size" value={size} sx={{ width: 100 }}
                       onChange={(e) => setSize(e.target.value)} />
            <TextField
              size="small" label="Qty" value={quantity} sx={{ width: 80 }}
              onChange={(e) => setQuantity(e.target.value.replace(/[^0-9]/g, ''))}
            />
          </Stack>
          <TextField
            size="small" label="Deposit held (SGD)" value={deposit}
            onChange={(e) => setDeposit(e.target.value.replace(/[^0-9.]/g, ''))}
            helperText="What you withhold against the kit, if anything"
          />
          <TextField size="small" multiline rows={2} label="Notes" value={notes}
                     onChange={(e) => setNotes(e.target.value)} />
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained"
                disabled={!picked || !qty || qty < 1 || issue.isPending}
                onClick={() => { setError(''); issue.mutate() }}>
          Issue
        </Button>
      </DialogActions>
    </Dialog>
  )
}

function UniformReturnDialog({ row, onClose }: { row: UniformIssue; onClose: () => void }) {
  const qc = useQueryClient()
  const [count, setCount] = useState(String(row.quantity))
  const [notes, setNotes] = useState('')
  const [error, setError] = useState('')

  const give = useMutation({
    mutationFn: () => returnUniform(row.id, {
      returned_quantity: Number(count) || 0,
      notes: notes.trim() || null,
    }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['uniforms'] }); onClose() },
    onError: (e) => setError(apiError(e, 'Could not record the return')),
  })

  const n = Number(count)
  return (
    <Dialog open onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle sx={{ pb: 1 }}>
        <Typography variant="subtitle1" sx={{ fontWeight: 800 }}>Receive uniform back</Typography>
        <Typography variant="caption" color="text.secondary">
          {row.quantity} × {tidy(row.item_type)}{row.size ? ` (${row.size})` : ''} issued
          to {row.full_name}
        </Typography>
      </DialogTitle>
      <DialogContent dividers>
        {error && <Alert severity="error" sx={{ mb: 1.5 }}>{error}</Alert>}
        <Stack spacing={1.5} sx={{ mt: 0.5 }}>
          <TextField
            size="small" label="How many came back in total" value={count} autoFocus
            onChange={(e) => setCount(e.target.value.replace(/[^0-9]/g, ''))}
            helperText={`A total, not an addition — ${row.returned_quantity} recorded so far`}
          />
          <TextField size="small" multiline rows={2} label="Notes" value={notes}
                     onChange={(e) => setNotes(e.target.value)} />
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained"
                disabled={give.isPending || Number.isNaN(n) || n < 0 || n > row.quantity}
                onClick={() => { setError(''); give.mutate() }}>
          Record
        </Button>
      </DialogActions>
    </Dialog>
  )
}

function HeldByDialog({ person, onClose }: { person: Person; onClose: () => void }) {
  const { data, isLoading } = useQuery({
    queryKey: ['held-by', person.id],
    queryFn: () => getHeldByUser(person.id),
  })

  return (
    <Dialog open onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle sx={{ pb: 1 }}>
        <Typography variant="subtitle1" sx={{ fontWeight: 800 }}>{person.label}</Typography>
        <Typography variant="caption" color="text.secondary">
          Everything still signed out to this officer
        </Typography>
      </DialogTitle>
      <DialogContent dividers>
        {isLoading ? (
          <Stack spacing={1}>
            {[0, 1, 2].map((i) => <Skeleton key={i} variant="rounded" height={46} />)}
          </Stack>
        ) : !data ? null : (
          <Stack spacing={1.5}>
            <Stack direction="row" spacing={0.75}>
              <Chip size="small" label={`${data.summary.equipment_out} items`}
                    sx={{ height: 20, fontSize: '0.65rem' }} />
              <Chip size="small" label={`${data.summary.uniform_pieces_out} uniform pieces`}
                    sx={{ height: 20, fontSize: '0.65rem' }} />
              {Number(data.summary.deposit_held) > 0 && (
                <Chip size="small" label={`SGD ${Number(data.summary.deposit_held).toFixed(2)} deposit`}
                      sx={{ height: 20, fontSize: '0.65rem' }} />
              )}
            </Stack>

            <Box>
              <Typography variant="caption" sx={{ fontWeight: 800, letterSpacing: '0.04em' }}>
                EQUIPMENT
              </Typography>
              {data.equipment.length === 0 ? (
                <Typography variant="body2" color="text.secondary" sx={{ fontSize: '0.8rem' }}>
                  Nothing signed out.
                </Typography>
              ) : (
                <Stack spacing={0.5} sx={{ mt: 0.5 }}>
                  {data.equipment.map((e) => (
                    <Stack key={e.id} direction="row" alignItems="center" spacing={1} sx={{
                      px: 1, py: 0.6, borderRadius: '6px',
                      background: e.is_overdue ? 'rgba(255,69,96,0.10)' : 'rgba(255,255,255,0.03)',
                      border: '1px solid rgba(255,255,255,0.07)',
                    }}>
                      <Typography variant="body2" sx={{ fontWeight: 700, fontSize: '0.78rem' }}>
                        {e.asset_code}
                      </Typography>
                      <Typography variant="caption" sx={{ flex: 1, color: 'text.secondary', fontSize: '0.63rem' }}>
                        {e.name} · since {fmt(e.issued_at)}
                      </Typography>
                      {e.is_overdue && (
                        <Chip size="small" label="overdue"
                              sx={{ height: 17, fontSize: '0.58rem',
                                    bgcolor: 'rgba(255,69,96,0.18)', color: '#FF4560' }} />
                      )}
                    </Stack>
                  ))}
                </Stack>
              )}
            </Box>

            <Box>
              <Typography variant="caption" sx={{ fontWeight: 800, letterSpacing: '0.04em' }}>
                UNIFORM
              </Typography>
              {data.uniform.length === 0 ? (
                <Typography variant="body2" color="text.secondary" sx={{ fontSize: '0.8rem' }}>
                  Nothing outstanding.
                </Typography>
              ) : (
                <Stack spacing={0.5} sx={{ mt: 0.5 }}>
                  {data.uniform.map((u) => (
                    <Stack key={u.id} direction="row" alignItems="center" spacing={1} sx={{
                      px: 1, py: 0.6, borderRadius: '6px',
                      background: 'rgba(255,255,255,0.03)',
                      border: '1px solid rgba(255,255,255,0.07)',
                    }}>
                      <Typography variant="body2" sx={{ fontWeight: 700, fontSize: '0.78rem' }}>
                        {u.outstanding_quantity} × {tidy(u.item_type)}
                      </Typography>
                      <Typography variant="caption" sx={{ flex: 1, color: 'text.secondary', fontSize: '0.63rem' }}>
                        {u.size ? `size ${u.size} · ` : ''}issued {fmt(u.issued_at)}
                      </Typography>
                    </Stack>
                  ))}
                </Stack>
              )}
            </Box>
          </Stack>
        )}
      </DialogContent>
      <DialogActions><Button onClick={onClose}>Close</Button></DialogActions>
    </Dialog>
  )
}

// ── Page ─────────────────────────────────────────────────────────────────────

export function EquipmentRegisterPage() {
  const roleId = useAuthStore((s) => s.user?.roleId ?? 0)
  const canManage = CAN_MANAGE.has(roleId)
  const canIssue = CAN_ISSUE.has(roleId)

  const [tab, setTab] = useState(0)
  const [siteFilter, setSiteFilter] = useState('')
  const [categoryFilter, setCategoryFilter] = useState('')
  const [issuing, setIssuing] = useState<EquipmentItem | null>(null)
  const [receiving, setReceiving] = useState<{ id: string; label: string } | null>(null)
  const [editing, setEditing] = useState<EquipmentItem | null>(null)
  const [adding, setAdding] = useState(false)
  const [issuingUniform, setIssuingUniform] = useState(false)
  const [returningUniform, setReturningUniform] = useState<UniformIssue | null>(null)
  const [inspecting, setInspecting] = useState<Person | null>(null)

  const { data: sites = [] } = useQuery({ queryKey: ['sites'], queryFn: () => getSites() })
  const { data: users = [] } = useQuery({ queryKey: ['users'], queryFn: getUsers })

  const { data: items = [], isLoading } = useQuery({
    queryKey: ['equipment', siteFilter, categoryFilter],
    queryFn: () => listEquipment({
      site_id: siteFilter || undefined,
      category: categoryFilter || undefined,
    }),
    refetchInterval: 120_000,
  })

  const { data: outstanding } = useQuery({
    queryKey: ['equipment-outstanding', siteFilter],
    queryFn: () => getOutstandingEquipment(siteFilter || undefined),
    refetchInterval: 120_000,
  })

  const { data: uniforms = [], isLoading: uniformsLoading } = useQuery({
    queryKey: ['uniforms'],
    queryFn: () => listUniformIssues({ outstanding_only: true }),
    enabled: tab === 1,
  })

  const people: Person[] = useMemo(
    () => users
      .filter((u) => u.is_active)
      .map((u) => ({ id: u.id, label: u.full_name || u.email })),
    [users],
  )

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
          pageKey="equipment"
          action={
            tab === 0
              ? (canManage ? (
                  <Button size="small" variant="contained" startIcon={<AddIcon />}
                          onClick={() => setAdding(true)}>
                    Add equipment
                  </Button>
                ) : undefined)
              : (canIssue ? (
                  <Button size="small" variant="contained" startIcon={<AddIcon />}
                          onClick={() => setIssuingUniform(true)}>
                    Issue uniform
                  </Button>
                ) : undefined)
          }
        />

        {tab === 0 && overdue > 0 && (
          <Alert severity="warning" sx={{ mb: 1.5 }}>
            {overdue} {overdue === 1 ? 'item is' : 'items are'} past the time they were due back.
          </Alert>
        )}

        <Tabs value={tab} onChange={(_, v) => setTab(v)} sx={{ minHeight: 34, mb: 1 }}>
          <Tab label="Equipment" sx={{ minHeight: 34, fontSize: '0.78rem' }} />
          <Tab label="Uniform" sx={{ minHeight: 34, fontSize: '0.78rem' }} />
        </Tabs>

        {tab === 0 ? (
          <>
            {outstanding && outstanding.out > 0 && (
              <GlassCard sx={{ p: 1.5, mb: 1.5 }}>
                <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 1 }}>
                  <LogoutIcon sx={{ fontSize: 17, color: '#F5A524' }} />
                  <Typography variant="subtitle2" sx={{ fontWeight: 800 }}>
                    Signed out
                  </Typography>
                  <Chip size="small" label={outstanding.out} sx={{ height: 18, fontSize: '0.62rem' }} />
                </Stack>
                <Stack spacing={0.75}>
                  {outstanding.items.map((row) => (
                    <OutstandingRow
                      key={row.id} row={row} canIssue={canIssue}
                      onReceive={() => setReceiving({
                        id: row.id, label: `${row.asset_code} — ${row.name}`,
                      })}
                    />
                  ))}
                </Stack>
              </GlassCard>
            )}

            {isLoading ? (
              <Box sx={{ display: 'grid', gap: 1.25, gridTemplateColumns: 'repeat(auto-fill, minmax(230px, 1fr))' }}>
                {[0, 1, 2, 3, 4, 5].map((i) => <Skeleton key={i} variant="rounded" height={150} />)}
              </Box>
            ) : items.length === 0 ? (
              <GlassCard sx={{ p: 4, textAlign: 'center' }}>
                <InventoryIcon sx={{ fontSize: 34, color: 'text.disabled', mb: 1 }} />
                <Typography color="text.secondary">
                  No equipment on the register yet.
                </Typography>
              </GlassCard>
            ) : (
              <Box sx={{
                display: 'grid', gap: 1.25, alignItems: 'start',
                gridTemplateColumns: 'repeat(auto-fill, minmax(230px, 1fr))',
              }}>
                {items.map((row) => (
                  <ItemTile
                    key={row.id} row={row} canManage={canManage} canIssue={canIssue}
                    onIssue={() => setIssuing(row)}
                    onReceive={() => row.open_assignment_id && setReceiving({
                      id: row.open_assignment_id, label: `${row.asset_code} — ${row.name}`,
                    })}
                    onEdit={() => setEditing(row)}
                  />
                ))}
              </Box>
            )}
          </>
        ) : (
          <>
            <Stack direction="row" spacing={1} sx={{ mb: 1.5 }} alignItems="center">
              <Autocomplete
                size="small" options={people} sx={{ minWidth: 260 }} value={null}
                onChange={(_, v) => v && setInspecting(v)}
                isOptionEqualToValue={(o, v) => o.id === v.id}
                renderInput={(params) => (
                  <TextField {...params} label="What is this officer holding?" />
                )}
              />
              <PersonSearchIcon sx={{ fontSize: 20, color: 'text.disabled' }} />
            </Stack>

            {uniformsLoading ? (
              <Stack spacing={0.75}>
                {[0, 1, 2, 3].map((i) => <Skeleton key={i} variant="rounded" height={48} />)}
              </Stack>
            ) : uniforms.length === 0 ? (
              <GlassCard sx={{ p: 4, textAlign: 'center' }}>
                <CheckroomIcon sx={{ fontSize: 34, color: 'text.disabled', mb: 1 }} />
                <Typography color="text.secondary">
                  Nothing outstanding. Uniform issued to officers shows here until it comes back.
                </Typography>
              </GlassCard>
            ) : (
              <Stack spacing={0.75}>
                {uniforms.map((u) => (
                  <Stack
                    key={u.id} direction="row" alignItems="center" spacing={1.25}
                    sx={{
                      px: 1.25, py: 0.9, borderRadius: '8px',
                      background: 'rgba(255,255,255,0.03)',
                      border: '1px solid rgba(255,255,255,0.07)',
                    }}
                  >
                    <CheckroomIcon sx={{ fontSize: 17, color: 'text.secondary' }} />
                    <Box sx={{ minWidth: 150 }}>
                      <Typography variant="body2" noWrap sx={{ fontWeight: 700, fontSize: '0.8rem' }}>
                        {u.full_name}
                      </Typography>
                      <Typography variant="caption" sx={{ color: 'text.secondary', fontSize: '0.63rem' }}>
                        {u.employee_code || '—'}
                      </Typography>
                    </Box>
                    <Box sx={{ flex: 1, minWidth: 0 }}>
                      <Typography variant="body2" noWrap sx={{ fontSize: '0.8rem' }}>
                        {u.outstanding_quantity} of {u.quantity} × {tidy(u.item_type)}
                        {u.size ? ` (${u.size})` : ''}
                      </Typography>
                      <Typography variant="caption" noWrap sx={{ display: 'block', color: 'text.secondary', fontSize: '0.63rem' }}>
                        Issued {fmt(u.issued_at)}
                        {u.deposit_amount ? ` · SGD ${Number(u.deposit_amount).toFixed(2)} deposit` : ''}
                      </Typography>
                    </Box>
                    {canIssue && (
                      <Button size="small" variant="outlined"
                              startIcon={<LoginIcon sx={{ fontSize: 15 }} />}
                              onClick={() => setReturningUniform(u)}>
                        Receive
                      </Button>
                    )}
                  </Stack>
                ))}
              </Stack>
            )}
          </>
        )}
      </Box>

      <FilterRail
        storageKey="equipment"
        groups={[
          {
            key: 'site', label: 'Site', options: siteOptions,
            value: siteFilter, onChange: setSiteFilter,
          },
          {
            key: 'category', label: 'Category',
            options: [{ value: '', label: 'All categories' },
                      ...EQUIPMENT_CATEGORIES.map((c) => ({ value: c, label: tidy(c) }))],
            value: categoryFilter, onChange: setCategoryFilter,
          },
        ]}
      />

      {issuing && <IssueDialog row={issuing} guards={people} onClose={() => setIssuing(null)} />}
      {receiving && (
        <ReceiveDialog
          assignmentId={receiving.id} label={receiving.label}
          onClose={() => setReceiving(null)}
        />
      )}
      {(adding || editing) && (
        <ItemEditorDialog
          row={editing} sites={sites}
          onClose={() => { setAdding(false); setEditing(null) }}
        />
      )}
      {issuingUniform && (
        <UniformIssueDialog guards={people} onClose={() => setIssuingUniform(false)} />
      )}
      {returningUniform && (
        <UniformReturnDialog row={returningUniform} onClose={() => setReturningUniform(null)} />
      )}
      {inspecting && <HeldByDialog person={inspecting} onClose={() => setInspecting(null)} />}
    </Box>
  )
}

export default EquipmentRegisterPage
