'use strict'
// Run with `npm test` in desktop/, and by CI. No packages are needed: Node's own test runner.

const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')

const { AT_A_TIME, SIGNED_KINDS, defaultFiles, filesUnder, inspect, nameIn, refusedOf, report, verdictOf } = require('./check-signatures')

const CA = 'CN=Example Public Code Signing CA 2026, O=Example Authority, C=US'
const OURS = 'CN="Example Publisher, Ltd", O="Example Publisher, Ltd", L=Example City, C=SG'
const row = (over = {}) => ({
  path: 'C:\\out\\Seventh AI Vision Setup 9.9.9.exe', status: 'Valid', message: 'Signature verified.',
  signer: OURS, issuer: CA, expires: '2027-10-10', stamped: true, ...over,
})
const UNSIGNED = { status: 'NotSigned', message: 'The file is not digitally signed.', signer: null, issuer: null, expires: null, stamped: false }

test('a file is fit to give to somebody only when it is signed, trusted and timestamped', () => {
  assert.equal(verdictOf(row()), 'trusted')
  assert.equal(verdictOf(row({ stamped: false })), 'unstamped')
  assert.equal(verdictOf(row(UNSIGNED)), 'unsigned')
  // Signed, and the certificate leads back to nobody this PC trusts.
  assert.equal(verdictOf(row({ status: 'UnknownError', message: 'A certificate chain processed, but terminated in a root certificate which is not trusted by the trust provider.' })), 'untrusted')
  // Signed, and changed afterwards.
  assert.equal(verdictOf(row({ status: 'HashMismatch' })), 'untrusted')
  assert.equal(verdictOf(row({ status: 'NotTrusted' })), 'untrusted')
  // Something that is not a program at all has no signer, whatever the status is called.
  assert.equal(verdictOf(row({ ...UNSIGNED, status: 'UnknownError' })), 'unsigned')
})

test('a certificate that signs itself is not trusted, whatever this PC has been told to think of it', () => {
  const own = 'CN=Made on this PC'
  assert.equal(verdictOf(row({ signer: own, issuer: own })), 'untrusted')
  assert.match(report([row({ signer: own, issuer: own })]), /NOT TRUSTED .* signs itself: no public authority stands behind it/)
})

test('everything that is not trusted is refused', () => {
  const rows = [row(), row({ stamped: false }), row(UNSIGNED), row({ status: 'HashMismatch' })]
  assert.deepEqual(refusedOf(rows), rows.slice(1))
  assert.deepEqual(refusedOf([row(), row()]), [])
})

test('the name in a certificate is read whole, commas and all', () => {
  assert.equal(nameIn(OURS), 'Example Publisher, Ltd')
  assert.equal(nameIn(CA), 'Example Public Code Signing CA 2026')
  assert.equal(nameIn('O=No Common Name, C=US'), 'O=No Common Name, C=US')
  assert.equal(nameIn(null), '')
})

test('the report says the worst first, shows files from the build folder, and says why', () => {
  const base = 'C:\\out'.split('\\').join(path.sep)
  const at = (name) => [base, ...name.split('/')].join(path.sep)
  const lines = report([
    row({ path: at('Seventh AI Vision Setup 9.9.9.exe') }),
    row({ path: at('win-unpacked/ffmpeg.dll'), ...UNSIGNED }),
    row({ path: at('win-unpacked/Seventh AI Vision.exe'), stamped: false }),
    row({ path: at('win-unpacked/vulkan-1.dll'), status: 'HashMismatch', message: 'The contents of the file\r\n  have changed.' }),
  ], base).split('\n')
  assert.deepEqual(lines.map((line) => line.trim().split(/ {2,}/)[0]), ['NOT SIGNED', 'NOT TRUSTED', 'NO TIMESTAMP', 'trusted'])
  assert.equal(lines[0].trim(), `NOT SIGNED    ${['win-unpacked', 'ffmpeg.dll'].join(path.sep)}`)
  assert.match(lines[1], /vulkan-1\.dll\s+Example Publisher, Ltd, issued by Example Public Code Signing CA 2026 - The contents of the file have changed\.$/)
  assert.match(lines[2], /no timestamp: good only until 2027-10-10$/)
  assert.match(lines[3], /Seventh AI Vision Setup 9\.9\.9\.exe\s+Example Publisher, Ltd, issued by Example Public Code Signing CA 2026$/)
  assert.ok(!lines.join('\n').includes(base), 'paths are shown from the build folder')
})

