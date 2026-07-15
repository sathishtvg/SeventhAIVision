import { useEffect, useMemo, useState } from 'react'
import {
  Box, Typography, Table, TableBody, TableCell, TableContainer, TableHead, TableRow,
  Chip, IconButton, Button, Dialog, DialogTitle, DialogContent, DialogActions,
  TextField, Select, MenuItem, FormControl, InputLabel, Skeleton, Paper, Tooltip, Stack,
  List, ListItem, ListItemText, ListItemSecondaryAction, Divider, Tabs, Tab,
} from '@mui/material'
import AddIcon from '@mui/icons-material/Add'
import BlockIcon from '@mui/icons-material/Block'
import EditIcon from '@mui/icons-material/Edit'
import LockOpenIcon from '@mui/icons-material/LockOpen'
import DevicesIcon from '@mui/icons-material/Devices'
import LogoutIcon from '@mui/icons-material/Logout'
import PlaceIcon from '@mui/icons-material/Place'
import WorkspacePremiumIcon from '@mui/icons-material/WorkspacePremium'
import AccountTreeIcon from '@mui/icons-material/AccountTree'
import DeleteIcon from '@mui/icons-material/Delete'
import WarningAmberIcon from '@mui/icons-material/WarningAmber'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { GlassCard } from '@/components/common/GlassCard'
import { PermissionGuard } from '@/components/common/PermissionGuard'
import {
  getUsers, createUser, updateUser, deactivateUser, getUserSites, setUserSites,
  getEmployeeDocuments, uploadEmployeeDocument, deleteEmployeeDocument,
} from '@/api/users'
import { listRoles } from '@/api/roles'
import { getUserSessions, revokeAllUserSessions, unlockUserAccount } from '@/api/sessions'
import { getSites } from '@/api/sites'
import type { User } from '@/types/api'

const ROLE_LABELS: Record<number, string> = {
  1: 'Super Admin',
  2: 'Admin',
  3: 'Supervisor',
  4: 'Operator',
  5: 'Security Guard',
  6: 'Viewer',
  7: 'Client',
  8: 'Manager',
}

interface UserFormDialogProps {
  open: boolean
  onClose: () => void
  editUser?: User | null
  onCreated?: (user: User) => void
}

const DOCUMENT_TYPE_LABELS: Record<string, string> = {
  passport: 'Passport',
  work_pass: 'Work Pass',
  certification: 'Certification',
  other: 'Other',
}

const EMPLOYMENT_TYPE_LABELS: Record<string, string> = {
  full_time: 'Full-Time',
  part_time: 'Part-Time',
  contract: 'Contract',
}

const GRID_COLUMN_COUNT = 9

function payDisplay(user: User): { label: string; isSet: boolean } {
  if (user.monthly_salary != null) return { label: `$${user.monthly_salary.toLocaleString()}/mo`, isSet: true }
  if (user.daily_rate != null) return { label: `$${user.daily_rate}/day`, isSet: true }
  if (user.hourly_rate != null) return { label: `$${user.hourly_rate}/hr`, isSet: true }
  return { label: 'Not set', isSet: false }
}

function documentStatus(expiryDate: string | null): { label: string; color: 'success' | 'warning' | 'error' | undefined } {
  if (!expiryDate) return { label: 'No Expiry', color: undefined }
  const days = (new Date(expiryDate).getTime() - Date.now()) / 86400000
  if (days < 0) return { label: 'Expired', color: 'error' }
  if (days <= 30) return { label: 'Expiring Soon', color: 'warning' }
  return { label: 'Valid', color: 'success' }
}

