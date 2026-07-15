import { useRef, useState } from 'react'
import {
  Box, Tabs, Tab, Typography, Table, TableBody, TableCell, TableContainer,
  TableHead, TableRow, Chip, IconButton, Button, Dialog, DialogTitle,
  DialogContent, DialogActions, TextField, Select, MenuItem, FormControl,
  InputLabel, Skeleton, Paper, Tooltip, CircularProgress, Alert,
} from '@mui/material'
import DeleteIcon from '@mui/icons-material/Delete'
import AddIcon from '@mui/icons-material/Add'
import UploadIcon from '@mui/icons-material/Upload'
import PhotoCameraIcon from '@mui/icons-material/PhotoCamera'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { GlassCard } from '@/components/common/GlassCard'
import { PermissionGuard } from '@/components/common/PermissionGuard'
import { getPlateWatchlist, addPlateEntry, deletePlateEntry, getFaceWatchlist, deleteFaceEntry, enrollFace, bulkImportPlates, type BulkImportResult } from '@/api/watchlist'

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
                  <Typography key={e.row} variant="caption" color="error" display="block">
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

function PlateTable() {
  const [open, setOpen] = useState(false)
  const [importOpen, setImportOpen] = useState(false)
  const [plate, setPlate] = useState('')
  const [listType, setListType] = useState<'allow' | 'block'>('block')
  const [reason, setReason] = useState('')
  const queryClient = useQueryClient()

  const { data, isLoading } = useQuery({ queryKey: ['plate-watchlist'], queryFn: getPlateWatchlist })

  const { mutate: add, isPending } = useMutation({
    mutationFn: addPlateEntry,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['plate-watchlist'] })
      setOpen(false)
      setPlate('')
      setReason('')
    },
  })

  const { mutate: remove } = useMutation({
    mutationFn: deletePlateEntry,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['plate-watchlist'] }),
  })

  return (
    <>
      <Box sx={{ display: 'flex', justifyContent: 'flex-end', gap: 1, p: 2 }}>
        <PermissionGuard permission="watchlist:manage">
          <Button startIcon={<UploadIcon />} variant="outlined" size="small" onClick={() => setImportOpen(true)}>
            Import CSV
          </Button>
          <Button startIcon={<AddIcon />} variant="contained" size="small" onClick={() => setOpen(true)}>
            Add Plate
          </Button>
        </PermissionGuard>
      </Box>
      <BulkImportDialog open={importOpen} onClose={() => setImportOpen(false)} />
      <TableContainer component={Paper} elevation={0} sx={{ background: 'transparent' }}>
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell>Plate</TableCell>
              <TableCell>List Type</TableCell>
              <TableCell>Reason</TableCell>
              <TableCell>Active</TableCell>
              <TableCell>Added</TableCell>
              <TableCell align="right">Action</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {isLoading
              ? Array.from({ length: 4 }).map((_, i) => (
                  <TableRow key={i}>{Array.from({ length: 6 }).map((__, j) => <TableCell key={j}><Skeleton /></TableCell>)}</TableRow>
                ))
              : data?.map((e) => (
                  <TableRow key={e.id} hover>
                    <TableCell><Typography variant="body2" sx={{ fontWeight: 700 }}>{e.plate_number}</Typography></TableCell>
                    <TableCell><Chip label={e.list_type} size="small" color={e.list_type === 'block' ? 'error' : 'success'} /></TableCell>
                    <TableCell><Typography variant="caption" color="text.secondary">{e.reason ?? '—'}</Typography></TableCell>
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

      <Dialog open={open} onClose={() => setOpen(false)} maxWidth="xs" fullWidth>
        <DialogTitle>Add Plate to Watchlist</DialogTitle>
        <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 2 }}>
          <TextField label="Plate Number" value={plate} onChange={(e) => setPlate(e.target.value)} size="small" fullWidth />
          <FormControl size="small" fullWidth>
            <InputLabel>List Type</InputLabel>
            <Select value={listType} label="List Type" onChange={(e) => setListType(e.target.value as 'allow' | 'block')}>
              <MenuItem value="block">Block</MenuItem>
              <MenuItem value="allow">Allow</MenuItem>
            </Select>
          </FormControl>
          <TextField label="Reason (optional)" value={reason} onChange={(e) => setReason(e.target.value)} size="small" fullWidth />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setOpen(false)}>Cancel</Button>
          <Button
            variant="contained"
            disabled={!plate.trim() || isPending}
            onClick={() => add({ plate_number: plate.trim().toUpperCase(), list_type: listType, reason: reason || undefined })}
          >
            Add
          </Button>
        </DialogActions>
      </Dialog>
    </>
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
