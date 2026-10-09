/**
 * Which cameras have a privacy zone now, for a screen that plays live video.
 *
 * HLS copies a camera's stream without decoding it, so a zone cannot be painted
 * into it and the server refuses HLS for a camera that has one. A player asks
 * here before it chooses, and shows such a camera through the MJPEG view, which
 * is masked.
 *
 * UNTIL IT IS KNOWN, THE ANSWER IS THE MASKED VIEW. `mayPlayHls` is false while
 * the list is loading and if it could not be read: the MJPEG view is always
 * right, and HLS is right only for a camera known to have no zone.
 */
import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { getMaskedCameras } from '@/api/privacyZones'

export const MASKED_CAMERAS_KEY = ['masked-cameras'] as const

export interface MaskedCamerasAnswer {
  /** Whether the list has been read. */
  known: boolean
  /** Whether the camera has a privacy zone, as last read. */
  has: (cameraId: string) => boolean
  /** Whether the camera may be played through HLS: known to have no zone. */
  mayPlayHls: (cameraId: string) => boolean
}

export function useMaskedCameras(): MaskedCamerasAnswer {
  const { data } = useQuery({
    queryKey: MASKED_CAMERAS_KEY,
    queryFn: getMaskedCameras,
    // The server applies a zone within ten seconds; the wall keeps up with it.
    refetchInterval: 10_000,
    staleTime: 5_000,
  })
  return useMemo(() => {
    const ids = new Set(data?.camera_ids ?? [])
    const known = data !== undefined
    return { known, has: (id: string) => ids.has(id), mayPlayHls: (id: string) => known && !ids.has(id) }
  }, [data])
}
