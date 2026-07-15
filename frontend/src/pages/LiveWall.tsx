import { useState, useEffect, useCallback, useRef } from 'react'
import { useSearchParams } from 'react-router-dom'
import {
  Box, Button, ButtonGroup, Checkbox, Chip, Dialog, DialogActions,
  DialogContent, DialogTitle, FormControlLabel, Grid, IconButton, List,
  ListItemButton, ListItemText, MenuItem, Select, Switch, TextField,
  Tooltip, Typography,
} from '@mui/material'
import AddIcon from '@mui/icons-material/Add'
import CloseIcon from '@mui/icons-material/Close'
import DeleteIcon from '@mui/icons-material/Delete'
import FiberManualRecordIcon from '@mui/icons-material/FiberManualRecord'
import HighlightAltIcon from '@mui/icons-material/HighlightAlt'
import OpenInNewIcon from '@mui/icons-material/OpenInNew'
import SaveIcon from '@mui/icons-material/Save'
import StopIcon from '@mui/icons-material/Stop'
import FullscreenIcon from '@mui/icons-material/Fullscreen'
import FullscreenExitIcon from '@mui/icons-material/FullscreenExit'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useAuthStore } from '@/store/auth'
import { useFocusModeStore } from '@/store/focusMode'
import { useWebSocket } from '@/hooks/useWebSocket'
import { apiClient } from '@/api/client'
import { getSites } from '@/api/sites'
import { startRecording, stopRecording } from '@/api/recordings'
import { getMyEnabledModules, ALL_AI_MODULES, MODULE_LABELS, type AiModuleType } from '@/api/licenses'
import {
  createWallLayout, deleteWallLayout, listWallLayouts, updateWallLayout,
  type WallLayout,
} from '@/api/wallLayouts'
import { GlassCard } from '@/components/common/GlassCard'
import { HlsPlayer } from '@/components/common/HlsPlayer'
import { DetectionOverlay } from '@/components/common/DetectionOverlay'
import { RestrictedZoneDialog } from '@/components/common/RestrictedZoneDialog'
import { AlertResponseDialog, type AlertSummary } from '@/components/common/AlertResponseDialog'
import { openLiveWallWindow } from '@/lib/liveWallWindow'

type GridSize = 1 | 4 | 9 | 16

interface WallCell {
  camera_id: string
  stream_id: string
  camera_name: string
  site_name?: string
}

const WALL_KEY = 'seventh_ai_live_wall'
const AUTOPOP_KEY = 'seventh_ai_live_wall_autopop'
const MODE_KEY = 'seventh_ai_live_mode'
const OVERLAY_MODULES_KEY = 'seventh_ai_live_overlay_modules'
const FLASH_MS = 30_000

/** A wall-strip/cell alert needs the camera id (to locate/flash the cell)
 * on top of the fields AlertResponseDialog needs to display + act on it. */
type WallAlert = AlertSummary & { camera_id: string }
const GRID_CONFIGS: { label: string; value: GridSize; cols: number }[] = [
  { label: '1×1', value: 1, cols: 1 },
  { label: '2×2', value: 4, cols: 2 },
  { label: '3×3', value: 9, cols: 3 },
  { label: '4×4', value: 16, cols: 4 },
]

function loadWall(): WallCell[] {
  try {
    return JSON.parse(localStorage.getItem(WALL_KEY) ?? '[]')
  } catch {
    return []
  }
}

function saveWall(cells: WallCell[]) {
  localStorage.setItem(WALL_KEY, JSON.stringify(cells))
}

interface CameraPickerProps {
  open: boolean
  onClose: () => void
  onAdd: (cell: WallCell) => void
  existing: WallCell[]
}

