<template>
  <header class="topbar">
    <!-- ① 系统标识（居左） -->
    <div class="topbar__brand">
      <div class="topbar__logo">
        <svg viewBox="0 0 24 24" width="26" height="26" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M12 2L2 7l10 5 10-5-10-5z"/><path d="M2 17l10 5 10-5"/><path d="M2 12l10 5 10-5"/>
        </svg>
      </div>
      <div class="topbar__brandtext">
        <div class="topbar__brandname">{{ t('topbar.systemName') }}</div>
        <div class="topbar__branddesc">{{ isZh ? t('topbar.systemNameZh') : t('topbar.systemNameEn') }}</div>
        <div class="topbar__statusline">
          <span class="topbar__live" :class="{ connected: realtimeOk }">
            <span class="topbar__livedot"></span>{{ realtimeOk ? t('status.live') : t('status.offline') }}
          </span>
          <span class="topbar__ver">v{{ appVersion }} · {{ editionLabel }}</span>
        </div>
      </div>
    </div>

    <!-- ② 刷新按钮 + 时间（标识右侧） -->
    <div class="topbar__refresh">
      <button class="topbar__refresh-btn" @click="doRefresh" :title="t('common.refresh')" aria-label="Refresh">
        <AppIcon name="refresh" :size="15" />
      </button>
      <span class="topbar__refreshtime">{{ formattedRefreshTime }}</span>
    </div>

    <div class="topbar__spacer"></div>

    <!-- ③ 布局/主题（用户左侧） -->
    <div class="topbar__prefs">
      <div class="topbar__pref-group">
        <Segmented :model-value="densityVal" :options="densityOpts" size="sm" @update:model-value="onDensityChange" />
      </div>
      <div class="topbar__pref-group">
        <Segmented :model-value="themeVal" :options="themeOpts" size="sm" @update:model-value="onThemeChange" />
      </div>
    </div>

    <!-- ④ 用户区（居右） -->
    <div class="topbar__user">
      <button class="topbar__avatar-btn" @click="goToUsers" :title="t('nav.users')" aria-label="User profile">
        <div class="topbar__avatar">{{ userInitial }}</div>
        <div class="topbar__usermeta">
          <span class="topbar__username">{{ userName || '—' }}</span>
          <span class="topbar__userrole">{{ roleLabel }}</span>
        </div>
      </button>
      <button v-if="authOn" class="topbar__logout-btn" @click="onLogout" :title="t('action.logout')" aria-label="Sign out">
        <AppIcon name="power" :size="14" />
        <span class="topbar__logout-text">{{ t('action.logout') }}</span>
      </button>
    </div>
  </header>
</template>

<script setup>
import { computed, ref, onMounted } from 'vue';
import { useRouter } from 'vue-router';
import { useI18n } from '../i18n/index.js';
import AppIcon from './AppIcon.vue';
import Segmented from './Segmented.vue';
import { useUiStore } from '../stores/ui.js';
import { useRealtimeStore } from '../stores/realtime.js';
import { useEditionStore } from '../stores/edition.js';
import { useApiStore } from '../stores/api.js';

const { t, locale } = useI18n();
const router = useRouter();
const ui = useUiStore();
const realtime = useRealtimeStore();
const edition = useEditionStore();
const api = useApiStore();

const isZh = computed(() => locale.value === 'zh');

// ── Stores 安全访问 ────────────────────────────────────────────────
const densityVal = computed(() => ui.density);
const themeVal = computed(() => ui.theme);
const realtimeOk = computed(() => realtime.connected);
const editionLabel = computed(() => edition.edition?.value === 'enterprise' ? 'Enterprise' : 'Personal');

function onDensityChange(v) { ui.setDensity(v); }
function onThemeChange(v) { ui.setTheme(v); }

const densityOpts = [
  { value: 'comfortable', label: t('settings.comfortable') },
  { value: 'compact', label: t('settings.compact') },
];
const themeOpts = [
  { value: 'light', icon: 'sun' },
  { value: 'dark', icon: 'moon' },
];

// ── 用户信息 ───────────────────────────────────────────────────────
function ls(key, fallback = '') { try { return localStorage.getItem(key) || fallback; } catch { return fallback; } }
function lsJSON(key, fallback = []) { try { const r = localStorage.getItem(key); return r ? JSON.parse(r) : fallback; } catch { return fallback; } }

const userName = ref(ls('maop_user'));
const userRoles = ref(lsJSON('maop_roles'));
const userInitial = computed(() => {
  const n = userName.value;
  if (!n) return '?';
  if (/[\u4e00-\u9fff]/.test(n)) return n.charAt(n.length - 1);
  return n.charAt(0).toUpperCase();
});

const roleLabel = computed(() => {
  const roles = userRoles.value;
  if (!Array.isArray(roles) || roles.length === 0) {
    return ls('maop_user') === 'admin' ? t('topbar.role.admin') : t('topbar.role.guest');
  }
  if (roles.includes('superadmin')) return t('topbar.role.superadmin');
  if (roles.includes('admin')) return t('topbar.role.admin');
  if (roles.includes('operator')) return t('topbar.role.operator');
  if (roles.includes('viewer')) return t('topbar.role.viewer');
  return roles[0];
});

const authOn = computed(() => ls('maop_auth_enabled') === 'true');
const appVersion = computed(() => ls('maop_version') || (typeof __APP_VERSION__ !== 'undefined' ? __APP_VERSION__ : 'unknown'));

