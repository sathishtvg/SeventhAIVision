'use strict'

const { Tray, Menu, nativeImage, app } = require('electron')
const path = require('path')

let tray = null

function buildMenu(mainWindow) {
  return Menu.buildFromTemplate([
    {
      label: 'Open 7th AI Vision',
      click: () => {
        if (mainWindow) {
          mainWindow.show()
          mainWindow.focus()
        }
      },
    },
    { type: 'separator' },
    {
      label: 'Quit',
      click: () => {
        app.isQuitting = true
        app.quit()
      },
    },
  ])
}

function createTray(mainWindow) {
  // Packaged: icon is in resources/assets; dev: relative to electron/ dir
  const iconPath = app.isPackaged
    ? path.join(process.resourcesPath, 'assets', 'tray-icon.png')
    : path.join(__dirname, '..', 'assets', 'tray-icon.png')

  let icon
  try {
    icon = nativeImage.createFromPath(iconPath)
    if (icon.isEmpty()) icon = nativeImage.createEmpty()
  } catch {
    icon = nativeImage.createEmpty()
  }

  tray = new Tray(icon)
  tray.setToolTip('7th AI Vision')
  tray.setContextMenu(buildMenu(mainWindow))

  tray.on('click', () => {
    if (mainWindow) {
      if (mainWindow.isVisible()) {
        mainWindow.focus()
      } else {
        mainWindow.show()
      }
    }
  })

  return tray
}

function setBadge(count) {
  if (!tray) return
  try {
    if (count > 0) {
      tray.setToolTip(`7th AI Vision — ${count} alert${count > 1 ? 's' : ''}`)
    } else {
      tray.setToolTip('7th AI Vision')
    }
  } catch {}
}

module.exports = { createTray, setBadge }
