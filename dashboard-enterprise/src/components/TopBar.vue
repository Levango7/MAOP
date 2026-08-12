<template>
  <header class="topbar">
    <!-- 顶部细线 hairline，强化视觉边界 -->
    <div class="topbar__hairline" aria-hidden="true"></div>

    <!-- ① 系统标识（居左） -->
    <!-- 侧栏折叠按钮已移入侧栏自身头部(.sidebar-head/.sidebar-toggle),
         顶栏不再重复提供 — 见 App.vue -->
    <div class="topbar__brand">
      <div class="topbar__logo" aria-hidden="true">
        <svg viewBox="0 0 24 24" width="26" height="26" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M12 2L2 7l10 5 10-5-10-5z"/><path d="M2 17l10 5 10-5"/><path d="M2 12l10 5 10-5"/>
        </svg>
      </div>
      <div class="topbar__brandtext">
        <div class="topbar__brandname-row">
          <span class="topbar__brandname">{{ t('topbar.systemName') }}</span>
          <span class="topbar__brandedition">{{ editionLabel }}</span>
        </div>
        <div class="topbar__branddesc">{{ isZh ? t('topbar.systemNameZh') : t('topbar.systemNameEn') }}</div>
        <div class="topbar__statusline">
          <span class="topbar__live" :class="{ connected: realtimeOk }">
            <span class="topbar__livedot"></span>{{ realtimeOk ? t('status.live') : t('status.offline') }}
          </span>
          <span class="topbar__ver">v{{ appVersion }}</span>
        </div>
      </div>
    </div>

    <!-- 分隔符 -->
    <div class="topbar__divider" aria-hidden="true"></div>

    <!-- ② 刷新按钮 + 时间（标识右侧） -->
    <div class="topbar__refresh">
      <button class="topbar__refresh-btn" :title="t('common.refresh')" aria-label="Refresh" @click="doRefresh">
        <AppIcon name="refresh" :size="15" />
      </button>
      <div class="topbar__refreshmeta">
        <span class="topbar__refreshtime">{{ formattedRefreshTime }}</span>
        <span class="topbar__refreshlabel">{{ t('common.refresh') }}</span>
      </div>
    </div>

    <div class="topbar__spacer"></div>

    <!-- ③ 布局/主题（用户左侧） -->
    <div class="topbar__prefs">
      <div class="topbar__pref-group" :title="t('settings.density')">
        <Segmented :model-value="densityVal" :options="densityOpts" size="sm" @update:model-value="onDensityChange" />
      </div>
      <div class="topbar__pref-group" :title="t('settings.theme')">
        <Segmented :model-value="themeVal" :options="themeOpts" size="sm" @update:model-value="onThemeChange" />
      </div>
    </div>

    <!-- 分隔符 -->
    <div class="topbar__divider" aria-hidden="true"></div>

    <!-- ④ 用户区（居右） -->
    <div class="topbar__user">
      <button class="topbar__avatar-btn" :title="t('nav.users')" aria-label="User profile" @click="goToUsers">
        <div class="topbar__avatar">
          <span class="topbar__avatar-letter">{{ userInitial }}</span>
          <span class="topbar__avatar-ring" aria-hidden="true"></span>
        </div>
        <div class="topbar__usermeta">
          <span class="topbar__username">{{ userName || '—' }}</span>
          <span class="topbar__userrole">
            <span class="topbar__roledot" aria-hidden="true"></span>{{ roleLabel }}
          </span>
        </div>
      </button>
      <button v-if="authOn" class="topbar__logout-btn" :title="t('action.logout')" aria-label="Sign out" @click="onLogout">
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

defineEmits(['toggle-rail']);
defineProps({ rail: { type: Boolean, default: false } });

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
const editionLabel = computed(() => edition.edition === 'enterprise' ? 'Enterprise' : 'Personal');

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
  try { await api.clearAuthToken(); } catch { /* ignore */ }
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
  padding: 0 var(--sp-6);
  height: var(--topbar-h);
  background: var(--surface);
  border-bottom: 1px solid var(--border);
  /* 通栏布局 (2026-08-12 重构): 顶栏作为 .app-layout 的第一个子元素, 已在顶端,
     无需 sticky (外层不是滚动容器); 也用 .app-layout 的 flex 自然撑开 */
  position: relative;
  z-index: var(--z-topbar, 50);
  flex-shrink: 0;
}
/* ⓪ 侧栏折叠按钮 — 在顶栏最左, 永远在, 不被 rail 状态影响 */
.topbar__siderail-btn {
  display: grid; place-items: center;
  width: 34px; height: 34px;
  background: transparent;
  border: 1px solid var(--border);
  border-radius: var(--r-md);
  color: var(--text-muted);
  cursor: pointer;
  flex-shrink: 0;
  transition: color var(--motion) var(--ease), background var(--motion) var(--ease), border-color var(--motion) var(--ease);
}
.topbar__siderail-btn:hover {
  color: var(--text);
  background: var(--surface-2);
  border-color: var(--border-strong);
}
.topbar__siderail-btn:active { transform: scale(.96); }

