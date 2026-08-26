<template>
  <div class="notifications-view">
    <ListPageLayout
      :loading="loading"
      :error="error"
      :empty="!visibleNotifications.length"
      :filter-schema="filterSchema"
      :filters="filters"
      search-key="keyword"
      :search-placeholder="t('view.notifications.filterKeyword')"
      :results-label="`${visibleNotifications.length} / ${notifications.length}`"
      :error-title="t('view.notifications.loadError')"
      :empty-title="t('view.notifications.empty')"
      :empty-desc="t('view.notifications.emptyDesc')"
      :loading-lines="6"
    >
      <template #badges>
        <Badge tone="brand" icon="info">{{ t('view.notifications.enterprise') }}</Badge>
      </template>

      <template #actions>
        <button
          class="act-btn"
          :disabled="loading || !hasUnread"
          :title="t('view.notifications.markAllRead')"
          @click="markAllRead"
        >
          <AppIcon name="check-circle" :size="14" />
          {{ t('view.notifications.markAllRead') }}
        </button>
        <button class="act-btn" :disabled="loading" @click="openPreferences">
          <AppIcon name="gear" :size="14" />
          {{ t('view.notifications.preferences') }}
        </button>
        <button class="act-btn" :disabled="loading" :title="t('common.refresh')" @click="loadAll">
          <AppIcon name="refresh" :size="14" :class="{ spinning: loading }" aria-hidden="true" />
        </button>
      </template>

      <template #stats>
        <StatCard
          :label="t('view.notifications.statUnread')"
          :value="statUnread"
          icon="info"
          tone="brand"
          :loading="loading"
        />
        <StatCard
          :label="t('view.notifications.statToday')"
          :value="statToday"
          icon="activity"
          tone="info"
          :loading="loading"
        />
        <StatCard
          :label="t('view.notifications.statWarnings')"
          :value="statWarnings"
          icon="alert-triangle"
          tone="warn"
          :loading="loading"
        />
        <StatCard
          :label="t('view.notifications.statErrors')"
          :value="statErrors"
          icon="alert-triangle"
          tone="fail"
          :loading="loading"
        />
      </template>

      <template #content>
        <div class="notif-list">
          <div
            v-for="n in visibleNotifications"
            :key="n.id"
            class="notif-row"
            :class="{ 'is-unread': !n.read, ['lvl-' + n.level]: true }"
            @click="openDetail(n)"
          >
            <span class="notif-row__bar" :class="'bar-' + n.level" aria-hidden="true" />
            <span class="notif-row__icon" :class="'ic-' + n.level">
              <AppIcon :name="levelIcon(n.level)" :size="16" />
            </span>
            <div class="notif-row__main">
              <div class="notif-row__title-line">
                <span class="notif-row__title">{{ n.title }}</span>
                <Badge :tone="levelTone(n.level)">{{ t(levelLabelKey(n.level)) }}</Badge>
                <Badge tone="neutral">{{ t(categoryLabelKey(n.category)) }}</Badge>
              </div>
              <div class="notif-row__message">{{ n.message }}</div>
            </div>
            <span class="notif-row__time">{{ relativeTime(n.created_at) }}</span>
            <div class="notif-row__actions" @click.stop>
              <button
                v-if="!n.read"
                class="act-btn small"
                :title="t('view.notifications.markRead')"
                @click="markRead(n)"
              >
                <AppIcon name="check" :size="13" />
              </button>
              <button
                class="act-btn small danger"
                :title="t('view.notifications.delete')"
                @click="removeNotification(n)"
              >
                <AppIcon name="trash" :size="13" />
              </button>
            </div>
          </div>

          <div v-if="hasMore" class="notif-loadmore">
            <button class="act-btn" :disabled="loadingMore" @click="loadMore">
              <AppIcon name="chevrondown" :size="14" />
              {{ loadingMore ? t('common.loading') : t('view.notifications.loadMore') }}
            </button>
          </div>
          <div v-else-if="notifications.length" class="notif-no-more">
            {{ t('view.notifications.noMore') }}
          </div>
        </div>
      </template>
    </ListPageLayout>

    <!-- 详情面板 -->
    <DetailDrawer
      :open="detailOpen"
      :title="t('view.notifications.detailTitle')"
      icon="info"
      @close="detailOpen = false"
    >
      <div v-if="detailItem" class="detail-body">
        <div class="detail-row">
          <span class="detail-label">{{ t('view.notifications.detailLevel') }}</span>
          <Badge :tone="levelTone(detailItem.level)">{{ t(levelLabelKey(detailItem.level)) }}</Badge>
        </div>
        <div class="detail-row">
          <span class="detail-label">{{ t('view.notifications.detailCategory') }}</span>
          <Badge tone="neutral">{{ t(categoryLabelKey(detailItem.category)) }}</Badge>
        </div>
        <div class="detail-row">
          <span class="detail-label">{{ t('view.notifications.detailTime') }}</span>
          <span class="detail-value">{{ formatTime(detailItem.created_at) }}</span>
        </div>
        <div class="detail-row">
          <span class="detail-label">{{ t('view.notifications.detailStatus') }}</span>
          <span class="detail-value">
            {{ detailItem.read ? t('view.notifications.statusRead') : t('view.notifications.statusUnread') }}
          </span>
        </div>
        <h4 class="detail-section">{{ t('view.notifications.detailMessage') }}</h4>
        <p class="detail-message">{{ detailItem.message }}</p>
        <template v-if="detailItem.metadata && Object.keys(detailItem.metadata).length">
          <h4 class="detail-section">{{ t('view.notifications.detailMetadata') }}</h4>
          <pre class="detail-meta">{{ JSON.stringify(detailItem.metadata, null, 2) }}</pre>
        </template>
      </div>
      <template #footer>
        <button
          v-if="detailItem && !detailItem.read"
          class="act-btn"
          @click="markRead(detailItem)"
        >
          {{ t('view.notifications.markRead') }}
        </button>
        <button class="act-btn ghost" @click="detailOpen = false">
          {{ t('view.notifications.prefCancel') }}
        </button>
      </template>
    </DetailDrawer>

    <!-- 偏好设置 Modal -->
    <div
      v-if="prefOpen"
      v-modal-a11y
      class="modal-overlay"
      @click.self="prefOpen = false"
      @modal:escape="prefOpen = false"
    >
      <div class="modal">
        <h3>{{ t('view.notifications.preferencesTitle') }}</h3>
        <p class="modal-desc">{{ t('view.notifications.preferencesDesc') }}</p>
        <div v-if="prefError" class="modal-error">{{ prefError }}</div>
        <table class="pref-table">
          <thead>
            <tr>
              <th>{{ t('view.notifications.prefChannel') }}</th>
              <th>{{ t('view.notifications.prefEnabled') }}</th>
              <th>{{ t('view.notifications.prefLevels') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="ch in channels" :key="ch.key">
              <td>{{ t(channelLabelKey(ch.key)) }}</td>
              <td>
                <input
                  v-model="preferences[ch.key].enabled"
                  type="checkbox"
                  :aria-label="t('view.notifications.prefEnabled')"
                />
              </td>
              <td>
                <select v-model="preferences[ch.key].min_level" class="input">
                  <option value="info">{{ t('view.notifications.levelInfo') }}</option>
                  <option value="warning">{{ t('view.notifications.levelWarning') }}</option>
                  <option value="error">{{ t('view.notifications.levelError') }}</option>
                </select>
              </td>
            </tr>
          </tbody>
        </table>
        <div class="modal-actions">
          <button class="btn" @click="prefOpen = false">{{ t('view.notifications.prefCancel') }}</button>
          <button class="btn btn--primary" :disabled="prefSaving" @click="savePreferences">
            {{ t('view.notifications.prefSave') }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue';
import { useApiStore } from '../stores/api.js';
import { useToast } from '../composables/useToast.js';
import { useI18n } from '../i18n';
import ListPageLayout from '../components/ListPageLayout.vue';
import DetailDrawer from '../components/DetailDrawer.vue';
import AppIcon from '../components/AppIcon.vue';
import Badge from '../components/Badge.vue';
import StatCard from '../components/StatCard.vue';

const { t } = useI18n();
const api = useApiStore();
const toast = useToast();

// ── State ──
const notifications = ref([]);
const loading = ref(true);
const loadingMore = ref(false);
const error = ref('');
const hasMore = ref(false);
const PAGE_SIZE = 20;
const offset = ref(0);

const filters = reactive({
  level: '',
  category: '',
  read: '',
  keyword: '',
});

// ── Detail drawer ──
const detailOpen = ref(false);
const detailItem = ref(null);

// ── Preferences ──
const prefOpen = ref(false);
const prefSaving = ref(false);
const prefError = ref('');
const channels = ref([
  { key: 'email' },
  { key: 'webhook' },
  { key: 'in_app' },
  { key: 'sms' },
]);
const preferences = reactive({
  email: { enabled: true, min_level: 'info' },
  webhook: { enabled: false, min_level: 'warning' },
  in_app: { enabled: true, min_level: 'info' },
  sms: { enabled: false, min_level: 'error' },
});

// ── Filter schema for ListPageLayout ──
const filterSchema = computed(() => [
  {
    key: 'level',
    label: t('view.notifications.filterLevel'),
    options: [
      { value: 'info', label: t('view.notifications.levelInfo') },
      { value: 'warning', label: t('view.notifications.levelWarning') },
      { value: 'error', label: t('view.notifications.levelError') },
      { value: 'success', label: t('view.notifications.levelSuccess') },
    ],
  },
  {
    key: 'category',
    label: t('view.notifications.filterCategory'),
    options: [
      { value: 'system', label: t('view.notifications.catSystem') },
      { value: 'agent', label: t('view.notifications.catAgent') },
      { value: 'cost', label: t('view.notifications.catCost') },
      { value: 'security', label: t('view.notifications.catSecurity') },
      { value: 'tenant', label: t('view.notifications.catTenant') },
      { value: 'quota', label: t('view.notifications.catQuota') },
    ],
  },
  {
    key: 'read',
    label: t('view.notifications.filterRead'),
    options: [
      { value: 'unread', label: t('view.notifications.readUnread') },
      { value: 'read', label: t('view.notifications.readRead') },
    ],
  },
]);

// ── Filtered list (client-side filter on top of server-paged data) ──
const visibleNotifications = computed(() => {
  const fk = (filters.keyword || '').trim().toLowerCase();
  return notifications.value.filter((n) => {
    if (filters.level && n.level !== filters.level) return false;
    if (filters.category && n.category !== filters.category) return false;
    if (filters.read === 'unread' && n.read) return false;
    if (filters.read === 'read' && !n.read) return false;
    if (fk) {
      const hay = `${n.title || ''} ${n.message || ''}`.toLowerCase();
      if (!hay.includes(fk)) return false;
    }
    return true;
  });
});

// ── Stats ──
const statUnread = computed(() => notifications.value.filter((n) => !n.read).length);
const statToday = computed(() => {
  const now = Date.now();
  const start = now - 24 * 3600 * 1000;
  return notifications.value.filter((n) => toMs(n.created_at) >= start).length;
});
const statWarnings = computed(() => notifications.value.filter((n) => n.level === 'warning').length);
const statErrors = computed(() => notifications.value.filter((n) => n.level === 'error').length);
const hasUnread = computed(() => notifications.value.some((n) => !n.read));

function toMs(ts) {
  if (!ts) return 0;
  if (typeof ts === 'number') return ts > 1e12 ? ts : ts * 1000;
  const d = new Date(ts);
  return isNaN(d.getTime()) ? 0 : d.getTime();
}

// ── Level / category helpers ──
function levelIcon(lv) {
  if (lv === 'error') return 'alert-triangle';
  if (lv === 'warning') return 'alert-triangle';
  if (lv === 'success') return 'check-circle';
  return 'info';
}
function levelTone(lv) {
  if (lv === 'error') return 'fail';
  if (lv === 'warning') return 'warn';
  if (lv === 'success') return 'success';
  return 'info';
}
function levelLabelKey(lv) {
  if (lv === 'error') return 'view.notifications.levelError';
  if (lv === 'warning') return 'view.notifications.levelWarning';
  if (lv === 'success') return 'view.notifications.levelSuccess';
  return 'view.notifications.levelInfo';
}
function categoryLabelKey(cat) {
  const map = {
    system: 'view.notifications.catSystem',
    agent: 'view.notifications.catAgent',
    cost: 'view.notifications.catCost',
    security: 'view.notifications.catSecurity',
    tenant: 'view.notifications.catTenant',
    quota: 'view.notifications.catQuota',
  };
  return map[cat] || 'view.notifications.catSystem';
}
function channelLabelKey(key) {
  const map = {
    email: 'view.notifications.channelEmail',
    webhook: 'view.notifications.channelWebhook',
    in_app: 'view.notifications.channelInApp',
    sms: 'view.notifications.channelSms',
  };
  return map[key] || key;
}

// ── Time formatters ──
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
function formatTime(ts) {
  const ms = toMs(ts);
  if (!ms) return '';
  return new Date(ms).toLocaleString();
}

// ── Data loading ──
async function loadAll() {
  loading.value = true;
  error.value = '';
  offset.value = 0;
  try {
    const d = await api.get('/api/notifications/list', {
      limit: PAGE_SIZE,
      offset: 0,
    });
    notifications.value = normalizeList(d);
    hasMore.value = !!(d.has_more || (d.total && d.total > notifications.value.length));
  } catch (e) {
    error.value = e.message || t('view.notifications.notificationsUnavailable');
    notifications.value = [];
  } finally {
    loading.value = false;
  }
}

async function loadMore() {
  if (loadingMore.value || !hasMore.value) return;
  loadingMore.value = true;
  try {
    const next = offset.value + PAGE_SIZE;
    const d = await api.get('/api/notifications/list', {
      limit: PAGE_SIZE,
      offset: next,
    });
    const items = normalizeList(d);
    notifications.value.push(...items);
    offset.value = next;
    hasMore.value = !!(d.has_more || (d.total && d.total > notifications.value.length));
  } catch (e) {
    toast.error(e.message || t('view.notifications.loadMoreFailed'));
  } finally {
    loadingMore.value = false;
  }
}

function normalizeList(d) {
  const items = d.notifications || d.items || d.data || [];
  return items.map((n) => ({
    id: n.id,
    level: n.level || 'info',
    category: n.category || 'system',
    title: n.title || '',
    message: n.message || '',
    read: !!n.read,
    created_at: n.created_at || n.created_at_ms || 0,
    metadata: n.metadata || {},
  }));
}

// ── Actions ──
async function markRead(n) {
  try {
    await api.post(`/api/notifications/${n.id}/read`, {});
    n.read = true;
    toast.success(t('view.notifications.markedRead'));
  } catch (e) {
    toast.error(e.message || t('view.notifications.markReadFailed'));
  }
}

async function markAllRead() {
  if (!hasUnread.value) return;
  try {
    await api.post('/api/notifications/read-all', {});
    notifications.value.forEach((n) => { n.read = true; });
    toast.success(t('view.notifications.allMarkedRead'));
  } catch (e) {
    toast.error(e.message || t('view.notifications.markAllReadFailed'));
  }
}

async function removeNotification(n) {
  if (typeof window !== 'undefined' && !window.confirm(t('view.notifications.deleteConfirm'))) return;
  try {
    await api.delete(`/api/notifications/${n.id}`);
    notifications.value = notifications.value.filter((x) => x.id !== n.id);
    toast.success(t('view.notifications.deleted'));
  } catch (e) {
    toast.error(e.message || t('view.notifications.deleteFailed'));
  }
}

function openDetail(n) {
  detailItem.value = n;
  detailOpen.value = true;
}

// ── Preferences ──
async function openPreferences() {
  prefOpen.value = true;
  prefError.value = '';
  try {
    const d = await api.get('/api/notifications/preferences');
    const prefs = d.preferences || d || {};
    channels.value = (d.channels || prefs.channels || [
      { key: 'email' }, { key: 'webhook' }, { key: 'in_app' }, { key: 'sms' },
    ]).map((c) => ({ key: typeof c === 'string' ? c : c.key }));
    // Reset to defaults then merge server values
    for (const ch of channels.value) {
      const src = prefs[ch.key] || {};
      preferences[ch.key] = {
        enabled: src.enabled !== undefined ? !!src.enabled : true,
        min_level: src.min_level || 'info',
      };
    }
  } catch (e) {
    prefError.value = e.message || t('view.notifications.prefLoadError');
  }
}

async function savePreferences() {
  prefSaving.value = true;
  try {
    await api.put('/api/notifications/preferences', { preferences });
    toast.success(t('view.notifications.prefSaved'));
    prefOpen.value = false;
  } catch (e) {
    toast.error(e.message || t('view.notifications.saveFailed'));
  } finally {
    prefSaving.value = false;
  }
}

onMounted(loadAll);
</script>

<style scoped>
.notifications-view { display: flex; flex-direction: column; gap: var(--sp-4); }

/* ── Action buttons (shared inline style, mirrors Audit.vue) ── */
.act-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 10px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r-md);
  color: var(--text);
  font-size: var(--fs-sm);
  cursor: pointer;
  transition: border-color var(--motion) var(--ease), background var(--motion) var(--ease);
}
.act-btn:hover:not(:disabled) { border-color: var(--border-strong); background: var(--surface-2); }
.act-btn:disabled { opacity: .55; cursor: not-allowed; }
.act-btn.small { padding: 4px 6px; }
.act-btn.ghost { background: transparent; }
.act-btn.danger { color: var(--fail); }
.act-btn.danger:hover:not(:disabled) { border-color: var(--fail); background: var(--fail-soft); }
.spinning { animation: spin 1s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }

/* ── Notification list ── */
.notif-list { display: flex; flex-direction: column; gap: var(--sp-2); }
.notif-row {
  display: grid;
  grid-template-columns: 4px 36px 1fr auto auto;
  gap: var(--sp-3);
  align-items: center;
  padding: var(--sp-3);
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r-md);
  cursor: pointer;
  transition: border-color var(--motion) var(--ease), background var(--motion) var(--ease);
}
.notif-row:hover { border-color: var(--border-strong); background: var(--surface-2); }
.notif-row.is-unread { background: color-mix(in srgb, var(--brand-soft) 35%, var(--surface)); }
.notif-row__bar { width: 4px; height: 32px; border-radius: 2px; background: var(--border); }
.notif-row__bar.bar-info { background: var(--info, #4cc2ff); }
.notif-row__bar.bar-warning { background: var(--warn); }
.notif-row__bar.bar-error { background: var(--fail); }
.notif-row__bar.bar-success { background: var(--success); }
.notif-row__icon {
  display: grid; place-items: center;
  width: 36px; height: 36px;
  border-radius: var(--r-md);
  background: var(--surface-2);
  color: var(--text-muted);
}
.notif-row__icon.ic-info { color: var(--info, #4cc2ff); }
.notif-row__icon.ic-warning { color: var(--warn); }
.notif-row__icon.ic-error { color: var(--fail); }
.notif-row__icon.ic-success { color: var(--success); }
.notif-row__main { min-width: 0; display: flex; flex-direction: column; gap: 4px; }
.notif-row__title-line { display: flex; align-items: center; gap: var(--sp-2); flex-wrap: wrap; }
.notif-row__title { font-weight: 600; color: var(--text); }
.notif-row__message {
  font-size: var(--fs-sm);
  color: var(--text-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.notif-row__time { font-size: var(--fs-xs); color: var(--text-faint); white-space: nowrap; }
.notif-row__actions { display: inline-flex; gap: 4px; }

.notif-loadmore { display: flex; justify-content: center; padding: var(--sp-3); }
.notif-no-more { text-align: center; font-size: var(--fs-xs); color: var(--text-faint); padding: var(--sp-2); }

/* ── Detail drawer body ── */
.detail-body { display: flex; flex-direction: column; gap: var(--sp-2); }
.detail-row { display: flex; align-items: center; gap: var(--sp-3); }
.detail-label { font-size: var(--fs-sm); color: var(--text-muted); min-width: 80px; }
.detail-value { font-size: var(--fs-sm); color: var(--text); }
.detail-section { margin: var(--sp-3) 0 var(--sp-1); font-size: var(--fs-sm); color: var(--text); }
.detail-message { margin: 0; color: var(--text); line-height: 1.55; white-space: pre-wrap; }
.detail-meta {
  margin: 0;
  padding: var(--sp-2);
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: var(--r-sm);
  font-size: var(--fs-xs);
  color: var(--text-muted);
  overflow-x: auto;
}

/* ── Preferences modal ── */
.modal-overlay {
  position: fixed; inset: 0;
  background: var(--overlay-scrim);
  display: flex; align-items: center; justify-content: center;
  z-index: calc(var(--z-modal, 90) + 5);
}
.modal {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r-lg);
  padding: var(--sp-4);
  width: min(560px, 92vw);
  max-height: 88vh;
  overflow-y: auto;
  box-shadow: var(--shadow-lg);
}
.modal h3 { margin: 0 0 var(--sp-1); font-size: var(--fs-md); }
.modal-desc { margin: 0 0 var(--sp-3); color: var(--text-muted); font-size: var(--fs-sm); }
.modal-error { color: var(--fail); font-size: var(--fs-sm); margin-bottom: var(--sp-2); }
.pref-table { width: 100%; border-collapse: collapse; margin-bottom: var(--sp-3); }
.pref-table th, .pref-table td {
  border: 1px solid var(--border);
  padding: var(--sp-2) var(--sp-3);
  text-align: left;
  font-size: var(--fs-sm);
}
.pref-table th { background: var(--surface-2); color: var(--text-muted); font-weight: 600; }
.input {
  width: 100%;
  padding: 6px 8px;
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: var(--r-sm);
  color: var(--text);
  font-size: var(--fs-sm);
  font-family: inherit;
}
.modal-actions { display: flex; justify-content: flex-end; gap: var(--sp-2); }
.btn {
  padding: 6px 14px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r-md);
  color: var(--text);
  cursor: pointer;
  font-size: var(--fs-sm);
}
.btn--primary { background: var(--brand); color: var(--brand-contrast); border-color: var(--brand); }
.btn:disabled { opacity: .55; cursor: not-allowed; }

@media (max-width: 700px) {
  .notif-row { grid-template-columns: 4px 36px 1fr auto; }
  .notif-row__time { display: none; }
}
</style>