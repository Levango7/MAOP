<template>
  <header class="topbar">
    <!-- 顶部细线 hairline，强化视觉边界 -->
    <div class="topbar__hairline" aria-hidden="true"></div>

    <!-- ① 系统标识（居左） -->
    <!-- 侧栏折叠按钮已移入侧栏自身头部(.sidebar-head/.sidebar-toggle),
         顶栏不再重复提供 — 见 App.vue -->
    <div class="topbar__brand">
      <div class="topbar__logo" aria-hidden="true">
        <svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M12 2L2 7l10 5 10-5-10-5z"/><path d="M2 17l10 5 10-5"/><path d="M2 12l10 5 10-5"/>
        </svg>
      </div>
      <div class="topbar__brandtext">
        <div class="topbar__brandname-row">
          <span class="topbar__brandname">{{ t('topbar.systemName') }}</span>
          <span class="topbar__brandedition">{{ editionLabel }}</span>
        </div>
        <div class="topbar__branddesc">{{ isZh ? t('topbar.systemNameZh') : t('topbar.systemNameEn') }}</div>
      </div>
    </div>

    <div class="topbar__spacer"></div>

    <!-- ②.5 分析仪表盘快速入口（IA 扩展：新增"分析"组直达） -->
    <button class="topbar__quick-analysis" :title="t('nav.analysis')" :aria-label="t('nav.analysis')" @click="goToAnalysis">
      <AppIcon name="activity" :size="15" />
      <span class="topbar__quick-analysis-label">{{ t('nav.analysis') }}</span>
    </button>

    <!-- ③ 布局/主题（用户左侧） -->
    <div class="topbar__prefs">
      <div class="topbar__pref-group topbar__pref-group--density" role="group" :aria-label="t('settings.density')" :title="t('settings.density')">
        <Segmented :model-value="densityVal" :options="densityOpts" size="sm" @update:model-value="onDensityChange" />
      </div>
      <div class="topbar__pref-group topbar__pref-group--theme" role="group" :aria-label="t('settings.theme')" :title="t('settings.theme')">
        <Segmented :model-value="themeVal" :options="themeOpts" size="sm" @update:model-value="onThemeChange" />
      </div>
    </div>

    <!-- ④ 刷新（居右工具簇；不再贴品牌区、无分割线） -->
    <button class="topbar__refresh" :title="t('common.refresh')" :aria-label="t('common.refresh')" @click="doRefresh">
      <AppIcon name="refresh" :size="15" />
      <span class="topbar__refreshtime">{{ formattedRefreshTime }}</span>
    </button>

    <!-- 分隔符（仅分隔工具簇与身份区） -->
    <div class="topbar__divider" aria-hidden="true"></div>

    <!-- ⑤ 用户区（居右） -->
    <div class="topbar__user">
      <button class="topbar__avatar-btn" :title="t('nav.users')" :aria-label="t('a11y.userProfile')" @click="goToUsers">
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
      <button v-if="authOn" class="topbar__logout-btn" :title="t('action.logout')" :aria-label="t('action.logout')" @click="onLogout">
        <AppIcon name="power" :size="14" />
        <span class="topbar__logout-text">{{ t('action.logout') }}</span>
      </button>
    </div>
  </header>
</template>

<script setup>
import { computed, ref, onMounted } from 'vue';
import { useRouter } from 'vue-router';
import { useI18n } from '../i18n';
import AppIcon from './AppIcon.vue';
import Segmented from './Segmented.vue';
import { useUiStore } from '../stores/ui.js';
import { useEditionStore } from '../stores/edition.js';
import { useApiStore } from '../stores/api.js';

defineEmits(['toggle-rail']);
defineProps({ rail: { type: Boolean, default: false } });

const { t, locale } = useI18n();
const router = useRouter();
const ui = useUiStore();
const edition = useEditionStore();
const api = useApiStore();

const isZh = computed(() => locale.value === 'zh');

// ── Stores 安全访问 ────────────────────────────────────────────────
const densityVal = computed(() => ui.density);
const themeVal = computed(() => ui.theme);
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

// IA 扩展: 顶栏直达分析仪表盘（新增"分析"导航组）
function goToAnalysis() { router.push('/analysis'); }

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
/* 顶栏硬编码值说明 (无精确 token 对应, 为顶栏统一 32px 胶囊微调):
 *   gap: 12px — 右侧工具簇(刷新/分析/偏好)之间距, 分组而不松散
 *   height: 32px — 全部交互胶囊统一高 (DESIGN.md §4 小按钮档)
 *   height: 24px — 分隔符竖线 (比胶囊短, 亚 token 视觉降噪)
 *   padding: 0 12px — 胶囊横向内边距 (图标+文字档)
 *   4px/5px/6px — 头像环/角色点等微间距 (亚 token 级别) */
