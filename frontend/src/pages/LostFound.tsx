/**
 * Lost & Found — what was handed in, where it is, and who took it away.
 *
 * The page is ordered by what somebody has to decide about: held items first,
 * oldest first, with the ones past the retention period called out. A closed
 * record is read-only on purpose — it is what a claimant signed for.
 *
 * The claimant's identity is deliberately thin: a name, a contact, and the
 * last few characters of a document. Verification happens against the card in
 * their hand; storing a copy of it would create a liability with no matching
 * benefit.
 */
import { useEffect, useMemo, useState } from 'react'
import {
  Alert, Box, Button, Chip, Dialog, DialogActions, DialogContent, DialogTitle,
  IconButton, MenuItem, Skeleton, TextField, Tooltip, Typography,
} from '@mui/material'
import Stack from '@/components/common/Stack'
import AddIcon from '@mui/icons-material/Add'
import Inventory2Icon from '@mui/icons-material/Inventory2'
import PhotoCameraIcon from '@mui/icons-material/PhotoCamera'
import HandshakeIcon from '@mui/icons-material/Handshake'
import DeleteSweepIcon from '@mui/icons-material/DeleteSweep'
import EditIcon from '@mui/icons-material/Edit'
import SearchIcon from '@mui/icons-material/Search'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import {
  disposeLostFoundItem, fetchLostFoundPhoto, getLostFoundSummary, listLostFound,
  logLostFoundItem, releaseLostFoundItem, updateLostFoundItem, uploadLostFoundPhoto,
  LOST_FOUND_CATEGORIES, type LostFoundItem,
} from '@/api/guardhouse'
import { getSites } from '@/api/sites'
import { GlassCard } from '@/components/common/GlassCard'
import { PageHeader } from '@/components/common/PageHeader'
import { FilterRail } from '@/components/common/FilterRail'
import { useAuthStore } from '@/store/auth'

const CAN_DISPOSE = new Set([1, 2, 3, 8])

const STATUS_META: Record<string, { label: string; colour: string }> = {
  held: { label: 'Held', colour: '#F5A524' },
  claimed: { label: 'Claimed', colour: '#00D97E' },
  disposed: { label: 'Disposed', colour: '#8892A6' },
  handed_to_police: { label: 'To police', colour: '#6C63FF' },
}

function fmtDate(ts: string) {
  return new Date(ts).toLocaleDateString(undefined, {
    day: '2-digit', month: 'short', year: 'numeric',
  })
}

function apiError(e: unknown, fallback: string) {
  const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
  return detail || fallback
}

// ── Photo ────────────────────────────────────────────────────────────────────

/** The photo endpoint is bearer-authenticated, so it cannot be an <img src>.
 *  Fetch the bytes, show the object URL, and revoke it on unmount. */
function ItemPhoto({ itemId, height = 140 }: { itemId: string; height?: number }) {
  const [url, setUrl] = useState<string | null>(null)

  useEffect(() => {
    let revoked: string | null = null
    let cancelled = false
    fetchLostFoundPhoto(itemId)
      .then((objectUrl) => {
        if (cancelled) { URL.revokeObjectURL(objectUrl); return }
        revoked = objectUrl
        setUrl(objectUrl)
      })
      .catch(() => undefined)
    return () => {
      cancelled = true
      if (revoked) URL.revokeObjectURL(revoked)
    }
  }, [itemId])

  if (!url) return <Skeleton variant="rounded" height={height} />
  return (
    <Box
      component="img" src={url} alt="Item as handed in"
      sx={{ width: '100%', height, objectFit: 'cover', borderRadius: '8px' }}
    />
  )
}

// ── Card ─────────────────────────────────────────────────────────────────────

