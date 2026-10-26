/**
 * A pop-up drawn over the screen has a solid panel, so the screen behind it does not show through its words.
 *
 * Owner's phone, 26 October 2026: the "Working overtime" sheet used `colors.surface` - a 6% white glass tint meant for
 * cards on the page - and the shift list showed through it, letters over letters. Four more pop-ups had the same
 * panel. They now use `colors.sheet`, the solid colour the Dispatch and Visitors pop-ups already used.
 */
// The app is not typed for Node, and this test reads source files: Node's reader and folder walk.
const fs = require('fs') as {
  readFileSync: (file: string, encoding: 'utf8') => string
  readdirSync: (dir: string, o: { withFileTypes: true }) => { name: string; isDirectory: () => boolean }[]
}

function sources(dir: string): string[] {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const full = `${dir}/${e.name}`
    if (e.isDirectory()) return sources(full)
    return /\.tsx$/.test(e.name) && !/\.test\.tsx$/.test(e.name) ? [full] : []
  })
}

const GLASS = /backgroundColor:\s*colors\.(surface|card|glassBg\w*)\b/

/** The styles a transparent Modal's panel is drawn with: the second View inside the Modal (the first is the
 *  backdrop). Read from the source, so a new pop-up is checked without being listed here. */
function panelStyles(src: string): string[] {
  const names: string[] = []
  for (const m of src.matchAll(/<Modal\b[^>]*\btransparent\b[^>]*>([\s\S]{0,600})/g)) {
    const views = [...m[1].matchAll(/<(?:View|Pressable)\s+style=\{\[?styles\.(\w+)/g)].map((v) => v[1])
    if (views[1]) names.push(views[1])
  }
  return names
}

function styleBody(src: string, name: string): string | null {
  const sheet = src.slice(src.lastIndexOf('StyleSheet.create'))
  const m = sheet.match(new RegExp(`\\n\\s+${name}\\s*:\\s*\\{([^{}]*)\\}`))
  return m ? m[1] : null
}

test('every pop-up drawn over the screen has a solid panel', () => {
  const checked: string[] = []
  const seeThrough: string[] = []
  for (const file of sources('src')) {
    const src = fs.readFileSync(file, 'utf8').replace(/\r\n/g, '\n')
    for (const name of panelStyles(src)) {
      const body = styleBody(src, name)
      if (body === null) continue
      checked.push(`${file}:${name}`)
      if (GLASS.test(body)) seeThrough.push(`${file}:${name}`)
    }
  }
  expect(seeThrough).toEqual([])
  // The five that were see-through are among those checked, so this cannot pass by finding nothing.
  for (const fixed of [
    'src/components/OvertimeRequestSheet.tsx:sheet', 'src/screens/OccurrenceBookScreen.tsx:modalCard',
    'src/screens/ShiftScreen.tsx:overlayCard', 'src/screens/TrainingScreen.tsx:resultCard',
    'src/components/VisitorAuthCards.tsx:sheet',
  ]) expect(checked).toContain(fixed)
})

test('the sheet colour is solid', () => {
  const theme = fs.readFileSync('src/theme/index.ts', 'utf8')
  expect(theme).toMatch(/\bsheet:\s*'#[0-9A-Fa-f]{6}'/)
})
