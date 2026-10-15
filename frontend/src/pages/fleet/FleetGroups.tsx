/**
 * Groups of fleet vehicles: the patrol cars of one site, the vans of a
 * contract. A geofence can apply to chosen groups instead of to every vehicle
 * of its site.
 *
 * A group that belongs to a site takes only that site's vehicles. Somebody
 * limited to sites sees their sites' groups only. A group is retired, never
 * deleted.
 */
import { useMemo, useState } from 'react'
import {
  Box, Button, Checkbox, Dialog, DialogActions, DialogContent, DialogTitle, FormControlLabel, IconButton, List, ListItem,
  ListItemButton, ListItemIcon, ListItemText, MenuItem, Skeleton, Switch, Table, TableBody, TableCell, TableContainer,
  TableHead, TableRow, TextField, Tooltip, Typography,
} from '@mui/material'
import AddIcon from '@mui/icons-material/Add'
import EditIcon from '@mui/icons-material/EditOutlined'
import GroupIcon from '@mui/icons-material/GroupWorkOutlined'
import RetireIcon from '@mui/icons-material/DoNotDisturbOnOutlined'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { PageHeader } from '@/components/common/PageHeader'
import { GlassCard } from '@/components/common/GlassCard'
import { SkeletonRows } from '@/components/common/SkeletonRows'
import { FleetNav } from '@/components/fleet/FleetNav'
import { ErrorState, SaveStatus, TableEmptyRow, TableErrorRow } from '@/components/states'
import {
  changeFleetGroup, createFleetGroup, getFleetOptions, getGroupVehicles, listFleetGroups, listFleetVehicles, retireFleetGroup,
  setGroupVehicles, vehicleLabel, type FleetGroup,
} from '@/api/fleet'

const COLS = 5

