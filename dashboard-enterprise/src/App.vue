<template>
  <div class="app-layout" :class="{ 'light-theme': isLight, 'sidebar-open': sidebarOpen && isMobile }">
    <!-- D3 (2026-07-22, Phase D): mobile drawer backdrop — click to close -->
    <div
      v-if="sidebarOpen && isMobile"
      class="sidebar-backdrop"
      @click="closeSidebar"
      aria-hidden="true"
    ></div>
    <nav class="sidebar" @click="onSidebarClick">
      <div class="logo">
        <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2L2 7l10 5 10-5-10-5z"/><path d="M2 17l10 5 10-5"/><path d="M2 12l10 5 10-5"/></svg>
        <span>MAOP</span>
      </div>
      <div class="nav-section">Core</div>
      <router-link to="/" class="nav-link"><span class="nav-icon">📊</span>Overview</router-link>
      <router-link to="/control" class="nav-link"><span class="nav-icon">▶️</span>Control</router-link>
      <router-link to="/chat" class="nav-link"><span class="nav-icon">💬</span>Chat</router-link>
      <router-link to="/agents" class="nav-link"><span class="nav-icon">🤖</span>Agents</router-link>
      <router-link to="/memory" class="nav-link"><span class="nav-icon">🧠</span>Memory</router-link>
      <router-link to="/evolve" class="nav-link"><span class="nav-icon">🧬</span>Evolution</router-link>
      <div class="nav-section">Search & Tools</div>
      <router-link to="/search" class="nav-link"><span class="nav-icon">🔎</span>Search</router-link>
      <router-link to="/vector" class="nav-link"><span class="nav-icon">🔍</span>Vector</router-link>
      <router-link to="/tools" class="nav-link"><span class="nav-icon">🛠️</span>Tools</router-link>
      <router-link to="/models" class="nav-link"><span class="nav-icon">🧮</span>Models</router-link>
      <div class="nav-section">Ops</div>
      <router-link to="/logs" class="nav-link"><span class="nav-icon">📝</span>Logs</router-link>
      <router-link to="/monitor" class="nav-link"><span class="nav-icon">📈</span>Monitor</router-link>
      <router-link to="/cost" class="nav-link"><span class="nav-icon">💰</span>Cost</router-link>
      <div class="nav-section">Enterprise</div>
      <router-link to="/audit" class="nav-link"><span class="nav-icon">📋</span>Audit</router-link>
      <router-link to="/rbac" class="nav-link"><span class="nav-icon">🔐</span>RBAC</router-link>
      <router-link to="/tenants" class="nav-link"><span class="nav-icon">🏢</span>Tenants</router-link>
      <router-link to="/settings" class="nav-link"><span class="nav-icon">⚙️</span>Settings</router-link>
      <div class="nav-footer">
        <span class="dot"></span>
        <span>v{{ version }}</span>
        <span class="live-indicator" :class="{ connected: realtimeConnected }">
          <span class="live-dot"></span>
          {{ realtimeConnected ? 'Live' : 'Offline' }}
        </span>
        <button class="theme-btn" @click="toggleTheme" :title="isLight ? 'Switch to dark' : 'Switch to light'">{{ isLight ? '🌙' : '☀️' }}</button>
        <button v-if="authEnabled" class="theme-btn logout-btn" @click="doLogout" title="Sign out">⏻</button>
      </div>
    </nav>
    <main class="content">
      <!-- D3: hamburger button — only visible on narrow screens (CSS-controlled) -->
      <button
        class="hamburger-btn"
        @click="toggleSidebar"
        :aria-expanded="sidebarOpen"
        aria-label="Toggle navigation menu"
      >
        <span></span><span></span><span></span>
      </button>
      <router-view />
    </main>
    <div v-if="authExpired" class="auth-overlay">
      <div class="auth-card">
        <h3>{{ loginTitle }}</h3>
        <p v-if="loginError" class="login-error">{{ loginError }}</p>
        <form @submit.prevent="doLogin">
          <input
            v-model="loginUsername"
            type="text"
            placeholder="Username"
            autocomplete="username"
            :disabled="loginLoading"
          />
          <input
            v-model="loginPassword"
            type="password"
            placeholder="Password"
            autocomplete="current-password"
            :disabled="loginLoading"
          />
          <button type="submit" :disabled="loginLoading || !loginUsername || !loginPassword">
            {{ loginLoading ? 'Signing in...' : 'Sign In' }}
          </button>
        </form>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue';
