<template>
  <header class="page-header">
    <!-- Left: page icon + title -->
    <div class="page-header__main">
      <span class="page-header__icon" v-if="iconName">
        <AppIcon :name="iconName" :size="22" />
      </span>
      <div class="page-header__text">
        <div class="page-header__titlerow">
          <h1 class="page-header__title">{{ titleText }}</h1>
          <span class="page-header__badges"><slot name="badges" /></span>
        </div>
        <p class="page-header__sub" v-if="subtitleText">{{ subtitleText }}</p>
      </div>
    </div>

    <!-- Right: user info bar (moved from sidebar footer) + page actions -->
    <div class="page-header__right">
      <!-- User profile -->
      <div class="ph-user-bar" v-if="userInfo.name">
        <div class="ph-user-avatar">{{ userInfo.initial }}</div>
        <div class="ph-user-detail" v-if="!isCompact">
          <span class="ph-user-name">{{ userInfo.name }}</span>
          <div class="ph-user-roles" v-if="userInfo.roles.length">
            <span class="ph-role-badge" v-for="r in userInfo.roles" :key="r" :class="'ph-role-' + r">{{ r }}</span>
          </div>
        </div>
      </div>

      <div class="ph-sep" v-if="userInfo.name"></div>

      <!-- Status + version -->
      <div class="ph-meta" v-if="!isCompact">
        <span class="ph-live" :class="{ connected: realtimeOk }">
          <AppIcon name="radio" :size="11" />
          {{ realtimeOk ? liveText : offlineText }}
        </span>
        <span class="ph-version">v{{ appVersion }}</span>
      </div>

      <!-- Edition switcher -->
      <Segmented
        v-if="!isCompact && editionVal"
        :model-value="editionVal"
        :options="editionOpts"
        size="sm"
        :disabled="!isAdmin"
        @update:model-value="onEditionChange"
      />

      <!-- Density switcher -->
      <Segmented
        v-if="!isCompact"
        :model-value="densityVal"
        :options="densityOpts"
        size="sm"
        @update:model-value="onDensityChange"
      />

      <!-- Theme toggle -->
      <button class="ph-theme-btn" @click="onToggleTheme" :title="themeTitle" aria-label="Toggle theme">
        <AppIcon :name="isLightTheme ? 'moon' : 'sun'" :size="15" />
      </button>

      <!-- Logout -->
      <button v-if="authOn && !isCompact" class="ph-logout-btn" @click="onLogout" :title="logoutTitle" aria-label="Sign out">
        <AppIcon name="power" :size="15" />
      </button>

      <div class="ph-sep" v-if="userInfo.name || (!isCompact && editionVal)"></div>

      <!-- Page-specific action buttons (refresh etc.) -->
      <div class="page-header__actions">
        <slot />
      </div>
    </div>
  </header>
</template>

<script setup>
import { computed, reactive, ref } from 'vue';
import { useRoute } from 'vue-router';
import { useI18n } from '../i18n/index.js';
import { getPageMeta } from '../nav.js';
import AppIcon from './AppIcon.vue';
import Segmented from './Segmented.vue';
import { useEditionStore } from '../stores/edition.js';
import { useRealtimeStore } from '../stores/realtime.js';
import { useUiStore } from '../stores/ui.js';
import { useApiStore } from '../stores/api.js';

const props = defineProps({
  icon: { type: String, default: '' },
  title: { type: String, default: '' },
  subtitle: { type: String, default: '' },
});

const { t } = useI18n();
const route = useRoute();
const meta = getPageMeta(route.path) || {};

const iconName = computed(() => props.icon || meta.icon || '');
const titleText = computed(() => props.title || (meta.label ? t(meta.label) : ''));
const subtitleText = computed(() => props.subtitle || (meta.subtitle ? t(meta.subtitle) : ''));

