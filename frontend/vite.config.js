import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';
import { fileURLToPath } from 'node:url';

// Dev:   npm run dev      -> http://localhost:5173 with hot module reload, /api proxied to the Noah server
// Build: npm run build    -> ../noah/web/dist, which the Noah server (and the desktop app, Docker image) serve
const api = process.env.NOAH_API || 'http://127.0.0.1:8765';

export default defineConfig({
  plugins: [vue()],
  // monaco-vs: Monaco's ESM sources. Its package exports add '.js' to every path, so its CSS files cannot be imported through them
  resolve: { alias: { 'monaco-vs': fileURLToPath(new URL('./node_modules/monaco-editor/esm/vs', import.meta.url)) } },
  // dev only: pre-bundling Monaco breaks its '?worker' imports (the worker module loses its default export)
  optimizeDeps: { exclude: ['monaco-editor', 'monaco-vs'] },
  // the code editor (Monaco, ~3 MB) is its own lazily loaded chunk, fetched only when an expert's Files tab opens
  build: { outDir: '../noah/web/dist', emptyOutDir: true, chunkSizeWarningLimit: 4000 },
  // VITE_POLL=1: poll for file changes (needed when the source is a bind mount under Docker Desktop on Windows/macOS)
  server: { port: 5173, proxy: { '/api': { target: api, changeOrigin: false } }, watch: process.env.VITE_POLL ? { usePolling: true } : undefined },
});
