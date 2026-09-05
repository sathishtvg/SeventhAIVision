import { useMemo, useState } from 'react'
import {
  Box, Button, Chip, MenuItem, Paper, Skeleton, Table, TableBody, TableCell,
  TableContainer, TableHead, TableRow, TextField, ToggleButton, Tooltip, Typography,
} from '@mui/material'
import DirectionsCarIcon from '@mui/icons-material/DirectionsCar'
import RefreshIcon from '@mui/icons-material/Refresh'
import FullscreenIcon from '@mui/icons-material/Fullscreen'
import TimerOffIcon from '@mui/icons-material/TimerOff'
import { useQuery } from '@tanstack/react-query'
import Stack from '@/components/common/Stack'
import { PageHeader } from '@/components/common/PageHeader'
import {
  listOnsiteVehicles, VISIT_TYPES, VISIT_TYPE_LABELS,
  type OnsiteVehicle, type VisitType,
} from '@/api/vms'
import { RegisterVisitorDialog } from '@/components/vms/RegisterVisitorDialog'
import PersonAddIcon from '@mui/icons-material/PersonAdd'
import { getSites } from '@/api/sites'
import { useKioskToggle } from '@/hooks/useKioskToggle'

/** Human-readable time on site. Minutes alone stop being readable somewhere
 * around the two-hour mark, which is exactly the range an overstaying vehicle
 * lives in — so this is where a guard needs it clearest. */
function formatDuration(minutes: number): string {
  const m = Math.max(0, Math.round(minutes))
  if (m < 60) return `${m}m`
  const h = Math.floor(m / 60)
  return `${h}h ${m % 60}m`
}

function formatEntry(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  return d.toLocaleString(undefined, {
    day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit',
  })
}

/** Expiry is derived, not stored: entry + the site's allowance. A site that
 * doesn't meter parking has no allowance and therefore no expiry — showing a
 * fabricated one there would be worse than showing none. */
function expiryLabel(v: OnsiteVehicle): { text: string; remaining: number | null } {
  // No vehicle means no parking clock, whatever the site's allowance says.
  if (v.vehicle_entry_at == null) return { text: '—', remaining: null }
  if (v.allowance_minutes == null) return { text: 'No limit', remaining: null }
  const remaining = v.allowance_minutes - v.minutes_on_site
  const due = new Date(new Date(v.vehicle_entry_at).getTime() + v.allowance_minutes * 60_000)
  const clock = due.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
  return { text: clock, remaining }
}

