'use strict'
// Run with `npm test` in desktop/, and by CI. No packages are needed: Node's own test runner.

const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')

const signing = require('./signing-config')
const { ALSO_SIGNED, AZURE_TIMESTAMP, SigningConfigError, builtFiles, checkWhatWasBuilt, configOf, signingOf, thumbprintOf } = signing

const THUMBPRINT = 'A1B2C3D4E5F6A1B2C3D4E5F6A1B2C3D4E5F6A1B2'
const AZURE = {
  AZURE_SIGNING_ENDPOINT: 'https://eus.codesigning.azure.net',
  AZURE_SIGNING_ACCOUNT: 'exampleaccount',
  AZURE_SIGNING_PROFILE: 'example-profile',
  AZURE_TENANT_ID: '00000000-0000-0000-0000-000000000001',
  AZURE_CLIENT_ID: '00000000-0000-0000-0000-000000000002',
  AZURE_CLIENT_SECRET: 'a-secret-that-must-not-be-copied-anywhere',
}
const without = (env, ...names) => Object.fromEntries(Object.entries(env).filter(([name]) => !names.includes(name)))
const refuses = (env, saying) => assert.throws(() => configOf(env), (err) => {
  assert.ok(err instanceof SigningConfigError, String(err))
  assert.match(err.message, saying)
  return true
})

test('with nothing set the build is what it always was: nothing is added', () => {
  assert.deepEqual(configOf({}), {})
  assert.deepEqual(signingOf({}), { way: 'none' })
  // A variable that is there and empty is not set - a CI secret that was never filled in arrives like this.
  assert.deepEqual(configOf({ CSC_LINK: '', WIN_CERT_SHA1: '   ', AZURE_SIGNING_ENDPOINT: '' }), {})
  // The publisher's name alone signs nothing.
  assert.deepEqual(configOf({ WIN_PUBLISHER_NAME: 'Example Publisher Ltd' }), {})
})

test('what electron-builder is handed is a function it calls for the configuration', () => {
  assert.equal(typeof signing, 'function')
  assert.equal(typeof signing(), 'object')
})

test('every signed build signs the libraries too, fails if a file could not be signed, and is checked at the end', () => {
  for (const env of [{ WIN_CERT_SHA1: THUMBPRINT }, { WIN_CERT_SUBJECT: 'Example Publisher Ltd' }, AZURE,
    { CSC_LINK: 'C:/certs/old.pfx', CSC_KEY_PASSWORD: 'x' }, { WIN_CSC_LINK: 'C:/certs/old.pfx' }]) {
    const config = configOf(env)
    assert.equal(config.forceCodeSigning, true)
    assert.deepEqual(config.win.signExts, ALSO_SIGNED)
    assert.ok(ALSO_SIGNED.includes('.dll'))
    assert.equal(config.afterAllArtifactBuild, checkWhatWasBuilt)
    // Only what signing needs: the app's identity, its targets and its files are the other file's to say.
    assert.deepEqual(Object.keys(config).sort(), ['afterAllArtifactBuild', 'forceCodeSigning', 'win'])
  }
})

test('a certificate in the Windows store is named by its thumbprint, however it was copied', () => {
  for (const copied of [THUMBPRINT, THUMBPRINT.toLowerCase(), 'a1 b2 c3 d4 e5 f6 a1 b2 c3 d4 e5 f6 a1 b2 c3 d4 e5 f6 a1 b2',
    `\u200e${THUMBPRINT}`, `  ${THUMBPRINT}\r\n`, 'A1:B2:C3:D4:E5:F6:A1:B2:C3:D4:E5:F6:A1:B2:C3:D4:E5:F6:A1:B2']) {
    assert.equal(thumbprintOf(copied), THUMBPRINT)
    assert.deepEqual(configOf({ WIN_CERT_SHA1: copied }).win.signtoolOptions, { certificateSha1: THUMBPRINT })
  }
  refuses({ WIN_CERT_SHA1: THUMBPRINT.slice(2) }, /forty characters.*has 38/)
  refuses({ WIN_CERT_SHA1: 'Example Publisher Ltd' }, /forty characters/)
})

