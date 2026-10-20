import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';
import { fileURLToPath } from 'node:url';
import noahWasm from './wasm.plugin.js';

// Dev:   npm run dev        -> http://localhost:5173 with hot module reload, /api proxied to the Noah server
// Build: npm run build      -> ../noah/web/dist, which the Noah server (and the desktop app, Docker image) serve
// Browser-only target ("wasm", no server: Python runs in the page, see src/wasm/):
//        npm run dev:wasm   -> http://localhost:5174
//        npm run build:wasm -> ../site, a static site (GitHub Pages: .github/workflows/pages.yml)
const api = process.env.NOAH_API || 'http://127.0.0.1:8765';

export default defineConfig(({ mode }) => {
  const wasm = mode === 'wasm';
  return {
    plugins: [vue(), ...(wasm ? [noahWasm()] : [])],
    base: wasm ? './' : '/',   // a GitHub Pages site lives under /<repo>/
    // monaco-vs: Monaco's ESM sources. Its package exports add '.js' to every path, so its CSS files cannot be imported through them
    resolve: { alias: { 'monaco-vs': fileURLToPath(new URL('./node_modules/monaco-editor/esm/vs', import.meta.url)) } },
    // dev only: pre-bundling Monaco breaks its '?worker' imports (the worker module loses its default export)
    optimizeDeps: { exclude: ['monaco-editor', 'monaco-vs'] },
    worker: { format: 'es' },
    // big lazily loaded chunks: the code editor (Monaco, ~3 MB), fetched only when an expert's Files tab opens, and WebLLM
    // (~6 MB, page and worker), fetched only when the Models page's WebLLM tab opens
    build: { outDir: wasm ? '../site' : '../noah/web/dist', emptyOutDir: true, chunkSizeWarningLimit: 6500 },
    // VITE_POLL=1: poll for file changes (needed when the source is a bind mount under Docker Desktop on Windows/macOS)
    server: wasm ? { port: 5174 }
      : { port: 5173, proxy: { '/api': { target: api, changeOrigin: false } }, watch: process.env.VITE_POLL ? { usePolling: true } : undefined },
  };
});
