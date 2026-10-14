import { existsSync, readdirSync, readFileSync, statSync, writeFileSync } from 'node:fs'
import { dirname, join, relative, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { DEFAULT_SCENE, pageKindOf, sceneOf } from '@/motion'

// The page-by-page matrix: what each page has, read from the page. It is written to the repository beside the
// audit, and this test fails when the file and the pages disagree - so the matrix cannot say more than is true.
//
//   UPDATE_MATRIX=1 npx vitest run src/motion/matrix.test.ts     writes it again

const SRC = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const MATRIX = resolve(SRC, '..', '..', 'PAGE_AWARE_ANIMATION_MATRIX.md')

function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const full = join(dir, name)
    if (statSync(full).isDirectory()) return sources(full)
    return /\.tsx$/.test(name) && !/\.test\.tsx$/.test(name) ? [full] : []
  })
}
const read = (file: string) => readFileSync(file, 'utf8').replace(/\r\n/g, '\n')

// Which file each route draws, from the route table.
const app = read(join(SRC, 'App.tsx'))
const fileOf = new Map<string, string>()
for (const [, def, named, from] of app.matchAll(/import (?:(\w+)|\{ ([^}]+) \}) from '@\/pages\/([^']+)'/g)) {
  for (const name of def ? [def] : named.split(',').map((n) => n.trim().split(' as ').pop()!)) fileOf.set(name, `${from}.tsx`)
}
const routesOf = new Map<string, string[]>()
const add = (file: string | undefined, path: string) => { if (file) routesOf.set(file, [...(routesOf.get(file) ?? []), path]) }
for (const [, path, component] of app.matchAll(/<Route path="([^"]+)" element=\{<(\w+)/g)) {
  if (path !== '*') add(fileOf.get(component), path.startsWith('/') ? path : `/${path}`)
}
add(fileOf.get(/<Route index element=\{<(\w+)/.exec(app)?.[1] ?? ''), '/')

const has = (text: string, shape: RegExp) => shape.test(text)
const yes = (on: boolean, applies = true) => (on ? 'yes' : applies ? 'NO' : '-')

interface Row { file: string; cells: string[]; fetches: boolean; failed: boolean; scene: boolean; routed: boolean }

const rows: Row[] = sources(join(SRC, 'pages')).map((full) => {
  const file = relative(join(SRC, 'pages'), full).split('\\').join('/')
  const text = read(full)
  const routes = routesOf.get(file) ?? []
  const inShell = routes.filter((r) => !/^\/(login|forgot-password|reset-password)$/.test(r))
  const fetches = has(text, /\buseQuery\s*[(<]/)
  const failed = has(text, /<(ErrorState|TableErrorRow|LoadState)\b/)
  const kept = (text.match(/isPlaceholderData: /g) ?? []).length
  const first = inShell[0]
  const scene = first ? sceneOf(first.replace(/:\w+/g, 'x1')) : null
  const own = [
    has(text, /\b(HlsPlayer|LiveImage|VideoLoadingState|VideoPlayer)\b/) && 'video states',
    has(text, /\b(StaleBadge|freshAt)\b/) && 'says when stale',
    has(text, /\bSuccessTick\b/) && 'confirms when done',
    has(text, /\b(ProgressState|SaveStatus)\b/) && 'progress as reported',
    has(text, /\b(InvestigationLoadingState|MissionLoadingState)\b/) && 'says what it is working on',
    has(text, /\b(AnimatedCounter|useCountUp)\b/) && 'figures count to real values',
    has(text, /\b(fadeUpSx|enterSx)\b/) && 'cards take turns',
  ].filter(Boolean).join('; ')
  return {
    file, fetches, failed, routed: routes.length > 0, scene: Boolean(scene && scene !== DEFAULT_SCENE),
    cells: [
      `\`${file}\``,
      routes.length ? routes.join(', ') : '(part of a page)',
      scene ? (scene === DEFAULT_SCENE ? 'NONE' : `${scene.motif}${scene.urgent ? ' (never covered)' : ''}`) : '-',
      first ? pageKindOf(first.replace(/:\w+/g, 'x1')) : '-',
      yes(failed, fetches),
      // A placeholder shaped like the content - or, for a camera's tile and a search, the state that says what
      // it is doing.
      yes(has(text, /Skeleton|VideoLoadingState|LiveImage|HlsPlayer|InvestigationLoadingState/), fetches),
      kept ? String(kept) : '-',
      yes(has(text, /\buseNewItems\(/), false),
      own || '-',
    ],
  }
})

const head = ['Page', 'Address', 'Scene', 'Arrives as', 'Says when a request failed', 'Placeholder', 'Lists that keep their rows', 'Marks what arrives', 'Its own states']
const count = (test: (row: Row) => boolean) => rows.filter(test).length
const written = [
  '# Page-aware motion: every page, and what it has',
  '',
  'Written by `frontend/src/motion/matrix.test.ts` from the pages themselves, and held to them by that test:',
  'it fails when this file and the code disagree. To write it again after a page changes:',
  '`UPDATE_MATRIX=1 npx vitest run src/motion/matrix.test.ts` (in `frontend/`).',
  '',
  '`yes` it has it. `-` it does not apply (a page that fetches nothing has no failed state; a page with no',
  'filtered list keeps no rows). `NO` it applies and is not there.',
  '',
  `- ${rows.length} files under \`src/pages\`; ${count((r) => r.routed)} are a page with an address of their own, the rest are parts of one.`,
  `- ${count((r) => r.scene)} pages inside the shell have a scene of their own; ${count((r) => r.cells[2] === 'NONE')} have none.`,
  `- ${count((r) => r.fetches)} fetch; ${count((r) => r.fetches && r.failed)} say so when a request failed; ${count((r) => r.fetches && !r.failed)} do not.`,
  `- ${count((r) => r.cells[5] === 'yes')} have a placeholder; ${count((r) => r.cells[6] !== '-')} keep a list's rows while a changed filter is fetched; ${count((r) => r.cells[7] === 'yes')} mark what arrives.`,
  '',
  `| ${head.join(' | ')} |`,
  `|${head.map(() => '---').join('|')}|`,
  ...rows.map((row) => `| ${row.cells.join(' | ')} |`),
  '',
].join('\n')

describe('the page-by-page matrix', () => {
  it('is the pages as they are', () => {
    if (process.env.UPDATE_MATRIX) writeFileSync(MATRIX, written)
    expect(existsSync(MATRIX)).toBe(true)
    expect(read(MATRIX)).toBe(written)
  })

  it('has no page that is missing what applies to it, but the one that is not a page', () => {
    // A row of links with a count: when the count cannot be fetched the links are still there (conversion.ts).
    expect(rows.filter((r) => r.fetches && !r.failed).map((r) => r.file)).toEqual(['intel/IntelNav.tsx'])
    expect(rows.filter((r) => r.cells[2] === 'NONE').map((r) => r.file)).toEqual([])
  })
})