function EmployeeDocumentsPanel({ userId }: { userId: string }) {
  const queryClient = useQueryClient()
  const [addOpen, setAddOpen] = useState(false)
  const [docType, setDocType] = useState<'passport' | 'work_pass' | 'certification' | 'other'>('work_pass')
  const [docNumber, setDocNumber] = useState('')
  const [issuingBody, setIssuingBody] = useState('')
  const [expiryDate, setExpiryDate] = useState('')
  const [file, setFile] = useState<File | undefined>(undefined)

  const { data: documents = [], isLoading } = useQuery({
    queryKey: ['employee-documents', userId],
    queryFn: () => getEmployeeDocuments(userId),
  })

  const resetForm = () => { setDocNumber(''); setIssuingBody(''); setExpiryDate(''); setFile(undefined) }

  const { mutate: upload, isPending: uploading } = useMutation({
    mutationFn: () =>
      uploadEmployeeDocument(userId, {
        document_type: docType,
        document_number: docNumber || undefined,
        issuing_body: issuingBody || undefined,
        expiry_date: expiryDate || undefined,
        file,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['employee-documents', userId] })
      setAddOpen(false)
      resetForm()
    },
  })

  const { mutate: remove } = useMutation({
    mutationFn: (docId: string) => deleteEmployeeDocument(userId, docId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['employee-documents', userId] }),
  })

  return (
    <Box sx={{ mt: 1 }}>
      <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 1 }}>
        <Typography variant="subtitle2">Documents</Typography>
        <Button size="small" startIcon={<AddIcon />} onClick={() => setAddOpen(true)}>Add Document</Button>
      </Stack>
      {isLoading ? (
        <Skeleton />
      ) : documents.length === 0 ? (
        <Typography color="text.secondary" variant="body2">No documents uploaded yet.</Typography>
      ) : (
        <List dense sx={{ maxHeight: 220, overflow: 'auto' }}>
          {documents.map((d) => {
            const status = documentStatus(d.expiry_date)
            return (
              <ListItem key={d.id} disablePadding sx={{ py: 0.5 }}>
                <ListItemText
                  primary={`${DOCUMENT_TYPE_LABELS[d.document_type] ?? d.document_type}${d.document_number ? ` — ${d.document_number}` : ''}`}
                  secondary={d.expiry_date ? `Expires ${new Date(d.expiry_date).toLocaleDateString()}` : undefined}
                />
                <ListItemSecondaryAction sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                  <Chip size="small" label={status.label} color={status.color} variant={status.color ? 'filled' : 'outlined'} />
                  <IconButton size="small" onClick={() => remove(d.id)} aria-label="Delete document">
                    <DeleteIcon fontSize="small" />
                  </IconButton>
                </ListItemSecondaryAction>
              </ListItem>
            )
          })}
        </List>
      )}
      <Dialog open={addOpen} onClose={() => setAddOpen(false)} maxWidth="xs" fullWidth>
        <DialogTitle>Add Document</DialogTitle>
        <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 2 }}>
          <FormControl size="small" fullWidth>
            <InputLabel>Document Type</InputLabel>
            <Select value={docType} label="Document Type" onChange={(e) => setDocType(e.target.value as typeof docType)}>
              <MenuItem value="passport">Passport</MenuItem>
              <MenuItem value="work_pass">Work Pass</MenuItem>
              <MenuItem value="certification">Certification</MenuItem>
              <MenuItem value="other">Other</MenuItem>
            </Select>
          </FormControl>
          <TextField label="Document Number (optional)" value={docNumber} onChange={(e) => setDocNumber(e.target.value)} size="small" fullWidth />
          <TextField label="Issuing Body (optional)" value={issuingBody} onChange={(e) => setIssuingBody(e.target.value)} size="small" fullWidth />
          <TextField
            label="Expiry Date" type="date" value={expiryDate} onChange={(e) => setExpiryDate(e.target.value)}
            size="small" fullWidth InputLabelProps={{ shrink: true }}
          />
          <Button component="label" variant="outlined" size="small">
            {file ? file.name : 'Choose File (optional)'}
            <input type="file" hidden onChange={(e) => setFile(e.target.files?.[0] ?? undefined)} />
          </Button>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setAddOpen(false)} disabled={uploading}>Cancel</Button>
          <Button variant="contained" onClick={() => upload()} disabled={uploading}>Add</Button>
        </DialogActions>
      </Dialog>
    </Box>
  )
}

