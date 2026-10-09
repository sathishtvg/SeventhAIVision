/**
 * Privacy zones: the parts of a camera's picture the organisation has chosen
 * not to look at, and where one is drawn.
 *
 * A zone is applied by the server. Within about ten seconds of its being drawn,
 * what is under it is blacked out of the live view, of what the AI is given, of
 * recordings and of the images a patrol keeps — and what is recorded from then
 * on cannot be unmasked. So drawing says what it does before it is done, and
 * deleting asks first and says what that changes.
 *
 * There is no editing: a zone is deleted and drawn again.
 */
import { useState } from 'react'
import {
  Alert, Box, Button, Dialog, DialogActions, DialogContent, DialogTitle, FormControl, IconButton, InputLabel, MenuItem,
  Paper, Select, Table, TableBody, TableCell, TableContainer, TableHead, TableRow, TextField, Tooltip, Typography,
} from '@mui/material'
import AddIcon from '@mui/icons-material/Add'
import DeleteIcon from '@mui/icons-material/Delete'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { getCameras } from '@/api/cameras'
import { apiError, createPrivacyZone, deletePrivacyZone, listPrivacyZones, type PrivacyZone, type ZoneCorner } from '@/api/privacyZones'
import { SkeletonRows } from '@/components/common/SkeletonRows'
import { ZonePolygonEditor } from '@/components/common/ZonePolygonEditor'
import { MASKED_CAMERAS_KEY } from '@/hooks/useMaskedCameras'
import { WHAT_A_ZONE_DOES, WHAT_DELETING_DOES, ZONE_LIMITS } from './privacyZoneWords'

/** The colour a zone is drawn in while it is being placed. It is applied in black. */
const DRAWING_COLOUR = '#B0BEC5'

const when = (iso: string) => new Date(iso).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })

function DrawDialog({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient()
  const [cameraId, setCameraId] = useState('')
  const [name, setName] = useState('')
  const [polygon, setPolygon] = useState<ZoneCorner[]>([])
  const { data: cameras = [] } = useQuery({ queryKey: ['cameras'], queryFn: getCameras })
  const draw = useMutation({
    mutationFn: () => createPrivacyZone({ camera_id: cameraId, name: name.trim(), polygon }),
    onSuccess: async () => {
      await Promise.all([
        qc.invalidateQueries({ queryKey: ['privacy-zones'] }),
        qc.invalidateQueries({ queryKey: MASKED_CAMERAS_KEY }),
      ])
      onClose()
    },
  })
  const ready = !!cameraId && !!name.trim() && polygon.length >= 3

  return (
    <Dialog open onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>Draw a privacy zone</DialogTitle>
      <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: '16px !important' }}>
        <Alert severity="warning" variant="outlined">{WHAT_A_ZONE_DOES}</Alert>
        <FormControl fullWidth>
          <InputLabel id="privacy-zone-camera">Camera</InputLabel>
          <Select labelId="privacy-zone-camera" value={cameraId} label="Camera"
                  onChange={(e) => { setCameraId(e.target.value); setPolygon([]) }}>
            {cameras.map((c) => (
              <MenuItem key={c.id} value={c.id}>{c.name}{c.location ? ` — ${c.location}` : ''}</MenuItem>))}
          </Select>
        </FormControl>
        <TextField label="What it covers" placeholder="The neighbour's window" value={name}
                   onChange={(e) => setName(e.target.value)} slotProps={{ htmlInput: { maxLength: 100 } }} fullWidth />
        {cameraId ? (
          <ZonePolygonEditor cameraId={cameraId} value={polygon} onChange={setPolygon} severityColor={DRAWING_COLOUR} />
        ) : (
          <Typography variant="caption" color="text.disabled">Choose a camera to draw the zone on its picture.</Typography>
        )}
        {draw.error && <Alert severity="error">{apiError(draw.error)}</Alert>}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={draw.isPending}>Cancel</Button>
        <Button variant="contained" onClick={() => draw.mutate()} disabled={!ready || draw.isPending}>
          Mask this part of the picture
        </Button>
      </DialogActions>
    </Dialog>
  )
}

