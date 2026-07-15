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