// ── Stores (sync import, defensive) ──────────────────────────
let _edition, _realtime, _ui, _api;
try { _edition = useEditionStore(); } catch { _edition = null; }
try { _realtime = useRealtimeStore(); } catch { _realtime = null; }
try { _ui = useUiStore(); } catch { _ui = null; }
try { _api = useApiStore(); } catch { _api = null; }

// ── Safe accessors ────────────────────────────────────────────────
const editionVal = computed(() => _edition?.edition?.value ?? '');
const densityVal = computed(() => _ui?.density?.value ?? 'comfortable');
const realtimeOk = computed(() => _realtime?.connected?.value ?? false);
const isLightTheme = computed(() => (_ui?.theme?.value ?? 'dark') === 'light');
const isCompact = computed(() => (_ui?.density?.value ?? 'comfortable') === 'compact');

// ── User info (localStorage, safe fallbacks) ───────────────────────
function ls(key, fallback = '') { try { return localStorage.getItem(key) || fallback; } catch { return fallback; } }
function lsJSON(key, fallback = []) {
  try { const r = localStorage.getItem(key); return r ? JSON.parse(r) : fallback; } catch { return fallback; }
}
function getUserInitial(n) {
  if (!n) return '?';
  if (/[\u4e00-\u9fff]/.test(n)) return n.charAt(n.length - 1);
  return n.charAt(0).toUpperCase();
}

const userInfo = reactive({ name: ls('maop_user'), roles: lsJSON('maop_roles'), initial: '' });
userInfo.initial = getUserInitial(userInfo.name);

const appVersion = computed(() => ls('maop_version'));
const isAdmin = computed(() => {
  try {
    const roles = lsJSON('maop_roles');
    if (Array.isArray(roles) && roles.some((r) => r === 'admin' || r === 'superadmin')) return true;
  } catch {}
  return ls('maop_user') === 'admin';
});
const authOn = computed(() => ls('maop_auth_enabled') === 'true');

const editionOpts = [
  { value: 'enterprise', label: t('view.settings.enterprise') },
  { value: 'personal', label: t('view.settings.personal') },
];
const densityOpts = [
  { value: 'comfortable', label: t('settings.comfortable') },
  { value: 'compact', label: t('settings.compact') },
];

const liveText = t('status.live');
const offlineText = t('status.offline');
const themeTitle = t('action.toggleTheme');
const logoutTitle = t('action.logout');

// ── Event handlers (safe) ─────────────────────────────────────────
async function onEditionChange(target) {
  if (target === editionVal.value) return;
  if (!isAdmin.value) { window.alert(t('nav.editionLocked')); return; }
  const msg = target === 'personal' ? t('nav.editionToPersonal') : t('nav.editionToEnterprise');
  if (!window.confirm(msg)) return;
  try { await _edition?.switchEdition?.(target); } catch {}
}
function onDensityChange(val) { _ui?.setDensity?.(val); }
function onToggleTheme() { _ui?.toggleTheme?.(); }
async function onLogout() {
  try { await _api?.clearAuthToken?.(); } catch {}
  if (typeof window !== 'undefined' && window.location) window.location.reload();
}
</script>

<style>
/* Shared page header — card-style TOP BAR (sticky) with user info on right */
.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--sp-4);
  margin-bottom: var(--sp-6);
  flex-wrap: wrap;
  padding: var(--sp-3) var(--sp-5);
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r-lg);
  /* Sticky: stays visible when scrolling the content area */
  position: sticky;
  top: 0;
  z-index: var(--z-sticky, 30);
}
.page-header__main {
  display: flex;
  align-items: center;
  gap: var(--sp-3);
  min-width: 0;
}
.page-header__icon {
  display: grid;
  place-items: center;
  width: 40px;
  height: 40px;
  flex-shrink: 0;
  border-radius: var(--r-md);
  background: var(--brand-soft);
  color: var(--brand-strong);
}
.page-header__text { min-width: 0; }
.page-header__titlerow {
  display: flex;
  align-items: center;
  gap: var(--sp-3);
  flex-wrap: wrap;
}
.page-header__title {
  font-size: var(--fs-2xl, 22px);
  font-weight: 700;
  line-height: 1.2;
  color: var(--text);
  margin: 0;
}
.page-header__badges {
  display: inline-flex;
  align-items: center;
  gap: var(--sp-2);
}
.page-header__sub {
  font-size: var(--fs-sm, 13px);
  color: var(--text-muted);
  margin: 4px 0 0;
  line-height: 1.4;
}

