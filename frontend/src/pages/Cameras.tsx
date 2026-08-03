import { useState, useMemo } from 'react'
import {
  Box, Grid, Typography, Chip, Stack, Skeleton, IconButton, Tooltip,
  Dialog, DialogTitle, DialogContent, DialogActions, Button, TextField,
  FormGroup, FormControlLabel, Checkbox, Divider, List, ListItem,
  ListItemText, ListItemSecondaryAction, Collapse, Alert, MenuItem, Select,
  InputLabel, FormControl, CircularProgress,
} from '@mui/material'
import VideocamIcon from '@mui/icons-material/Videocam'
import VideocamOffIcon from '@mui/icons-material/VideocamOff'
import AddIcon from '@mui/icons-material/Add'
import EditIcon from '@mui/icons-material/Edit'
import DeleteIcon from '@mui/icons-material/Delete'
import ExpandMoreIcon from '@mui/icons-material/ExpandMore'
import ExpandLessIcon from '@mui/icons-material/ExpandLess'
import LinkIcon from '@mui/icons-material/Link'
import PlayCircleOutlineIcon from '@mui/icons-material/PlayCircleOutlined'
import CheckCircleIcon from '@mui/icons-material/CheckCircle'
import ErrorIcon from '@mui/icons-material/Error'
import FiberManualRecordIcon from '@mui/icons-material/FiberManualRecord'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { GlassCard } from '@/components/common/GlassCard'
import { PermissionGuard } from '@/components/common/PermissionGuard'
import {
  getCameras, createCamera, updateCamera, deleteCamera,
  getStreams, createStream, deleteStream, updateStream, validateStream,
} from '@/api/cameras'
import { getSites } from '@/api/sites'
import { apiClient } from '@/api/client'
import { useAuthStore } from '@/store/auth'
import type { Camera, Stream, StreamValidationResult } from '@/types/api'

const ALL_MODULES = ['lpr', 'face', 'intrusion', 'ppe', 'crowd', 'fire_smoke', 'weapon', 'behavior']

const STATUS_COLORS: Record<string, 'success' | 'warning' | 'error' | 'default'> = {
  online: 'success', degraded: 'warning', offline: 'error',
}

// ──────────────────────────────────────────────────────────
// Camera create/edit dialog
// ──────────────────────────────────────────────────────────

interface CameraDialogProps {
  open: boolean
  onClose: () => void
  existing?: Camera
}

function CameraDialog({ open, onClose, existing }: CameraDialogProps) {
  const qc = useQueryClient()
  const [name, setName] = useState(existing?.name ?? '')
  const [location, setLocation] = useState(existing?.location ?? '')
  const [modules, setModules] = useState<string[]>(existing?.ai_modules_enabled ?? [])
  const [siteId, setSiteId] = useState<string>((existing as any)?.site_id ?? '')

  const { data: sites = [] } = useQuery({ queryKey: ['sites'], queryFn: () => getSites() })
  const isEdit = !!existing

  const mutation = useMutation({
    mutationFn: () =>
      isEdit
        ? updateCamera(existing!.id, { name, location: location || undefined, ai_modules_enabled: modules, site_id: siteId || undefined })
        : createCamera({ name, location: location || undefined, ai_modules_enabled: modules, site_id: siteId || undefined }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['cameras'] })
      onClose()
    },
  })

  const toggleModule = (mod: string) =>
    setModules((prev) => prev.includes(mod) ? prev.filter((m) => m !== mod) : [...prev, mod])

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>{isEdit ? 'Edit Camera' : 'Add Camera'}</DialogTitle>
      <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: '16px !important' }}>
        <TextField label="Name" value={name} onChange={(e) => setName(e.target.value)} fullWidth required />
        <TextField label="Location" value={location} onChange={(e) => setLocation(e.target.value)} fullWidth />
        <FormControl fullWidth size="small">
          <InputLabel>Site (optional)</InputLabel>
          <Select value={siteId} onChange={(e) => setSiteId(e.target.value)} label="Site (optional)">
            <MenuItem value="">No site</MenuItem>
            {sites.map((s: any) => <MenuItem key={s.id} value={s.id}>{s.name}</MenuItem>)}
          </Select>
        </FormControl>
        <Box>
          <Typography variant="caption" color="text.secondary" sx={{ mb: 0.5, display: 'block' }}>
            AI Modules
          </Typography>
          <FormGroup row>
            {ALL_MODULES.map((mod) => (
              <FormControlLabel
                key={mod}
                control={<Checkbox size="small" checked={modules.includes(mod)} onChange={() => toggleModule(mod)} />}
                label={<Typography variant="caption">{mod.replace('_', ' ')}</Typography>}
                sx={{ mr: 1 }}
              />
            ))}
          </FormGroup>
        </Box>
        {mutation.error && <Typography color="error" variant="caption">{String(mutation.error)}</Typography>}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" onClick={() => mutation.mutate()} disabled={!name || mutation.isPending}>
          {isEdit ? 'Save' : 'Add'}
        </Button>
      </DialogActions>
    </Dialog>
  )
}