/* 顶部细线：强化顶栏边界感 */
.topbar__hairline {
  position: absolute;
  top: 0; left: 0; right: 0;
  height: 1px;
  background: linear-gradient(90deg,
    transparent 0%,
    var(--topbar-hairline) 12%,
    var(--brand-faint) 50%,
    var(--topbar-hairline) 88%,
    transparent 100%);
  pointer-events: none;
  z-index: 1;
}
/* 右上品牌微光 */
.topbar::after {
  content: "";
  position: absolute;
  inset: 0;
  pointer-events: none;
  background: var(--topbar-glow);
  opacity: .7;
}

/* ① 品牌区 */
.topbar__brand { display: flex; align-items: center; gap: 12px; flex-shrink: 0; position: relative; }
.topbar__logo {
  display: grid; place-items: center;
  width: 34px; height: 34px;
  border-radius: var(--r-md);
  background: var(--brand);
  color: var(--brand-contrast);
  /* JB 精修: 移除双层渐变+内发光+外部投影 — 单一品牌色块, 克制 */
  position: relative;
}
.topbar__brandtext { display: flex; flex-direction: column; line-height: 1.2; min-width: 0; }
.topbar__brandname-row { display: flex; align-items: baseline; gap: 8px; }
.topbar__brandname {
  font-size: 16px; font-weight: 700; color: var(--text);
  letter-spacing: -0.012em;
}
.topbar__brandedition {
  font-size: 9.5px; font-weight: 700;
  color: var(--brand-strong);
  background: var(--brand-soft);
  border: 1px solid var(--brand-faint);
  padding: 2px 6px;
  border-radius: var(--r-sm);
  letter-spacing: .04em;
  text-transform: uppercase;
}
.topbar__branddesc {
  font-size: 10.5px; color: var(--text-muted);
  letter-spacing: .015em; margin-top: 1px;
}
.topbar__statusline {
  display: flex; align-items: center; gap: 8px; margin-top: 3px;
  font-size: 10px; color: var(--text-faint);
}
.topbar__live { display: inline-flex; align-items: center; gap: 5px; font-weight: 500; }
.topbar__livedot {
  width: 6px; height: 6px; border-radius: 50%;
  background: var(--text-faint);
  transition: background var(--motion) var(--ease), box-shadow var(--motion) var(--ease);
}
.topbar__live.connected { color: var(--success); }
.topbar__live.connected .topbar__livedot {
  background: var(--success);
  /* 呼吸灯动画删除 — 常亮圆点即是状态, 不需要脉动吸引注意 */
}
.topbar__ver {
  font-family: var(--font-mono);
  padding-left: 8px;
  border-left: 1px solid var(--border-subtle);
  color: var(--text-faint);
}

/* 分隔符 */
.topbar__divider {
  width: 1px;
  height: 36px;
  background: linear-gradient(180deg, transparent, var(--border) 25%, var(--border) 75%, transparent);
  flex-shrink: 0;
}

/* ② 刷新区 */
.topbar__refresh {
  display: flex; align-items: center; gap: 10px;
  padding: 5px 12px 5px 5px;
  background: var(--surface-2);
  border: 1px solid var(--border-subtle);
  border-radius: var(--r-full);
  position: relative;
  transition: border-color var(--motion) var(--ease), background var(--motion) var(--ease);
}
.topbar__refresh:hover { border-color: var(--border-strong); background: var(--surface-3); }
.topbar__refresh-btn {
  display: grid; place-items: center;
  width: 30px; height: 30px;
  background: var(--surface-3); border: 1px solid var(--border-subtle);
  border-radius: var(--r-full); color: var(--text-muted);
  cursor: pointer;
  transition: color var(--motion) var(--ease), background var(--motion) var(--ease), transform var(--motion-slow) var(--ease), border-color var(--motion) var(--ease);
}
.topbar__refresh-btn:hover {
  color: var(--brand-strong);
  background: var(--brand-soft);
  border-color: var(--brand);
}
.topbar__refresh-btn:active { transform: rotate(180deg); }
.topbar__refreshmeta { display: flex; flex-direction: column; line-height: 1.1; }
.topbar__refreshtime {
  font-size: 12px; color: var(--text);
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
  font-weight: 600;
}
.topbar__refreshlabel {
  font-size: 9.5px; color: var(--text-faint);
  letter-spacing: .02em;
}