.topbar {
  /* 通栏 fixed (T-UI 2026-09-30): 顶栏是顶栏——不被侧栏覆盖, 侧栏从顶栏下方开始;
     品牌大标题常驻可见 (废弃"展开盖品牌区"的层叠设计)。
     F32: z-index 硬编码 token 化 → var(--z-topbar-fixed) */
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  z-index: var(--z-topbar-fixed);
  display: flex;
  align-items: center;
  gap: var(--sp-3);
  padding: 0 var(--sp-6);
  height: var(--topbar-h);
  background: var(--surface);
  border-bottom: 1px solid var(--border);
}

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
  z-index: var(--z-raised);
}
/* 右上品牌微光: --topbar-glow 为 none, 伪元素无视觉效果, 已移除避免多余渲染 */

/* ① 品牌区 */
.topbar__brand { display: flex; align-items: center; gap: var(--sp-3); flex-shrink: 0; position: relative; }
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
.topbar__brandname-row { display: flex; align-items: baseline; gap: var(--sp-2); }
.topbar__brandname {
  /* T-UI: 顶栏左侧大标题——与页面标题同档 --fs-2xl; 侧栏下移后品牌区常驻, 撑得起大字 */
  font-size: var(--fs-2xl); font-weight: 800; color: var(--text);
  letter-spacing: -0.02em;
}
.topbar__brandedition {
  /* 修复: 9.5px 硬编码 token 化 → calc(var(--fs-2xs) - .5px) (10px - 0.5px) */
  font-size: calc(var(--fs-2xs) - .5px); font-weight: 700;
  color: var(--brand-strong);
  background: var(--brand-soft);
  border: 1px solid var(--brand-faint);
  padding: 2px 6px;
  border-radius: var(--r-sm);
  letter-spacing: .04em;
  text-transform: uppercase;
}
.topbar__branddesc {
  /* 修复: 10.5px 硬编码 token 化 → calc(var(--fs-xs) - .5px) (11px - 0.5px) */
  font-size: calc(var(--fs-xs) - .5px); color: var(--text-muted);
  letter-spacing: .015em; margin-top: 1px;
}
/* 分隔符 — 只留一处: 右侧工具簇与身份区之间 (品牌区旁的旧分割线已删) */
.topbar__divider {
  width: 1px;
  height: 24px;
  background: linear-gradient(180deg, transparent, var(--border) 25%, var(--border) 75%, transparent);
  flex-shrink: 0;
}

/* ④ 刷新 (居右工具簇; 与顶栏其他胶囊同高 32px, 图标+等宽时间) */
.topbar__refresh {
  display: inline-flex; align-items: center; gap: var(--sp-2);
  height: 32px;
  padding: 0 var(--sp-3);
  background: var(--surface-2);
  border: 1px solid var(--border-subtle);
  border-radius: var(--r-full);
  color: var(--text-muted);
  font-size: var(--fs-sm);
  cursor: pointer;
  flex-shrink: 0;
  transition: color var(--motion) var(--ease), background var(--motion) var(--ease), border-color var(--motion) var(--ease);
}
.topbar__refresh:hover {
  color: var(--brand-strong);
  background: var(--brand-soft);
  border-color: var(--brand);
}
/* 按下只转图标 — 整钮旋转会把时间文字倒过来 (旧圆钮时代的残留) */
.topbar__refresh .app-icon { transition: transform var(--motion-slow) var(--ease); }
.topbar__refresh:active .app-icon { transform: rotate(180deg); }
.topbar__refreshtime {
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
  font-weight: 600;
}

/* 弹性分隔 */
.topbar__spacer { flex: 1; }

/* ②.5 分析快速入口: 轻量胶囊按钮, 复用 design token, 不破坏顶栏紧凑布局 */
.topbar__quick-analysis {
  display: flex; align-items: center; gap: 6px;
  height: 32px;
  padding: 0 var(--sp-3);
  background: var(--surface-2);
  border: 1px solid var(--border-subtle);
  border-radius: var(--r-full);
  color: var(--text-muted);
  font-size: var(--fs-xs);
  font-weight: 600;
  cursor: pointer;
  flex-shrink: 0;
  transition: color var(--motion) var(--ease), background var(--motion) var(--ease), border-color var(--motion) var(--ease);
}
.topbar__quick-analysis:hover {
  color: var(--brand-strong);
  background: var(--brand-soft);
  border-color: var(--brand);
}
.topbar__quick-analysis-label { letter-spacing: .01em; }

