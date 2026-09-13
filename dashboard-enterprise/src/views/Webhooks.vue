<template>
  <div class="webhooks-page">
    <ListPageLayout
      :loading="loading"
      :error="error"
      :empty="!hooks.length"
      :error-title="t('view.webhooks.loadError')"
      :empty-title="t('view.webhooks.empty')"
      :empty-desc="t('view.webhooks.emptyDesc')"
      :loading-lines="6"
    >
      <template #badges>
        <Badge tone="brand">{{ t('view.webhooks.enterprise') }}</Badge>
      </template>
      <template #actions>
        <button class="btn btn--primary" @click="openCreate">
          <AppIcon name="plus" :size="15" /> {{ t('view.webhooks.createBtn') }}
        </button>
        <button class="btn" :disabled="loading" :title="t('view.webhooks.refreshBtn')" @click="load">
          <AppIcon name="refresh" :size="14" :class="{ spinning: loading }" aria-hidden="true" />
        </button>
      </template>

      <template #content>
        <p v-if="hooks.length" class="wh-hint">{{ t('view.webhooks.hint') }}</p>
        <div class="wh-table" role="table" :aria-label="t('view.webhooks.subtitle')">
          <div class="wh-row wh-row--head" role="row">
            <div class="wh-cell wh-cell--name" role="columnheader">{{ t('view.webhooks.colName') }}</div>
            <div class="wh-cell wh-cell--url" role="columnheader">{{ t('view.webhooks.colUrl') }}</div>
            <div class="wh-cell wh-cell--events" role="columnheader">{{ t('view.webhooks.colEvents') }}</div>
            <div class="wh-cell wh-cell--status" role="columnheader">{{ t('view.webhooks.colStatus') }}</div>
            <div class="wh-cell wh-cell--last" role="columnheader">{{ t('view.webhooks.colLastTrigger') }}</div>
            <div class="wh-cell wh-cell--actions" role="columnheader">{{ t('view.webhooks.colActions') }}</div>
          </div>
          <div v-for="h in hooks" :key="h.id" class="wh-row" role="row">
            <div class="wh-cell wh-cell--name" role="cell">
              <span class="wh-name">{{ h.name }}</span>
            </div>
            <div class="wh-cell wh-cell--url" role="cell">
              <code class="wh-mono wh-url">{{ h.url }}</code>
            </div>
            <div class="wh-cell wh-cell--events" role="cell">
              <template v-if="(h.events || []).length">
                <Badge v-for="e in (h.events || []).slice(0, 2)" :key="e" tone="neutral">{{ eventLabel(e) }}</Badge>
                <span v-if="(h.events || []).length > 2" class="wh-more">+{{ h.events.length - 2 }}</span>
              </template>
              <span v-else class="wh-muted">—</span>
            </div>
            <div class="wh-cell wh-cell--status" role="cell">
              <Badge :tone="h.enabled ? 'success' : 'neutral'">
                {{ h.enabled ? t('view.webhooks.statusEnabled') : t('view.webhooks.statusDisabled') }}
              </Badge>
            </div>
            <div class="wh-cell wh-cell--last" role="cell">
              <span class="wh-time">{{ h.last_triggered_at ? formatRel(h.last_triggered_at) : t('view.webhooks.never') }}</span>
            </div>
            <div class="wh-cell wh-cell--actions" role="cell">
              <button class="btn-icon" type="button" :title="t('view.webhooks.actionTest')" :aria-label="t('view.webhooks.actionTest')" :disabled="testingId === h.id" @click="testHook(h)">
                <AppIcon name="zap" :size="14" aria-hidden="true" />
              </button>
              <button class="btn-icon" type="button" :title="t('view.webhooks.actionHistory')" :aria-label="t('view.webhooks.actionHistory')" @click="openHistory(h)">
                <AppIcon name="clock" :size="14" aria-hidden="true" />
              </button>
              <button class="btn-icon" type="button" :title="t('view.webhooks.actionEdit')" :aria-label="t('view.webhooks.actionEdit')" @click="openEdit(h)">
                <AppIcon name="gear" :size="14" aria-hidden="true" />
              </button>
              <button
                class="btn-icon btn-icon--danger"
                type="button"
                :title="t('view.webhooks.actionDelete')"
                :aria-label="t('view.webhooks.actionDelete')"
                @click="removeHook(h)"
              >
                <AppIcon name="trash" :size="14" aria-hidden="true" />
              </button>
            </div>
          </div>
        </div>
      </template>
    </ListPageLayout>

    <!-- 创建/编辑对话框 -->
    <div v-if="showForm" v-modal-a11y class="modal-overlay" @click.self="closeForm" @modal:escape="closeForm">
      <div class="modal" role="dialog" aria-modal="true">
        <button class="modal-close" type="button" :aria-label="t('common.close')" @click="closeForm">
          <AppIcon name="x" :size="16" aria-hidden="true" />
        </button>
        <h3>{{ editingId ? t('view.webhooks.dialogTitleEdit') : t('view.webhooks.dialogTitleCreate') }}</h3>
        <div class="form">
          <label class="form-label">
            <span>{{ t('view.webhooks.fieldName') }}</span>
            <input v-model="form.name" class="input" type="text" :placeholder="t('view.webhooks.fieldName')" />
          </label>
          <label class="form-label">
            <span>{{ t('view.webhooks.fieldUrl') }}</span>
            <input v-model="form.url" class="input" type="text" :placeholder="t('view.webhooks.placeholderUrl')" />
          </label>
          <div class="form-label">
            <span>{{ t('view.webhooks.fieldEvents') }}</span>
            <div class="event-chips">
              <label v-for="e in EVENT_TYPES" :key="e" class="event-chip">
                <input v-model="form.events" type="checkbox" :value="e" />
                <span>{{ eventLabel(e) }}</span>
              </label>
            </div>
          </div>
          <label class="form-label">
            <span>{{ t('view.webhooks.fieldAuth') }}</span>
            <select v-model="form.auth_type" class="input">
              <option value="none">{{ t('view.webhooks.authNone') }}</option>
              <option value="hmac">{{ t('view.webhooks.authHmac') }}</option>
              <option value="bearer">{{ t('view.webhooks.authBearer') }}</option>
            </select>
          </label>
          <label v-if="form.auth_type === 'hmac'" class="form-label">
            <span>{{ t('view.webhooks.fieldAuthSecret') }}</span>
            <input v-model="form.auth_secret" class="input" type="password" :placeholder="t('view.webhooks.placeholderSecret')" />
          </label>
          <label v-if="form.auth_type === 'bearer'" class="form-label">
            <span>{{ t('view.webhooks.fieldBearerToken') }}</span>
            <input v-model="form.auth_token" class="input" type="password" :placeholder="t('view.webhooks.placeholderToken')" />
          </label>
          <div class="form-row">
            <label class="form-label">
              <span>{{ t('view.webhooks.fieldTimeout') }}</span>
              <input v-model.number="form.timeout_s" class="input" type="number" min="1" placeholder="30" />
            </label>
            <label class="form-label">
              <span>{{ t('view.webhooks.fieldRetry') }}</span>
              <input v-model.number="form.retry_count" class="input" type="number" min="0" placeholder="3" />
            </label>
          </div>
          <label class="form-label form-label--inline">
            <input v-model="form.enabled" type="checkbox" />
            <span>{{ t('view.webhooks.fieldEnabled') }}</span>
          </label>
        </div>
        <div class="modal-actions">
          <button class="btn" type="button" @click="closeForm">{{ t('view.webhooks.btnCancel') }}</button>
          <button class="btn btn--primary" type="button" :disabled="saving" @click="save">
            {{ saving ? t('view.webhooks.saving') : t('view.webhooks.btnSave') }}
          </button>
        </div>
      </div>
    </div>

    <!-- 触发历史详情面板 -->
    <DetailDrawer :open="showHistory" :title="t('view.webhooks.historyTitle')" icon="clock" @close="closeHistory">
      <div v-if="historyHook" class="detail-content">
        <section class="detail-section">
          <h4 class="detail-section__title">{{ t('view.webhooks.colName') }}</h4>
          <p class="detail-value">{{ historyHook.name }}</p>
          <h4 class="detail-section__title">{{ t('view.webhooks.colUrl') }}</h4>
          <p class="detail-value"><code class="wh-mono">{{ historyHook.url }}</code></p>
        </section>
        <section class="detail-section">
          <h4 class="detail-section__title">{{ t('view.webhooks.historyTitle') }}</h4>
          <div v-if="historyLoading" class="wh-muted">{{ t('view.webhooks.loading') }}</div>
          <div v-else-if="historyRecords.length">
            <DataTable
              :columns="historyCols"
              :rows="historyRecords"
              row-key="id"
              :empty-text="t('view.webhooks.historyEmpty')"
              compact
            />
          </div>
          <p v-else class="wh-muted">{{ t('view.webhooks.historyEmpty') }}</p>
        </section>
      </div>
    </DetailDrawer>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue';
