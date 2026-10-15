/**
 * Registering a vehicle, or changing one: the whole record, with the
 * organisation's own custom fields.
 *
 * The server checks everything again and says what is wrong in its own words:
 * a registration number already on another vehicle, a site the person may not
 * assign, a required custom field left empty. Nothing is saved until it has
 * said yes.
 */
import { useMemo, useState } from 'react'
import {
  Box, Button, Dialog, DialogActions, DialogContent, DialogTitle, FormControlLabel, MenuItem, Skeleton, Switch, TextField,
  Typography,
} from '@mui/material'
import { useMutation, useQuery } from '@tanstack/react-query'
import Stack from '@/components/common/Stack'
import { ErrorState, SaveStatus } from '@/components/states'
import {
  createFleetVehicle, changeFleetVehicle, getFleetOptions,
  type CustomValue, type FleetVehicle, type FleetVehicleDetail, type VehicleField, type VehicleInput,
} from '@/api/fleet'

interface Props {
  /** The vehicle to change; left out, a new one is registered. */
  vehicle?: FleetVehicleDetail | null
  onClose: () => void
  onSaved: (vehicle: FleetVehicle) => void
}

type Form = Record<'name' | 'plate_number' | 'vehicle_type' | 'make' | 'model' | 'color' | 'site_id' | 'department'
  | 'assigned_driver_user_id' | 'driver_name' | 'driver_phone' | 'service_status' | 'notes', string>

const blank = (v: string | null | undefined) => v ?? ''
const orNull = (v: string) => (v.trim() ? v.trim() : null)

function startFrom(v?: FleetVehicleDetail | null): Form {
  return {
    name: blank(v?.name), plate_number: blank(v?.plate_number), vehicle_type: v?.vehicle_type ?? 'patrol_car',
    make: blank(v?.make), model: blank(v?.model), color: blank(v?.color), site_id: blank(v?.site_id),
    department: blank(v?.department), assigned_driver_user_id: blank(v?.assigned_driver_user_id),
    driver_name: blank(v?.driver_name), driver_phone: blank(v?.driver_phone), service_status: v?.service_status ?? 'in_service',
    notes: blank(v?.notes),
  }
}

