import { useState } from 'react'
import {
  Box, Button, ButtonGroup, Checkbox, Chip, Dialog, DialogContent, DialogTitle,
  Divider, IconButton, List, ListItemButton, ListItemText, Menu, MenuItem,
  Select, Tooltip, Typography,
} from '@mui/material'
import AddIcon from '@mui/icons-material/Add'
import CloseIcon from '@mui/icons-material/Close'
import TuneIcon from '@mui/icons-material/Tune'
import { useQuery } from '@tanstack/react-query'
import { useAuthStore } from '@/store/auth'
import { apiClient } from '@/api/client'
import { getSites } from '@/api/sites'
import { getMyEnabledModules, ALL_AI_MODULES, MODULE_LABELS, type AiModuleType } from '@/api/licenses'

export type GridSize = 1 | 4 | 9 | 16

export interface WallScreenCell {
  camera_id: string
  stream_id: string
  camera_name: string
  site_name?: string
  /** Per-camera analytics override; undefined/null = inherit the screen's. */
  analytics_modules?: string[] | null
}

export const GRID_CONFIGS: { label: string; value: GridSize; cols: number }[] = [
  { label: '1×1', value: 1, cols: 1 },
  { label: '2×2', value: 4, cols: 2 },
  { label: '3×3', value: 9, cols: 3 },
  { label: '4×4', value: 16, cols: 4 },
]

interface CameraPickerProps {
  open: boolean
  onClose: () => void
  onAdd: (cell: WallScreenCell) => void
  existing: WallScreenCell[]
}

/** Camera-picker dialog shared by the standalone Live Wall page and the
 * multi-screen profile setup wizard (WallScreenEditor below) — extracted
 * here since it has no page-specific coupling. */
export function CameraPicker({ open, onClose, onAdd, existing }: CameraPickerProps) {
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
          {filteredCameras.map((c: any) => (
            <ListItemButton key={c.id} onClick={() => handleCameraSelect(c)}>
              <ListItemText primary={c.name} secondary={c.site_name ?? c.location ?? 'No site'} />
            </ListItemButton>
          ))}
          {filteredCameras.length === 0 && (
            <Typography color="text.secondary" p={2}>No cameras available</Typography>
          )}
        </List>
      </DialogContent>
    </Dialog>
  )
}

export interface WallScreenEditorProps {
  gridSize: GridSize
  cells: WallScreenCell[]
  analyticsModules: string[]
  onChangeGridSize: (g: GridSize) => void
  onChangeCells: (cells: WallScreenCell[]) => void
  onChangeAnalytics: (modules: string[]) => void
}

/** One screen's camera + grid-size + analytics configuration — used inside
 * the "how many screens" multi-screen profile setup wizard. This is a
 * lightweight config editor (no live video, no recording/kiosk controls)
 * since a profile screen is configured before it's ever opened, unlike the
 * standalone Live Wall page's live monitoring cells. */