/* ③ 布局/主题 */
.topbar__prefs { display: flex; align-items: center; gap: var(--sp-3); position: relative; }
/* 密度/主题 Segmented 与其他胶囊同高, 上下边对齐 */
.topbar__prefs :deep(.segmented) { height: 32px; }
.topbar__prefs :deep(.segmented__item) { height: 100%; }

/* ④ 用户区 */
.topbar__user { display: flex; align-items: center; gap: 10px; flex-shrink: 0; position: relative; }
.topbar__avatar-btn {
  display: flex; align-items: center; gap: var(--sp-2);
  height: 32px;
  padding: 0 var(--sp-3) 0 4px;
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
  width: 24px; height: 24px; flex-shrink: 0;
  border-radius: var(--r-full);
  /* 实底承载白字必须用 --brand-solid（白字 4.51:1）；--brand 只有 3.74:1。 */
  background: var(--brand-solid);
  color: var(--brand-contrast); font-size: 11px; font-weight: 700;
  display: grid; place-items: center;
  box-shadow: var(--shadow-brand), var(--shadow-inset-highlight);
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
.topbar__usermeta { display: flex; align-items: center; gap: 6px; line-height: 1.2; text-align: left; min-width: 0; }
.topbar__username {
  font-size: var(--fs-xs); font-weight: 600; color: var(--text);
  max-width: 120px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  letter-spacing: -0.005em;
}
.topbar__userrole {
  display: inline-flex; align-items: center; gap: 4px;
  font-size: var(--fs-2xs); color: var(--brand-strong);
  font-weight: 600; letter-spacing: .02em;
  white-space: nowrap;
}
.topbar__roledot {
  width: 4px; height: 4px; border-radius: var(--r-full);
  background: var(--brand-strong);
}

.topbar__logout-btn {
  display: flex; align-items: center; gap: 6px;
  height: 32px;
  padding: 0 var(--sp-3);
  background: var(--surface-2); border: 1px solid var(--border-subtle);
  border-radius: var(--r-md);
  color: var(--fail); font-size: var(--fs-xs);
  font-weight: 600;
  cursor: pointer;
  transition: background var(--motion) var(--ease), border-color var(--motion) var(--ease), transform var(--motion) var(--ease), box-shadow var(--motion) var(--ease);
}
.topbar__logout-btn:hover {
  background: var(--fail-soft); border-color: var(--fail);
  transform: translateY(-1px);
}
.topbar__logout-btn:active { transform: translateY(0); }
.topbar__logout-text { font-weight: 600; }

@media (max-width: 1100px) {
  .topbar { padding: 0 var(--sp-5); }
  .topbar__branddesc { display: none; }
}

@media (max-width: 900px) {
  .topbar { gap: var(--sp-2); padding: 0 var(--sp-3); }
  /* 移动端分析入口仅保留图标, 隐藏文字标签 */
  .topbar__quick-analysis-label { display: none; }
  .topbar__quick-analysis { padding: 0 6px; }
  /* 移动端保留主题切换(高使用频次), 仅隐藏密度切换(低使用频次)。
     修复 P3: 此前整个 .topbar__prefs 被隐藏导致移动端无法切换主题。 */
  .topbar__pref-group--density { display: none; }
  .topbar__usermeta { display: none; }
  .topbar__logout-text { display: none; }
  /* 刷新只留图标, 时间隐藏 */
  .topbar__refreshtime { display: none; }
  .topbar__refresh { padding: 0; width: 32px; justify-content: center; }
  .topbar__divider { display: none; }
  .topbar__avatar-btn { padding: 0 4px; }
  .topbar__brandedition { display: none; }
}

@media (max-width: 700px) {
  .topbar__brandtext { display: none; }
}

/* a11y: 键盘焦点环 (P0 focus-visible 补充) */
.topbar__refresh:focus-visible,
.topbar__quick-analysis:focus-visible,
.topbar__avatar-btn:focus-visible,
.topbar__logout-btn:focus-visible {
  outline: 2px solid var(--brand);
  outline-offset: 2px;
  border-radius: var(--r-sm);
}
</style>