function ItemCard({ row, canDispose, onRelease, onDispose, onEdit, onPhoto }: {
  row: LostFoundItem
  canDispose: boolean
  onRelease: () => void
  onDispose: () => void
  onEdit: () => void
  onPhoto: () => void
}) {
  const meta = STATUS_META[row.status] ?? STATUS_META.held
  const held = row.status === 'held'
  const days = Math.floor(Number(row.days_held) || 0)

  return (
    <GlassCard sx={{
      p: 1.25,
      border: row.due_for_disposal ? '1px solid rgba(255,69,96,0.42)' : undefined,
      opacity: held ? 1 : 0.72,
    }}>
      {row.has_photo && <Box sx={{ mb: 1 }}><ItemPhoto itemId={row.id} /></Box>}

      <Stack direction="row" alignItems="center" spacing={0.75} sx={{ mb: 0.5 }}>
        <Chip
          size="small" label={meta.label}
          sx={{
            height: 17, fontSize: '0.58rem', fontWeight: 700,
            bgcolor: `${meta.colour}22`, color: meta.colour,
          }}
        />
        <Chip size="small" variant="outlined" label={row.category}
              sx={{ height: 17, fontSize: '0.58rem' }} />
        <Box sx={{ flex: 1 }} />
        {row.due_for_disposal && (
          <Tooltip title="Held past the retention period — decide what happens to it">
            <Chip size="small" label={`${days}d`}
                  sx={{ height: 17, fontSize: '0.58rem', bgcolor: 'rgba(255,69,96,0.18)', color: '#FF4560' }} />
          </Tooltip>
        )}
      </Stack>

      <Typography variant="body2" sx={{ fontWeight: 600, fontSize: '0.82rem', mb: 0.4 }}>
        {row.description}
      </Typography>
      <Typography variant="caption" sx={{ display: 'block', color: 'text.secondary', fontSize: '0.63rem' }}>
        Found {fmtDate(row.found_at)}
        {row.found_location ? ` · ${row.found_location}` : ''}
        {row.site_name ? ` · ${row.site_name}` : ''}
      </Typography>
      {row.storage_location && held && (
        <Typography variant="caption" sx={{ display: 'block', color: 'text.secondary', fontSize: '0.63rem' }}>
          Stored: {row.storage_location}
        </Typography>
      )}

      {row.status === 'claimed' && (
        <Box sx={{
          mt: 0.9, px: 1, py: 0.6, borderRadius: '6px',
          background: 'rgba(0,217,126,0.09)', border: '1px solid rgba(0,217,126,0.26)',
        }}>
          <Typography variant="caption" sx={{ display: 'block', fontWeight: 700, fontSize: '0.63rem' }}>
            {row.claimed_by_name}
          </Typography>
          <Typography variant="caption" sx={{ display: 'block', color: 'text.secondary', fontSize: '0.6rem' }}>
            {[row.claimed_id_type && `${row.claimed_id_type} …${row.claimed_id_last4 ?? ''}`,
              row.claimed_by_contact,
              row.released_at && `released ${fmtDate(row.released_at)}`,
              row.released_by_name && `by ${row.released_by_name}`,
            ].filter(Boolean).join(' · ')}
          </Typography>
        </Box>
      )}

      {(row.status === 'disposed' || row.status === 'handed_to_police') && (
        <Typography variant="caption" sx={{ display: 'block', mt: 0.9, color: 'text.secondary', fontSize: '0.63rem' }}>
          {row.disposal_method}{row.disposed_at ? ` · ${fmtDate(row.disposed_at)}` : ''}
        </Typography>
      )}

      {held && (
        <Stack direction="row" spacing={0.5} sx={{ mt: 1 }}>
          <Button size="small" variant="outlined" fullWidth
                  startIcon={<HandshakeIcon sx={{ fontSize: 15 }} />} onClick={onRelease}>
            Release
          </Button>
          {canDispose && (
            <Tooltip title="Dispose or hand to police">
              <IconButton size="small" onClick={onDispose} aria-label="Dispose of this item">
                <DeleteSweepIcon sx={{ fontSize: 17 }} />
              </IconButton>
            </Tooltip>
          )}
          <Tooltip title={row.has_photo ? 'Replace photo' : 'Add photo'}>
            <IconButton size="small" onClick={onPhoto} aria-label="Photograph this item">
              <PhotoCameraIcon sx={{ fontSize: 17 }} />
            </IconButton>
          </Tooltip>
          <Tooltip title="Edit">
            <IconButton size="small" onClick={onEdit} aria-label="Edit this item">
              <EditIcon sx={{ fontSize: 17 }} />
            </IconButton>
          </Tooltip>
        </Stack>
      )}
    </GlassCard>
  )
}

