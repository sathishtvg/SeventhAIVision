/**
 * Evidence signatures on the evidence screens (Phase 7 of the known limits, 5 November 2026).
 *
 *   SealSignatureLine   a sealed package's signature: signed and by which key, not signed and why, or sealed before
 *                       signing began
 *   CheckExportButton   upload an export to be checked; the answer in plain words, line by line, and this
 *                       installation's key fingerprints to compare
 *
 * Every word of a check is the server's. Nothing of the upload is kept.
 */
import { useState } from 'react'
import {
  Alert, Box, Button, Dialog, DialogActions, DialogContent, DialogTitle, Skeleton, Typography,
} from '@mui/material'
import { useMutation, useQuery } from '@tanstack/react-query'
import Stack from '@/components/common/Stack'
import { checkExport, getSigningKeys } from '@/api/evidenceSignatures'
import type { CheckLine, SealSignature } from '@/api/evidenceSignatures'
import { apiError } from '@/api/securityAssets'
import { fmt } from './evidenceFormat'
import { ErrorState } from '@/components/states'

// A fingerprint wraps between its groups of four, never inside one.
const mono = { fontFamily: 'monospace', overflowWrap: 'break-word' } as const

/** A sealed package's signature, as its page says it. */
export function SealSignatureLine({ signature }: { signature: SealSignature | null | undefined }) {
  if (!signature) return null
  const severity = signature.state === 'SIGNED' ? 'success' : signature.state === 'NOT_SIGNED' ? 'warning' : 'info'
  return (
    <Alert severity={severity} sx={{ mt: 1 }} data-testid="seal-signature">
      {signature.words}{signature.signed_at && signature.state === 'SIGNED' ? `, ${fmt(signature.signed_at)}` : ''}.
      {signature.key_spaced && (
        <Typography component="span" variant="body2" sx={{ display: 'block', ...mono }}>
          Key {signature.key_spaced}</Typography>)}
    </Alert>
  )
}

function Line({ line }: { line: CheckLine }) {
  const mark = line.ok === true ? '✓' : line.ok === false ? '✗' : '–'
  const colour = line.ok === true ? 'success.main' : line.ok === false ? 'error.main' : 'text.secondary'
  return (
    <Stack direction="row" sx={{ gap: 1, alignItems: 'flex-start' }} data-testid="check-line">
      <Typography component="span" sx={{ color: colour, fontWeight: 700, minWidth: 16 }} aria-hidden>{mark}</Typography>
      <Box>
        <Typography variant="body2">
          <Box component="span" sx={{ position: 'absolute', left: -9999 }}>
            {line.ok === true ? 'Yes: ' : line.ok === false ? 'No: ' : 'Not said: '}</Box>
          {line.what}</Typography>
        {line.detail && <Typography variant="caption" color="text.secondary">{line.detail}</Typography>}
      </Box>
    </Stack>
  )
}

export function CheckExportButton() {
  const [open, setOpen] = useState(false)
  return (
    <>
      <Button variant="outlined" onClick={() => setOpen(true)}>Check an export</Button>
      {open && <CheckExportDialog onClose={() => setOpen(false)} />}
    </>
  )
}

function CheckExportDialog({ onClose }: { onClose: () => void }) {
  const [file, setFile] = useState<File | null>(null)
  const check = useMutation({ mutationFn: (f: File) => checkExport(f) })
  const keys = useQuery({ queryKey: ['evidence-signing-keys'], queryFn: getSigningKeys })
  const result = check.data
  return (
    <Dialog open onClose={onClose} fullWidth maxWidth="sm">
      <DialogTitle>Check an export</DialogTitle>
      <DialogContent>
        <Stack sx={{ gap: 1.5, mt: 0.5 }}>
          <Typography variant="body2" color="text.secondary">
            Choose an export ZIP as the platform made it. It is checked and then deleted; nothing of it is kept.
          </Typography>
          <Stack direction="row" sx={{ gap: 1, alignItems: 'center', flexWrap: 'wrap' }}>
            <Button variant="outlined" component="label">
              Choose a ZIP
              <input hidden type="file" accept=".zip,application/zip" data-testid="export-file"
                     onChange={(e) => { setFile(e.target.files?.[0] ?? null); check.reset() }} />
            </Button>
            <Typography variant="body2" sx={{ wordBreak: 'break-all' }}>{file?.name ?? 'No file chosen'}</Typography>
          </Stack>
          {check.isPending && <Skeleton height={80} />}
          {check.isError && <Alert severity="error">{apiError(check.error)}</Alert>}
          {result && (
            <Box data-testid="check-result">
              <Alert severity={result.genuine ? 'success' : 'error'} sx={{ mb: 1 }}>
                {result.genuine ? 'Genuine: the platform signed this export, and every file in it is the one signed.'
                  : result.signed ? 'Not shown to be genuine. See what did not hold below.'
                    : 'This export is not signed, so nothing shows it came from the platform unaltered.'}
                {result.package_number && (
                  <Typography component="span" variant="body2" sx={{ display: 'block' }}>
                    {result.package_number}, exported {result.exported_at ? fmt(result.exported_at) : '—'} by{' '}
                    {result.exported_by ?? '—'}</Typography>)}
                {result.key_spaced && (
                  <Typography component="span" variant="body2" sx={{ display: 'block', ...mono }}>
                    Key {result.key_spaced}</Typography>)}
              </Alert>
              <Stack sx={{ gap: 0.75 }}>
                {[...result.checks, ...result.platform].map((line) => <Line key={line.what} line={line} />)}
              </Stack>
            </Box>)}
          <Box data-testid="signing-keys">
            <Typography variant="subtitle2" sx={{ fontWeight: 700 }}>This installation's keys</Typography>
            {keys.isLoading && <Skeleton height={40} />}
            {keys.isError && <ErrorState compact error={keys.error} onRetry={keys.refetch} />}
            {keys.data && !keys.data.keys.length && (
              <Typography variant="body2" color="text.secondary">
                None yet: a key is made the first time evidence is sealed or exported.</Typography>)}
            {keys.data?.keys.map((k) => (
              <Typography key={k.fingerprint} variant="body2" sx={mono}>
                {k.spaced}
                <Typography component="span" variant="caption" color="text.secondary" sx={{ fontFamily: 'inherit', ml: 1 }}>
                  {k.in_use ? `in use since ${fmt(k.first_used_at)}`
                    : `used ${fmt(k.first_used_at)} to ${fmt(k.retired_at)}`}</Typography>
              </Typography>))}
            {keys.data?.not_signing_because && (
              <Alert severity="warning" sx={{ mt: 1 }}>Evidence is not being signed: {keys.data.not_signing_because}.</Alert>)}
            {keys.data && <Typography variant="caption" color="text.secondary">{keys.data.note}</Typography>}
          </Box>
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Close</Button>
        <Button variant="contained" disabled={!file || check.isPending} onClick={() => file && check.mutate(file)}>
          Check it</Button>
      </DialogActions>
    </Dialog>
  )
}
