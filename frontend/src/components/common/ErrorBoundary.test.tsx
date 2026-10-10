import { useState } from 'react'
import { render, screen, fireEvent } from '@/test/utils'
import { ErrorBoundary } from './ErrorBoundary'

/** A page that does what Analytics did: reads a property of something that is null. */
function Broken({ camera }: { camera: { id: string } | null }) {
  return <p>camera {camera!.id.slice(0, 8)}</p>
}

function Shell({ first }: { first: string }) {
  const [path, setPath] = useState(first)
  return (
    <div>
      <nav>
        <button onClick={() => setPath('/analytics')}>Analytics</button>
        <button onClick={() => setPath('/alerts')}>Alerts</button>
      </nav>
      <ErrorBoundary resetKey={path}>
        {path === '/analytics' ? <Broken camera={null} /> : <p>the alerts page</p>}
      </ErrorBoundary>
    </div>
  )
}

describe('ErrorBoundary', () => {
  // React logs the error it caught; that is expected here and would only be noise.
  let logged: ReturnType<typeof vi.spyOn>
  beforeEach(() => { logged = vi.spyOn(console, 'error').mockImplementation(() => {}) })
  afterEach(() => logged.mockRestore())

  it('shows what it was given when nothing goes wrong', () => {
    render(<ErrorBoundary><p>a page</p></ErrorBoundary>)
    expect(screen.getByText('a page')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('says the page could not be shown instead of leaving a blank window, and keeps the menu', () => {
    render(<Shell first="/analytics" />)
    expect(screen.getByText('This page could not be shown')).toBeInTheDocument()
    expect(screen.getByText(/nothing was changed or lost/i)).toBeInTheDocument()
    // What went wrong is there for whoever is asked about it.
    expect(screen.getByText(/null/i)).toBeInTheDocument()
    // The way out is still on the screen.
    expect(screen.getByRole('button', { name: 'Alerts' })).toBeInTheDocument()
  })

  it('starts afresh when the person goes to another screen', () => {
    render(<Shell first="/analytics" />)
    expect(screen.getByText('This page could not be shown')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Alerts' }))
    expect(screen.getByText('the alerts page')).toBeInTheDocument()
    expect(screen.queryByText('This page could not be shown')).not.toBeInTheDocument()
  })

  it('tries the same page again when asked, and says so again if it still fails', () => {
    render(<Shell first="/analytics" />)
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(screen.getByText('This page could not be shown')).toBeInTheDocument()
  })
})
