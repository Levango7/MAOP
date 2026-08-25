<template>
  <!-- 层叠覆盖布局: 顶栏全宽 fixed (z-index:10), 侧栏全高 fixed (z-index:20) 覆盖顶栏左侧;
       折叠时侧栏变窄(--rail-w), 顶栏左侧品牌区自然露出; 内容区用 padding 腾位 -->
  <div class="app-layout" :class="{ 'rail': ui.rail && !isMobile, 'sidebar-open': sidebarOpen && isMobile }">
    <!-- 顶栏: 全宽 fixed (z-index:10), 侧栏 (z-index:20) 展开时覆盖其左侧品牌区 -->
    <TopBar />

    <!-- Mobile drawer backdrop -->
    <div v-if="sidebarOpen && isMobile" class="sidebar-backdrop" @click="closeSidebar"></div>

    <nav class="sidebar" :aria-label="t('a11y.mainNavigation')" @click="onSidebarClick">
      <div class="sidebar-head">
        <button
          class="sidebar-toggle"
          :title="ui.rail ? t('action.expandSidebar') : t('action.collapseSidebar')"
          :aria-label="ui.rail ? t('action.expandSidebar') : t('action.collapseSidebar')"
          :aria-expanded="isMobile ? sidebarOpen : !ui.rail"
          @click="toggleRail"
        >
          <AppIcon :name="ui.rail ? 'panelright' : 'panelleft'" :size="16" />
        </button>
      </div>
      <div class="nav-scroll">
        <template v-for="(item, i) in visibleNav" :key="i">
          <div v-if="item.section" class="nav-section">{{ t(item.section) }}</div>
          <router-link v-else :to="item.to" class="nav-link" :title="t(item.label)" :class="{ 'router-link-active': isActive(item) }" :aria-current="isActive(item) ? 'page' : undefined">
            <AppIcon class="nav-icon" :name="item.icon" :size="18" />
            <span class="nav-label">{{ t(item.label) }}</span>
          </router-link>
        </template>
      </div>
    </nav>

    <main class="content">

      <button
        v-if="isMobile"
        class="hamburger-btn"
        :aria-expanded="sidebarOpen"
        :aria-label="t('a11y.toggleNavigation')"
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

    <div v-if="authExpired" class="auth-overlay" role="dialog" aria-modal="true" :aria-label="t('a11y.loginDialog')" @keydown.esc="authExpired = false">
      <div class="auth-card">
        <div class="auth-card__icon" aria-hidden="true"><AppIcon name="bot" :size="26" /></div>
        <h3>{{ loginTitle }}</h3>
        <p v-if="loginError" class="login-error" role="alert">{{ loginError }}</p>
        <form @submit.prevent="doLogin">
          <div>
            <label for="login-username">{{ t('auth.username') }}</label>
            <input id="login-username" v-model="loginUsername" type="text" :placeholder="t('auth.username')" autocomplete="username" :disabled="loginLoading" />
          </div>
          <div>
            <label for="login-password">{{ t('auth.password') }}</label>
            <input id="login-password" v-model="loginPassword" type="password" :placeholder="t('auth.password')" autocomplete="current-password" :disabled="loginLoading" />
          </div>
          <button type="submit" :disabled="loginLoading || !loginUsername || !loginPassword">
            {{ loginLoading ? t('auth.signingIn') : t('auth.signIn') }}
          </button>
        </form>
      </div>
    </div>

    <Toast />
    <CoachMarks />
    <CommandPalette />
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, onErrorCaptured } from 'vue';
import { useEditionStore } from './stores/edition.js';
import { useApiStore } from './stores/api.js';
import { useRealtimeStore } from './stores/realtime.js';
import { useUiStore } from './stores/ui.js';
import AppIcon from './components/AppIcon.vue';
import TopBar from './components/TopBar.vue';
import Toast from './components/Toast.vue';
import AppFooter from './components/AppFooter.vue';
import CoachMarks from './components/CoachMarks.vue';
import CommandPalette from './components/CommandPalette.vue';
import { useI18n } from './i18n/index.js';
import { nav, filterNavByEdition } from './nav.js';

// 个人版隐藏企业版菜单项 (RFC-001 修正): 所见即所得, 不再"点了被弹走"
const visibleNav = computed(() => filterNavByEdition(nav, edition.edition));

const edition = useEditionStore();
const api = useApiStore();
const realtime = useRealtimeStore();
const ui = useUiStore();
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

// Admin detection: roles in localStorage OR auth-disabled runs (superuser).
const isAdmin = ref(false);
function computeAdmin() {
  try {
    const rolesStr = localStorage.getItem('maop_roles');
    if (rolesStr) {
      const roles = JSON.parse(rolesStr);
      if (Array.isArray(roles) && roles.some((r) => r === 'admin' || r === 'superadmin')) { isAdmin.value = true; return; }
    }
  } catch { /* ignore */ }
  if (authEnabled.value === false) { isAdmin.value = true; return; }
  isAdmin.value = localStorage.getItem('maop_user') === 'admin';
}

