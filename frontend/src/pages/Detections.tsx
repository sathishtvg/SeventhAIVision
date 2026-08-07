import { useState } from 'react'
import {
  Box, Tabs, Tab, Typography, Table, TableBody, TableCell, TableContainer,
  TableHead, TableRow, Chip, Skeleton, Paper, LinearProgress,
} from '@mui/material'
import { useQuery } from '@tanstack/react-query'
import { GlassCard } from '@/components/common/GlassCard'
import { EvidenceThumb } from '@/components/common/EvidenceThumb'
import {
  getLprEvents, getFaceEvents, getIntrusionEvents,
  getPpeEvents, getCrowdEvents, getFireSmokeEvents, getWeaponEvents, getBehaviorEvents,
  getTamperingEvents, getAbandonedEvents, getFallEvents,
} from '@/api/detections'

interface TabPanelProps {
  children: React.ReactNode
  value: number
  index: number
}

function TabPanel({ children, value, index }: TabPanelProps) {
  return <Box hidden={value !== index}>{value === index && children}</Box>
}

function SkeletonRows({ cols, rows = 5 }: { cols: number; rows?: number }) {
  return (
    <>
      {Array.from({ length: rows }).map((_, i) => (
        <TableRow key={i}>
          {Array.from({ length: cols }).map((__, j) => <TableCell key={j}><Skeleton /></TableCell>)}
        </TableRow>
      ))}
    </>
  )
}

// ──────────────────────────────────────────────────────────
// Phase 1 tables
// ──────────────────────────────────────────────────────────

function LprTable() {
  const { data, isLoading } = useQuery({ queryKey: ['lpr-events'], queryFn: () => getLprEvents(undefined, 100) })
  return (
    <TableContainer component={Paper} elevation={0} sx={{ background: 'transparent' }}>
      <Table size="small">
        <TableHead>
          <TableRow>
            <TableCell>Plate</TableCell>
            <TableCell>Confidence</TableCell>
            <TableCell>Watchlist</TableCell>
            <TableCell>Direction</TableCell>
            <TableCell>Vehicle</TableCell>
            <TableCell>Proof</TableCell>
            <TableCell>Time</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {isLoading
            ? <SkeletonRows cols={7} />
            : data?.map((e) => (
                <TableRow key={e.detection_id} hover>
                  <TableCell><Typography variant="body2" sx={{ fontWeight: 700 }}>{e.plate_number}</Typography></TableCell>
                  <TableCell>{e.plate_confidence ? `${(e.plate_confidence * 100).toFixed(1)}%` : '—'}</TableCell>
                  <TableCell>
                    {e.watchlist_match
                      ? <Chip label={e.watchlist_match} size="small" color={e.watchlist_match === 'block' ? 'error' : 'success'} />
                      : '—'}
                  </TableCell>
                  <TableCell>{e.direction ?? '—'}</TableCell>
                  <TableCell>{[e.vehicle_type, e.vehicle_color].filter(Boolean).join(', ') || '—'}</TableCell>
                  <TableCell><EvidenceThumb frameEvidenceId={e.frame_evidence_id} plateEvidenceId={e.plate_evidence_id} /></TableCell>
                  <TableCell><Typography variant="caption" color="text.secondary">{new Date(e.created_at).toLocaleString()}</Typography></TableCell>
                </TableRow>
              ))}
        </TableBody>
      </Table>
    </TableContainer>
  )
}

function FaceTable() {
  const { data, isLoading } = useQuery({ queryKey: ['face-events'], queryFn: () => getFaceEvents(100) })
  return (
    <TableContainer component={Paper} elevation={0} sx={{ background: 'transparent' }}>
      <Table size="small">
        <TableHead>
          <TableRow>
            <TableCell>Detection ID</TableCell>
            <TableCell>Watchlist</TableCell>
            <TableCell>Match Confidence</TableCell>
            <TableCell>Proof</TableCell>
            <TableCell>Time</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {isLoading
            ? <SkeletonRows cols={5} />
            : data?.map((e) => (
                <TableRow key={e.detection_id} hover>
                  <TableCell><Typography variant="caption" sx={{ fontFamily: 'monospace' }}>{e.detection_id.slice(0, 8)}…</Typography></TableCell>
                  <TableCell>
                    {e.watchlist_match
                      ? <Chip label={e.watchlist_match} size="small" color={e.watchlist_match === 'block' ? 'error' : 'success'} />
                      : <Chip label="unrecognized" size="small" variant="outlined" />}
                  </TableCell>
                  <TableCell>{e.match_confidence ? `${(e.match_confidence * 100).toFixed(1)}%` : '—'}</TableCell>
                  <TableCell><EvidenceThumb frameEvidenceId={e.frame_evidence_id} plateEvidenceId={e.plate_evidence_id} /></TableCell>
                  <TableCell><Typography variant="caption" color="text.secondary">{new Date(e.created_at).toLocaleString()}</Typography></TableCell>
                </TableRow>
              ))}
        </TableBody>
      </Table>
    </TableContainer>
  )
}