// ── Dialogs ──────────────────────────────────────────────────────────────────

function ItemEditorDialog({ row, sites, onClose }: {
  row: LostFoundItem | null
  sites: { id: string; name: string }[]
  onClose: () => void
}) {
  const qc = useQueryClient()
  const [description, setDescription] = useState(row?.description || '')
  const [category, setCategory] = useState(row?.category || 'other')
  const [siteId, setSiteId] = useState(row?.site_id || '')
  const [foundLocation, setFoundLocation] = useState(row?.found_location || '')
  const [storage, setStorage] = useState(row?.storage_location || '')
  const [notes, setNotes] = useState(row?.notes || '')
  const [error, setError] = useState('')

  const save = useMutation({
    mutationFn: () => row
      ? updateLostFoundItem(row.id, {
          description: description.trim(), category,
          found_location: foundLocation.trim() || null,
          storage_location: storage.trim() || null,
          notes: notes.trim() || null,
        })
      : logLostFoundItem({
          description: description.trim(), category,
          site_id: siteId || null,
          found_location: foundLocation.trim() || null,
          storage_location: storage.trim() || null,
          notes: notes.trim() || null,
        }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['lost-found'] })
      qc.invalidateQueries({ queryKey: ['lost-found-summary'] })
      onClose()
    },
    onError: (e) => setError(apiError(e, 'Could not save this item')),
  })

  return (
    <Dialog open onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle>{row ? 'Edit item' : 'Book in found property'}</DialogTitle>
      <DialogContent dividers>
        {error && <Alert severity="error" sx={{ mb: 1.5 }}>{error}</Alert>}
        <Stack spacing={1.5} sx={{ mt: 0.5 }}>
          <TextField
            size="small" label="Description" value={description} autoFocus multiline rows={2}
            onChange={(e) => setDescription(e.target.value)}
            helperText="Enough detail to tell it apart from a similar item"
          />
          <TextField select size="small" label="Category" value={category}
                     onChange={(e) => setCategory(e.target.value)}>
            {LOST_FOUND_CATEGORIES.map((c) => <MenuItem key={c} value={c}>{c}</MenuItem>)}
          </TextField>
          {!row && (
            <TextField select size="small" label="Site" value={siteId}
                       onChange={(e) => setSiteId(e.target.value)}>
              <MenuItem value="">Not site-specific</MenuItem>
              {sites.map((s) => <MenuItem key={s.id} value={s.id}>{s.name}</MenuItem>)}
            </TextField>
          )}
          <TextField size="small" label="Where it was found" value={foundLocation}
                     onChange={(e) => setFoundLocation(e.target.value)} />
          <TextField
            size="small" label="Where it is stored" value={storage}
            onChange={(e) => setStorage(e.target.value)}
            helperText="The drawer or safe — so the next shift can find it"
          />
          <TextField size="small" multiline rows={2} label="Notes" value={notes}
                     onChange={(e) => setNotes(e.target.value)} />
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" disabled={!description.trim() || save.isPending}
                onClick={() => { setError(''); save.mutate() }}>
          Save
        </Button>
      </DialogActions>
    </Dialog>
  )
}

