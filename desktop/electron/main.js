'use strict'

// NOTE: `screen` is deliberately NOT destructured here. Electron documents it
// as unusable until the app 'ready' event, and destructuring at module load
// invokes its lazy getter — which would fail before the app is ready and take
// the whole main process down at startup. It's required on demand inside
// placeOnDisplay() instead, which only ever runs post-ready via IPC.
const { app, BrowserWindow, ipcMain, Notification, shell, dialog, session, protocol, net } = require('electron')
const path = require('path')
const fs = require('fs')
const Store = require('electron-store')
const { createTray, setBadge } = require('./tray')

// Must be called before app is ready so Electron registers the scheme.
// This lets /assets/... absolute paths in the built index.html resolve correctly
// under file:// by mapping app:// → desktop/dist/.
protocol.registerSchemesAsPrivileged([
  {
    scheme: 'app',
    privileges: { secure: true, standard: true, supportFetchAPI: true, corsEnabled: true, stream: true },
  },
])

// electron-updater: loaded lazily so dev runs without a GitHub release URL
let autoUpdater = null
try { autoUpdater = require('electron-updater').autoUpdater } catch (_) {}

const store = new Store({
  schema: {
    serverUrl: {
      type: 'string',
      default: '',
    },
    autoLaunch: {
      type: 'boolean',
      default: false,
    },
  },
})

let mainWindow = null
let setupWindow = null
// Secondary windows for multi-monitor control-room setups (Live Wall,
// Attendance, Command Centre, Action Center) — kept in one array purely so
// they aren't garbage-collected while open; closing one just drops it from
// the list, it does not affect mainWindow. A single generic tracking array
// (rather than one per page) is what lets createSecondaryWindow stay one
// function instead of forking per caller.
const secondaryWindows = []

