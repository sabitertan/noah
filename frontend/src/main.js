import { createApp } from 'vue';
import App from './App.vue';
import './assets/app.css';
import { init } from './store.js';

init();
createApp(App).mount('#app');