/* 弹性分隔 */
.topbar__spacer { flex: 1; }

/* ③ 布局/主题 */
.topbar__prefs { display: flex; align-items: center; gap: var(--sp-3); position: relative; }

/* ④ 用户区 */
.topbar__user { display: flex; align-items: center; gap: 10px; flex-shrink: 0; position: relative; }
.topbar__avatar-btn {
  display: flex; align-items: center; gap: 10px;
  padding: 5px 14px 5px 5px;
  background: var(--surface-2); border: 1px solid var(--border-subtle);
  border-radius: var(--r-full);
  cursor: pointer;
  transition: border-color var(--motion) var(--ease), background var(--motion) var(--ease), box-shadow var(--motion) var(--ease);
  position: relative;
}
.topbar__avatar-btn:hover {
  border-color: var(--brand);
  background: var(--surface-3);
  box-shadow: 0 0 0 3px var(--brand-soft), var(--shadow-sm);
}
.topbar__avatar {
  width: 34px; height: 34px; flex-shrink: 0;
  border-radius: var(--r-full);
  background: linear-gradient(135deg, var(--brand) 0%, var(--chart-6) 100%);
  color: var(--brand-contrast); font-size: 13px; font-weight: 700;
  display: grid; place-items: center;
  box-shadow: var(--shadow-brand), inset 0 1px 0 rgba(255, 255, 255, .25);
  font-family: var(--font-sans);
  position: relative;
}
.topbar__avatar-ring {
  position: absolute;
  inset: -2px;
  border-radius: var(--r-full);
  border: 1px solid var(--brand-faint);
  pointer-events: none;
}
.topbar__avatar-btn:hover .topbar__avatar-ring {
  border-color: var(--brand);
  box-shadow: 0 0 0 3px var(--brand-soft);
}
.topbar__usermeta { display: flex; flex-direction: column; line-height: 1.2; text-align: left; min-width: 0; }
.topbar__username {
  font-size: 13px; font-weight: 600; color: var(--text);
  max-width: 130px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  letter-spacing: -0.005em;
}
.topbar__userrole {
  display: inline-flex; align-items: center; gap: 4px;
  font-size: 10px; color: var(--brand-strong);
  font-weight: 600; letter-spacing: .02em;
  margin-top: 1px;
}
.topbar__roledot {
  width: 4px; height: 4px; border-radius: 50%;
  background: var(--brand-strong);
  box-shadow: 0 0 4px rgba(129, 140, 248, .6);
}

.topbar__logout-btn {
  display: flex; align-items: center; gap: 6px;
  padding: 8px 12px;
  background: var(--surface-2); border: 1px solid var(--border-subtle);
  border-radius: var(--r-md);
  color: var(--fail); font-size: 11px;
  font-weight: 600;
  cursor: pointer;
  transition: background var(--motion) var(--ease), border-color var(--motion) var(--ease), transform var(--motion) var(--ease), box-shadow var(--motion) var(--ease);
}
.topbar__logout-btn:hover {
  background: var(--fail-soft); border-color: var(--fail);
  transform: translateY(-1px);
  box-shadow: 0 2px 8px rgba(239, 68, 68, .25);
}
.topbar__logout-btn:active { transform: translateY(0); }
.topbar__logout-text { font-weight: 600; }

@media (max-width: 1100px) {
  .topbar { padding: 0 var(--sp-5); }
  .topbar__branddesc { display: none; }
  .topbar__refreshlabel { display: none; }
  .topbar__refreshmeta { flex-direction: row; align-items: center; }
}

@media (max-width: 900px) {
  .topbar { gap: var(--sp-2); padding: 0 var(--sp-3); }
  .topbar__statusline { display: none; }
  .topbar__prefs { display: none; }
  .topbar__usermeta { display: none; }
  .topbar__logout-text { display: none; }
  .topbar__refresh { padding: 4px; }
  .topbar__refreshmeta { display: none; }
  .topbar__divider { display: none; }
  .topbar__avatar-btn { padding: 4px; }
  .topbar__brandedition { display: none; }
}

@media (max-width: 600px) {
  .topbar__brandtext { display: none; }
  .topbar__refresh { display: none; }
}
</style>
