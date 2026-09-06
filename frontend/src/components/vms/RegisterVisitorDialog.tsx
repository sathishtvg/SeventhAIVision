import { useEffect, useMemo, useState } from 'react'
import {
  Alert, Box, Button, Checkbox, Dialog, DialogActions, DialogContent, DialogTitle,
  FormControlLabel, MenuItem, TextField, ToggleButton, ToggleButtonGroup, Typography,
} from '@mui/material'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import Stack from '@/components/common/Stack'
import {
  createManualEntry, listFormFields, VISIT_TYPES, VISIT_TYPE_LABELS,
  type VisitType, type VisitorFormField,
} from '@/api/vms'
import { getSites } from '@/api/sites'
import type { LabelVisitor } from '@/components/vms/VisitorLabelDialog'

/** Types that mean a vehicle came through the barrier. Only these show the
 *  plate field, and only these start a parking clock — a walk-in with a plate
 *  recorded against it would be metered for a car that is not there. */
const VEHICLE_TYPES: VisitType[] = ['vehicle', 'delivery', 'drop_off', 'pick_up']

interface Props {
  open: boolean
  onClose: () => void
  /** Pre-selects the site when the board is already filtered to one. */
  defaultSiteId?: string
  /** Called with the created visit so the caller can offer its pass. */
  onRegistered?: (v: LabelVisitor) => void
}

/**
 * Register anyone arriving at the gate — on foot or in a vehicle.
 *
 * The endpoint behind this has existed since the VMS shipped; nothing in the
 * UI ever called it, so a site without an entry LPR camera (which is every
 * site by default) had no way to record a visitor at all.
 *
 * The form is the site's own: whatever fields an admin configured under
 * Settings appear here, in their configured order, with their own required
 * flags. That is the point of the field registry — a gatehouse that must
 * capture an NRIC or a delivery docket number should not need a release.
 */
