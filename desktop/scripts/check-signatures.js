'use strict'
/**
 * Asks Windows what it makes of the signature on each file of a build.
 *
 *   npm run verify:signed                  the installers of this version, and the unpacked app
 *   node scripts/check-signatures.js <file or folder> ...
 *
 * It answers one of four things for a file:
 *
 *   trusted    signed, the certificate leads back to an authority this PC
 *              trusts, and the signature carries a timestamp
 *   unstamped  the same, without a timestamp: it stops being good the day the
 *              certificate runs out, which for Azure Artifact Signing is days
 *   untrusted  signed, and Windows does not accept it - the certificate was
 *              made by hand, has run out, or the file changed after signing
 *   unsigned
 *
 * Only the first is fit to give to anybody, and this ends with an error unless
 * every file is. The build runs it by itself when it has been asked to sign
 * (scripts/signing-config.js).
 *
 * WHAT IT CANNOT SAY. "Trusted" is this PC's opinion. Smart App Control, on a
 * customer's PC, accepts a certificate only from a public authority in
 * Microsoft's programme. A certificate somebody made and then added to this
 * PC's trusted list by hand would read as trusted here and be refused there -
 * so a certificate that signs itself is called untrusted here whatever this PC
 * thinks of it.
 *
 * It reads; it changes nothing on the PC and nothing in the files.
 */

const { execFileSync } = require('child_process')
const fs = require('fs')
const path = require('path')

/** The kinds of file Windows checks a signature on. */
const SIGNED_KINDS = ['.exe', '.msi', '.dll', '.node']

/** Files are handed to PowerShell this many at a time: an environment variable holds only so much. */
const AT_A_TIME = 60

// Written for Windows PowerShell 5.1 running in the constrained mode Smart App
// Control puts it in: cmdlets, and methods of the plainest types, nothing else.
// The answer goes out as base64 so that no code page has a say in it. The list
// is put in a variable before it is walked: 5.1 hands a JSON list down a
// pipeline as one thing, and a loop over the pipeline would see one "file".
const ASK = `
$ErrorActionPreference = 'Stop'
$paths = $env:SAV_SIGNATURE_PATHS | ConvertFrom-Json
$rows = foreach ($p in $paths) {
  $s = Get-AuthenticodeSignature -LiteralPath $p
  $c = $s.SignerCertificate
  [pscustomobject]@{
    path    = $p
    status  = [string]$s.Status
    message = [string]$s.StatusMessage
    signer  = if ($c) { $c.Subject } else { $null }
    issuer  = if ($c) { $c.Issuer } else { $null }
    expires = if ($c) { $c.NotAfter.ToString('yyyy-MM-dd') } else { $null }
    stamped = [bool]$s.TimeStamperCertificate
  }
}
$json = ConvertTo-Json -InputObject @($rows) -Compress
[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($json))
`

/** Every file under a folder that Windows checks a signature on. Nothing for a folder that is not there. */
function filesUnder(folder) {
  if (!fs.existsSync(folder)) return []
  const found = []
  for (const entry of fs.readdirSync(folder, { withFileTypes: true })) {
    const full = path.join(folder, entry.name)
    if (entry.isDirectory()) found.push(...filesUnder(full))
    else if (SIGNED_KINDS.includes(path.extname(entry.name).toLowerCase())) found.push(full)
  }
  return found.sort()
}

/** What Windows says of each file. Windows only: nothing else can answer. */
function inspect(files, run = execFileSync, platform = process.platform) {
  if (platform !== 'win32') throw new Error('Signatures can only be checked on Windows.')
  const rows = []
  for (let from = 0; from < files.length; from += AT_A_TIME) {
    const some = files.slice(from, from + AT_A_TIME)
    const out = run('powershell.exe', ['-NoProfile', '-NonInteractive', '-Command', ASK], {
      env: { ...process.env, SAV_SIGNATURE_PATHS: JSON.stringify(some) },
      encoding: 'utf8',
      maxBuffer: 16 * 1024 * 1024,
    })
    rows.push(...JSON.parse(Buffer.from(String(out).trim(), 'base64').toString('utf8')))
  }
  return rows
}