function IntrusionTable() {
  const { data, isLoading } = useQuery({ queryKey: ['intrusion-events'], queryFn: () => getIntrusionEvents(100) })
  return (
    <TableContainer component={Paper} elevation={0} sx={{ background: 'transparent' }}>
      <Table size="small">
        <TableHead>
          <TableRow>
            <TableCell>Detection ID</TableCell>
            <TableCell>Zone ID</TableCell>
            <TableCell>Dwell Time</TableCell>
            <TableCell>Proof</TableCell>
            <TableCell>Time</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {isLoading
            ? <SkeletonRows cols={5} />
            : data?.map((e) => (
                <TableRow key={e.detection_id} hover>
                  <TableCell><Typography variant="caption" sx={{ fontFamily: 'monospace' }}>{e.detection_id.slice(0, 8)}…</Typography></TableCell>
                  <TableCell><Typography variant="caption" sx={{ fontFamily: 'monospace' }}>{e.zone_id.slice(0, 8)}…</Typography></TableCell>
                  <TableCell>{e.dwell_time_seconds != null ? `${e.dwell_time_seconds.toFixed(1)}s` : '—'}</TableCell>
                  <TableCell><EvidenceThumb frameEvidenceId={e.frame_evidence_id} plateEvidenceId={e.plate_evidence_id} /></TableCell>
                  <TableCell><Typography variant="caption" color="text.secondary">{new Date(e.created_at).toLocaleString()}</Typography></TableCell>
                </TableRow>
              ))}
        </TableBody>
      </Table>
    </TableContainer>
  )
}

// ──────────────────────────────────────────────────────────
// Phase 3 tables
// ──────────────────────────────────────────────────────────

function PpeTable() {
  const { data, isLoading } = useQuery({ queryKey: ['ppe-events'], queryFn: () => getPpeEvents(100) })
  return (
    <TableContainer component={Paper} elevation={0} sx={{ background: 'transparent' }}>
      <Table size="small">
        <TableHead>
          <TableRow>
            <TableCell>Detection ID</TableCell>
            <TableCell>Missing Items</TableCell>
            <TableCell>Detected Items</TableCell>
            <TableCell>Severity</TableCell>
            <TableCell>Proof</TableCell>
            <TableCell>Time</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {isLoading
            ? <SkeletonRows cols={6} />
            : data?.map((e) => (
                <TableRow key={e.detection_id} hover>
                  <TableCell><Typography variant="caption" sx={{ fontFamily: 'monospace' }}>{e.detection_id.slice(0, 8)}…</Typography></TableCell>
                  <TableCell>
                    <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.5 }}>
                      {e.items_missing.map((item) => (
                        <Chip key={item} label={item.replace('_', ' ')} size="small" color="error" variant="outlined" />
                      ))}
                    </Box>
                  </TableCell>
                  <TableCell>
                    <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.5 }}>
                      {e.items_detected.map((item) => (
                        <Chip key={item} label={item.replace('_', ' ')} size="small" color="success" variant="outlined" />
                      ))}
                    </Box>
                  </TableCell>
                  <TableCell><Chip label={e.severity} size="small" color="warning" /></TableCell>
                  <TableCell><EvidenceThumb frameEvidenceId={e.frame_evidence_id} plateEvidenceId={e.plate_evidence_id} /></TableCell>
                  <TableCell><Typography variant="caption" color="text.secondary">{new Date(e.created_at).toLocaleString()}</Typography></TableCell>
                </TableRow>
              ))}
        </TableBody>
      </Table>
    </TableContainer>
  )
}