function CameraPicker({ open, onClose, onAdd, existing }: CameraPickerProps) {
  const [siteFilter, setSiteFilter] = useState('')
  const token = useAuthStore((s) => s.accessToken)
  const { data: sites = [] } = useQuery({ queryKey: ['sites'], queryFn: () => getSites() })
  const { data: cameras = [] } = useQuery({
    queryKey: ['cameras'],
    queryFn: () => apiClient.get('/api/v1/cameras').then((r) => r.data),
    enabled: open,
  })

  const existingSet = new Set(existing.map((c) => `${c.camera_id}:${c.stream_id}`))

  const filteredCameras = siteFilter
    ? cameras.filter((c: any) => c.site_id === siteFilter)
    : cameras

  const handleCameraSelect = async (camera: any) => {
    // Get first stream for camera
    const streams = await apiClient.get(`/api/v1/cameras/${camera.id}/streams`).then((r) => r.data)
    if (!streams || streams.length === 0) return
    const stream = streams[0]
    const key = `${camera.id}:${stream.id}`
    if (existingSet.has(key)) return
    const site = sites.find((s: any) => s.id === camera.site_id)
    onAdd({ camera_id: camera.id, stream_id: stream.id, camera_name: camera.name, site_name: site?.name })
    onClose()
  }

  if (!token) return null

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>Add Camera to Wall</DialogTitle>
      <DialogContent>
        <Select
          size="small"
          value={siteFilter}
          onChange={(e) => setSiteFilter(e.target.value)}
          displayEmpty
          fullWidth
          sx={{ mb: 1 }}
        >
          <MenuItem value="">All Sites</MenuItem>
          {sites.map((s: any) => (
            <MenuItem key={s.id} value={s.id}>{s.name}</MenuItem>
          ))}
        </Select>
        <List dense>
          {filteredCameras.map((c: any) => {
            return (
              <ListItemButton key={c.id} onClick={() => handleCameraSelect(c)}>
                <ListItemText primary={c.name} secondary={c.site_name ?? c.location ?? 'No site'} />
              </ListItemButton>
            )
          })}
          {filteredCameras.length === 0 && (
            <Typography color="text.secondary" p={2}>No cameras available</Typography>
          )}
        </List>
      </DialogContent>
    </Dialog>
  )
}

type StreamMode = 'mjpeg' | 'hls'

interface LiveCellProps {
  cell: WallCell
  onRemove: () => void
  alert?: WallAlert | null
  mode: StreamMode
  activeModules: string[]
  onDrawZone: () => void
  onOpenAlert: () => void
}