import { useApiStore } from '../stores/api.js';
import { useToast } from '../composables/useToast.js';
import { useConfirm } from '../composables/useConfirm.js';
import { useI18n } from '../i18n';
import Badge from '../components/Badge.vue';
import ListPageLayout from '../components/ListPageLayout.vue';
import DetailDrawer from '../components/DetailDrawer.vue';
import DataTable from '../components/DataTable.vue';
import AppIcon from '../components/AppIcon.vue';

const { t } = useI18n();
const api = useApiStore();
const toast = useToast();
const { showConfirm } = useConfirm();

// 可订阅的事件类型
const EVENT_TYPES = [
  'agent.dispatch',
  'task.complete',
  'alert.triggered',
  'cost.threshold',
  'system.health',
  'audit.event',
];

// ── 列表状态 ──
const hooks = ref([]);
const loading = ref(true);
const error = ref('');

// ── 创建/编辑对话框 ──
const showForm = ref(false);
const saving = ref(false);
const editingId = ref('');
const form = ref(defaultForm());

// ── 测试中状态 ──
const testingId = ref('');

// ── 触发历史 ──
const showHistory = ref(false);
const historyHook = ref(null);
const historyLoading = ref(false);
const historyRecords = ref([]);

function defaultForm() {
  return {
    name: '',
    url: '',
    events: [],
    auth_type: 'none',
    auth_secret: '',
    auth_token: '',
    timeout_s: 30,
    retry_count: 3,
    enabled: true,
  };
}

