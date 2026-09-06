import { useEffect } from 'react'

/** Input types that own a browser-drawn picker worth opening on click. */
const PICKER_TYPES = new Set(['date', 'time', 'datetime-local', 'month', 'week'])

/**
 * Clicking anywhere in a date/time field opens its picker.
 *
 * A native `<input type="date">` only opens its calendar when the small
 * indicator glyph at the right edge is clicked. Clicking the text — which is
 * most of the control's surface, and the obvious target — does nothing at
 * all. The field reads as broken rather than as "aim for the tiny icon".
 * `showPicker()` is the supported way to open it from script, and a click is
 * a user gesture so the call is allowed.
 *
 * Delegated from `document` rather than wired onto each input on purpose:
 * date fields appear on a dozen pages and inside dialogs that mount long
 * after this runs, so a per-field handler would have to be remembered by
 * every future one — exactly the kind of thing that gets forgotten and
 * leaves one screen subtly worse than the rest.
 */
export function useNativePickerOnClick() {
  useEffect(() => {
    const onClick = (event: MouseEvent) => {
      const target = event.target as HTMLElement | null
      const input = target?.closest?.('input') as HTMLInputElement | null
      if (!input || !PICKER_TYPES.has(input.type)) return
      if (input.disabled || input.readOnly) return
      try {
        input.showPicker()
      } catch {
        // Older Safari has no showPicker, and the browser can refuse the call
        // if it decides the gesture was not direct enough. Either way the
        // field still works exactly as it did before — the indicator glyph
        // opens it — so there is nothing to recover from.
      }
    }
    document.addEventListener('click', onClick)
    return () => document.removeEventListener('click', onClick)
  }, [])
}
