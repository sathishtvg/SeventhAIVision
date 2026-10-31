import path from 'path'
import { execSync } from 'child_process'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

/** Which build this is, shown at the foot of the sidebar (src/lib/appVersion.ts): the commit and the day it was built.
 *  APP_BUILD_COMMIT / APP_BUILD_DATE win when set (a build with no git to ask); without either the commit is left out. */
function buildStamp(): { commit: string; date: string } {
  let commit = process.env.APP_BUILD_COMMIT ?? ''
  if (!commit) {
    try {
      commit = execSync('git rev-parse --short=7 HEAD', { stdio: ['ignore', 'pipe', 'ignore'] }).toString().trim()
    } catch {
      commit = ''
    }
  }
  return { commit: commit.slice(0, 7), date: process.env.APP_BUILD_DATE ?? new Date().toISOString().slice(0, 10) }
}

export default defineConfig({
  plugins: [react()],
  define: {
    __APP_BUILD__: JSON.stringify(buildStamp()),
  },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://localhost:8000', changeOrigin: true },
      '/ws': { target: 'ws://localhost:8000', ws: true, changeOrigin: true },
    },
  },
  // `vite preview` serves the real production bundle. Without the same proxy
  // it can't reach the API, so the only way to exercise a built frontend was
  // to rebuild the Docker image — too slow to be a useful check, which meant
  // production-only rendering bugs went unlooked-for.
  preview: {
    port: 5175,
    proxy: {
      '/api': { target: 'http://localhost:8000', changeOrigin: true },
      '/ws': { target: 'ws://localhost:8000', ws: true, changeOrigin: true },
    },
  },
})
