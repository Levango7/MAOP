<template>
  <div class="licenses-view">
    <ListPageLayout
      :filters="filters"
      :loading="loading"
      :error="error"
      :empty="!visibleRows.length"
      :filter-schema="filterSchema"
      search-key="query"
      :search-placeholder="t('view.licenses.searchPlaceholder')"
      :results-label="`${visibleRows.length} / ${tableRows.length}`"
      :error-title="t('view.licenses.loadError')"
      :empty-title="t('view.licenses.noLicenses')"
      :empty-desc="t('view.licenses.noLicensesDesc')"
    >
      <template #badges>
        <Badge tone="brand" icon="shield">{{ t('view.licenses.enterprise') }}</Badge>
      </template>
      <template #actions>
        <button class="lic-btn lic-btn--primary" @click="openGenerate">
          <AppIcon name="plus" :size="15" /> {{ t('view.licenses.generate') }}
        </button>
      </template>

      <template #stats>
        <StatCard
          :label="t('view.licenses.totalLicenses')"
          :value="licenses.length"
          icon="scroll"
          tone="brand"
          :loading="loading"
        />
        <StatCard
          :label="t('view.licenses.activeLicenses')"
          :value="activeCount"
          icon="check-circle"
          tone="success"
          :loading="loading"
        />
        <StatCard
          :label="t('view.licenses.expiringSoon')"
          :value="expiringSoonCount"
          icon="clock"
          tone="warn"
          :loading="loading"
        />
      </template>

      <template #content>
        <DataTable
          :columns="cols"
          :rows="visibleRows"
          :loading="false"
          row-key="license_id"
          :empty-text="t('view.licenses.noLicenses')"
          clickable
          @row-click="openDetail"
        />
      </template>
    </ListPageLayout>

    <!-- 生成 License 对话框 -->
    <div
      v-if="showGenerate"
      v-modal-a11y
      class="lic-overlay"
      @click.self="showGenerate = false"
      @modal:escape="showGenerate = false"
    >
      <div class="lic-dialog" role="document">
        <button class="lic-dialog__close" type="button" :aria-label="t('common.close')" @click="showGenerate = false">
          <AppIcon name="x" :size="16" aria-hidden="true" />
        </button>
        <h3>{{ t('view.licenses.generate') }}</h3>

        <fieldset class="lic-fieldset">
          <legend>{{ t('view.licenses.customerInfo') }}</legend>
          <label class="lic-field">
            <span>{{ t('view.licenses.customerName') }}</span>
            <input v-model="form.customer_name" class="lic-input" placeholder="Acme Corporation" />
          </label>
          <label class="lic-field">
            <span>{{ t('view.licenses.customerEmail') }}</span>
            <input v-model="form.customer_email" class="lic-input" type="email" placeholder="admin@acme.com" />
          </label>
        </fieldset>

        <label class="lic-field">
          <span>{{ t('view.licenses.version') }}</span>
          <select v-model="form.version" class="lic-input">
            <option value="personal">{{ t('view.licenses.versionPersonal') }}</option>
            <option value="team">{{ t('view.licenses.versionTeam') }}</option>
            <option value="enterprise">{{ t('view.licenses.versionEnterprise') }}</option>
          </select>
        </label>

        <fieldset class="lic-fieldset">
          <legend>{{ t('view.licenses.quotaSettings') }}</legend>
          <div class="lic-quota-row">
            <label class="lic-field">
              <span>{{ t('view.licenses.maxAgents') }}</span>
              <input v-model.number="form.max_agents" class="lic-input" type="number" min="1" />
            </label>
            <label class="lic-field">
              <span>{{ t('view.licenses.maxUsers') }}</span>
              <input v-model.number="form.max_users" class="lic-input" type="number" min="1" />
            </label>
          </div>
        </fieldset>

        <fieldset class="lic-fieldset">
          <legend>{{ t('view.licenses.validPeriod') }}</legend>
          <label class="lic-field">
            <span>{{ t('view.licenses.validDays') }}</span>
            <input v-model.number="form.valid_days" class="lic-input" type="number" min="1" />
          </label>
        </fieldset>

        <p v-if="formError" class="lic-form-error">{{ formError }}</p>

        <div class="lic-dialog-actions">
          <button class="lic-btn" @click="showGenerate = false">{{ t('common.cancel') }}</button>
          <button class="lic-btn lic-btn--primary" :disabled="saving" @click="generateLicense">
            {{ saving ? t('view.licenses.generating') : t('common.save') }}
          </button>
        </div>
      </div>
    </div>

    <!-- 续期对话框 -->
    <div
      v-if="showRenew"
      v-modal-a11y
      class="lic-overlay"
      @click.self="showRenew = false"
      @modal:escape="showRenew = false"
    >
      <div class="lic-dialog lic-dialog--sm" role="document">
        <button class="lic-dialog__close" type="button" :aria-label="t('common.close')" @click="showRenew = false">
          <AppIcon name="x" :size="16" aria-hidden="true" />
        </button>
        <h3>{{ t('view.licenses.renew') }}</h3>
        <label class="lic-field">
          <span>{{ t('view.licenses.validDays') }}</span>
          <input v-model.number="renewDays" class="lic-input" type="number" min="1" />
        </label>
        <div class="lic-dialog-actions">
          <button class="lic-btn" @click="showRenew = false">{{ t('common.cancel') }}</button>
          <button class="lic-btn lic-btn--primary" :disabled="saving" @click="renewLicense">
            {{ saving ? t('view.licenses.renewing') : t('view.licenses.renew') }}
          </button>
        </div>
      </div>
    </div>

    <!-- 详情面板 -->
    <DetailDrawer
      :open="showDetail"
      :title="t('view.licenses.detailTitle')"
      icon="shield"
      @close="closeDetail"
    >
      <div v-if="detail" class="lic-detail">
        <section class="lic-detail-section">
          <h4>{{ t('view.licenses.detailInfo') }}</h4>
          <dl class="lic-dl">
            <dt>{{ t('view.licenses.licenseId') }}</dt>
            <dd class="lic-mono">{{ detail.license_id }}</dd>
            <dt>{{ t('view.licenses.customerName') }}</dt>
            <dd>{{ detail.customer_name || '—' }}</dd>
            <dt>{{ t('view.licenses.customerEmail') }}</dt>
            <dd>{{ detail.customer_email || '—' }}</dd>
            <dt>{{ t('view.licenses.version') }}</dt>
            <dd>{{ detail.version || '—' }}</dd>
            <dt>{{ t('view.licenses.status') }}</dt>
            <dd>
              <Badge :tone="statusTone(detail.status)">{{ statusText(detail.status) }}</Badge>
            </dd>
            <dt>{{ t('view.licenses.expiresAt') }}</dt>
            <dd>{{ formatDate(detail.expires_at) }}</dd>
            <dt>{{ t('view.licenses.created') }}</dt>
            <dd>{{ formatDate(detail.created_at) }}</dd>
          </dl>
        </section>

        <section class="lic-detail-section">
          <h4>{{ t('view.licenses.detailQuota') }}</h4>
          <dl class="lic-dl">
            <dt>{{ t('view.licenses.maxAgents') }}</dt>
            <dd>{{ detail.max_agents ?? '—' }}</dd>
            <dt>{{ t('view.licenses.maxUsers') }}</dt>
            <dd>{{ detail.max_users ?? '—' }}</dd>
          </dl>
        </section>

        <section class="lic-detail-section">
          <h4>{{ t('view.licenses.detailHistory') }}</h4>
          <div v-if="detail.history && detail.history.length" class="lic-history">
            <div v-for="(h, i) in detail.history" :key="i" class="lic-history-item">
              <span class="lic-history-action">{{ h.action }}</span>
              <span class="lic-history-time">{{ formatDate(h.timestamp || h.time) }}</span>
              <span v-if="h.actor" class="lic-history-actor">{{ h.actor }}</span>
            </div>
          </div>
          <p v-else class="lic-muted">{{ t('view.licenses.noHistory') }}</p>
        </section>
      </div>

      <template #footer>
        <button class="lic-btn" @click="closeDetail">{{ t('common.close') }}</button>
        <button
          v-if="detail && detail.status !== 'revoked'"
          class="lic-btn lic-btn--primary"
          @click="openRenew(detail)"
        >
          {{ t('view.licenses.renew') }}
        </button>
        <button
          v-if="detail && detail.status !== 'revoked'"
          class="lic-btn lic-btn--danger"
          @click="revokeLicense(detail)"
        >
          {{ t('view.licenses.revoke') }}
        </button>
      </template>
    </DetailDrawer>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue';