/* ── Right side: user bar + actions ──────────────────────────────── */
.page-header__right {
  display: flex;
  align-items: center;
  gap: var(--sp-3);
  flex-wrap: wrap;
}

/* User profile card (compact, inline) */
.ph-user-bar {
  display: flex; align-items: center; gap: 8px;
  padding: 4px 8px 4px 4px;
  background: var(--surface-2); border: 1px solid var(--border);
  border-radius: var(--r-md); cursor: default;
  transition: border-color var(--motion) var(--ease);
}
.ph-user-bar:hover { border-color: var(--border-strong); }
.ph-user-avatar {
  width: 30px; height: 30px; flex-shrink: 0;
  border-radius: var(--r-full);
  background: linear-gradient(135deg, var(--brand), var(--chart-6));
  color: #fff; font-size: 12px; font-weight: 700;
  display: grid; place-items: center;
}
.ph-user-detail { min-width: 0; }
.ph-user-name {
  display: block; font-size: 12px; font-weight: 600;
  color: var(--text); white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  line-height: 1.3; max-width: 120px;
}
.ph-user-roles { display: flex; gap: 3px; flex-wrap: wrap; margin-top: 1px; }
.ph-role-badge {
  font-size: 9px; font-weight: 600; padding: 1px 5px; border-radius: var(--r-sm);
  text-transform: uppercase; letter-spacing: .04em; line-height: 1.6;
}
.ph-role-admin    { background: var(--fail-soft, rgba(239,68,68,.16)); color: var(--fail, #ef4444); }
.ph-role-superadmin { background: var(--brand-soft, rgba(168,85,247,.18)); color: var(--chart-6, #a78bfa); }
.ph-role-operator { background: var(--info-soft, rgba(56,189,248,.16)); color: var(--info, #38bdf8); }
.ph-role-viewer   { background: var(--border-subtle, rgba(148,163,184,.16)); color: var(--text-muted, #94a3b8); }

/* Meta info (live status + version) */
.ph-meta { display: flex; align-items: center; gap: 10px; }
.ph-live { display: inline-flex; align-items: center; gap: 4px; font-size: 11px; color: var(--text-faint); }
.ph-live.connected { color: var(--success); }
.ph-version { font-size: 11px; color: var(--text-faint); font-family: var(--font-mono); }

/* Separator */
.ph-sep { width: 1px; height: 24px; background: var(--border); flex-shrink: 0; }

/* Theme / logout buttons */
.ph-theme-btn, .ph-logout-btn {
  background: var(--surface-2); border: 1px solid var(--border); border-radius: var(--r-md);
  color: var(--text-muted); width: 32px; height: 32px; display: grid; place-items: center;
  transition: color var(--motion) var(--ease), border-color var(--motion) var(--ease), background var(--motion) var(--ease);
}
.ph-theme-btn:hover { color: var(--text); border-color: var(--border-strong); background: var(--surface-3); }
.ph-logout-btn { color: var(--fail); }
.ph-logout-btn:hover { background: var(--fail-soft); }

/* Actions slot */
.page-header__actions {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  flex-wrap: wrap;
}

@media (max-width: 900px) {
  .page-header { gap: var(--sp-3); padding: var(--sp-3) var(--sp-4); }
  .page-header__right { width: 100%; justify-content: flex-start; }
  .ph-user-name { max-width: 100px; }
  .ph-meta, .ph-sep { display: none; }
}

@media (max-width: 700px) {
  .page-header__right { flex-direction: column; align-items: flex-start; }
  .ph-user-detail { display: none; }
}
</style>
