/**
 * The evidence of a situation: what the platform kept, and the way to open it.
 *
 * The layer keeps no media. Each line is a reference to something the platform
 * already holds — a frame, a clip, a recording, a drone's media, a patrol's
 * snapshot — and names the permission its own endpoint asks for. Nothing is
 * loaded until a person presses Open: opening is their act, it is recorded
 * (chain of custody for a frame or a clip, the audit log for every kind), and
 * only then is the file fetched from the platform's own endpoint.
 */
import { useEffect, useRef, useState } from 'react'
import {
  Alert, Box, Button, Dialog, DialogActions, DialogContent, DialogTitle, Tooltip, Typography,
} from '@mui/material'
import ImageIcon from '@mui/icons-material/Image'
import MovieIcon from '@mui/icons-material/Movie'
import { useMutation, useQuery } from '@tanstack/react-query'
import { GlassCard } from '@/components/common/GlassCard'
import { apiClient } from '@/api/client'
import { useAuthStore } from '@/store/auth'
import { apiError, getSituationEvidence, openSituationEvidence } from '@/api/securityIntelligence'
import type { EvidenceItem, EvidenceOpened } from '@/api/securityIntelligence'
import { fmtTime } from './intelFormat'
import { ErrorState } from '@/components/states'

const KEPT: Record<string, string> = { central: 'held centrally', local: 'held at the site', both: 'held at the site and centrally' }
const LOGGED: Record<string, string> = {
  evidence_access_log: 'Recorded in the chain of custody and the audit log.',
  audit_log: 'Recorded in the audit log.',
}

interface Opened { item: EvidenceItem; opened: EvidenceOpened; url: string }

/** What was opened, shown from the platform's own endpoint. */
function Viewer({ shown, onClose }: { shown: Opened; onClose: () => void }) {
  const video = useRef<HTMLVideoElement>(null)
  const offset = shown.item.offset_seconds
  return (
    <Dialog open onClose={onClose} maxWidth="md" fullWidth>
      <DialogTitle>{shown.item.what}{shown.item.camera_name ? ` · ${shown.item.camera_name}` : ''}</DialogTitle>
      <DialogContent>
        {shown.opened.media_type === 'video' ? (
          <video ref={video} src={shown.url} controls style={{ width: '100%' }} data-testid="evidence-video"
                 onLoadedMetadata={() => { if (video.current && offset) video.current.currentTime = offset }} />
        ) : <img src={shown.url} alt={shown.item.what} style={{ width: '100%' }} data-testid="evidence-image" />}
        <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1 }}>
          Opened by you. {LOGGED[shown.item.logged_in] ?? ''}
          {offset != null ? ` The event is ${offset} s into this recording.` : ''}
          {shown.opened.checksum_sha256 ? ` SHA-256 ${shown.opened.checksum_sha256}` : ''}
        </Typography>
      </DialogContent>
      <DialogActions><Button onClick={onClose}>Close</Button></DialogActions>
    </Dialog>
  )
}

export function SituationEvidence({ situationId }: { situationId: string }) {
  const token = useAuthStore((s) => s.accessToken)
  const { data, error, refetch: refetchData } = useQuery({
    queryKey: ['intel-evidence', situationId], queryFn: () => getSituationEvidence(situationId), refetchInterval: 60_000 })
  const [shown, setShown] = useState<Opened | null>(null)
  // An object URL made for a file fetched with a header is released when it is no longer shown.
  useEffect(() => () => { if (shown?.url.startsWith('blob:')) URL.revokeObjectURL(shown.url) }, [shown])
  const open = useMutation({
    mutationFn: async (item: EvidenceItem): Promise<Opened> => {
      // Recorded first; the file is asked for only once that has succeeded.
      const opened = await openSituationEvidence(situationId, { kind: item.kind, id: item.id })
      const { path, token_in_query: inQuery } = opened.served_at
      if (inQuery) {
        return { item, opened, url: `${apiClient.defaults.baseURL ?? ''}${path}?token=${token ?? ''}` }
      }
      const blob = await apiClient.get<Blob>(path, { responseType: 'blob' }).then((r) => r.data)
      return { item, opened, url: URL.createObjectURL(blob) }
    },
    onSuccess: setShown,
  })
  const items = data?.items ?? []
  // Nothing kept, or not known yet: no card. It appears with what there is to show.
  if (!error && !items.length) return null
  return (
    <GlassCard sx={{ p: 2, mb: 2 }} data-testid="evidence-card">
      <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>Evidence{data ? ` (${items.length})` : ''}</Typography>
      <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1 }}>
        What the platform kept. Nothing is loaded until you open it; opening is recorded as yours.
      </Typography>
      {error ? <ErrorState compact error={error} onRetry={refetchData} /> : items.map((i) => {
        const button = (
          <Button size="small" variant="outlined" disabled={!i.may_open || open.isPending}
                  onClick={() => open.mutate(i)}>Open</Button>
        )
        return (
          <Box key={`${i.kind}-${i.id}`} data-testid="evidence-item" data-kind={i.kind}
               sx={{ display: 'grid', gridTemplateColumns: '24px 1fr auto', columnGap: 1, alignItems: 'center', py: 0.75,
                     borderBottom: '1px solid rgba(255,255,255,0.08)' }}>
            {i.media_type === 'video' ? <MovieIcon fontSize="small" sx={{ opacity: 0.7 }} />
              : <ImageIcon fontSize="small" sx={{ opacity: 0.7 }} />}
            <Box sx={{ minWidth: 0 }}>
              <Typography variant="body2">{i.what}{i.camera_name && !i.what.includes(i.camera_name) ? ` · ${i.camera_name}` : ''}</Typography>
              <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
                {fmtTime(i.captured_at)}
                {i.offset_seconds != null ? ` · the event is ${i.offset_seconds} s in` : ''}
                {i.kept ? ` · ${KEPT[i.kept] ?? i.kept}` : ''}
                {i.checksum_sha256 ? ` · SHA-256 ${i.checksum_sha256.slice(0, 12)}…` : ' · no checksum recorded'}
              </Typography>
            </Box>
            {i.may_open ? button
              : <Tooltip title={`Opening this needs the permission ${i.needs}.`}><span>{button}</span></Tooltip>}
          </Box>
        )
      })}
      {open.error && <Alert severity="error" sx={{ mt: 1 }}>{apiError(open.error)}</Alert>}
      {shown && <Viewer shown={shown} onClose={() => setShown(null)} />}
    </GlassCard>
  )
}