import { useApiStore } from '../stores/api.js';
import { useToast } from '../composables/useToast.js';
import { useI18n } from '../i18n';
import { StatCard, Badge, DataTable } from '../components/index.js';
import ListPageLayout from '../components/ListPageLayout.vue';
import DetailDrawer from '../components/DetailDrawer.vue';
import AppIcon from '../components/AppIcon.vue';

const { t } = useI18n();
const api = useApiStore();
const toast = useToast();

// ── State ──────────────────────────────────────────────────────
const licenses = ref([]);
const loading = ref(true);
const error = ref('');
const showGenerate = ref(false);
const showRenew = ref(false);
const showDetail = ref(false);
const saving = ref(false);
const formError = ref('');
const detail = ref(null);
const renewTarget = ref(null);
const renewDays = ref(365);

const form = ref({
  customer_name: '',
  customer_email: '',
  version: 'enterprise',
  max_agents: 10,
  max_users: 50,
  valid_days: 365,
});

const filters = reactive({ status: '', version: '', expiry: '', query: '' });

// ── Filters ────────────────────────────────────────────────────
const filterSchema = computed(() => [
  {
    key: 'status',
    label: t('view.licenses.allStatuses'),
    options: [
      { value: 'trial', label: t('view.licenses.statusTrial') },
      { value: 'active', label: t('view.licenses.statusActive') },
      { value: 'expired', label: t('view.licenses.statusExpired') },
      { value: 'revoked', label: t('view.licenses.statusRevoked') },
    ],
  },
  {
    key: 'version',
    label: t('view.licenses.allVersions'),
    options: [
      { value: 'personal', label: t('view.licenses.versionPersonal') },
      { value: 'team', label: t('view.licenses.versionTeam') },
      { value: 'enterprise', label: t('view.licenses.versionEnterprise') },
    ],
  },
  {
    key: 'expiry',
    label: t('view.licenses.allExpiry'),
    options: [
      { value: 'active', label: t('view.licenses.expiryActive') },
      { value: 'soon', label: t('view.licenses.expirySoon') },
      { value: 'expired', label: t('view.licenses.expiryExpired') },
    ],
  },
]);

