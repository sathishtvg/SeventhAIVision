/**
 * Archiving a vehicle: what the delete button does now.
 *
 * It names the vehicle - its registration number and its name - so the
 * person can see which one they are about to take off the lists, and asks
 * why. Archiving keeps everything recorded about the vehicle and can be
 * undone; the dialog says so, and says nothing is deleted.
 */
import { useState } from 'react'
import { Alert, Button, Dialog, DialogActions, DialogContent, DialogTitle, TextField, Typography } from '@mui/material'
import { useMutation } from '@tanstack/react-query'
import { SaveStatus } from '@/components/states'
import { archiveFleetVehicle, vehicleLabel, type FleetVehicle } from '@/api/fleet'

export function ArchiveVehicleDialog({ vehicle, onClose, onArchived }: {
  vehicle: Pick<FleetVehicle, 'id' | 'name' | 'plate_number'>
  onClose: () => void
  onArchived: (v: FleetVehicle) => void
}) {
  const [reason, setReason] = useState('')
  const archive = useMutation({ mutationFn: () => archiveFleetVehicle(vehicle.id, reason.trim()), onSuccess: onArchived })
  const ready = reason.trim().length >= 3
  return (
    <Dialog open onClose={onClose} maxWidth="sm" fullWidth aria-labelledby="archive-vehicle-title">
      <DialogTitle id="archive-vehicle-title">Archive {vehicleLabel(vehicle)}?</DialogTitle>
      <DialogContent>
        <Typography variant="body2" sx={{ mb: 2 }}>
          The vehicle leaves the lists and the map. Its journeys, positions, events and history of changes are kept,
          and it can be restored. Nothing is deleted.
        </Typography>
        <TextField label="Why is it archived?" required fullWidth multiline minRows={2} value={reason}
                   onChange={(e) => setReason(e.target.value)} slotProps={{ htmlInput: { maxLength: 500 } }}
                   helperText="Kept with the vehicle and in the audit log, e.g. Sold, written off, returned to lessor" />
        {archive.isError && <Alert severity="error" sx={{ mt: 2 }}>The vehicle was not archived.</Alert>}
      </DialogContent>
      <DialogActions sx={{ gap: 1 }}>
        <SaveStatus state={archive.isPending ? 'saving' : archive.isError ? 'failed' : 'idle'} error={archive.error}
                    savingText="Archiving…" />
        <Button onClick={onClose}>Cancel</Button>
        <Button color="warning" variant="contained" disabled={!ready || archive.isPending} onClick={() => archive.mutate()}>
          Archive
        </Button>
      </DialogActions>
    </Dialog>
  )
}
