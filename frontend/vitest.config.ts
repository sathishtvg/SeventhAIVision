import path from 'path'
import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { '@': path.resolve(__dirname, './src') },
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    css: false,
    // Vitest's 5s default is not enough here. Every MUI file is inlined and
    // transformed (see below), which costs well over two minutes across the
    // suite; under that load a test doing a couple of async waits can exceed
    // 5s purely from contention. The Login tests failed only in a full run and
    // passed in isolation — a timing artefact, not a defect, and one that
    // turns a green suite into noise nobody reads.
    testTimeout: 20_000,
    hookTimeout: 20_000,
    server: {
      deps: {
        // @mui/material ships .mjs files that do a bare-directory sub-path
        // import (react-transition-group/TransitionGroupContext) which Node.js
        // ESM rejects. Inlining both forces all MUI files + the transition-group
        // through Vite's transform pipeline whose CJS-aware resolver handles the
        // sub-directory package.json `main` field correctly.
        inline: [/@mui\//, 'react-transition-group'],
      },
    },
  },
})