function UserFormDialog({ open, onClose, editUser, onCreated }: UserFormDialogProps) {
  const queryClient = useQueryClient()
  const [tab, setTab] = useState(0)
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [fullName, setFullName] = useState('')
  const [roleId, setRoleId] = useState<number>(4)

  // Personal tab
  const [nricFin, setNricFin] = useState('')
  const [dob, setDob] = useState('')
  const [nationality, setNationality] = useState('')
  const [phone, setPhone] = useState('')
  const [address, setAddress] = useState('')
  const [emergencyName, setEmergencyName] = useState('')
  const [emergencyPhone, setEmergencyPhone] = useState('')

  // Employment tab
  const [employmentType, setEmploymentType] = useState('')
  const [designation, setDesignation] = useState('')
  const [department, setDepartment] = useState('')
  const [dateJoined, setDateJoined] = useState('')

  // Work Pass tab
  const [workPassType, setWorkPassType] = useState('')
  const [workPassExpiry, setWorkPassExpiry] = useState('')

  // Bank Details tab
  const [bankName, setBankName] = useState('')
  const [bankAccount, setBankAccount] = useState('')
  const [hourlyRate, setHourlyRate] = useState('')
  const [dailyRate, setDailyRate] = useState('')
  const [monthlySalary, setMonthlySalary] = useState('')

  // Resync every field whenever the dialog opens or which user it's editing
  // changes — a bare useState(editUser?.foo ?? '') initializer only runs on
  // this component's first mount (it's rendered unconditionally in Users()),
  // so without this effect neither "Create -> continue straight into Edit"
  // nor "Edit user A -> Edit user B" without a reload would ever resync.
  useEffect(() => {
    if (!open) return
    const u = editUser
    setTab(0)
    setEmail('')
    setPassword('')
    setFullName(u?.full_name ?? '')
    setRoleId(u?.role_id ?? 4)
    setNricFin(u?.nric_fin ?? '')
    setDob(u?.date_of_birth ?? '')
    setNationality(u?.nationality ?? '')
    setPhone(u?.phone ?? '')
    setAddress(u?.address ?? '')
    setEmergencyName(u?.emergency_contact_name ?? '')
    setEmergencyPhone(u?.emergency_contact_phone ?? '')
    setEmploymentType(u?.employment_type ?? '')
    setDesignation(u?.designation ?? '')
    setDepartment(u?.department ?? '')
    setDateJoined(u?.date_joined ?? '')
    setWorkPassType(u?.work_pass_type ?? '')
    setWorkPassExpiry(u?.work_pass_expiry ?? '')
    setBankName(u?.bank_name ?? '')
    setBankAccount(u?.bank_account_number ?? '')
    setHourlyRate(u?.hourly_rate?.toString() ?? '')
    setDailyRate(u?.daily_rate?.toString() ?? '')
    setMonthlySalary(u?.monthly_salary?.toString() ?? '')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, editUser?.id])

  // Built-in roles + this tenant's custom roles (Gap 91). Falls back to the
  // hardcoded built-in labels if the caller can't list roles.
  const { data: roles = [] } = useQuery({
    queryKey: ['roles'], queryFn: listRoles, enabled: open,
  })

  const { mutate: create, isPending: creating } = useMutation({
    mutationFn: createUser,
    onSuccess: (created) => {
      queryClient.invalidateQueries({ queryKey: ['users'] })
      // Stay open and transition into edit mode for the just-created user
      // instead of closing — lets the admin continue straight into
      // Employment/Work Pass & Documents/Bank & Pay without reopening.
      onCreated?.(created)
    },
  })

  const { mutate: update, isPending: updating } = useMutation({
    mutationFn: ({ id, data }: { id: string; data: Parameters<typeof updateUser>[1] }) => updateUser(id, data),
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: ['users'] }); onClose() },
  })

  const isPending = creating || updating
  const isEdit = !!editUser

  const roleSelect = (
    <FormControl size="small" fullWidth>
      <InputLabel>Role</InputLabel>
      <Select value={roleId} label="Role" onChange={(e) => setRoleId(Number(e.target.value))}>
        {roles.length > 0
          ? roles.map((r) => (
              <MenuItem key={r.id} value={r.id}>
                {r.is_custom ? `${r.name} (Custom)` : (ROLE_LABELS[r.id] ?? r.name)}
              </MenuItem>
            ))
          : Object.entries(ROLE_LABELS).map(([id, label]) => (
              <MenuItem key={id} value={Number(id)}>{label}</MenuItem>
            ))}
      </Select>
    </FormControl>
  )

  function handleSubmit() {
    if (isEdit) {
      update({
        id: editUser!.id,
        data: {
          full_name: fullName || undefined,
          role_id: roleId,
          nric_fin: nricFin || undefined,
          date_of_birth: dob || undefined,
          nationality: nationality || undefined,
          phone: phone || undefined,
          address: address || undefined,
          emergency_contact_name: emergencyName || undefined,
          emergency_contact_phone: emergencyPhone || undefined,
          employment_type: (employmentType || undefined) as any,
          designation: designation || undefined,
          department: department || undefined,
          date_joined: dateJoined || undefined,
          work_pass_type: (workPassType || undefined) as any,
          work_pass_expiry: workPassExpiry || undefined,
          bank_name: bankName || undefined,
          bank_account_number: bankAccount || undefined,
          hourly_rate: hourlyRate ? Number(hourlyRate) : undefined,
          daily_rate: dailyRate ? Number(dailyRate) : undefined,
          monthly_salary: monthlySalary ? Number(monthlySalary) : undefined,
        },
      })
    } else {
      create({ email: email.trim(), password, role_id: roleId, full_name: fullName || undefined })
    }
  }

  return (
    <Dialog open={open} onClose={onClose} maxWidth={isEdit ? 'sm' : 'xs'} fullWidth>
      <DialogTitle>{isEdit ? 'Edit User' : 'Create User'}</DialogTitle>
      {isEdit && (
        <Tabs
          value={tab} onChange={(_, v) => setTab(v)} variant="scrollable" scrollButtons="auto"
          sx={{ px: 3, borderBottom: 1, borderColor: 'divider' }}
        >
          <Tab label="Personal" />
          <Tab label="Employment" />
          <Tab label="Work Pass & Documents" />
          <Tab label="Bank & Pay Details" />
        </Tabs>
      )}
      <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 2, minHeight: isEdit ? 340 : undefined }}>
        {!isEdit && (
          <>
            <TextField label="Email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} size="small" fullWidth />
            <TextField label="Password" type="password" value={password} onChange={(e) => setPassword(e.target.value)} size="small" fullWidth />
            <TextField label="Full Name (optional)" value={fullName} onChange={(e) => setFullName(e.target.value)} size="small" fullWidth />
            {roleSelect}
          </>
        )}

        {isEdit && tab === 0 && (
          <>
            <TextField label="Email" value={editUser!.email} size="small" fullWidth disabled />
            <TextField label="Full Name" value={fullName} onChange={(e) => setFullName(e.target.value)} size="small" fullWidth />
            <TextField label="Phone" value={phone} onChange={(e) => setPhone(e.target.value)} size="small" fullWidth />
            <TextField
              label="Date of Birth" type="date" value={dob} onChange={(e) => setDob(e.target.value)}
              size="small" fullWidth InputLabelProps={{ shrink: true }}
            />
            <TextField label="Nationality" value={nationality} onChange={(e) => setNationality(e.target.value)} size="small" fullWidth />
            <TextField label="NRIC / FIN" value={nricFin} onChange={(e) => setNricFin(e.target.value)} size="small" fullWidth />
            <TextField label="Address" value={address} onChange={(e) => setAddress(e.target.value)} size="small" fullWidth multiline minRows={2} />
            <TextField label="Emergency Contact Name" value={emergencyName} onChange={(e) => setEmergencyName(e.target.value)} size="small" fullWidth />
            <TextField label="Emergency Contact Phone" value={emergencyPhone} onChange={(e) => setEmergencyPhone(e.target.value)} size="small" fullWidth />
          </>
        )}

        {isEdit && tab === 1 && (
          <>
            {roleSelect}
            <FormControl size="small" fullWidth>
              <InputLabel>Employment Type</InputLabel>
              <Select value={employmentType} label="Employment Type" onChange={(e) => setEmploymentType(e.target.value as typeof employmentType)}>
                <MenuItem value=""><em>Not set</em></MenuItem>
                <MenuItem value="full_time">Full-Time</MenuItem>
                <MenuItem value="part_time">Part-Time</MenuItem>
                <MenuItem value="contract">Contract</MenuItem>
              </Select>
            </FormControl>
            <TextField label="Designation" value={designation} onChange={(e) => setDesignation(e.target.value)} size="small" fullWidth />
            <TextField label="Department" value={department} onChange={(e) => setDepartment(e.target.value)} size="small" fullWidth />
            <TextField
              label="Date Joined" type="date" value={dateJoined} onChange={(e) => setDateJoined(e.target.value)}
              size="small" fullWidth InputLabelProps={{ shrink: true }}
            />
          </>
        )}

        {isEdit && tab === 2 && (
          <>
            <FormControl size="small" fullWidth>
              <InputLabel>Work Pass Type</InputLabel>
              <Select value={workPassType} label="Work Pass Type" onChange={(e) => setWorkPassType(e.target.value as typeof workPassType)}>
                <MenuItem value=""><em>Not set</em></MenuItem>
                <MenuItem value="citizen">Citizen</MenuItem>
                <MenuItem value="pr">Permanent Resident</MenuItem>
                <MenuItem value="ep">Employment Pass (EP)</MenuItem>
                <MenuItem value="sp">S Pass (SP)</MenuItem>
                <MenuItem value="wp">Work Permit (WP)</MenuItem>
              </Select>
            </FormControl>
            <TextField
              label="Work Pass Expiry" type="date" value={workPassExpiry} onChange={(e) => setWorkPassExpiry(e.target.value)}
              size="small" fullWidth InputLabelProps={{ shrink: true }}
            />
            <Divider />
            <EmployeeDocumentsPanel userId={editUser!.id} />
          </>
        )}

        {isEdit && tab === 3 && (
          <>
            <TextField label="Bank Name" value={bankName} onChange={(e) => setBankName(e.target.value)} size="small" fullWidth />
            <TextField label="Bank Account Number" value={bankAccount} onChange={(e) => setBankAccount(e.target.value)} size="small" fullWidth />
            <TextField
              label="Hourly Rate ($/hr)" type="number" value={hourlyRate}
              onChange={(e) => setHourlyRate(e.target.value)} size="small" fullWidth
              helperText="Used for base pay if no daily/monthly rate is set. Always used to compute overtime pay when set — even for a guard whose base pay comes from Daily or Monthly Rate."
            />
            <TextField
              label="Daily Rate ($/day)" type="number" value={dailyRate}
              onChange={(e) => setDailyRate(e.target.value)} size="small" fullWidth
              helperText="Paid per distinct day worked in the period — for part-time/relief guards on a day-basis rate. Used for base pay when set, unless Monthly Salary is also set."
            />
            <TextField
              label="Monthly Salary ($)" type="number" value={monthlySalary}
              onChange={(e) => setMonthlySalary(e.target.value)} size="small" fullWidth
              helperText="Overrides Daily/Hourly Rate for base pay if set"
            />
          </>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={isPending}>Cancel</Button>
        <Button
          variant="contained"
          disabled={(!isEdit && (!email.trim() || !password)) || isPending}
          onClick={handleSubmit}
        >
          {isEdit ? 'Save' : 'Create'}
        </Button>
      </DialogActions>
    </Dialog>
  )
}