const historyCols = computed(() => [
  { key: 'time', label: t('view.webhooks.historyColTime'), type: 'time' },
  { key: 'event', label: t('view.webhooks.historyColEvent') },
  { key: 'status', label: t('view.webhooks.historyColStatus'), type: 'badge' },
  { key: 'latency_ms', label: t('view.webhooks.historyColLatency'), type: 'num' },
  { key: 'response_code', label: t('view.webhooks.historyColResponse'), type: 'num' },
]);

// ── 工具函数 ──
function eventLabel(e) {
  const map = {
    'agent.dispatch': t('view.webhooks.eventAgentDispatch'),
    'task.complete': t('view.webhooks.eventTaskComplete'),
    'alert.triggered': t('view.webhooks.eventAlertTriggered'),
    'cost.threshold': t('view.webhooks.eventCostThreshold'),
    'system.health': t('view.webhooks.eventSystemHealth'),
    'audit.event': t('view.webhooks.eventAuditEvent'),
  };
  return map[e] || e;
}

function formatRel(ts) {
  if (ts === null || ts === undefined || ts === '') return '—';
  const d = new Date(typeof ts === 'number' ? ts : String(ts));
  if (isNaN(d.getTime())) return String(ts);
  const diff = Math.max(0, Math.floor((Date.now() - d.getTime()) / 1000));
  if (diff < 60) return t('common.secondsAgo', { n: diff });
  if (diff < 3600) return t('common.minutesAgo', { n: Math.floor(diff / 60) });
  if (diff < 86400) return t('common.hoursAgo', { n: Math.floor(diff / 3600) });
  return t('common.daysAgo', { n: Math.floor(diff / 86400) });
}

