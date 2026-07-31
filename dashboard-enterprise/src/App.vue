<template>
  <div class="app-layout" :class="{ 'rail': ui.rail && !isMobile, 'sidebar-open': sidebarOpen && isMobile }">
    <!-- Mobile drawer backdrop -->
    <div v-if="sidebarOpen && isMobile" class="sidebar-backdrop" @click="closeSidebar" aria-hidden="true"></div>

    <nav class="sidebar">
      <div class="logo">
        <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2L2 7l10 5 10-5-10-5z"/><path d="M2 17l10 5 10-5"/><path d="M2 12l10 5 10-5"/></svg>
        <span>MAOP</span>
        <button class="rail-btn" @click="toggleRail" :title="ui.rail ? t('action.expandSidebar') : t('action.collapseSidebar')" aria-label="Toggle sidebar">
          <AppIcon name="panelleft" :size="16" />
        </button>
      </div>

      <div class="nav-scroll">
        <template v-for="(item, i) in nav" :key="i">
          <div v-if="item.section" class="nav-section">{{ t(item.section) }}</div>
          <router-link v-else :to="item.to" class="nav-link" :title="t(item.label)">
            <AppIcon class="nav-icon" :name="item.icon" :size="18" />
            <span class="nav-label">{{ t(item.label) }}</span>
          </router-link>
        </template>
      </div>

      <div class="nav-footer">
        <!-- Sidebar footer is minimal now; user info moved to PageHeader -->
        <div class="nf-group nf-status">
          <span class="live-indicator" :class="{ connected: realtimeConnected }">
            <AppIcon class="live-dot" name="radio" :size="12" />
            {{ realtimeConnected ? t('status.live') : t('status.offline') }}
          </span>
          <span class="version">v{{ version }}</span>
        </div>
      </div>
    </nav>

    <main class="content">
      <button
        class="hamburger-btn"
        @click="toggleSidebar"
        :aria-expanded="sidebarOpen"
        aria-label="Toggle navigation menu"
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
        <h3>{{ loginTitle }}</h3>
        <p v-if="loginError" class="login-error">{{ loginError }}</p>
        <form @submit.prevent="doLogin">
          <input v-model="loginUsername" type="text" :placeholder="t('auth.username')" autocomplete="username" :disabled="loginLoading" />
          <input v-model="loginPassword" type="password" :placeholder="t('auth.password')" autocomplete="current-password" :disabled="loginLoading" />
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

// ── Navigation ────────────────────────────────────────────────────────────
// `nav` is imported from ./nav.js (single source of truth shared with PageHeader).

