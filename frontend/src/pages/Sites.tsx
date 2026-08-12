import { useState } from 'react'
import {
  Alert, Box, Button, Chip, Dialog, DialogActions, DialogContent, DialogTitle,
  FormControlLabel, Grid, IconButton, MenuItem, Stack, Switch, TextField,
  Tooltip, Typography,
} from '@mui/material'
import AddIcon from '@mui/icons-material/Add'
import EditIcon from '@mui/icons-material/Edit'
import BlockIcon from '@mui/icons-material/Block'
import VideocamIcon from '@mui/icons-material/Videocam'
import VideoSettingsIcon from '@mui/icons-material/VideoSettings'
import WarningAmberIcon from '@mui/icons-material/WarningAmber'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { getSites, createSite, updateSite, deactivateSite } from '@/api/sites'
import { listClients } from '@/api/invoicing'
import { getCameras } from '@/api/cameras'
import type { Site } from '@/types/api'
import { GlassCard } from '@/components/common/GlassCard'
import { PageHeader } from '@/components/common/PageHeader'
import { RecordingPolicyDialog } from '@/components/common/RecordingPolicyDialog'
import { LocationPickerMap } from '@/components/common/LocationPickerMap'
import { usePermission } from '@/hooks/usePermission'

interface SiteDialogProps {
  open: boolean
  site?: Site
  onClose: () => void
}