// ── First-run server URL wizard ───────────────────────────────
function createSetupWindow() {
  setupWindow = new BrowserWindow({
    width: 480,
    height: 400,
    resizable: false,
    frame: false,
    transparent: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  })

  // Inline HTML for server URL entry — glassmorphism styled
  setupWindow.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(`
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
    background: linear-gradient(135deg, #020617 0%, #0D0826 50%, #020B17 100%);
    color: #F8FAFC;
    display: flex; flex-direction: column;
    align-items: center; justify-content: center;
    height: 100vh; padding: 28px;
    -webkit-app-region: drag;
    overflow: hidden;
    position: relative;
  }
  .blob1 {
    position: absolute; width: 260px; height: 260px; border-radius: 50%;
    background: rgba(108,99,255,0.12); top: -80px; right: -60px;
    pointer-events: none;
  }
  .blob2 {
    position: absolute; width: 200px; height: 200px; border-radius: 50%;
    background: rgba(0,217,192,0.08); bottom: -60px; left: -70px;
    pointer-events: none;
  }
  .card {
    width: 100%; max-width: 380px;
    background: rgba(255,255,255,0.10);
    backdrop-filter: blur(20px) saturate(180%);
    -webkit-backdrop-filter: blur(20px) saturate(180%);
    border: 1px solid rgba(255,255,255,0.18);
    border-radius: 20px;
    padding: 28px;
    position: relative; z-index: 1;
    -webkit-app-region: no-drag;
  }
  .logo-row {
    display: flex; align-items: center; gap: 10px; margin-bottom: 16px;
  }
  .logo-icon {
    width: 40px; height: 40px; border-radius: 11px;
    background: rgba(108,99,255,0.18);
    border: 1px solid rgba(108,99,255,0.35);
    display: flex; align-items: center; justify-content: center; flex-shrink: 0;
  }
  .logo-icon svg { width: 20px; height: 20px; }
  .brand { font-size: 16px; font-weight: 700; color: #F8FAFC; letter-spacing: -0.4px; }
  .brand-sub { font-size: 10px; color: rgba(248,250,252,0.5); letter-spacing: 1px; margin-top: 2px; }
  .accent {
    height: 1.5px; width: 100%;
    background: linear-gradient(90deg, #6C63FF, #00D9C0);
    border-radius: 1px; margin: 14px 0; opacity: 0.8;
  }
  .field-label {
    font-size: 11px; color: rgba(248,250,252,0.55);
    letter-spacing: 0.3px; margin-bottom: 6px;
  }
  .input-wrap {
    display: flex; align-items: center; gap: 8px;
    background: rgba(255,255,255,0.06);
    border: 1px solid rgba(255,255,255,0.12);
    border-radius: 8px;
    padding: 0 12px; margin-bottom: 16px;
  }
  .input-wrap svg { flex-shrink: 0; opacity: 0.5; }
  input {
    flex: 1; border: none; background: transparent;
    color: #F8FAFC; font-size: 13px;
    padding: 10px 0; outline: none; font-family: inherit;
  }
  input::placeholder { color: rgba(248,250,252,0.30); }
  .input-wrap:focus-within { border-color: rgba(108,99,255,0.50); }
  .hint { font-size: 11px; color: rgba(248,250,252,0.40); margin-bottom: 18px; line-height: 1.5; }
  .btn {
    width: 100%; padding: 11px; border: none; border-radius: 8px;
    background: linear-gradient(90deg, #6C63FF, #00D9C0);
    color: #fff; font-size: 13px; font-weight: 700;
    cursor: pointer; letter-spacing: 0.4px;
    transition: opacity 0.15s;
  }
  .btn:hover { opacity: 0.88; }
  .btn:active { opacity: 0.75; }
</style>
</head>
<body>
  <div class="blob1"></div>
  <div class="blob2"></div>
  <div class="card">
    <div class="logo-row">
      <div class="logo-icon">
        <svg viewBox="0 0 24 24" fill="none" stroke="#6C63FF" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>
          <path d="m9 12 2 2 4-4" stroke="#00D9C0"/>
        </svg>
      </div>
      <div>
        <div class="brand">Seventh AI Vision</div>
        <div class="brand-sub">SECURITY OPERATIONS</div>
      </div>
    </div>
    <div class="accent"></div>
    <div class="field-label">Backend Server URL</div>
    <div class="input-wrap">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#F8FAFC" stroke-width="2" stroke-linecap="round">
        <rect x="2" y="2" width="20" height="8" rx="2"/><rect x="2" y="14" width="20" height="8" rx="2"/>
        <line x1="6" y1="6" x2="6.01" y2="6"/><line x1="6" y1="18" x2="6.01" y2="18"/>
      </svg>
      <input id="url" type="url" placeholder="http://192.168.1.100:8000" value="http://localhost:8000" autofocus />
    </div>
    <div class="hint">Enter the IP address and port of your 7th AI Vision backend server.<br>Default port is <strong style="color:rgba(248,250,252,0.7)">8000</strong></div>
    <button class="btn" onclick="connect()">Connect to Server</button>
  </div>
  <script>
    async function connect() {
      const url = document.getElementById('url').value.trim()
      if (!url) return
      await window.electronAPI.setServerUrl(url)
      window.location.reload()
    }
    document.getElementById('url').addEventListener('keydown', e => {
      if (e.key === 'Enter') connect()
    })
  </script>
</body>
</html>
  `)}`)
}

// ── Main application window ───────────────────────────────────
function createMainWindow(serverUrl) {
  mainWindow = new BrowserWindow({
    width: 1400,
    height: 900,
    minWidth: 1024,
    minHeight: 600,
    title: '7th AI Vision',
    backgroundColor: '#020617',
    autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      // Control-room siren must sound without a prior click (Gap 83)
      autoplayPolicy: 'no-user-gesture-required',
    },
    show: false,
  })

  // Esc exits kiosk mode (the toolbar is hidden in kiosk, so Esc is the way out)
  mainWindow.webContents.on('before-input-event', (_event, input) => {
    if (input.type === 'keyDown' && input.key === 'Escape' && mainWindow.isKiosk()) {
      mainWindow.setKiosk(false)
    }
  })

  // Load the bundled React frontend via the custom app:// scheme so that
  // the built index.html's absolute /assets/... paths resolve correctly.
  mainWindow.loadURL('app://./index.html')

  mainWindow.once('ready-to-show', () => {
    mainWindow.show()
    if (setupWindow && !setupWindow.isDestroyed()) {
      setupWindow.close()
    }
  })

  // Open external links in the system browser, not in Electron
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url)
    return { action: 'deny' }
  })

  // Minimize-to-tray on close
  mainWindow.on('close', (e) => {
    if (!app.isQuitting) {
      e.preventDefault()
      mainWindow.hide()
    }
  })

  return mainWindow
}

// ── Secondary windows (multi-monitor control-room) ─────────────
// Generic pop-out used by every "open in a new window" caller — Live Wall
// (optionally with ?layout=<id>), Attendance, Command Centre, Action
// Center. Independent, ordinary windows (no kiosk/tray coupling to
// mainWindow) so an operator can drag each one to a different physical
// monitor. routePath is an opaque app-relative path + query string built
// by the renderer (e.g. "/live?layout=abc123") — this function doesn't
// need to know what any of the query params mean.
function createSecondaryWindow(routePath, title) {
  const win = new BrowserWindow({
    width: 1280,
    height: 800,
    minWidth: 640,
    minHeight: 480,
    title: title || '7th AI Vision',
    backgroundColor: '#020617',
    autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      autoplayPolicy: 'no-user-gesture-required',
    },
    show: false,
  })

  win.webContents.on('before-input-event', (_event, input) => {
    if (input.type === 'keyDown' && input.key === 'Escape' && win.isKiosk()) {
      win.setKiosk(false)
    }
  })

  // A real path, not a hash — this app uses BrowserRouter (reads
  // location.pathname), and the SPA-fallback protocol handler above
  // serves index.html for it while preserving this path/query in the
  // renderer's actual location for React Router to read.
  win.loadURL('app://.' + routePath)

  win.once('ready-to-show', () => win.show())
  win.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url)
    return { action: 'deny' }
  })

  secondaryWindows.push(win)
  win.on('closed', () => {
    const idx = secondaryWindows.indexOf(win)
    if (idx !== -1) secondaryWindows.splice(idx, 1)
  })

  placeOnDisplay(win)

  return win
}

// Multi-monitor control-room placement: spread secondary windows across
// distinct physical displays when more than one is connected — this is
// what makes a multi-screen Live Wall profile (or several pages opened
// side-by-side) auto-arrange itself onto separate monitors instead of
// stacking every new window on top of the primary display. No-ops on a
// single-display machine (the default OS placement is fine there).
function placeOnDisplay(win) {
  // Required lazily — see the note on the electron require at the top of this file.
  const { screen } = require('electron')
  const displays = screen.getAllDisplays()
  if (displays.length <= 1) return
  // secondaryWindows already includes `win` (pushed just before this is
  // called), so its own position in the array picks the next display,
  // cycling back around once there are more windows than displays.
  const slot = secondaryWindows.length - 1
  const display = displays[slot % displays.length]
  const { x, y, width, height } = display.workArea
  const w = Math.min(1280, width - 40)
  const h = Math.min(800, height - 40)
  win.setBounds({
    x: x + Math.floor((width - w) / 2),
    y: y + Math.floor((height - h) / 2),
    width: w,
    height: h,
  })
}

// ── Auto-updater ──────────────────────────────────────────────
function initAutoUpdater() {
  if (!autoUpdater) return

  autoUpdater.autoDownload = false
  autoUpdater.logger = null  // suppress console noise in prod

  autoUpdater.on('update-available', (info) => {
    if (!mainWindow) return
    dialog.showMessageBox(mainWindow, {
      type: 'info',
      title: 'Update Available',
      message: `Version ${info.version} is available.`,
      detail: 'A new version of 7th AI Vision has been released.',
      buttons: ['Download & Install', 'Later'],
      defaultId: 0,
    }).then(({ response }) => {
      if (response === 0) autoUpdater.downloadUpdate()
    })
  })

  autoUpdater.on('update-downloaded', () => {
    dialog.showMessageBox(mainWindow, {
      type: 'info',
      title: 'Update Ready',
      message: 'Update downloaded. The application will restart to install it.',
      buttons: ['Restart Now', 'Later'],
      defaultId: 0,
    }).then(({ response }) => {
      if (response === 0) {
        app.isQuitting = true
        autoUpdater.quitAndInstall()
      }
    })
  })

  // Check silently — don't show a dialog if already up to date
  autoUpdater.checkForUpdates().catch(() => {})
}

// ── App lifecycle ─────────────────────────────────────────────
app.whenReady().then(() => {
  // Serve the built React app via a custom app:// scheme so that absolute asset
  // paths like /assets/index.js resolve correctly under Electron's file:// origin
  // without having to change Vite's build output or the nginx deployment.
  const DIST_ROOT = path.join(__dirname, '../dist')
  protocol.handle('app', (request) => {
    let { pathname } = new URL(request.url)
    // Strip leading slash so path.join doesn't treat it as absolute
    if (pathname.startsWith('/')) pathname = pathname.slice(1)
    let filePath = path.join(DIST_ROOT, pathname || 'index.html')
    // SPA fallback: client-side routes (e.g. /live) aren't real files on
    // disk — serve index.html for those so BrowserRouter can take over
    // and read the intended path from the window's actual location
    // (only the response body changes here; the address bar/location the
    // renderer sees still reflects the originally requested path).
    if (!fs.existsSync(filePath) || fs.statSync(filePath).isDirectory()) {
      filePath = path.join(DIST_ROOT, 'index.html')
    }
    return net.fetch(`file:///${filePath.replace(/\\/g, '/')}`)
  })

  // Patch CORS response headers so the app:// origin can reach the backend.
  // This is narrower than webSecurity:false — it only modifies CORS headers on
  // responses; XSS protection, CSP, and the renderer sandbox are unaffected.
  session.defaultSession.webRequest.onHeadersReceived((details, callback) => {
    callback({
      responseHeaders: {
        ...details.responseHeaders,
        'access-control-allow-origin':  ['*'],
        'access-control-allow-methods': ['GET, POST, PUT, PATCH, DELETE, OPTIONS'],
        'access-control-allow-headers': ['Content-Type, Authorization'],
      },
    })
  })

  const serverUrl = store.get('serverUrl')

  if (!serverUrl) {
    createSetupWindow()
  } else {
    createMainWindow(serverUrl)
    createTray(mainWindow)
  }

  app.on('activate', () => {
    if (mainWindow) mainWindow.show()
  })

  // Check for updates 10 seconds after launch (only in packaged builds)
  if (app.isPackaged) {
    setTimeout(initAutoUpdater, 10_000)
  }
})

