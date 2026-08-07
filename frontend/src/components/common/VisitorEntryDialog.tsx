/**
 * Visitor entry prompt — opens on the operator's current screen the moment
 * the entry LPR camera reads a plate that needs a human.
 *
 * Mounted once in AppShell rather than on a page, because the requirement is
 * that it appears on whatever the operator is already watching (the wall, the
 * command centre) — not on a page they'd have to navigate to first.
 *
 * The visitor record already exists by the time this opens: the backend
 * created it when the plate was read, so the free-parking clock is already
 * running. This form fills in WHO the vehicle is. That is why dismissing is
 * allowed and non-destructive — the arrival is recorded either way, and an
 * unidentified vehicle still shows up on the on-site list and the overstay
 * sweep. Dismissing loses nothing except the operator's input.
 */
import { useEffect, useMemo, useState } from 'react'
import {
  Dialog, DialogTitle, DialogContent, DialogActions, TextField, Button,
  Typography, Box, Chip, Alert, MenuItem, Checkbox, FormControlLabel,
  Select, InputLabel, FormControl, OutlinedInput, IconButton, Tooltip,
} from '@mui/material'
import Stack from '@/components/common/Stack'
import CloseIcon from '@mui/icons-material/Close'
import DirectionsCarIcon from '@mui/icons-material/DirectionsCar'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useVisitorEntryStore } from '@/store/visitorEntry'
import { completeVisitorEntry, type VisitorFormField } from '@/api/vms'
import { getEvidenceForDetection, evidenceImageUrl } from '@/api/evidence'
import { useAuthStore } from '@/store/auth'
import { usePermission } from '@/hooks/usePermission'

/** Decisions that mean the vehicle was let in vs. held. Purely presentational
 * — the gate has already acted by the time this dialog opens. */
const DECISION_META: Record<string, { label: string; color: string }> = {
  auto_open: { label: 'Gate opened', color: '#00E396' },
  auto_allow: { label: 'Permitted', color: '#00E396' },
  require_operator: { label: 'Held for operator', color: '#FF9800' },
  block: { label: 'Refused', color: '#FF4560' },
  alarm: { label: 'Refused — alarm', color: '#FF4560' },
}

function emptyValueFor(f: VisitorFormField): unknown {
  if (f.field_type === 'multiselect') return []
  if (f.field_type === 'checkbox') return false
  return ''
}

function DynamicField({
  field, value, onChange,
}: {
  field: VisitorFormField
  value: unknown
  onChange: (v: unknown) => void
}) {
  const common = {
    fullWidth: true,
    label: field.label,
    required: field.is_required,
    helperText: field.help_text ?? undefined,
    placeholder: field.placeholder ?? undefined,
  }

  switch (field.field_type) {
    case 'select':
      return (
        <FormControl fullWidth required={field.is_required}>
          <InputLabel>{field.label}</InputLabel>
          <Select
            label={field.label}
            value={(value as string) ?? ''}
            onChange={(e) => onChange(e.target.value)}
          >
            {field.options.map((o) => (
              <MenuItem key={String(o)} value={String(o)}>{String(o)}</MenuItem>
            ))}
          </Select>
        </FormControl>
      )
    case 'multiselect':
      return (
        <FormControl fullWidth required={field.is_required}>
          <InputLabel>{field.label}</InputLabel>
          <Select
            multiple
            label={field.label}
            value={(value as string[]) ?? []}
            input={<OutlinedInput label={field.label} />}
            onChange={(e) =>
              onChange(typeof e.target.value === 'string'
                ? e.target.value.split(',')
                : e.target.value)
            }
            renderValue={(sel) => (sel as string[]).join(', ')}
          >
            {field.options.map((o) => (
              <MenuItem key={String(o)} value={String(o)}>{String(o)}</MenuItem>
            ))}
          </Select>
        </FormControl>
      )
    case 'checkbox':
      return (
        <FormControlLabel
          control={
            <Checkbox checked={!!value} onChange={(e) => onChange(e.target.checked)} />
          }
          label={field.label + (field.is_required ? ' *' : '')}
        />
      )
    case 'number':
      return (
        <TextField
          {...common}
          type="number"
          value={value ?? ''}
          // Sent as a real number — the backend rejects a string for a
          // 'number' field, and an empty box must stay absent, not become 0.
          onChange={(e) => onChange(e.target.value === '' ? '' : Number(e.target.value))}
        />
      )
    case 'date':
      return (
        <TextField {...common} type="date" InputLabelProps={{ shrink: true }}
          value={(value as string) ?? ''} onChange={(e) => onChange(e.target.value)} />
      )
    case 'textarea':
      return (
        <TextField {...common} multiline minRows={2}
          value={(value as string) ?? ''} onChange={(e) => onChange(e.target.value)} />
      )
    default:
      return (
        <TextField
          {...common}
          type={field.field_type === 'email' ? 'email' : field.field_type === 'phone' ? 'tel' : 'text'}
          value={(value as string) ?? ''}
          onChange={(e) => onChange(e.target.value)}
        />
      )
  }
}

