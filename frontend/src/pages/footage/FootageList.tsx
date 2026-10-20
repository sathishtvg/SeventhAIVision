/**
 * Footage Review — the videos uploaded to be searched and reviewed.
 *
 * Each video is kept 30 days from when its upload began, then removed with
 * everything worked out from it, unless somebody has placed a hold on it. An
 * upload that stopped can be carried on with the same file; one left
 * unfinished for a day is removed.
 *
 * Nothing here raises an alert or an incident: what is found in a video stays
 * in Footage Review until a person does something with it.
 */
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Alert, Box, Button, Chip, LinearProgress, Link, MenuItem, Skeleton, Table, TableBody, TableCell, TableContainer, TableHead,
  TablePagination, TableRow, TextField, Tooltip, Typography,
} from '@mui/material'
import CloudUploadIcon from '@mui/icons-material/CloudUpload'
import { keepPreviousData, useQuery, useQueryClient } from '@tanstack/react-query'
import Stack from '@/components/common/Stack'
import { GlassCard } from '@/components/common/GlassCard'
import { PageHeader } from '@/components/common/PageHeader'
import { ErrorState, RefreshingLine } from '@/components/states'
import { WhyDialog } from '@/components/investigations/InvestigationDialogs'
import UploadDialog from '@/components/footage/UploadDialog'
import { FootageNav } from '@/components/footage/FootageNav'
import { keptText } from '@/components/footage/footageFormat'
import { fmt } from '@/components/evidence/evidenceFormat'
import { useAuthStore } from '@/store/auth'
import { cancelUpload, deleteFootage, holdFootage, listFootage, sizeText } from '@/api/footage'
import type { FootageStatus, FootageVideo } from '@/api/footage'

const STATUS_COLOR: Record<FootageStatus, 'default' | 'info' | 'warning' | 'success' | 'error'> = {
  UPLOADING: 'info', QUEUED: 'default', PROCESSING: 'warning', READY: 'success', FAILED: 'error',
}
const STATUSES: FootageStatus[] = ['UPLOADING', 'QUEUED', 'PROCESSING', 'READY', 'FAILED']
const STATUS_WORDS: Record<FootageStatus, string> = {
  UPLOADING: 'Uploading', QUEUED: 'Waiting to be analysed', PROCESSING: 'Being analysed', READY: 'Ready',
  FAILED: 'Could not be analysed',
}