function CrowdTable() {
  const { data, isLoading } = useQuery({ queryKey: ['crowd-events'], queryFn: () => getCrowdEvents(undefined, 100) })
  return (
    <TableContainer component={Paper} elevation={0} sx={{ background: 'transparent' }}>
      <Table size="small">
        <TableHead>
          <TableRow>
            <TableCell>Detection ID</TableCell>
            <TableCell>Zone</TableCell>
            <TableCell>Count / Capacity</TableCell>
            <TableCell>Density</TableCell>
            <TableCell>Proof</TableCell>
            <TableCell>Time</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {isLoading
            ? <SkeletonRows cols={6} />
            : data?.map((e) => (
                <TableRow key={e.detection_id} hover>
                  <TableCell><Typography variant="caption" sx={{ fontFamily: 'monospace' }}>{e.detection_id.slice(0, 8)}…</Typography></TableCell>
                  <TableCell><Typography variant="caption" sx={{ fontFamily: 'monospace' }}>{e.zone_id.slice(0, 8)}…</Typography></TableCell>
                  <TableCell><Typography variant="body2">{e.person_count} / {e.max_capacity}</Typography></TableCell>
                  <TableCell>
                    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, minWidth: 100 }}>
                      <LinearProgress
                        variant="determinate"
                        value={Math.min(e.density_ratio * 100, 100)}
                        color={e.density_ratio >= 1 ? 'error' : e.density_ratio >= 0.8 ? 'warning' : 'success'}
                        sx={{ flex: 1, height: 6, borderRadius: 3 }}
                      />
                      <Typography variant="caption">{(e.density_ratio * 100).toFixed(0)}%</Typography>
                    </Box>
                  </TableCell>
                  <TableCell><EvidenceThumb frameEvidenceId={e.frame_evidence_id} plateEvidenceId={e.plate_evidence_id} /></TableCell>
                  <TableCell><Typography variant="caption" color="text.secondary">{new Date(e.created_at).toLocaleString()}</Typography></TableCell>
                </TableRow>
              ))}
        </TableBody>
      </Table>
    </TableContainer>
  )
}

function FireSmokeTable() {
  const { data, isLoading } = useQuery({ queryKey: ['fire-smoke-events'], queryFn: () => getFireSmokeEvents(undefined, 100) })
  return (
    <TableContainer component={Paper} elevation={0} sx={{ background: 'transparent' }}>
      <Table size="small">
        <TableHead>
          <TableRow>
            <TableCell>Detection ID</TableCell>
            <TableCell>Type</TableCell>
            <TableCell>Confidence</TableCell>
            <TableCell>Proof</TableCell>
            <TableCell>Time</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {isLoading
            ? <SkeletonRows cols={5} />
            : data?.map((e) => (
                <TableRow key={e.detection_id} hover>
                  <TableCell><Typography variant="caption" sx={{ fontFamily: 'monospace' }}>{e.detection_id.slice(0, 8)}…</Typography></TableCell>
                  <TableCell>
                    <Chip
                      label={e.detection_type}
                      size="small"
                      color={e.detection_type === 'fire' ? 'error' : 'warning'}
                    />
                  </TableCell>
                  <TableCell>{`${(e.confidence * 100).toFixed(1)}%`}</TableCell>
                  <TableCell><EvidenceThumb frameEvidenceId={e.frame_evidence_id} plateEvidenceId={e.plate_evidence_id} /></TableCell>
                  <TableCell><Typography variant="caption" color="text.secondary">{new Date(e.created_at).toLocaleString()}</Typography></TableCell>
                </TableRow>
              ))}
        </TableBody>
      </Table>
    </TableContainer>
  )
}

function WeaponTable() {
  const { data, isLoading } = useQuery({ queryKey: ['weapon-events'], queryFn: () => getWeaponEvents(undefined, 100) })
  return (
    <TableContainer component={Paper} elevation={0} sx={{ background: 'transparent' }}>
      <Table size="small">
        <TableHead>
          <TableRow>
            <TableCell>Detection ID</TableCell>
            <TableCell>Weapon Type</TableCell>
            <TableCell>Confidence</TableCell>
            <TableCell>Proof</TableCell>
            <TableCell>Time</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {isLoading
            ? <SkeletonRows cols={5} />
            : data?.map((e) => (
                <TableRow key={e.detection_id} hover>
                  <TableCell><Typography variant="caption" sx={{ fontFamily: 'monospace' }}>{e.detection_id.slice(0, 8)}…</Typography></TableCell>
                  <TableCell>
                    <Chip
                      label={e.weapon_type}
                      size="small"
                      color={e.weapon_type === 'firearm' ? 'error' : e.weapon_type === 'blade' ? 'warning' : 'default'}
                    />
                  </TableCell>
                  <TableCell>{`${(e.confidence * 100).toFixed(1)}%`}</TableCell>
                  <TableCell><EvidenceThumb frameEvidenceId={e.frame_evidence_id} plateEvidenceId={e.plate_evidence_id} /></TableCell>
                  <TableCell><Typography variant="caption" color="text.secondary">{new Date(e.created_at).toLocaleString()}</Typography></TableCell>
                </TableRow>
              ))}
        </TableBody>
      </Table>
    </TableContainer>
  )
}