import { useEditionStore } from './stores/edition.js';
import { useApiStore, withAuth } from './stores/api.js';
import { useRealtimeStore } from './stores/realtime.js';

const edition = useEditionStore();
const api = useApiStore();
const realtime = useRealtimeStore();
const realtimeConnected = computed(() => realtime.connected);
const isLight = ref(localStorage.getItem('maop_theme') !== 'dark');
const version = ref('4.0.0');
const authExpired = ref(false);
// F-P0-8 fix: login form state
const loginUsername = ref('');
const loginPassword = ref('');
const loginError = ref('');
const loginLoading = ref(false);
const loginTitle = ref('Sign In');

// D3 (2026-07-22, Phase D): responsive drawer state.
// On desktop (≥900px) the sidebar is a static flex column and these
// refs are irrelevant. On narrow screens the sidebar becomes a
// fixed-position drawer that slides in/out via CSS transform.
const sidebarOpen = ref(false);
const isMobile = ref(false);

// Breakpoint (px) below which the sidebar becomes a drawer.
// 900px gives the 220px sidebar + ~680px content — below this the
// content gets too cramped for the data-dense dashboard pages.
const MOBILE_BREAKPOINT = 900;

function checkMobile() {
  if (typeof window === 'undefined') return;
  isMobile.value = window.innerWidth < MOBILE_BREAKPOINT;
  // Auto-close the drawer when resizing back to desktop so the static
  // layout is clean on the next mobile visit.
  if (!isMobile.value) sidebarOpen.value = false;
}

function toggleSidebar() {
  sidebarOpen.value = !sidebarOpen.value;
}

function closeSidebar() {
  sidebarOpen.value = false;
}

// Event delegation: close the drawer when any nav-link is clicked on
// mobile. This avoids adding @click to every router-link individually.
function onSidebarClick(e) {
  if (isMobile.value && sidebarOpen.value && e.target.closest('.nav-link')) {
    closeSidebar();
  }
}

function toggleTheme() {
  isLight.value = !isLight.value;
  localStorage.setItem('maop_theme', isLight.value ? 'light' : 'dark');
  if (typeof document !== 'undefined') {
    document.body.classList.toggle('light-theme', isLight.value);
  }
}

// 401 监听：由 api store 触发，显示会话过期浮层
function onUnauthorized() {
  authExpired.value = true;
  loginTitle.value = 'Session Expired';
  loginError.value = '';
}

async function doLogin() {
  loginLoading.value = true;
  loginError.value = '';
  try {
    const r = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        username: loginUsername.value,
        password: loginPassword.value,
      }),
    });
    const data = await r.json();
    if (r.ok && data.status === 'ok' && data.token) {
      localStorage.setItem('maop_token', data.token);
      authExpired.value = false;
      loginPassword.value = '';
      // Reload to re-initialize stores with new token
      if (typeof window !== 'undefined' && window.location) window.location.reload();
    } else {
      loginError.value = data.error || 'Login failed';
    }
  } catch (e) {
    loginError.value = e.message || 'Network error';
  } finally {
    loginLoading.value = false;
  }
}

function reloadForLogin() {
  authExpired.value = false;
  api.clearAuthToken();
  if (typeof window !== 'undefined' && window.location) window.location.reload();
}

// P1 fix: user-initiated logout — revoke token server-side then reload
const authEnabled = ref(false);
async function checkAuthEnabled() {
  try {
    const r = await fetch('/api/auth/status');
    const d = await r.json();
    authEnabled.value = d.auth_enabled === true;
  } catch { authEnabled.value = false; }
}

async function doLogout() {
  try {
    await api.clearAuthToken();
  } catch { /* ignore */ }
  if (typeof window !== 'undefined' && window.location) window.location.reload();
}

onMounted(async () => {
  if (typeof window !== 'undefined') {
    window.addEventListener('maop:unauthorized', onUnauthorized);
    window.addEventListener('resize', checkMobile);
  }
  checkMobile(); // initial detection
  if (typeof document !== 'undefined') {
    document.body.classList.toggle('light-theme', isLight.value);
  }
  await edition.fetchEdition();
  checkAuthEnabled(); // P1 fix: check if auth is enabled to show/hide logout button
  try {
    // 注入 Bearer token（与零构建版行为一致）
    const r = await fetch('/api/health', withAuth({}, {}));
    if (r.ok) {
      const d = await r.json();
      version.value = d.version || '4.0.0';
    } else if (r.status === 401) {
      // 未登录：保持默认版本号，不强制弹窗（用户可能尚未启用 auth）
    }
  } catch {}
  // Establish the global realtime WebSocket connection.
  realtime.connect();
});

