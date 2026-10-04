/**
 * The drone analytics charts, drawn with layout boxes like the app's other
 * analytics — no chart library.
 *
 * One hue per chart: every bar measures the same thing, so colour carries no
 * second meaning and the length is the message. Bars are thin and grow from one
 * baseline with a rounded data end; each has a hover target the full height of
 * its slot, and says its exact value there. The largest bar carries its number;
 * the rest are read from the hover or the table beside the chart. Values are
 * written in text colours, never in the bar's.
 */
import { Box, Tooltip, Typography } from '@mui/material'

export interface Datum {
  key: string
  /** Shown on hover and to screen readers: "Tue 30 Sep". */
  label: string
  value: number
  /** A recessive band behind the bar — "this slot is night". */
  shaded?: boolean
}

const BAR = 'primary.main'

/** Columns over an ordered axis (days, hours). `axis` picks which labels are printed. */
export function ColumnChart({ data, title, unit, height = 96, axis }: {
  data: Datum[]; title: string; unit: string; height?: number; axis?: (d: Datum, i: number) => string | null
}) {
  const max = Math.max(1, ...data.map((d) => d.value))
  const peak = data.findIndex((d) => d.value === max && d.value > 0)
  const total = data.reduce((n, d) => n + d.value, 0)
  const thin = data.length > 14
  return (
    <Box role="img" aria-label={`${title}: ${total} ${unit} in total; highest ${max} (${data[peak]?.label ?? 'none'})`}>
      <Box sx={{ display: 'flex', alignItems: 'flex-end', gap: '2px', height, borderBottom: '1px solid',
                 borderColor: 'divider', pt: 2 }}>
        {data.map((d, i) => (
          <Tooltip key={d.key} title={`${d.label}: ${d.value} ${unit}`} arrow disableInteractive>
            <Box sx={{ flex: 1, minWidth: 0, height: '100%', display: 'flex', flexDirection: 'column',
                       justifyContent: 'flex-end', alignItems: 'center',
                       bgcolor: d.shaded ? 'action.hover' : 'transparent',
                       '&:hover > .bar': { opacity: 0.75 } }}>
              {/* The label hangs above the bar, out of the layout: in the flow it
                  would take height from the very bar it labels, and the tallest
                  bar would be drawn shorter than its equals. */}
              <Box className="bar" sx={{ position: 'relative', flexShrink: 0, width: '100%', maxWidth: 24,
                                         height: `${(d.value / max) * 100}%`, minHeight: d.value ? 2 : 0,
                                         bgcolor: BAR, borderRadius: '4px 4px 0 0' }}>
                {i === peak && (
                  <Typography variant="caption" sx={{ position: 'absolute', bottom: '100%', left: '50%',
                                                      transform: 'translateX(-50%)', lineHeight: 1.3,
                                                      color: 'text.primary', fontWeight: 600 }}>
                    {d.value}</Typography>
                )}
              </Box>
            </Box>
          </Tooltip>
        ))}
      </Box>
      {axis && (
        <Box sx={{ display: 'flex', gap: '2px', mt: 0.5 }}>
          {/* A label is wider than a thin column, so it spills past its cell —
              inwards at the two ends when the columns are thin, so the first
              and last are never clipped. Wide columns keep it under the bar. */}
          {data.map((d, i) => (
            <Box key={d.key} sx={{ flex: 1, minWidth: 0, display: 'flex', overflow: 'visible',
                                   justifyContent: !thin ? 'center' : i === 0 ? 'flex-start'
                                     : i === data.length - 1 ? 'flex-end' : 'center' }}>
              <Typography variant="caption" color="text.secondary" sx={{ fontSize: 10, whiteSpace: 'nowrap' }}>
                {axis(d, i) ?? ''}</Typography>
            </Box>
          ))}
        </Box>
      )}
    </Box>
  )
}

/** Horizontal bars for named things, largest first, the value at each tip. */
export function BarList({ data, unit, note }: {
  data: { key: string; name: string; value: number; note?: string }[]; unit: string; note?: string
}) {
  const max = Math.max(1, ...data.map((d) => d.value))
  const hasNotes = data.some((d) => d.note)
  if (!data.length) return <Typography variant="body2" color="text.secondary">{note ?? 'Nothing in this period.'}</Typography>
  return (
    <Box role="list">
      {data.map((d) => (
        <Tooltip key={d.key} title={`${d.name}: ${d.value} ${unit}${d.note ? ` · ${d.note}` : ''}`} arrow
                 disableInteractive placement="top-start">
          <Box role="listitem" sx={{ display: 'grid', gap: 1, alignItems: 'center', py: 0.5,
                                     gridTemplateColumns: 'minmax(96px, 28%) 1fr auto' }}>
            <Typography variant="body2" noWrap>{d.name}</Typography>
            {/* The bar has its own track, so the longest one still leaves room
                for its value and note instead of pushing them off the card. */}
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75, minWidth: 0 }}>
              <Box sx={{ width: `${(d.value / max) * 100}%`, minWidth: d.value ? 2 : 0, height: 10,
                         bgcolor: BAR, borderRadius: '0 4px 4px 0' }} />
              <Typography variant="caption" sx={{ color: 'text.primary', fontWeight: 600, flexShrink: 0 }}>
                {d.value}</Typography>
            </Box>
            <Typography variant="caption" color="text.secondary" noWrap sx={{ minWidth: hasNotes ? 120 : 0 }}>
              {d.note ?? ''}</Typography>
          </Box>
        </Tooltip>
      ))}
    </Box>
  )
}

/** A headline number: the label, the value, and what it is a share of. */
export function StatTile({ label, value, sub }: { label: string; value: string | number; sub?: string }) {
  return (
    <Box>
      <Typography variant="caption" color="text.secondary">{label}</Typography>
      <Typography variant="h5" sx={{ fontWeight: 700, lineHeight: 1.2 }}>{value}</Typography>
      {sub && <Typography variant="caption" color="text.secondary">{sub}</Typography>}
    </Box>
  )
}