// ──────────────────────────────────────────────────────────
// Stream add dialog — supports Static IP, DDNS, and Manual URL
// ──────────────────────────────────────────────────────────

type ConnectionMethod = 'manual' | 'static_ip' | 'ddns'

function StreamDialog({ open, onClose, cameraId }: { open: boolean; onClose: () => void; cameraId: string }) {
  const qc = useQueryClient()

  const [method, setMethod]         = useState<ConnectionMethod>('manual')
  const [url, setUrl]               = useState('')
  const [protocol, setProtocol]     = useState('rtsp')
  const [ipAddr, setIpAddr]         = useState('')
  const [hostname, setHostname]     = useState('')
  const [port, setPort]             = useState('554')
  const [streamPath, setStreamPath] = useState('/stream1')
  const [username, setUsername]     = useState('')
  const [password, setPassword]     = useState('')
  const [testResult, setTestResult] = useState<StreamValidationResult | null>(null)
  const [testing, setTesting]       = useState(false)

  const computedUrl = useMemo(() => {
    if (method === 'static_ip') return `rtsp://${ipAddr}:${port}${streamPath || '/'}`
    if (method === 'ddns')      return `rtsp://${hostname}:${port}${streamPath || '/'}`
    return url
  }, [method, ipAddr, hostname, port, streamPath, url])

  const isValid = method === 'manual' ? !!url : method === 'static_ip' ? !!ipAddr : !!hostname
  const submitProtocol = method === 'manual' ? protocol : 'rtsp'

  const resetForm = () => {
    setMethod('manual'); setUrl(''); setProtocol('rtsp')
    setIpAddr(''); setHostname(''); setPort('554'); setStreamPath('/stream1')
    setUsername(''); setPassword(''); setTestResult(null)
  }

  const handleTest = async () => {
    if (!computedUrl) return
    setTesting(true)
    setTestResult(null)
    try {
      const res = await validateStream({ url: computedUrl, username: username || undefined, password: password || undefined })
      setTestResult(res)
    } catch {
      setTestResult({ valid: false, error: 'Request failed', resolution_w: null, resolution_h: null, fps: null, latency_ms: null })
    } finally {
      setTesting(false)
    }
  }

  const mutation = useMutation({
    mutationFn: () => createStream(cameraId, {
      url: computedUrl,
      protocol: submitProtocol,
      username: username || undefined,
      password: password || undefined,
    }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['streams', cameraId] })
      resetForm()
      onClose()
    },
  })

  const handleClose = () => { resetForm(); onClose() }

  return (
    <Dialog open={open} onClose={handleClose} maxWidth="sm" fullWidth>
      <DialogTitle>Add Stream</DialogTitle>
      <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: '16px !important' }}>

        {/* Connection method selector */}
        <TextField
          select
          label="Connection Method"
          value={method}
          onChange={(e) => setMethod(e.target.value as ConnectionMethod)}
          fullWidth
          slotProps={{ select: { native: true } }}
        >
          <option value="manual">Manual URL</option>
          <option value="static_ip">RTSP — Static IP</option>
          <option value="ddns">RTSP — DDNS Hostname</option>
        </TextField>

        {/* Manual URL mode */}
        {method === 'manual' && (
          <>
            <TextField
              label="Stream URL"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="rtsp://192.168.1.100:554/stream1"
              fullWidth
            />
            <TextField
              select
              label="Protocol"
              value={protocol}
              onChange={(e) => setProtocol(e.target.value)}
              fullWidth
              slotProps={{ select: { native: true } }}
            >
              {['rtsp', 'rtmp', 'onvif', 'http'].map((p) => (
                <option key={p} value={p}>{p.toUpperCase()}</option>
              ))}
            </TextField>
          </>
        )}

        {/* Static IP mode */}
        {method === 'static_ip' && (
          <>
            <TextField
              label="IP Address"
              value={ipAddr}
              onChange={(e) => setIpAddr(e.target.value)}
              placeholder="192.168.1.100"
              fullWidth
            />
            <Box sx={{ display: 'flex', gap: 1.5 }}>
              <TextField
                label="Port"
                value={port}
                onChange={(e) => setPort(e.target.value)}
                sx={{ width: 110 }}
              />
              <TextField
                label="Stream Path"
                value={streamPath}
                onChange={(e) => setStreamPath(e.target.value)}
                placeholder="/stream1"
                fullWidth
              />
            </Box>
          </>
        )}

        {/* DDNS mode */}
        {method === 'ddns' && (
          <>
            <TextField
              label="DDNS Hostname"
              value={hostname}
              onChange={(e) => setHostname(e.target.value)}
              placeholder="myhome.ddns.net"
              fullWidth
            />
            <Box sx={{ display: 'flex', gap: 1.5 }}>
              <TextField
                label="Port"
                value={port}
                onChange={(e) => setPort(e.target.value)}
                sx={{ width: 110 }}
              />
              <TextField
                label="Stream Path"
                value={streamPath}
                onChange={(e) => setStreamPath(e.target.value)}
                placeholder="/stream1"
                fullWidth
              />
            </Box>
          </>
        )}

        {/* Constructed URL preview */}
        {method !== 'manual' && (ipAddr || hostname) && (
          <Box
            sx={{
              p: 1.5,
              borderRadius: 1,
              bgcolor: 'action.hover',
              border: '1px solid',
              borderColor: 'divider',
            }}
          >
            <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 0.5 }}>
              RTSP URL Preview
            </Typography>
            <Typography
              variant="caption"
              sx={{ fontFamily: 'monospace', wordBreak: 'break-all', color: 'primary.main' }}
            >
              {computedUrl}
            </Typography>
          </Box>
        )}

        {/* Credentials */}
        <Box sx={{ display: 'flex', gap: 1.5 }}>
          <TextField
            label="Username (optional)"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            size="small"
            fullWidth
          />
          <TextField
            label="Password (optional)"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            type="password"
            size="small"
            fullWidth
          />
        </Box>

        {/* Test connection */}
        <Box sx={{ display: 'flex', gap: 1, alignItems: 'center' }}>
          <Button
            size="small"
            variant="outlined"
            onClick={handleTest}
            disabled={!isValid || testing}
            startIcon={testing ? <CircularProgress size={14} /> : undefined}
          >
            {testing ? 'Testing…' : 'Test Connection'}
          </Button>
          {testResult && (
            testResult.valid ? (
              <Chip
                icon={<CheckCircleIcon />}
                label={`${testResult.resolution_w}×${testResult.resolution_h} @ ${testResult.fps} fps — ${testResult.latency_ms}ms`}
                size="small"
                color="success"
              />
            ) : (
              <Chip
                icon={<ErrorIcon />}
                label={testResult.error ?? 'Failed'}
                size="small"
                color="error"
              />
            )
          )}
        </Box>

        {mutation.error && (
          <Typography color="error" variant="caption">{String(mutation.error)}</Typography>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={handleClose}>Cancel</Button>
        <Button
          variant="contained"
          onClick={() => mutation.mutate()}
          disabled={!isValid || mutation.isPending}
        >
          Add
        </Button>
      </DialogActions>
    </Dialog>
  )
}