function BehaviorTable() {
  const { data, isLoading } = useQuery({ queryKey: ['behavior-events'], queryFn: () => getBehaviorEvents(undefined, 100) })
  const BEHAVIOR_COLORS: Record<string, 'error' | 'warning' | 'info' | 'default'> = {
    aggression: 'error',
    tailgating: 'warning',
    loitering: 'info',
    running: 'default',
  }
  return (
    <TableContainer component={Paper} elevation={0} sx={{ background: 'transparent' }}>
      <Table size="small">
        <TableHead>
          <TableRow>
            <TableCell>Detection ID</TableCell>
            <TableCell>Behavior</TableCell>
            <TableCell>Duration</TableCell>
            <TableCell>Confidence</TableCell>
            <TableCell>Proof</TableCell>
            <TableCell>Time</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {isLoading
            ? <SkeletonRows cols={6} />
            : data?.map((e) => (
                <TableRow key={e.detection_id} hover>
                  <TableCell><Typography variant="caption" sx={{ fontFamily: 'monospace' }}>{e.detection_id.slice(0, 8)}…</Typography></TableCell>
                  <TableCell>
                    <Chip
                      label={e.behavior_type.replace('_', ' ')}
                      size="small"
                      color={BEHAVIOR_COLORS[e.behavior_type] ?? 'default'}
                    />
                  </TableCell>
                  <TableCell>{e.duration_seconds != null ? `${e.duration_seconds.toFixed(1)}s` : '—'}</TableCell>
                  <TableCell>{e.confidence != null ? `${(e.confidence * 100).toFixed(1)}%` : '—'}</TableCell>
                  <TableCell><EvidenceThumb frameEvidenceId={e.frame_evidence_id} plateEvidenceId={e.plate_evidence_id} /></TableCell>
                  <TableCell><Typography variant="caption" color="text.secondary">{new Date(e.created_at).toLocaleString()}</Typography></TableCell>
                </TableRow>
              ))}
        </TableBody>
      </Table>
    </TableContainer>
  )
}

function TamperingTable() {
  const { data, isLoading } = useQuery({ queryKey: ['tampering-events'], queryFn: () => getTamperingEvents(undefined, 100) })
  return (
    <TableContainer component={Paper} elevation={0} sx={{ background: 'transparent' }}>
      <Table size="small">
        <TableHead>
          <TableRow>
            <TableCell>Camera</TableCell>
            <TableCell>Type</TableCell>
            <TableCell>Score</TableCell>
            <TableCell>Reason</TableCell>
            <TableCell>Proof</TableCell>
            <TableCell>Time</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {isLoading
            ? <SkeletonRows cols={6} />
            : data?.map((e) => (
                <TableRow key={e.detection_id} hover>
                  <TableCell>{e.camera_name ?? e.camera_id.slice(0, 8)}</TableCell>
                  <TableCell><Chip label={e.tampering_type.replace('_', ' ')} size="small" color="warning" /></TableCell>
                  <TableCell>{e.score != null ? `${(e.score * 100).toFixed(1)}%` : '—'}</TableCell>
                  <TableCell><Typography variant="caption">{e.reason ?? '—'}</Typography></TableCell>
                  <TableCell><EvidenceThumb frameEvidenceId={e.frame_evidence_id} plateEvidenceId={e.plate_evidence_id} /></TableCell>
                  <TableCell><Typography variant="caption" color="text.secondary">{new Date(e.detected_at).toLocaleString()}</Typography></TableCell>
                </TableRow>
              ))}
        </TableBody>
      </Table>
    </TableContainer>
  )
}

