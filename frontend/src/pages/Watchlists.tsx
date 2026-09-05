import { useRef, useState } from 'react'
import {
  Box, Tabs, Tab, Typography, Table, TableBody, TableCell, TableContainer,
  TableHead, TableRow, Chip, IconButton, Button, Dialog, DialogTitle,
  DialogContent, DialogActions, TextField, Select, MenuItem, FormControl,
  InputLabel, Skeleton, Paper, Tooltip, CircularProgress, Alert,
} from '@mui/material'
import DeleteIcon from '@mui/icons-material/Delete'
import EditIcon from '@mui/icons-material/Edit'
import AddIcon from '@mui/icons-material/Add'
import UploadIcon from '@mui/icons-material/Upload'
import PhotoCameraIcon from '@mui/icons-material/PhotoCamera'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { GlassCard } from '@/components/common/GlassCard'
import { FilterRail, type FilterGroup } from '@/components/common/FilterRail'
import { PermissionGuard } from '@/components/common/PermissionGuard'
import {
  getPlateWatchlist, addPlateEntry, updatePlateEntry, deletePlateEntry,
  getFaceWatchlist, deleteFaceEntry, enrollFace, bulkImportPlates,
  CATEGORY_META, VEHICLE_CATEGORIES,
  type BulkImportResult, type PlateRegistryInput,
} from '@/api/watchlist'
import type { WatchlistEntry, VehicleCategory } from '@/types/api'
import { PageHeader } from '@/components/common/PageHeader'

function BulkImportDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const qc = useQueryClient()
  const fileRef = useRef<HTMLInputElement>(null)
  const [file, setFile] = useState<File | null>(null)
  const [result, setResult] = useState<BulkImportResult | null>(null)

  const reset = () => { setFile(null); setResult(null) }
  const handleClose = () => { reset(); onClose() }

  const { mutate: doImport, isPending, error } = useMutation({
    mutationFn: () => bulkImportPlates(file!),
    onSuccess: (data) => {
      setResult(data)
      qc.invalidateQueries({ queryKey: ['plate-watchlist'] })
    },
  })

  const errorMsg = error
    ? (error as { response?: { data?: { detail?: string } } }).response?.data?.detail ?? String(error)
    : null

  return (
    <Dialog open={open} onClose={handleClose} maxWidth="sm" fullWidth>
      <DialogTitle>Bulk Import Plates (CSV)</DialogTitle>
      <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: '16px !important' }}>
        <Typography variant="body2" color="text.secondary">
          Upload a CSV with columns: <code>plate_number</code>, <code>list_type</code> (allow/block), <code>reason</code> (optional), <code>expires_at</code> (optional ISO date). Max 1,000 rows.
        </Typography>
        <Button variant="outlined" startIcon={<UploadIcon />} onClick={() => fileRef.current?.click()}>
          {file ? file.name : 'Choose CSV file'}
        </Button>
        <input ref={fileRef} type="file" accept=".csv,text/csv" style={{ display: 'none' }}
          onChange={(e) => { setFile(e.target.files?.[0] ?? null); setResult(null) }} />

        {errorMsg && <Alert severity="error">{errorMsg}</Alert>}

        {result && (
          <Box>
            <Alert severity={result.errors.length > 0 ? 'warning' : 'success'} sx={{ mb: 1 }}>
              Imported <strong>{result.imported}</strong> of <strong>{result.total}</strong> rows
              {result.duplicates > 0 && ` · ${result.duplicates} duplicate(s) skipped`}
              {result.errors.length > 0 && ` · ${result.errors.length} error(s)`}
            </Alert>
            {result.errors.length > 0 && (
              <Box sx={{ maxHeight: 160, overflowY: 'auto' }}>
                {result.errors.map((e) => (
                  <Typography key={e.row} variant="caption" color="error" sx={{ display: "block" }}>
                    Row {e.row}: {e.error}
                  </Typography>
                ))}
              </Box>
            )}
          </Box>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={handleClose}>Close</Button>
        <Button variant="contained" disabled={!file || isPending}
          onClick={() => doImport()}
          startIcon={isPending ? <CircularProgress size={14} /> : <UploadIcon />}>
          {isPending ? 'Importing…' : 'Import'}
        </Button>
      </DialogActions>
    </Dialog>
  )
}

/** Colour-coded category chip. The tooltip states what the category actually
 * DOES at a gate — an admin picking "Watchlist" should know it never
 * auto-opens, which is not obvious from the word alone. */
