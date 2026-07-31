import { createApp } from 'vue';
import { createPinia } from 'pinia';
import App from './App.vue';
import router from './router/index.js';

// Design tokens must load before any component styles.
import './styles/tokens.css';
import './styles/themes.css';
import './styles/layout.css';
import './styles/pages.css';

const app = createApp(App);
app.use(createPinia());
app.use(router);
app.mount('#app');