function SessionsDialog({ open, onClose, userId }: { open: boolean; onClose: () => void; userId: string }) {
  const queryClient = useQueryClient()
  const { data: sessions, isLoading } = useQuery({
    queryKey: ['user-sessions', userId],
    queryFn: () => getUserSessions(userId),
    enabled: open,
  })
  const { mutate: revokeAll, isPending: revoking } = useMutation({
    mutationFn: () => revokeAllUserSessions(userId),
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: ['user-sessions', userId] }); onClose() },
  })
  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>Active Sessions</DialogTitle>
      <DialogContent sx={{ p: 0 }}>
        {isLoading ? (
          <Box sx={{ p: 2 }}><Skeleton /><Skeleton /><Skeleton /></Box>
        ) : !sessions?.length ? (
          <Box sx={{ p: 3, textAlign: 'center' }}>
            <Typography color="text.secondary">No active sessions</Typography>
          </Box>
        ) : (
          <List dense>
            {sessions.map((s, i) => (
              <Box key={s.id}>
                {i > 0 && <Divider />}
                <ListItem>
                  <ListItemText
                    primary={s.device_name ?? 'Unknown device'}
                    secondary={`IP: ${s.last_ip ?? '—'} · Last seen: ${s.last_seen_at ? new Date(s.last_seen_at).toLocaleString() : '—'}`}
                  />
                  <ListItemSecondaryAction>
                    <Typography variant="caption" color="text.secondary">
                      Expires {new Date(s.expires_at).toLocaleDateString()}
                    </Typography>
                  </ListItemSecondaryAction>
                </ListItem>
              </Box>
            ))}
          </List>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Close</Button>
        <Button color="error" variant="outlined" disabled={revoking || !sessions?.length} onClick={() => revokeAll()}>
          Revoke All Sessions
        </Button>
      </DialogActions>
    </Dialog>
  )
}

