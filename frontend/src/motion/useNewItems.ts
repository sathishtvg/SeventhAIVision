import { useEffect, useRef, useState } from 'react'
import { announce } from './announce'
import { NEW_ITEM_MS } from './tokens'

const NONE: ReadonlySet<string> = new Set()

export interface NewItemsOptions {
  /**
   * Everything that decides which list this is: the filters, the page number.
   * When it changes the list is a different list, and what it first shows is
   * not new - only what arrives afterwards is.
   */
  scope?: string
  /** False while the rows on screen are the last list's, kept until this one's arrive. Nothing is new then. */
  settled?: boolean
  /** How long a row stays marked. */
  ttl?: number
  /** What to tell a screen reader: `(n) => countOf(n, 'new alert')`. Left out, nothing is said. */
  say?: (count: number) => string
}

/**
 * The ids of the rows that arrived after the list was first shown.
 *
 * A live event makes a list fetch itself again; this says which of the rows
 * that came back were not there before, so the page can mark them for a few
 * seconds (`newItemProps`) and a screen reader can be told. It reads the list
 * and changes nothing about it: the order, the times and the rows themselves
 * are the server's.
 *
 * The first rows a list shows are never new, nor are the first after a filter
 * changes - otherwise opening a page would light every row.
 */
export function useNewItems<T>(
  items: readonly T[] | undefined,
  idOf: (item: T) => string | number,
  { scope = '', settled = true, ttl = NEW_ITEM_MS, say }: NewItemsOptions = {},
): ReadonlySet<string> {
  const [fresh, setFresh] = useState<ReadonlySet<string>>(NONE)
  const known = useRef<Set<string> | null>(null)
  const knownScope = useRef(scope)
  const timers = useRef(new Map<string, ReturnType<typeof setTimeout>>())
  const read = useRef({ idOf, say })
  useEffect(() => { read.current = { idOf, say } })

  useEffect(() => {
    if (!items || !settled) {
      // Not this list's rows yet. Whatever comes next is where it starts from.
      known.current = null
      return
    }
    const ids = items.map((item) => String(read.current.idOf(item)))
    if (known.current === null || knownScope.current !== scope) {
      known.current = new Set(ids)
      knownScope.current = scope
      for (const timer of timers.current.values()) clearTimeout(timer)
      timers.current.clear()
      setFresh((was) => (was.size ? NONE : was))
      return
    }
    const arrived = ids.filter((id) => !known.current!.has(id))
    if (arrived.length === 0) return
    for (const id of arrived) known.current.add(id)
    setFresh((was) => new Set([...was, ...arrived]))
    const words = read.current.say?.(arrived.length)
    if (words) announce(words)
    for (const id of arrived) {
      timers.current.set(id, setTimeout(() => {
        timers.current.delete(id)
        setFresh((was) => {
          if (!was.has(id)) return was
          const next = new Set(was)
          next.delete(id)
          return next
        })
      }, ttl))
    }
  }, [items, scope, settled, ttl])

  useEffect(() => {
    const pending = timers.current
    return () => { for (const timer of pending.values()) clearTimeout(timer) }
  }, [])

  return fresh
}

/** What tints a new row: these are the names `motion.css` has a colour for. */
export type NewItemTone = 'critical' | 'high' | 'medium' | 'low' | 'info' | 'success'

/**
 * Spread onto a row or a card: `<TableRow {...newItemProps(fresh.has(id), 'critical')}>`.
 * A class and a data attribute, so it sits beside whatever `sx` the row has.
 * The mark is a tint and a bar at the row's leading edge, which is there
 * without any movement; it is the screen reader's announcement, not this, that
 * a person who cannot see it relies on.
 */
export function newItemProps(isNew: boolean, tone: NewItemTone = 'info'): { className?: string; 'data-tone'?: NewItemTone } {
  return isNew ? { className: 'sav-new', 'data-tone': tone } : {}
}

/** The tone for the words the server uses for severity and priority. Anything it does not know is `info`. */
export function toneOf(severity: string | null | undefined): NewItemTone {
  switch ((severity ?? '').toLowerCase()) {
    case 'critical': case 'emergency': case 'p1': return 'critical'
    case 'high': case 'urgent': case 'p2': return 'high'
    case 'medium': case 'moderate': case 'warning': case 'p3': return 'medium'
    case 'low': case 'minor': case 'p4': return 'low'
    default: return 'info'
  }
}
