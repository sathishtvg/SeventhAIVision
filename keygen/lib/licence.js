'use strict'
/**
 * Licence key format — the contract shared by this generator and the
 * Seventh AI Vision backend (backend/app/services/licensing.py).
 *
 * A key looks like:
 *
 *     SAV1.<base64url(payload JSON)>.<base64url(Ed25519 signature)>
 *
 * The signature covers the ASCII bytes of "SAV1.<base64url(payload)>" —
 * the already-encoded string, not a re-serialised object. That detail is
 * load-bearing: if each side re-serialised the JSON before verifying, the
 * two languages would have to agree byte-for-byte on key order, spacing
 * and non-ASCII escaping (Python's json.dumps escapes non-ASCII by
 * default, JavaScript's JSON.stringify does not), and a customer name with
 * an accent in it would silently fail to verify. Signing the encoded form
 * removes that whole class of bug. Same reasoning as JWS compact form.
 *
 * The version prefix is inside the signed region so it cannot be swapped
 * for a future, weaker format without breaking the signature.
 */
const crypto = require('crypto')

const PREFIX = 'SAV1'
const KEY_TYPES = ['server', 'desktop']

// ── base64url ────────────────────────────────────────────────────────
const b64u = (buf) => Buffer.from(buf).toString('base64url')
const unb64u = (str) => Buffer.from(str, 'base64url')

// ── keypair ──────────────────────────────────────────────────────────

/** A fresh Ed25519 keypair. The private half never leaves this machine. */
function generateKeypair() {
  const { publicKey, privateKey } = crypto.generateKeyPairSync('ed25519')
  return { publicKey, privateKey }
}

/**
 * The public key as raw 32 bytes, base64url — the form pasted into the
 * server's LICENCE_PUBLIC_KEY setting. JWK is used to reach the raw bytes
 * because the SPKI DER form carries a 12-byte algorithm header the Python
 * side would have to strip by offset, which is exactly the kind of
 * brittle assumption that breaks on a library upgrade.
 */
function exportPublicKey(publicKey) {
  return publicKey.export({ format: 'jwk' }).x
}

function importPublicKey(rawB64u) {
  return crypto.createPublicKey({
    key: { kty: 'OKP', crv: 'Ed25519', x: rawB64u },
    format: 'jwk',
  })
}

// ── sign / verify ────────────────────────────────────────────────────

/**
 * @param {object} payload  license claims (see buildPayload)
 * @param {crypto.KeyObject} privateKey
 * @returns {string} the licence key
 */
function signLicence(payload, privateKey) {
  const body = `${PREFIX}.${b64u(JSON.stringify(payload))}`
  // Ed25519 hashes internally, so the digest argument must be null.
  const sig = crypto.sign(null, Buffer.from(body, 'ascii'), privateKey)
  return `${body}.${b64u(sig)}`
}

/**
 * Verifies signature and structure only. Expiry is deliberately NOT checked
 * here: the generator needs to re-read keys it issued long ago, and the
 * server checks expiry against its own tamper-guarded clock. Mixing the two
 * would mean a valid-but-expired key looked identical to a forged one.
 *
 * @returns {{valid: boolean, payload?: object, error?: string}}
 */
function verifyLicence(key, publicKey) {
  if (typeof key !== 'string') return { valid: false, error: 'Key must be a string' }
  const parts = key.trim().split('.')
  if (parts.length !== 3) return { valid: false, error: 'Malformed key' }
  const [prefix, payloadB64, sigB64] = parts
  if (prefix !== PREFIX) return { valid: false, error: `Unsupported key version "${prefix}"` }

  let ok = false
  try {
    ok = crypto.verify(
      null,
      Buffer.from(`${prefix}.${payloadB64}`, 'ascii'),
      publicKey,
      unb64u(sigB64)
    )
  } catch (err) {
    return { valid: false, error: 'Signature could not be checked' }
  }
  if (!ok) return { valid: false, error: 'Signature does not match — key was altered or is not yours' }

  let payload
  try {
    payload = JSON.parse(unb64u(payloadB64).toString('utf8'))
  } catch (err) {
    return { valid: false, error: 'Payload is not valid JSON' }
  }
  return { valid: true, payload }
}

// ── payload ──────────────────────────────────────────────────────────

/**
 * Short keys are field-typed, so claim names stay abbreviated.
 * `bind` empty means an unbound key — it will run on any install, which is
 * convenient for a trial and a leak risk for a sale.
 */