// ──────────────────────────────────────────────────────────
// Live MJPEG view dialog
// Browsers render multipart/x-mixed-replace directly via <img> — no JS player.
// ──────────────────────────────────────────────────────────

interface LiveViewDialogProps {
  open: boolean
  onClose: () => void
  cameraId: string
  streamId: string
  streamUrl: string
}

function LiveViewDialog({ open, onClose, cameraId, streamId, streamUrl }: LiveViewDialogProps) {
  const accessToken = useAuthStore((s) => s.accessToken)
  const [imgError, setImgError] = useState(false)

  const liveUrl = `${apiClient.defaults.baseURL}/api/v1/cameras/${cameraId}/streams/${streamId}/live?token=${accessToken ?? ''}`

  const handleClose = () => {
    setImgError(false)
    onClose()
  }

  return (
    <Dialog open={open} onClose={handleClose} maxWidth="md" fullWidth>
      <DialogTitle sx={{ pb: 0.5 }}>
        Live View
        <Typography variant="caption" color="text.secondary" sx={{ ml: 1, fontFamily: 'monospace' }}>
          {streamUrl}
        </Typography>
      </DialogTitle>
      <DialogContent>
        <Box
          sx={{
            position: 'relative',
            width: '100%',
            bgcolor: '#000',
            borderRadius: 1,
            minHeight: 300,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            overflow: 'hidden',
          }}
        >
          {imgError ? (
            <Alert severity="error" sx={{ m: 2 }}>
              Stream unavailable — verify the camera is online and the RTSP URL is reachable from the server.
            </Alert>
          ) : (
            /* key resets the img on each dialog open, dropping any stale connection */
            <img
              key={open ? liveUrl : 'closed'}
              src={open ? liveUrl : undefined}
              style={{ width: '100%', display: 'block', borderRadius: 4 }}
              onError={() => setImgError(true)}
              alt="Live camera feed"
            />
          )}
        </Box>
        <Typography variant="caption" color="text.secondary" sx={{ mt: 1, display: 'block' }}>
          Frames are proxied via MJPEG. Actual frame rate depends on camera and network latency.
        </Typography>
      </DialogContent>
      <DialogActions>
        <Button onClick={handleClose}>Close</Button>
      </DialogActions>
    </Dialog>
  )
}

