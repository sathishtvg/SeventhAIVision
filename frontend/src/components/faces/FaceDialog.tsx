/**
 * One person's registered face, for check-in: see it, register or replace it from a photo, or delete it.
 *
 * Opened from the Users page, only for the people the signed-in person may change (the server's list):
 * a supervisor changes a guard's face, an admin a supervisor's. The person's agreement is confirmed with a
 * tick, and is recorded as a consent with the photo. Design: docs/plans/2026-10-23-staff-face-registration-design.md.
 */
import { useState } from 'react'
import {
  Alert, Avatar, Box, Button, Checkbox, Dialog, DialogActions, DialogContent, DialogTitle, FormControlLabel,
  Typography,
} from '@mui/material'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { deleteFace, facePhotoUrl, putFace, type StaffFace } from '@/api/faces'
import { errorText } from '@/components/states'
import { useAuthStore } from '@/store/auth'

export function FaceDialog({ face, onClose }: { face: StaffFace; onClose: () => void }) {
  const token = useAuthStore((s) => s.accessToken)
  const queryClient = useQueryClient()
  const [file, setFile] = useState<File | null>(null)
  const [agreed, setAgreed] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [version, setVersion] = useState(0)
  const name = face.full_name ?? face.email

  const done = () => {
    queryClient.invalidateQueries({ queryKey: ['staff-faces'] })
    setFile(null); setAgreed(false); setConfirmDelete(false)
    setVersion((v) => v + 1)
  }
  const save = useMutation({ mutationFn: () => putFace(face.user_id, file!, agreed), onSuccess: done })
  const remove = useMutation({ mutationFn: () => deleteFace(face.user_id), onSuccess: done })

  const photo = face.registered ? facePhotoUrl(face.user_id, token) : null
  const failure = save.error ?? remove.error

  return (
    <Dialog open onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle>Face for check-in: {name}</DialogTitle>
      <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 2 }}>
        <Box sx={{ display: 'flex', gap: 2, alignItems: 'center' }}>
          <Avatar
            variant="rounded"
            // An image that cannot be loaded falls back to the initial (MUI's Avatar does that itself).
            src={photo ? `${photo}&v=${version}` : undefined}
            sx={{ width: 96, height: 96 }}
          >
            {name.slice(0, 1).toUpperCase()}
          </Avatar>
          <Box>
            <Typography variant="body2" sx={{ fontWeight: 700 }}>
              {face.registered ? 'A face is on file' : 'No face on file'}
            </Typography>
            <Typography variant="caption" color="text.secondary">
              {face.registered
                ? `${face.source === 'UPLOAD' ? 'From a photo' : 'Selfie'}${face.registered_by_name ? `, by ${face.registered_by_name}` : ''}${face.updated_at ? `, ${new Date(face.updated_at).toLocaleDateString()}` : ''}`
                : 'Check-ins are recorded as "No face on file" until one is registered.'}
            </Typography>
          </Box>
        </Box>

        <Box>
          <Button component="label" variant="outlined" size="small" disabled={save.isPending}>
            {file ? file.name : face.registered ? 'Choose a new photo' : 'Choose a photo'}
            <input hidden type="file" accept="image/jpeg,image/png"
                   onChange={(e) => { setFile(e.target.files?.[0] ?? null); e.target.value = '' }} />
          </Button>
          <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 0.5 }}>
            A clear photo of this person alone, facing the camera.
          </Typography>
        </Box>

        <FormControlLabel
          control={<Checkbox checked={agreed} onChange={(e) => setAgreed(e.target.checked)} />}
          label={<Typography variant="body2">{name} has agreed to their face being used to confirm them at check-in.</Typography>}
        />

        {failure && <Alert severity="error">{errorText(failure)}</Alert>}
        {save.isSuccess && !file && <Alert severity="success">Saved.</Alert>}
        {remove.isSuccess && <Alert severity="success">Deleted.</Alert>}
      </DialogContent>
      <DialogActions>
        {face.registered && (confirmDelete ? (
          <Button color="error" onClick={() => remove.mutate()} disabled={remove.isPending}>
            {remove.isPending ? 'Deleting...' : 'Yes, delete the face'}
          </Button>
        ) : (
          <Button color="error" onClick={() => setConfirmDelete(true)}>Delete</Button>
        ))}
        <Box sx={{ flex: 1 }} />
        <Button onClick={onClose}>Close</Button>
        <Button variant="contained" onClick={() => save.mutate()} disabled={!file || !agreed || save.isPending}>
          {save.isPending ? 'Checking the photo...' : face.registered ? 'Replace' : 'Register'}
        </Button>
      </DialogActions>
    </Dialog>
  )
}