function SiteDialog({ open, site, onClose }: SiteDialogProps) {
  const qc = useQueryClient()
  const isEdit = Boolean(site)
  const [name, setName] = useState(site?.name ?? '')
  const [address, setAddress] = useState(site?.address ?? '')
  const [description, setDescription] = useState(site?.description ?? '')
  const [latitude, setLatitude] = useState(site?.latitude != null ? String(site.latitude) : '')
  const [longitude, setLongitude] = useState(site?.longitude != null ? String(site.longitude) : '')
  const [geofenceRadius, setGeofenceRadius] = useState(
    site?.geofence_radius_meters != null ? String(site.geofence_radius_meters) : ''
  )
  const [lateGrace, setLateGrace] = useState(
    site?.late_grace_minutes != null ? String(site.late_grace_minutes) : ''
  )
  const [clientId, setClientId] = useState(site?.client_id ?? '')
  const [billRate, setBillRate] = useState(site?.bill_rate != null ? String(site.bill_rate) : '')
  const [vmsEnabled, setVmsEnabled] = useState(site?.vms_enabled ?? false)
  const [entryCam, setEntryCam] = useState(site?.entry_lpr_camera_id ?? '')
  const [exitCam, setExitCam] = useState(site?.exit_lpr_camera_id ?? '')
  const [freeParking, setFreeParking] = useState(
    site?.free_parking_minutes != null ? String(site.free_parking_minutes) : ''
  )

  const { data: clients = [] } = useQuery({ queryKey: ['billing-clients'], queryFn: () => listClients() })
  // Only this site's own cameras can be bound to its lanes — offering the whole
  // tenant's cameras would let an admin wire another site's gate by accident.
  const { data: allCameras = [] } = useQuery({
    queryKey: ['cameras'], queryFn: () => getCameras(), enabled: open && isEdit,
  })
  const siteCameras = allCameras.filter((c: any) => c.site_id === site?.id)

  const mutation = useMutation({
    mutationFn: () => {
      const data = {
        name,
        address: address || undefined,
        description: description || undefined,
        latitude: latitude !== '' ? Number(latitude) : undefined,
        longitude: longitude !== '' ? Number(longitude) : undefined,
        geofence_radius_meters: geofenceRadius !== '' ? Number(geofenceRadius) : undefined,
        late_grace_minutes: lateGrace !== '' ? Number(lateGrace) : undefined,
        client_id: clientId || undefined,
        bill_rate: billRate !== '' ? Number(billRate) : undefined,
        // Edit-only, and sent as explicit null rather than undefined when
        // cleared — that is what unbinds a camera or removes the allowance.
        ...(isEdit
          ? {
              vms_enabled: vmsEnabled,
              entry_lpr_camera_id: entryCam || null,
              exit_lpr_camera_id: exitCam || null,
              free_parking_minutes: freeParking !== '' ? Number(freeParking) : null,
            }
          : {}),
      }
      return isEdit ? updateSite(site!.id, data) : createSite(data)
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['sites'] })
      onClose()
    },
  })

  const handleSubmit = () => {
    if (!name.trim()) return
    mutation.mutate()
  }

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>{isEdit ? 'Edit Site' : 'Add Site'}</DialogTitle>
      <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: '12px !important' }}>
        <TextField
          label="Site Name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          required
          autoFocus
          fullWidth
        />
        <TextField
          label="Address"
          value={address}
          onChange={(e) => setAddress(e.target.value)}
          fullWidth
        />
        <TextField
          label="Description"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          multiline
          rows={3}
          fullWidth
        />
        <Typography variant="caption" color="text.secondary">
          Geofence — required for attendance check-in/out distance validation
        </Typography>
        {/* Placed on a map rather than typed. The stored values are unchanged;
            only the way an admin produces them differs. The radius circle is
            drawn around the pin so "200" is legible on the ground instead of
            being a number you find out was wrong when check-ins start
            failing. */}
        <LocationPickerMap
          latitude={latitude !== '' ? Number(latitude) : null}
          longitude={longitude !== '' ? Number(longitude) : null}
          onChange={(lat, lng) => {
            setLatitude(lat.toFixed(6))
            setLongitude(lng.toFixed(6))
          }}
          radiusMeters={geofenceRadius !== '' ? Number(geofenceRadius) : null}
        />
        <TextField
          label="Geofence radius (m)" type="number" value={geofenceRadius}
          onChange={(e) => setGeofenceRadius(e.target.value)}
          slotProps={{ htmlInput: { min: 1 } }}
          helperText="Shown as a circle on the map above"
          sx={{ maxWidth: 220 }}
        />
        {/* Sites do not behave alike: a remote gate with one bus an hour
            cannot hold the same standard as a lobby on a train line. Blank
            keeps the company-wide default, so only sites that need their own
            rule carry one. */}
        <TextField
          label="Late grace (minutes)" type="number" value={lateGrace}
          onChange={(e) => setLateGrace(e.target.value)}
          slotProps={{ htmlInput: { min: 0, max: 240 } }}
          helperText="Minutes after the rostered start before a guard counts as late. Blank = company default."
          sx={{ maxWidth: 300 }}
        />
        <Typography variant="caption" color="text.secondary">
          Billing — link this site to a client and rate for invoicing
        </Typography>
        <Stack direction="row" spacing={1.5}>
          <TextField
            select label="Billing Client" value={clientId}
            onChange={(e) => setClientId(e.target.value)} sx={{ flex: 1 }}
          >
            <MenuItem value="">No client</MenuItem>
            {clients.map((c) => (
              <MenuItem key={c.id} value={c.id}>{c.name}</MenuItem>
            ))}
          </TextField>
          <TextField
            label="Bill Rate ($/hr)" type="number" value={billRate}
            onChange={(e) => setBillRate(e.target.value)} sx={{ flex: 1 }}
            inputProps={{ min: 0, step: 0.01 }}
          />
        </Stack>

        {/* VMS is edit-only: the cameras must already exist and be assigned to
            this site before they can be bound to its entry/exit lanes. */}
        {isEdit && (
          <>
            <Typography variant="caption" color="text.secondary">
              Visitor Management — drive visitor entry/exit from this site&apos;s ANPR cameras
            </Typography>
            <FormControlLabel
              control={
                <Switch checked={vmsEnabled} onChange={(e) => setVmsEnabled(e.target.checked)} />
              }
              label="Enable Visitor Management at this site"
            />
            {vmsEnabled && siteCameras.length === 0 && (
              <Alert severity="warning">
                No cameras are assigned to this site yet. Assign a camera on the Cameras page
                before binding an entry or exit lane — until then visitors must be added by hand.
              </Alert>
            )}
            <Stack direction="row" spacing={1.5}>
              <TextField
                select label="Entry LPR camera" value={entryCam} disabled={!vmsEnabled}
                onChange={(e) => setEntryCam(e.target.value)} sx={{ flex: 1 }}
                helperText="Opens the visitor form on a plate read"
              >
                <MenuItem value="">Not configured</MenuItem>
                {siteCameras.map((c: any) => (
                  <MenuItem key={c.id} value={c.id}>{c.name}</MenuItem>
                ))}
              </TextField>
              <TextField
                select label="Exit LPR camera" value={exitCam} disabled={!vmsEnabled}
                onChange={(e) => setExitCam(e.target.value)} sx={{ flex: 1 }}
                helperText="Closes the visit automatically"
              >
                <MenuItem value="">Not configured</MenuItem>
                {siteCameras.map((c: any) => (
                  <MenuItem key={c.id} value={c.id}>{c.name}</MenuItem>
                ))}
              </TextField>
            </Stack>
            <TextField
              label="Free parking (minutes)" type="number" value={freeParking}
              onChange={(e) => setFreeParking(e.target.value)}
              disabled={!vmsEnabled}
              inputProps={{ min: 0 }}
              // Blank is meaningfully different from 0: blank means this site
              // does not meter parking at all, so nothing can ever overstay.
              helperText="Leave blank if this site does not meter parking. Exceeding it alerts the operator."
              sx={{ maxWidth: 320 }}
            />
          </>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button
          variant="contained"
          onClick={handleSubmit}
          disabled={!name.trim() || mutation.isPending}
        >
          {isEdit ? 'Save' : 'Create'}
        </Button>
      </DialogActions>
    </Dialog>
  )
}

