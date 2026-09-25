/**
 * Drone Patrol — mission list. A mission is a drone, a route, a security
 * profile and schedules at one site; running it asks pre-flight first.
 */
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Alert, Box, Button, Skeleton, Switch, Table, TableBody, TableCell, TableContainer, TableHead, TableRow,
  Typography,
} from '@mui/material'
import AddIcon from '@mui/icons-material/Add'
import PlayArrowIcon from '@mui/icons-material/PlayArrow'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import Stack from '@/components/common/Stack'
import { GlassCard } from '@/components/common/GlassCard'
import { PageHeader } from '@/components/common/PageHeader'
import { usePermission } from '@/hooks/usePermission'
import { apiError, listMissions, runMission, setMissionEnabled } from '@/api/drones'
import { LicenceBanner, SessionStatusChip } from '@/components/drones/droneUi'
import { fmt, pretty, useDroneRealtime } from '@/components/drones/droneFormat'
import { DroneNav } from './DroneNav'

export default function DroneMissions() {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const canCreate = usePermission('drone:mission:create')
  const canUpdate = usePermission('drone:mission:update')
  const canRun = usePermission('drone:mission:execute')
  const [notice, setNotice] = useState<{ ok: boolean; text: string; session?: string } | null>(null)
  useDroneRealtime([['drone-missions']])
  const { data, isLoading } = useQuery({ queryKey: ['drone-missions'], queryFn: () => listMissions(),
                                          refetchInterval: 20_000 })
  const toggle = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) => setMissionEnabled(id, enabled),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['drone-missions'] }),
  })
  const run = useMutation({
    mutationFn: (id: string) => runMission(id),
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ['drone-missions'] })
      setNotice(r.session.status === 'BLOCKED'
        ? { ok: false, text: `${r.mission_name} was blocked by pre-flight: ${r.session.blocked_reason ?? ''}` }
        : { ok: true, text: `${r.mission_name} is ready to launch.`, session: r.session.id })
    },
    onError: (e) => setNotice({ ok: false, text: apiError(e) }),
  })

  const missions = data?.items ?? []
  return (
    <Box sx={{ p: 3 }}>
      <PageHeader title="Drone Missions" subtitle="What each drone patrols, when, and how it judges what it sees" />
      <DroneNav />
      <LicenceBanner />
      {notice && (
        <Alert severity={notice.ok ? 'success' : 'warning'} sx={{ mb: 2 }} onClose={() => setNotice(null)}
               action={notice.session ? <Button color="inherit" size="small"
                                                onClick={() => navigate(`/drone-patrols/${notice.session}`)}>Watch</Button> : undefined}>
          {notice.text}
        </Alert>
      )}
      <GlassCard sx={{ p: 2 }}>
        <Stack direction="row" sx={{ justifyContent: 'flex-end', mb: 1 }}>
          {canCreate && <Button startIcon={<AddIcon />} variant="contained" onClick={() => navigate('/drone-missions/new')}>
            New mission</Button>}
        </Stack>
        {isLoading ? <Skeleton height={200} /> : !missions.length ? (
          <Alert severity="info">No missions yet. A mission needs a drone, a route and, ideally, a security profile.</Alert>
        ) : (
          <TableContainer>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>Mission</TableCell><TableCell>Site</TableCell><TableCell>Drone</TableCell>
                  <TableCell>Route</TableCell><TableCell>Security profile</TableCell><TableCell>Schedule</TableCell>
                  <TableCell>Last run</TableCell><TableCell>Next run</TableCell><TableCell>Enabled</TableCell>
                  <TableCell align="right" />
                </TableRow>
              </TableHead>
              <TableBody>
                {missions.map((m) => (
                  <TableRow key={m.id} hover sx={{ cursor: 'pointer' }} onClick={() => navigate(`/drone-missions/${m.id}`)}>
                    <TableCell><Typography variant="body2" sx={{ fontWeight: 600 }}>{m.name}</Typography>
                      {m.in_flight && <Typography variant="caption" color="primary">in flight</Typography>}</TableCell>
                    <TableCell>{m.site_name}</TableCell>
                    <TableCell>{m.drone_name ?? <Typography variant="caption" color="error">none</Typography>}
                      {m.drone_status && <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
                        {pretty(m.drone_status)}</Typography>}</TableCell>
                    <TableCell>{m.route_name ?? <Typography variant="caption" color="error">none</Typography>}</TableCell>
                    <TableCell>{m.profile_name ?? <Typography variant="caption" color="text.secondary">defaults</Typography>}</TableCell>
                    <TableCell>{m.schedule_count ? `${m.schedule_count} schedule(s)` : 'manual only'}</TableCell>
                    <TableCell>
                      {m.last_session_status ? <SessionStatusChip status={m.last_session_status} /> : '—'}
                      {m.last_session_at && <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
                        {fmt(m.last_session_at)}</Typography>}
                    </TableCell>
                    <TableCell>{m.next_run ? new Date(m.next_run.utc).toLocaleString() : '—'}</TableCell>
                    <TableCell onClick={(e) => e.stopPropagation()}>
                      <Switch size="small" checked={m.enabled} disabled={!canUpdate || toggle.isPending}
                              onChange={(_, v) => toggle.mutate({ id: m.id, enabled: v })} />
                    </TableCell>
                    <TableCell align="right" onClick={(e) => e.stopPropagation()}>
                      {canRun && (
                        <Button size="small" startIcon={<PlayArrowIcon />} disabled={!m.enabled || m.in_flight || run.isPending}
                                onClick={() => run.mutate(m.id)}>Run now</Button>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
        )}
      </GlassCard>
    </Box>
  )
}
