'use strict'
/**
 * Licence format self-check. Runs as part of `npm run dist`, so a build
 * cannot ship a generator whose keys the server would reject.
 *
 * The last step writes a real key + public key to disk for the Python
 * cross-check (test/verify_cross.py) — the interop boundary is the part
 * most likely to break silently, since a signature that verifies happily
 * in Node and fails in Python would only surface at a customer site.
 */
const assert = require('assert')
const fs = require('fs')
const path = require('path')
const os = require('os')
const lic = require('../lib/licence')

const PASSWORD = 'test-password-not-a-real-one'
let passed = 0
function check(name, fn) {
  fn()
  passed += 1
  console.log(`  ok  ${name}`)
}

console.log('licence format')

// ── keystore ─────────────────────────────────────────────────
const { keystore, privateKey, publicKey } = lic.createKeystore(PASSWORD)

check('keystore exposes a raw public key', () => {
  assert.strictEqual(typeof keystore.pub, 'string')
  assert.strictEqual(Buffer.from(keystore.pub, 'base64url').length, 32)
})

check('wrong password does not decrypt', () => {
  assert.throws(() => lic.unlockKeystore(keystore, 'wrong-password'), /Wrong password/)
})

check('right password recovers the same key', () => {
  const re = lic.unlockKeystore(keystore, PASSWORD)
  const payload = lic.buildPayload({
    type: 'server', licenceId: 'X', customer: 'C', expires: '2030-01-01',
  })
  // A signature made with the re-opened key must verify against the
  // original public key, or the keystore round-trip lost something.
  const key = lic.signLicence(payload, re.privateKey)
  assert.strictEqual(lic.verifyLicence(key, publicKey).valid, true)
})

check('short password rejected', () => {
  assert.throws(() => lic.createKeystore('short'), /at least 8/)
})

// ── sign / verify ────────────────────────────────────────────
const serverPayload = lic.buildPayload({
  type: 'server',
  licenceId: lic.newLicenceId('server'),
  customer: 'Aegis Security Services',
  bind: 'install-abc-123',
  expires: '2027-08-13',
  modules: ['lpr', 'face', 'intrusion'],
})
const serverKey = lic.signLicence(serverPayload, privateKey)

check('server key verifies', () => {
  const r = lic.verifyLicence(serverKey, publicKey)
  assert.strictEqual(r.valid, true)
  assert.strictEqual(r.payload.cust, 'Aegis Security Services')
  assert.strictEqual(r.payload.typ, 'server')
  assert.deepStrictEqual(r.payload.mods, ['lpr', 'face', 'intrusion'])
})

check('desktop key carries seats', () => {
  const p = lic.buildPayload({
    type: 'desktop', licenceId: lic.newLicenceId('desktop'),
    customer: 'Aegis', bind: 'machine-xyz', expires: '2027-01-01', seats: 3,
  })
  const r = lic.verifyLicence(lic.signLicence(p, privateKey), publicKey)
  assert.strictEqual(r.valid, true)
  assert.strictEqual(r.payload.seats, 3)
})

check('non-ASCII customer name survives the round trip', () => {
  // The reason the signature covers the encoded payload rather than a
  // re-serialised object: Python escapes non-ASCII by default and JS does
  // not, so this exact case would fail under naive canonicalisation.
  const p = lic.buildPayload({
    type: 'server', licenceId: 'X', customer: 'Sécurité Montréal Ltée', expires: '2030-01-01',
  })
  const r = lic.verifyLicence(lic.signLicence(p, privateKey), publicKey)
  assert.strictEqual(r.valid, true)
  assert.strictEqual(r.payload.cust, 'Sécurité Montréal Ltée')
})

check('tampered payload fails', () => {
  const [prefix, body, sig] = serverKey.split('.')
  const forged = JSON.parse(Buffer.from(body, 'base64url').toString())
  forged.exp = '2099-12-31'                       // the obvious attack
  const evil = `${prefix}.${Buffer.from(JSON.stringify(forged)).toString('base64url')}.${sig}`
  const r = lic.verifyLicence(evil, publicKey)
  assert.strictEqual(r.valid, false)
  assert.match(r.error, /Signature does not match/)
})

check('key from a different keypair fails', () => {
  const other = lic.generateKeypair()
  const foreign = lic.signLicence(serverPayload, other.privateKey)
  assert.strictEqual(lic.verifyLicence(foreign, publicKey).valid, false)
})

check('malformed input is rejected, not thrown', () => {
  for (const bad of ['', 'nonsense', 'SAV1.only-two', 'SAV9.a.b']) {
    assert.strictEqual(lic.verifyLicence(bad, publicKey).valid, false)
  }
})

check('expiry is not enforced by verify', () => {
  // Deliberate: an expired key must still be *readable*, so the server can
  // say "your licence lapsed on X" instead of "this key is invalid".
  const p = lic.buildPayload({
    type: 'server', licenceId: 'X', customer: 'C', expires: '2000-01-01',
  })
  assert.strictEqual(lic.verifyLicence(lic.signLicence(p, privateKey), publicKey).valid, true)
})

check('bad expiry format rejected at build time', () => {
  assert.throws(() => lic.buildPayload({
    type: 'server', licenceId: 'X', customer: 'C', expires: '13/08/2026',
  }), /YYYY-MM-DD/)
})

// ── hand-off for the Python cross-check ──────────────────────
const outDir = path.join(os.tmpdir(), 'sav-licence-crosscheck')
fs.mkdirSync(outDir, { recursive: true })
fs.writeFileSync(path.join(outDir, 'fixture.json'), JSON.stringify({
  public_key: keystore.pub,
  server_key: serverKey,
  expected_customer: 'Aegis Security Services',
}, null, 2))

console.log(`\n${passed} checks passed`)
console.log(`cross-check fixture: ${path.join(outDir, 'fixture.json')}`)
