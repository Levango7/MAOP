<template>
  <!-- 满高左侧栏布局: 侧栏纵贯页面从上至下, 折叠按钮固定在侧栏左上角;
       折叠后侧栏变窄(--rail-w)但仍满高; 顶栏位于右侧内容区顶部 -->
  <div class="app-layout" :class="{ 'rail': ui.rail && !isMobile, 'sidebar-open': sidebarOpen && isMobile }">
    <!-- Mobile drawer backdrop -->
    <div v-if="sidebarOpen && isMobile" class="sidebar-backdrop" aria-hidden="true" @click="closeSidebar"></div>

    <nav class="sidebar" @click="onSidebarClick">
      <div class="sidebar-head">
        <button
          class="sidebar-toggle"
          :title="ui.rail ? t('action.expandSidebar') : t('action.collapseSidebar')"
          :aria-label="ui.rail ? 'Expand sidebar' : 'Collapse sidebar'"
          @click="toggleRail"
        >
          <AppIcon :name="ui.rail ? 'panelright' : 'panelleft'" :size="16" />
        </button>
      </div>
      <div class="nav-scroll">
        <template v-for="(item, i) in nav" :key="i">
          <div v-if="item.section" class="nav-section">{{ t(item.section) }}</div>
          <router-link v-else :to="item.to" class="nav-link" :title="t(item.label)" :class="{ 'router-link-active': isActive(item) }">
            <AppIcon class="nav-icon" :name="item.icon" :size="18" />
            <span class="nav-label">{{ t(item.label) }}</span>
          </router-link>
        </template>
      </div>
    </nav>

    <main class="content">
      <TopBar />
      <button
        class="hamburger-btn"
        :aria-expanded="sidebarOpen"
        aria-label="Toggle navigation menu"
        @click="toggleSidebar"
      >
        <span></span><span></span><span></span>
      </button>
      <div class="content-shell">
        <div v-if="renderError" class="fatal-error" role="alert">
          <AppIcon name="alert-triangle" :size="28" />
          <h2>{{ t('error.somethingWrong') }}</h2>
          <p>{{ renderError }}</p>
          <button class="btn-primary" @click="renderError = null; $router.go(0)">{{ t('error.reload') }}</button>
        </div>
        <router-view v-else v-slot="{ Component }">
          <component :is="Component" class="view-enter" />
        </router-view>
      </div>
      <AppFooter :version="version" />
    </main>

    <div v-if="authExpired" class="auth-overlay">
      <div class="auth-card">
        <div class="auth-card__icon"><AppIcon name="bot" :size="26" /></div>
        <h3>{{ loginTitle }}</h3>
        <p v-if="loginError" class="login-error">{{ loginError }}</p>
        <form @submit.prevent="doLogin">
          <div>
            <label>{{ t('auth.username') }}</label>
            <input v-model="loginUsername" type="text" :placeholder="t('auth.username')" autocomplete="username" :disabled="loginLoading" />
          </div>
          <div>
            <label>{{ t('auth.password') }}</label>
            <input v-model="loginPassword" type="password" :placeholder="t('auth.password')" autocomplete="current-password" :disabled="loginLoading" />
          </div>
          <button type="submit" :disabled="loginLoading || !loginUsername || !loginPassword">
            {{ loginLoading ? t('auth.signingIn') : t('auth.signIn') }}
          </button>
        </form>
      </div>
    </div>

    <Toast />
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, onErrorCaptured } from 'vue';
import { useEditionStore } from './stores/edition.js';
import { useApiStore, withAuth } from './stores/api.js';
import { useRealtimeStore } from './stores/realtime.js';
import { useUiStore } from './stores/ui.js';
import AppIcon from './components/AppIcon.vue';
import TopBar from './components/TopBar.vue';
import Toast from './components/Toast.vue';
import AppFooter from './components/AppFooter.vue';
import { useI18n } from './i18n/index.js';
import { nav } from './nav.js';