export default function FootageList() {
  const qc = useQueryClient()
  const navigate = useNavigate()
  const me = useAuthStore((s) => s.user?.id)
  const [q, setQ] = useState('')
  const [status, setStatus] = useState<FootageStatus | ''>('')
  const [page, setPage] = useState(0)
  const [rows, setRows] = useState(25)
  const [uploading, setUploading] = useState(false)
  const [resume, setResume] = useState<FootageVideo | null>(null)
  const [holding, setHolding] = useState<FootageVideo | null>(null)
  const [deleting, setDeleting] = useState<FootageVideo | null>(null)
  const { data, isLoading, isLoadingError: dataFailed, error: dataError, refetch: refetchData, isPlaceholderData: dataRefreshing } = useQuery({
    queryKey: ['footage', q, status, page, rows],
    placeholderData: keepPreviousData,
    queryFn: () => listFootage({ q: q || undefined, status: status || undefined, limit: rows, offset: page * rows }),
    // A video being analysed says how far it has got; nothing else here changes by itself.
    refetchInterval: (query) => (query.state.data?.items.some((v) => v.status === 'QUEUED' || v.status === 'PROCESSING') ? 5000 : false),
  })
  const videos = data?.items ?? []
  const may = data?.may
  const refresh = () => qc.invalidateQueries({ queryKey: ['footage'] })

  return (
    <Box sx={{ p: 3 }}>
      <PageHeader title="Footage Review"
                  subtitle="Videos uploaded to be searched and reviewed. Each is kept 30 days unless it is held."
                  action={may?.upload ? (
                    <Button variant="contained" startIcon={<CloudUploadIcon />} onClick={() => setUploading(true)}>
                      Upload a video</Button>) : undefined} />
      <FootageNav />
      <GlassCard sx={{ p: 2, mb: 2 }}>
        <Stack direction="row" sx={{ gap: 1.5, flexWrap: 'wrap', alignItems: 'center' }}>
          <TextField size="small" label="Search titles, places and file names" value={q} sx={{ minWidth: 280 }}
                     onChange={(e) => { setQ(e.target.value); setPage(0) }} />
          <TextField select size="small" label="Status" value={status} sx={{ minWidth: 200 }}
                     onChange={(e) => { setStatus(e.target.value as FootageStatus | ''); setPage(0) }}>
            <MenuItem value="">Any</MenuItem>
            {STATUSES.map((s) => <MenuItem key={s} value={s}>{STATUS_WORDS[s]}</MenuItem>)}
          </TextField>
          <Typography variant="caption" color="text.secondary">
            Nothing found in a video raises an alert or an incident. It stays here until somebody acts on it.</Typography>
        </Stack>
      </GlassCard>
      <GlassCard sx={{ p: 2 }}>
        {dataRefreshing && <RefreshingLine />}
        {isLoading ? <Skeleton height={240} /> : dataFailed ? <ErrorState compact error={dataError} onRetry={refetchData} /> : !videos.length ? (
          <Alert severity="info">{q || status ? 'No video matches.' : 'No video has been uploaded yet.'}</Alert>
        ) : (
          <TableContainer>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>Video</TableCell><TableCell>Where</TableCell><TableCell>Status</TableCell>
                  <TableCell>Uploaded</TableCell><TableCell>Kept</TableCell><TableCell />
                </TableRow>
              </TableHead>
              <TableBody>
                {videos.map((v) => {
                  const mine = v.uploaded_by_user_id === me
                  return (
                    <TableRow key={v.id} data-testid="footage-row">
                      <TableCell sx={{ maxWidth: 320 }}>
                        {v.status === 'UPLOADING' ? (
                          <Typography variant="body2" sx={{ fontWeight: 600 }}>{v.title}</Typography>
                        ) : (
                          <Link component="button" variant="body2" sx={{ fontWeight: 600, textAlign: 'left' }}
                                onClick={() => navigate(`/footage/${v.id}`)}>{v.title}</Link>
                        )}
                        <Typography variant="caption" color="text.secondary">
                          {v.original_filename} · {sizeText(v.size_bytes)}{v.filmed_at ? ` · filmed ${fmt(v.filmed_at)}` : ''}</Typography>
                      </TableCell>
                      <TableCell>{[v.site_name, v.place].filter(Boolean).join(' · ') || '—'}</TableCell>
                      <TableCell sx={{ minWidth: 180 }}>
                        <Chip size="small" color={STATUS_COLOR[v.status]} label={v.status_label} />
                        {v.status === 'UPLOADING' && (
                          <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
                            {sizeText(v.received_bytes)} of {sizeText(v.size_bytes)} arrived</Typography>)}
                        {v.status === 'PROCESSING' && (
                          <LinearProgress variant="determinate" value={v.progress} sx={{ mt: 0.5, height: 4, borderRadius: 2 }} />)}
                        {v.status === 'FAILED' && v.error && (
                          <Typography variant="caption" color="error" sx={{ display: 'block' }}>{v.error}</Typography>)}
                      </TableCell>
                      <TableCell>{fmt(v.created_at)}
                        <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
                          {v.uploaded_by_name ?? '—'}</Typography></TableCell>
                      <TableCell>
                        {v.held ? (
                          <Tooltip title={`${v.hold_reason ?? ''} — ${v.hold_placed_by_name ?? ''}`}>
                            <Chip size="small" color="warning" label="Held" />
                          </Tooltip>
                        ) : keptText(v)}
                      </TableCell>
                      <TableCell align="right" sx={{ whiteSpace: 'nowrap' }}>
                        {v.status === 'UPLOADING' && mine && may?.upload && (
                          <Button size="small" onClick={() => setResume(v)}>Carry on</Button>)}
                        {v.status === 'UPLOADING' && (mine || may?.delete) && (
                          <Button size="small" color="error" onClick={() => { void cancelUpload(v.id).then(refresh) }}>
                            Cancel</Button>)}
                        {v.status !== 'UPLOADING' && !v.held && may?.hold && (
                          <Button size="small" onClick={() => setHolding(v)}>Hold</Button>)}
                        {v.status !== 'UPLOADING' && !v.held && v.status !== 'PROCESSING' && may?.delete && (
                          <Button size="small" color="error" onClick={() => setDeleting(v)}>Delete</Button>)}
                      </TableCell>
                    </TableRow>
                  )
                })}
              </TableBody>
            </Table>
          </TableContainer>
        )}
        <TablePagination component="div" count={data?.total ?? 0} page={page} rowsPerPage={rows}
                         rowsPerPageOptions={[25, 50, 100]} onPageChange={(_, p) => setPage(p)}
                         onRowsPerPageChange={(e) => { setRows(Number(e.target.value)); setPage(0) }} />
      </GlassCard>
      {data?.rules && (
        <UploadDialog open={uploading || !!resume} rules={data.rules} resume={resume}
                      onClose={() => { setUploading(false); setResume(null); void refresh() }}
                      onUploaded={() => { void refresh() }} />
      )}
      <WhyDialog open={!!holding} title="Keep this video past its 30 days" label="Why it must be kept" confirm="Hold it" min={5}
                 hint="A held video is not deleted until the hold is lifted, on the Evidence Holds page, with a reason."
                 onClose={() => setHolding(null)}
                 onConfirm={(text) => (holding ? holdFootage(holding.id, text).then(refresh) : Promise.resolve())} />
      <WhyDialog open={!!deleting} title={`Delete ${deleting?.title ?? 'this video'}`} label="Why it is being deleted"
                 confirm="Delete it" min={5}
                 hint="The video and everything worked out from it are deleted now, and cannot be brought back. The deletion is recorded with your reason."
                 onClose={() => setDeleting(null)}
                 onConfirm={(text) => (deleting ? deleteFootage(deleting.id, text).then(refresh) : Promise.resolve())} />
    </Box>
  )
}