// ──────────────────────────────────────────────────────────
// Per-camera streams panel
// ──────────────────────────────────────────────────────────

function CameraStreams({ cameraId }: { cameraId: string }) {
  const qc = useQueryClient()
  const [addOpen, setAddOpen] = useState(false)
  const [liveStream, setLiveStream] = useState<Stream | null>(null)

  const { data: streams = [], isLoading } = useQuery({
    queryKey: ['streams', cameraId],
    queryFn: () => getStreams(cameraId),
  })
  const removeMutation = useMutation({
    mutationFn: (streamId: string) => deleteStream(cameraId, streamId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['streams', cameraId] }),
  })
  const toggleRecMutation = useMutation({
    mutationFn: ({ streamId, enabled }: { streamId: string; enabled: boolean }) =>
      updateStream(cameraId, streamId, { continuous_recording: enabled }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['streams', cameraId] }),
  })

  return (
    <Box>
      <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 0.5 }}>
        <Typography variant="caption" color="text.secondary" sx={{ textTransform: 'uppercase', letterSpacing: 1 }}>
          Streams
        </Typography>
        <PermissionGuard permission="camera:create">
          <Tooltip title="Add stream">
            <IconButton size="small" onClick={() => setAddOpen(true)}>
              <AddIcon fontSize="small" />
            </IconButton>
          </Tooltip>
        </PermissionGuard>
      </Box>
      {isLoading ? (
        <Skeleton height={32} />
      ) : streams.length === 0 ? (
        <Typography variant="caption" color="text.disabled">No streams configured</Typography>
      ) : (
        <List dense disablePadding>
          {streams.map((s: Stream) => (
            <ListItem key={s.id} disablePadding sx={{ py: 0.25 }}>
              <LinkIcon sx={{ fontSize: 14, mr: 0.75, color: 'text.secondary', flexShrink: 0 }} />
              <ListItemText
                primary={
                  <Typography variant="caption" sx={{ fontFamily: 'monospace', wordBreak: 'break-all' }}>
                    {s.url}
                  </Typography>
                }
                secondary={
                  <Box sx={{ display: 'flex', gap: 0.5, mt: 0.25 }}>
                    <Chip label={s.protocol.toUpperCase()} size="small" variant="outlined" sx={{ height: 16, fontSize: '0.6rem' }} />
                    <Chip
                      label={s.status}
                      size="small"
                      color={STATUS_COLORS[s.status] ?? 'default'}
                      sx={{ height: 16, fontSize: '0.6rem' }}
                    />
                  </Box>
                }
                slotProps={{ secondary: { component: 'div' } }}
              />
              <ListItemSecondaryAction sx={{ display: 'flex', gap: 0.25, alignItems: 'center' }}>
                <PermissionGuard permission="camera:update">
                  <Tooltip title={s.continuous_recording
                    ? '24/7 recording ON — segments saved continuously'
                    : 'Enable 24/7 continuous recording'}>
                    <IconButton
                      size="small"
                      color={s.continuous_recording ? 'error' : 'default'}
                      disabled={toggleRecMutation.isPending}
                      onClick={() => toggleRecMutation.mutate({
                        streamId: s.id, enabled: !s.continuous_recording,
                      })}
                    >
                      <FiberManualRecordIcon sx={{ fontSize: 14 }} />
                    </IconButton>
                  </Tooltip>
                </PermissionGuard>
                <Tooltip title="Live view">
                  <IconButton size="small" color="primary" onClick={() => setLiveStream(s)}>
                    <PlayCircleOutlineIcon sx={{ fontSize: 14 }} />
                  </IconButton>
                </Tooltip>
                <PermissionGuard permission="camera:delete">
                  <IconButton size="small" color="error" onClick={() => removeMutation.mutate(s.id)} edge="end">
                    <DeleteIcon sx={{ fontSize: 14 }} />
                  </IconButton>
                </PermissionGuard>
              </ListItemSecondaryAction>
            </ListItem>
          ))}
        </List>
      )}
      <StreamDialog open={addOpen} onClose={() => setAddOpen(false)} cameraId={cameraId} />
      {liveStream && (
        <LiveViewDialog
          open={!!liveStream}
          onClose={() => setLiveStream(null)}
          cameraId={cameraId}
          streamId={liveStream.id}
          streamUrl={liveStream.url}
        />
      )}
    </Box>
  )
}

