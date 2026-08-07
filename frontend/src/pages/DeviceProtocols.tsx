/**
 * Device Protocols — what each site's hardware needs configured.
 *
 * Every site wires its barriers differently: an ANPR camera driving the boom
 * off its own relay, a dedicated access controller, a no-name LAN relay board.
 * Each needs different parameters, and until the device-protocol work those
 * were a fixed set of columns plus a hardcoded vendor CHECK constraint — so
 * even naming a new protocol required a migration.
 *
 * This screen renders its forms FROM THE SERVER'S DECLARATION. Adding a
 * protocol backend-side makes it appear here with no frontend change, which is
 * the whole point of the catalogue endpoint.
 *
 * Its own route and window because commissioning hardware is a different job,
 * usually a different person, from tuning alerts.
 */
import { useState } from 'react'
import {
  Box, Tabs, Tab, Typography, Chip, Tooltip, IconButton, Skeleton,
  Table, TableBody, TableCell, TableContainer, TableHead, TableRow, Paper,
  Alert as MuiAlert, Accordion, AccordionSummary, AccordionDetails,
} from '@mui/material'
import Stack from '@/components/common/Stack'
import OpenInNewIcon from '@mui/icons-material/OpenInNew'
import ExpandMoreIcon from '@mui/icons-material/ExpandMore'
import WarningAmberIcon from '@mui/icons-material/WarningAmber'
import VerifiedIcon from '@mui/icons-material/Verified'
import { useQuery } from '@tanstack/react-query'
import { GlassCard } from '@/components/common/GlassCard'
import { PageHeader } from '@/components/common/PageHeader'
import { openInNewWindow } from '@/lib/popoutWindow'
import { listDeviceFamilies, type ProtocolField } from '@/api/deviceProtocols'

const FAMILY_LABELS: Record<string, string> = {
  barrier: 'Barriers & Gates',
  alarm_panel: 'Alarm Panels',
}

const TYPE_LABELS: Record<string, string> = {
  string: 'Text',
  int: 'Number',
  bool: 'Yes / No',
  select: 'Choice',
  secret: 'Secret',
}

function FieldRow({ f }: { f: ProtocolField }) {
  const bounds =
    f.min != null || f.max != null
      ? `${f.min ?? '—'} to ${f.max ?? '—'}`
      : null
  return (
    <TableRow hover>
      <TableCell>
        <Stack direction="row" spacing={1} sx={{ alignItems: 'center', flexWrap: 'wrap' }}>
          <Typography variant="body2" sx={{ fontWeight: 600 }}>{f.label}</Typography>
          {f.required && <Chip size="small" label="required" color="warning" variant="outlined" />}
          {f.type === 'secret' && (
            <Tooltip title="Stored encrypted at rest; never returned by the API once saved">
              <Chip size="small" label="encrypted" color="success" variant="outlined" />
            </Tooltip>
          )}
        </Stack>
        {f.help && (
          <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 0.25 }}>
            {f.help}
          </Typography>
        )}
      </TableCell>
      <TableCell>
        <Typography variant="caption">{TYPE_LABELS[f.type] ?? f.type}</Typography>
      </TableCell>
      <TableCell>
        {f.options.length > 0 ? (
          <Stack direction="row" spacing={0.5} sx={{ flexWrap: 'wrap' }}>
            {f.options.map((o) => (
              <Chip key={o} size="small" variant="outlined" label={o} />
            ))}
          </Stack>
        ) : bounds ? (
          <Typography variant="caption" color="text.secondary">{bounds}</Typography>
        ) : (
          <Typography variant="caption" color="text.disabled">—</Typography>
        )}
      </TableCell>
      <TableCell>
        <Typography variant="caption" sx={{ fontFamily: 'monospace' }}>
          {f.default === null || f.default === undefined ? '—' : String(f.default)}
        </Typography>
      </TableCell>
      <TableCell>
        {/* Where the value lands. Not something an admin acts on, but it
            explains why some fields are searchable and others aren't. */}
        <Typography variant="caption" color="text.secondary">
          {f.column ? `column: ${f.column}` : 'config'}
        </Typography>
      </TableCell>
    </TableRow>
  )
}