export default function VmsOnsite() {
  const [siteId, setSiteId] = useState('')
  const [overstayedOnly, setOverstayedOnly] = useState(false)
  const [visitType, setVisitType] = useState<VisitType | ''>('')
  const [registerOpen, setRegisterOpen] = useState(false)
  const { kiosk, toggleKiosk } = useKioskToggle()

  const { data: sites } = useQuery({ queryKey: ['sites'], queryFn: () => getSites() })

  // Only VMS-enabled sites belong in this filter — offering a site that
  // doesn't run visitor management would just produce a permanently empty
  // grid. getSites() is already narrowed server-side to the sites this user
  // is assigned to, so a guard sees only their own posting.
  const vmsSites = useMemo(
    () => (sites ?? []).filter((s) => s.vms_enabled),
    [sites],
  )

  const { data, isLoading, isFetching, refetch } = useQuery({
    queryKey: ['vms-onsite', siteId, overstayedOnly, visitType],
    queryFn: () => listOnsiteVehicles(siteId || undefined, overstayedOnly, visitType || undefined),
    refetchInterval: 30_000,
  })

  const rows = data ?? []
  const overstayCount = rows.filter((r) => r.is_overstayed).length

  return (
    <Box>
      <PageHeader
        title="Visitors On Site"
        subtitle="Everyone currently on site — walk-ins, deliveries and vehicles — with arrival time and parking expiry"
        action={
          <Stack direction="row" spacing={1} alignItems="center">
            {/* The gatehouse's primary action, so it leads and is the only
                contained button here. Every site starts without an entry LPR
                camera, which makes registering by hand the normal path, not
                the fallback. */}
            <Button
              size="small"
              variant="contained"
              startIcon={<PersonAddIcon />}
              onClick={() => setRegisterOpen(true)}
            >
              Register
            </Button>
            <Tooltip title="Refresh now">
              <span>
                <Button
                  size="small"
                  variant="outlined"
                  startIcon={<RefreshIcon />}
                  onClick={() => refetch()}
                  disabled={isFetching}
                >
                  Refresh
                </Button>
              </span>
            </Tooltip>
            {/* Hidden once full screen: AppShell's focus-mode strip already
                draws Back and Exit, and this page drew its own Exit in the same
                corner — two Exit Full Screen buttons on the same screen. */}
            {!kiosk && (
              <Tooltip title="Full screen for the gatehouse display">
                <Button
                  size="small"
                  variant="outlined"
                  startIcon={<FullscreenIcon />}
                  onClick={toggleKiosk}
                >
                  Full Screen
                </Button>
              </Tooltip>
            )}
          </Stack>
        }
      />

      <Stack direction="row" spacing={2} alignItems="center" sx={{ mb: 2, flexWrap: 'wrap' }}>
        <TextField
          select
          size="small"
          label="Site"
          value={siteId}
          onChange={(e) => setSiteId(e.target.value)}
          sx={{ minWidth: 220 }}
        >
          <MenuItem value="">All sites</MenuItem>
          {vmsSites.map((s) => (
            <MenuItem key={s.id} value={s.id}>{s.name}</MenuItem>
          ))}
        </TextField>
        <TextField
          select
          size="small"
          label="Visit type"
          value={visitType}
          onChange={(e) => setVisitType(e.target.value as VisitType | '')}
          sx={{ minWidth: 170 }}
        >
          <MenuItem value="">All types</MenuItem>
          {VISIT_TYPES.map((tt) => (
            <MenuItem key={tt} value={tt}>{VISIT_TYPE_LABELS[tt]}</MenuItem>
          ))}
        </TextField>
        <Tooltip title="Show only vehicles past their allowed parking time">
          <ToggleButton
            size="small"
            value="overstayed"
            selected={overstayedOnly}
            onChange={() => setOverstayedOnly((v) => !v)}
            color="error"
          >
            <TimerOffIcon fontSize="small" sx={{ mr: 0.5 }} />
            Overstayed only
          </ToggleButton>
        </Tooltip>
        <Box sx={{ flexGrow: 1 }} />
        <Chip
          icon={<DirectionsCarIcon />}
          label={`${rows.length} on site`}
          color="primary"
          variant="outlined"
        />
        {overstayCount > 0 && (
          <Chip label={`${overstayCount} overstayed`} color="error" />
        )}
      </Stack>

      <TableContainer component={Paper}>
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell>Type</TableCell>
              <TableCell>Plate</TableCell>
              <TableCell>Visitor</TableCell>
              <TableCell>Company</TableCell>
              <TableCell>Site</TableCell>
              <TableCell>Entry</TableCell>
              <TableCell>On site</TableCell>
              <TableCell>Expires</TableCell>
              <TableCell>Status</TableCell>
              <TableCell>Registered by</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {isLoading &&
              Array.from({ length: 5 }).map((_, i) => (
                <TableRow key={i}>
                  {Array.from({ length: 10 }).map((__, j) => (
                    <TableCell key={j}><Skeleton /></TableCell>
                  ))}
                </TableRow>
              ))}
            {!isLoading && rows.length === 0 && (
              <TableRow>
                <TableCell colSpan={10}>
                  <Typography variant="body2" color="text.secondary" sx={{ py: 3, textAlign: 'center' }}>
                    {overstayedOnly ? 'No vehicles are overstaying.' : 'No visitor vehicles on site.'}
                  </Typography>
                </TableCell>
              </TableRow>
            )}
            {rows.map((v) => {
              const exp = expiryLabel(v)
              return (
                <TableRow
                  key={v.id}
                  hover
                  sx={v.is_overstayed ? { bgcolor: 'rgba(255,69,96,0.08)' } : undefined}
                >
                  <TableCell>
                    <Chip size="small" variant="outlined" label={VISIT_TYPE_LABELS[v.visit_type] ?? v.visit_type} />
                  </TableCell>
                  <TableCell>
                    <Typography sx={{ fontWeight: 700, letterSpacing: '0.04em' }}>
                      {v.vehicle_plate ?? '—'}
                    </Typography>
                  </TableCell>
                  <TableCell>{v.full_name}</TableCell>
                  <TableCell>{v.company ?? '—'}</TableCell>
                  <TableCell>{v.site_name ?? '—'}</TableCell>
                  <TableCell>{formatEntry(v.arrived_at)}</TableCell>
                  <TableCell>{formatDuration(v.minutes_on_site)}</TableCell>
                  <TableCell>
                    {exp.text}
                    {exp.remaining != null && (
                      <Typography
                        variant="caption"

                        color={exp.remaining < 0 ? 'error.main' : 'text.secondary'} sx={{ display: "block" }}
                      >
                        {exp.remaining < 0
                          ? `${formatDuration(-exp.remaining)} over`
                          : `${formatDuration(exp.remaining)} left`}
                      </Typography>
                    )}
                  </TableCell>
                  <TableCell>
                    <Chip
                      size="small"
                      label={v.is_overstayed ? 'Overstayed' : 'On site'}
                      color={v.is_overstayed ? 'error' : 'success'}
                    />
                  </TableCell>
                  <TableCell>
                    <Typography variant="caption" color="text.secondary">
                      {v.registered_by ?? 'LPR'}
                    </Typography>
                  </TableCell>
                </TableRow>
              )
            })}
          </TableBody>
        </Table>
      </TableContainer>
      <RegisterVisitorDialog
        open={registerOpen}
        onClose={() => setRegisterOpen(false)}
        defaultSiteId={siteId || undefined}
      />
    </Box>
  )
}
