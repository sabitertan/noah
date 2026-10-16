import { createApp } from 'vue';
import App from './App.vue';
import './assets/app.css';
import { init } from './store.js';

// Browser-only build: answer /api requests with Python in a worker instead of a server (left out of the normal build)
if (import.meta.env.MODE === 'wasm') (await import('./wasm/bridge.js')).install();
init();
createApp(App).mount('#app');