function buildPayload({ type, licenceId, customer, bind, issued, expires, seats, modules }) {
  if (!KEY_TYPES.includes(type)) throw new Error(`type must be one of ${KEY_TYPES.join(', ')}`)
  if (!customer || !customer.trim()) throw new Error('Customer name is required')
  if (!expires) throw new Error('Expiry date is required')
  if (!/^\d{4}-\d{2}-\d{2}$/.test(expires)) throw new Error('Expiry must be YYYY-MM-DD')

  const payload = {
    v: 1,
    typ: type,
    lic: licenceId,
    cust: customer.trim(),
    bind: (bind || '').trim(),
    iat: issued || new Date().toISOString().slice(0, 10),
    exp: expires,
  }
  if (type === 'desktop' && seats) payload.seats = Number(seats)
  if (type === 'server' && modules && modules.length) payload.mods = modules
  return payload
}

/** Human-readable licence id, e.g. SAV-SRV-20260813-4F2A. */
function newLicenceId(type) {
  const tag = type === 'server' ? 'SRV' : 'DSK'
  const date = new Date().toISOString().slice(0, 10).replace(/-/g, '')
  const rand = crypto.randomBytes(2).toString('hex').toUpperCase()
  return `SAV-${tag}-${date}-${rand}`
}

// ── encrypted keystore ───────────────────────────────────────────────
// The private key at rest. scrypt turns the password into a key-encryption
// key; AES-256-GCM gives tamper detection for free, so a corrupted or
// edited keystore fails loudly instead of yielding a garbage private key.
//
// There is no separate "is the password correct" check anywhere, and that
// is on purpose: the password IS the decryption key, so a wrong one simply
// fails to decrypt. Nothing to compare means nothing to patch out.

const SCRYPT = { N: 16384, r: 8, p: 1, keylen: 32 }

function deriveKey(password, salt) {
  return crypto.scryptSync(password, salt, SCRYPT.keylen, {
    N: SCRYPT.N, r: SCRYPT.r, p: SCRYPT.p,
    maxmem: 256 * 1024 * 1024,
  })
}

function createKeystore(password) {
  if (!password || password.length < 8) throw new Error('Password must be at least 8 characters')
  const { publicKey, privateKey } = generateKeypair()
  const pkcs8 = privateKey.export({ format: 'der', type: 'pkcs8' })

  const salt = crypto.randomBytes(16)
  const iv = crypto.randomBytes(12)
  const cipher = crypto.createCipheriv('aes-256-gcm', deriveKey(password, salt), iv)
  const ct = Buffer.concat([cipher.update(pkcs8), cipher.final()])

  return {
    keystore: {
      v: 1,
      kdf: 'scrypt',
      scrypt: { N: SCRYPT.N, r: SCRYPT.r, p: SCRYPT.p },
      salt: salt.toString('base64'),
      iv: iv.toString('base64'),
      tag: cipher.getAuthTag().toString('base64'),
      ct: ct.toString('base64'),
      pub: exportPublicKey(publicKey),
      created_at: new Date().toISOString(),
    },
    publicKey,
    privateKey,
  }
}

function unlockKeystore(keystore, password) {
  const salt = Buffer.from(keystore.salt, 'base64')
  const iv = Buffer.from(keystore.iv, 'base64')
  const decipher = crypto.createDecipheriv('aes-256-gcm', deriveKey(password, salt), iv)
  decipher.setAuthTag(Buffer.from(keystore.tag, 'base64'))
  let pkcs8
  try {
    pkcs8 = Buffer.concat([
      decipher.update(Buffer.from(keystore.ct, 'base64')),
      decipher.final(),
    ])
  } catch (err) {
    // GCM tag mismatch. Overwhelmingly the wrong password; could also be a
    // damaged file, which the caller cannot fix either way.
    throw new Error('Wrong password, or the keystore file is damaged')
  }
  const privateKey = crypto.createPrivateKey({ key: pkcs8, format: 'der', type: 'pkcs8' })
  return { privateKey, publicKey: importPublicKey(keystore.pub) }
}

module.exports = {
  PREFIX, KEY_TYPES,
  generateKeypair, exportPublicKey, importPublicKey,
  signLicence, verifyLicence,
  buildPayload, newLicenceId,
  createKeystore, unlockKeystore,
}