app.on('window-all-closed', () => {
  // Don't quit when all windows are closed on Windows — live in the tray
  if (process.platform !== 'darwin') return
  app.quit()
})

app.on('before-quit', () => {
  app.isQuitting = true
})

// ── IPC Handlers ─────────────────────────────────────────────

ipcMain.handle('get-server-url', () => store.get('serverUrl'))

// Synchronous version so preload.js can expose serverUrl before React boots
ipcMain.on('get-server-url-sync', (event) => {
  event.returnValue = store.get('serverUrl') || ''
})

ipcMain.handle('set-server-url', async (_event, url) => {
  store.set('serverUrl', url)
  if (setupWindow && !setupWindow.isDestroyed()) {
    setupWindow.close()
  }
  createMainWindow(url)
  createTray(mainWindow)
})

ipcMain.handle('show-notification', (_event, { title, body }) => {
  if (Notification.isSupported()) {
    new Notification({ title, body, silent: false }).show()
  }
})

ipcMain.handle('set-badge-count', (_event, count) => {
  setBadge(count)
  // app.setBadgeCount is macOS/Linux only; on Windows we use the tray tooltip
})

// Resolves the window from the IPC event's own sender rather than the
// module-level `mainWindow` reference — previously this always kiosked
// mainWindow regardless of which window's Full Screen button was actually
// clicked, so a secondary window's own toggle silently did nothing useful.
// BrowserWindow.fromWebContents(event.sender) is always the window that
// made the call, whether that's mainWindow or any secondary window.
ipcMain.handle('set-kiosk', (event, enabled) => {
  const win = BrowserWindow.fromWebContents(event.sender)
  if (!win || win.isDestroyed()) return false
  win.setKiosk(Boolean(enabled))
  return win.isKiosk()
})

ipcMain.handle('open-secondary-window', (_event, routePath) => {
  createSecondaryWindow(String(routePath || '/'))
})

ipcMain.handle('get-auto-launch', () => store.get('autoLaunch'))

ipcMain.handle('set-auto-launch', (_event, enabled) => {
  store.set('autoLaunch', enabled)
  app.setLoginItemSettings({
    openAtLogin: enabled,
    path: app.getPath('exe'),
  })
})