// ── 数据加载 ──
async function load() {
  loading.value = true;
  error.value = '';
  try {
    const d = await api.get('/api/hooks');
    hooks.value = normalizeList(d);
  } catch (e) {
    error.value = e.message || String(e);
    hooks.value = [];
  } finally {
    loading.value = false;
  }
}

function normalizeList(d) {
  const items = Array.isArray(d) ? d : (d.items || d.webhooks || d.data || []);
  return items.map((h) => ({
    id: h.id,
    name: h.name || '',
    url: h.url || '',
    events: h.events || [],
    auth_type: h.auth_type || 'none',
    enabled: h.enabled !== false,
    timeout_s: h.timeout_s || 30,
    retry_count: h.retry_count || 0,
    last_triggered_at: h.last_triggered_at || h.last_triggered || null,
  }));
}

// ── 创建/编辑 ──
function openCreate() {
  editingId.value = '';
  form.value = defaultForm();
  showForm.value = true;
}

function openEdit(h) {
  editingId.value = h.id;
  form.value = {
    name: h.name || '',
    url: h.url || '',
    events: [...(h.events || [])],
    auth_type: h.auth_type || 'none',
    auth_secret: '',
    auth_token: '',
    timeout_s: h.timeout_s || 30,
    retry_count: h.retry_count || 0,
    enabled: h.enabled !== false,
  };
  showForm.value = true;
}

function closeForm() {
  showForm.value = false;
}

function validate() {
  if (!form.value.name.trim()) {
    toast.warn(t('view.webhooks.validateNameRequired'));
    return false;
  }
  if (!form.value.url.trim()) {
    toast.warn(t('view.webhooks.validateUrlRequired'));
    return false;
  }
  if (!/^https?:\/\//i.test(form.value.url.trim())) {
    toast.warn(t('view.webhooks.validateUrlInvalid'));
    return false;
  }
  if (!form.value.events.length) {
    toast.warn(t('view.webhooks.validateEventsRequired'));
    return false;
  }
  if (form.value.auth_type === 'hmac' && !form.value.auth_secret.trim()) {
    toast.warn(t('view.webhooks.validateSecretRequired'));
    return false;
  }
  if (form.value.auth_type === 'bearer' && !form.value.auth_token.trim()) {
    toast.warn(t('view.webhooks.validateTokenRequired'));
    return false;
  }
  return true;
}

async function save() {
  if (!validate()) return;
  saving.value = true;
  try {
    const payload = {
      name: form.value.name.trim(),
      url: form.value.url.trim(),
      events: form.value.events,
      auth_type: form.value.auth_type,
      timeout_s: Number(form.value.timeout_s) || 30,
      retry_count: Number(form.value.retry_count) || 0,
      enabled: form.value.enabled,
    };
    if (form.value.auth_type === 'hmac') payload.auth_secret = form.value.auth_secret;
    if (form.value.auth_type === 'bearer') payload.auth_token = form.value.auth_token;

    if (editingId.value) {
      await api.put(`/api/hooks/${encodeURIComponent(editingId.value)}`, payload);
    } else {
      await api.post('/api/hooks', payload);
    }
    toast.success(t('view.webhooks.saved'));
    showForm.value = false;
    await load();
  } catch (e) {
    toast.error(e.message || t('view.webhooks.saveError'));
  } finally {
    saving.value = false;
  }
}

// ── 删除 ──
async function removeHook(h) {
  const ok = await showConfirm({ message: t('view.webhooks.deleteConfirm', { name: h.name }), tone: 'danger' });
  if (!ok) return;
  try {
    await api.delete(`/api/hooks/${encodeURIComponent(h.id)}`);
    toast.success(t('view.webhooks.deleted'));
    await load();
  } catch (e) {
    toast.error(e.message || t('view.webhooks.deleteError'));
  }
}