function LiveCell({ cell, onRemove, alert, mode, activeModules, onDrawZone, onOpenAlert }: LiveCellProps) {
  const alertTitle = alert?.title ?? null
  const token = useAuthStore((s) => s.accessToken)
  const qc = useQueryClient()
  const [recordingId, setRecordingId] = useState<string | null>(null)
  const [elapsed, setElapsed] = useState(0)
  const [fullscreen, setFullscreen] = useState(false)

  useEffect(() => {
    if (!recordingId) { setElapsed(0); return }
    const t = setInterval(() => setElapsed((n) => n + 1), 1000)
    return () => clearInterval(t)
  }, [recordingId])

  const startMutation = useMutation({
    mutationFn: () => startRecording(cell.camera_id, cell.stream_id),
    onSuccess: (data) => {
      setRecordingId(data.recording_id)
      setElapsed(0)
    },
  })

  const stopMutation = useMutation({
    mutationFn: () => stopRecording(cell.camera_id, cell.stream_id, recordingId!),
    onSuccess: () => {
      setRecordingId(null)
      qc.invalidateQueries({ queryKey: ['recordings'] })
    },
  })

  const liveUrl = token
    ? `${apiClient.defaults.baseURL}/api/v1/cameras/${cell.camera_id}/streams/${cell.stream_id}/live?token=${token}`
    : null
  const hlsUrl = token
    ? `${apiClient.defaults.baseURL}/api/v1/cameras/${cell.camera_id}/streams/${cell.stream_id}/hls/index.m3u8?token=${token}`
    : null

  const formatElapsed = (s: number) =>
    `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`

  return (
    <>
      <Box
        sx={{
          position: 'relative',
          bgcolor: 'rgba(0,0,0,0.7)',
          borderRadius: 1,
          overflow: 'hidden',
          aspectRatio: '16/9',
          border: alertTitle ? '2px solid #FF4560' : '1px solid rgba(255,255,255,0.08)',
          '& .cell-hover-controls': { opacity: 0, transition: 'opacity 0.15s' },
          '&:hover .cell-hover-controls, &:focus-within .cell-hover-controls': { opacity: 1 },
          ...(alertTitle && {
            animation: 'alert-cell-pulse 1s ease-in-out infinite',
            '@keyframes alert-cell-pulse': {
              '0%, 100%': { boxShadow: '0 0 4px 1px rgba(255,69,96,0.55)' },
              '50%': { boxShadow: '0 0 18px 5px rgba(255,69,96,0.95)' },
            },
          }),
        }}
      >
        {mode === 'hls' && hlsUrl ? (
          <HlsPlayer src={hlsUrl} sx={{ aspectRatio: '16/9' }} />
        ) : liveUrl ? (
          <Box
            component="img"
            src={liveUrl}
            alt={cell.camera_name}
            sx={{ width: '100%', height: '100%', objectFit: 'contain', display: 'block' }}
          />
        ) : null}

        {/* AI annotation layer (Gap 92), filtered to the operator's selected modules */}
        <DetectionOverlay cameraId={cell.camera_id} enabled={activeModules.length > 0} moduleFilter={activeModules} />

        {/* Overlay — top */}
        <Box
          sx={{
            position: 'absolute', top: 0, left: 0, right: 0,
            background: 'linear-gradient(180deg,rgba(0,0,0,0.65) 0%,transparent 100%)',
            px: 1, py: 0.5,
            display: 'flex', alignItems: 'center', gap: 0.5,
          }}
        >
          <Typography variant="caption" color="white" fontWeight={700} noWrap sx={{ flex: 1 }}>
            {cell.camera_name}
          </Typography>
          {alertTitle && (
            <Chip
              label={alertTitle}
              size="small"
              color="error"
              onClick={onOpenAlert}
              sx={{ height: 16, fontSize: '0.6rem', maxWidth: 160, cursor: 'pointer' }}
            />
          )}
          {cell.site_name && (
            <Chip label={cell.site_name} size="small" sx={{ height: 16, fontSize: '0.6rem' }} />
          )}
          {recordingId && (
            <Chip
              icon={<FiberManualRecordIcon sx={{ fontSize: '0.6rem !important', color: 'red !important' }} />}
              label={formatElapsed(elapsed)}
              size="small"
              color="error"
              sx={{ height: 16, fontSize: '0.6rem' }}
            />
          )}
        </Box>

        {/* Overlay — bottom controls (hover-only, see .cell-hover-controls on root) */}
        <Box
          className="cell-hover-controls"
          sx={{
            position: 'absolute', bottom: 0, left: 0, right: 0,
            background: 'linear-gradient(0deg,rgba(0,0,0,0.65) 0%,transparent 100%)',
            px: 1, py: 0.5,
            display: 'flex', alignItems: 'center', justifyContent: 'flex-end', gap: 0.5,
          }}
        >
          {!recordingId ? (
            <Tooltip title="Start Recording">
              <IconButton
                size="small"
                onClick={() => startMutation.mutate()}
                disabled={startMutation.isPending}
                sx={{ color: '#fff', bgcolor: 'rgba(255,69,96,0.3)', '&:hover': { bgcolor: 'rgba(255,69,96,0.6)' } }}
              >
                <FiberManualRecordIcon sx={{ fontSize: 14 }} />
              </IconButton>
            </Tooltip>
          ) : (
            <Tooltip title="Stop Recording">
              <IconButton
                size="small"
                onClick={() => stopMutation.mutate()}
                disabled={stopMutation.isPending}
                sx={{ color: '#fff', bgcolor: 'rgba(255,69,96,0.7)', '&:hover': { bgcolor: 'rgba(255,69,96,0.9)' } }}
              >
                <StopIcon sx={{ fontSize: 14 }} />
              </IconButton>
            </Tooltip>
          )}
          <Tooltip title="Draw restricted zone on this feed">
            <IconButton size="small" onClick={onDrawZone} sx={{ color: '#fff' }}>
              <HighlightAltIcon sx={{ fontSize: 14 }} />
            </IconButton>
          </Tooltip>
          <Tooltip title="Full screen">
            <IconButton size="small" onClick={() => setFullscreen(true)} sx={{ color: '#fff' }}>
              <FullscreenIcon sx={{ fontSize: 14 }} />
            </IconButton>
          </Tooltip>
          <Tooltip title="Remove from wall">
            <IconButton size="small" onClick={onRemove} sx={{ color: '#fff' }}>
              <CloseIcon sx={{ fontSize: 14 }} />
            </IconButton>
          </Tooltip>
        </Box>
      </Box>

      {/* Fullscreen dialog */}
      <Dialog open={fullscreen} onClose={() => setFullscreen(false)} maxWidth="xl" fullWidth>
        <DialogTitle sx={{ py: 1 }}>
          {cell.camera_name}
          <IconButton sx={{ float: 'right' }} onClick={() => setFullscreen(false)} size="small">
            <CloseIcon />
          </IconButton>
        </DialogTitle>
        <DialogContent sx={{ p: 0 }}>
          {mode === 'hls' && hlsUrl ? (
            <HlsPlayer src={hlsUrl} sx={{ maxHeight: '80vh' }} />
          ) : liveUrl ? (
            <Box component="img" src={liveUrl} alt={cell.camera_name} sx={{ width: '100%', display: 'block' }} />
          ) : null}
        </DialogContent>
      </Dialog>
    </>
  )
}