// ──────────────────────────────────────────────────────────
// Camera card
// ──────────────────────────────────────────────────────────

function CameraCard({ camera }: { camera: Camera }) {
  const qc = useQueryClient()
  const [editOpen, setEditOpen] = useState(false)
  const [expanded, setExpanded] = useState(false)

  const deactivate = useMutation({
    mutationFn: () => deleteCamera(camera.id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['cameras'] }),
  })

  return (
    <GlassCard sx={{ p: 3 }}>
      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', mb: 1.5 }}>
        <Box sx={{ color: camera.is_active ? 'primary.main' : 'text.disabled', display: 'flex', gap: 0.5 }}>
          {camera.is_active ? <VideocamIcon sx={{ fontSize: 28 }} /> : <VideocamOffIcon sx={{ fontSize: 28 }} />}
        </Box>
        <Box sx={{ display: 'flex', gap: 0.5 }}>
          <Chip
            label={camera.is_active ? 'Active' : 'Inactive'}
            size="small"
            color={camera.is_active ? 'success' : 'default'}
            variant="outlined"
          />
          <PermissionGuard permission="camera:update">
            <Tooltip title="Edit camera">
              <IconButton size="small" onClick={() => setEditOpen(true)}>
                <EditIcon fontSize="small" />
              </IconButton>
            </Tooltip>
          </PermissionGuard>
          <PermissionGuard permission="camera:delete">
            <Tooltip title="Deactivate camera">
              <IconButton size="small" color="error" onClick={() => deactivate.mutate()} disabled={!camera.is_active}>
                <DeleteIcon fontSize="small" />
              </IconButton>
            </Tooltip>
          </PermissionGuard>
        </Box>
      </Box>

      <Typography variant="subtitle1" sx={{ fontWeight: 700 }} noWrap>{camera.name}</Typography>
      {(camera as any).site_name && (
        <Chip label={(camera as any).site_name} size="small" variant="outlined" color="primary" sx={{ mb: 0.5, height: 18, fontSize: '0.65rem' }} />
      )}
      {camera.location && (
        <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }} noWrap>
          {camera.location}
        </Typography>
      )}

      <Stack direction="row" spacing={0.5} sx={{ mt: 1.5, mb: 1.5, flexWrap: 'wrap', gap: 0.5 }}>
        {camera.ai_modules_enabled.map((mod) => (
          <Chip key={mod} label={mod.replace('_', ' ')} size="small" variant="outlined" color="secondary" />
        ))}
        {camera.ai_modules_enabled.length === 0 && (
          <Typography variant="caption" color="text.disabled">No AI modules</Typography>
        )}
      </Stack>

      <Divider sx={{ borderColor: 'rgba(255,255,255,0.08)', mb: 1 }} />

      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <Typography variant="caption" color="text.secondary">Streams & Config</Typography>
        <IconButton size="small" onClick={() => setExpanded((e) => !e)}>
          {expanded ? <ExpandLessIcon fontSize="small" /> : <ExpandMoreIcon fontSize="small" />}
        </IconButton>
      </Box>

      <Collapse in={expanded}>
        <Box sx={{ mt: 1 }}>
          <CameraStreams cameraId={camera.id} />
        </Box>
      </Collapse>

      {editOpen && <CameraDialog open={editOpen} onClose={() => setEditOpen(false)} existing={camera} />}
    </GlassCard>
  )
}