const edition = useEditionStore();
const api = useApiStore();
const realtime = useRealtimeStore();
const ui = useUiStore();
const realtimeConnected = computed(() => realtime.connected);
const { t } = useI18n();

// ── Global error boundary ──────────────────────────────────────────
// Catch any render-time or lifecycle error in a view so a single
// component failure no longer blanks the entire app (white screen).
const renderError = ref(null);
onErrorCaptured((err, instance, info) => {
  console.error('[view-error]', info, err);
  renderError.value = (err && err.message) ? err.message : String(err);
  // Return false so the error is not propagated further up the tree.
  return false; // suppress render error propagation — error card is shown
});

const version = ref(typeof __APP_VERSION__ !== 'undefined' ? __APP_VERSION__ : 'unknown');
const authExpired = ref(false);
const loginUsername = ref('');
const loginPassword = ref('');
const loginError = ref('');
const loginLoading = ref(false);
const loginTitle = ref(t('auth.signIn'));
const authEnabled = ref(false);

// ── User profile (from localStorage after auth) ──────────────────────
function getStoredUser() { try { return localStorage.getItem('maop_user') || ''; } catch { return ''; } }
function getStoredRoles() {
  try { const r = localStorage.getItem('maop_roles'); return r ? JSON.parse(r) : []; } catch { return []; }
}
const userName = ref(getStoredUser());
const userRoles = ref(getStoredRoles());
const userInitial = computed(() => {
  const n = userName.value;
  if (!n) return '?';
  // For Chinese names, take the last character (surname); for English, first letter
  if (/[\u4e00-\u9fff]/.test(n)) return n.charAt(n.length - 1);
  return n.charAt(0).toUpperCase();
});

// Admin detection: roles in localStorage OR auth-disabled runs (superuser).
const isAdmin = ref(false);
function computeAdmin() {
  try {
    const rolesStr = localStorage.getItem('maop_roles');
    if (rolesStr) {
      const roles = JSON.parse(rolesStr);
      if (Array.isArray(roles) && roles.some((r) => r === 'admin' || r === 'superadmin')) { isAdmin.value = true; return; }
    }
  } catch (e) { /* ignore */ }
  if (authEnabled.value === false) { isAdmin.value = true; return; }
  isAdmin.value = localStorage.getItem('maop_user') === 'admin';
}
async function onEditionChange(target) {
  if (target === edition.edition) return;
  if (!isAdmin.value) { window.alert(t('nav.editionLocked')); return; }
  const msg = target === 'personal' ? t('nav.editionToPersonal') : t('nav.editionToEnterprise');
  if (!window.confirm(msg)) return;
  try {
    await edition.switchEdition(target);
  } catch (e) { /* error surfaced via store.switchError */ }
}

// ── Theme / density / rail now live in the shared ui store (stores/ui.js) ─
const isLight = computed(() => ui.theme === 'light');
function toggleTheme() { ui.toggleTheme(); }
function toggleRail() { ui.toggleRail(); }

// ── Mobile drawer ─────────────────────────────────────────────────────────
const sidebarOpen = ref(false);
const isMobile = ref(false);
const MOBILE_BREAKPOINT = 900;
function checkMobile() {
  if (typeof window === 'undefined') return;
  isMobile.value = window.innerWidth < MOBILE_BREAKPOINT;
  if (!isMobile.value) sidebarOpen.value = false;
}
function toggleSidebar() { sidebarOpen.value = !sidebarOpen.value; }
function closeSidebar() { sidebarOpen.value = false; }
function onSidebarClick(e) {
  if (isMobile.value && sidebarOpen.value && e.target.closest('.nav-link')) closeSidebar();
}

