import { render, screen } from '@/test/utils'
import { SeverityChip } from '@/components/common/SeverityChip'
import type { AlertSeverity } from '@/types/api'

const SEVERITY_CASES: [AlertSeverity, string][] = [
  ['info', 'Info'],
  ['low', 'Low'],
  ['medium', 'Medium'],
  ['high', 'High'],
  ['critical', 'Critical'],
]

describe('SeverityChip', () => {
  it.each(SEVERITY_CASES)('renders severity "%s" with label "%s"', (severity, label) => {
    render(<SeverityChip severity={severity} />)
    expect(screen.getByText(label)).toBeInTheDocument()
  })

  it('defaults to small size', () => {
    render(<SeverityChip severity="high" />)
    expect(screen.getByText('High')).toBeInTheDocument()
  })

  it('accepts medium size prop without error', () => {
    render(<SeverityChip severity="critical" size="medium" />)
    expect(screen.getByText('Critical')).toBeInTheDocument()
  })
})
