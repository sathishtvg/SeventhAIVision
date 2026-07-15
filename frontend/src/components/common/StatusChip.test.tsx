import { render, screen } from '@/test/utils'
import { StatusChip } from '@/components/common/StatusChip'

const STATUS_CASES: [string, string][] = [
  ['open', 'Open'],
  ['acknowledged', 'Acknowledged'],
  ['resolved', 'Resolved'],
  ['closed', 'Closed'],
  ['dismissed', 'Dismissed'],
  ['investigating', 'Investigating'],
  ['online', 'Online'],
  ['degraded', 'Degraded'],
  ['offline', 'Offline'],
]

describe('StatusChip', () => {
  it.each(STATUS_CASES)('renders status "%s" capitalised as "%s"', (status, label) => {
    render(<StatusChip status={status} />)
    expect(screen.getByText(label)).toBeInTheDocument()
  })

  it('falls back gracefully for unknown status strings', () => {
    render(<StatusChip status="unknown_state" />)
    expect(screen.getByText('Unknown_state')).toBeInTheDocument()
  })
})