// Nav active state: matchPaths (e.g. /evolution-history highlights /evolve)
import { useRoute } from 'vue-router';
const route = useRoute();
function isActive(item) {
  if (!item.to) return false;
  if (item.to === '/') return route.path === '/';
  if (route.path === item.to || route.path.startsWith(item.to + '/')) return true;
  if (Array.isArray(item.matchPaths) && item.matchPaths.some((p) => route.path === p || route.path.startsWith(p + '/'))) return true;
  return false;
}

// ── Navigation ────────────────────────────────────────────────────────────
// `nav` is imported from ./nav.js (single source of truth shared with PageHeader).

// ── Auth flows ───────────────────────────────────────────────────────────
// Proactive: when auth is enabled and no token exists, show the login
// overlay immediately with a "Sign In" title — don't wait for 401 errors
// to trickle in and flash empty pages at the user.
function showLoginIfRequired() {
  if (authEnabled.value && !api.authToken()) {
    authExpired.value = true;
    loginTitle.value = t('auth.signIn');
    loginError.value = '';
    return true;
  }
  return false;
}
// Reactive: a 401 was intercepted by the api store. Only say "Session
// Expired" when the user actually had a token before; otherwise it's a
// first-visit sign-in.
function onUnauthorized() {
  const hadToken = !!api.authToken();
  authExpired.value = true;
  loginTitle.value = hadToken ? t('auth.sessionExpired') : t('auth.signIn');
  loginError.value = '';
}
async function doLogin() {
  loginLoading.value = true;
  loginError.value = '';
  try {
    const r = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: loginUsername.value, password: loginPassword.value }),
    });
    const data = await r.json();
    if (r.ok && data.status === 'ok' && data.token) {
      api.setAuthToken(data.token, data.username);
      if (data.username) localStorage.setItem('maop_user', data.username);
      if (data.roles) localStorage.setItem('maop_roles', JSON.stringify(data.roles));
      // Sync reactive refs
      userName.value = data.username || getStoredUser();
      userRoles.value = data.roles || getStoredRoles();
      authExpired.value = false;
      loginPassword.value = '';
      if (typeof window !== 'undefined' && window.location) window.location.reload();
    } else {
      loginError.value = data.error || 'Login failed';
    }
  } catch (e) {
    loginError.value = e.message || t('auth.networkError');
  } finally {
    loginLoading.value = false;
  }
}
async function checkAuthEnabled() {
  try {
    const d = await api.get('/api/auth/status');
    authEnabled.value = d && d.auth_enabled === true;
    try { localStorage.setItem('maop_auth_enabled', String(authEnabled.value)); } catch { /* ignore */ }
    // If backend says auth is enabled but our token is invalid (has_token:false),
    // clear the stale local token so showLoginIfRequired() can trigger the
    // login overlay immediately — instead of waiting for 401 errors from
    // every subsequent authenticated endpoint.
    // Direct localStorage removal (not api.clearAuthToken) to avoid triggering
    // /api/auth/logout which would itself 401 and cause a retry loop.
    if (authEnabled.value && d && d.has_token === false && api.authToken()) {
      try { localStorage.removeItem('maop_token'); localStorage.removeItem('maop_user'); } catch { /* ignore */ }
    }
  } catch { authEnabled.value = false; }
}
async function doLogout() {
  try { await api.clearAuthToken(); } catch { /* ignore */ }
  if (typeof window !== 'undefined' && window.location) window.location.reload();
}

onMounted(async () => {
  if (typeof window !== 'undefined') {
    window.addEventListener('maop:unauthorized', onUnauthorized);
    window.addEventListener('resize', checkMobile);
  }
  // Sync user profile from localStorage (may be pre-populated by prior session)
  userName.value = getStoredUser();
  userRoles.value = getStoredRoles();
  checkMobile();
  // Check auth status FIRST — if login is required, show the overlay
  // immediately and skip the data-fetching calls that would just 401.
  await checkAuthEnabled();
  if (showLoginIfRequired()) return;
  await edition.fetchEdition();
  computeAdmin();
  try {
    const d = await api.get('/api/health');
    if (d) {
      version.value = d.version || (typeof __APP_VERSION__ !== 'undefined' ? __APP_VERSION__ : 'unknown');
      try { localStorage.setItem('maop_version', version.value); } catch { /* ignore */ }
    }
  } catch { /* ignore */ }
  realtime.connect();
});

