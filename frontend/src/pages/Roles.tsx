/**
 * Roles (Gap 91) — view the built-in roles and create/edit tenant-custom
 * roles with bespoke permission sets (e.g. "Control Room Operator without
 * evidence download"). Gated on role:manage (super_admin + admin).
 */
import { useMemo, useState } from 'react'
import {
  Box,
  Button,
  Checkbox,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Divider,
  FormControlLabel,
  IconButton,
  List,
  ListItem,
  ListItemText,
  TextField,
  Tooltip,
  Typography,
} from '@mui/material'
import Stack from '@/components/common/Stack'
import AddIcon from '@mui/icons-material/Add'
import DeleteIcon from '@mui/icons-material/Delete'
import EditIcon from '@mui/icons-material/Edit'
import LockIcon from '@mui/icons-material/Lock'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  createRole, deleteRole, listPermissionCatalogue, listRoles, updateRole,
  type RoleRow,
} from '@/api/roles'
import { GlassCard } from '@/components/common/GlassCard'
import { PageHeader } from '@/components/common/PageHeader'

const BUILTIN_LABELS: Record<number, string> = {
  1: 'Super Admin', 2: 'Admin', 3: 'Supervisor', 4: 'Operator',
  5: 'Security Guard', 6: 'Viewer', 7: 'Client', 8: 'Manager',
}

function RoleEditor({ open, onClose, existing }: {
  open: boolean; onClose: () => void; existing?: RoleRow | null
}) {
  const qc = useQueryClient()
  const [name, setName] = useState(existing?.name ?? '')
  const [description, setDescription] = useState(existing?.description ?? '')
  const [selected, setSelected] = useState<Set<string>>(new Set(existing?.permission_codes ?? []))

  const { data: catalogue = [] } = useQuery({
    queryKey: ['permission-catalogue'],
    queryFn: listPermissionCatalogue,
    enabled: open,
  })

  const byCategory = useMemo(() => {
    const map = new Map<string, typeof catalogue>()
    for (const p of catalogue) {
      if (!map.has(p.category)) map.set(p.category, [])
      map.get(p.category)!.push(p)
    }
    return map
  }, [catalogue])

  const { mutate: save, isPending } = useMutation({
    mutationFn: () => {
      const codes = [...selected]
      return existing
        ? updateRole(existing.id, { name, description, permission_codes: codes })
        : createRole({ name, description, permission_codes: codes })
    },
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['roles'] }); onClose() },
  })

  const toggle = (code: string) =>
    setSelected((prev) => {
      const next = new Set(prev)
      next.has(code) ? next.delete(code) : next.add(code)
      return next
    })

  return (
    <Dialog open={open} onClose={onClose} maxWidth="md" fullWidth>
      <DialogTitle>{existing ? `Edit — ${existing.name}` : 'New Custom Role'}</DialogTitle>
      <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 1.5, pt: 1 }}>
        <TextField label="Role name" size="small" value={name}
                   onChange={(e) => setName(e.target.value)} fullWidth autoFocus />
        <TextField label="Description (optional)" size="small" value={description}
                   onChange={(e) => setDescription(e.target.value)} fullWidth />
        <Typography variant="caption" color="text.secondary">
          {selected.size} permission{selected.size === 1 ? '' : 's'} selected
        </Typography>
        <Box sx={{ maxHeight: 360, overflow: 'auto', pr: 1 }}>
          {[...byCategory.entries()].map(([category, perms]) => (
            <Box key={category} sx={{ mb: 1 }}>
              <Typography variant="caption" fontWeight={700}
                          sx={{ textTransform: 'uppercase', color: 'primary.main' }}>
                {category}
              </Typography>
              <Stack sx={{ pl: 1 }}>
                {perms.map((p) => (
                  <FormControlLabel
                    key={p.code}
                    control={<Checkbox size="small" checked={selected.has(p.code)}
                                       onChange={() => toggle(p.code)} />}
                    label={
                      <Tooltip title={p.description ?? ''} placement="right">
                        <Typography variant="body2" component="span">{p.code}</Typography>
                      </Tooltip>
                    }
                  />
                ))}
              </Stack>
              <Divider sx={{ mt: 0.5 }} />
            </Box>
          ))}
        </Box>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={isPending}>Cancel</Button>
        <Button variant="contained" onClick={() => save()}
                disabled={isPending || !name.trim() || selected.size === 0}>
          {existing ? 'Save' : 'Create Role'}
        </Button>
      </DialogActions>
    </Dialog>
  )
}

export default function Roles() {
  const qc = useQueryClient()
  const [editorOpen, setEditorOpen] = useState(false)
  const [editing, setEditing] = useState<RoleRow | null>(null)

  const { data: roles = [] } = useQuery({ queryKey: ['roles'], queryFn: listRoles })

  const { mutate: remove } = useMutation({
    mutationFn: (id: number) => deleteRole(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['roles'] }),
    onError: (err: any) => {
      const msg = err?.response?.data?.detail ?? 'Could not delete role'
      alert(msg)
    },
  })

  return (
    <Box>
      <PageHeader title="Roles & Permissions"
                  subtitle="Built-in roles plus your own custom roles" />

      <Stack direction="row" sx={{ mb: 2 }}>
        <Button variant="contained" size="small" startIcon={<AddIcon />}
                onClick={() => { setEditing(null); setEditorOpen(true) }}>
          New Custom Role
        </Button>
      </Stack>

      <GlassCard>
        <List>
          {roles.map((role) => (
            <ListItem key={role.id} divider
                      sx={{ '&:hover': { background: 'rgba(255,255,255,0.03)' } }}>
              <ListItemText
                primary={
                  <Stack direction="row" spacing={1} alignItems="center">
                    <Typography variant="body2" fontWeight={700}>
                      {role.is_custom ? role.name : (BUILTIN_LABELS[role.id] ?? role.name)}
                    </Typography>
                    {role.is_custom ? (
                      <Chip label="Custom" size="small" color="primary"
                            sx={{ height: 18, fontSize: '0.62rem' }} />
                    ) : (
                      <Chip icon={<LockIcon sx={{ fontSize: '0.7rem !important' }} />}
                            label="Built-in" size="small" variant="outlined"
                            sx={{ height: 18, fontSize: '0.62rem' }} />
                    )}
                  </Stack>
                }
                secondary={`${role.permission_codes.length} permissions · ${role.user_count} user${role.user_count === 1 ? '' : 's'}`}
              />
              {role.is_custom && (
                <Stack direction="row" spacing={0.5}>
                  <Tooltip title="Edit permissions">
                    <IconButton size="small" onClick={() => { setEditing(role); setEditorOpen(true) }}>
                      <EditIcon fontSize="small" />
                    </IconButton>
                  </Tooltip>
                  <Tooltip title={role.user_count > 0
                    ? 'Reassign its users before deleting' : 'Delete role'}>
                    <span>
                      <IconButton size="small" color="error"
                                  disabled={role.user_count > 0}
                                  onClick={() => remove(role.id)}>
                        <DeleteIcon fontSize="small" />
                      </IconButton>
                    </span>
                  </Tooltip>
                </Stack>
              )}
            </ListItem>
          ))}
        </List>
      </GlassCard>

      {editorOpen && (
        <RoleEditor open onClose={() => setEditorOpen(false)} existing={editing} />
      )}
    </Box>
  )
}