function ReleaseDialog({ row, onClose }: { row: LostFoundItem; onClose: () => void }) {
  const qc = useQueryClient()
  const [name, setName] = useState('')
  const [contact, setContact] = useState('')
  const [idType, setIdType] = useState('NRIC')
  const [idLast4, setIdLast4] = useState('')
  const [notes, setNotes] = useState('')
  const [error, setError] = useState('')

  const release = useMutation({
    mutationFn: () => releaseLostFoundItem(row.id, {
      claimed_by_name: name.trim(),
      claimed_by_contact: contact.trim() || null,
      claimed_id_type: idType || null,
      claimed_id_last4: idLast4.trim() || null,
      notes: notes.trim() || null,
    }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['lost-found'] })
      qc.invalidateQueries({ queryKey: ['lost-found-summary'] })
      onClose()
    },
    onError: (e) => setError(apiError(e, 'Could not release this item')),
  })

  return (
    <Dialog open onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle sx={{ pb: 1 }}>
        <Typography variant="subtitle1" sx={{ fontWeight: 800 }}>Release to claimant</Typography>
        <Typography variant="caption" color="text.secondary">{row.description}</Typography>
      </DialogTitle>
      <DialogContent dividers>
        {error && <Alert severity="error" sx={{ mb: 1.5 }}>{error}</Alert>}
        <Stack spacing={1.5} sx={{ mt: 0.5 }}>
          <TextField size="small" label="Claimant name" value={name} autoFocus
                     onChange={(e) => setName(e.target.value)} />
          <TextField size="small" label="Contact number" value={contact}
                     onChange={(e) => setContact(e.target.value)} />
          <Stack direction="row" spacing={1}>
            <TextField select size="small" label="Document" value={idType} sx={{ flex: 1 }}
                       onChange={(e) => setIdType(e.target.value)}>
              {['NRIC', 'FIN', 'Passport', 'Work Permit', 'Driving Licence', 'Other']
                .map((t) => <MenuItem key={t} value={t}>{t}</MenuItem>)}
            </TextField>
            <TextField
              size="small" label="Last 4" value={idLast4} sx={{ flex: 1 }}
              onChange={(e) => setIdLast4(e.target.value.slice(0, 8))}
              helperText="Last few characters only"
            />
          </Stack>
          <TextField size="small" multiline rows={2} label="Notes" value={notes}
                     onChange={(e) => setNotes(e.target.value)} />
          <Alert severity="info" sx={{ py: 0.5 }}>
            Check the document in their hand. Only the last few characters are stored.
          </Alert>
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" disabled={!name.trim() || release.isPending}
                onClick={() => { setError(''); release.mutate() }}>
          Release item
        </Button>
      </DialogActions>
    </Dialog>
  )
}

function DisposeDialog({ row, onClose }: { row: LostFoundItem; onClose: () => void }) {
  const qc = useQueryClient()
  const [method, setMethod] = useState('')
  const [toPolice, setToPolice] = useState(false)
  const [notes, setNotes] = useState('')
  const [error, setError] = useState('')

  const dispose = useMutation({
    mutationFn: () => disposeLostFoundItem(row.id, {
      disposal_method: method.trim(),
      handed_to_police: toPolice,
      notes: notes.trim() || null,
    }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['lost-found'] })
      qc.invalidateQueries({ queryKey: ['lost-found-summary'] })
      onClose()
    },
    onError: (e) => setError(apiError(e, 'Could not close this record')),
  })

  return (
    <Dialog open onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle sx={{ pb: 1 }}>
        <Typography variant="subtitle1" sx={{ fontWeight: 800 }}>Close this record</Typography>
        <Typography variant="caption" color="text.secondary">{row.description}</Typography>
      </DialogTitle>
      <DialogContent dividers>
        {error && <Alert severity="error" sx={{ mb: 1.5 }}>{error}</Alert>}
        <Stack spacing={1.5} sx={{ mt: 0.5 }}>
          <TextField
            select size="small" label="What happened to it"
            value={toPolice ? 'police' : 'disposed'}
            onChange={(e) => setToPolice(e.target.value === 'police')}
          >
            <MenuItem value="disposed">Disposed of</MenuItem>
            <MenuItem value="police">Handed to police</MenuItem>
          </TextField>
          <TextField
            size="small" label="How" value={method} autoFocus
            onChange={(e) => setMethod(e.target.value)}
            helperText={toPolice
              ? 'Which post, and who took it'
              : 'Donated, binned, returned to building management…'}
          />
          <TextField size="small" multiline rows={2} label="Notes" value={notes}
                     onChange={(e) => setNotes(e.target.value)} />
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" color="error" disabled={!method.trim() || dispose.isPending}
                onClick={() => { setError(''); dispose.mutate() }}>
          Close record
        </Button>
      </DialogActions>
    </Dialog>
  )
}