onUnmounted(() => {
  if (typeof window !== 'undefined') {
    window.removeEventListener('maop:unauthorized', onUnauthorized);
    window.removeEventListener('resize', checkMobile);
  }
  if (typeof document !== 'undefined') {
    document.body.classList.remove('light-theme');
  }
  // Tear down the global realtime WebSocket connection.
  realtime.disconnect();
});
</script>

<style>
:root {
  --bg: #0f172a; --bg2: #1e293b; --bg3: #334155; --bg4: #0f172a;
  --border: #334155; --text: #e2e8f0; --text2: #94a3b8; --text3: #64748b;
  --accent: #3b82f6; --accent2: #60a5fa; --success: #22c55e; --warn: #f59e0b; --fail: #ef4444;
  --radius: 12px; --shadow: 0 1px 3px rgba(0,0,0,.3);
}
:root .light-theme, .light-theme {
  --bg: #ffffff; --bg2: #e8f4ff; --bg3: #d1e9ff; --bg4: #ffffff;
  --border: #b8d4f0; --text: #1a2332; --text2: #3d4f65; --text3: #6b7d94;
  --accent: #2563eb; --accent2: #3b82f6; --success: #16a34a; --warn: #d97706; --fail: #dc2626;
  --radius: 14px; --shadow: 0 1px 4px rgba(0,0,0,.08);
}
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Inter', 'Segoe UI', sans-serif; background: var(--bg); color: var(--text); }
.app-layout { display: flex; min-height: 100vh; }
.sidebar { width: 220px; background: var(--bg2); padding: 16px 0; display: flex; flex-direction: column; border-right: 1px solid var(--border); position: sticky; top: 0; height: 100vh; overflow-y: auto; flex-shrink: 0; }
.sidebar .logo { padding: 12px 20px; font-size: 17px; font-weight: 700; color: var(--accent); margin-bottom: 8px; display: flex; align-items: center; gap: 8px; }
.nav-section { font-size: 10px; font-weight: 700; color: var(--text3); text-transform: uppercase; letter-spacing: 1px; padding: 16px 20px 4px; border-top: 1px solid var(--border); }
.nav-section:first-of-type { border-top: none; }
.nav-link { padding: 9px 20px; color: var(--text2); text-decoration: none; font-size: 13px; font-weight: 500; transition: all .15s; display: flex; align-items: center; gap: 8px; border-radius: 0; }
.nav-link:hover { color: var(--text); background: var(--bg3); }
.nav-link.router-link-active { color: var(--accent); background: color-mix(in srgb, var(--accent) 10%, transparent); border-right: 3px solid var(--accent); }
.nav-icon { font-size: 15px; width: 20px; text-align: center; }
.nav-footer { margin-top: auto; padding: 12px 20px; border-top: 1px solid var(--border); font-size: 11px; color: var(--text3); display: flex; align-items: center; gap: 6px; }
.dot { width: 7px; height: 7px; border-radius: 50%; background: var(--success); animation: pulse 2s infinite; }
@keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: .4; } }
.live-indicator { display: inline-flex; align-items: center; gap: 4px; margin-left: 8px; font-size: 11px; color: var(--text3); }
.live-indicator .live-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--text3); transition: background .2s; }
.live-indicator.connected { color: var(--success); }
.live-indicator.connected .live-dot { background: var(--success); animation: pulse 2s infinite; }
.theme-btn { margin-left: auto; background: none; border: 1px solid var(--border); border-radius: 6px; cursor: pointer; font-size: 14px; padding: 2px 6px; }
.logout-btn { margin-left: 4px; color: var(--fail); }
.logout-btn:hover { background: var(--bg3); }
.content { flex: 1; padding: 28px; overflow-y: auto; min-width: 0; }