onUnmounted(() => {
  if (typeof window !== 'undefined') {
    window.removeEventListener('maop:unauthorized', onUnauthorized);
    window.removeEventListener('resize', checkMobile);
  }
  realtime.disconnect();
});
</script>

<style>
:root { color-scheme: dark; }
* { margin: 0; padding: 0; box-sizing: border-box; }

/* 满高左侧栏布局 (2026-08-12 按用户指定重构):
 * 侧栏纵贯页面左侧——上至视口最上、下至最下(sticky + 100vh)。
 * 收缩/扩展按钮固定在侧栏左上角 (.sidebar-head), 与侧栏宽度无关,
 * rail 模式下侧栏收窄为 64px 但仍满高, 按钮居中保持可点。
 * 顶栏 (TopBar) 回到右侧内容区顶部。
 */
.app-layout {
  display: flex;
  flex-direction: row;
  min-height: 100vh;
  position: relative;
  z-index: 1;
}

/* ── Sidebar ─────────────────────────────────────────────────────── */
.sidebar {
  width: var(--sidebar-w);
  background: var(--surface);
  padding: 0;
  display: flex;
  flex-direction: column;
  border-right: 1px solid var(--border);
  flex-shrink: 0;
  z-index: var(--z-sidebar);
  transition: width var(--motion) var(--ease);
  position: sticky;
  top: 0;
  height: 100vh;
  align-self: flex-start;
}

/* 侧栏头部: 仅放折叠按钮。展开时按钮靠左, rail 时居中。 */
.sidebar-head {
  display: flex;
  align-items: center;
  height: var(--topbar-h);
  padding: 0 var(--sp-3);
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
}
.sidebar-toggle {
  display: grid;
  place-items: center;
  width: 34px;
  height: 34px;
  flex-shrink: 0;
  background: transparent;
  border: 1px solid var(--border);
  border-radius: var(--r-md);
  color: var(--text-muted);
  cursor: pointer;
  transition: color var(--motion) var(--ease), background var(--motion) var(--ease), border-color var(--motion) var(--ease);
}
.sidebar-toggle:hover {
  color: var(--text);
  background: var(--surface-2);
  border-color: var(--border-strong);
}
.sidebar-toggle:active { transform: scale(.96); }

.nav-scroll { flex: 1; overflow-y: auto; padding: var(--sp-2) 0; }
.nav-section {
  font-size: 10px; font-weight: 700; color: var(--text-faint);
  text-transform: uppercase; letter-spacing: .08em;
  padding: 16px 18px 6px;
  position: relative;
}
.nav-section::after {
  content: "";
  position: absolute;
  left: 18px; right: 18px;
  bottom: 0;
  height: 1px;
  background: var(--border-subtle);
  opacity: 0;
}
.nav-section:not(:first-of-type) {
  border-top: 1px solid var(--border-subtle, var(--border));
  margin-top: 6px;
}
.nav-link {
  display: flex; align-items: center; gap: 12px;
  padding: 10px 18px; color: var(--text-muted); text-decoration: none;
  font-size: 13px; font-weight: 500; border-radius: 0;
  border-left: 3px solid transparent;
  transition: color var(--motion) var(--ease), background var(--motion) var(--ease), border-color var(--motion) var(--ease), padding-left var(--motion) var(--ease);
  position: relative;
  margin: 1px 0;
}
.nav-link:hover {
  color: var(--text); background: var(--surface-2);
  padding-left: 20px;
}
.nav-link.router-link-active {
  color: var(--brand-strong); background: var(--brand-soft);
  border-left-color: var(--brand);
  font-weight: 600;
  padding-left: 18px;
}
.nav-link.router-link-active::before {
  content: '';
  position: absolute;
  left: 0; top: 0; bottom: 0;
  width: 2px;
  background: var(--brand);
  /* JB 精修: 去掉 box-shadow 发光 — 纯粹的 1px 色条就是全部装饰 */
}
.nav-link.router-link-active::after {
  content: '';
  position: absolute;
  right: 12px;
  top: 50%;
  transform: translateY(-50%);
  width: 4px; height: 4px;
  border-radius: 50%;
  background: var(--brand);
  /* 同步去发光 */
}
.nav-icon { flex-shrink: 0; color: inherit; transition: transform var(--motion) var(--ease); }
.nav-link:hover .nav-icon { transform: scale(1.08); }
.nav-link.router-link-active .nav-icon { color: var(--brand-strong); }