// ── Columns ────────────────────────────────────────────────────
const cols = computed(() => [
  { key: 'customer_name', label: t('view.licenses.customerName') },
  { key: 'licenseKeyMasked', label: t('view.licenses.licenseKey') },
  { key: 'version', label: t('view.licenses.version') },
  {
    key: 'statusText',
    label: t('view.licenses.status'),
    type: 'badge',
    tone: (_v, row) => statusTone(row.status),
  },
  { key: 'expires_at', label: t('view.licenses.expiresAt'), type: 'time' },
  { key: 'max_agents', label: t('view.licenses.maxAgents'), type: 'num' },
  { key: 'max_users', label: t('view.licenses.maxUsers'), type: 'num' },
]);

// ── Helpers ────────────────────────────────────────────────────
function statusTone(status) {
  if (status === 'trial') return 'success';
  if (status === 'active') return 'info';
  if (status === 'expired') return 'fail';
  if (status === 'revoked') return 'warn';
  return 'neutral';
}

function statusText(status) {
  if (status === 'trial') return t('view.licenses.statusTrial');
  if (status === 'active') return t('view.licenses.statusActive');
  if (status === 'expired') return t('view.licenses.statusExpired');
  if (status === 'revoked') return t('view.licenses.statusRevoked');
  return status || '—';
}