export default function FleetGroups() {
  const qc = useQueryClient()
  const [retired, setRetired] = useState(false)
  const [editing, setEditing] = useState<FleetGroup | 'new' | null>(null)
  const [members, setMembers] = useState<FleetGroup | null>(null)
  const { data, isLoading, isLoadingError: groupsFailed, error: groupsError, refetch: refetchGroups } = useQuery({
    queryKey: ['fleet-groups', retired], queryFn: () => listFleetGroups(retired),
  })
  const { data: options } = useQuery({ queryKey: ['fleet-options'], queryFn: getFleetOptions })
  const retire = useMutation({
    mutationFn: (g: FleetGroup) => retireFleetGroup(g.id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['fleet-groups'] }),
  })
  const mayEdit = !!options?.may.edit
  const again = () => qc.invalidateQueries({ queryKey: ['fleet-groups'] })

  return (
    <Box sx={{ p: 3 }}>
      <PageHeader title="Vehicle groups" subtitle="Vehicles that belong together - a site's patrol cars, a contract's vans" />
      <FleetNav />
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, mb: 1, flexWrap: 'wrap' }}>
        {mayEdit && <Button variant="contained" startIcon={<AddIcon />} onClick={() => setEditing('new')}>New group</Button>}
        <FormControlLabel label="Show retired groups" control={<Switch size="small" checked={retired} onChange={(e) => setRetired(e.target.checked)} />} />
        <SaveStatus state={retire.isPending ? 'saving' : retire.isError ? 'failed' : 'idle'} error={retire.error} savingText="Retiring…" />
      </Box>
      <GlassCard sx={{ p: 0 }}>
        <TableContainer>
          <Table size="small" aria-label="Vehicle groups">
            <TableHead>
              <TableRow><TableCell>Group</TableCell><TableCell>Site</TableCell><TableCell>Vehicles</TableCell><TableCell>Status</TableCell><TableCell align="right">Actions</TableCell></TableRow>
            </TableHead>
            <TableBody>
              {isLoading ? (
                <SkeletonRows cols={COLS} rows={4} />
              ) : groupsFailed ? (
                <TableErrorRow cols={COLS} title="The groups could not be loaded" error={groupsError} onRetry={refetchGroups} />
              ) : !data?.items.length ? (
                <TableEmptyRow cols={COLS} title="No groups yet" hint={mayEdit ? 'Make one with the New group button.' : undefined} />
              ) : data.items.map((g) => (
                <TableRow key={g.id} hover sx={{ opacity: g.is_active ? 1 : 0.6 }}>
                  <TableCell><Typography variant="body2" sx={{ fontWeight: 600 }}>{g.name}</Typography>
                    {g.description && <Typography variant="caption" color="text.secondary">{g.description}</Typography>}</TableCell>
                  <TableCell>{g.site_name ?? 'Whole organisation'}</TableCell>
                  <TableCell>{g.vehicles}</TableCell>
                  <TableCell>{g.is_active ? 'In use' : 'Retired'}</TableCell>
                  <TableCell align="right">
                    {g.is_active && (
                      <Tooltip title="Vehicles in the group"><IconButton size="small" aria-label={`Vehicles in ${g.name}`} onClick={() => setMembers(g)}>
                        <GroupIcon fontSize="small" /></IconButton></Tooltip>)}
                    {g.is_active && mayEdit && (
                      <Tooltip title="Rename or move"><IconButton size="small" aria-label={`Change ${g.name}`} onClick={() => setEditing(g)}>
                        <EditIcon fontSize="small" /></IconButton></Tooltip>)}
                    {g.is_active && mayEdit && (
                      <Tooltip title="Retire"><IconButton size="small" aria-label={`Retire ${g.name}`} disabled={retire.isPending}
                                                          onClick={() => retire.mutate(g)}><RetireIcon fontSize="small" /></IconButton></Tooltip>)}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      </GlassCard>
      {editing && (
        <GroupDialog group={editing === 'new' ? null : editing} sites={options?.sites ?? []} siteRequired={!!options?.site_required}
                     onClose={() => setEditing(null)} onSaved={() => { setEditing(null); again() }} />
      )}
      {members && (
        <MembersDialog group={members} mayEdit={mayEdit} onClose={() => setMembers(null)} onSaved={() => { setMembers(null); again() }} />
      )}
    </Box>
  )
}

function GroupDialog({ group, sites, siteRequired, onClose, onSaved }: {
  group: FleetGroup | null; sites: { id: string; name: string }[]; siteRequired: boolean; onClose: () => void; onSaved: () => void
}) {
  const [name, setName] = useState(group?.name ?? '')
  const [description, setDescription] = useState(group?.description ?? '')
  const [siteId, setSiteId] = useState(group?.site_id ?? '')
  const save = useMutation({
    mutationFn: () => {
      const body = { name: name.trim(), description: description.trim() || null, site_id: siteId || null }
      return group ? changeFleetGroup(group.id, body) : createFleetGroup(body)
    },
    onSuccess: onSaved,
  })
  return (
    <Dialog open onClose={onClose} maxWidth="sm" fullWidth aria-labelledby="group-title">
      <DialogTitle id="group-title">{group ? `Change ${group.name}` : 'New group'}</DialogTitle>
      <DialogContent>
        <Box sx={{ display: 'grid', gap: 2, pt: 1 }}>
          <TextField label="Name" required size="small" value={name} onChange={(e) => setName(e.target.value)} />
          <TextField label="What it is for" size="small" value={description} onChange={(e) => setDescription(e.target.value)} />
          <TextField select label="Site" size="small" value={siteId} onChange={(e) => setSiteId(e.target.value)} required={siteRequired}
                     slotProps={{ select: { displayEmpty: true }, inputLabel: { shrink: true } }}
                     helperText="A group at a site takes only that site's vehicles">
            {!siteRequired && <MenuItem value="">Whole organisation</MenuItem>}
            {sites.map((s) => <MenuItem key={s.id} value={s.id}>{s.name}</MenuItem>)}
          </TextField>
        </Box>
      </DialogContent>
      <DialogActions sx={{ gap: 1 }}>
        <SaveStatus state={save.isPending ? 'saving' : save.isError ? 'failed' : 'idle'} error={save.error} />
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" disabled={!name.trim() || (siteRequired && !siteId) || save.isPending} onClick={() => save.mutate()}>
          {group ? 'Save' : 'Make the group'}
        </Button>
      </DialogActions>
    </Dialog>
  )
}

function MembersDialog({ group, mayEdit, onClose, onSaved }: { group: FleetGroup; mayEdit: boolean; onClose: () => void; onSaved: () => void }) {
  const current = useQuery({ queryKey: ['fleet-group-vehicles', group.id], queryFn: () => getGroupVehicles(group.id) })
  const candidates = useQuery({
    queryKey: ['fleet-group-candidates', group.site_id],
    queryFn: () => listFleetVehicles({ site_id: group.site_id ?? undefined, archived: 'active', sort: 'plate_number', limit: 200 }),
  })
  const [picked, setPicked] = useState<Set<string> | null>(null)
  const chosen = useMemo(() => picked ?? new Set(current.data?.items.filter((v) => !v.archived_at).map((v) => v.id) ?? []), [picked, current.data])
  const toggle = (id: string) => setPicked(() => {
    const next = new Set(chosen)
    if (next.has(id)) next.delete(id); else next.add(id)
    return next
  })
  const save = useMutation({ mutationFn: () => setGroupVehicles(group.id, [...chosen]), onSuccess: onSaved })
  const loading = current.isLoading || candidates.isLoading
  const failed = current.isLoadingError || candidates.isLoadingError
  return (
    <Dialog open onClose={onClose} maxWidth="sm" fullWidth aria-labelledby="members-title">
      <DialogTitle id="members-title">Vehicles in {group.name}</DialogTitle>
      <DialogContent>
        {group.site_id && <Typography variant="caption" color="text.secondary">Only {group.site_name}'s vehicles can be in this group.</Typography>}
        {loading ? (
          <Box sx={{ pt: 1 }}>{[0, 1, 2].map((i) => <Skeleton key={i} height={40} />)}</Box>
        ) : failed ? (
          <ErrorState compact title="The vehicles could not be loaded" error={current.error ?? candidates.error}
                      onRetry={() => { current.refetch(); candidates.refetch() }} />
        ) : !candidates.data?.items.length ? (
          <Typography variant="body2" sx={{ py: 2 }}>No vehicle in use {group.site_id ? 'at this site' : ''} yet.</Typography>
        ) : (
          <List dense>
            {candidates.data.items.map((v) => (
              <ListItem key={v.id} disablePadding>
                <ListItemButton onClick={() => mayEdit && toggle(v.id)} disabled={!mayEdit}>
                  <ListItemIcon><Checkbox edge="start" checked={chosen.has(v.id)} tabIndex={-1} disableRipple
                                          slotProps={{ input: { 'aria-label': vehicleLabel(v) } }} /></ListItemIcon>
                  <ListItemText primary={vehicleLabel(v)} secondary={v.site_name ?? 'No site'} />
                </ListItemButton>
              </ListItem>
            ))}
          </List>
        )}
      </DialogContent>
      <DialogActions sx={{ gap: 1 }}>
        <SaveStatus state={save.isPending ? 'saving' : save.isError ? 'failed' : 'idle'} error={save.error} />
        <Button onClick={onClose}>{mayEdit ? 'Cancel' : 'Close'}</Button>
        {mayEdit && <Button variant="contained" disabled={save.isPending || loading || failed} onClick={() => save.mutate()}>Save</Button>}
      </DialogActions>
    </Dialog>
  )
}