function CategoryChip({ category }: { category: VehicleCategory }) {
  const meta = CATEGORY_META[category] ?? CATEGORY_META.unknown
  return (
    <Tooltip title={meta.hint}>
      <Chip
        label={meta.label}
        size="small"
        sx={{ bgcolor: `${meta.color}22`, color: meta.color, fontWeight: 600 }}
      />
    </Tooltip>
  )
}

/** Validity state derived on the client from valid_from/valid_to — mirrors the
 * decision engine's own not-yet-valid / expired split so what an admin sees
 * here matches what happens at the gate. */
function validityOf(e: WatchlistEntry): { label: string; color: string } | null {
  const today = new Date().toISOString().slice(0, 10)
  if (e.valid_from && today < e.valid_from) return { label: `From ${e.valid_from}`, color: '#FF9800' }
  if (e.valid_to && today > e.valid_to) return { label: `Expired ${e.valid_to}`, color: '#FF4560' }
  if (e.valid_to) return { label: `Until ${e.valid_to}`, color: '#7A8195' }
  return null
}

const EMPTY_FORM: PlateRegistryInput = {
  plate_number: '', category: 'watchlist', owner_name: '', company: '',
  vehicle_type: '', vehicle_color: '', valid_from: '', valid_to: '', remarks: '',
}