// ── 测试 ──
async function testHook(h) {
  testingId.value = h.id;
  try {
    const d = await api.post(`/api/hooks/${encodeURIComponent(h.id)}/test`, {});
    if (d && d.success) {
      toast.success(t('view.webhooks.testSuccess', { ms: d.latency_ms ?? d.latency ?? 0 }));
    } else if (d && d.error) {
      toast.error(t('view.webhooks.testFailed', { error: d.error }));
    } else {
      toast.info(t('view.webhooks.testNoListener'));
    }
  } catch (e) {
    toast.error(e.message || t('view.webhooks.testError'));
  } finally {
    testingId.value = '';
  }
}

// ── 触发历史 ──
async function openHistory(h) {
  historyHook.value = h;
  showHistory.value = true;
  historyLoading.value = true;
  historyRecords.value = [];
  try {
    const d = await api.get(`/api/hooks/${encodeURIComponent(h.id)}/history`);
    const items = Array.isArray(d) ? d : (d.items || d.history || d.records || []);
    historyRecords.value = items.map((r) => ({
      id: r.id || r.ts || Math.random(),
      time: r.time || r.triggered_at || r.ts,
      event: r.event || '',
      status: deliveryStatus(r),
      latency_ms: r.latency_ms ?? r.latency ?? 0,
      response_code: r.response_code ?? r.status_code ?? r.code ?? 0,
    }));
  } catch (e) {
    toast.error(e.message || t('view.webhooks.historyError'));
  } finally {
    historyLoading.value = false;
  }
}

function deliveryStatus(r) {
  const code = r.response_code ?? r.status_code ?? r.code;
  if (r.success === true || (code && code >= 200 && code < 300)) return t('view.webhooks.deliverySuccess');
  if (r.retrying || r.status === 'retry') return t('view.webhooks.deliveryRetry');
  return t('view.webhooks.deliveryFailed');
}

function closeHistory() {
  showHistory.value = false;
  historyHook.value = null;
  historyRecords.value = [];
}

onMounted(load);
</script>

<style scoped>
.webhooks-page { display: flex; flex-direction: column; }

/* ── 操作按钮 ── */
.btn {
  display: inline-flex; align-items: center; gap: 5px;
  background: var(--surface-2); color: var(--text); border: 1px solid var(--border);
  border-radius: var(--r-md); padding: 7px var(--sp-3); font-size: var(--fs-sm); font-weight: 600;
  cursor: pointer; transition: opacity var(--motion) var(--ease);
}
.btn:hover { opacity: .9; }
.btn--primary {
  background: var(--brand); color: var(--brand-contrast); border: none;
}
.btn--primary:disabled { opacity: .5; cursor: not-allowed; }
.btn:disabled { opacity: .5; cursor: not-allowed; }
.btn-icon {
  display: grid; place-items: center;
  width: 28px; height: 28px;
  background: var(--surface-2); border: 1px solid var(--border);
  border-radius: var(--r-sm); color: var(--text-muted); cursor: pointer;
  transition: color var(--motion) var(--ease), border-color var(--motion) var(--ease);
}
.btn-icon:hover { color: var(--text); border-color: var(--border-strong); }
.btn-icon:disabled { opacity: .5; cursor: not-allowed; }
.btn-icon--danger:hover { color: var(--fail); border-color: var(--fail); }
.spinning { animation: maop-spin 1s linear infinite; }
@keyframes maop-spin { to { transform: rotate(360deg); } }

/* ── 提示 ── */
.wh-hint {
  font-size: var(--fs-sm); color: var(--text-muted); margin: 0 0 var(--sp-3);
  line-height: 1.5;
}