.nav-footer {
  margin-top: auto; padding: 12px 16px; border-top: 1px solid var(--border);
  display: flex; flex-direction: column; gap: 10px;
}
.nf-group { display: flex; flex-direction: column; gap: 6px; }
.nf-group.nf-status { flex-direction: row; align-items: center; justify-content: space-between; }
.nf-label { font-size: 10px; font-weight: 700; color: var(--text-faint); text-transform: uppercase; letter-spacing: .06em; }

/* ── Rail (collapsed) state ─────────────────────────────────────── */
.app-layout.rail .sidebar { width: var(--rail-w); }
.app-layout.rail .nav-label,
.app-layout.rail .nav-section { display: none; }
.app-layout.rail .nav-link { justify-content: center; padding: 10px 0; gap: 0; }
.app-layout.rail .sidebar-head { justify-content: center; padding: 0; }

/* ── Content (scroll area) ─────────────────────────────────────── */
.content { flex: 1; overflow-y: auto; min-width: 0; position: relative; z-index: 1; display: flex; flex-direction: column; }
/* Centered, max-width shell so content aligns consistently on wide screens;
   the footer is pinned to the bottom via margin-top:auto inside this shell. */
.content-shell { flex: 1; min-height: 0; display: flex; flex-direction: column; width: 100%; max-width: var(--maxw); margin: 0 auto; padding: var(--sp-2) var(--content-pad) var(--content-pad); }