function PlateTable() {
  const [open, setOpen] = useState(false)
  const [importOpen, setImportOpen] = useState(false)
  const [editing, setEditing] = useState<WatchlistEntry | null>(null)
  const [form, setForm] = useState<PlateRegistryInput>(EMPTY_FORM)
  const [error, setError] = useState<string | null>(null)
  const [categoryFilter, setCategoryFilter] = useState<VehicleCategory | ''>('')
  const [search, setSearch] = useState('')
  const queryClient = useQueryClient()

  const { data, isLoading } = useQuery({
    queryKey: ['plate-watchlist', categoryFilter, search],
    queryFn: () => getPlateWatchlist({
      category: categoryFilter || undefined,
      search: search.trim() || undefined,
    }),
  })

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['plate-watchlist'] })

  const { mutate: save, isPending } = useMutation({
    mutationFn: () => {
      // Blank optional fields go as null, not '' — an empty string would fail
      // the backend's date parsing on valid_from/valid_to.
      const clean = Object.fromEntries(
        Object.entries(form).map(([k, v]) => [k, v === '' ? null : v]),
      ) as PlateRegistryInput
      clean.plate_number = form.plate_number.trim().toUpperCase()
      return editing ? updatePlateEntry(editing.id, clean) : addPlateEntry(clean)
    },
    onSuccess: () => { invalidate(); setOpen(false); setEditing(null); setForm(EMPTY_FORM) },
    onError: (e: any) => setError(e?.response?.data?.detail ?? 'Could not save vehicle'),
  })

  const { mutate: remove } = useMutation({
    mutationFn: deletePlateEntry,
    onSuccess: invalidate,
  })

  const openAdd = () => { setEditing(null); setForm(EMPTY_FORM); setError(null); setOpen(true) }
  const openEdit = (e: WatchlistEntry) => {
    setEditing(e)
    setForm({
      plate_number: e.plate_number, category: e.category,
      owner_name: e.owner_name ?? '', company: e.company ?? '',
      vehicle_type: e.vehicle_type ?? '', vehicle_color: e.vehicle_color ?? '',
      valid_from: e.valid_from ?? '', valid_to: e.valid_to ?? '', remarks: e.remarks ?? '',
    })
    setError(null)
    setOpen(true)
  }
  const set = <K extends keyof PlateRegistryInput>(k: K, v: PlateRegistryInput[K]) =>
    setForm((f) => ({ ...f, [k]: v }))

  // Category is enumerable so it moves to the rail; the free-text plate/owner
  // search stays on the page — it is one line, not a wrapping chip row, and a
  // search box you cannot see is a search box nobody uses.
  const filterGroups: FilterGroup[] = [{
    key: 'category',
    label: 'Category',
    value: categoryFilter,
    onChange: (v) => setCategoryFilter(v as VehicleCategory | ''),
    options: [
      { value: '', label: 'All categories' },
      ...VEHICLE_CATEGORIES.map((c) => ({ value: c, label: CATEGORY_META[c].label })),
    ],
  }]

  return (
    <Box sx={{ display: 'flex', alignItems: 'flex-start', gap: 2 }}>
      <Box sx={{ flex: 1, minWidth: 0 }}>
      <Box sx={{ display: 'flex', gap: 1, p: 2, alignItems: 'center', flexWrap: 'wrap' }}>
        <TextField
          size="small" label="Search plate, owner or company" value={search}
          onChange={(e) => setSearch(e.target.value)} sx={{ minWidth: 260 }}
        />
        <Box sx={{ flex: 1 }} />
        <PermissionGuard permission="watchlist:manage">
          <Button startIcon={<UploadIcon />} variant="outlined" size="small" onClick={() => setImportOpen(true)}>
            Import CSV
          </Button>
          <Button startIcon={<AddIcon />} variant="contained" size="small" onClick={openAdd}>
            Add Vehicle
          </Button>
        </PermissionGuard>
      </Box>
      <BulkImportDialog open={importOpen} onClose={() => setImportOpen(false)} />
      <TableContainer component={Paper} elevation={0} sx={{ background: 'transparent' }}>
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell>Plate</TableCell>
              <TableCell>Category</TableCell>
              <TableCell>Owner / Company</TableCell>
              <TableCell>Vehicle</TableCell>
              <TableCell>Validity</TableCell>
              <TableCell>Active</TableCell>
              <TableCell align="right">Actions</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {isLoading
              ? Array.from({ length: 4 }).map((_, i) => (
                  <TableRow key={i}>{Array.from({ length: 7 }).map((__, j) => <TableCell key={j}><Skeleton /></TableCell>)}</TableRow>
                ))
              : data?.length === 0
              ? (
                <TableRow>
                  <TableCell colSpan={7}>
                    <Typography color="text.secondary" sx={{ py: 3, textAlign: 'center' }}>
                      No vehicles match. Registered vehicles drive the gate decision — an
                      unregistered plate is held for an operator.
                    </Typography>
                  </TableCell>
                </TableRow>
              )
              : data?.map((e) => {
                  const validity = validityOf(e)
                  return (
                    <TableRow key={e.id} hover>
                      <TableCell>
                        <Typography variant="body2" sx={{ fontWeight: 700, letterSpacing: 0.5 }}>
                          {e.plate_number}
                        </Typography>
                      </TableCell>
                      <TableCell><CategoryChip category={e.category} /></TableCell>
                      <TableCell>
                        <Typography variant="body2">{e.owner_name ?? '—'}</Typography>
                        {e.company && (
                          <Typography variant="caption" color="text.secondary">{e.company}</Typography>
                        )}
                      </TableCell>
                      <TableCell>
                        <Typography variant="caption" color="text.secondary">
                          {[e.vehicle_color, e.vehicle_type].filter(Boolean).join(' ') || '—'}
                        </Typography>
                      </TableCell>
                      <TableCell>
                        {validity
                          ? <Chip label={validity.label} size="small" variant="outlined"
                              sx={{ color: validity.color, borderColor: validity.color }} />
                          : <Typography variant="caption" color="text.secondary">No limit</Typography>}
                      </TableCell>
                      <TableCell>
                        <Chip label={e.is_active ? 'Yes' : 'No'} size="small" variant="outlined"
                          color={e.is_active ? 'success' : 'default'} />
                      </TableCell>
                      <TableCell align="right">
                        <PermissionGuard permission="watchlist:manage">
                          <Tooltip title="Edit">
                            <IconButton size="small" onClick={() => openEdit(e)}>
                              <EditIcon fontSize="small" />
                            </IconButton>
                          </Tooltip>
                          <Tooltip title="Deactivate">
                            <IconButton size="small" color="error" onClick={() => remove(e.id)}>
                              <DeleteIcon fontSize="small" />
                            </IconButton>
                          </Tooltip>
                        </PermissionGuard>
                      </TableCell>
                    </TableRow>
                  )
                })}
          </TableBody>
        </Table>
      </TableContainer>

      <Dialog open={open} onClose={() => setOpen(false)} maxWidth="sm" fullWidth>
        <DialogTitle>{editing ? `Edit ${editing.plate_number}` : 'Add Vehicle to Registry'}</DialogTitle>
        <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: '12px !important' }}>
          {error && <Alert severity="error" onClose={() => setError(null)}>{error}</Alert>}
          <Box sx={{ display: 'flex', gap: 2 }}>
            <TextField
              label="Plate Number" value={form.plate_number} size="small" sx={{ flex: 1 }}
              onChange={(e) => set('plate_number', e.target.value)}
              slotProps={{ htmlInput: { style: { textTransform: 'uppercase', letterSpacing: 1 } } }}
            />
            <FormControl size="small" sx={{ flex: 1 }}>
              <InputLabel>Category</InputLabel>
              <Select
                value={form.category} label="Category"
                onChange={(e) => set('category', e.target.value as VehicleCategory)}
              >
                {VEHICLE_CATEGORIES.map((c) => (
                  <MenuItem key={c} value={c}>{CATEGORY_META[c].label}</MenuItem>
                ))}
              </Select>
            </FormControl>
          </Box>
          {/* Spells out what this category does at the gate, so picking one is
              an informed choice rather than a guess at the wording. */}
          <Alert severity="info" sx={{ py: 0.25 }}>
            {CATEGORY_META[form.category]?.hint}
          </Alert>
          <Box sx={{ display: 'flex', gap: 2 }}>
            <TextField label="Owner Name" value={form.owner_name ?? ''} size="small" sx={{ flex: 1 }}
              onChange={(e) => set('owner_name', e.target.value)} />
            <TextField label="Company" value={form.company ?? ''} size="small" sx={{ flex: 1 }}
              onChange={(e) => set('company', e.target.value)} />
          </Box>
          <Box sx={{ display: 'flex', gap: 2 }}>
            <TextField label="Vehicle Type" placeholder="sedan, van, lorry…" value={form.vehicle_type ?? ''}
              size="small" sx={{ flex: 1 }} onChange={(e) => set('vehicle_type', e.target.value)} />
            <TextField label="Colour" value={form.vehicle_color ?? ''} size="small" sx={{ flex: 1 }}
              onChange={(e) => set('vehicle_color', e.target.value)} />
          </Box>
          <Box sx={{ display: 'flex', gap: 2 }}>
            <TextField
              label="Valid From" type="date" size="small" sx={{ flex: 1 }}
 value={form.valid_from ?? ''}
              onChange={(e) => set('valid_from', e.target.value)}
              helperText="Blank = no start limit" slotProps={{ inputLabel: { shrink: true } }}
            />
            <TextField
              label="Valid To" type="date" size="small" sx={{ flex: 1 }}
 value={form.valid_to ?? ''}
              onChange={(e) => set('valid_to', e.target.value)}
              helperText="Blank = never expires" slotProps={{ inputLabel: { shrink: true } }}
            />
          </Box>
          <TextField label="Remarks" value={form.remarks ?? ''} size="small" fullWidth multiline minRows={2}
            onChange={(e) => set('remarks', e.target.value)} />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setOpen(false)}>Cancel</Button>
          <Button
            variant="contained"
            disabled={!form.plate_number.trim() || isPending}
            onClick={() => save()}
          >
            {editing ? 'Save' : 'Add'}
          </Button>
        </DialogActions>
      </Dialog>
      </Box>

      <FilterRail groups={filterGroups} storageKey="watchlist-plates" />
    </Box>
  )
}