export function VisitorEntryDialog() {
  const queue = useVisitorEntryStore((s) => s.queue)
  const resolveCurrent = useVisitorEntryStore((s) => s.resolveCurrent)
  const prompt = queue[0]
  const canCheckIn = usePermission('visitor:checkin')
  const qc = useQueryClient()

  const [fullName, setFullName] = useState('')
  const [company, setCompany] = useState('')
  const [idNumber, setIdNumber] = useState('')
  const [hostName, setHostName] = useState('')
  const [purpose, setPurpose] = useState('')
  const [custom, setCustom] = useState<Record<string, unknown>>({})
  const [error, setError] = useState<string | null>(null)

  // Reset per prompt. Keyed on visitor_id so the next vehicle in the queue
  // never inherits the previous one's typed values — the same stale-form class
  // of bug already fixed in UserFormDialog.
  useEffect(() => {
    if (!prompt) return
    setFullName(prompt.needs_details ? '' : prompt.display_name)
    setCompany(prompt.company ?? '')
    setIdNumber('')
    setHostName('')
    setPurpose('')
    setCustom(
      Object.fromEntries(prompt.form_fields.map((f) => [f.field_key, emptyValueFor(f)])),
    )
    setError(null)
  }, [prompt?.visitor_id]) // eslint-disable-line react-hooks/exhaustive-deps

  const submit = useMutation({
    mutationFn: () =>
      completeVisitorEntry(prompt!.visitor_id, {
        full_name: fullName.trim(),
        company: company.trim() || null,
        id_number: idNumber.trim() || null,
        host_name: hostName.trim() || null,
        purpose: purpose.trim() || null,
        // Empty strings would fail a 'number' field's type check and add noise
        // to stored records, so unfilled optional fields are omitted entirely.
        custom_fields: Object.fromEntries(
          Object.entries(custom).filter(([, v]) =>
            v !== '' && v !== null && !(Array.isArray(v) && v.length === 0)),
        ),
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['vms-onsite'] })
      void qc.invalidateQueries({ queryKey: ['visitors'] })
      resolveCurrent()
    },
    onError: (e: any) => setError(e?.response?.data?.detail ?? 'Could not save visitor'),
  })

  const decision = useMemo(
    () => (prompt ? DECISION_META[prompt.decision] ?? { label: prompt.decision, color: '#7A8195' } : null),
    [prompt],
  )

  const authToken = useAuthStore((st) => st.accessToken)
  const { data: proof } = useQuery({
    queryKey: ['detection-evidence', prompt?.detection_id],
    queryFn: () => getEvidenceForDetection(prompt!.detection_id!),
    enabled: !!prompt?.detection_id,
    // The crop is written by the worker on a separate path from the prompt, so
    // on a fast gate it can arrive just after this opens. Retry briefly rather
    // than showing "no image" for a read that does have one.
    retry: 3,
    retryDelay: 800,
    staleTime: 60_000,
  })

  if (!prompt) return null

  return (
    <Dialog open maxWidth="sm" fullWidth onClose={() => { /* deliberate: needs an explicit choice */ }}>
      <DialogTitle sx={{ pb: 1 }}>
        <Stack direction="row" spacing={1} sx={{ alignItems: 'center' }}>
          <DirectionsCarIcon color="primary" />
          <Box sx={{ flex: 1 }}>
            <Typography variant="h6" component="div" sx={{ lineHeight: 1.2 }}>
              Vehicle at {prompt.site_name}
            </Typography>
            <Typography variant="caption" color="text.secondary">
              {prompt.reason}
            </Typography>
          </Box>
          {queue.length > 1 && (
            <Tooltip title="More vehicles are waiting to be identified">
              <Chip size="small" color="warning" label={`+${queue.length - 1} waiting`} />
            </Tooltip>
          )}
          <Tooltip title="Dismiss — the arrival stays recorded">
            <IconButton size="small" onClick={resolveCurrent}><CloseIcon /></IconButton>
          </Tooltip>
        </Stack>
      </DialogTitle>

      <DialogContent>
        <Stack spacing={2} sx={{ mt: 0.5 }}>
          <Stack direction="row" spacing={1} sx={{ alignItems: 'center', flexWrap: 'wrap' }}>
            <Chip
              label={prompt.plate_number}
              sx={{ fontWeight: 700, letterSpacing: 1, fontSize: '1rem', px: 0.5 }}
            />
            {/* The read, as a picture. The operator is being asked to confirm a
                vehicle's identity — without this they are confirming a text
                string against nothing. Crop preferred; the full frame is the
                fallback when no crop was captured (pre-0082 reads). */}
            {(proof?.plate_evidence_id || proof?.frame_evidence_id) && (
              <Tooltip title={proof.plate_evidence_id ? 'Plate as read by the camera' : 'Full frame — no plate crop captured'}>
                <Box
                  component="img"
                  src={
                    evidenceImageUrl(
                      (proof.plate_evidence_id ?? proof.frame_evidence_id)!,
                      authToken,
                      240,
                    ) ?? undefined
                  }
                  alt="Plate read"
                  sx={{
                    height: 44,
                    maxWidth: 160,
                    objectFit: 'contain',
                    borderRadius: 1,
                    border: '1px solid rgba(255,255,255,0.2)',
                    bgcolor: 'rgba(0,0,0,0.3)',
                  }}
                />
              </Tooltip>
            )}
            {decision && (
              <Chip
                size="small"
                label={decision.label}
                sx={{ bgcolor: `${decision.color}22`, color: decision.color, fontWeight: 600 }}
              />
            )}
            {prompt.category && <Chip size="small" variant="outlined" label={prompt.category} />}
            {prompt.free_parking_minutes != null && (
              <Chip size="small" variant="outlined"
                label={`${prompt.free_parking_minutes} min free parking`} />
            )}
          </Stack>

          {prompt.owner_name && (
            <Alert severity="info" sx={{ py: 0.5 }}>
              Registered to {prompt.owner_name}
              {prompt.company ? ` (${prompt.company})` : ''}
            </Alert>
          )}

          {!canCheckIn && (
            <Alert severity="warning">
              You don&apos;t have permission to check visitors in. The arrival is recorded; someone
              with visitor check-in rights needs to complete the details.
            </Alert>
          )}

          {error && <Alert severity="error" onClose={() => setError(null)}>{error}</Alert>}

          <TextField
            autoFocus required fullWidth label="Visitor name"
            value={fullName} onChange={(e) => setFullName(e.target.value)}
            disabled={!canCheckIn}
          />
          <Stack direction="row" spacing={2}>
            <TextField fullWidth label="Company" value={company}
              onChange={(e) => setCompany(e.target.value)} disabled={!canCheckIn} />
            <TextField fullWidth label="ID / NRIC" value={idNumber}
              onChange={(e) => setIdNumber(e.target.value)} disabled={!canCheckIn} />
          </Stack>
          <Stack direction="row" spacing={2}>
            <TextField fullWidth label="Host" value={hostName}
              onChange={(e) => setHostName(e.target.value)} disabled={!canCheckIn} />
            <TextField fullWidth label="Purpose" value={purpose}
              onChange={(e) => setPurpose(e.target.value)} disabled={!canCheckIn} />
          </Stack>

          {prompt.form_fields.length > 0 && (
            <>
              <Typography variant="caption" color="text.secondary">
                Additional details for this site
              </Typography>
              {prompt.form_fields.map((f) => (
                <Box key={f.id} sx={{ opacity: canCheckIn ? 1 : 0.6, pointerEvents: canCheckIn ? 'auto' : 'none' }}>
                  <DynamicField
                    field={f}
                    value={custom[f.field_key]}
                    onChange={(v) => setCustom((c) => ({ ...c, [f.field_key]: v }))}
                  />
                </Box>
              ))}
            </>
          )}
        </Stack>
      </DialogContent>

      <DialogActions>
        <Button onClick={resolveCurrent} color="inherit">Dismiss</Button>
        <Button
          variant="contained"
          disabled={!canCheckIn || !fullName.trim() || submit.isPending}
          onClick={() => submit.mutate()}
        >
          {submit.isPending ? 'Saving…' : 'Check In Visitor'}
        </Button>
      </DialogActions>
    </Dialog>
  )
}