function DeleteDialog({ zone, onClose }: { zone: PrivacyZone; onClose: () => void }) {
  const qc = useQueryClient()
  const remove = useMutation({
    mutationFn: () => deletePrivacyZone(zone.id),
    onSuccess: async () => {
      await Promise.all([
        qc.invalidateQueries({ queryKey: ['privacy-zones'] }),
        qc.invalidateQueries({ queryKey: MASKED_CAMERAS_KEY }),
      ])
      onClose()
    },
  })
  return (
    <Dialog open onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle>Delete this privacy zone?</DialogTitle>
      <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 1.5 }}>
        <Typography variant="body2">
          “{zone.name}” on {zone.camera_name ?? 'this camera'}.
        </Typography>
        <Typography variant="body2" color="text.secondary">{WHAT_DELETING_DOES}</Typography>
        {remove.error && <Alert severity="error">{apiError(remove.error)}</Alert>}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={remove.isPending}>Keep it</Button>
        <Button color="error" variant="contained" onClick={() => remove.mutate()} disabled={remove.isPending}>
          Delete the zone
        </Button>
      </DialogActions>
    </Dialog>
  )
}

export function PrivacyZonesPanel() {
  const [drawing, setDrawing] = useState(false)
  const [deleting, setDeleting] = useState<PrivacyZone | null>(null)
  const { data: zones = [], isLoading, error } = useQuery({ queryKey: ['privacy-zones'], queryFn: listPrivacyZones })

  return (
    <>
      <Box sx={{ p: 2, display: 'flex', flexDirection: 'column', gap: 1.5 }}>
        <Alert severity="info" variant="outlined">
          {WHAT_A_ZONE_DOES}
          <Box component="ul" sx={{ m: 0, mt: 1, pl: 2.5 }}>
            {ZONE_LIMITS.map((limit) => <li key={limit}>{limit}</li>)}
          </Box>
        </Alert>
        <Box sx={{ display: 'flex', justifyContent: 'flex-end' }}>
          <Button variant="contained" startIcon={<AddIcon />} onClick={() => setDrawing(true)}>
            Draw a privacy zone
          </Button>
        </Box>
        {error && <Alert severity="error">{apiError(error)}</Alert>}
      </Box>
      <TableContainer component={Paper} elevation={0} sx={{ background: 'transparent' }}>
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell>Camera</TableCell>
              <TableCell>What it covers</TableCell>
              <TableCell>Drawn by</TableCell>
              <TableCell>Drawn</TableCell>
              <TableCell align="right" />
            </TableRow>
          </TableHead>
          <TableBody>
            {isLoading ? <SkeletonRows cols={5} /> : zones.length === 0 ? (
              <TableRow>
                <TableCell colSpan={5}>
                  <Typography variant="body2" color="text.secondary" sx={{ py: 2, textAlign: 'center' }}>
                    No privacy zone is drawn. Every camera is shown, analysed and recorded whole.
                  </Typography>
                </TableCell>
              </TableRow>
            ) : zones.map((zone) => (
              <TableRow key={zone.id} hover>
                <TableCell>{zone.camera_name ?? '—'}</TableCell>
                <TableCell>
                  {zone.name}
                  {!zone.is_active && (
                    <Typography component="span" variant="caption" color="text.secondary"> · switched off, masks nothing</Typography>)}
                </TableCell>
                {/* A name and a moment are each read whole: these two do not wrap, the camera's name may. */}
                <TableCell sx={{ whiteSpace: 'nowrap' }}>{zone.created_by_name ?? '—'}</TableCell>
                <TableCell sx={{ whiteSpace: 'nowrap' }}>{when(zone.created_at)}</TableCell>
                <TableCell align="right">
                  <Tooltip title="Delete this zone">
                    <IconButton size="small" aria-label={`Delete the zone ${zone.name}`} onClick={() => setDeleting(zone)}>
                      <DeleteIcon fontSize="small" />
                    </IconButton>
                  </Tooltip>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </TableContainer>
      {drawing && <DrawDialog onClose={() => setDrawing(false)} />}
      {deleting && <DeleteDialog zone={deleting} onClose={() => setDeleting(null)} />}
    </>
  )
}