// ──────────────────────────────────────────────────────────
// Face enrollment dialog — uploads a photo; backend runs ArcFace extraction
// ──────────────────────────────────────────────────────────

function EnrollFaceDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const queryClient = useQueryClient()
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [personName, setPersonName] = useState('')
  const [listType, setListType] = useState<'allow' | 'block'>('block')
  const [file, setFile] = useState<File | null>(null)
  const [preview, setPreview] = useState<string | null>(null)

  const reset = () => {
    setPersonName('')
    setListType('block')
    setFile(null)
    setPreview(null)
  }

  const { mutate: enroll, isPending, error, reset: resetMutation } = useMutation({
    mutationFn: () => {
      const fd = new FormData()
      fd.append('person_name', personName)
      fd.append('list_type', listType)
      fd.append('file', file!)
      return enrollFace(fd)
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['face-watchlist'] })
      reset()
      resetMutation()
      onClose()
    },
  })

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0]
    if (!f) return
    setFile(f)
    setPreview(URL.createObjectURL(f))
  }

  const handleClose = () => {
    reset()
    resetMutation()
    onClose()
  }

  const errorMsg = error
    ? (error as { response?: { data?: { detail?: string } } }).response?.data?.detail ?? String(error)
    : null

  return (
    <Dialog open={open} onClose={handleClose} maxWidth="xs" fullWidth>
      <DialogTitle>Enroll Face</DialogTitle>
      <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: '16px !important' }}>
        <TextField
          label="Person Name"
          value={personName}
          onChange={(e) => setPersonName(e.target.value)}
          size="small"
          fullWidth
          required
        />
        <FormControl size="small" fullWidth>
          <InputLabel>List Type</InputLabel>
          <Select
            value={listType}
            label="List Type"
            onChange={(e) => setListType(e.target.value as 'allow' | 'block')}
          >
            <MenuItem value="block">Block (Blacklist)</MenuItem>
            <MenuItem value="allow">Allow (VIP / Whitelist)</MenuItem>
          </Select>
        </FormControl>

        <Button
          variant="outlined"
          startIcon={<PhotoCameraIcon />}
          onClick={() => fileInputRef.current?.click()}
          fullWidth
        >
          {file ? file.name : 'Upload Face Photo (JPEG / PNG)'}
        </Button>
        <input
          ref={fileInputRef}
          type="file"
          accept="image/jpeg,image/jpg,image/png"
          style={{ display: 'none' }}
          onChange={handleFileChange}
        />

        {preview && (
          <Box sx={{ textAlign: 'center' }}>
            <img
              src={preview}
              alt="Face preview"
              style={{ maxWidth: '100%', maxHeight: 200, borderRadius: 8, objectFit: 'contain' }}
            />
          </Box>
        )}

        {errorMsg && <Alert severity="error" sx={{ fontSize: '0.8rem' }}>{errorMsg}</Alert>}

        <Typography variant="caption" color="text.secondary">
          The server extracts the ArcFace embedding automatically. The first enrollment
          may take longer while the face recognition model downloads (~330 MB).
        </Typography>
      </DialogContent>
      <DialogActions>
        <Button onClick={handleClose} disabled={isPending}>Cancel</Button>
        <Button
          variant="contained"
          disabled={!personName.trim() || !file || isPending}
          onClick={() => enroll()}
          startIcon={isPending ? <CircularProgress size={14} /> : undefined}
        >
          {isPending ? 'Processing…' : 'Enroll'}
        </Button>
      </DialogActions>
    </Dialog>
  )
}