.auth-overlay { position: fixed; inset: 0; background: rgba(0,0,0,.55); display: flex; align-items: center; justify-content: center; z-index: 9999; }
.auth-card { background: var(--bg2); border: 1px solid var(--border); border-radius: 12px; padding: 24px; max-width: 360px; text-align: center; }
.auth-card h3 { margin-bottom: 12px; color: var(--text); }
.auth-card p { color: var(--text2); font-size: 13px; margin-bottom: 16px; }
.auth-card button { background: var(--accent); color: #fff; border: none; border-radius: 6px; padding: 8px 16px; cursor: pointer; font-size: 13px; width: 100%; margin-top: 8px; }
.auth-card button:disabled { opacity: 0.5; cursor: not-allowed; }
.auth-card form { display: flex; flex-direction: column; gap: 8px; }
.auth-card input { background: var(--bg); border: 1px solid var(--border); border-radius: 6px; padding: 8px 12px; color: var(--text); font-size: 13px; }
.auth-card input:focus { outline: none; border-color: var(--accent); }
.login-error { color: var(--fail); font-size: 12px; margin-bottom: 8px; }

.light-theme .panel,
.light-theme .stat-card,
.light-theme .metric-card,
.light-theme .agent-card,
.light-theme .entry-card,
.light-theme .result-card,
.light-theme .strategy-item,
.light-theme .history-content,
.light-theme .dispatch-card,
.light-theme .maint-card,
.light-theme .layer-card,
.light-theme .vrow:not(.header),
.light-theme .trow:not(.header),
.light-theme .search-panel,
.light-theme .version-table,
.light-theme .dispatch-panel,
.light-theme .detail-panel,
.light-theme .degradation-panel {
  background: #e8f4ff !important;
  border-color: #b8d4f0 !important;
  box-shadow: 0 1px 4px rgba(0,0,0,.06) !important;
}
.light-theme .sidebar { background: #e8f4ff !important; border-right-color: #b8d4f0 !important; }
.light-theme .nav-link:hover { background: #d1e9ff !important; }
.light-theme .nav-link.router-link-active { background: rgba(37,99,235,.12) !important; }
.light-theme textarea,
.light-theme input[type="text"],
.light-theme input[type="number"],
.light-theme select {
  background: #ffffff !important;
  border-color: #b8d4f0 !important;
  color: #1a2332 !important;
}
.light-theme .msg-bubble { background: #ffffff !important; border-color: #b8d4f0 !important; }
.light-theme .msg-row.user .msg-bubble { background: rgba(37,99,235,.08) !important; border-color: rgba(37,99,235,.2) !important; }

/* ── D3 (2026-07-22, Phase D): Responsive layout shell ──────────── */
/* Hamburger button — hidden on desktop, shown via media query on mobile. */
.hamburger-btn {
  display: none;
  flex-direction: column;
  justify-content: center;
  gap: 4px;
  width: 38px;
  height: 38px;
  background: var(--bg2);
  border: 1px solid var(--border);
  border-radius: 8px;
  cursor: pointer;
  padding: 0 8px;
  margin-bottom: 12px;
  position: sticky;
  top: 12px;
  z-index: 100;
  transition: background .15s;
}
.hamburger-btn:hover { background: var(--bg3); }
.hamburger-btn span {
  display: block;
  width: 22px;
  height: 2px;
  background: var(--text);
  border-radius: 1px;
  transition: transform .2s, opacity .2s;
}
/* Animate hamburger into an X when sidebar is open (mobile only). */
.sidebar-open .hamburger-btn span:nth-child(1) { transform: translateY(6px) rotate(45deg); }
.sidebar-open .hamburger-btn span:nth-child(2) { opacity: 0; }
.sidebar-open .hamburger-btn span:nth-child(3) { transform: translateY(-6px) rotate(-45deg); }

/* Sidebar backdrop — only rendered on mobile when drawer is open. */
.sidebar-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(0,0,0,.5);
  z-index: 998;
  animation: fadeIn .15s ease;
}
@keyframes fadeIn { from { opacity: 0; } to { opacity: 1; } }

/* ── Mobile breakpoint: sidebar becomes a slide-in drawer ──────── */
@media (max-width: 899px) {
  .sidebar {
    position: fixed;
    top: 0;
    left: 0;
    z-index: 999;
    transform: translateX(-100%);
    transition: transform .25s ease;
    box-shadow: 2px 0 12px rgba(0,0,0,.3);
  }
  /* When sidebarOpen is true on mobile, slide the drawer in. */
  .sidebar-open .sidebar { transform: translateX(0); }
  /* Show the hamburger button on mobile. */
  .hamburger-btn { display: flex; }
  /* Reduce content padding on narrow screens. */
  .content { padding: 16px; }
}
</style>