<template>
  <div ref="rootRef" class="notif-bell">
    <button
      type="button"
      class="notif-bell__btn"
      :aria-label="t('view.notifications.bellTitle')"
      @click="togglePanel"
    >
      <AppIcon name="info" :size="16" />
      <span v-if="unreadCount > 0" class="notif-bell__badge">{{ displayCount }}</span>
    </button>

    <Transition name="bell-pop">
      <div
        v-if="panelOpen"
        class="notif-bell__panel"
        role="dialog"
        :aria-label="t('view.notifications.bellTitle')"
      >
        <div class="notif-bell__head">
          <span>{{ t('view.notifications.bellTitle') }}</span>
          <span class="notif-bell__count">{{ unreadCount }}</span>
        </div>

        <div v-if="panelLoading && !recent.length" class="notif-bell__loading">
          {{ t('common.loading') }}
        </div>
        <div v-else-if="!recent.length" class="notif-bell__empty">
          {{ t('view.notifications.bellEmpty') }}
        </div>
        <ul v-else class="notif-bell__list">
          <li
            v-for="n in recent"
            :key="n.id"
            class="notif-bell__item"
            :class="{ 'is-unread': !n.read }"
            @click="onItemClick(n)"
          >
            <span class="notif-bell__dot" :class="'dot-' + n.level" aria-hidden="true" />
            <div class="notif-bell__item-main">
              <div class="notif-bell__item-title">{{ n.title }}</div>
              <div class="notif-bell__item-meta">
                <span>{{ t(levelLabelKey(n.level)) }}</span>
                <span>·</span>
                <span>{{ relativeTime(n.created_at) }}</span>
              </div>
            </div>
          </li>
        </ul>

        <button class="notif-bell__view-all" @click="goToAll">
          {{ t('view.notifications.bellViewAll') }}
          <AppIcon name="chevron-right" :size="12" />
        </button>
      </div>
    </Transition>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount, watch } from 'vue';
import { useRouter } from 'vue-router';
import { useApiStore } from '../stores/api.js';
import { useI18n } from '../i18n';
import AppIcon from '../components/AppIcon.vue';

const POLL_INTERVAL_MS = 30000;
const RECENT_LIMIT = 5;

const { t } = useI18n();
const api = useApiStore();
const router = useRouter();

const unreadCount = ref(0);
const recent = ref([]);
const panelOpen = ref(false);
const panelLoading = ref(false);
const rootRef = ref(null);
let pollTimer = null;

const displayCount = computed(() => (unreadCount.value > 99 ? '99+' : String(unreadCount.value)));

// ── Polling ──
async function refreshUnreadCount() {
  try {
    const d = await api.get('/api/notifications/unread-count');
    unreadCount.value = d.count ?? d.unread_count ?? d.unread ?? 0;
  } catch {
    // silent: bell should never block UI on background poll failure
  }
}

async function loadRecent() {
  panelLoading.value = true;
  try {
    const d = await api.get('/api/notifications/list', {
      limit: RECENT_LIMIT,
      offset: 0,
      unread_only: true,
    });
    const items = d.notifications || d.items || d.data || [];
    recent.value = items.map((n) => ({
      id: n.id,
      level: n.level || 'info',
      category: n.category || 'system',
      title: n.title || '',
      message: n.message || '',
      read: !!n.read,
      created_at: n.created_at || n.created_at_ms || 0,
      metadata: n.metadata || {},
    }));
  } catch {
    recent.value = [];
  } finally {
    panelLoading.value = false;
  }
}

function startPolling() {
  stopPolling();
  pollTimer = setInterval(refreshUnreadCount, POLL_INTERVAL_MS);
}
function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

// ── Panel control ──
function togglePanel() {
  panelOpen.value = !panelOpen.value;
}
watch(panelOpen, (open) => {
  if (open) {
    loadRecent();
    document.addEventListener('click', onDocClick, true);
  } else {
    document.removeEventListener('click', onDocClick, true);
  }
});

function onDocClick(e) {
  if (rootRef.value && !rootRef.value.contains(e.target)) {
    panelOpen.value = false;
  }
}

// ── Item click: mark read + navigate ──
async function onItemClick(n) {
  if (!n.read) {
    try {
      await api.post(`/api/notifications/${n.id}/read`, {});
      n.read = true;
      unreadCount.value = Math.max(0, unreadCount.value - 1);
    } catch {
      // ignore — still navigate
    }
  }
  panelOpen.value = false;
  navigateToDetail(n);
}

function goToAll() {
  panelOpen.value = false;
  navigateToNotifications();
}

function navigateToNotifications() {
  if (router && router.push) {
    router.push('/notifications').catch(() => { /* ignore navigation errors */ });
  }
}
function navigateToDetail(n) {
  if (!router || !router.push) return;
  // Route by category if a deep link is desired; otherwise land on the hub.
  const target = categoryRoute(n.category);
  router.push(target).catch(() => { /* ignore */ });
}
function categoryRoute(cat) {
  // Map notification category → existing route. Falls back to /notifications.
  const map = {
    agent: '/agents',
    cost: '/cost',
    security: '/audit',
    tenant: '/tenants',
    quota: '/quotas',
    system: '/notifications',
  };
  return map[cat] || '/notifications';
}

