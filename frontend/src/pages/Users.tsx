import { useEffect, useMemo, useState } from 'react'
import {
  Box,
  Typography,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Chip,
  IconButton,
  Button,
  Avatar,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  TextField,
  Select,
  MenuItem,
  FormControl,
  FormHelperText,
  InputLabel,
  Skeleton,
  Paper,
  Tooltip,
  List,
  ListItem,
  ListItemText,
  ListItemSecondaryAction,
  Divider,
  Tabs,
  Tab,
  InputAdornment,
} from '@mui/material'
import Stack from '@/components/common/Stack'
import AddIcon from '@mui/icons-material/Add'
import SearchIcon from '@mui/icons-material/Search'
import BlockIcon from '@mui/icons-material/Block'
import EditIcon from '@mui/icons-material/Edit'
import LockOpenIcon from '@mui/icons-material/LockOpen'
import DevicesIcon from '@mui/icons-material/Devices'
import PlaceIcon from '@mui/icons-material/Place'
import WorkspacePremiumIcon from '@mui/icons-material/WorkspacePremium'
import AccountTreeIcon from '@mui/icons-material/AccountTree'
import DeleteIcon from '@mui/icons-material/Delete'
import WarningAmberIcon from '@mui/icons-material/WarningAmber'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { GlassCard } from '@/components/common/GlassCard'
import { FilterRail, type FilterGroup } from '@/components/common/FilterRail'
import { PermissionGuard } from '@/components/common/PermissionGuard'
import {
  getUsers, createUser, updateUser, deactivateUser, getUserSites, setUserSites,
  getEmployeeDocuments, uploadEmployeeDocument, deleteEmployeeDocument,
  uploadProfilePhoto,
} from '@/api/users'
import { profilePhotoUrl } from '@/api/attendance'
import { useAuthStore } from '@/store/auth'
import { listRoles } from '@/api/roles'
// Reused, not rebuilt: these have driven the Roster page's preference panel
// since Phase 2B, and the auto-scheduler already scores against them.
import { getPreferences, setPreferences } from '@/api/roster'
import { getUserSessions, revokeAllUserSessions, unlockUserAccount } from '@/api/sessions'
import { getSites } from '@/api/sites'
import type { User } from '@/types/api'
import { PageHeader } from '@/components/common/PageHeader'

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
            size="small" fullWidth slotProps={{ inputLabel: { shrink: true } }}
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
  // Lives on guard_shift_preferences, not users — so it saves through its own
  // endpoint alongside the profile write below.
  const [preferredShift, setPreferredShift] = useState('')

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

  // The preference is a separate record, so it needs its own read. Only for
  // an existing user: a preference cannot be stored against someone who does
  // not have an id yet, which is why this field is edit-only like pay.
  const { data: storedPrefs } = useQuery({
    queryKey: ['guard-preferences', editUser?.id],
    queryFn: () => getPreferences(editUser!.id),
    enabled: open && Boolean(editUser?.id),
  })

  useEffect(() => {
    setPreferredShift(storedPrefs?.preferred_shift_type ?? '')
  }, [storedPrefs?.preferred_shift_type, editUser?.id])

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
    mutationFn: async ({ id, data }: { id: string; data: Parameters<typeof updateUser>[1] }) => {
      const result = await updateUser(id, data)
      // Two writes because it is two records. Only sent when it actually
      // changed, so editing an admin's phone number does not create a shift
      // preference row for someone who will never stand a shift.
      if ((storedPrefs?.preferred_shift_type ?? '') !== preferredShift) {
        await setPreferences(id, { preferred_shift_type: preferredShift || null })
      }
      return result
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['users'] })
      queryClient.invalidateQueries({ queryKey: ['guard-preferences'] })
      onClose()
    },
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
            {/* The command office needs a face for a guard before they turn
                up — the one you most need to identify is the one who has not
                arrived. The check-in selfie can only answer that afterwards. */}
            <ProfilePhotoField user={editUser!} />
            <TextField label="Email" value={editUser!.email} size="small" fullWidth disabled />
            <TextField label="Full Name" value={fullName} onChange={(e) => setFullName(e.target.value)} size="small" fullWidth />
            <TextField label="Phone" value={phone} onChange={(e) => setPhone(e.target.value)} size="small" fullWidth />
            <TextField
              label="Date of Birth" type="date" value={dob} onChange={(e) => setDob(e.target.value)}
              size="small" fullWidth slotProps={{ inputLabel: { shrink: true } }}
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
              size="small" fullWidth slotProps={{ inputLabel: { shrink: true } }}
            />
            {/* Feeds the roster auto-scheduler directly: a guard whose
                preference matches a post scores higher for it, so setting this
                here shapes every roster generated afterwards. It is a
                preference, not a restriction — the scheduler will still post
                someone against it rather than leave a site unmanned. */}
            <FormControl size="small" fullWidth>
              <InputLabel>Preferred Shift</InputLabel>
              <Select
                value={preferredShift} label="Preferred Shift"
                onChange={(e) => setPreferredShift(e.target.value)}
              >
                <MenuItem value=""><em>No preference</em></MenuItem>
                <MenuItem value="day">Day duty</MenuItem>
                <MenuItem value="night">Night duty</MenuItem>
              </Select>
              <FormHelperText>
                The auto-scheduler favours matching shifts when building a roster.
              </FormHelperText>
            </FormControl>
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
              size="small" fullWidth slotProps={{ inputLabel: { shrink: true } }}
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