// ── Theme / density / rail now live in the shared ui store (stores/ui.js) ─
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
// Proactive: when auth is enabled and the user is not logged in, show the
// login overlay immediately with a "Sign In" title — don't wait for 401
// errors to trickle in and flash empty pages at the user.
// M6 fix: token lives in an httpOnly cookie (unreadable by JS), so login
// state is derived from isLoggedIn() (stored user), not authToken().
function showLoginIfRequired() {
  if (authEnabled.value && !api.isLoggedIn()) {
    authExpired.value = true;
    loginTitle.value = t('auth.signIn');
    loginError.value = '';
    return true;
  }
  return false;
}
// Reactive: a 401 was intercepted by the api store. Only say "Session
// Expired" when the user actually had a session before; otherwise it's a
// first-visit sign-in.
// M6 fix: derive from isLoggedIn() — authToken() is always empty under
// httpOnly-cookie auth.
function onUnauthorized() {
  const hadToken = api.isLoggedIn();
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
    // If backend says auth is enabled but our session cookie is invalid
    // (has_token:false), clear the stale local user info so
    // showLoginIfRequired() can trigger the login overlay immediately —
    // instead of waiting for 401 errors from every subsequent authenticated
    // endpoint.
    // Direct localStorage removal (not api.clearAuthToken) to avoid triggering
    // /api/auth/logout which would itself 401 and cause a retry loop.
    // M6 fix: local login state is the stored user (authToken() is always
    // empty under httpOnly-cookie auth).
    if (authEnabled.value && d && d.has_token === false && api.isLoggedIn()) {
      try { localStorage.removeItem('maop_user'); } catch { /* ignore */ }
      // Drop the userName/userRoles refs too — the session is gone.
      userName.value = '';
      userRoles.value = [];
    }
  } catch { authEnabled.value = false; }
}