test('or by the name it was issued to - one or the other', () => {
  assert.deepEqual(configOf({ WIN_CERT_SUBJECT: ' Example Publisher Ltd ' }).win.signtoolOptions,
    { certificateSubjectName: 'Example Publisher Ltd' })
  refuses({ WIN_CERT_SHA1: THUMBPRINT, WIN_CERT_SUBJECT: 'Example Publisher Ltd' }, /not both/)
})

test('Azure Artifact Signing is told where, and to timestamp: its certificates live three days', () => {
  const config = configOf(AZURE)
  assert.deepEqual(config.win.azureSignOptions, {
    endpoint: 'https://eus.codesigning.azure.net',
    codeSigningAccountName: 'exampleaccount',
    certificateProfileName: 'example-profile',
    TimestampRfc3161: AZURE_TIMESTAMP,
    TimestampDigest: 'SHA256',
  })
  assert.equal(AZURE_TIMESTAMP, 'http://timestamp.acs.microsoft.com')
  assert.equal(config.win.signtoolOptions, undefined)
  assert.ok(configOf({ ...AZURE, AZURE_SIGNING_ENDPOINT: 'https://sea.codesigning.azure.net/' }).win.azureSignOptions)
})

test('Azure asked for by halves is an error that names what is missing, not an unsigned build', () => {
  for (const name of Object.keys(AZURE).filter((n) => n !== 'AZURE_SIGNING_ENDPOINT')) {
    refuses(without(AZURE, name), new RegExp(`not set: .*${name}`))
  }
  refuses({ AZURE_SIGNING_ACCOUNT: 'exampleaccount' },
    /AZURE_SIGNING_ENDPOINT, AZURE_SIGNING_PROFILE, AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET/)
  refuses({ ...AZURE, AZURE_SIGNING_ENDPOINT: 'eus' }, /address of the region/)
  refuses({ ...AZURE, AZURE_SIGNING_ENDPOINT: 'http://eus.codesigning.azure.net' }, /address of the region/)
  // The service's own sign-in can be proved another way than with a secret.
  assert.ok(configOf({ ...without(AZURE, 'AZURE_CLIENT_SECRET'), AZURE_CLIENT_CERTIFICATE_PATH: 'C:/certs/login.pem' }))
})

test('two ways at once is an error that names both', () => {
  refuses({ ...AZURE, WIN_CERT_SHA1: THUMBPRINT }, /more than one way: azure \(.*\) and store \(WIN_CERT_SHA1\)/)
  refuses({ WIN_CERT_SUBJECT: 'Example Publisher Ltd', CSC_LINK: 'C:/certs/old.pfx' }, /store \(WIN_CERT_SUBJECT\) and file \(CSC_LINK\)/)
})

test('a .pfx file is left to electron-builder, which reads it and its password itself', () => {
  const config = configOf({ CSC_LINK: 'C:/certs/old.pfx', CSC_KEY_PASSWORD: 'the-pfx-password' })
  assert.equal(config.win.signtoolOptions, undefined)
  assert.equal(config.win.azureSignOptions, undefined)
})

test("the publisher's name is the one that was given, and is left to the certificate when none was", () => {
  assert.equal(configOf({ WIN_CERT_SHA1: THUMBPRINT, WIN_PUBLISHER_NAME: ' Example Publisher Ltd ' }).win.signtoolOptions.publisherName,
    'Example Publisher Ltd')
  assert.equal(configOf({ CSC_LINK: 'C:/certs/old.pfx', WIN_PUBLISHER_NAME: 'Example Publisher Ltd' }).win.signtoolOptions.publisherName,
    'Example Publisher Ltd')
  assert.equal('publisherName' in configOf({ WIN_CERT_SHA1: THUMBPRINT }).win.signtoolOptions, false)
})