/** Upload / replace a guard's permanent profile photo. */
function ProfilePhotoField({ user }: { user: User }) {
  const token = useAuthStore((s) => s.accessToken)
  const queryClient = useQueryClient()
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  // Bumped after a successful upload: the URL is stable per user, so the
  // browser would otherwise keep serving the old cached image.
  const [version, setVersion] = useState(0)
  // Local record that this user now has a photo, replacing an assignment
  // straight onto the `user` prop. That object belongs to the react-query
  // cache and is shared with every other reader of ['users'] — writing to it
  // changed what they saw without re-rendering them, and left the cache
  // disagreeing with the server until something happened to refetch.
  const [justUploaded, setJustUploaded] = useState(false)

  const hasPhoto = Boolean(user.profile_photo_path) || justUploaded
  const src = hasPhoto ? `${profilePhotoUrl(user.id, token)}&v=${version}` : null

  const onPick = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    setBusy(true); setErr(null)
    try {
      await uploadProfilePhoto(user.id, file)
      // Shows the new photo at once; the invalidate then makes the cache
      // agree with the server rather than leaving it to be corrected later.
      setJustUploaded(true)
      setVersion((v) => v + 1)
      queryClient.invalidateQueries({ queryKey: ['users'] })
    } catch {
      setErr('Upload failed — use a JPEG, PNG or WebP image.')
    } finally {
      setBusy(false)
      e.target.value = ''               // allow re-picking the same file
    }
  }

  return (
    <Box sx={{ display: 'flex', gap: 2, alignItems: 'center' }}>
      <Avatar src={src ?? undefined} sx={{ width: 64, height: 64 }}>
        {(user.full_name ?? '?').slice(0, 1).toUpperCase()}
      </Avatar>
      <Box>
        <Button component="label" size="small" variant="outlined" disabled={busy}>
          {busy ? 'Uploading…' : hasPhoto ? 'Replace photo' : 'Upload photo'}
          <input hidden type="file" accept="image/jpeg,image/png,image/webp" onChange={onPick} />
        </Button>
        <Typography variant="caption" sx={{ display: 'block', mt: 0.5 }}
                    color={err ? 'error' : 'text.secondary'}>
          {err ?? 'Shown on the attendance board until the guard checks in.'}
        </Typography>
      </Box>
    </Box>
  )
}

