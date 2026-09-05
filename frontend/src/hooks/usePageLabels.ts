import { useQuery } from '@tanstack/react-query'
import { getSettings } from '@/api/settings'
import { PAGE_LABELS, resolvePageLabel, type PageKey, type PageLabel, type PageLabelOverrides } from '@/lib/pageLabels'

export const PAGE_LABELS_SETTING = 'ui.page_labels'

/**
 * The tenant's page-title overrides, or an empty map.
 *
 * Shared query key with the Settings page so renaming a page updates every
 * header without a reload. staleTime is long because these change roughly
 * never, and every page in the app mounts this — refetching per navigation
 * would be a request per page view for data that is almost always identical.
 */
export function usePageLabelOverrides(): PageLabelOverrides {
  const { data } = useQuery({
    queryKey: ['settings'],
    queryFn: getSettings,
    staleTime: 5 * 60_000,
  })
  const row = data?.find((s) => s.setting_key === PAGE_LABELS_SETTING)
  const value = row?.setting_value
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as PageLabelOverrides)
    : {}
}

/** Resolved title + subtitle for one page: product default under any override. */
export function usePageLabel(key: PageKey): PageLabel {
  return resolvePageLabel(key, usePageLabelOverrides())
}

export { PAGE_LABELS }
export type { PageKey, PageLabel }