test('nothing secret is copied into the configuration, which electron-builder writes to a file beside the build', () => {
  const said = (env) => JSON.stringify(configOf(env))
  assert.ok(!said(AZURE).includes(AZURE.AZURE_CLIENT_SECRET))
  assert.ok(!said(AZURE).includes(AZURE.AZURE_TENANT_ID) && !said(AZURE).includes(AZURE.AZURE_CLIENT_ID))
  const file = { CSC_LINK: 'C:/certs/old.pfx', CSC_KEY_PASSWORD: 'the-pfx-password' }
  assert.ok(!said(file).includes('the-pfx-password') && !said(file).includes('old.pfx'))
})

test('the build configuration builds on this file, and keeps the identity it has always had', () => {
  const desktop = path.join(__dirname, '..')
  const yml = fs.readFileSync(path.join(desktop, 'electron-builder.yml'), 'utf8')
  const settings = yml.split(/\r?\n/).filter((line) => !line.trim().startsWith('#')).join('\n')
  assert.match(settings, /^extends: \.\/scripts\/signing-config\.js$/m)
  // A name written as ${env.X} in that file is not filled in by electron-builder: none is relied on.
  assert.ok(!settings.includes('${env.'), 'a setting that is never filled in')
  assert.match(settings, /^appId: ai\.seventh\.vision\.desktop$/m)
  assert.match(settings, /^ {2}upgradeCode: "5F3A9C21-8B47-4E2D-9A16-7C0E1F2B3D4A"$/m)
  const pkg = JSON.parse(fs.readFileSync(path.join(desktop, 'package.json'), 'utf8'))
  assert.equal(pkg.build.extends, './electron-builder.yml')
  assert.equal(pkg.scripts['verify:signed'], 'node scripts/check-signatures.js')
  assert.ok(pkg.scripts.dist.endsWith('electron-builder --win'), 'one way to build, signed or not')
  // Neither script is packed into the app: only what the package file lists is.
  assert.ok(!pkg.build.files.some((pattern) => pattern.startsWith('scripts')))
})

test('what is checked at the end is every installer and every program file the build made', () => {
  const out = fs.mkdtempSync(path.join(os.tmpdir(), 'sav-built-'))
  try {
    const app = path.join(out, 'win-unpacked')
    fs.mkdirSync(path.join(app, 'resources'), { recursive: true })
    for (const name of ['Seventh AI Vision.exe', 'ffmpeg.dll', 'LICENSE.electron.txt', 'resources/elevate.exe', 'resources/app.asar']) {
      fs.writeFileSync(path.join(app, name), 'x')
    }
    const made = ['Seventh AI Vision Setup 9.9.9.exe', 'Seventh AI Vision Setup 9.9.9.exe.blockmap', 'Seventh AI Vision 9.9.9.msi', 'latest.yml']
      .map((name) => path.join(out, name))
    const files = builtFiles({ outDir: out, artifactPaths: made }).map((file) => path.relative(out, file).split(path.sep).join('/'))
    assert.deepEqual(files, ['Seventh AI Vision Setup 9.9.9.exe', 'Seventh AI Vision 9.9.9.msi',
      'win-unpacked/Seventh AI Vision.exe', 'win-unpacked/ffmpeg.dll', 'win-unpacked/resources/elevate.exe'])
  } finally {
    fs.rmSync(out, { recursive: true, force: true })
  }
})

test('a build asked to sign that leaves a file unsigned fails, and so does one that made nothing',
  { skip: process.platform !== 'win32' && 'Windows is the only thing that can be asked' }, async () => {
    const out = fs.mkdtempSync(path.join(os.tmpdir(), 'sav-built-'))
    const quiet = console.log
    console.log = () => {}
    try {
      await assert.rejects(checkWhatWasBuilt({ outDir: out, artifactPaths: [] }), /made nothing to check/)
      const installer = path.join(out, 'Seventh AI Vision Setup 9.9.9.exe')
      fs.writeFileSync(installer, 'not a program, and not signed')
      await assert.rejects(checkWhatWasBuilt({ outDir: out, artifactPaths: [installer] }),
        /1 of the 1 files this build made are not signed in a way Windows trusts/)
    } finally {
      console.log = quiet
      fs.rmSync(out, { recursive: true, force: true })
    }
  })
