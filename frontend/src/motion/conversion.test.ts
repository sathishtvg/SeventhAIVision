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
    expect(pages.length).toBe(116)
    expect(fetching.length).toBe(110)
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
