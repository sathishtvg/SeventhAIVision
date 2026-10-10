/**
 * Which cameras have a privacy zone now, for a screen that shows live video.
 *
 * The phone's live view is the server's masked one whatever this says: it is
 * asked only so that a camera with a zone can be labelled, and a black block in
 * its picture read as meant. If it cannot be read, nothing is labelled and
 * nothing else changes.
 */
import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { getMaskedCameras } from '@/api/privacyZones'

export const MASKED_CAMERAS_KEY = ['masked-cameras'] as const

export function useMaskedCameras(): { has: (cameraId: string) => boolean; key: string } {
  const { data } = useQuery({
    queryKey: MASKED_CAMERAS_KEY,
    queryFn: getMaskedCameras,
    // The server applies a zone within ten seconds; the label keeps up with it.
    refetchInterval: 10_000,
    staleTime: 5_000,
  })
  return useMemo(() => {
    const ids = [...(data?.camera_ids ?? [])].sort()
    const set = new Set(ids)
    // `key` changes only when the cameras do: a screen that rebuilds a page on it does not rebuild on every read.
    return { has: (id: string) => set.has(id), key: ids.join(',') }
  }, [data])
}
