import { useEffect, useMemo, useState } from 'react'
import {
  Alert, Button, Dialog, DialogActions, DialogContent, DialogTitle,
  MenuItem, TextField, Typography,
} from '@mui/material'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import Stack from '@/components/common/Stack'
import { createVisitor, sendVisitorQR } from '@/api/visitors'
import { getSites } from '@/api/sites'
import type { LabelVisitor } from '@/components/vms/VisitorLabelDialog'

interface Props {
  open: boolean
  onClose: () => void
  defaultSiteId?: string
  /** Lets the caller offer the pass straight away, same as Register does. */
  onCreated?: (v: LabelVisitor) => void
}

/**
 * Book a visitor in ahead of time, without leaving the gatehouse board.
 *
 * This used to navigate to the pre-registration page. That is the wrong shape
 * for the desk it happens at: a guard books tomorrow's contractor in the middle
 * of watching today's arrivals, and sending them to another screen loses the
 * board they are supposed to be watching — and their place in it.
 *
 * A pre-registration differs from a walk-up registration in one way that
 * matters: the visitor has NOT arrived. It deliberately does not set
 * arrived_at, so they stay off the on-site board until they actually turn up
 * and are checked in — by the entry LPR reading their plate, or by scanning
 * the pass emailed to them.
 */
export function PreRegisterDialog({ open, onClose, defaultSiteId, onCreated }: Props) {
  const qc = useQueryClient()
  const [siteId, setSiteId] = useState(defaultSiteId ?? '')
  const [fullName, setFullName] = useState('')
  const [company, setCompany] = useState('')
  const [email, setEmail] = useState('')
  const [plate, setPlate] = useState('')
  const [host, setHost] = useState('')
  const [purpose, setPurpose] = useState('')
  const [from, setFrom] = useState('')
  const [until, setUntil] = useState('')
  const [emailPass, setEmailPass] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const { data: sites = [] } = useQuery({ queryKey: ['sites'], queryFn: () => getSites() })
  const vmsSites = useMemo(() => sites.filter((s) => s.vms_enabled), [sites])

  useEffect(() => {
    if (!open) return
    setSiteId(defaultSiteId ?? '')
    setFullName(''); setCompany(''); setEmail(''); setPlate('')
    setHost(''); setPurpose(''); setUntil(''); setError(null); setEmailPass(true)
    // Defaults to tomorrow morning, which is what "pre-register" almost always
    // means at a gate. Local time, so it matches the clock on the wall.
    const d = new Date()
    d.setDate(d.getDate() + 1)
    d.setHours(9, 0, 0, 0)
    const pad = (n: number) => String(n).padStart(2, '0')
    setFrom(`${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`)
  }, [open, defaultSiteId])

  const { mutate: create, isPending } = useMutation({
    mutationFn: async () => {
      const created = await createVisitor({
        full_name: fullName.trim(),
        company: company.trim() || undefined,
        visitor_email: email.trim() || undefined,
        vehicle_plate: plate.trim() ? plate.trim().toUpperCase() : undefined,
        host_name: host.trim() || undefined,
        purpose: purpose.trim() || undefined,
        site_id: siteId || undefined,
        expected_from: from ? new Date(from).toISOString() : undefined,
        expected_until: until ? new Date(until).toISOString() : undefined,
      })
      // Best effort: the booking is the point, and a mail server that is down
      // must not lose it. The guard can resend or print the pass instead.
      if (emailPass && email.trim() && created?.id) {
        try { await sendVisitorQR(created.id) } catch { /* pass can be reprinted */ }
      }
      return created
    },
    onSuccess: (created) => {
      void qc.invalidateQueries({ queryKey: ['visitors'] })
      void qc.invalidateQueries({ queryKey: ['vms-onsite'] })
      const site = vmsSites.find((s) => s.id === siteId)
      onClose()
      if (created?.id) {
        onCreated?.({
          id: created.id,
          full_name: fullName.trim(),
          company: company.trim() || null,
          site_name: site?.name ?? null,
          visit_type_label: 'Pre-registered',
          arrived_at: null,
        })
      }
    },
    onError: (err: unknown) => {
      const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
      setError(typeof detail === 'string' ? detail : 'Could not pre-register this visitor.')
    },
  })

  const canSubmit = fullName.trim().length > 0 && !!from && !isPending

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>Pre-register a visitor</DialogTitle>
      <DialogContent dividers>
        <Stack direction="column" spacing={2} sx={{ pt: 1 }}>
          {error && <Alert severity="error" onClose={() => setError(null)}>{error}</Alert>}
          <Typography variant="body2" color="text.secondary">
            Books a visit for later. They stay off the on-site board until they
            arrive and are checked in.
          </Typography>

          <TextField
            select size="small" fullWidth label="Site"
            value={siteId} onChange={(e) => setSiteId(e.target.value)}
          >
            <MenuItem value="">Not set</MenuItem>
            {vmsSites.map((s) => <MenuItem key={s.id} value={s.id}>{s.name}</MenuItem>)}
          </TextField>

          <TextField
            size="small" fullWidth required label="Visitor name"
            value={fullName} onChange={(e) => setFullName(e.target.value)}
          />
          <Stack direction="row" spacing={2}>
            <TextField size="small" fullWidth label="Company"
              value={company} onChange={(e) => setCompany(e.target.value)} />
            <TextField size="small" fullWidth label="Email" type="email"
              value={email} onChange={(e) => setEmail(e.target.value)}
              helperText="Used to send their pass" />
          </Stack>
          <Stack direction="row" spacing={2}>
            <TextField size="small" fullWidth label="Vehicle plate"
              value={plate} onChange={(e) => setPlate(e.target.value)}
              helperText="Lets the entry camera check them in automatically"
              slotProps={{ htmlInput: { style: { textTransform: 'uppercase', letterSpacing: 1 } } }} />
            <TextField size="small" fullWidth label="Host"
              value={host} onChange={(e) => setHost(e.target.value)} />
          </Stack>
          <Stack direction="row" spacing={2}>
            <TextField
              size="small" fullWidth required type="datetime-local" label="Expected from"
              value={from} onChange={(e) => setFrom(e.target.value)}
              slotProps={{ inputLabel: { shrink: true } }}
            />
            <TextField
              size="small" fullWidth type="datetime-local" label="Expected until"
              value={until} onChange={(e) => setUntil(e.target.value)}
              slotProps={{ inputLabel: { shrink: true } }}
            />
          </Stack>
          <TextField
            size="small" fullWidth label="Purpose" multiline minRows={2}
            value={purpose} onChange={(e) => setPurpose(e.target.value)}
          />
          <TextField
            select size="small" fullWidth label="Send the pass by email"
            value={emailPass ? 'yes' : 'no'}
            onChange={(e) => setEmailPass(e.target.value === 'yes')}
            helperText={email.trim() ? undefined : 'Needs an email address'}
            disabled={!email.trim()}
          >
            <MenuItem value="yes">Yes, email it now</MenuItem>
            <MenuItem value="no">No, I will print it</MenuItem>
          </TextField>
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" disabled={!canSubmit} onClick={() => create()}>
          {isPending ? 'Booking…' : 'Pre-register'}
        </Button>
      </DialogActions>
    </Dialog>
  )
}