export function SitesPage() {
  const qc = useQueryClient()
  const { data: sites = [], isLoading } = useQuery({ queryKey: ['sites'], queryFn: () => getSites() })
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editSite, setEditSite] = useState<Site | undefined>()
  const [policySite, setPolicySite] = useState<Site | undefined>()
  const canSeePolicy = usePermission('recording_policy:read')

  const deactivateMutation = useMutation({
    mutationFn: (id: string) => deactivateSite(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['sites'] }),
  })

  const openCreate = () => { setEditSite(undefined); setDialogOpen(true) }
  const openEdit = (s: Site) => { setEditSite(s); setDialogOpen(true) }

  return (
    <Box sx={{ p: 3 }}>
      <PageHeader
        title="Sites"
        subtitle="Manage physical locations that group cameras"
        action={
          <Button variant="contained" size="small" startIcon={<AddIcon />} onClick={openCreate}>
            Add Site
          </Button>
        }
      />

      {isLoading ? (
        <Typography color="text.secondary">Loading…</Typography>
      ) : sites.length === 0 ? (
        <GlassCard sx={{ p: 4, textAlign: 'center' }}>
          <Typography color="text.secondary">No sites yet. Create your first site to organize cameras by location.</Typography>
        </GlassCard>
      ) : (
        <Grid container spacing={2}>
          {sites.map((site) => (
            <Grid size={{ xs: 12, sm: 6, lg: 4 }} key={site.id}>
              <GlassCard sx={{ p: 2.5 }}>
                <Box sx={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between' }}>
                  <Box sx={{ flex: 1, minWidth: 0 }}>
                    <Typography variant="subtitle1" sx={{ fontWeight: 700 }} noWrap>
                      {site.name}
                    </Typography>
                    {site.address && (
                      <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }} noWrap>
                        {site.address}
                      </Typography>
                    )}
                    {site.description && (
                      <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5, fontSize: '0.78rem' }}>
                        {site.description}
                      </Typography>
                    )}
                  </Box>
                  <Box sx={{ display: 'flex', gap: 0.5, ml: 1 }}>
                    {canSeePolicy && (
                      <Tooltip title="Recording policy — retention, sync and clip settings for this site">
                        <IconButton size="small" onClick={() => setPolicySite(site)}>
                          <VideoSettingsIcon fontSize="small" />
                        </IconButton>
                      </Tooltip>
                    )}
                    <Tooltip title="Edit">
                      <IconButton size="small" onClick={() => openEdit(site)}>
                        <EditIcon fontSize="small" />
                      </IconButton>
                    </Tooltip>
                    {site.is_active && (
                      <Tooltip title="Deactivate">
                        <IconButton
                          size="small"
                          color="error"
                          onClick={() => deactivateMutation.mutate(site.id)}
                        >
                          <BlockIcon fontSize="small" />
                        </IconButton>
                      </Tooltip>
                    )}
                  </Box>
                </Box>

                <Box sx={{ display: 'flex', gap: 1, mt: 1.5, alignItems: 'center', flexWrap: 'wrap' }}>
                  <Chip
                    icon={<VideocamIcon />}
                    label={`${site.camera_count} camera${site.camera_count !== 1 ? 's' : ''}`}
                    size="small"
                    variant="outlined"
                  />
                  <Chip
                    label={site.is_active ? 'Active' : 'Inactive'}
                    size="small"
                    color={site.is_active ? 'success' : 'default'}
                  />
                  {site.client_name && (
                    <Chip label={site.client_name} size="small" variant="outlined" color="primary" />
                  )}
                  {(site.latitude == null || site.longitude == null || site.geofence_radius_meters == null) && (
                    <Tooltip title="Set latitude, longitude, and radius so attendance check-in/out can validate guard location for this site">
                      <Chip
                        icon={<WarningAmberIcon />}
                        label="Geofence not configured"
                        size="small"
                        color="warning"
                        variant="outlined"
                      />
                    </Tooltip>
                  )}
                  {/* Only shown once VMS is switched on. A site with VMS off is
                      not misconfigured — the manual visitor flow is the default
                      and perfectly valid, so flagging it would be noise. */}
                  {site.vms_enabled && (
                    site.entry_lpr_camera_id ? (
                      <Tooltip title="A plate read at this site's entry camera opens the visitor form on the operator's screen">
                        <Chip label="VMS · ANPR entry" size="small" color="success" variant="outlined" />
                      </Tooltip>
                    ) : (
                      <Tooltip title="Visitor Management is on but no entry camera is bound, so visitors must be added by hand">
                        <Chip
                          icon={<WarningAmberIcon />}
                          label="VMS · manual entry"
                          size="small"
                          color="warning"
                          variant="outlined"
                        />
                      </Tooltip>
                    )
                  )}
                </Box>
              </GlassCard>
            </Grid>
          ))}
        </Grid>
      )}

      <SiteDialog
        key={editSite?.id ?? 'new'}
        open={dialogOpen}
        site={editSite}
        onClose={() => setDialogOpen(false)}
      />
      {policySite && (
        <RecordingPolicyDialog
          key={policySite.id}
          open
          siteId={policySite.id}
          siteName={policySite.name}
          onClose={() => setPolicySite(undefined)}
        />
      )}
    </Box>
  )
}