function PhotoDialog({ row, onClose }: { row: LostFoundItem; onClose: () => void }) {
  const qc = useQueryClient()
  const [file, setFile] = useState<File | null>(null)
  const [error, setError] = useState('')

  // Derived, not stored: an effect that sets state here would render twice for
  // every file picked. The effect exists only to release the URL afterwards.
  const preview = useMemo(() => (file ? URL.createObjectURL(file) : null), [file])
  useEffect(() => () => { if (preview) URL.revokeObjectURL(preview) }, [preview])

  const upload = useMutation({
    mutationFn: () => uploadLostFoundPhoto(row.id, file!),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['lost-found'] }); onClose() },
    onError: (e) => setError(apiError(e, 'Could not upload the photo')),
  })

  return (
    <Dialog open onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle>Photograph the item</DialogTitle>
      <DialogContent dividers>
        {error && <Alert severity="error" sx={{ mb: 1.5 }}>{error}</Alert>}
        <Button component="label" variant="outlined" fullWidth
                startIcon={<PhotoCameraIcon />} sx={{ mb: 1.5 }}>
          {file ? file.name : 'Choose a photo'}
          <input
            hidden type="file" accept="image/jpeg,image/png"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          />
        </Button>
        {preview && (
          <Box component="img" src={preview} alt="Preview"
               sx={{ width: '100%', borderRadius: '8px', maxHeight: 260, objectFit: 'contain' }} />
        )}
        <Typography variant="caption" sx={{ display: 'block', mt: 1, color: 'text.secondary' }}>
          Taken at the counter, this is what settles a disputed claim. JPEG or PNG.
        </Typography>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" disabled={!file || upload.isPending}
                onClick={() => { setError(''); upload.mutate() }}>
          Upload
        </Button>
      </DialogActions>
    </Dialog>
  )
}

// ── Page ─────────────────────────────────────────────────────────────────────

