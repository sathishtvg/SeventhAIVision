import { useEffect } from 'react'
import type { ReactNode } from 'react'
import { Box, Skeleton, Typography } from '@mui/material'
import ManageSearchIcon from '@mui/icons-material/ManageSearch'
import RouteIcon from '@mui/icons-material/Route'
import { announce } from '@/motion'
import { MapSkeleton, TableBlockSkeleton } from './Skeletons'

function Heading({ icon, title, detail }: { icon: ReactNode; title: string; detail?: string }) {
  return (
    <Box sx={{ display: 'flex', alignItems: 'flex-start', gap: 1.25, mb: 1.5 }}>
      <Box className="sav-waits" sx={{ color: 'primary.main', display: 'grid', placeItems: 'center', mt: '2px' }}>{icon}</Box>
      <Box sx={{ minWidth: 0 }}>
        <Typography variant="body2" sx={{ fontWeight: 600 }}>{title}</Typography>
        {detail && <Typography variant="caption" color="text.secondary">{detail}</Typography>}
      </Box>
    </Box>
  )
}

/**
 * A search of the records that is running.
 *
 * It says what is being looked for, in the words that were asked, and holds
 * the room the results will take. It shows no result, no count and no guess:
 * what was found is shown when the server has said what was found.
 */
export function InvestigationLoadingState({ asked }: { asked?: string }) {
  const title = asked ? `Searching the records for “${asked}”` : 'Searching the records'
  useEffect(() => { announce(title) }, [title])
  return (
    <Box aria-busy="true" sx={{ width: '100%' }}>
      <Heading icon={<ManageSearchIcon fontSize="small" />} title={`${title}…`}
               detail="Results are listed when the search has finished." />
      <TableBlockSkeleton rows={5} rowHeight={44} />
    </Box>
  )
}

/**
 * A drone mission or patrol that is being fetched: where its map will be, and
 * its details beside it.
 *
 * Nothing is drawn on the map - no route, no waypoint, no drone - until the
 * mission's own data has arrived. A route sketched in advance would be a
 * route nobody planned.
 */
export function MissionLoadingState({ what = 'the mission' }: { what?: string }) {
  useEffect(() => { announce(`Loading ${what}`) }, [what])
  return (
    <Box aria-busy="true" sx={{ width: '100%' }}>
      <Heading icon={<RouteIcon fontSize="small" />} title={`Loading ${what}…`}
               detail="The route and its waypoints are drawn when they have been loaded." />
      <Box sx={{ display: 'grid', gap: 2, gridTemplateColumns: { xs: '1fr', md: 'minmax(0, 2fr) minmax(0, 1fr)' } }}>
        <MapSkeleton height={360} />
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
          <Skeleton variant="rounded" height={56} />
          <Skeleton variant="rounded" height={56} />
          <Skeleton variant="rounded" height={120} />
          <Skeleton variant="text" width="60%" />
        </Box>
      </Box>
    </Box>
  )
}