function maskKey(key) {
  if (!key) return '—';
  if (typeof key !== 'string') return String(key);
  // 已脱敏格式直接返回
  if (key.endsWith('_****')) return key;
  const parts = key.split('_');
  // maop_<segment>_<secret> → maop_<segment>_****
  if (parts.length >= 3 && parts[0] === 'maop') {
    return parts[0] + '_' + parts[1] + '_****';
  }
  // 通用脱敏：保留前 12 位，末尾用 **** 替代
  if (key.length > 12) return key.slice(0, -4) + '****';
  return key;
}

function formatDate(ts) {
  if (!ts) return '—';
  const d = new Date(typeof ts === 'number' ? ts * (ts < 1e12 ? 1000 : 1) : ts);
  if (isNaN(d.getTime())) return String(ts);
  return d.toLocaleString();
}
function daysFromNow(days) {
  const n = Number(days) || 365;
  return new Date(Date.now() + n * 86400000).toISOString().slice(0, 10); // YYYY-MM-DD
}

function isExpired(lic) {
  if (!lic.expires_at) return false;
  const exp = typeof lic.expires_at === 'number'
    ? lic.expires_at * (lic.expires_at < 1e12 ? 1000 : 1)
    : new Date(lic.expires_at).getTime();
  return exp < Date.now();
}

function isExpiringSoon(lic) {
  if (!lic.expires_at) return false;
  const exp = typeof lic.expires_at === 'number'
    ? lic.expires_at * (lic.expires_at < 1e12 ? 1000 : 1)
    : new Date(lic.expires_at).getTime();
  const diff = exp - Date.now();
  return diff > 0 && diff < 30 * 86400 * 1000;
}

// ── Derived data ───────────────────────────────────────────────
const tableRows = computed(() =>
  licenses.value.map((lic) => ({
    ...lic,
    licenseKeyMasked: maskKey(lic.license_id || lic.license_key),
    statusText: statusText(lic.status),
  })),
);

const visibleRows = computed(() => {
  const fq = (filters.query || '').trim().toLowerCase();
  return tableRows.value.filter((row) => {
    if (filters.status && row.status !== filters.status) return false;
    if (filters.version && row.version !== filters.version) return false;
    if (filters.expiry) {
      if (filters.expiry === 'active' && (isExpired(row) || isExpiringSoon(row))) return false;
      if (filters.expiry === 'soon' && !isExpiringSoon(row)) return false;
      if (filters.expiry === 'expired' && !isExpired(row)) return false;
    }
    if (fq) {
      const hay = (row.customer_name + ' ' + (row.license_id || '')).toLowerCase();
      if (!hay.includes(fq)) return false;
    }
    return true;
  });
});

const activeCount = computed(() =>
  licenses.value.filter((l) => l.status === 'active' || l.status === 'trial').length,
);

const expiringSoonCount = computed(() => licenses.value.filter(isExpiringSoon).length);

// ── API ────────────────────────────────────────────────────────
async function load() {
  loading.value = true;
  error.value = '';
  try {
    const d = await api.get('/api/licenses/list');
    licenses.value = d.licenses || [];
  } catch (e) {
    error.value = e.message || t('view.licenses.loadFailed');
    licenses.value = [];
  } finally {
    loading.value = false;
  }
}