// ──────────────────────────────────────────────────────────
// Page
// ──────────────────────────────────────────────────────────

export default function Cameras() {
  const [addOpen, setAddOpen] = useState(false)
  const [siteFilter, setSiteFilter] = useState('')
  const { data: cameras, isLoading } = useQuery({
    queryKey: ['cameras'],
    queryFn: getCameras,
    refetchInterval: 30_000,
  })
  const { data: sites = [] } = useQuery({ queryKey: ['sites'], queryFn: () => getSites() })

  const filtered = siteFilter
    ? (cameras ?? []).filter((c: any) => c.site_id === siteFilter)
    : cameras

  return (
    <Box>
      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 3, gap: 2 }}>
        <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap' }}>
          <Chip
            label="All Sites"
            onClick={() => setSiteFilter('')}
            color={siteFilter === '' ? 'primary' : 'default'}
            variant={siteFilter === '' ? 'filled' : 'outlined'}
          />
          {(sites as any[]).map((s) => (
            <Chip
              key={s.id}
              label={s.name}
              onClick={() => setSiteFilter(s.id)}
              color={siteFilter === s.id ? 'primary' : 'default'}
              variant={siteFilter === s.id ? 'filled' : 'outlined'}
            />
          ))}
        </Box>
        <PermissionGuard permission="camera:create">
          <Button startIcon={<AddIcon />} variant="contained" size="small" onClick={() => setAddOpen(true)} sx={{ whiteSpace: 'nowrap' }}>
            Add Camera
          </Button>
        </PermissionGuard>
      </Box>

      <Grid container spacing={3}>
        {isLoading
          ? Array.from({ length: 6 }).map((_, i) => (
              <Grid size={{ xs: 12, sm: 6, md: 4 }} key={i}>
                <GlassCard sx={{ p: 3 }}><Skeleton variant="rectangular" height={160} /></GlassCard>
              </Grid>
            ))
          : (filtered ?? []).length === 0
          ? (
              <Grid size={{ xs: 12 }}>
                <Typography color="text.secondary" align="center" sx={{ mt: 8 }}>
                  No cameras configured
                </Typography>
              </Grid>
            )
          : (filtered ?? []).map((camera) => (
              <Grid size={{ xs: 12, sm: 6, md: 4 }} key={camera.id}>
                <CameraCard camera={camera} />
              </Grid>
            ))}
      </Grid>

      {addOpen && <CameraDialog open={addOpen} onClose={() => setAddOpen(false)} />}
    </Box>
  )
}