onMounted(async () => {
  if (typeof window !== 'undefined') {
    window.addEventListener('maop:unauthorized', onUnauthorized);
    window.addEventListener('resize', checkMobile);
    // Global Esc: forward to the topmost open modal (a11y baseline — modals that
    // use v-modal-a11y listen for this and close).
    window.addEventListener('keydown', onGlobalEsc);
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

// Global Esc handler: finds the currently open modal (marked by v-modal-a11y)
// and dispatches a bubbling 'modal:escape' event on it. Views listen via
// @modal:escape on the same overlay element to run their close logic.
function onGlobalEsc(e) {
  if (e.key !== 'Escape') return;
  const openModals = document.querySelectorAll('[data-modal-root="true"]');
  if (!openModals.length) return;
  const topmost = openModals[openModals.length - 1];
  topmost.dispatchEvent(new CustomEvent('modal:escape', { bubbles: true, cancelable: true }));
}

onUnmounted(() => {
  if (typeof window !== 'undefined') {
    window.removeEventListener('maop:unauthorized', onUnauthorized);
    window.removeEventListener('resize', checkMobile);
    window.removeEventListener('keydown', onGlobalEsc);
  }
  realtime.disconnect();
});
</script>

<style>
:root { color-scheme: dark; }
* { margin: 0; padding: 0; box-sizing: border-box; }

/* 层叠覆盖布局 (2026-08-13 按用户指定重构):
 * 顶栏全宽 fixed (z-index:10) 始终不动; 侧栏全高 fixed (z-index:20) 覆盖顶栏左侧,
 * 展开时盖住顶栏左侧品牌区, 折叠 (rail) 时侧栏收窄为 64px, 品牌区自然露出。
 * 内容区用 padding-top/padding-left 给顶栏与侧栏腾位, 过渡平滑。
 */
.app-layout {
  /* 顶栏与侧栏均为 fixed, 不需要 flex row; 内容区用 padding 腾位 */
  min-height: 100vh;
  position: relative;
}

/* ── Sidebar ─────────────────────────────────────────────────────── */
/* 侧栏: 全高 fixed, z-index:20 高于顶栏 (z-index:10), 覆盖顶栏左侧品牌区。
   展开时宽 --sidebar-w 盖住品牌区, rail 时宽 --rail-w 品牌区露出。 */
.sidebar {
  position: fixed;
  top: 0;
  left: 0;
  bottom: 0;
  width: var(--sidebar-w);
  z-index: 20;
  background: var(--bg2);
  padding: 0;
  display: flex;
  flex-direction: column;
  border-right: 1px solid var(--border);
  transition: width var(--motion) var(--ease);
}

/* 侧栏头部: 仅放折叠按钮。展开时按钮靠左, rail 时居中。 */
.sidebar-head {
  display: flex;
  align-items: center;
  height: var(--topbar-h);
  padding: 0 var(--sp-3);
  flex-shrink: 0;
  position: relative;
}
/* 柔和过渡带: 用渐变取代硬分割线, 侧栏头部与导航区视觉连续 */
.sidebar-head::after {
  content: '';
  position: absolute;
  left: var(--sp-3);
  right: var(--sp-3);
  bottom: 0;
  height: 1px;
  background: linear-gradient(90deg, transparent, var(--border) 20%, var(--border) 80%, transparent);
  opacity: .5;
  pointer-events: none;
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
  font-size: var(--fs-xs); font-weight: 700; color: var(--text-faint);
  text-transform: uppercase; letter-spacing: .08em;
  padding: var(--sp-4) var(--sp-4) var(--sp-2);
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
  display: flex; align-items: center; gap: var(--sp-3);
  padding: var(--sp-2) var(--sp-4); color: var(--text-muted); text-decoration: none;
  font-size: var(--fs-base); font-weight: 500; border-radius: 0;
  border-left: 3px solid transparent;
  transition: color var(--motion) var(--ease), background var(--motion) var(--ease), border-color var(--motion) var(--ease), padding-left var(--motion) var(--ease);
  position: relative;
  margin: 1px 0;
}
.nav-link:hover {
  color: var(--text); background: var(--surface-2);
  padding-left: var(--sp-5);
}
.nav-link.router-link-active {
  color: var(--brand-strong); background: var(--brand-soft);
  border-left-color: var(--brand);
  font-weight: 600;
  padding-left: var(--sp-4);
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
  margin-top: auto; padding: var(--sp-3) var(--sp-4); border-top: 1px solid var(--border);
  display: flex; flex-direction: column; gap: var(--sp-2);
}
.nf-group { display: flex; flex-direction: column; gap: var(--sp-1); }
.nf-group.nf-status { flex-direction: row; align-items: center; justify-content: space-between; }
.nf-label { font-size: var(--fs-xs); font-weight: 700; color: var(--text-faint); text-transform: uppercase; letter-spacing: .06em; }

/* ── Rail (collapsed) state ─────────────────────────────────────── */
.app-layout.rail .sidebar { width: var(--rail-w); }
.app-layout.rail .content { padding-left: var(--rail-w); }
.app-layout.rail .nav-label,
.app-layout.rail .nav-section { display: none; }
.app-layout.rail .nav-link { justify-content: center; padding: 10px 0; gap: 0; }
.app-layout.rail .sidebar-head { justify-content: center; padding: 0; }

/* 层级分离: 顶栏与内容同明度(--surface), 侧栏下沉到 --bg2,
   形成 "侧栏(最暗) < 卡片/顶栏(中) < hover(最亮)" 的视觉阶梯。
   顶栏下沿与侧栏右缘的分界线让两个区域在第一眼就分开。 */
.app-layout > .topbar { border-bottom: 1px solid var(--border-strong); }

/* ── Content (padding 给顶栏与侧栏腾位) ─────────────────────────── */
/* 顶栏 fixed 全宽 (z-index:10) + 侧栏 fixed 全高 (z-index:20) 均脱离文档流,
   内容区用 padding-top/padding-left 腾位; rail 时 padding-left 随侧栏宽度收窄。 */
.content {
  padding-top: var(--topbar-h);
  padding-left: var(--sidebar-w);
  min-height: 100vh;
  overflow-y: auto;
  position: relative;
  display: flex;
  flex-direction: column;
  transition: padding-left var(--motion) var(--ease);
}
/* Centered, max-width shell so content aligns consistently on wide screens;
   the footer is pinned to the bottom via margin-top:auto inside this shell. */
.content-shell { flex: 1; min-height: 0; display: flex; flex-direction: column; width: 100%; max-width: var(--maxw); margin: 0 auto; padding: var(--sp-2) var(--content-pad) var(--content-pad); }

/* ── Auth overlay ───────────────────────────────────────────────── */
.auth-overlay {
  position: fixed; inset: 0; z-index: var(--z-modal);
  display: flex; align-items: center; justify-content: center;
  background: var(--overlay-scrim);
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

.sidebar-backdrop { position: fixed; inset: 0; background: var(--overlay-scrim); z-index: var(--z-overlay, 98); animation: maop-view-in var(--motion) var(--ease); }

@media (max-width: 899px) {
  /* 侧栏变为 drawer: z-index:30 高于顶栏(10)与桌面侧栏(20), 滑入滑出。
     position/top/left/bottom 已由桌面基类设为 fixed 全高, 此处仅覆盖 z-index 与 transform。 */
  .sidebar {
    z-index: 30;
    transform: translateX(-100%);
    transition: transform var(--motion-slow) var(--ease);
    box-shadow: var(--shadow-lg);
  }
  .sidebar-open .sidebar { transform: translateX(0); }
  /* 移动端侧栏为浮层 drawer, 不占位, 内容区无需 padding-left */
  .content { padding-left: 0; }
  /* hamburger: 粘在顶栏下方 12px 处 (而非视口顶部 12px), 避免滚动时浮在顶栏上
     遮挡顶栏内容。z-index 35: 高于 sidebar drawer(30) 使 X 按钮可点击关闭,
     低于 backdrop(80) 不干扰遮罩层。 */
  .hamburger-btn {
    display: flex;
    top: calc(var(--topbar-h) + 12px);
    z-index: 35;
  }
  .content-shell { padding: 16px; }
}
</style>
