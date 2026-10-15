/**
 * The organisation's own fields on a fleet vehicle - a fuel card number, an
 * insurance expiry, a lease company - asked for on the vehicle form.
 *
 * A field's key and type stay once it is made, so the values given under it
 * keep their meaning. A field is retired, never deleted: it is no longer asked
 * for, and the values already given under it are kept and shown. Everybody
 * who can see the fleet sees the fields; whoever may define them changes them.
 */
import { useState } from 'react'
import {
  Box, Button, Chip, Dialog, DialogActions, DialogContent, DialogTitle, FormControlLabel, IconButton, MenuItem, Switch, Table,
  TableBody, TableCell, TableContainer, TableHead, TableRow, TextField, Tooltip, Typography,
} from '@mui/material'
import AddIcon from '@mui/icons-material/Add'
import EditIcon from '@mui/icons-material/EditOutlined'
import RetireIcon from '@mui/icons-material/DoNotDisturbOnOutlined'
import RestoreIcon from '@mui/icons-material/RestoreOutlined'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { PageHeader } from '@/components/common/PageHeader'
import { GlassCard } from '@/components/common/GlassCard'
import { SkeletonRows } from '@/components/common/SkeletonRows'
import { FleetNav } from '@/components/fleet/FleetNav'
import { SaveStatus, TableEmptyRow, TableErrorRow } from '@/components/states'
import {
  changeVehicleField, createVehicleField, getFleetOptions, listVehicleFields, fieldKeyOf, setVehicleFieldInUse, type FieldType, type VehicleField,
} from '@/api/fleet'

const COLS = 6
const TYPE_WORDS: Record<FieldType, string> = { text: 'Text', number: 'Number', date: 'Date', select: 'Choice', boolean: 'Yes or no' }