export function LiveWallPage() {
  const [cells, setCells] = useState<WallCell[]>(loadWall)
  const [gridSize, setGridSize] = useState<GridSize>(4)
  const [pickerOpen, setPickerOpen] = useState(false)
  const [autoPop, setAutoPop] = useState(() => localStorage.getItem(AUTOPOP_KEY) !== 'false')
  const [kiosk, setKiosk] = useState(false)
  const setFocusMode = useFocusModeStore((s) => s.setFocusMode)
  const [streamMode, setStreamMode] = useState<StreamMode>(
    () => (localStorage.getItem(MODE_KEY) as StreamMode) || 'mjpeg')

  // Which AI modules' overlays are currently shown — restricted to what this
  // tenant actually licenses (Live Wall analytics switcher).
  const { data: licensedModules = [] } = useQuery({ queryKey: ['my-enabled-modules'], queryFn: getMyEnabledModules })
  const availableModules = ALL_AI_MODULES.filter((m) => licensedModules.includes(m))
  const [activeModules, setActiveModules] = useState<string[]>(() => {
    try { return JSON.parse(localStorage.getItem(OVERLAY_MODULES_KEY) ?? '[]') } catch { return [] }
  })
  const toggleModule = (m: string) => {
    setActiveModules((prev) => {
      const next = prev.includes(m) ? prev.filter((x) => x !== m) : [...prev, m]
      localStorage.setItem(OVERLAY_MODULES_KEY, JSON.stringify(next))
      return next
    })
  }

  // camera_id → alert currently flashing on that cell
  const [alertFlash, setAlertFlash] = useState<Record<string, WallAlert>>({})
  // Rolling strip of the last few high/critical alerts across the whole wall
  const [recentAlerts, setRecentAlerts] = useState<WallAlert[]>([])
  // The alert currently open in the response dialog (from a cell chip or the strip)
  const [respondingAlert, setRespondingAlert] = useState<WallAlert | null>(null)
  // The cell currently drawing a zone (opens RestrictedZoneDialog pre-scoped to it)
  const [zoneCell, setZoneCell] = useState<WallCell | null>(null)

  const { lastMessage } = useWebSocket()
  const cols = GRID_CONFIGS.find((g) => g.value === gridSize)?.cols ?? 2
  const qc = useQueryClient()

  const updateCells = useCallback((next: WallCell[]) => {
    setCells(next)
    saveWall(next)
  }, [])

  // Refs so the WS handler always sees current state without re-subscribing
  const cellsRef = useRef(cells)
  cellsRef.current = cells
  const gridRef = useRef(gridSize)
  gridRef.current = gridSize
  const autoPopRef = useRef(autoPop)
  autoPopRef.current = autoPop

  const flashCamera = useCallback((cameraId: string, alert: WallAlert) => {
    setAlertFlash((prev) => ({ ...prev, [cameraId]: alert }))
    setTimeout(() => {
      setAlertFlash((prev) => {
        const { [cameraId]: _gone, ...rest } = prev
        return rest
      })
    }, FLASH_MS)
  }, [])

  const pushRecentAlert = useCallback((entry: WallAlert) => {
    setRecentAlerts((prev) => [entry, ...prev].slice(0, 5))
  }, [])

  // ── Alert auto-pop (Gap 83): flash the camera's cell; if the camera isn't
  // on the wall, pull it in (replacing the last cell when the grid is full).
  // Also feeds the bottom alert strip so operators see "what just happened"
  // across the whole wall, independent of which cameras are currently tiled.
  useEffect(() => {
    if (!lastMessage) return
    let event: any
    try { event = JSON.parse(lastMessage) } catch { return }
    if (event.event_type !== 'alert_created') return
    const p = event.payload ?? {}
    const severity = p.severity ?? ''
    const cameraId = p.camera_id ?? ''
    if (!cameraId || (severity !== 'high' && severity !== 'critical')) return
    const title = p.title ?? 'Alert'
    const createdAt = event.occurred_at ?? new Date().toISOString()

    const existingCell = cellsRef.current.find((c) => c.camera_id === cameraId)
    if (existingCell) {
      const alert: WallAlert = {
        id: p.alert_id ?? crypto.randomUUID(), title, severity, camera_id: cameraId,
        module_type: p.module_type, camera_name: existingCell.camera_name,
        site_name: existingCell.site_name ?? null, created_at: createdAt,
      }
      flashCamera(cameraId, alert)
      pushRecentAlert(alert)
      return
    }
    if (!autoPopRef.current) {
      pushRecentAlert({
        id: p.alert_id ?? crypto.randomUUID(), title, severity, camera_id: cameraId,
        module_type: p.module_type, created_at: createdAt,
      })
      return
    }

    void (async () => {
      try {
        const cam = await apiClient.get(`/api/v1/cameras/${cameraId}`).then((r) => r.data)
        const streams = await apiClient.get(`/api/v1/cameras/${cameraId}/streams`).then((r) => r.data)
        if (!streams?.length) return
        const newCell: WallCell = {
          camera_id: cameraId,
          stream_id: streams[0].id,
          camera_name: cam.name ?? 'Camera',
          site_name: cam.site_name ?? undefined,
        }
        const current = cellsRef.current
        const next = current.length >= gridRef.current
          ? [...current.slice(0, gridRef.current - 1), newCell]
          : [...current, newCell]
        updateCells(next)
        const alert: WallAlert = {
          id: p.alert_id ?? crypto.randomUUID(), title, severity, camera_id: cameraId,
          module_type: p.module_type, camera_name: newCell.camera_name,
          site_name: newCell.site_name ?? null, created_at: createdAt,
        }
        flashCamera(cameraId, alert)
        pushRecentAlert(alert)
      } catch { /* camera not visible to this user (site scoping) — skip */ }
    })()
  }, [lastMessage, flashCamera, updateCells, pushRecentAlert])

  const toggleAutoPop = () => {
    const next = !autoPop
    setAutoPop(next)
    localStorage.setItem(AUTOPOP_KEY, String(next))
  }

  // ── Kiosk / fullscreen: Electron gets true kiosk mode; browsers get the
  // Fullscreen API. Both also flip focus-mode so AppShell hides its own
  // sidebar/topbar — fullscreening the window alone doesn't hide the app's
  // own chrome. Focus-mode is the primary, always-applied toggle; the
  // Fullscreen API call is best-effort on top of it (browsers can reject
  // requestFullscreen for reasons outside our control — e.g. missing
  // transient user activation — and that must not block hiding our own
  // chrome, which is the actual ask). Esc exits browser fullscreen
  // (Electron handles Esc itself); the fullscreenchange listener below
  // keeps focus-mode in sync when a real fullscreen session ends.
  // Branches on our own `kiosk` state, not document.fullscreenElement — if
  // requestFullscreen() is ever rejected (missing user gesture, browser
  // policy), fullscreenElement stays null while kiosk is already true, and
  // keying off fullscreenElement would re-enter instead of exit, leaving
  // the user stuck with no way to bring the chrome back via this button.
  const toggleKiosk = async () => {
    if (window.electronAPI?.setKiosk) {
      const now = await window.electronAPI.setKiosk(!kiosk)
      setKiosk(now)
      setFocusMode(now)
      return
    }
    if (kiosk) {
      setKiosk(false)
      setFocusMode(false)
      if (document.fullscreenElement) {
        try { await document.exitFullscreen() } catch { /* already exiting */ }
      }
      return
    }
    setKiosk(true)
    setFocusMode(true)
    try { await document.documentElement.requestFullscreen() } catch { /* focus mode still applies */ }
  }

  useEffect(() => {
    const sync = () => {
      if (!document.fullscreenElement) { setKiosk(false); setFocusMode(false) }
    }
    document.addEventListener('fullscreenchange', sync)
    return () => document.removeEventListener('fullscreenchange', sync)
  }, [setFocusMode])

  const addCell = (cell: WallCell) => updateCells([...cells, cell].slice(0, gridSize))
  const removeCell = (idx: number) => updateCells(cells.filter((_, i) => i !== idx))

  // ── Saved layouts (Gap 84): named walls that follow the operator ─────────
  const [layoutId, setLayoutId] = useState('')
  const [saveOpen, setSaveOpen] = useState(false)
  const [saveName, setSaveName] = useState('')
  const [saveShared, setSaveShared] = useState(false)
  const [overwrite, setOverwrite] = useState(true)
  const { data: layouts = [] } = useQuery({ queryKey: ['wall-layouts'], queryFn: listWallLayouts })
  const currentLayout = layouts.find((l) => l.id === layoutId)

  const applyLayout = (layout: WallLayout) => {
    const grid = (GRID_CONFIGS.some((g) => g.value === layout.grid_size)
      ? layout.grid_size : 4) as GridSize
    setGridSize(grid)
    updateCells(layout.cells.slice(0, grid).map((c) => ({
      camera_id: c.camera_id,
      stream_id: c.stream_id,
      camera_name: c.camera_name,
      site_name: c.site_name ?? undefined,
    })))
    setLayoutId(layout.id)
  }

  // ── Multi-screen: a window opened via openLiveWallWindow(layoutId) lands
  // here with ?layout=<id> in the URL — auto-apply it once loaded so each
  // pop-out window shows its own saved camera set without manual reselection.
  const [searchParams] = useSearchParams()
  const appliedFromUrlRef = useRef(false)
  useEffect(() => {
    if (appliedFromUrlRef.current || layouts.length === 0) return
    const wantedId = searchParams.get('layout')
    if (!wantedId) return
    const found = layouts.find((l) => l.id === wantedId)
    if (found) {
      applyLayout(found)
      appliedFromUrlRef.current = true
    }
  }, [layouts, searchParams])

  const { mutate: saveLayout, isPending: saving } = useMutation({
    mutationFn: () => {
      const payload = { name: saveName.trim(), grid_size: gridSize, cells, is_shared: saveShared }
      return currentLayout?.is_mine && overwrite
        ? updateWallLayout(currentLayout.id, payload)
        : createWallLayout(payload)
    },
    onSuccess: (saved: WallLayout) => {
      qc.invalidateQueries({ queryKey: ['wall-layouts'] })
      setLayoutId(saved.id)
      setSaveOpen(false)
    },
  })

  const { mutate: removeLayout } = useMutation({
    mutationFn: (id: string) => deleteWallLayout(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['wall-layouts'] })
      setLayoutId('')
    },
  })

  const openSaveDialog = () => {
    setSaveName(currentLayout?.is_mine ? currentLayout.name : '')
    setSaveShared(currentLayout?.is_mine ? currentLayout.is_shared : false)
    setOverwrite(Boolean(currentLayout?.is_mine))
    setSaveOpen(true)
  }

  return (
    <Box sx={{ p: 3 }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, mb: 2, flexWrap: 'wrap' }}>
        <Typography variant="h5" fontWeight={800} sx={{ flex: 1 }}>
          Live Wall
        </Typography>
        <Select
          size="small"
          displayEmpty
          value={layoutId}
          onChange={(e) => {
            const layout = layouts.find((l) => l.id === e.target.value)
            if (layout) applyLayout(layout)
            else setLayoutId('')
          }}
          sx={{ minWidth: 170 }}
          renderValue={(v) => {
            const l = layouts.find((x) => x.id === v)
            return l ? l.name : 'Layouts…'
          }}
        >
          <MenuItem value="">
            <em>Unsaved wall</em>
          </MenuItem>
          {layouts.map((l) => (
            <MenuItem key={l.id} value={l.id}>
              <ListItemText
                primary={l.name}
                secondary={l.is_mine ? undefined : `Shared by ${l.owner_name}`}
              />
            </MenuItem>
          ))}
        </Select>
        <Tooltip title="Save this wall as a layout">
          <IconButton size="small" onClick={openSaveDialog}>
            <SaveIcon fontSize="small" />
          </IconButton>
        </Tooltip>
        {currentLayout?.is_mine && (
          <Tooltip title={`Delete layout "${currentLayout.name}"`}>
            <IconButton size="small" color="error" onClick={() => removeLayout(currentLayout.id)}>
              <DeleteIcon fontSize="small" />
            </IconButton>
          </Tooltip>
        )}
        <Tooltip title="Open this wall in a new window — drag it to another monitor for multi-screen monitoring">
          <IconButton size="small" onClick={() => openLiveWallWindow(layoutId || undefined)}>
            <OpenInNewIcon fontSize="small" />
          </IconButton>
        </Tooltip>
        <Tooltip title="Automatically bring the camera on screen when a high or critical alert fires">
          <FormControlLabel
            control={<Switch size="small" checked={autoPop} onChange={toggleAutoPop} />}
            label={<Typography variant="caption">Auto-pop alerts</Typography>}
            sx={{ mr: 0 }}
          />
        </Tooltip>
        <ButtonGroup size="small" variant="outlined">
          {GRID_CONFIGS.map((g) => (
            <Button
              key={g.value}
              onClick={() => setGridSize(g.value)}
              variant={gridSize === g.value ? 'contained' : 'outlined'}
            >
              {g.label}
            </Button>
          ))}
        </ButtonGroup>
        <Tooltip title="Streaming mode — HD/HLS scales better across many cells and over WAN; Live/MJPEG is lowest latency">
          <ButtonGroup size="small" variant="outlined">
            <Button
              onClick={() => { setStreamMode('mjpeg'); localStorage.setItem(MODE_KEY, 'mjpeg') }}
              variant={streamMode === 'mjpeg' ? 'contained' : 'outlined'}
            >
              Live
            </Button>
            <Button
              onClick={() => { setStreamMode('hls'); localStorage.setItem(MODE_KEY, 'hls') }}
              variant={streamMode === 'hls' ? 'contained' : 'outlined'}
            >
              HD
            </Button>
          </ButtonGroup>
        </Tooltip>
        {availableModules.length > 0 && (
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, flexWrap: 'wrap' }}>
            <Typography variant="caption" color="text.secondary" sx={{ mr: 0.25 }}>Analytics:</Typography>
            {availableModules.map((m) => (
              <Chip
                key={m}
                label={MODULE_LABELS[m as AiModuleType] ?? m}
                size="small"
                onClick={() => toggleModule(m)}
                color={activeModules.includes(m) ? 'primary' : 'default'}
                variant={activeModules.includes(m) ? 'filled' : 'outlined'}
                sx={{ cursor: 'pointer' }}
              />
            ))}
          </Box>
        )}
        <Tooltip title={kiosk ? 'Exit full screen (Esc)' : 'Enter full screen for continuous monitoring'}>
          <Button
            size="small"
            variant={kiosk ? 'contained' : 'outlined'}
            startIcon={kiosk ? <FullscreenExitIcon /> : <FullscreenIcon />}
            onClick={toggleKiosk}
          >
            {kiosk ? 'Exit Full Screen' : 'Full Screen'}
          </Button>
        </Tooltip>
        <Button
          variant="contained"
          size="small"
          startIcon={<AddIcon />}
          onClick={() => setPickerOpen(true)}
          disabled={cells.length >= gridSize}
        >
          Add Camera
        </Button>
      </Box>

      <Grid container spacing={1}>
        {Array.from({ length: gridSize }).map((_, idx) => (
          <Grid key={idx} size={12 / cols}>
            {cells[idx] ? (
              <LiveCell
                cell={cells[idx]}
                onRemove={() => removeCell(idx)}
                alert={alertFlash[cells[idx].camera_id] ?? null}
                mode={streamMode}
                activeModules={activeModules}
                onDrawZone={() => setZoneCell(cells[idx])}
                onOpenAlert={() => setRespondingAlert(alertFlash[cells[idx].camera_id] ?? null)}
              />
            ) : (
              <Box
                sx={{
                  aspectRatio: '16/9',
                  border: '2px dashed rgba(255,255,255,0.1)',
                  borderRadius: 1,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  cursor: 'pointer',
                  '&:hover': { borderColor: 'primary.main', bgcolor: 'rgba(108,99,255,0.05)' },
                }}
                onClick={() => setPickerOpen(true)}
              >
                <Typography color="text.disabled" variant="caption">
                  Click to add camera
                </Typography>
              </Box>
            )}
          </Grid>
        ))}
      </Grid>

      {/* Bottom alert strip — persistent "what just happened" feed, visible in and out of full screen */}
      {recentAlerts.length > 0 && (
        <Box
          sx={{
            mt: 2, p: 1, borderRadius: 1.5,
            bgcolor: 'rgba(0,0,0,0.55)', border: '1px solid rgba(255,255,255,0.08)',
            display: 'flex', alignItems: 'center', gap: 1.5, overflowX: 'auto',
          }}
        >
          <Typography variant="caption" sx={{ color: 'text.secondary', fontWeight: 700, flexShrink: 0, textTransform: 'uppercase', fontSize: '0.62rem' }}>
            Recent Alerts
          </Typography>
          {recentAlerts.map((a) => (
            <Box
              key={a.id}
              onClick={() => {
                const idx = cellsRef.current.findIndex((c) => c.camera_id === a.camera_id)
                if (idx >= 0) flashCamera(a.camera_id, a)
                setRespondingAlert(a)
              }}
              sx={{
                display: 'flex', alignItems: 'center', gap: 0.75, flexShrink: 0,
                px: 1, py: 0.5, borderRadius: 1, cursor: 'pointer',
                bgcolor: a.severity === 'critical' ? 'rgba(255,69,96,0.12)' : 'rgba(255,152,0,0.12)',
                '&:hover': { bgcolor: a.severity === 'critical' ? 'rgba(255,69,96,0.22)' : 'rgba(255,152,0,0.22)' },
              }}
            >
              <Box sx={{ width: 6, height: 6, borderRadius: '50%', bgcolor: a.severity === 'critical' ? '#FF4560' : '#FF9800' }} />
              <Typography variant="caption" sx={{ fontWeight: 600, fontSize: '0.7rem' }} noWrap>{a.title}</Typography>
              {a.camera_name && (
                <Typography variant="caption" sx={{ color: 'text.secondary', fontSize: '0.65rem' }} noWrap>· {a.camera_name}</Typography>
              )}
              <Typography variant="caption" sx={{ color: 'text.disabled', fontSize: '0.62rem' }} noWrap>
                {a.created_at ? new Date(a.created_at).toLocaleTimeString() : ''}
              </Typography>
            </Box>
          ))}
        </Box>
      )}

      <CameraPicker
        open={pickerOpen}
        onClose={() => setPickerOpen(false)}
        onAdd={addCell}
        existing={cells}
      />

      {respondingAlert && (
        <AlertResponseDialog
          alert={respondingAlert}
          onClose={() => setRespondingAlert(null)}
          onResolved={() => {
            setAlertFlash((prev) => {
              const { [respondingAlert.camera_id]: _gone, ...rest } = prev
              return rest
            })
            setRecentAlerts((prev) => prev.filter((a) => a.id !== respondingAlert.id))
            qc.invalidateQueries({ queryKey: ['camera-overlay', respondingAlert.camera_id] })
            setRespondingAlert(null)
          }}
        />
      )}

      {zoneCell && (
        <RestrictedZoneDialog
          open
          onClose={() => setZoneCell(null)}
          initialCameraId={zoneCell.camera_id}
          initialCameraName={zoneCell.camera_name}
        />
      )}

      {/* Save layout dialog */}
      <Dialog open={saveOpen} onClose={() => setSaveOpen(false)} maxWidth="xs" fullWidth>
        <DialogTitle>Save Wall Layout</DialogTitle>
        <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 1.5, pt: 1 }}>
          <TextField
            label="Layout name"
            size="small"
            value={saveName}
            onChange={(e) => setSaveName(e.target.value)}
            autoFocus
            fullWidth
          />
          <FormControlLabel
            control={<Checkbox size="small" checked={saveShared}
                               onChange={(e) => setSaveShared(e.target.checked)} />}
            label={<Typography variant="body2">Share with everyone in this organisation</Typography>}
          />
          {currentLayout?.is_mine && (
            <FormControlLabel
              control={<Checkbox size="small" checked={overwrite}
                                 onChange={(e) => setOverwrite(e.target.checked)} />}
              label={<Typography variant="body2">Overwrite “{currentLayout.name}”</Typography>}
            />
          )}
          <Typography variant="caption" color="text.secondary">
            Saves the current {cells.length}-camera wall and grid size to the server,
            so it follows you to any machine.
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setSaveOpen(false)} disabled={saving}>Cancel</Button>
          <Button variant="contained" onClick={() => saveLayout()}
                  disabled={saving || !saveName.trim()}>
            Save
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  )
}
