import { createApp } from 'vue';
import App from './App.vue';
import './style.css';
import { initTheme } from './telemetry/theme.js';

initTheme();
createApp(App).mount('#app');