export function LostFoundPage() {
  const roleId = useAuthStore((s) => s.user?.roleId ?? 0)
  const canDispose = CAN_DISPOSE.has(roleId)

  const [siteFilter, setSiteFilter] = useState('')
  const [statusFilter, setStatusFilter] = useState('held')
  const [categoryFilter, setCategoryFilter] = useState('')
  const [search, setSearch] = useState('')
  const [adding, setAdding] = useState(false)
  const [editing, setEditing] = useState<LostFoundItem | null>(null)
  const [releasing, setReleasing] = useState<LostFoundItem | null>(null)
  const [disposing, setDisposing] = useState<LostFoundItem | null>(null)
  const [photographing, setPhotographing] = useState<LostFoundItem | null>(null)

  const { data: sites = [] } = useQuery({ queryKey: ['sites'], queryFn: () => getSites() })

  const { data: items = [], isLoading } = useQuery({
    queryKey: ['lost-found', siteFilter, statusFilter, categoryFilter, search],
    queryFn: () => listLostFound({
      site_id: siteFilter || undefined,
      status_filter: statusFilter || undefined,
      category: categoryFilter || undefined,
      search: search.trim() || undefined,
    }),
  })

  const { data: summary } = useQuery({
    queryKey: ['lost-found-summary', siteFilter],
    queryFn: () => getLostFoundSummary(siteFilter || undefined),
  })

  const siteOptions = useMemo(
    () => [{ value: '', label: 'All sites' },
           ...sites.map((s) => ({ value: s.id, label: s.name }))],
    [sites],
  )

  return (
    <Box sx={{ display: 'flex', gap: 1.5, alignItems: 'flex-start' }}>
      <Box sx={{ flex: 1, minWidth: 0 }}>
        <PageHeader
          pageKey="lost-found"
          action={
            <Button size="small" variant="contained" startIcon={<AddIcon />}
                    onClick={() => setAdding(true)}>
              Book in
            </Button>
          }
        />

        {summary && summary.due_for_disposal > 0 && (
          <Alert severity="warning" sx={{ mb: 1.5 }}>
            {summary.due_for_disposal} {summary.due_for_disposal === 1 ? 'item has' : 'items have'} been
            held longer than {summary.retention_days} days. Decide what happens to them — an item kept
            indefinitely is personal property nobody has accounted for.
          </Alert>
        )}

        <Stack direction="row" spacing={1} sx={{ mb: 1.5 }} alignItems="center">
          <TextField
            size="small" placeholder="Search description or location" value={search}
            onChange={(e) => setSearch(e.target.value)}
            slotProps={{ input: { startAdornment: <SearchIcon sx={{ fontSize: 17, mr: 0.75, color: 'text.disabled' }} /> } }}
            sx={{ maxWidth: 320, flex: 1 }}
          />
          {summary && (
            <Stack direction="row" spacing={0.75}>
              <Chip size="small" label={`${summary.held} held`} sx={{ height: 20, fontSize: '0.65rem' }} />
              <Chip size="small" label={`${summary.claimed} claimed`} sx={{ height: 20, fontSize: '0.65rem' }} />
            </Stack>
          )}
        </Stack>

        {isLoading ? (
          <Box sx={{ display: 'grid', gap: 1.25, gridTemplateColumns: 'repeat(auto-fill, minmax(250px, 1fr))' }}>
            {[0, 1, 2, 3, 4, 5].map((i) => <Skeleton key={i} variant="rounded" height={150} />)}
          </Box>
        ) : items.length === 0 ? (
          <GlassCard sx={{ p: 4, textAlign: 'center' }}>
            <Inventory2Icon sx={{ fontSize: 34, color: 'text.disabled', mb: 1 }} />
            <Typography color="text.secondary">
              Nothing here. Book in property as it is handed to the guardhouse.
            </Typography>
          </GlassCard>
        ) : (
          <Box sx={{
            display: 'grid', gap: 1.25, alignItems: 'start',
            gridTemplateColumns: 'repeat(auto-fill, minmax(250px, 1fr))',
          }}>
            {items.map((row) => (
              <ItemCard
                key={row.id} row={row} canDispose={canDispose}
                onRelease={() => setReleasing(row)}
                onDispose={() => setDisposing(row)}
                onEdit={() => setEditing(row)}
                onPhoto={() => setPhotographing(row)}
              />
            ))}
          </Box>
        )}
      </Box>

      <FilterRail
        storageKey="lost-found"
        groups={[
          {
            key: 'status', label: 'Status',
            options: [
              { value: '', label: 'Everything' },
              { value: 'held', label: 'Held' },
              { value: 'claimed', label: 'Claimed' },
              { value: 'disposed', label: 'Disposed' },
              { value: 'handed_to_police', label: 'Handed to police' },
            ],
            value: statusFilter, onChange: setStatusFilter,
          },
          {
            key: 'site', label: 'Site', options: siteOptions,
            value: siteFilter, onChange: setSiteFilter,
          },
          {
            key: 'category', label: 'Category',
            options: [{ value: '', label: 'All categories' },
                      ...LOST_FOUND_CATEGORIES.map((c) => ({ value: c, label: c }))],
            value: categoryFilter, onChange: setCategoryFilter,
          },
        ]}
      />

      {(adding || editing) && (
        <ItemEditorDialog
          row={editing} sites={sites}
          onClose={() => { setAdding(false); setEditing(null) }}
        />
      )}
      {releasing && <ReleaseDialog row={releasing} onClose={() => setReleasing(null)} />}
      {disposing && <DisposeDialog row={disposing} onClose={() => setDisposing(null)} />}
      {photographing && (
        <PhotoDialog row={photographing} onClose={() => setPhotographing(null)} />
      )}
    </Box>
  )
}

export default LostFoundPage