export function WallScreenEditor({
  gridSize, cells, analyticsModules, onChangeGridSize, onChangeCells, onChangeAnalytics,
}: WallScreenEditorProps) {
  const [pickerOpen, setPickerOpen] = useState(false)
  const [pickerSlot, setPickerSlot] = useState<number | null>(null)
  // Which cell's per-camera analytics menu is open (index into cells)
  const [analyticsSlot, setAnalyticsSlot] = useState<number | null>(null)
  const [analyticsAnchor, setAnalyticsAnchor] = useState<HTMLElement | null>(null)
  const { data: licensedModules = [] } = useQuery({ queryKey: ['my-enabled-modules'], queryFn: getMyEnabledModules })
  const availableModules = ALL_AI_MODULES.filter((m) => licensedModules.includes(m))
  const cols = GRID_CONFIGS.find((g) => g.value === gridSize)?.cols ?? 2

  const toggleModule = (m: string) => {
    onChangeAnalytics(
      analyticsModules.includes(m) ? analyticsModules.filter((x) => x !== m) : [...analyticsModules, m]
    )
  }

  const addCellAtSlot = (cell: WallScreenCell) => {
    const next = [...cells]
    if (pickerSlot !== null) next[pickerSlot] = cell
    else next.push(cell)
    onChangeCells(next.slice(0, gridSize))
    setPickerOpen(false)
    setPickerSlot(null)
  }

  const removeCell = (idx: number) => onChangeCells(cells.filter((_, i) => i !== idx))

  /** Per-camera analytics override. `undefined` = inherit the screen's
   * selection; an array (even empty) = an explicit choice for this camera. */
  const setCellModules = (idx: number, modules: string[] | undefined) => {
    onChangeCells(cells.map((c, i) => (i === idx ? { ...c, analytics_modules: modules } : c)))
  }

  const toggleCellModule = (idx: number, m: string) => {
    const current = cells[idx]?.analytics_modules
    // First click on an inheriting cell starts from the screen's selection, so
    // the operator adjusts from what they can already see rather than from
    // nothing.
    const base = current ?? analyticsModules
    setCellModules(idx, base.includes(m) ? base.filter((x) => x !== m) : [...base, m])
  }

  return (
    <Box>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1.5, flexWrap: 'wrap' }}>
        <Typography variant="caption" color="text.secondary">Grid:</Typography>
        <ButtonGroup size="small" variant="outlined">
          {GRID_CONFIGS.map((g) => (
            <Button
              key={g.value}
              onClick={() => onChangeGridSize(g.value)}
              variant={gridSize === g.value ? 'contained' : 'outlined'}
            >
              {g.label}
            </Button>
          ))}
        </ButtonGroup>
        {availableModules.length > 0 && (
          <>
            <Typography variant="caption" color="text.secondary" sx={{ ml: 1 }}>Analytics:</Typography>
            {availableModules.map((m) => (
              <Chip
                key={m}
                label={MODULE_LABELS[m as AiModuleType] ?? m}
                size="small"
                onClick={() => toggleModule(m)}
                color={analyticsModules.includes(m) ? 'primary' : 'default'}
                variant={analyticsModules.includes(m) ? 'filled' : 'outlined'}
                sx={{ cursor: 'pointer' }}
              />
            ))}
          </>
        )}
      </Box>

      <Box sx={{ display: 'grid', gridTemplateColumns: `repeat(${cols}, 1fr)`, gap: 1 }}>
        {Array.from({ length: gridSize }).map((_, idx) => (
          <Box
            key={idx}
            sx={{
              aspectRatio: '16/9',
              border: cells[idx] ? '1px solid rgba(255,255,255,0.15)' : '2px dashed rgba(255,255,255,0.1)',
              borderRadius: 1,
              position: 'relative',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              cursor: 'pointer',
              bgcolor: 'rgba(0,0,0,0.3)',
              '&:hover': { borderColor: 'primary.main' },
            }}
            onClick={() => { setPickerSlot(idx); setPickerOpen(true) }}
          >
            {cells[idx] ? (
              <>
                <Typography variant="caption" sx={{ px: 1, textAlign: 'center' }} noWrap>
                  {cells[idx].camera_name}
                </Typography>
                {cells[idx].analytics_modules && (
                  <Chip
                    label={cells[idx].analytics_modules!.length
                      ? `${cells[idx].analytics_modules!.length} analytic${cells[idx].analytics_modules!.length === 1 ? '' : 's'}`
                      : 'No analytics'}
                    size="small"
                    color="primary"
                    sx={{ position: 'absolute', bottom: 2, left: 2, height: 16, fontSize: '0.6rem' }}
                  />
                )}
                {availableModules.length > 0 && (
                  <Tooltip title="Analytics for this camera (overrides the screen's selection)">
                    <IconButton
                      size="small"
                      onClick={(e) => {
                        e.stopPropagation()
                        setAnalyticsSlot(idx)
                        setAnalyticsAnchor(e.currentTarget)
                      }}
                      sx={{ position: 'absolute', top: 2, left: 2, color: '#fff' }}
                    >
                      <TuneIcon sx={{ fontSize: 14 }} />
                    </IconButton>
                  </Tooltip>
                )}
                <IconButton
                  size="small"
                  onClick={(e) => { e.stopPropagation(); removeCell(idx) }}
                  sx={{ position: 'absolute', top: 2, right: 2, color: '#fff' }}
                >
                  <CloseIcon sx={{ fontSize: 14 }} />
                </IconButton>
              </>
            ) : (
              <AddIcon sx={{ color: 'text.disabled' }} />
            )}
          </Box>
        ))}
      </Box>

      <CameraPicker
        open={pickerOpen}
        onClose={() => { setPickerOpen(false); setPickerSlot(null) }}
        onAdd={addCellAtSlot}
        existing={cells}
      />

      {/* Per-camera analytics override menu */}
      <Menu
        open={analyticsSlot !== null}
        anchorEl={analyticsAnchor}
        onClose={() => { setAnalyticsSlot(null); setAnalyticsAnchor(null) }}
      >
        <MenuItem
          onClick={() => { if (analyticsSlot !== null) setCellModules(analyticsSlot, undefined) }}
          selected={analyticsSlot !== null && !cells[analyticsSlot]?.analytics_modules}
        >
          <ListItemText
            primary="Inherit from screen"
            secondary={analyticsModules.length
              ? analyticsModules.map((m) => MODULE_LABELS[m as AiModuleType] ?? m).join(', ')
              : 'No analytics selected'}
          />
        </MenuItem>
        <Divider />
        {availableModules.map((m) => {
          const cellModules = analyticsSlot !== null ? cells[analyticsSlot]?.analytics_modules : null
          const checked = (cellModules ?? analyticsModules).includes(m)
          return (
            <MenuItem
              key={m}
              onClick={() => { if (analyticsSlot !== null) toggleCellModule(analyticsSlot, m) }}
            >
              <Checkbox size="small" checked={checked} sx={{ p: 0.5, mr: 1 }} />
              <ListItemText primary={MODULE_LABELS[m as AiModuleType] ?? m} />
            </MenuItem>
          )
        })}
      </Menu>
    </Box>
  )
}