export default function DeviceProtocols() {
  const [tab, setTab] = useState(0)
  const { data: families, isLoading } = useQuery({
    queryKey: ['device-families'],
    queryFn: listDeviceFamilies,
  })

  const family = families?.[tab]

  return (
    <Box>
      <PageHeader
        title="Device Protocols"
        subtitle="What each hardware protocol needs configured, per site"
        action={
          <Tooltip title="Open Device Protocols in its own window">
            <IconButton size="small" onClick={() => openInNewWindow('/device-protocols')}>
              <OpenInNewIcon fontSize="small" />
            </IconButton>
          </Tooltip>
        }
      />

      <MuiAlert severity="info" sx={{ mb: 2 }}>
        These forms are generated from the server's protocol catalogue, so a protocol
        added on the backend appears here automatically. Assign a protocol and fill in
        its values per device on the <strong>Cameras &rarr; Barriers</strong> screen;
        this page is the reference for what each one expects.
      </MuiAlert>

      {isLoading ? (
        <Skeleton variant="rounded" height={400} />
      ) : (
        <>
          <Tabs value={tab} onChange={(_, v) => setTab(v)} sx={{ mb: 2 }}>
            {(families ?? []).map((f) => (
              <Tab key={f.family} label={FAMILY_LABELS[f.family] ?? f.family} />
            ))}
          </Tabs>

          <Stack spacing={2}>
            {(family?.protocols ?? []).map((p) => (
              <GlassCard key={p.key} sx={{ p: 0, overflow: 'hidden' }}>
                <Accordion disableGutters sx={{ background: 'transparent', boxShadow: 'none' }}>
                  <AccordionSummary expandIcon={<ExpandMoreIcon />}>
                    <Stack spacing={0.5} sx={{ flex: 1 }}>
                      <Stack direction="row" spacing={1} sx={{ alignItems: 'center', flexWrap: 'wrap' }}>
                        <Typography variant="subtitle1" sx={{ fontWeight: 700 }}>
                          {p.label}
                        </Typography>
                        <Chip size="small" variant="outlined" label={p.key} sx={{ fontFamily: 'monospace' }} />
                        {p.hardware_verified ? (
                          <Tooltip title="Exercised against real hardware by the test suite">
                            <Chip size="small" color="success" icon={<VerifiedIcon />} label="verified" />
                          </Tooltip>
                        ) : (
                          <Tooltip title="Driver written from the published spec but never run against a real device — commission it on site before relying on it">
                            <Chip size="small" color="warning" icon={<WarningAmberIcon />} label="unverified" />
                          </Tooltip>
                        )}
                        {!p.supports_close && (
                          <Tooltip title="This device cannot be closed under remote command — the driver reports it as unsupported rather than pretending">
                            <Chip size="small" variant="outlined" color="info" label="no remote close" />
                          </Tooltip>
                        )}
                      </Stack>
                      <Typography variant="caption" color="text.secondary">
                        {p.description}
                      </Typography>
                    </Stack>
                  </AccordionSummary>
                  <AccordionDetails sx={{ p: 0 }}>
                    <TableContainer component={Paper} elevation={0} sx={{ background: 'transparent' }}>
                      <Table size="small">
                        <TableHead>
                          <TableRow>
                            <TableCell>Setting</TableCell>
                            <TableCell>Type</TableCell>
                            <TableCell>Allowed</TableCell>
                            <TableCell>Default</TableCell>
                            <TableCell>Stored as</TableCell>
                          </TableRow>
                        </TableHead>
                        <TableBody>
                          {p.fields.map((f) => (
                            <FieldRow key={f.name} f={f} />
                          ))}
                        </TableBody>
                      </Table>
                    </TableContainer>
                  </AccordionDetails>
                </Accordion>
              </GlassCard>
            ))}
          </Stack>
        </>
      )}
    </Box>
  )
}