// ── Auth flows (unchanged behavior) ──────────────────────────────────────
function onUnauthorized() {
  authExpired.value = true;
  loginTitle.value = t('auth.sessionExpired');
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
    try { localStorage.setItem('maop_auth_enabled', String(authEnabled.value)); } catch {}
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
  await edition.fetchEdition();
  await checkAuthEnabled();
  computeAdmin();
  try {
    const d = await api.get('/api/health');
    if (d) {
      version.value = d.version || (typeof __APP_VERSION__ !== 'undefined' ? __APP_VERSION__ : 'unknown');
      try { localStorage.setItem('maop_version', version.value); } catch {}
    }
  } catch {}
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

.app-layout { display: flex; min-height: 100vh; position: relative; z-index: 1; }

/* ── User profile card (sidebar footer) — MOVED to PageHeader ───── */

/* ── Sidebar ─────────────────────────────────────────────────────── */
.sidebar {
  width: var(--sidebar-w);
  background: var(--surface);
  padding: 14px 0;
  display: flex;
  flex-direction: column;
  border-right: 1px solid var(--border);
  position: sticky; top: 0; height: 100vh;
  flex-shrink: 0;
  z-index: var(--z-sidebar);
  transition: width var(--motion) var(--ease);
}
.logo {
  display: flex; align-items: center; gap: 8px;
  padding: 4px 18px 14px; font-size: 17px; font-weight: 700; color: var(--brand-strong);
}
.logo .rail-btn {
  margin-left: auto; background: none; border: none; color: var(--text-faint);
  display: grid; place-items: center; padding: 4px; border-radius: var(--r-sm);
  transition: color var(--motion) var(--ease), background var(--motion) var(--ease);
}
.logo .rail-btn:hover { color: var(--text); background: var(--surface-2); }

.nav-scroll { flex: 1; overflow-y: auto; padding-bottom: 8px; }
.nav-section {
  font-size: 10px; font-weight: 700; color: var(--text-faint);
  text-transform: uppercase; letter-spacing: .08em;
  padding: 14px 18px 4px; border-top: 1px solid var(--border);
}
.nav-section:first-of-type { border-top: none; }
.nav-link {
  display: flex; align-items: center; gap: 10px;
  padding: 9px 18px; color: var(--text-muted); text-decoration: none;
  font-size: 13px; font-weight: 500; border-radius: 0;
  border-left: 3px solid transparent;
  transition: color var(--motion) var(--ease), background var(--motion) var(--ease), border-color var(--motion) var(--ease);
}
.nav-link:hover { color: var(--text); background: var(--surface-2); }
.nav-link.router-link-active {
  color: var(--brand-strong); background: var(--brand-soft);
  border-left-color: var(--brand);
}
.nav-icon { flex-shrink: 0; color: inherit; }

.nav-footer {
  margin-top: auto; padding: 12px 16px; border-top: 1px solid var(--border);
  display: flex; flex-direction: column; gap: 10px;
}
.nf-group { display: flex; flex-direction: column; gap: 6px; }
.nf-group.nf-status { flex-direction: row; align-items: center; justify-content: space-between; }
.nf-label { font-size: 10px; font-weight: 700; color: var(--text-faint); text-transform: uppercase; letter-spacing: .06em; }
.live-indicator { display: inline-flex; align-items: center; gap: 6px; font-size: 11px; color: var(--text-faint); }
.live-indicator .live-dot { color: var(--text-faint); }
.live-indicator.connected { color: var(--success); }
.live-indicator.connected .live-dot { color: var(--success); }
.version { font-size: 11px; color: var(--text-faint); font-family: var(--font-mono); }

/* ── Rail (collapsed) state ─────────────────────────────────────── */
.app-layout.rail .sidebar { width: var(--rail-w); }
.app-layout.rail .logo span,
.app-layout.rail .nav-label,
.app-layout.rail .nav-section,
.app-layout.rail .nf-status .version,
.app-layout.rail .nf-label { display: none; }
.app-layout.rail .logo { justify-content: center; padding: 4px 0 14px; }
.app-layout.rail .nav-link { justify-content: center; padding: 10px 0; gap: 0; }
.app-layout.rail .nav-footer { align-items: center; padding: 12px 8px; }

/* ── Content (scroll area) ─────────────────────────────────────── */
.content { flex: 1; overflow-y: auto; min-width: 0; position: relative; z-index: 1; display: flex; flex-direction: column; }
/* Centered, max-width shell so content aligns consistently on wide screens;
   the footer is pinned to the bottom via margin-top:auto inside this shell. */
.content-shell { flex: 1; min-height: 0; display: flex; flex-direction: column; width: 100%; max-width: var(--maxw); margin: 0 auto; padding: var(--content-pad); }

/* ── Auth overlay ───────────────────────────────────────────────── */
.auth-overlay { position: fixed; inset: 0; background: rgba(0,0,0,.55); display: flex; align-items: center; justify-content: center; z-index: var(--z-modal); }
.auth-card { background: var(--surface); border: 1px solid var(--border); border-radius: var(--r-lg); padding: 24px; max-width: 360px; width: calc(100% - 32px); text-align: center; box-shadow: var(--shadow-lg); }
.auth-card h3 { margin-bottom: 12px; color: var(--text); }
.auth-card p { color: var(--text-muted); font-size: 13px; margin-bottom: 16px; }
.auth-card button { background: var(--brand); color: #fff; border: none; border-radius: var(--r-md); padding: 8px 16px; font-size: 13px; width: 100%; margin-top: 8px; }
.auth-card button:disabled { opacity: .5; cursor: not-allowed; }
.auth-card form { display: flex; flex-direction: column; gap: 8px; }
.auth-card input { background: var(--bg); border: 1px solid var(--border); border-radius: var(--r-md); padding: 8px 12px; color: var(--text); font-size: 13px; }
.auth-card input:focus { outline: none; border-color: var(--brand); }
.login-error { color: var(--fail); font-size: 12px; margin-bottom: 8px; }

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