/* ── 列表表格 ── */
.wh-table {
  background: var(--surface); border: 1px solid var(--border); border-radius: var(--r-lg);
  overflow: hidden;
}
.wh-row {
  display: grid;
  grid-template-columns: 1.2fr 1.8fr 1.4fr 0.8fr 1fr 150px;
  align-items: center;
  padding: 10px 14px;
  border-bottom: 1px solid var(--border);
  font-size: var(--fs-base);
}
.wh-row:last-child { border-bottom: none; }
.wh-row--head {
  background: var(--surface-2);
  font-size: var(--fs-xs); font-weight: 700; color: var(--text-faint);
  text-transform: uppercase; letter-spacing: .05em;
}
.wh-cell { padding: 0 var(--sp-1); }
.wh-cell--events { display: flex; align-items: center; gap: var(--sp-1); flex-wrap: wrap; }
.wh-cell--actions { display: flex; gap: 6px; justify-content: flex-end; }
.wh-name { font-weight: 600; color: var(--text); }
.wh-mono { font-family: var(--font-mono); font-size: var(--fs-sm); color: var(--text); background: var(--surface-2); padding: 1px 6px; border-radius: var(--r-sm); }
.wh-url { display: inline-block; max-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.wh-more { font-size: var(--fs-xs); color: var(--text-muted); }
.wh-muted { color: var(--text-muted); font-size: var(--fs-sm); }
.wh-time { color: var(--text-muted); white-space: nowrap; font-size: var(--fs-sm); }

/* ── 模态 ── */
.modal-overlay {
  position: fixed; inset: 0; background: var(--overlay-scrim);
  display: flex; align-items: center; justify-content: center;
  z-index: var(--z-modal);
}
.modal {
  position: relative;
  background: var(--surface); border: 1px solid var(--border);
  border-radius: var(--r-lg); padding: var(--sp-6);
  width: calc(100% - 32px); max-width: 520px; max-height: 88vh; overflow-y: auto;
  box-shadow: var(--shadow-lg);
}
.modal-close {
  position: absolute; top: 12px; right: 12px;
  display: grid; place-items: center;
  width: 28px; height: 28px;
  background: transparent; border: none; border-radius: var(--r-sm);
  color: var(--text-muted); cursor: pointer;
  transition: color var(--motion) var(--ease), background var(--motion) var(--ease);
}
.modal-close:hover { color: var(--text); background: var(--surface-2); }
.modal h3 { margin: 0 0 var(--sp-4); font-size: var(--fs-lg); color: var(--text); }
.modal-actions { display: flex; justify-content: flex-end; gap: var(--sp-2); margin-top: var(--sp-4); }

/* ── 表单 ── */
.form { display: flex; flex-direction: column; gap: var(--sp-3); }
.form-label { display: flex; flex-direction: column; gap: var(--sp-1); font-size: var(--fs-sm); font-weight: 600; color: var(--text-muted); }
.form-label--inline { flex-direction: row; align-items: center; gap: 6px; }
.form-row { display: grid; grid-template-columns: 1fr 1fr; gap: var(--sp-3); }
.input {
  font-family: inherit; font-size: var(--fs-base);
  background: var(--surface-2); color: var(--text);
  border: 1px solid var(--border); border-radius: var(--r-md);
  padding: 7px 10px; width: 100%;
  transition: border-color var(--motion) var(--ease);
}
.input:focus { outline: none; border-color: var(--brand); }

/* ── 事件选择 ── */
.event-chips { display: flex; flex-wrap: wrap; gap: 6px; }
.event-chip {
  display: inline-flex; align-items: center; gap: var(--sp-1);
  padding: var(--sp-1) var(--sp-2);
  background: var(--surface-2); border: 1px solid var(--border);
  border-radius: var(--r-sm); font-size: var(--fs-xs); cursor: pointer; color: var(--text);
  font-weight: 400;
}
.event-chip input { margin: 0; }

/* ── 详情面板 ── */
.detail-content { display: flex; flex-direction: column; gap: var(--sp-4); }
.detail-section { display: flex; flex-direction: column; gap: 6px; }
.detail-section__title { font-size: var(--fs-sm); font-weight: 700; color: var(--text); margin: var(--sp-2) 0 var(--sp-1); text-transform: uppercase; letter-spacing: .04em; }
.detail-value { font-size: var(--fs-base); color: var(--text); margin: 0 0 var(--sp-2); word-break: break-all; }

@media (max-width: 900px) {
  .wh-row { grid-template-columns: 1fr 1fr 0.8fr 120px; }
  .wh-cell--events, .wh-cell--last { display: none; }
  .form-row { grid-template-columns: 1fr; }
}
@media (max-width: 640px) {
  .wh-row { grid-template-columns: 1fr 100px; }
  .wh-cell--status, .wh-cell--url { display: none; }
}
</style>
