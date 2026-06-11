import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { crx } from '@crxjs/vite-plugin'
import manifest from './src/manifest.config'

// CRXJS handles MV3 manifest stitching, content-script HMR, and the
// dance around web-accessible resources for us. Without it we'd be
// hand-rolling a build step for each entry point and shipping a
// `dev-friendly` manifest plus a separate production manifest.
export default defineConfig({
  plugins: [react(), crx({ manifest })],
  server: {
    port: 5173,
    strictPort: true,
    hmr: { port: 5173 },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    sourcemap: true,
  },
})