// ── 刷新时间 ───────────────────────────────────────────────────────
const refreshTime = ref(new Date());
const formattedRefreshTime = computed(() => {
  const d = refreshTime.value;
  const pad = (n) => String(n).padStart(2, '0');
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
});

function doRefresh() {
  refreshTime.value = new Date();
  // 触发当前页刷新：派发全局事件，各 view 自行监听
  if (typeof window !== 'undefined') window.dispatchEvent(new CustomEvent('maop:refresh'));
}

function goToUsers() { router.push('/users'); }

async function onLogout() {
  try { await api.clearAuthToken(); } catch {}
  if (typeof window !== 'undefined' && window.location) window.location.reload();
}

onMounted(() => {
  userName.value = ls('maop_user');
  userRoles.value = lsJSON('maop_roles');
});
</script>

<style scoped>
.topbar {
  display: flex;
  align-items: center;
  gap: var(--sp-4);
  padding: 0 var(--sp-5);
  height: 56px;
  background: var(--surface);
  border-bottom: 1px solid var(--border);
  position: sticky;
  top: 0;
  z-index: var(--z-sticky, 30);
  flex-shrink: 0;
}

/* ① 品牌区 */
.topbar__brand { display: flex; align-items: center; gap: 10px; flex-shrink: 0; }
.topbar__logo {
  display: grid; place-items: center;
  width: 36px; height: 36px;
  border-radius: var(--r-md);
  background: var(--brand-soft);
  color: var(--brand-strong);
}
.topbar__brandtext { display: flex; flex-direction: column; line-height: 1.15; }
.topbar__brandname { font-size: 15px; font-weight: 700; color: var(--text); }
.topbar__branddesc { font-size: 11px; color: var(--text-muted); }
.topbar__statusline { display: flex; align-items: center; gap: 8px; margin-top: 2px; font-size: 10px; color: var(--text-faint); }
.topbar__live { display: inline-flex; align-items: center; gap: 4px; }
.topbar__livedot {
  width: 6px; height: 6px; border-radius: 50%;
  background: var(--text-faint);
}
.topbar__live.connected { color: var(--success); }
.topbar__live.connected .topbar__livedot { background: var(--success); }
.topbar__ver { font-family: var(--font-mono); }

/* ② 刷新区 */
.topbar__refresh { display: flex; align-items: center; gap: 8px; margin-left: var(--sp-3); }
.topbar__refresh-btn {
  display: grid; place-items: center;
  width: 30px; height: 30px;
  background: var(--surface-2); border: 1px solid var(--border);
  border-radius: var(--r-md); color: var(--text-muted);
  cursor: pointer;
  transition: color var(--motion) var(--ease), border-color var(--motion) var(--ease), background var(--motion) var(--ease);
}
.topbar__refresh-btn:hover { color: var(--text); border-color: var(--border-strong); background: var(--surface-3); }
.topbar__refreshtime { font-size: 11px; color: var(--text-faint); font-family: var(--font-mono); }

/* 弹性分隔 */
.topbar__spacer { flex: 1; }

/* ③ 布局/主题 */
.topbar__prefs { display: flex; align-items: center; gap: var(--sp-3); }
.topbar__pref-group { display: flex; align-items: center; }

/* ④ 用户区 */
.topbar__user { display: flex; align-items: center; gap: 8px; flex-shrink: 0; }
.topbar__avatar-btn {
  display: flex; align-items: center; gap: 8px;
  padding: 4px 10px 4px 4px;
  background: var(--surface-2); border: 1px solid var(--border);
  border-radius: var(--r-md);
  cursor: pointer;
  transition: border-color var(--motion) var(--ease);
}
.topbar__avatar-btn:hover { border-color: var(--border-strong); }
.topbar__avatar {
  width: 30px; height: 30px; flex-shrink: 0;
  border-radius: var(--r-full);
  background: linear-gradient(135deg, var(--brand), var(--chart-6));
  color: #fff; font-size: 12px; font-weight: 700;
  display: grid; place-items: center;
}
.topbar__usermeta { display: flex; flex-direction: column; line-height: 1.15; text-align: left; }
.topbar__username { font-size: 12px; font-weight: 600; color: var(--text); max-width: 120px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.topbar__userrole { font-size: 10px; color: var(--text-faint); }

.topbar__logout-btn {
  display: flex; align-items: center; gap: 4px;
  padding: 6px 8px;
  background: var(--surface-2); border: 1px solid var(--border);
  border-radius: var(--r-md);
  color: var(--fail); font-size: 11px;
  cursor: pointer;
  transition: background var(--motion) var(--ease), border-color var(--motion) var(--ease);
}
.topbar__logout-btn:hover { background: var(--fail-soft); border-color: var(--fail); }
.topbar__logout-text { font-weight: 600; }

@media (max-width: 1100px) {
  .topbar__branddesc { display: none; }
  .topbar__refreshtime { display: none; }
}

@media (max-width: 900px) {
  .topbar { gap: var(--sp-2); padding: 0 var(--sp-3); height: 52px; }
  .topbar__statusline { display: none; }
  .topbar__prefs { display: none; }
  .topbar__usermeta { display: none; }
  .topbar__logout-text { display: none; }
  .topbar__refresh { margin-left: var(--sp-2); }
}

@media (max-width: 600px) {
  .topbar__brandtext { display: none; }
}
</style>
