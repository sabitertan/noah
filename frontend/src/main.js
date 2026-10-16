import { createApp } from 'vue';
import App from './App.vue';
import './assets/app.css';
import { init } from './store.js';
import { vCode } from './lib/codeblocks.js';

// Browser-only build: answer /api requests with Python in a worker instead of a server (left out of the normal build)
if (import.meta.env.MODE === 'wasm') (await import('./wasm/bridge.js')).install();
init();
createApp(App).directive('code', vCode).mount('#app');  // v-code: code blocks become read-only Monaco editors
