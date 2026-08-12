import { createApp } from 'vue';
import { createPinia } from 'pinia';
import App from './App.vue';
import router from './router/index.js';
import { vModalA11y } from './directives/modalA11y.js';

// Design tokens must load before any component styles.
import './styles/tokens.css';
import './styles/themes.css';
import './styles/layout.css';
import './styles/pages.css';

const app = createApp(App);
app.use(createPinia());
app.use(router);
app.directive('modal-a11y', vModalA11y);
app.mount('#app');
