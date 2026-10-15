import { readdirSync, readFileSync, statSync } from 'node:fs'
import { dirname, join, relative, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { NOT_A_PAGE, NOT_YET_CONVERTED } from '@/motion/conversion'

// Where the conversion of every page stands, read from the pages themselves.

const SRC = resolve(dirname(fileURLToPath(import.meta.url)), '..')

function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const full = join(dir, name)
    if (statSync(full).isDirectory()) return name === 'states' ? [] : sources(full)
    return /\.tsx$/.test(name) && !/\.test\.tsx$/.test(name) ? [full] : []
  })
}
const read = (file: string) => readFileSync(file, 'utf8').replace(/\r\n/g, '\n')
const pages = sources(join(SRC, 'pages')).map((file) => ({ name: relative(join(SRC, 'pages'), file).split('\\').join('/'), text: read(file) }))
const everything = [...sources(join(SRC, 'pages')), ...sources(join(SRC, 'components'))].map((file) => ({
  name: relative(SRC, file).split('\\').join('/'), text: read(file),
}))

const fetches = (text: string) => /\buseQuery\s*[(<]/.test(text)
const saysItFailed = (text: string) => /<(ErrorState|TableErrorRow|LoadState)\b/.test(text)

describe('every page says so when a request fails', () => {
  it('is true of every page that fetches, but those listed as not yet done - and the list is exact', () => {
    const notYet = pages.filter((p) => fetches(p.text) && !saysItFailed(p.text)).map((p) => p.name).sort()
    expect(notYet).toEqual([...Object.keys(NOT_YET_CONVERTED), ...Object.keys(NOT_A_PAGE)].sort())
    // Nothing is on both lists, and every entry says why it is there.
    expect(Object.keys(NOT_YET_CONVERTED).filter((name) => name in NOT_A_PAGE)).toEqual([])
    for (const why of [...Object.values(NOT_YET_CONVERTED), ...Object.values(NOT_A_PAGE)]) expect(why.length).toBeGreaterThan(20)
  })

  it('counts what has been done', () => {
    const fetching = pages.filter((p) => fetches(p.text))
    const done = fetching.filter((p) => saysItFailed(p.text))
    expect(pages.length).toBe(121)
    expect(fetching.length).toBe(115)
    expect(done.length).toBe(fetching.length - Object.keys(NOT_YET_CONVERTED).length - Object.keys(NOT_A_PAGE).length)
    const places = everything.reduce((n, f) => n + (f.text.match(/<(ErrorState|TableErrorRow)\b/g) ?? []).length, 0)
    expect(places).toBeGreaterThanOrEqual(220)
  })

  it('binds the failed state to "failed with nothing to show", so a failed refresh never blanks rows that are there', () => {
    // Every name the conversion made is one request's isLoadingError; none is its isError, which is also true
    // when a refresh fails over rows that are on screen.
    for (const file of everything) {
      for (const [, name] of file.text.matchAll(/\b(\w+Failed\d*) (?:\?|&&) <(?:ErrorState|TableErrorRow)\b/g)) {
        expect([file.name, name, file.text.includes(`isLoadingError: ${name}`)]).toEqual([file.name, name, true])
      }
    }
  })

  it('answers "failed" before it waits for data that will never come', () => {
    // `if (loading || !data) return <placeholder>` shows the placeholder for ever once the request has failed.
    for (const file of everything) {
      const lines = file.text.split('\n')
      lines.forEach((line, at) => {
        if (!/^\s*if \(\w*[lL]oading \|\| !\w+\) return </.test(line)) return
        const before = lines.slice(0, at).reverse().find((l) => l.trim() !== '') ?? ''
        expect([file.name, at + 1, /^\s*if \(\w+Failed\d*\) return <ErrorState\b/.test(before)]).toEqual([file.name, at + 1, true])
      })
      for (const [whole] of file.text.matchAll(/\{[^{}\n]*\w*[lL]oading \|\| !\w+ \? /g)) {
        expect([file.name, /(Failed\d*|\berror) \? <ErrorState\b/.test(whole)]).toEqual([file.name, true])
      }
    }
  })

  it('says it once in each place', () => {
    for (const file of everything) {
      expect([file.name, /(\b\w+Failed\d*) \? <(?:ErrorState|TableErrorRow)\b[^<>]*\/> : \1 \? </.test(file.text)]).toEqual([file.name, false])
      const lines = file.text.split('\n')
      const doubled = lines.some((line, at) => at > 0 && line === lines[at - 1] && /Failed\d*\) return <ErrorState\b/.test(line))
      expect([file.name, doubled]).toEqual([file.name, false])
    }
  })

  it('does not show a request’s own error as a bare alert any more', () => {
    for (const file of everything) {
      expect([file.name, /\{!!?error && <Alert severity="error"[^>]*>\{apiError\(error\)\}<\/Alert>\}/.test(file.text)]).toEqual([file.name, false])
    }
  })
})

describe('a changed filter keeps the rows that were there', () => {
  // Rule 4. A list whose key is made of the page's own filters keeps its last answer on screen while the new one
  // is fetched, and says so.
  const kept = everything.flatMap((file) => [...file.text.matchAll(/isPlaceholderData: (\w+)/g)].map(([, name]) => ({ file, name })))

  it('says that the rows on screen are the last list’s, wherever they are kept', () => {
    expect(kept.length).toBeGreaterThanOrEqual(45)
    for (const { file, name } of kept) {
      const said = file.text.includes(`{${name} && <TableRefreshingRow />}`) || file.text.includes(`{${name} && <RefreshingLine />}`)
      expect([file.name, name, said]).toEqual([file.name, name, true])
    }
  })

  it('does not call the kept rows, or the first rows of the new list, new', () => {
    const both = everything.filter((file) => /\buseNewItems\(/.test(file.text) && /isPlaceholderData: /.test(file.text))
    expect(both.length).toBeGreaterThanOrEqual(6)
    for (const file of both) expect([file.name, /settled: !\w+Refreshing\d*/.test(file.text)]).toEqual([file.name, true])
  })

  it('never keeps one record under another’s name, nor a list that can be switched off', () => {
    let checked = 0
    for (const file of everything) {
      // Every list that says it is being refreshed: the binding and the options of one request, read together.
      for (const [whole] of file.text.matchAll(/const \{[^}]*isPlaceholderData: \w+[^}]*\} = useQuery\(\{[^}]*\}/g)) {
        checked += 1
        const key = /queryKey: \[([^\]]*)\]/.exec(whole)?.[1] ?? ''
        // A record's id from the address bar is never part of a kept key.
        const params = [...file.text.matchAll(/const \{ ([^}]+) \} = useParams/g)].flatMap(([, names]) => names.split(',').map((n) => n.trim().split(':')[0].trim()))
        expect([file.name, key, params.some((name) => new RegExp(`\\b${name}\\b`).test(key))]).toEqual([file.name, key, false])
        expect([file.name, key, /\benabled:/.test(whole)]).toEqual([file.name, key, false])
      }
    }
    expect(checked).toBeGreaterThanOrEqual(39)
  })
})
