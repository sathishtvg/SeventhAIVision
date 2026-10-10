'use strict'
/**
 * How the Windows build is signed: read from the environment when it is built.
 *
 * electron-builder.yml names this file as the configuration it builds on, so
 * every way of building - `npm run dist`, or electron-builder run by hand -
 * signs when the environment says how, and builds unsigned, as it always has,
 * when it says nothing.
 *
 * WHY. On 10 October 2026 Windows refused the 1.0.7 installer on the machine
 * it was built on ("An Application Control policy has blocked this file").
 * Smart App Control lets a program run when Microsoft's service already knows
 * the file, or when it carries a signature from a public certificate
 * authority. A build made a minute ago is known to nobody, so an unsigned one
 * is refused until the service has made its mind up - which took a quarter of
 * an hour that day, and is promised by nobody. A signature is the only thing
 * that makes it run the first time. CODE_SIGNING.md says how to get one.
 *
 * THREE WAYS, one at a time:
 *
 *   azure  Azure Artifact Signing (it was called Trusted Signing). The key
 *          stays with Microsoft; the build asks the service to sign.
 *            AZURE_SIGNING_ENDPOINT  AZURE_SIGNING_ACCOUNT  AZURE_SIGNING_PROFILE
 *            AZURE_TENANT_ID  AZURE_CLIENT_ID  and AZURE_CLIENT_SECRET
 *
 *   store  A certificate Windows can see in its certificate store - which is
 *          how one bought from a certificate authority arrives now: on a USB
 *          token, or through the authority's cloud key service.
 *            WIN_CERT_SHA1 (its thumbprint)  or  WIN_CERT_SUBJECT (its name)
 *
 *   file   A .pfx file. Authorities stopped handing these out in June 2023;
 *          it remains for a certificate older than that.
 *            CSC_LINK  CSC_KEY_PASSWORD   (electron-builder reads these itself)
 *
 * WHEN ONE IS SET, three things follow that an unsigned build does not have:
 *
 *   - every .dll is signed as well as every .exe. Five of the libraries that
 *     come with Electron are not signed by anybody, and Smart App Control
 *     looks at each file a program loads, not only at the one that was started;
 *   - a file that could not be signed fails the build (forceCodeSigning),
 *     where it would otherwise be left unsigned without a word;
 *   - when the build is done, Windows is asked about every file it made
 *     (scripts/check-signatures.js), and the build fails if any of them is not
 *     trusted. An installer that only looks signed is worse than an unsigned one.
 *
 * Half-set is an error, not an unsigned build: a release that was meant to be
 * signed must not quietly come out unsigned because one name was misspelt.
 *
 * Nothing secret is read into this file's result except what electron-builder
 * needs to be told, and none of it is printed.
 */

const path = require('path')

/** Microsoft's timestamp service. Without a timestamp a signature dies with its
 * certificate, and an Artifact Signing certificate lives three days. */
const AZURE_TIMESTAMP = 'http://timestamp.acs.microsoft.com'

const AZURE_PLACE = ['AZURE_SIGNING_ENDPOINT', 'AZURE_SIGNING_ACCOUNT', 'AZURE_SIGNING_PROFILE']
const AZURE_WHO = ['AZURE_TENANT_ID', 'AZURE_CLIENT_ID']
/** Any one of these proves who is asking; the first is the usual one. */
const AZURE_PROOF = ['AZURE_CLIENT_SECRET', 'AZURE_CLIENT_CERTIFICATE_PATH', 'AZURE_USERNAME']
const STORE = ['WIN_CERT_SHA1', 'WIN_CERT_SUBJECT']
const FILE = ['CSC_LINK', 'WIN_CSC_LINK']

/** The file kinds Windows checks a signature on, beyond the .exe that is always signed. */
const ALSO_SIGNED = ['.dll', '.node']

const value = (env, name) => String(env[name] == null ? '' : env[name]).trim()
const given = (env, names) => names.filter((name) => value(env, name) !== '')

class SigningConfigError extends Error {}

/**
 * A thumbprint as Windows writes it has spaces, and one copied out of the
 * certificate window often begins with a mark that cannot be seen. What is
 * left when everything but the forty digits is taken away is the thumbprint.
 */
function thumbprintOf(text) {
  const digits = String(text).replace(/[^0-9a-fA-F]/g, '').toUpperCase()
  if (digits.length !== 40) {
    throw new SigningConfigError(
      `WIN_CERT_SHA1 is a certificate's thumbprint: forty characters, 0-9 and A-F. What is set has ${digits.length}.`)
  }
  return digits
}