function SiteAccessDialog({ open, onClose, user }: { open: boolean; onClose: () => void; user: User }) {
  const queryClient = useQueryClient()
  const [selected, setSelected] = useState<string[] | null>(null)

  const { data: allSites = [] } = useQuery({ queryKey: ['sites'], queryFn: () => getSites(), enabled: open })
  const { data: assigned, isLoading } = useQuery({
    queryKey: ['user-sites', user.id],
    queryFn: () => getUserSites(user.id),
    enabled: open,
  })

  // Initialise the selection once the current assignments load
  const current = selected ?? assigned?.map((s) => s.site_id) ?? []

  const { mutate: save, isPending: saving } = useMutation({
    mutationFn: () => setUserSites(user.id, current),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['user-sites', user.id] })
      onClose()
    },
  })

  const toggle = (siteId: string) =>
    setSelected(current.includes(siteId) ? current.filter((s) => s !== siteId) : [...current, siteId])

  const isClient = user.role_id === 7

  return (
    <Dialog open={open} onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle>Site Access — {user.full_name ?? user.email}</DialogTitle>
      <DialogContent sx={{ pt: 1 }}>
        <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1.5 }}>
          {isClient
            ? 'Clients see ONLY the sites selected below. With none selected, this client sees no data.'
            : 'With no sites selected, this user sees every site. Selecting sites restricts them to those sites only.'}
        </Typography>
        {isLoading ? (
          <Box><Skeleton /><Skeleton /><Skeleton /></Box>
        ) : allSites.length === 0 ? (
          <Typography color="text.secondary" variant="body2">No sites created yet.</Typography>
        ) : (
          <List dense sx={{ maxHeight: 320, overflow: 'auto' }}>
            {allSites.map((site: { id: string; name: string }) => (
              <ListItem key={site.id} disablePadding>
                <ListItemText
                  primary={site.name}
                  sx={{ px: 1 }}
                />
                <Chip
                  label={current.includes(site.id) ? 'Assigned' : 'Assign'}
                  size="small"
                  color={current.includes(site.id) ? 'primary' : 'default'}
                  variant={current.includes(site.id) ? 'filled' : 'outlined'}
                  onClick={() => toggle(site.id)}
                  sx={{ cursor: 'pointer' }}
                />
              </ListItem>
            ))}
          </List>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={saving}>Cancel</Button>
        <Button variant="contained" onClick={() => save()} disabled={saving || isLoading}>
          Save
        </Button>
      </DialogActions>
    </Dialog>
  )
}