function FaceTable() {
  const queryClient = useQueryClient()
  const [enrollOpen, setEnrollOpen] = useState(false)
  const { data, isLoading } = useQuery({ queryKey: ['face-watchlist'], queryFn: getFaceWatchlist })
  const { mutate: remove } = useMutation({
    mutationFn: deleteFaceEntry,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['face-watchlist'] }),
  })

  return (
    <>
      <Box sx={{ display: 'flex', justifyContent: 'flex-end', p: 2 }}>
        <PermissionGuard permission="watchlist:manage">
          <Button startIcon={<PhotoCameraIcon />} variant="contained" size="small" onClick={() => setEnrollOpen(true)}>
            Enroll Face
          </Button>
        </PermissionGuard>
      </Box>
      <TableContainer component={Paper} elevation={0} sx={{ background: 'transparent' }}>
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell>Name</TableCell>
              <TableCell>List Type</TableCell>
              <TableCell>Active</TableCell>
              <TableCell>Added</TableCell>
              <TableCell align="right">Action</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {isLoading
              ? Array.from({ length: 4 }).map((_, i) => (
                  <TableRow key={i}>{Array.from({ length: 5 }).map((__, j) => <TableCell key={j}><Skeleton /></TableCell>)}</TableRow>
                ))
              : data?.map((e) => (
                  <TableRow key={e.id} hover>
                    <TableCell><Typography variant="body2" sx={{ fontWeight: 600 }}>{e.person_name}</Typography></TableCell>
                    <TableCell><Chip label={e.list_type} size="small" color={e.list_type === 'block' ? 'error' : 'success'} /></TableCell>
                    <TableCell><Chip label={e.is_active ? 'Yes' : 'No'} size="small" variant="outlined" color={e.is_active ? 'success' : 'default'} /></TableCell>
                    <TableCell><Typography variant="caption" color="text.secondary">{new Date(e.created_at).toLocaleDateString()}</Typography></TableCell>
                    <TableCell align="right">
                      <PermissionGuard permission="watchlist:manage">
                        <Tooltip title="Remove">
                          <IconButton size="small" color="error" onClick={() => remove(e.id)}>
                            <DeleteIcon fontSize="small" />
                          </IconButton>
                        </Tooltip>
                      </PermissionGuard>
                    </TableCell>
                  </TableRow>
                ))}
          </TableBody>
        </Table>
      </TableContainer>
      <EnrollFaceDialog open={enrollOpen} onClose={() => setEnrollOpen(false)} />
    </>
  )
}

export default function Watchlists() {
  const [tab, setTab] = useState(0)

  return (
    <Box>
      <PageHeader pageKey="watchlists" />
      <GlassCard>
        <Box sx={{ borderBottom: 1, borderColor: 'rgba(255,255,255,0.1)' }}>
          <Tabs value={tab} onChange={(_, v) => setTab(v)}>
            <Tab label="Plate Watchlist" />
            <Tab label="Face Watchlist" />
          </Tabs>
        </Box>
        {tab === 0 && <PlateTable />}
        {tab === 1 && <FaceTable />}
      </GlassCard>
    </Box>
  )
}