test('the files looked at are the kinds Windows checks, wherever under a folder they are', () => {
  assert.deepEqual(SIGNED_KINDS, ['.exe', '.msi', '.dll', '.node'])
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'sav-files-'))
  try {
    fs.mkdirSync(path.join(root, 'resources', 'app.asar.unpacked'), { recursive: true })
    for (const name of ['App.EXE', 'ffmpeg.dll', 'resources/elevate.exe', 'resources/app.asar', 'resources/app.asar.unpacked/addon.node',
      'LICENSES.chromium.html', 'chrome_100_percent.pak']) fs.writeFileSync(path.join(root, name), 'x')
    assert.deepEqual(filesUnder(root).map((file) => path.relative(root, file).split(path.sep).join('/')),
      ['App.EXE', 'ffmpeg.dll', 'resources/app.asar.unpacked/addon.node', 'resources/elevate.exe'])
    assert.deepEqual(filesUnder(path.join(root, 'not-there')), [])
  } finally {
    fs.rmSync(root, { recursive: true, force: true })
  }
})

test('with nothing named, it is the installers of this version and the unpacked app - not last month\'s', () => {
  const release = fs.mkdtempSync(path.join(os.tmpdir(), 'sav-release-'))
  try {
    fs.mkdirSync(path.join(release, 'win-unpacked'))
    for (const name of ['Seventh AI Vision Setup 1.0.7.exe', 'Seventh AI Vision 1.0.7.exe', 'Seventh AI Vision 1.0.7.msi',
      'Seventh AI Vision Setup 1.0.6.exe', 'Seventh AI Vision 1.0.70.exe', 'Seventh AI Vision Setup 1.0.7.exe.blockmap',
      'builder-debug.yml', 'win-unpacked/Seventh AI Vision.exe', 'win-unpacked/ffmpeg.dll']) fs.writeFileSync(path.join(release, name), 'x')
    assert.deepEqual(defaultFiles(release, '1.0.7').map((file) => path.relative(release, file).split(path.sep).join('/')),
      ['Seventh AI Vision 1.0.7.exe', 'Seventh AI Vision 1.0.7.msi', 'Seventh AI Vision Setup 1.0.7.exe',
        'win-unpacked/Seventh AI Vision.exe', 'win-unpacked/ffmpeg.dll'])
    assert.deepEqual(defaultFiles(path.join(release, 'not-there'), '1.0.7'), [])
  } finally {
    fs.rmSync(release, { recursive: true, force: true })
  }
})

test('Windows is asked a batch at a time, with the file names beside the command and never inside it', () => {
  const files = Array.from({ length: AT_A_TIME + 5 }, (_, n) => `C:\\out\\it's file ${n} $(calc).dll`)
  const calls = []
  const windows = (command, args, options) => {
    const asked = JSON.parse(options.env.SAV_SIGNATURE_PATHS)
    calls.push({ command, args, asked })
    const rows = asked.map((file) => ({ path: file, ...UNSIGNED }))
    return Buffer.from(JSON.stringify(rows), 'utf8').toString('base64') + '\r\n'
  }
  const rows = inspect(files, windows, 'win32')
  assert.deepEqual(rows.map((r) => r.path), files)
  assert.deepEqual(calls.map((call) => call.asked.length), [AT_A_TIME, 5])
  for (const call of calls) {
    assert.equal(call.command, 'powershell.exe')
    assert.deepEqual(call.args.slice(0, 3), ['-NoProfile', '-NonInteractive', '-Command'])
    assert.ok(!call.args.join(' ').includes('calc'), 'a file name is data, never part of the command')
    assert.ok(!/Set-|Remove-|Import-Certificate|-ExecutionPolicy/i.test(call.args.join(' ')), 'it reads; it changes nothing')
  }
  assert.deepEqual(inspect([], windows, 'win32'), [])
})

test('nothing but Windows can be asked', () => {
  assert.throws(() => inspect(['a.exe'], () => { throw new Error('must not be run') }, 'linux'), /only be checked on Windows/)
})

test('on Windows: a file of Windows itself is trusted, and one that is nothing is not signed',
  { skip: process.platform !== 'win32' && 'Windows is the only thing that can be asked' }, () => {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), 'sav-ask-'))
    try {
      const nothing = path.join(root, "it's nothing.exe")
      fs.writeFileSync(nothing, 'not a program, and not signed')
      const explorer = path.join(process.env.SystemRoot || 'C:\\Windows', 'explorer.exe')
      const [ours, theirs] = inspect([nothing, explorer])
      assert.equal(ours.path, nothing)
      assert.equal(verdictOf(ours), 'unsigned')
      assert.equal(verdictOf(theirs), 'trusted')
      assert.match(theirs.signer, /Microsoft/)
    } finally {
      fs.rmSync(root, { recursive: true, force: true })
    }
  })
