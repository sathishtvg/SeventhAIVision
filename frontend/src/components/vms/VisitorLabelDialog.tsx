import { useEffect, useState } from 'react'
import {
  Alert, Box, Button, CircularProgress, Dialog, DialogActions, DialogContent,
  DialogTitle, Typography,
} from '@mui/material'
import PrintIcon from '@mui/icons-material/Print'
import { fetchVisitorQRDataUrl } from '@/api/visitors'

export interface LabelVisitor {
  id: string
  full_name: string
  company?: string | null
  site_name?: string | null
  visit_type_label?: string | null
  arrived_at?: string | null
}

interface Props {
  visitor: LabelVisitor | null
  onClose: () => void
}

/**
 * The visitor's pass: who they are and a QR code that ends their visit.
 *
 * Printing is deliberately a button, not automatic. Most gatehouses hand out a
 * badge; some scan the visitor's own phone and print nothing. Firing a print
 * dialog on every registration would put a wasted sheet through the printer
 * for every one of those, so the operator decides.
 *
 * The QR is inlined as a data: URL rather than linked. The PNG route requires
 * visitor:read, and neither an <img src> nor a print window opened with
 * document.write carries the Authorization header — both would render a broken
 * image, and a badge with no code on it is worse than no badge.
 *
 * The scanned value is the visitor's qr_token, which /visitors/qr-scan already
 * treats as a toggle: first scan checks in, a scan while already on site checks
 * out. So this same label is what the guard scans on the way out.
 */
export function VisitorLabelDialog({ visitor, onClose }: Props) {
  const [qr, setQr] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setQr(null); setError(null)
    if (!visitor) return
    fetchVisitorQRDataUrl(visitor.id)
      .then((d) => { if (!cancelled) setQr(d) })
      .catch(() => { if (!cancelled) setError('Could not load the QR code for this pass.') })
    return () => { cancelled = true }
  }, [visitor])

  function print() {
    if (!visitor || !qr) return
    const w = window.open('', '_blank', 'width=420,height=560')
    if (!w) {
      setError('The browser blocked the print window. Allow pop-ups for this site and try again.')
      return
    }
    const esc = (s: string) =>
      s.replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]!))
    const line = (label: string, value?: string | null) =>
      value ? `<div class="row"><span>${esc(label)}</span><b>${esc(value)}</b></div>` : ''

    // Sized for a 62mm label roll, the common gatehouse printer, and falls back
    // sanely on A4. Written as a standalone document so no app CSS reaches it.
    w.document.write(`<!doctype html><html><head><meta charset="utf-8">
<title>Visitor pass — ${esc(visitor.full_name)}</title>
<style>
  @page { size: 62mm auto; margin: 4mm; }
  body { font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
         margin: 0; color: #000; text-align: center; }
  h1 { font-size: 15pt; margin: 0 0 2mm; letter-spacing: -0.2pt; }
  .sub { font-size: 9pt; color: #333; margin-bottom: 3mm; }
  img { width: 42mm; height: 42mm; image-rendering: pixelated; }
  .row { display: flex; justify-content: space-between; font-size: 8pt;
         border-top: 0.4pt solid #bbb; padding: 1.2mm 0; text-align: left; }
  .row span { color: #555; }
  .foot { margin-top: 2mm; font-size: 7pt; color: #555; }
</style></head><body>
  <h1>${esc(visitor.full_name)}</h1>
  <div class="sub">${esc(visitor.company ?? 'Visitor')}</div>
  <img src="${qr}" alt="">
  ${line('Site', visitor.site_name)}
  ${line('Type', visitor.visit_type_label)}
  ${line('Arrived', visitor.arrived_at ? new Date(visitor.arrived_at).toLocaleString() : null)}
  <div class="foot">Scan this code on the way out to check out.</div>
</body></html>`)
    w.document.close()
    w.focus()
    w.print()
  }

  return (
    <Dialog open={!!visitor} onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle>Visitor pass</DialogTitle>
      <DialogContent dividers>
        {error && <Alert severity="warning" sx={{ mb: 2 }}>{error}</Alert>}
        {visitor && (
          <Box sx={{ textAlign: 'center' }}>
            <Typography variant="h6" sx={{ fontWeight: 700 }}>{visitor.full_name}</Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
              {visitor.company || 'Visitor'}
              {visitor.site_name ? ` · ${visitor.site_name}` : ''}
            </Typography>
            <Box sx={{ minHeight: 200, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              {qr
                ? <Box component="img" src={qr} alt="Visitor QR code"
                    sx={{ width: 200, height: 200, background: '#fff', p: 1, borderRadius: 1 }} />
                : !error && <CircularProgress size={28} />}
            </Box>
            <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1.5 }}>
              Scanning this code on the way out checks the visitor out.
            </Typography>
          </Box>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Close</Button>
        <Button variant="contained" startIcon={<PrintIcon />} disabled={!qr} onClick={print}>
          Print label
        </Button>
      </DialogActions>
    </Dialog>
  )
}