function AbandonedTable() {
  const { data, isLoading } = useQuery({ queryKey: ['abandoned-events'], queryFn: () => getAbandonedEvents(undefined, 100) })
  return (
    <TableContainer component={Paper} elevation={0} sx={{ background: 'transparent' }}>
      <Table size="small">
        <TableHead>
          <TableRow>
            <TableCell>Camera</TableCell>
            <TableCell>Object Class</TableCell>
            <TableCell>Dwell Time</TableCell>
            <TableCell>Proof</TableCell>
            <TableCell>Time</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {isLoading
            ? <SkeletonRows cols={5} />
            : data?.map((e) => (
                <TableRow key={e.detection_id} hover>
                  <TableCell>{e.camera_name ?? e.camera_id.slice(0, 8)}</TableCell>
                  <TableCell><Chip label={e.object_class.replace('_', ' ')} size="small" /></TableCell>
                  <TableCell>{e.dwell_seconds != null ? `${e.dwell_seconds.toFixed(1)}s` : '—'}</TableCell>
                  <TableCell><EvidenceThumb frameEvidenceId={e.frame_evidence_id} plateEvidenceId={e.plate_evidence_id} /></TableCell>
                  <TableCell><Typography variant="caption" color="text.secondary">{new Date(e.detected_at).toLocaleString()}</Typography></TableCell>
                </TableRow>
              ))}
        </TableBody>
      </Table>
    </TableContainer>
  )
}

function FallTable() {
  const { data, isLoading } = useQuery({ queryKey: ['fall-events'], queryFn: () => getFallEvents(undefined, 100) })
  return (
    <TableContainer component={Paper} elevation={0} sx={{ background: 'transparent' }}>
      <Table size="small">
        <TableHead>
          <TableRow>
            <TableCell>Camera</TableCell>
            <TableCell>Confidence</TableCell>
            <TableCell>Proof</TableCell>
            <TableCell>Time</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {isLoading
            ? <SkeletonRows cols={4} />
            : data?.map((e) => (
                <TableRow key={e.detection_id} hover>
                  <TableCell>{e.camera_name ?? e.camera_id.slice(0, 8)}</TableCell>
                  <TableCell>
                    <Chip
                      label={e.fall_confidence != null ? `${(e.fall_confidence * 100).toFixed(1)}%` : 'Unknown'}
                      size="small"
                      color={e.fall_confidence != null && e.fall_confidence >= 0.8 ? 'error' : 'warning'}
                    />
                  </TableCell>
                  <TableCell><EvidenceThumb frameEvidenceId={e.frame_evidence_id} plateEvidenceId={e.plate_evidence_id} /></TableCell>
                  <TableCell><Typography variant="caption" color="text.secondary">{new Date(e.detected_at).toLocaleString()}</Typography></TableCell>
                </TableRow>
              ))}
        </TableBody>
      </Table>
    </TableContainer>
  )
}

export default function Detections() {
  const [tab, setTab] = useState(0)

  return (
    <Box>
      <GlassCard>
        <Box sx={{ borderBottom: 1, borderColor: 'rgba(255,255,255,0.1)' }}>
          <Tabs value={tab} onChange={(_, v) => setTab(v)} variant="scrollable" scrollButtons="auto">
            <Tab label="LPR Events" />
            <Tab label="Face Events" />
            <Tab label="Intrusion" />
            <Tab label="PPE" />
            <Tab label="Crowd" />
            <Tab label="Fire / Smoke" />
            <Tab label="Weapon" />
            <Tab label="Behavior" />
            <Tab label="Tampering" />
            <Tab label="Abandoned" />
            <Tab label="Falls" />
          </Tabs>
        </Box>
        <TabPanel value={tab} index={0}><LprTable /></TabPanel>
        <TabPanel value={tab} index={1}><FaceTable /></TabPanel>
        <TabPanel value={tab} index={2}><IntrusionTable /></TabPanel>
        <TabPanel value={tab} index={3}><PpeTable /></TabPanel>
        <TabPanel value={tab} index={4}><CrowdTable /></TabPanel>
        <TabPanel value={tab} index={5}><FireSmokeTable /></TabPanel>
        <TabPanel value={tab} index={6}><WeaponTable /></TabPanel>
        <TabPanel value={tab} index={7}><BehaviorTable /></TabPanel>
        <TabPanel value={tab} index={8}><TamperingTable /></TabPanel>
        <TabPanel value={tab} index={9}><AbandonedTable /></TabPanel>
        <TabPanel value={tab} index={10}><FallTable /></TabPanel>
      </GlassCard>
    </Box>
  )
}