/** Which way the environment asks for. Throws when it asks for two, or for half of one. */
function signingOf(env = process.env) {
  const asked = {
    azure: given(env, AZURE_PLACE),
    store: given(env, STORE),
    file: given(env, FILE),
  }
  const ways = Object.keys(asked).filter((way) => asked[way].length > 0)
  if (ways.length === 0) return { way: 'none' }
  if (ways.length > 1) {
    const named = ways.map((way) => `${way} (${asked[way].join(', ')})`).join(' and ')
    throw new SigningConfigError(`Signing is asked for in more than one way: ${named}. Set the variables of one.`)
  }

  const publisher = value(env, 'WIN_PUBLISHER_NAME') || null
  const [way] = ways

  if (way === 'azure') {
    const missing = [...AZURE_PLACE, ...AZURE_WHO].filter((name) => value(env, name) === '')
    if (given(env, AZURE_PROOF).length === 0) missing.push('AZURE_CLIENT_SECRET')
    if (missing.length) {
      throw new SigningConfigError(`Azure Artifact Signing is asked for, and these are not set: ${missing.join(', ')}.`)
    }
    const endpoint = value(env, 'AZURE_SIGNING_ENDPOINT')
    if (!/^https:\/\/[a-z0-9-]+\.codesigning\.azure\.net\/?$/i.test(endpoint)) {
      throw new SigningConfigError(
        'AZURE_SIGNING_ENDPOINT is the address of the region the signing account was made in, '
        + 'such as https://eus.codesigning.azure.net.')
    }
    return {
      way,
      publisher,
      endpoint,
      account: value(env, 'AZURE_SIGNING_ACCOUNT'),
      profile: value(env, 'AZURE_SIGNING_PROFILE'),
    }
  }

  if (way === 'store') {
    if (asked.store.length > 1) {
      throw new SigningConfigError('Set WIN_CERT_SHA1 or WIN_CERT_SUBJECT, not both: the thumbprint is the surer of the two.')
    }
    return value(env, 'WIN_CERT_SHA1')
      ? { way, publisher, thumbprint: thumbprintOf(value(env, 'WIN_CERT_SHA1')) }
      : { way, publisher, subject: value(env, 'WIN_CERT_SUBJECT') }
  }

  // The password is electron-builder's to read (CSC_KEY_PASSWORD). A wrong or missing one fails the build there.
  return { way, publisher }
}

/** The file names a finished build made that Windows checks a signature on. */
function builtFiles(buildResult) {
  const { filesUnder, SIGNED_KINDS } = require('./check-signatures')
  const made = (buildResult.artifactPaths || []).filter((file) => SIGNED_KINDS.includes(path.extname(file).toLowerCase()))
  return Array.from(new Set([...made, ...filesUnder(path.join(buildResult.outDir, 'win-unpacked'))]))
}

/** Run by electron-builder when everything is built: Windows is asked about each file. */
async function checkWhatWasBuilt(buildResult) {
  const { inspect, refusedOf, report } = require('./check-signatures')
  const rows = inspect(builtFiles(buildResult))
  console.log(`\nSignatures on what was built:\n${report(rows, buildResult.outDir)}\n`)
  const refused = refusedOf(rows)
  if (rows.length === 0 || refused.length) {
    throw new Error(rows.length === 0
      ? 'Signing was asked for and the build made nothing to check.'
      : `${refused.length} of the ${rows.length} files this build made are not signed in a way Windows trusts. `
        + 'They are listed above. The installers are in the output folder and must not be given to anybody.')
  }
  return []
}

/** What is added to electron-builder.yml for this environment: nothing at all when no signing is asked for. */
function configOf(env = process.env) {
  const signing = signingOf(env)
  if (signing.way === 'none') return {}

  const win = { signExts: ALSO_SIGNED }
  const signtool = {}
  // The name updates are checked against. Left out, it is read from the certificate.
  if (signing.publisher) signtool.publisherName = signing.publisher
  if (signing.way === 'store') {
    if (signing.thumbprint) signtool.certificateSha1 = signing.thumbprint
    else signtool.certificateSubjectName = signing.subject
  }
  if (signing.way === 'azure') {
    win.azureSignOptions = {
      endpoint: signing.endpoint,
      codeSigningAccountName: signing.account,
      certificateProfileName: signing.profile,
      TimestampRfc3161: AZURE_TIMESTAMP,
      TimestampDigest: 'SHA256',
    }
  }
  if (Object.keys(signtool).length) win.signtoolOptions = signtool

  return { forceCodeSigning: true, win, afterAllArtifactBuild: checkWhatWasBuilt }
}

module.exports = () => configOf()
Object.assign(module.exports, {
  ALSO_SIGNED, AZURE_TIMESTAMP, SigningConfigError, builtFiles, checkWhatWasBuilt, configOf, signingOf, thumbprintOf,
})
