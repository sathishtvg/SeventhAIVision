import { Alert, Snackbar } from '@mui/material'
import { useLoadFailures } from '@/store/loadFailures'

/**
 * The one notice that says a request for data failed somewhere on the page
 * (`store/loadFailures.ts`). In the shell, once.
 *
 * It is a status and not an alert: it does not take the keyboard, a screen
 * reader says it when it has finished what it was saying, and it goes away by
 * itself. It sits at the foot of the screen in the middle: clear of the alert
 * toasts at the right, which are a different thing - those are events at a
 * site; this is the app saying it could not fetch something - and clear of the
 * menu at the left, whose last entries it covered when it was put there.
 */
export function LoadFailureNotice() {
  const open = useLoadFailures((s) => s.open)
  const close = useLoadFailures((s) => s.close)
  return (
    <Snackbar
      open={open}
      autoHideDuration={5000}
      onClose={(_, reason) => { if (reason !== 'clickaway') close() }}
      anchorOrigin={{ vertical: 'bottom', horizontal: 'center' }}
    >
      <Alert severity="warning" variant="filled" role="status" onClose={close} sx={{ alignItems: 'center' }}>
        Some information could not be loaded. What is on screen may be out of date.
      </Alert>
    </Snackbar>
  )
}