/** trusted | unstamped | untrusted | unsigned */
function verdictOf(row) {
  if (row.status === 'NotSigned' || !row.signer) return 'unsigned'
  if (row.status !== 'Valid') return 'untrusted'
  // No authority stands behind a certificate that signs itself, whatever this PC has been told.
  if (row.signer === row.issuer) return 'untrusted'
  return row.stamped ? 'trusted' : 'unstamped'
}

/** The files that are not fit to give to anybody. */
function refusedOf(rows) {
  return rows.filter((row) => verdictOf(row) !== 'trusted')
}

/** The first name in a certificate's subject, which is who it was issued to. */
function nameIn(subject) {
  const found = /(?:^|,\s*)CN=("[^"]*"|[^,]*)/.exec(subject || '')
  return found ? found[1].replace(/^"|"$/g, '') : (subject || '')
}

function whyOf(row) {
  const verdict = verdictOf(row)
  if (verdict === 'unsigned') return ''
  const by = `${nameIn(row.signer)}, issued by ${nameIn(row.issuer)}`
  if (verdict === 'trusted') return by
  if (verdict === 'unstamped') return `${by} - no timestamp: good only until ${row.expires}`
  if (row.status === 'Valid') return `${by} - the certificate signs itself: no public authority stands behind it`
  return `${by} - ${row.message.replace(/\s+/g, ' ').trim()}`
}

/** One line a file, the ones that are not trusted first. Paths are shown from `base` when they are under it. */
function report(rows, base = '') {
  const shown = (file) => (base && file.startsWith(base + path.sep) ? file.slice(base.length + 1) : file)
  const order = ['unsigned', 'untrusted', 'unstamped', 'trusted']
  const label = { unsigned: 'NOT SIGNED', untrusted: 'NOT TRUSTED', unstamped: 'NO TIMESTAMP', trusted: 'trusted' }
  const lines = rows
    .map((row) => ({ verdict: verdictOf(row), file: shown(row.path), why: whyOf(row) }))
    .sort((a, b) => order.indexOf(a.verdict) - order.indexOf(b.verdict) || a.file.localeCompare(b.file))
  const wide = Math.max(0, ...lines.map((line) => line.file.length))
  return lines
    .map((line) => `  ${label[line.verdict].padEnd(12)}  ${line.file.padEnd(wide)}  ${line.why}`.trimEnd())
    .join('\n')
}

/** With nothing named: the installers of the version in package.json, and the app as it is unpacked. */
function defaultFiles(release, version) {
  if (!fs.existsSync(release)) return []
  const installers = fs.readdirSync(release, { withFileTypes: true })
    .filter((entry) => entry.isFile() && ['.exe', '.msi'].includes(path.extname(entry.name).toLowerCase()))
    .filter((entry) => new RegExp(`(^| )${version.replace(/\./g, '\\.')}\\.(exe|msi)$`, 'i').test(entry.name))
    .map((entry) => path.join(release, entry.name))
    .sort()
  return [...installers, ...filesUnder(path.join(release, 'win-unpacked'))]
}

function main(argv) {
  const desktop = path.join(__dirname, '..')
  const release = path.join(desktop, 'release')
  const { version } = require(path.join(desktop, 'package.json'))
  const files = argv.length
    ? argv.flatMap((given) => {
        const full = path.resolve(given)
        if (!fs.existsSync(full)) throw new Error(`Not found: ${given}`)
        return fs.statSync(full).isDirectory() ? filesUnder(full) : [full]
      })
    : defaultFiles(release, version)
  if (files.length === 0) {
    console.error(argv.length ? 'Nothing there that Windows checks a signature on.'
      : `No installers of version ${version} in ${release}. Build first, or name the files.`)
    return 2
  }
  const rows = inspect(files)
  const refused = refusedOf(rows)
  console.log(report(rows, argv.length ? '' : release))
  console.log(refused.length
    ? `\n${refused.length} of ${rows.length} are not signed in a way Windows trusts. `
      + 'Smart App Control refuses a new build of this app until every file of it is. CODE_SIGNING.md says how.'
    : `\nEvery file checked (${rows.length}) is signed, trusted on this PC and timestamped.`)
  return refused.length ? 1 : 0
}

module.exports = { AT_A_TIME, SIGNED_KINDS, defaultFiles, filesUnder, inspect, nameIn, refusedOf, report, verdictOf }

if (require.main === module) {
  try {
    process.exitCode = main(process.argv.slice(2))
  } catch (err) {
    console.error(err.message)
    process.exitCode = 2
  }
}