export default function FleetFields() {
  const qc = useQueryClient()
  const [editing, setEditing] = useState<VehicleField | 'new' | null>(null)
  const { data, isLoading, isLoadingError: fieldsFailed, error: fieldsError, refetch: refetchFields } = useQuery({
    queryKey: ['fleet-fields'], queryFn: listVehicleFields,
  })
  const { data: options } = useQuery({ queryKey: ['fleet-options'], queryFn: getFleetOptions })
  const inUse = useMutation({
    mutationFn: ({ field, on }: { field: VehicleField; on: boolean }) => setVehicleFieldInUse(field.id, on),
    onSuccess: () => again(),
  })
  const again = () => {
    qc.invalidateQueries({ queryKey: ['fleet-fields'] })
    qc.invalidateQueries({ queryKey: ['fleet-options'] })
  }
  const mayManage = !!options?.may.manage_fields

  return (
    <Box sx={{ p: 3 }}>
      <PageHeader title="Vehicle custom fields" subtitle="The organisation's own fields on every fleet vehicle" />
      <FleetNav />
      {mayManage && (
        <Button variant="contained" startIcon={<AddIcon />} onClick={() => setEditing('new')} sx={{ mb: 2 }}>New field</Button>
      )}
      <SaveStatus state={inUse.isPending ? 'saving' : inUse.isError ? 'failed' : 'idle'} error={inUse.error} />
      <GlassCard sx={{ p: 0 }}>
        <TableContainer>
          <Table size="small" aria-label="Vehicle custom fields">
            <TableHead>
              <TableRow><TableCell>Field</TableCell><TableCell>Key</TableCell><TableCell>Type</TableCell><TableCell>Required</TableCell><TableCell>Status</TableCell><TableCell align="right">Actions</TableCell></TableRow>
            </TableHead>
            <TableBody>
              {isLoading ? (
                <SkeletonRows cols={COLS} rows={3} />
              ) : fieldsFailed ? (
                <TableErrorRow cols={COLS} title="The fields could not be loaded" error={fieldsError} onRetry={refetchFields} />
              ) : !data?.items.length ? (
                <TableEmptyRow cols={COLS} title="No custom fields yet"
                               hint={mayManage ? 'Add one, e.g. a fuel card number or an insurance expiry.' : undefined} />
              ) : data.items.map((f) => (
                <TableRow key={f.id} hover sx={{ opacity: f.is_active ? 1 : 0.6 }}>
                  <TableCell><Typography variant="body2" sx={{ fontWeight: 600 }}>{f.label}</Typography>
                    {f.field_type === 'select' && <Typography variant="caption" color="text.secondary">{f.options.join(' · ')}</Typography>}</TableCell>
                  <TableCell><code>{f.field_key}</code></TableCell>
                  <TableCell>{TYPE_WORDS[f.field_type]}</TableCell>
                  <TableCell>{f.is_required ? 'Yes' : 'No'}</TableCell>
                  <TableCell>{f.is_active ? <Chip size="small" label="In use" color="success" variant="outlined" /> : <Chip size="small" label="Retired" />}</TableCell>
                  <TableCell align="right">
                    {mayManage && f.is_active && (
                      <Tooltip title="Change"><IconButton size="small" aria-label={`Change ${f.label}`} onClick={() => setEditing(f)}>
                        <EditIcon fontSize="small" /></IconButton></Tooltip>)}
                    {mayManage && (
                      <Tooltip title={f.is_active ? 'Retire: stop asking for it, keep its values' : 'Use it again'}>
                        <IconButton size="small" aria-label={`${f.is_active ? 'Retire' : 'Reinstate'} ${f.label}`} disabled={inUse.isPending}
                                    onClick={() => inUse.mutate({ field: f, on: !f.is_active })}>
                          {f.is_active ? <RetireIcon fontSize="small" /> : <RestoreIcon fontSize="small" />}
                        </IconButton>
                      </Tooltip>)}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      </GlassCard>
      {editing && <FieldDialog field={editing === 'new' ? null : editing} onClose={() => setEditing(null)}
                               onSaved={() => { setEditing(null); again() }} />}
    </Box>
  )
}

function FieldDialog({ field, onClose, onSaved }: { field: VehicleField | null; onClose: () => void; onSaved: () => void }) {
  const [label, setLabel] = useState(field?.label ?? '')
  const [key, setKey] = useState(field?.field_key ?? '')
  const [keyTouched, setKeyTouched] = useState(!!field)
  const [type, setType] = useState<FieldType>(field?.field_type ?? 'text')
  const [options, setOptions] = useState((field?.options ?? []).join('\n'))
  const [required, setRequired] = useState(field?.is_required ?? false)
  const [help, setHelp] = useState(field?.help_text ?? '')
  const [order, setOrder] = useState(String(field?.sort_order ?? 0))
  const choices = options.split('\n').map((o) => o.trim()).filter(Boolean)
  const save = useMutation({
    mutationFn: () => {
      const common = {
        label: label.trim(), is_required: required, help_text: help.trim() || null, sort_order: Number(order) || 0,
        ...(type === 'select' ? { options: choices } : {}),
      }
      return field ? changeVehicleField(field.id, common) : createVehicleField({ ...common, field_key: key, field_type: type })
    },
    onSuccess: onSaved,
  })
  const ready = label.trim() && /^[a-z][a-z0-9_]{0,49}$/.test(key) && (type !== 'select' || choices.length > 0)
  return (
    <Dialog open onClose={onClose} maxWidth="sm" fullWidth aria-labelledby="field-title">
      <DialogTitle id="field-title">{field ? `Change ${field.label}` : 'New field'}</DialogTitle>
      <DialogContent>
        <Box sx={{ display: 'grid', gap: 2, pt: 1 }}>
          <TextField label="Label" required size="small" value={label}
                     onChange={(e) => { setLabel(e.target.value); if (!keyTouched) setKey(fieldKeyOf(e.target.value)) }} />
          <TextField label="Key" required size="small" value={key} disabled={!!field}
                     onChange={(e) => { setKey(e.target.value); setKeyTouched(true) }}
                     helperText={field ? 'A field keeps its key, so the values given under it keep their meaning'
                       : 'Lower-case letters, digits and _; it cannot be changed later'} />
          <TextField select label="Type" size="small" value={type} disabled={!!field} onChange={(e) => setType(e.target.value as FieldType)}
                     helperText={field ? 'A field keeps its type' : undefined}>
            {(Object.keys(TYPE_WORDS) as FieldType[]).map((t) => <MenuItem key={t} value={t}>{TYPE_WORDS[t]}</MenuItem>)}
          </TextField>
          {type === 'select' && (
            <TextField label="Choices, one per line" required size="small" multiline minRows={3} value={options}
                       onChange={(e) => setOptions(e.target.value)} />
          )}
          <TextField label="Help shown under the field" size="small" value={help} onChange={(e) => setHelp(e.target.value)} />
          <TextField label="Order on the form" size="small" type="number" value={order} onChange={(e) => setOrder(e.target.value)} />
          <FormControlLabel label="Required on every vehicle" control={<Switch checked={required} onChange={(e) => setRequired(e.target.checked)} />} />
        </Box>
      </DialogContent>
      <DialogActions sx={{ gap: 1 }}>
        <SaveStatus state={save.isPending ? 'saving' : save.isError ? 'failed' : 'idle'} error={save.error} />
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" disabled={!ready || save.isPending} onClick={() => save.mutate()}>{field ? 'Save' : 'Add the field'}</Button>
      </DialogActions>
    </Dialog>
  )
}
