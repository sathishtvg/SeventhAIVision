'use strict'
const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('keygen', {
  status: () => ipcRenderer.invoke('keystore:status'),
  create: (password) => ipcRenderer.invoke('keystore:create', password),
  unlock: (password) => ipcRenderer.invoke('keystore:unlock', password),
  lock: () => ipcRenderer.invoke('keystore:lock'),
  generate: (form) => ipcRenderer.invoke('licence:generate', form),
  verify: (key) => ipcRenderer.invoke('licence:verify', key),
  issued: () => ipcRenderer.invoke('licence:issued'),
  copy: (text) => ipcRenderer.invoke('clipboard:write', text),
  openFolder: () => ipcRenderer.invoke('folder:open'),
  backup: () => ipcRenderer.invoke('keystore:backup'),
})
