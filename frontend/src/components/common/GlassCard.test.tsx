import { render, screen } from '@/test/utils'
import { GlassCard } from '@/components/common/GlassCard'

describe('GlassCard', () => {
  it('renders children', () => {
    render(<GlassCard><p>hello world</p></GlassCard>)
    expect(screen.getByText('hello world')).toBeInTheDocument()
  })

  it('forwards additional props (e.g. data-testid)', () => {
    render(<GlassCard data-testid="my-card"><span>content</span></GlassCard>)
    expect(screen.getByTestId('my-card')).toBeInTheDocument()
  })

  it('merges sx prop with default styles', () => {
    render(<GlassCard data-testid="card" sx={{ width: 400 }}><span>test</span></GlassCard>)
    expect(screen.getByTestId('card')).toBeInTheDocument()
  })
})
