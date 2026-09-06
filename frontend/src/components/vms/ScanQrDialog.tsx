import { useEffect, useRef, useState } from 'react'
import {
  Alert, Box, Button, Dialog, DialogActions, DialogContent, DialogTitle,
  TextField, Typography,
} from '@mui/material'
import QrCodeScannerIcon from '@mui/icons-material/QrCodeScanner'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { qrScanCheckin } from '@/api/visitors'

interface Props {
  open: boolean
  onClose: () => void
}

/**
 * Scan a visitor pass to check them out.
 *
 * A handheld barcode scanner is a keyboard: it types the code and presses
 * Enter. So this is a focused text field, not a camera — it works with the
 * hardware every gatehouse already has, and a guard can also type a code by
 * hand when a label is damaged.
 *
 * /visitors/qr-scan is a toggle rather than two endpoints: a scan for someone
 * already on site records a departure, otherwise an arrival. That is why one
 * label serves both directions and the guard does not have to pick a mode
 * before scanning — picking wrongly under pressure is how the wrong event
 * gets written.
 *
 * The field stays open and clears itself after each scan, because visitors
 * leave in groups and closing between them would make the common case the slow
 * one.
 */
export function ScanQrDialog({ open, onClose }: Props) {
  const qc = useQueryClient()
  const inputRef = useRef<HTMLInputElement>(null)
  const [value, setValue] = useState('')
  const [last, setLast] = useState<{ ok: boolean; text: string } | null>(null)

  useEffect(() => {
    if (open) {
      setValue(''); setLast(null)
      // A scanner types into whatever has focus, so the field must take it
      // before the guard scans, not after they notice nothing happened.
      const id = setTimeout(() => inputRef.current?.focus(), 80)
      return () => clearTimeout(id)
    }
  }, [open])

  const { mutate: scan, isPending } = useMutation({
    mutationFn: (token: string) => qrScanCheckin({ qr_token: token }),
    onSuccess: (res: unknown) => {
      const r = res as { event_type?: string; visitor?: { full_name?: string } }
      const name = r.visitor?.full_name ?? 'Visitor'
      setLast({
        ok: true,
        text: r.event_type === 'departure'
          ? `${name} checked out.`
          : `${name} checked in.`,
      })
      void qc.invalidateQueries({ queryKey: ['vms-onsite'] })
      void qc.invalidateQueries({ queryKey: ['visitors'] })
      setValue('')
      inputRef.current?.focus()
    },
    onError: (err: unknown) => {
      const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
      setLast({ ok: false, text: typeof detail === 'string' ? detail : 'That code was not recognised.' })
      setValue('')
      inputRef.current?.focus()
    },
  })

  function submit() {
    const token = value.trim()
    if (token) scan(token)
  }

  return (
    <Dialog open={open} onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
        <QrCodeScannerIcon /> Scan a visitor pass
      </DialogTitle>
      <DialogContent dividers>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
          Scan the code on the visitor's pass. Someone already on site is checked
          out; anyone else is checked in.
        </Typography>
        <TextField
          inputRef={inputRef}
          autoFocus
          fullWidth
          size="small"
          label="Scan or type the code"
          value={value}
          disabled={isPending}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); submit() } }}
        />
        {last && (
          <Box sx={{ mt: 2 }}>
            <Alert severity={last.ok ? 'success' : 'error'}>{last.text}</Alert>
          </Box>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Done</Button>
        <Button variant="contained" disabled={!value.trim() || isPending} onClick={submit}>
          {isPending ? 'Checking…' : 'Check'}
        </Button>
      </DialogActions>
    </Dialog>
  )
}
