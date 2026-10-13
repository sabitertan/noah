import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';

// Dev:   npm run dev      -> http://localhost:5173 with hot module reload, /api proxied to the Noah server
// Build: npm run build    -> ../noah/web/dist, which the Noah server (and the desktop app, Docker image) serve
const api = process.env.NOAH_API || 'http://127.0.0.1:8765';

export default defineConfig({
  plugins: [vue()],
  build: { outDir: '../noah/web/dist', emptyOutDir: true },
  // VITE_POLL=1: poll for file changes (needed when the source is a bind mount under Docker Desktop on Windows/macOS)
  server: { port: 5173, proxy: { '/api': { target: api, changeOrigin: false } }, watch: process.env.VITE_POLL ? { usePolling: true } : undefined },
});