export function VehicleFormDialog({ vehicle, onClose, onSaved }: Props) {
  const editing = !!vehicle
  const { data: options, isLoading, isLoadingError: optionsFailed, error: optionsError, refetch } = useQuery({
    queryKey: ['fleet-options'], queryFn: getFleetOptions,
  })
  const [form, setForm] = useState<Form>(() => startFrom(vehicle))
  const [custom, setCustom] = useState<Record<string, CustomValue | ''>>(() => ({ ...(vehicle?.custom_fields ?? {}) }))
  const set = (k: keyof Form) => (e: { target: { value: string } }) => setForm((f) => ({ ...f, [k]: e.target.value }))

  // The fields asked for: those in use, and - on a vehicle that has one - a retired field's value, shown as it is.
  const fields = useMemo<VehicleField[]>(() => {
    const inUse = options?.fields ?? []
    const retired = (vehicle?.fields ?? []).filter((f) => !f.is_active && vehicle?.custom_fields?.[f.field_key] !== undefined)
    return [...inUse, ...retired]
  }, [options, vehicle])

  const save = useMutation({
    mutationFn: () => {
      const body: VehicleInput = {
        name: form.name.trim(), plate_number: orNull(form.plate_number), vehicle_type: form.vehicle_type,
        make: orNull(form.make), model: orNull(form.model), color: orNull(form.color), site_id: orNull(form.site_id),
        department: orNull(form.department), assigned_driver_user_id: orNull(form.assigned_driver_user_id),
        driver_name: orNull(form.driver_name), driver_phone: orNull(form.driver_phone),
        service_status: form.service_status as VehicleInput['service_status'], notes: orNull(form.notes),
        // Only what this form shows is sent: a retired field's value is left as it is.
        custom_fields: Object.fromEntries((options?.fields ?? []).map((f) => [f.field_key, custom[f.field_key] === '' ? null : custom[f.field_key] ?? null])),
      }
      return editing ? changeFleetVehicle(vehicle!.id, body) : createFleetVehicle(body)
    },
    onSuccess: onSaved,
  })

  const needs = !form.name.trim() || (!editing && !form.plate_number.trim()) || (!!options?.site_required && !form.site_id)
  const title = editing ? `Change ${vehicle!.plate_number ?? vehicle!.name}` : 'Register a vehicle'

  return (
    <Dialog open onClose={onClose} maxWidth="md" fullWidth aria-labelledby="vehicle-form-title">
      <DialogTitle id="vehicle-form-title">{title}</DialogTitle>
      <DialogContent>
        {isLoading ? (
          <Stack sx={{ gap: 1.5, pt: 1 }}>{[0, 1, 2, 3].map((i) => <Skeleton key={i} variant="rounded" height={40} />)}</Stack>
        ) : optionsFailed ? (
          <ErrorState title="The form could not be loaded" error={optionsError} onRetry={refetch} />
        ) : (
          <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', sm: '1fr 1fr' }, gap: 2, pt: 1 }}>
            <TextField label="Registration number" required={!editing} value={form.plate_number} onChange={set('plate_number')}
                       helperText="One vehicle per registration number; spaces and dashes do not matter" size="small" />
            <TextField label="Name" required value={form.name} onChange={set('name')} size="small"
                       helperText="What the vehicle is called on lists, e.g. Patrol 3" />
            <TextField select label="Type" value={form.vehicle_type} onChange={set('vehicle_type')} size="small">
              {options?.vehicle_types.map((t) => <MenuItem key={t.value} value={t.value}>{t.label}</MenuItem>)}
            </TextField>
            <TextField select label="Service status" value={form.service_status} onChange={set('service_status')} size="small">
              {options?.service_statuses.map((s) => <MenuItem key={s.value} value={s.value}>{s.label}</MenuItem>)}
            </TextField>
            <TextField label="Make" value={form.make} onChange={set('make')} size="small" />
            <TextField label="Model" value={form.model} onChange={set('model')} size="small" />
            <TextField label="Colour" value={form.color} onChange={set('color')} size="small" />
            <TextField label="Department" value={form.department} onChange={set('department')} size="small" />
            <TextField select label="Site" value={form.site_id} onChange={set('site_id')} size="small" required={options?.site_required}
                       slotProps={{ select: { displayEmpty: true }, inputLabel: { shrink: true } }}
                       helperText={options?.site_required ? 'One of your sites' : 'Leave empty for a vehicle of the whole organisation'}>
              {!options?.site_required && <MenuItem value="">No site</MenuItem>}
              {options?.sites.map((s) => <MenuItem key={s.id} value={s.id}>{s.name}</MenuItem>)}
            </TextField>
            <TextField select label="Driver (a person here)" value={form.assigned_driver_user_id} onChange={set('assigned_driver_user_id')}
                       size="small" helperText="Or write a driver who has no account below" slotProps={{ select: { displayEmpty: true }, inputLabel: { shrink: true } }}>
              <MenuItem value="">Nobody</MenuItem>
              {options?.drivers.map((d) => <MenuItem key={d.id} value={d.id}>{d.full_name}</MenuItem>)}
            </TextField>
            <TextField label="Driver's name" value={form.driver_name} onChange={set('driver_name')} size="small" />
            <TextField label="Driver's phone" value={form.driver_phone} onChange={set('driver_phone')} size="small" />
            <TextField label="Notes" value={form.notes} onChange={set('notes')} size="small" multiline minRows={2}
                       sx={{ gridColumn: { sm: '1 / -1' } }} />
            {fields.length > 0 && (
              <Typography variant="subtitle2" sx={{ gridColumn: '1 / -1', mt: 1 }}>The organisation's own fields</Typography>
            )}
            {fields.map((f) => (
              <CustomField key={f.field_key} field={f} value={custom[f.field_key] ?? ''}
                           onChange={(value) => setCustom((c) => ({ ...c, [f.field_key]: value }))} />
            ))}
          </Box>
        )}
      </DialogContent>
      <DialogActions sx={{ gap: 1 }}>
        <SaveStatus state={save.isPending ? 'saving' : save.isError ? 'failed' : 'idle'} error={save.error} />
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" disabled={needs || save.isPending || !options} onClick={() => save.mutate()}>
          {editing ? 'Save changes' : 'Register'}
        </Button>
      </DialogActions>
    </Dialog>
  )
}

function CustomField({ field, value, onChange }: { field: VehicleField; value: CustomValue | ''; onChange: (v: CustomValue | '') => void }) {
  const retired = !field.is_active
  const helper = retired ? 'Retired field: its value is kept, and cannot be changed' : field.help_text ?? undefined
  const common = { label: field.label, size: 'small' as const, required: field.is_required && !retired, disabled: retired, helperText: helper }
  if (field.field_type === 'boolean') {
    return (
      <FormControlLabel label={field.label} disabled={retired}
                        control={<Switch checked={value === true} onChange={(e) => onChange(e.target.checked)} />} />
    )
  }
  if (field.field_type === 'select') {
    return (
      <TextField select {...common} value={value} onChange={(e) => onChange(e.target.value)} slotProps={{ select: { displayEmpty: true }, inputLabel: { shrink: true } }}>
        {!field.is_required && <MenuItem value="">Not given</MenuItem>}
        {field.options.map((o) => <MenuItem key={o} value={o}>{o}</MenuItem>)}
      </TextField>
    )
  }
  if (field.field_type === 'number') {
    return (
      <TextField {...common} type="number" value={value}
                 onChange={(e) => onChange(e.target.value === '' ? '' : Number(e.target.value))} />
    )
  }
  return (
    <TextField {...common} type={field.field_type === 'date' ? 'date' : 'text'} value={value}
               slotProps={field.field_type === 'date' ? { inputLabel: { shrink: true } } : undefined}
               onChange={(e) => onChange(e.target.value)} />
  )
}