export default function Users() {
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editUser, setEditUser] = useState<User | null>(null)
  const [sessionsUserId, setSessionsUserId] = useState<string | null>(null)
  const [siteAccessUser, setSiteAccessUser] = useState<User | null>(null)
  const queryClient = useQueryClient()

  const { data: users, isLoading } = useQuery({ queryKey: ['users'], queryFn: getUsers })

  const { mutate: deactivate } = useMutation({
    mutationFn: deactivateUser,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['users'] }),
  })

  const { mutate: unlock } = useMutation({
    mutationFn: (userId: string) => unlockUserAccount(userId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['users'] }),
  })

  const isLocked = (user: User) =>
    user.locked_until != null && new Date(user.locked_until) > new Date()

  // Aegis (the tenant) is the customer org; role_id is already the hierarchy
  // rank (1=Super Admin down to 7=Client) — group the already-fetched users
  // by tier for a quick "who's in charge" view, no extra API call needed.
  const tiers = useMemo(() => {
    const byRole = new Map<number, User[]>()
    for (const u of users ?? []) {
      if (!byRole.has(u.role_id)) byRole.set(u.role_id, [])
      byRole.get(u.role_id)!.push(u)
    }
    return Object.keys(ROLE_LABELS)
      .map(Number)
      .sort((a, b) => a - b)
      .map((roleId) => ({ roleId, label: ROLE_LABELS[roleId], members: byRole.get(roleId) ?? [] }))
      .filter((t) => t.members.length > 0)
  }, [users])

  return (
    <Box>
      {tiers.length > 0 && (
        <GlassCard sx={{ p: 2, mb: 2 }}>
          <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 1.5 }}>
            <AccountTreeIcon fontSize="small" color="primary" />
            <Typography variant="subtitle2" sx={{ fontWeight: 700 }}>Organization Hierarchy</Typography>
          </Stack>
          <Stack direction="row" spacing={1.5} flexWrap="wrap" useFlexGap>
            {tiers.map((tier) => {
              const isSuperAdmin = tier.roleId === 1
              return (
                <Paper
                  key={tier.roleId}
                  variant="outlined"
                  sx={{
                    p: 1.5, minWidth: 160,
                    borderColor: isSuperAdmin ? 'warning.main' : 'divider',
                    bgcolor: isSuperAdmin ? 'rgba(255,193,7,0.08)' : 'transparent',
                  }}
                >
                  <Stack direction="row" alignItems="center" spacing={0.75} sx={{ mb: 0.5 }}>
                    {isSuperAdmin && <WorkspacePremiumIcon fontSize="small" sx={{ color: 'warning.main' }} />}
                    <Typography variant="caption" sx={{ fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                      {tier.label}
                    </Typography>
                  </Stack>
                  <Typography variant="body2" color="text.secondary">
                    {tier.members.length} {tier.members.length === 1 ? 'user' : 'users'}
                  </Typography>
                  {isSuperAdmin && (
                    <Typography variant="caption" sx={{ display: 'block', mt: 0.5 }}>
                      {tier.members.map((m) => m.full_name ?? m.email).join(', ')}
                    </Typography>
                  )}
                </Paper>
              )
            })}
          </Stack>
        </GlassCard>
      )}

      <Box sx={{ display: 'flex', justifyContent: 'flex-end', mb: 2 }}>
        <PermissionGuard permission="user:create">
          <Button startIcon={<AddIcon />} variant="contained" size="small" onClick={() => { setEditUser(null); setDialogOpen(true) }}>
            Add User
          </Button>
        </PermissionGuard>
      </Box>

      <GlassCard>
        <TableContainer component={Paper} elevation={0} sx={{ background: 'transparent' }}>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>User</TableCell>
                <TableCell>Role</TableCell>
                <TableCell>Site(s)</TableCell>
                <TableCell>Employment</TableCell>
                <TableCell>Pay</TableCell>
                <TableCell>Documents</TableCell>
                <TableCell>Status</TableCell>
                <TableCell>Last Login</TableCell>
                <TableCell align="right">Actions</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {isLoading
                ? Array.from({ length: 6 }).map((_, i) => (
                    <TableRow key={i}>
                      {Array.from({ length: GRID_COLUMN_COUNT }).map((__, j) => (
                        <TableCell key={j}><Skeleton /></TableCell>
                      ))}
                    </TableRow>
                  ))
                : users?.map((user) => {
                    const sites = user.site_names ?? []
                    const pay = payDisplay(user)
                    const expired = user.documents_expired_count ?? 0
                    const expiring = user.documents_expiring_count ?? 0
                    const totalDocs = user.documents_total_count ?? 0
                    return (
                    <TableRow key={user.id} hover>
                      <TableCell sx={{ maxWidth: 220 }}>
                        <Typography variant="body2" noWrap sx={{ color: user.full_name ? 'text.primary' : 'text.disabled' }}>
                          {user.full_name ?? user.email}
                        </Typography>
                        {user.full_name && (
                          <Typography variant="caption" color="text.secondary" noWrap sx={{ display: 'block' }}>
                            {user.email}
                          </Typography>
                        )}
                      </TableCell>
                      <TableCell>
                        <Chip label={ROLE_LABELS[user.role_id] ?? `Role ${user.role_id}`} size="small" variant="outlined" />
                      </TableCell>
                      <TableCell sx={{ maxWidth: 160 }}>
                        {sites.length === 0 ? (
                          <Chip
                            label={user.role_id === 7 ? 'No Sites' : 'Unrestricted'}
                            size="small" variant="outlined"
                            color={user.role_id === 7 ? 'error' : 'default'}
                          />
                        ) : (
                          <Stack direction="row" spacing={0.5} alignItems="center">
                            <Chip label={sites[0]} size="small" variant="outlined" sx={{ maxWidth: 110 }} />
                            {sites.length > 1 && (
                              <Tooltip title={sites.slice(1).join(', ')}>
                                <Typography variant="caption" color="text.secondary">+{sites.length - 1}</Typography>
                              </Tooltip>
                            )}
                          </Stack>
                        )}
                      </TableCell>
                      <TableCell>
                        {user.employment_type ? (
                          <Chip label={EMPLOYMENT_TYPE_LABELS[user.employment_type] ?? user.employment_type} size="small" variant="outlined" />
                        ) : (
                          <Typography variant="caption" color="text.disabled">—</Typography>
                        )}
                      </TableCell>
                      <TableCell>
                        {pay.isSet ? (
                          <Typography variant="body2">{pay.label}</Typography>
                        ) : (
                          <Tooltip title="No hourly, daily, or monthly rate configured — this guard will be skipped in payroll runs">
                            <Chip icon={<WarningAmberIcon />} label="Not set" size="small" color="warning" variant="outlined" />
                          </Tooltip>
                        )}
                      </TableCell>
                      <TableCell>
                        {totalDocs === 0 ? (
                          <Typography variant="caption" color="text.disabled">—</Typography>
                        ) : expired > 0 ? (
                          <Chip label={`${expired} Expired`} size="small" color="error" />
                        ) : expiring > 0 ? (
                          <Chip label={`${expiring} Expiring`} size="small" color="warning" variant="outlined" />
                        ) : (
                          <Chip label="Clean" size="small" color="success" variant="outlined" />
                        )}
                      </TableCell>
                      <TableCell>
                        <Stack direction="row" spacing={0.5} alignItems="center">
                          <Chip
                            label={user.is_active ? 'Active' : 'Inactive'}
                            size="small"
                            color={user.is_active ? 'success' : 'default'}
                            variant="outlined"
                          />
                          {isLocked(user) && (
                            <Tooltip title={`Locked until ${new Date(user.locked_until!).toLocaleTimeString()} (${user.failed_login_count} failed attempts)`}>
                              <Chip label="Locked" size="small" color="error" />
                            </Tooltip>
                          )}
                          {!isLocked(user) && user.failed_login_count > 0 && (
                            <Tooltip title={`${user.failed_login_count} failed login attempt(s)`}>
                              <Chip label={`${user.failed_login_count} fails`} size="small" color="warning" variant="outlined" />
                            </Tooltip>
                          )}
                        </Stack>
                      </TableCell>
                      <TableCell>
                        <Typography variant="caption" color="text.secondary">
                          {user.last_login_at ? new Date(user.last_login_at).toLocaleString() : 'Never'}
                        </Typography>
                      </TableCell>
                      <TableCell align="right">
                        <Stack direction="row" spacing={0.5} sx={{ justifyContent: 'flex-end' }}>
                          <PermissionGuard permission="user:update">
                            <Tooltip title="Edit">
                              <IconButton size="small" onClick={() => { setEditUser(user); setDialogOpen(true) }}>
                                <EditIcon fontSize="small" />
                              </IconButton>
                            </Tooltip>
                            <Tooltip title="Site Access">
                              <IconButton size="small" onClick={() => setSiteAccessUser(user)}>
                                <PlaceIcon fontSize="small" />
                              </IconButton>
                            </Tooltip>
                          </PermissionGuard>
                          <PermissionGuard permission="session:manage">
                            <Tooltip title="View Sessions">
                              <IconButton size="small" onClick={() => setSessionsUserId(user.id)}>
                                <DevicesIcon fontSize="small" />
                              </IconButton>
                            </Tooltip>
                            {isLocked(user) && (
                              <Tooltip title="Unlock Account">
                                <IconButton size="small" color="warning" onClick={() => unlock(user.id)}>
                                  <LockOpenIcon fontSize="small" />
                                </IconButton>
                              </Tooltip>
                            )}
                          </PermissionGuard>
                          <PermissionGuard permission="user:delete">
                            {user.is_active && (
                              <Tooltip title="Deactivate">
                                <IconButton size="small" color="error" onClick={() => deactivate(user.id)}>
                                  <BlockIcon fontSize="small" />
                                </IconButton>
                              </Tooltip>
                            )}
                          </PermissionGuard>
                        </Stack>
                      </TableCell>
                    </TableRow>
                    )
                  })}
            </TableBody>
          </Table>
        </TableContainer>
      </GlassCard>

      <UserFormDialog
        open={dialogOpen}
        onClose={() => setDialogOpen(false)}
        editUser={editUser}
        onCreated={(user) => setEditUser(user)}
      />

      {sessionsUserId && (
        <SessionsDialog
          open
          onClose={() => setSessionsUserId(null)}
          userId={sessionsUserId}
        />
      )}

      {siteAccessUser && (
        <SiteAccessDialog
          open
          onClose={() => setSiteAccessUser(null)}
          user={siteAccessUser}
        />
      )}
    </Box>
  )
}