export function RegisterVisitorDialog({ open, onClose, defaultSiteId, onRegistered }: Props) {
  const qc = useQueryClient()
  const [siteId, setSiteId] = useState(defaultSiteId ?? '')
  const [visitType, setVisitType] = useState<VisitType>('walk_in')
  const [fullName, setFullName] = useState('')
  const [company, setCompany] = useState('')
  const [idNumber, setIdNumber] = useState('')
  const [purpose, setPurpose] = useState('')
  const [plate, setPlate] = useState('')
  const [custom, setCustom] = useState<Record<string, unknown>>({})
  const [error, setError] = useState<string | null>(null)

  const { data: sites = [] } = useQuery({ queryKey: ['sites'], queryFn: () => getSites() })
  const vmsSites = useMemo(() => sites.filter((s) => s.vms_enabled), [sites])

  // Only fetched once a site is chosen: the field set is per site, so asking
  // before then would render a form the operator has to re-fill.
  const { data: fields = [] } = useQuery({
    queryKey: ['vms-form-fields', siteId],
    queryFn: () => listFormFields(siteId),
    enabled: open && !!siteId,
  })
  const activeFields = useMemo(
    () => (fields as VisitorFormField[]).filter((f) => f.is_active).sort((a, b) => a.sort_order - b.sort_order),
    [fields],
  )

  useEffect(() => {
    if (open) {
      setSiteId(defaultSiteId ?? '')
      setVisitType('walk_in')
      setFullName(''); setCompany(''); setIdNumber(''); setPurpose(''); setPlate('')
      setCustom({}); setError(null)
    }
  }, [open, defaultSiteId])

  const showsPlate = VEHICLE_TYPES.includes(visitType)

  const missingRequired = activeFields
    .filter((f) => f.is_required)
    .filter((f) => {
      const v = custom[f.field_key]
      return v === undefined || v === '' || (Array.isArray(v) && v.length === 0)
    })

  const { mutate: register, isPending } = useMutation({
    mutationFn: () =>
      createManualEntry({
        site_id: siteId,
        visit_type: visitType,
        full_name: fullName.trim(),
        company: company.trim() || null,
        id_number: idNumber.trim() || null,
        purpose: purpose.trim() || null,
        // Sent only for vehicle-bearing visits, so the parking clock never
        // starts for someone on foot.
        vehicle_plate: showsPlate && plate.trim() ? plate.trim().toUpperCase() : null,
        custom_fields: custom,
      }),
    onSuccess: (created: unknown) => {
      void qc.invalidateQueries({ queryKey: ['vms-onsite'] })
      const c = created as { id?: string; arrived_at?: string }
      const site = vmsSites.find((s) => s.id === siteId)
      onClose()
      if (c?.id) {
        onRegistered?.({
          id: c.id,
          full_name: fullName.trim(),
          company: company.trim() || null,
          site_name: site?.name ?? null,
          visit_type_label: VISIT_TYPE_LABELS[visitType],
          arrived_at: c.arrived_at ?? new Date().toISOString(),
        })
      }
    },
    onError: (err: unknown) => {
      const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
      setError(typeof detail === 'string' ? detail : 'Could not register this visitor.')
    },
  })

  const canSubmit = !!siteId && fullName.trim().length > 0 && missingRequired.length === 0 && !isPending

  function renderField(f: VisitorFormField) {
    const value = custom[f.field_key]
    const set = (v: unknown) => setCustom((c) => ({ ...c, [f.field_key]: v }))

    if (f.field_type === 'checkbox') {
      return (
        <FormControlLabel
          key={f.id}
          control={<Checkbox checked={!!value} onChange={(e) => set(e.target.checked)} />}
          label={f.label + (f.is_required ? ' *' : '')}
        />
      )
    }
    const common = {
      key: f.id,
      label: f.label,
      required: f.is_required,
      size: 'small' as const,
      fullWidth: true,
      helperText: f.help_text ?? undefined,
      placeholder: f.placeholder ?? undefined,
    }
    if (f.field_type === 'select' || f.field_type === 'multiselect') {
      const multi = f.field_type === 'multiselect'
      return (
        <TextField
          {...common}
          select
          value={multi ? (Array.isArray(value) ? value : []) : (value ?? '')}
          onChange={(e) => set(e.target.value)}
          slotProps={{ select: { multiple: multi } }}
        >
          {f.options.map((o) => <MenuItem key={o} value={o}>{o}</MenuItem>)}
        </TextField>
      )
    }
    const typeMap: Record<string, string> = {
      number: 'number', date: 'date', email: 'email', phone: 'tel',
    }
    return (
      <TextField
        {...common}
        type={typeMap[f.field_type] ?? 'text'}
        multiline={f.field_type === 'textarea'}
        minRows={f.field_type === 'textarea' ? 2 : undefined}
        value={(value as string) ?? ''}
        onChange={(e) => set(e.target.value)}
        slotProps={f.field_type === 'date' ? { inputLabel: { shrink: true } } : undefined}
      />
    )
  }

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>Register a visitor</DialogTitle>
      <DialogContent dividers>
        <Stack direction="column" spacing={2} sx={{ pt: 1 }}>
          {error && <Alert severity="error" onClose={() => setError(null)}>{error}</Alert>}

          <Box>
            <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 0.75 }}>
              What kind of visit?
            </Typography>
            <ToggleButtonGroup
              exclusive
              size="small"
              value={visitType}
              onChange={(_, v: VisitType | null) => v && setVisitType(v)}
              sx={{ flexWrap: 'wrap' }}
            >
              {VISIT_TYPES.map((t) => (
                <ToggleButton key={t} value={t}>{VISIT_TYPE_LABELS[t]}</ToggleButton>
              ))}
            </ToggleButtonGroup>
          </Box>

          <TextField
            select size="small" fullWidth required label="Site"
            value={siteId} onChange={(e) => setSiteId(e.target.value)}
            helperText={vmsSites.length === 0 ? 'No site has visitor management enabled yet.' : undefined}
          >
            {vmsSites.map((s) => <MenuItem key={s.id} value={s.id}>{s.name}</MenuItem>)}
          </TextField>

          <TextField
            size="small" fullWidth required label="Visitor name"
            value={fullName} onChange={(e) => setFullName(e.target.value)}
          />
          <Stack direction="row" spacing={2}>
            <TextField size="small" fullWidth label="Company"
              value={company} onChange={(e) => setCompany(e.target.value)} />
            <TextField size="small" fullWidth label="ID / NRIC"
              value={idNumber} onChange={(e) => setIdNumber(e.target.value)} />
          </Stack>

          {showsPlate && (
            <TextField
              size="small" fullWidth label="Vehicle plate"
              value={plate}
              onChange={(e) => setPlate(e.target.value)}
              helperText="Starts the parking clock for this site"
              slotProps={{ htmlInput: { style: { textTransform: 'uppercase', letterSpacing: 1 } } }}
            />
          )}

          <TextField
            size="small" fullWidth label="Purpose" multiline minRows={2}
            value={purpose} onChange={(e) => setPurpose(e.target.value)}
          />

          {activeFields.length > 0 && (
            <>
              <Typography variant="caption" color="text.secondary">
                Required by this site
              </Typography>
              {activeFields.map(renderField)}
            </>
          )}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" disabled={!canSubmit} onClick={() => register()}>
          {isPending ? 'Registering…' : 'Register'}
        </Button>
      </DialogActions>
    </Dialog>
  )
}
