/**
 * What else the site recorded around one entry, exit or stay: plate reads, barrier commands, door events and
 * incidents open at the time, each "Plate matches" or "Near in time only", with how long before or after.
 *
 * Never "confirmed": a plate that matches is a plate that matches, not the vehicle, and not who was driving.
 * A section the person may not read says so; an empty one says none was found.
 */
import { useState } from 'react'
import {
  Box, Button, Chip, Dialog, DialogActions, DialogContent, DialogTitle, List, ListItem, ListItemText, Skeleton, TextField, Typography,
} from '@mui/material'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import Stack from '@/components/common/Stack'
import { ErrorState, SaveStatus } from '@/components/states'
import { getEventEvidence, setEvidenceSettings, type EvidenceItem, type EventEvidence, type GeofenceEvent } from '@/api/fleet'
import { eventWhen, eventWords, gapWords } from './geofenceFormat'

const ORDER = ['plate_reads', 'barrier_commands', 'door_events', 'incidents'] as const

function itemWords(kind: (typeof ORDER)[number], i: EvidenceItem): string {
  if (kind === 'plate_reads') {
    return `${i.plate_number ?? 'No plate read'}${i.confidence != null ? ` (${Math.round(i.confidence <= 1 ? i.confidence * 100 : i.confidence)}% sure)` : ''}`
      + `${i.direction ? `, ${i.direction}` : ''} at ${i.place}`
  }
  if (kind === 'barrier_commands') {
    return `${i.command} at ${i.place}${i.plate_number ? ` for ${i.plate_number}` : ''}${i.source ? `, by ${i.source}` : ''}`
      + `${i.succeeded === false ? ' (failed)' : ''}`
  }
  if (kind === 'door_events') return `${i.event_type}${i.denial_reason ? ` (${i.denial_reason})` : ''} at ${i.place}`
  return `${i.title} - ${i.status}${i.severity ? `, ${i.severity}` : ''}`
}

export function EvidenceDialog({ event, mayChangeWindow, onClose }: { event: GeofenceEvent; mayChangeWindow: boolean; onClose: () => void }) {
  const qc = useQueryClient()
  const { data, isLoading, isLoadingError: evidenceFailed, error: evidenceError, refetch } = useQuery({
    queryKey: ['fleet-event-evidence', event.id], queryFn: () => getEventEvidence(event.id),
  })
  return (
    <Dialog open onClose={onClose} maxWidth="md" fullWidth aria-labelledby="evidence-title">
      <DialogTitle id="evidence-title">
        {eventWords(event)} {event.geofence_name}: what else was recorded
      </DialogTitle>
      <DialogContent>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
          {event.plate_number ?? event.vehicle_name}, {eventWhen(event)}. A plate that matches is not proof of who was driving.
        </Typography>
        {isLoading ? (
          <Box>{[0, 1, 2].map((i) => <Skeleton key={i} height={40} />)}</Box>
        ) : evidenceFailed || !data ? (
          <ErrorState compact title="What else was recorded could not be loaded" error={evidenceError} onRetry={refetch} />
        ) : data.said ? (
          <Typography variant="body2" sx={{ py: 2 }}>{data.said}</Typography>
        ) : (
          <>
            <Window data={data} mayChange={mayChangeWindow}
                    onSaved={() => qc.invalidateQueries({ queryKey: ['fleet-event-evidence'] })} />
            {ORDER.map((kind) => {
              const section = data.sections[kind]
              if (!section) return null
              return (
                <Box key={kind} sx={{ mt: 2 }}>
                  <Typography variant="subtitle2">{section.label}</Typography>
                  {section.said && <Typography variant="body2" color="text.secondary">{section.said}</Typography>}
                  {section.items.length > 0 && (
                    <List dense disablePadding>
                      {section.items.map((i, n) => (
                        <ListItem key={`${i.at}-${n}`} disableGutters>
                          <ListItemText primary={itemWords(kind, i)}
                                        secondary={`${new Date(i.at).toLocaleTimeString()} · ${gapWords(i.gap_s)}`} />
                          {i.match && (
                            <Chip size="small" variant={i.match === 'plate_matches' ? 'filled' : 'outlined'}
                                  color={i.match === 'plate_matches' ? 'info' : 'default'} label={data.match_words[i.match]} />
                          )}
                        </ListItem>
                      ))}
                    </List>
                  )}
                </Box>
              )
            })}
          </>
        )}
      </DialogContent>
      <DialogActions><Button onClick={onClose}>Close</Button></DialogActions>
    </Dialog>
  )
}

function Window({ data, mayChange, onSaved }: { data: EventEvidence; mayChange: boolean; onSaved: () => void }) {
  const [seconds, setSeconds] = useState(String(data.window_s))
  const save = useMutation({ mutationFn: () => setEvidenceSettings(Number(seconds)), onSuccess: onSaved })
  const asked = Number(seconds)
  const valid = Number.isInteger(asked) && asked >= 30 && asked <= 900
  return (
    <Stack direction="row" sx={{ gap: 1, alignItems: 'center', flexWrap: 'wrap' }}>
      <Typography variant="body2">
        Looked for {gapWords(-data.window_s).replace(' before', '')} either side, at {data.site_name}.
      </Typography>
      {mayChange && (
        <>
          <TextField size="small" type="number" label="Seconds either side" value={seconds} error={!valid} sx={{ width: 170 }}
                     onChange={(e) => setSeconds(e.target.value)} helperText="30 to 900, for every event" />
          <Button size="small" variant="outlined" disabled={!valid || asked === data.window_s || save.isPending} onClick={() => save.mutate()}>
            Look again
          </Button>
          <SaveStatus state={save.isPending ? 'saving' : save.isError ? 'failed' : 'idle'} error={save.error} />
        </>
      )}
    </Stack>
  )
}