async function generateLicense() {
  formError.value = '';
  if (!form.value.customer_name.trim() || !form.value.customer_email.trim()) {
    formError.value = t('view.licenses.customerRequired');
    return;
  }
  saving.value = true;
  try {
    await api.post('/api/licenses/create', {
      customer: form.value.customer_name.trim(),
      expires_at: daysFromNow(form.value.valid_days),
      max_users: form.value.max_users,
      features: form.value.max_agents ? [`max_agents:${form.value.max_agents}`] : [],
      notes: form.value.customer_email ? `email: ${form.value.customer_email.trim()}` : '',
    });
    toast.success(t('view.licenses.generated', { name: form.value.customer_name }));
    showGenerate.value = false;
    await load();
  } catch (e) {
    formError.value = e.message || t('view.licenses.generateFailed');
  } finally {
    saving.value = false;
  }
}

async function openDetail(row) {
  detail.value = row;
  showDetail.value = true;
  // 懒加载完整详情（含操作历史）
  try {
    const id = row.license_id || row.license_key;
    const d = await api.get(`/api/licenses/${encodeURIComponent(id)}`);
    if (d && d.license) {
      detail.value = { ...row, ...d.license };
    }
  } catch {
    // 详情加载失败时保留列表行数据
  }
}

function closeDetail() {
  showDetail.value = false;
  detail.value = null;
}

function openRenew(lic) {
  renewTarget.value = lic;
  renewDays.value = 365;
  showRenew.value = true;
}

async function renewLicense() {
  if (!renewTarget.value) return;
  saving.value = true;
  try {
    const id = renewTarget.value.license_id || renewTarget.value.license_key;
    await api.post(`/api/licenses/${encodeURIComponent(id)}/renew`, { new_expires_at: daysFromNow(renewDays.value) });
    toast.success(t('view.licenses.renewed', { id, days: renewDays.value }));
    showRenew.value = false;
    renewTarget.value = null;
    await load();
    // 如详情面板打开，刷新详情
    if (showDetail.value && detail.value) {
      try {
        const d = await api.get(`/api/licenses/${encodeURIComponent(detail.value.license_id || detail.value.license_key)}`);
        if (d && d.license) detail.value = { ...detail.value, ...d.license };
      } catch { /* ignore */ }
    }
  } catch (e) {
    toast.error(e.message || t('view.licenses.renewFailed'));
  } finally {
    saving.value = false;
  }
}

async function revokeLicense(lic) {
  const id = lic.license_id || lic.license_key;
  if (typeof confirm === 'function' && !confirm(t('view.licenses.revokeConfirm', { id }))) return;
  try {
    await api.post(`/api/licenses/${encodeURIComponent(id)}/revoke`, { reason: '' });
    toast.success(t('view.licenses.revoked', { id }));
    await load();
    if (showDetail.value && detail.value && (detail.value.license_id || detail.value.license_key) === id) {
      closeDetail();
    }
  } catch (e) {
    toast.error(e.message || t('view.licenses.revokeFailed'));
  }
}

function openGenerate() {
  form.value = {
    customer_name: '',
    customer_email: '',
    version: 'enterprise',
    max_agents: 10,
    max_users: 50,
    valid_days: 365,
  };
  formError.value = '';
  showGenerate.value = true;
}

onMounted(load);
</script>

<style scoped>
.licenses-view { display: flex; flex-direction: column; }

/* ── Buttons ─────────────────────────────────────────────────── */
.lic-btn {
  display: inline-flex; align-items: center; gap: 5px;
  background: var(--surface-2); color: var(--text); border: 1px solid var(--border);
  border-radius: var(--r-md); padding: 7px 12px; font-size: 12px; font-weight: 600;
  cursor: pointer; transition: opacity var(--motion) var(--ease), border-color var(--motion) var(--ease);
  font-family: inherit;
}
.lic-btn:hover { border-color: var(--border-strong); }
.lic-btn--primary {
  background: var(--brand); color: var(--brand-contrast); border: none;
}
.lic-btn--primary:hover { opacity: .9; }
.lic-btn--primary:disabled { opacity: .5; cursor: not-allowed; }
.lic-btn--danger {
  background: var(--fail-soft); color: var(--fail); border: 1px solid color-mix(in srgb, var(--fail) 30%, transparent);
}
.lic-btn--danger:hover { opacity: .85; }

