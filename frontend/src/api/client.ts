import axios from 'axios'

// In Electron the backend URL is injected synchronously via preload before this module loads.
// In the browser (Docker/nginx) fall back to the build-time env var.
const baseURL =
  (window as any).electronAPI?.serverUrl ||
  import.meta.env.VITE_API_BASE_URL ||
  ''

export const apiClient = axios.create({
  baseURL,
  headers: { 'Content-Type': 'application/json' },
})

// Interceptors for auth token injection and 401 refresh are wired in
// src/store/auth.ts after the store is created, to avoid circular imports.