export default function Users() {
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editUser, setEditUser] = useState<User | null>(null)
  const [sessionsUserId, setSessionsUserId] = useState<string | null>(null)
  const [siteAccessUser, setSiteAccessUser] = useState<User | null>(null)
  const queryClient = useQueryClient()

  const { data: users, isLoading } = useQuery({ queryKey: ['users'], queryFn: getUsers })

  // The site list comes from the sites API rather than from whichever sites
  // happen to appear on a user row: an admin looking for "who covers Jurong"
  // needs to be able to pick Jurong and see that the answer is nobody. Deriving
  // the options from the rows would hide exactly the site they are worried about.
  const { data: allSites = [] } = useQuery({ queryKey: ['sites'], queryFn: () => getSites() })

  const [search, setSearch] = useState('')
  const [siteFilter, setSiteFilter] = useState('')
  const [roleFilter, setRoleFilter] = useState('')
  const [employmentFilter, setEmploymentFilter] = useState('')
  const [statusFilter, setStatusFilter] = useState('')

  const filteredUsers = useMemo(() => {
    const q = search.trim().toLowerCase()
    return (users ?? []).filter((u) => {
      if (q) {
        // Phone is searchable because it is how a duty manager actually looks
        // someone up — they have the number from a call, not the spelling of
        // the name.
        const hay = [u.full_name, u.email, u.phone, u.designation]
          .filter(Boolean).join(' ').toLowerCase()
        if (!hay.includes(q)) return false
      }
      if (siteFilter) {
        const sites = u.site_names ?? []
        // "Unrestricted" is a real answer, not the absence of one — it is what
        // the table shows for a user tied to no site, and "show me everyone who
        // is not pinned to a site" is a question worth being able to ask.
        if (siteFilter === '__unrestricted__') {
          if (sites.length > 0) return false
        } else if (!sites.includes(siteFilter)) {
          return false
        }
      }
      if (roleFilter && String(u.role_id) !== roleFilter) return false
      if (employmentFilter && (u.employment_type ?? '') !== employmentFilter) return false
      if (statusFilter === 'active' && !u.is_active) return false
      if (statusFilter === 'inactive' && u.is_active) return false
      return true
    })
  }, [users, search, siteFilter, roleFilter, employmentFilter, statusFilter])

  const filterGroups: FilterGroup[] = [
    {
      key: 'site',
      label: 'Site',
      value: siteFilter,
      onChange: setSiteFilter,
      options: [
        { value: '', label: 'All sites' },
        { value: '__unrestricted__', label: 'Unrestricted' },
        ...allSites.map((s: any) => ({ value: s.name, label: s.name })),
      ],
    },
    {
      key: 'role',
      label: 'Role',
      value: roleFilter,
      onChange: setRoleFilter,
      options: [
        { value: '', label: 'All roles' },
        ...Object.keys(ROLE_LABELS).map(Number).sort((a, b) => a - b)
          .map((id) => ({ value: String(id), label: ROLE_LABELS[id] })),
      ],
    },
    {
      key: 'employment',
      label: 'Employment',
      value: employmentFilter,
      onChange: setEmploymentFilter,
      options: [
        { value: '', label: 'All types' },
        { value: 'full_time', label: 'Full-Time' },
        { value: 'part_time', label: 'Part-Time' },
        { value: 'contract', label: 'Contract' },
      ],
    },
    {
      key: 'status',
      label: 'Status',
      value: statusFilter,
      onChange: setStatusFilter,
      options: [
        { value: '', label: 'All' },
        { value: 'active', label: 'Active' },
        { value: 'inactive', label: 'Deactivated' },
      ],
    },
  ]

  const anyFilter = Boolean(search || siteFilter || roleFilter || employmentFilter || statusFilter)
  const clearFilters = () => {
    setSearch(''); setSiteFilter(''); setRoleFilter(''); setEmploymentFilter(''); setStatusFilter('')
  }

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

  // Group the already-fetched users by role for a quick "who's in charge"
  // view — no extra API call needed.
  //
  // Sorting by role_id is a display order, NOT a seniority ranking, however
  // much it looks like one. 1=Super Admin down to 7=Client reads as a
  // hierarchy right up to 8=Manager, which the roster service treats as
  // senior to Supervisor (MANAGER_ROLE_IDS = (1, 2, 8)). Anything that needs
  // to know who outranks whom has to use an explicit rank map, not this.
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
      <PageHeader pageKey="users" />
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

      {/* Search shares the row the Add User button already had, rather than
          taking a band of its own — the same reason the filters live in a rail
          instead of a chip row. */}
      <Stack direction="row" spacing={1.5} alignItems="center" sx={{ mb: 2 }}>
        <TextField
          size="small"
          placeholder="Search name, email or phone"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          sx={{ flex: 1, maxWidth: 360 }}
          slotProps={{
            input: {
              startAdornment: (
                <InputAdornment position="start"><SearchIcon fontSize="small" /></InputAdornment>
              ),
            },
          }}
        />
        {anyFilter && (
          <>
            <Typography variant="caption" color="text.secondary" sx={{ whiteSpace: 'nowrap' }}>
              {filteredUsers.length} of {users?.length ?? 0}
            </Typography>
            <Button size="small" variant="outlined" onClick={clearFilters}>Show All</Button>
          </>
        )}
        <Box sx={{ flex: 1 }} />
        <PermissionGuard permission="user:create">
          <Button startIcon={<AddIcon />} variant="contained" size="small" onClick={() => { setEditUser(null); setDialogOpen(true) }}>
            Add User
          </Button>
        </PermissionGuard>
      </Stack>

      <Box sx={{ display: 'flex', alignItems: 'flex-start', gap: 2 }}>
        <Box sx={{ flex: 1, minWidth: 0 }}>
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
                : filteredUsers.length === 0
                ? (
                    <TableRow>
                      <TableCell colSpan={GRID_COLUMN_COUNT} align="center" sx={{ py: 4 }}>
                        <Typography variant="body2" color="text.secondary">
                          {anyFilter ? 'No users match these filters.' : 'No users yet.'}
                        </Typography>
                        {anyFilter && (
                          <Button size="small" sx={{ mt: 1 }} onClick={clearFilters}>Show All</Button>
                        )}
                      </TableCell>
                    </TableRow>
                  )
                : filteredUsers.map((user) => {
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
        </Box>
        <FilterRail groups={filterGroups} storageKey="users" />
      </Box>

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
