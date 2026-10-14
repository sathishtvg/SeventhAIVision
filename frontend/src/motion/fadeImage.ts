import type { SyntheticEvent } from 'react'

/** Marks a picture as there. Until it is marked the style sheet keeps it out of sight (`.sav-img`). */
export function markShown(el: HTMLImageElement | null): void {
  if (el) el.dataset.loaded = 'true'
}

/**
 * Spread onto an `<img>`: the picture is not seen until it is there, and
 * fades in when it is, in place of appearing from the top down or popping in
 * a row at a time.
 *
 * A picture that could not be fetched is marked as well, so that what the
 * browser draws for a broken picture (its alt text) is seen and not hidden.
 * An `<img>` with its own `onError` should call `markShown` in it, unless it
 * takes the picture away.
 */
export const fadeImageProps = {
  className: 'sav-img',
  // Already there when it is put on the page (from the browser's cache): nothing to wait for.
  ref: (el: HTMLImageElement | null) => { if (el?.complete && el.naturalWidth > 0) markShown(el) },
  onLoad: (e: SyntheticEvent<HTMLImageElement>) => markShown(e.currentTarget),
  onError: (e: SyntheticEvent<HTMLImageElement>) => markShown(e.currentTarget),
} as const