/* ── Modal overlay ───────────────────────────────────────────── */
.lic-overlay {
  position: fixed; inset: 0; background: rgba(0, 0, 0, .5);
  display: flex; align-items: center; justify-content: center;
  z-index: var(--z-modal, 200);
}
.lic-dialog {
  position: relative;
  background: var(--surface); border: 1px solid var(--border);
  border-radius: var(--r-lg); padding: 24px;
  width: calc(100% - 32px); max-width: 520px;
  box-shadow: var(--shadow-lg);
  max-height: calc(100vh - 48px); overflow-y: auto;
}
.lic-dialog--sm { max-width: 400px; }
.lic-dialog__close {
  position: absolute; top: 12px; right: 12px;
  display: grid; place-items: center;
  width: 28px; height: 28px;
  background: transparent; border: none; border-radius: var(--r-sm);
  color: var(--text-muted); cursor: pointer;
  transition: color var(--motion) var(--ease), background var(--motion) var(--ease);
}
.lic-dialog__close:hover { color: var(--text); background: var(--surface-2); }
.lic-dialog h3 { margin: 0 0 16px; font-size: 16px; color: var(--text); }

/* ── Form ────────────────────────────────────────────────────── */
.lic-fieldset {
  border: 1px solid var(--border); border-radius: var(--r-md);
  padding: 12px; margin: 0 0 12px;
}
.lic-fieldset legend {
  font-size: 11px; font-weight: 700; color: var(--text-muted);
  text-transform: uppercase; letter-spacing: .05em; padding: 0 6px;
}
.lic-field {
  display: flex; flex-direction: column; gap: 4px;
  font-size: 12px; color: var(--text-muted); margin-bottom: 8px;
}
.lic-field:last-child { margin-bottom: 0; }
.lic-input {
  background: var(--bg, var(--surface-2)); border: 1px solid var(--border);
  border-radius: var(--r-md); padding: 8px 10px; color: var(--text); font-size: 13px;
  font-family: inherit; transition: border-color var(--motion) var(--ease);
}
.lic-input:focus { outline: none; border-color: var(--brand); }
.lic-quota-row { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.lic-form-error { color: var(--fail); font-size: 12px; margin: 8px 0; }
.lic-dialog-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 16px; }

/* ── Detail drawer content ───────────────────────────────────── */
.lic-detail { display: flex; flex-direction: column; gap: var(--sp-4); }
.lic-detail-section h4 {
  font-size: 12px; font-weight: 700; color: var(--text-muted);
  text-transform: uppercase; letter-spacing: .05em;
  margin: 0 0 var(--sp-2); padding-bottom: var(--sp-2);
  border-bottom: 1px solid var(--border);
}
.lic-dl { display: grid; grid-template-columns: 110px 1fr; gap: var(--sp-1) var(--sp-3); font-size: 13px; }
.lic-dl dt { color: var(--text-muted); font-weight: 600; }
.lic-dl dd { margin: 0; color: var(--text); word-break: break-all; }
.lic-mono { font-family: var(--font-mono, monospace); font-size: 12px; }

.lic-history { display: flex; flex-direction: column; gap: var(--sp-2); }
.lic-history-item {
  display: flex; align-items: center; gap: var(--sp-2); flex-wrap: wrap;
  padding: var(--sp-2); background: var(--surface-2); border-radius: var(--r-sm);
  font-size: 12px;
}
.lic-history-action { font-weight: 600; color: var(--text); }
.lic-history-time { color: var(--text-muted); }
.lic-history-actor {
  margin-left: auto; font-size: 11px; color: var(--text-faint);
  background: var(--surface-3, var(--surface-2)); padding: 1px 6px; border-radius: var(--r-full);
}
.lic-muted { color: var(--text-faint); font-size: 13px; }
</style>