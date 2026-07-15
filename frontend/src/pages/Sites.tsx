import { useState } from 'react'
import {
  Box, Button, Chip, Dialog, DialogActions, DialogContent, DialogTitle,
  Grid, IconButton, MenuItem, Stack, TextField, Tooltip, Typography,
} from '@mui/material'
import AddIcon from '@mui/icons-material/Add'
import EditIcon from '@mui/icons-material/Edit'
import BlockIcon from '@mui/icons-material/Block'
import VideocamIcon from '@mui/icons-material/Videocam'
import WarningAmberIcon from '@mui/icons-material/WarningAmber'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { getSites, createSite, updateSite, deactivateSite } from '@/api/sites'
import { listClients } from '@/api/invoicing'
import type { Site } from '@/types/api'
import { GlassCard } from '@/components/common/GlassCard'
import { PageHeader } from '@/components/common/PageHeader'

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
  const [clientId, setClientId] = useState(site?.client_id ?? '')
  const [billRate, setBillRate] = useState(site?.bill_rate != null ? String(site.bill_rate) : '')

  const { data: clients = [] } = useQuery({ queryKey: ['billing-clients'], queryFn: () => listClients() })

  const mutation = useMutation({
    mutationFn: () => {
      const data = {
        name,
        address: address || undefined,
        description: description || undefined,
        latitude: latitude !== '' ? Number(latitude) : undefined,
        longitude: longitude !== '' ? Number(longitude) : undefined,
        geofence_radius_meters: geofenceRadius !== '' ? Number(geofenceRadius) : undefined,
        client_id: clientId || undefined,
        bill_rate: billRate !== '' ? Number(billRate) : undefined,
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
        <Stack direction="row" spacing={1.5}>
          <TextField
            label="Latitude" type="number" value={latitude}
            onChange={(e) => setLatitude(e.target.value)} sx={{ flex: 1 }}
          />
          <TextField
            label="Longitude" type="number" value={longitude}
            onChange={(e) => setLongitude(e.target.value)} sx={{ flex: 1 }}
          />
          <TextField
            label="Radius (m)" type="number" value={geofenceRadius}
            onChange={(e) => setGeofenceRadius(e.target.value)} sx={{ flex: 1 }}
            inputProps={{ min: 1 }}
          />
        </Stack>
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
    </Box>
  )
}