/* ── Auth overlay ───────────────────────────────────────────────── */
.auth-overlay {
  position: fixed; inset: 0; z-index: var(--z-modal);
  display: flex; align-items: center; justify-content: center;
  background: var(--overlay-scrim, rgba(15, 23, 42, .65));
  backdrop-filter: blur(8px);
  animation: maop-view-in var(--motion) var(--ease) both;
}
.auth-card {
  position: relative;
  background: var(--card-sheen), var(--surface);
  border: 1px solid var(--border-strong, var(--border));
  border-radius: var(--r-xl);
  padding: var(--sp-6);
  max-width: 420px; width: calc(100% - 32px);
  text-align: center;
  box-shadow: var(--shadow-lg);
  animation: maop-view-in var(--motion) var(--ease-out) both;
}
.auth-card::before {
  content: ''; position: absolute; top: 0; left: 0; right: 0; height: 1px;
  background: linear-gradient(90deg, transparent, var(--brand-faint) 30%, var(--brand) 50%, var(--brand-faint) 70%, transparent);
  border-radius: var(--r-xl) var(--r-xl) 0 0; pointer-events: none;
}
.auth-card__icon {
  display: grid; place-items: center;
  width: 52px; height: 52px; margin: 0 auto var(--sp-4);
  border-radius: var(--r-full);
  background: linear-gradient(135deg, var(--brand-soft), var(--brand-faint));
  border: 1px solid var(--brand-faint);
  color: var(--brand-strong);
}
.auth-card h3 { margin-bottom: var(--sp-2); color: var(--text); font-size: var(--fs-lg); font-weight: 700; letter-spacing: -0.01em; }
.auth-card p { color: var(--text-muted); font-size: var(--fs-sm); margin-bottom: var(--sp-4); line-height: 1.5; }
.auth-card button[type="submit"] {
  background: var(--brand); color: var(--brand-contrast);
  border: 1px solid var(--brand); border-radius: var(--r-md);
  padding: var(--sp-3) var(--sp-4); font-size: var(--fs-sm); font-weight: 600;
  width: 100%; margin-top: var(--sp-3); cursor: pointer;
  transition: background var(--motion) var(--ease), box-shadow var(--motion) var(--ease), transform var(--motion) var(--ease);
}
.auth-card button[type="submit"]:hover:not(:disabled) {
  background: var(--brand-strong); border-color: var(--brand-strong);
  box-shadow: 0 4px 14px color-mix(in srgb, var(--brand) 35%, transparent);
  transform: translateY(-1px);
}
.auth-card button[type="submit"]:disabled { opacity: .5; cursor: not-allowed; }
.auth-card form { display: flex; flex-direction: column; gap: var(--sp-3); text-align: left; }
.auth-card form label { display: block; font-size: var(--fs-xs); font-weight: 600; color: var(--text-muted); text-transform: uppercase; letter-spacing: .04em; margin-bottom: 4px; }
.auth-card input {
  width: 100%; box-sizing: border-box;
  background: var(--bg); border: 1px solid var(--border); border-radius: var(--r-md);
  padding: var(--sp-3) var(--sp-4); color: var(--text); font-size: var(--fs-sm); font-family: inherit;
  transition: border-color var(--motion) var(--ease), box-shadow var(--motion) var(--ease);
}
.auth-card input:focus { outline: none; border-color: var(--brand); box-shadow: 0 0 0 3px var(--brand-soft); }
.auth-card input::placeholder { color: var(--text-faint); }
.login-error {
  color: var(--fail); font-size: var(--fs-xs); margin-bottom: var(--sp-2);
  padding: var(--sp-2) var(--sp-3); border-radius: var(--r-sm);
  background: var(--fail-soft); border: 1px solid color-mix(in srgb, var(--fail) 25%, transparent);
}

/* ── Responsive: sidebar becomes a slide-in drawer ──────────────── */
.hamburger-btn {
  display: none; flex-direction: column; justify-content: center; gap: 4px;
  width: 38px; height: 38px; background: var(--surface); border: 1px solid var(--border);
  border-radius: var(--r-md); padding: 0 8px; margin-bottom: 12px; position: sticky; top: 12px; z-index: var(--z-toast, 100);
  transition: background var(--motion) var(--ease);
}
.hamburger-btn:hover { background: var(--surface-2); }
.hamburger-btn span { display: block; width: 22px; height: 2px; background: var(--text); border-radius: 1px; transition: transform var(--motion) var(--ease), opacity var(--motion) var(--ease); }
.sidebar-open .hamburger-btn span:nth-child(1) { transform: translateY(6px) rotate(45deg); }
.sidebar-open .hamburger-btn span:nth-child(2) { opacity: 0; }
.sidebar-open .hamburger-btn span:nth-child(3) { transform: translateY(-6px) rotate(-45deg); }

.sidebar-backdrop { position: fixed; inset: 0; background: rgba(0,0,0,.5); z-index: var(--z-overlay, 98); animation: maop-view-in var(--motion) var(--ease); }

@media (max-width: 899px) {
  .sidebar {
    position: fixed; top: 0; left: 0; z-index: var(--z-drawer, 99);
    transform: translateX(-100%); transition: transform var(--motion-slow) var(--ease);
    box-shadow: var(--shadow-lg);
  }
  .sidebar-open .sidebar { transform: translateX(0); }
  .hamburger-btn { display: flex; }
  .content-shell { padding: 16px; }
}
</style>
