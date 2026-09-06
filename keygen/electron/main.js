'use strict'
/**
 * 7th AI Vision — Licence Key Generator (vendor tool, never distributed).
 *
 * Holds the Ed25519 private key that signs every server and desktop
 * licence. Whoever holds this file and its password can mint licences, so
 * it is deliberately kept out of the customer-facing build pipeline.
 */
const { app, BrowserWindow, ipcMain, clipboard, dialog, shell } = require('electron')
const path = require('path')
const fs = require('fs')
const licence = require('../lib/licence')

// Where the keystore and issue log live. The portable electron-builder
// target unpacks the app into a temp directory that is wiped between runs,
// so writing next to __dirname would silently lose the private key on
// every launch. PORTABLE_EXECUTABLE_DIR is the real folder the user keeps
// the .exe in — that is the only durable location available to us.
function dataDir() {
  if (process.env.PORTABLE_EXECUTABLE_DIR) return process.env.PORTABLE_EXECUTABLE_DIR
  if (app.isPackaged) return path.dirname(app.getPath('exe'))
  return path.join(__dirname, '..')
}

const KEYSTORE_FILE = () => path.join(dataDir(), 'keystore.enc')
const ISSUED_FILE = () => path.join(dataDir(), 'issued-keys.json')

let mainWindow = null
// Unlocked key material lives only in memory, only while the app is open.
let session = null // { privateKey, publicKey, pub }

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 940,
    height: 800,
    minWidth: 760,
    minHeight: 640,
    backgroundColor: '#080818',
    title: '7th AI Vision — Licence Key Generator',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  })
  mainWindow.removeMenu()
  mainWindow.loadFile(path.join(__dirname, '..', 'ui', 'index.html'))
  mainWindow.on('closed', () => { mainWindow = null })
}

app.whenReady().then(createWindow)
app.on('window-all-closed', () => app.quit())
app.on('activate', () => { if (mainWindow === null) createWindow() })

// ── issue log ────────────────────────────────────────────────────────
// A local record of every key minted, so months later it is possible to
// answer "what did we give this customer, and when does it lapse?" without
// asking them to send the key back.

function readIssued() {
  try {
    return JSON.parse(fs.readFileSync(ISSUED_FILE(), 'utf8'))
  } catch (err) {
    return []
  }
}

function appendIssued(entry) {
  const all = readIssued()
  all.unshift(entry)
  fs.writeFileSync(ISSUED_FILE(), JSON.stringify(all, null, 2), 'utf8')
  return all
}

// ── IPC ──────────────────────────────────────────────────────────────

ipcMain.handle('keystore:status', () => ({
  exists: fs.existsSync(KEYSTORE_FILE()),
  unlocked: session !== null,
  publicKey: session ? session.pub : null,
  dataDir: dataDir(),
}))

ipcMain.handle('keystore:create', (_e, password) => {
  if (fs.existsSync(KEYSTORE_FILE())) {
    throw new Error('A keystore already exists in this folder — delete it only if you are certain, every issued key stops verifying without it')
  }
  const { keystore, privateKey, publicKey } = licence.createKeystore(password)
  fs.writeFileSync(KEYSTORE_FILE(), JSON.stringify(keystore, null, 2), 'utf8')
  session = { privateKey, publicKey, pub: keystore.pub }
  return { publicKey: keystore.pub, path: KEYSTORE_FILE() }
})

ipcMain.handle('keystore:unlock', (_e, password) => {
  const keystore = JSON.parse(fs.readFileSync(KEYSTORE_FILE(), 'utf8'))
  const { privateKey, publicKey } = licence.unlockKeystore(keystore, password)
  session = { privateKey, publicKey, pub: keystore.pub }
  return { publicKey: keystore.pub }
})

ipcMain.handle('keystore:lock', () => { session = null; return true })

ipcMain.handle('licence:generate', (_e, form) => {
  if (!session) throw new Error('Unlock the keystore first')
  const licenceId = licence.newLicenceId(form.type)
  const payload = licence.buildPayload({ ...form, licenceId })
  const key = licence.signLicence(payload, session.privateKey)

  // Verify what we just produced before handing it over — cheap, and it
  // means a key that reaches a customer has already been round-tripped.
  const check = licence.verifyLicence(key, session.publicKey)
  if (!check.valid) throw new Error(`Generated key failed its own verification: ${check.error}`)

  appendIssued({
    licence_id: licenceId,
    type: payload.typ,
    customer: payload.cust,
    bind: payload.bind,
    issued: payload.iat,
    expires: payload.exp,
    seats: payload.seats ?? null,
    key,
  })
  return { key, payload }
})

ipcMain.handle('licence:verify', (_e, key) => {
  if (!session) throw new Error('Unlock the keystore first')
  return licence.verifyLicence(key, session.publicKey)
})

ipcMain.handle('licence:issued', () => readIssued())

ipcMain.handle('clipboard:write', (_e, text) => { clipboard.writeText(text); return true })

ipcMain.handle('folder:open', () => shell.openPath(dataDir()))

ipcMain.handle('keystore:backup', async () => {
  const { canceled, filePath } = await dialog.showSaveDialog(mainWindow, {
    title: 'Back up keystore',
    defaultPath: path.join(app.getPath('documents'), 'keystore.enc'),
    filters: [{ name: 'Keystore', extensions: ['enc'] }],
  })
  if (canceled || !filePath) return { saved: false }
  fs.copyFileSync(KEYSTORE_FILE(), filePath)
  return { saved: true, path: filePath }
})
