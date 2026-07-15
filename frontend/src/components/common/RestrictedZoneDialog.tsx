import { useState } from 'react'
import {
  Box, Button, Checkbox, Dialog, DialogActions, DialogContent, DialogTitle,
  FormControl, FormControlLabel, FormGroup, InputLabel, MenuItem, Select,
  TextField, Typography,
} from '@mui/material'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { ZonePolygonEditor } from './ZonePolygonEditor'
import { createZone } from '@/api/zones'
import { getCameras } from '@/api/cameras'

const SEVERITY_HEX: Record<string, string> = {
  critical: '#FF4560', high: '#FF7F50', medium: '#FFA500', low: '#00E396',
}
const ZONE_MODULES = ['intrusion', 'behavior'] as const

interface RestrictedZoneDialogProps {
  open: boolean
  onClose: () => void
  /** Pre-select and lock the camera — used when opening this dialog from a
   * context that already knows which camera (e.g. a Live Wall cell), so the
   * operator doesn't have to re-find it in the dropdown. */
  initialCameraId?: string
  /** Display name for initialCameraId, shown in the locked field. */
  initialCameraName?: string
}

export function RestrictedZoneDialog({ open, onClose, initialCameraId, initialCameraName }: RestrictedZoneDialogProps) {
  const qc = useQueryClient()
  const [cameraId, setCameraId] = useState(initialCameraId ?? '')
  const [name, setName] = useState('')
  const [severity, setSeverity] = useState('medium')
  const [polygon, setPolygon] = useState<Array<{ x: number; y: number }>>([])
  const [appliesTo, setAppliesTo] = useState<string[]>(['intrusion'])

  const { data: cameras = [] } = useQuery({ queryKey: ['cameras'], queryFn: getCameras, enabled: open && !initialCameraId })

  const mutation = useMutation({
    mutationFn: () => createZone({ camera_id: cameraId, name, polygon, severity, applies_to_modules: appliesTo }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['zones'] })
      qc.invalidateQueries({ queryKey: ['camera-overlay', cameraId] })
      setCameraId(initialCameraId ?? ''); setName(''); setSeverity('medium'); setPolygon([]); setAppliesTo(['intrusion'])
      onClose()
    },
  })

  const toggleModule = (m: string) => {
    setAppliesTo((prev) => prev.includes(m) ? prev.filter((x) => x !== m) : [...prev, m])
  }

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>Add Restricted Zone</DialogTitle>
      <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: '16px !important' }}>
        {initialCameraId ? (
          <TextField label="Camera" value={initialCameraName ?? initialCameraId} fullWidth disabled />
        ) : (
          <FormControl fullWidth>
            <InputLabel>Camera</InputLabel>
            <Select value={cameraId} label="Camera" onChange={(e) => { setCameraId(e.target.value); setPolygon([]) }}>
              {cameras.map((c: any) => (
                <MenuItem key={c.id} value={c.id}>{c.name}{c.site_name ? ` — ${c.site_name}` : ''}</MenuItem>
              ))}
            </Select>
          </FormControl>
        )}
        <TextField label="Zone Name" value={name} onChange={(e) => setName(e.target.value)} fullWidth />
        <FormControl fullWidth>
          <InputLabel>Alert Severity</InputLabel>
          <Select value={severity} label="Alert Severity" onChange={(e) => setSeverity(e.target.value)}>
            {['low', 'medium', 'high', 'critical'].map((s) => <MenuItem key={s} value={s}>{s}</MenuItem>)}
          </Select>
        </FormControl>
        <Box>
          <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 0.5 }}>
            Applies to
          </Typography>
          <FormGroup row>
            {ZONE_MODULES.map((m) => (
              <FormControlLabel
                key={m}
                control={<Checkbox size="small" checked={appliesTo.includes(m)} onChange={() => toggleModule(m)} />}
                label={<Typography variant="body2" sx={{ textTransform: 'capitalize' }}>{m}</Typography>}
              />
            ))}
          </FormGroup>
        </Box>
        {cameraId ? (
          <ZonePolygonEditor cameraId={cameraId} value={polygon} onChange={setPolygon} severityColor={SEVERITY_HEX[severity]} />
        ) : (
          <Typography variant="caption" color="text.disabled">Select a camera to draw the zone on its live feed.</Typography>
        )}
        {mutation.error && <Typography color="error" variant="caption">{String(mutation.error)}</Typography>}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button
          variant="contained"
          onClick={() => mutation.mutate()}
          disabled={!cameraId || !name || polygon.length < 3 || appliesTo.length === 0 || mutation.isPending}
        >
          Add
        </Button>
      </DialogActions>
    </Dialog>
  )
}