// ── Helpers ──
function levelLabelKey(lv) {
  if (lv === 'error') return 'view.notifications.levelError';
  if (lv === 'warning') return 'view.notifications.levelWarning';
  if (lv === 'success') return 'view.notifications.levelSuccess';
  return 'view.notifications.levelInfo';
}
function toMs(ts) {
  if (!ts) return 0;
  if (typeof ts === 'number') return ts > 1e12 ? ts : ts * 1000;
  const d = new Date(ts);
  return isNaN(d.getTime()) ? 0 : d.getTime();
}
function relativeTime(ts) {
  const ms = toMs(ts);
  if (!ms) return '';
  const diff = Math.max(0, Date.now() - ms);
  const m = Math.floor(diff / 60000);
  if (m < 1) return t('view.notifications.justNow');
  if (m < 60) return t('view.notifications.minutesAgo', { n: m });
  const h = Math.floor(m / 60);
  if (h < 24) return t('view.notifications.hoursAgo', { n: h });
  const d = Math.floor(h / 24);
  return t('view.notifications.daysAgo', { n: d });
}

onMounted(() => {
  refreshUnreadCount();
  startPolling();
});
onBeforeUnmount(() => {
  stopPolling();
  document.removeEventListener('click', onDocClick, true);
});
</script>

<style scoped>
.notif-bell { position: relative; display: inline-flex; }

.notif-bell__btn {
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 34px; height: 34px;
  background: transparent;
  border: 1px solid transparent;
  border-radius: var(--r-md);
  color: var(--text-muted);
  cursor: pointer;
  transition: background var(--motion) var(--ease), color var(--motion) var(--ease);
}
.notif-bell__btn:hover { background: var(--surface-2); color: var(--text); }
.notif-bell__badge {
  position: absolute;
  top: -2px; right: -2px;
  min-width: 16px; height: 16px;
  padding: 0 4px;
  background: var(--fail);
  color: #fff;
  font-size: 10px;
  font-weight: 700;
  line-height: 16px;
  border-radius: 8px;
  box-shadow: 0 0 0 2px var(--surface);
}

.notif-bell__panel {
  position: absolute;
  top: calc(100% + 6px);
  right: 0;
  width: 320px;
  max-width: 92vw;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r-lg);
  box-shadow: var(--shadow-lg);
  z-index: calc(var(--z-modal, 90) + 4);
  overflow: hidden;
  display: flex;
  flex-direction: column;
}
.notif-bell__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--sp-2) var(--sp-3);
  font-size: var(--fs-sm);
  font-weight: 600;
  color: var(--text);
  border-bottom: 1px solid var(--border);
}
.notif-bell__count {
  font-size: var(--fs-xs);
  color: var(--text-muted);
  background: var(--surface-2);
  padding: 2px 6px;
  border-radius: var(--r-full);
}
.notif-bell__loading,
.notif-bell__empty {
  padding: var(--sp-4);
  text-align: center;
  font-size: var(--fs-sm);
  color: var(--text-muted);
}
.notif-bell__list { list-style: none; margin: 0; padding: 0; max-height: 320px; overflow-y: auto; }
.notif-bell__item {
  display: grid;
  grid-template-columns: 8px 1fr;
  gap: var(--sp-2);
  align-items: center;
  padding: var(--sp-2) var(--sp-3);
  cursor: pointer;
  transition: background var(--motion) var(--ease);
}
.notif-bell__item:hover { background: var(--surface-2); }
.notif-bell__item.is-unread { background: color-mix(in srgb, var(--brand-soft) 30%, var(--surface)); }
.notif-bell__dot { width: 8px; height: 8px; border-radius: 50%; background: var(--text-faint); }
.notif-bell__dot.dot-info { background: var(--info, #38bdf8); }
.notif-bell__dot.dot-warning { background: var(--warn); }
.notif-bell__dot.dot-error { background: var(--fail); }
.notif-bell__dot.dot-success { background: var(--success); }
.notif-bell__item-main { min-width: 0; display: flex; flex-direction: column; gap: 2px; }
.notif-bell__item-title {
  font-size: var(--fs-sm);
  color: var(--text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.notif-bell__item-meta {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: var(--fs-xs);
  color: var(--text-faint);
}
.notif-bell__view-all {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
  padding: var(--sp-2);
  background: var(--surface-2);
  border: none;
  border-top: 1px solid var(--border);
  color: var(--brand-strong);
  font-size: var(--fs-sm);
  cursor: pointer;
  transition: background var(--motion) var(--ease);
}
.notif-bell__view-all:hover { background: var(--brand-soft); }

.bell-pop-enter-active, .bell-pop-leave-active { transition: opacity .15s var(--ease), transform .15s var(--ease); }
.bell-pop-enter-from, .bell-pop-leave-to { opacity: 0; transform: translateY(-4px); }
</style>