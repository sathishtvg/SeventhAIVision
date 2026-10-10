/**
 * What is shown when a page cannot be drawn.
 *
 * Without this, one row a page did not expect — a camera that was null, on
 * 8 October 2026 — took the whole application down to a blank white window,
 * with nothing to say what had happened or how to get back. The page that
 * failed is replaced by this; the menu around it stays, so the way out is one
 * click.
 *
 * It resets itself when `resetKey` changes (the path, in the shell): going to
 * another screen is a fresh start, not the same failure shown again.
 */
import { Component, type ErrorInfo, type ReactNode } from 'react'
import { Alert, Box, Button, Typography } from '@mui/material'

interface Props {
  children: ReactNode
  /** When this changes the boundary lets its children try again. */
  resetKey?: string
}

interface State {
  error: Error | null
  seenKey: string | undefined
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null, seenKey: this.props.resetKey }

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { error }
  }

  static getDerivedStateFromProps(props: Props, state: State): Partial<State> | null {
    if (props.resetKey !== state.seenKey) return { error: null, seenKey: props.resetKey }
    return null
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // Kept in the console for whoever is asked what went wrong.
    console.error('A page could not be shown:', error, info.componentStack)
  }

  render() {
    const { error } = this.state
    if (!error) return this.props.children
    return (
      <Box role="alert" sx={{ p: 4, maxWidth: 720 }}>
        <Typography variant="h5" sx={{ fontWeight: 700, mb: 1 }}>This page could not be shown</Typography>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
          Something on it was not what the page expected. Nothing was changed or lost. The rest of the
          application is working: choose another screen from the menu, or try this one again.
        </Typography>
        <Alert severity="error" sx={{ mb: 2, fontFamily: 'monospace', fontSize: '0.8rem' }}>
          {error.message || String(error)}
        </Alert>
        <Box sx={{ display: 'flex', gap: 1 }}>
          <Button variant="contained" onClick={() => this.setState({ error: null })}>Try again</Button>
          <Button onClick={() => window.location.assign('/')}>Go to the dashboard</Button>
        </Box>
      </Box>
    )
  }
}
