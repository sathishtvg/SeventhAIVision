/**
 * Reading a new staff member's IC for the assistant (backend/app/services/ai_ic.py).
 *
 * The photos go to our own server, are read there and are not kept anywhere. What was read is shown here for the
 * person to check and correct; only then is it given to the assistant, with the NRIC/FIN shortened to its last
 * four characters. The full number is filled in on the server when the person confirms the change that saves it.
 */
import { useState } from 'react'
import {
  Alert, Box, Button, Chip, Dialog, DialogActions, DialogContent, DialogTitle, TextField, Typography,
} from '@mui/material'
import Stack from '@/components/common/Stack'
import { correctIc, readIc } from '@/api/aiAssistant'
import type { IcFields, IcReading } from '@/api/aiAssistant'
import { IC_LABELS, IC_ORDER as ORDER, changed } from './icScanFormat'
import { apiError } from '@/api/platformDroneLicense'

function PhotoPicker({ label, file, onPick }: { label: string; file: File | null; onPick: (f: File | null) => void }) {
  return (
    <Button component="label" variant={file ? 'contained' : 'outlined'} sx={{ flex: 1, textTransform: 'none' }}>
      {file ? `${label}: ${file.name}` : label}
      <input hidden type="file" accept="image/*" capture="environment" aria-label={label}
             onChange={(e) => onPick(e.target.files?.[0] ?? null)} />
    </Button>
  )
}

export default function IcScanDialog({ open, onClose, onUse }: {
  open: boolean
  onClose: () => void
  /** Called with the message for the assistant once the person has checked what was read. */
  onUse: (message: string) => void
}) {
  const [front, setFront] = useState<File | null>(null)
  const [back, setBack] = useState<File | null>(null)
  const [reading, setReading] = useState<IcReading | null>(null)
  const [fields, setFields] = useState<IcFields>({})
  const [working, setWorking] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const close = () => {
    // Nothing of the card is kept in the page once the dialog closes.
    setFront(null); setBack(null); setReading(null); setFields({}); setError(null)
    onClose()
  }

  const read = async () => {
    if (!front) return
    setWorking(true)
    setError(null)
    try {
      const r = await readIc(front, back)
      setReading(r)
      setFields(r.fields)
      setFront(null)
      setBack(null)
    } catch (e) {
      setError(apiError(e))
    } finally {
      setWorking(false)
    }
  }

  const use = async () => {
    if (!reading) return
    setWorking(true)
    setError(null)
    try {
      const final = changed(reading.fields, fields) ? await correctIc(reading.draft_id, fields) : reading
      onUse(final.message)
      close()
    } catch (e) {
      setError(apiError(e))
    } finally {
      setWorking(false)
    }
  }

  return (
    <Dialog open={open} onClose={close} fullWidth maxWidth="sm">
      <DialogTitle>Scan a new staff member&apos;s IC</DialogTitle>
      <DialogContent>
        {!reading ? (
          <>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
              Take or choose a photo of the front of the NRIC or FIN card, and the back for the address. The photos
              are read on your own server and are not kept anywhere. You check every detail before it is used.
            </Typography>
            <Stack direction={{ xs: 'column', sm: 'row' }} sx={{ gap: 1 }}>
              <PhotoPicker label="Front of the card" file={front} onPick={setFront} />
              <PhotoPicker label="Back of the card" file={back} onPick={setBack} />
            </Stack>
          </>
        ) : (
          <>
            {reading.check_letter_ok === true && (
              <Chip color="success" size="small" label="The number's check letter is correct" sx={{ mb: 1.5 }} />
            )}
            {reading.check_letter_ok === false && (
              <Alert severity="warning" sx={{ mb: 1.5 }}>
                The number&apos;s check letter does not match. Look at the card again and correct the number.
              </Alert>
            )}
            {reading.unread.length > 0 && (
              <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5 }}>
                Not read from the card: {reading.unread.map((k) => IC_LABELS[k]).join(', ')}. Fill them in here, or the
                assistant will ask.
              </Typography>
            )}
            <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', sm: '1fr 1fr' }, gap: 1.5 }}>
              {ORDER.map((k) => (
                <TextField key={k} size="small" label={IC_LABELS[k]} value={fields[k] ?? ''}
                           multiline={k === 'address'} sx={k === 'address' ? { gridColumn: '1 / -1' } : undefined}
                           onChange={(e) => setFields((f) => ({ ...f, [k]: e.target.value }))} />
              ))}
            </Box>
            <Typography variant="caption" color="text.secondary" component="div" sx={{ mt: 1.5 }}>
              The assistant is given the NRIC/FIN&apos;s last four characters only. The full number is saved when you
              confirm the change that creates the staff member.
            </Typography>
          </>
        )}
        {error && <Alert severity="error" sx={{ mt: 1.5 }}>{error}</Alert>}
      </DialogContent>
      <DialogActions>
        <Button onClick={close} disabled={working}>Cancel</Button>
        {!reading ? (
          <Button variant="contained" onClick={() => void read()} disabled={!front || working}>
            {working ? 'Reading…' : 'Read the card'}
          </Button>
        ) : (
          <Button variant="contained" onClick={() => void use()} disabled={working}>Use these details</Button>
        )}
      </DialogActions>
    </Dialog>
  )
}
