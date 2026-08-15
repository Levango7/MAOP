<template>
  <div class="apikeys-page">
    <ListPageLayout
      :loading="loading"
      :error="error"
      :empty="!keys.length"
      :error-title="t('view.apikeys.loadError')"
      :empty-title="t('view.apikeys.noKeys')"
      :empty-desc="t('view.apikeys.noKeysDesc')"
      :loading-lines="6"
    >
      <template #badges>
        <Badge tone="brand">{{ t('view.apikeys.enterprise') }}</Badge>
      </template>
      <template #actions>
        <button class="btn btn--primary" @click="openGenerate">
          <AppIcon name="plus" :size="15" /> {{ t('view.apikeys.generate') }}
        </button>
      </template>

      <template #content>
        <div class="ak-table" role="table" :aria-label="t('view.apikeys.subtitle')">
          <div class="ak-row ak-row--head" role="row">
            <div class="ak-cell ak-cell--name" role="columnheader">{{ t('view.apikeys.name') }}</div>
            <div class="ak-cell ak-cell--prefix" role="columnheader">{{ t('view.apikeys.keyPrefix') }}</div>
            <div class="ak-cell ak-cell--scopes" role="columnheader">{{ t('view.apikeys.scopes') }}</div>
            <div class="ak-cell ak-cell--status" role="columnheader">{{ t('view.apikeys.status') }}</div>
            <div class="ak-cell ak-cell--lastused" role="columnheader">{{ t('view.apikeys.lastUsed') }}</div>
            <div class="ak-cell ak-cell--created" role="columnheader">{{ t('view.apikeys.createdAt') }}</div>
            <div class="ak-cell ak-cell--actions" role="columnheader">{{ t('view.apikeys.actions') }}</div>
          </div>
          <div v-for="k in keys" :key="k.key_id" class="ak-row" role="row">
            <div class="ak-cell ak-cell--name" role="cell">
              <span class="ak-name">{{ k.name }}</span>
            </div>
            <div class="ak-cell ak-cell--prefix" role="cell">
              <code class="ak-mono">{{ k.key_prefix }}</code>
            </div>
            <div class="ak-cell ak-cell--scopes" role="cell">
              <template v-if="(k.scopes || []).length">
                <Badge v-for="s in (k.scopes || []).slice(0, 3)" :key="s" tone="neutral">{{ s }}</Badge>
                <span v-if="(k.scopes || []).length > 3" class="ak-more">+{{ k.scopes.length - 3 }}</span>
              </template>
              <span v-else class="ak-muted">—</span>
            </div>
            <div class="ak-cell ak-cell--status" role="cell">
              <Badge :tone="statusTone(k.status)">{{ statusLabel(k.status) }}</Badge>
            </div>
            <div class="ak-cell ak-cell--lastused" role="cell">
              <span class="ak-time">{{ k.last_used_at ? formatRel(k.last_used_at) : t('view.apikeys.never') }}</span>
            </div>
            <div class="ak-cell ak-cell--created" role="cell">
              <span class="ak-time">{{ formatRel(k.created_at) }}</span>
            </div>
            <div class="ak-cell ak-cell--actions" role="cell">
              <button class="btn-icon" type="button" :title="t('view.apikeys.viewDetail')" :aria-label="t('view.apikeys.viewDetail')" @click="openDetail(k)">
                <AppIcon name="file-text" :size="14" aria-hidden="true" />
              </button>
              <button class="btn-icon" type="button" :title="t('view.apikeys.edit')" :aria-label="t('view.apikeys.edit')" @click="openEdit(k)">
                <AppIcon name="gear" :size="14" aria-hidden="true" />
              </button>
              <button
                v-if="k.status !== 'revoked'"
                class="btn-icon btn-icon--danger"
                type="button"
                :title="t('view.apikeys.revoke')"
                :aria-label="t('view.apikeys.revoke')"
                @click="revoke(k)"
              >
                <AppIcon name="trash" :size="14" aria-hidden="true" />
              </button>
            </div>
          </div>
        </div>
      </template>
    </ListPageLayout>

    <!-- 生成 API Key 对话框 -->
    <div v-if="showGenerate" v-modal-a11y class="modal-overlay" @click.self="closeGenerate" @modal:escape="closeGenerate">
      <div class="modal" role="document">
        <button class="modal-close" type="button" :aria-label="t('common.close')" @click="closeGenerate">
          <AppIcon name="x" :size="16" aria-hidden="true" />
        </button>
        <h3>{{ t('view.apikeys.generate') }}</h3>
        <div class="form">
          <label class="form-label">
            <span>{{ t('view.apikeys.name') }}</span>
            <input v-model="form.name" class="input" type="text" :placeholder="t('view.apikeys.name')" />
          </label>
          <div class="form-label">
            <span>{{ t('view.apikeys.scopes') }}</span>
            <div class="scope-groups">
              <div v-for="g in SCOPE_GROUPS" :key="g.group" class="scope-group">
                <div class="scope-group__head">{{ t('view.apikeys.scopeGroup.' + g.group) }}</div>
                <label v-for="s in g.scopes" :key="s" class="scope-chip">
                  <input v-model="form.scopes" type="checkbox" :value="s" />
                  <span>{{ s }}</span>
                </label>
              </div>
            </div>
          </div>
          <label class="form-label">
            <span>{{ t('view.apikeys.rateLimit') }}</span>
            <input v-model.number="form.rate_limit" class="input" type="number" min="0" placeholder="0" />
          </label>
          <label class="form-label">
            <span>{{ t('view.apikeys.ipWhitelist') }}</span>
            <input v-model="form.ip_whitelist" class="input" type="text" placeholder="10.0.0.0/8, 192.168.1.1" />
          </label>
          <label class="form-label">
            <span>{{ t('view.apikeys.expiresIn') }}</span>
            <select v-model="form.expires_in" class="input">
              <option value="7d">{{ t('view.apikeys.expire7d') }}</option>
              <option value="30d">{{ t('view.apikeys.expire30d') }}</option>
              <option value="90d">{{ t('view.apikeys.expire90d') }}</option>
              <option value="never">{{ t('view.apikeys.expireNever') }}</option>
            </select>
          </label>
        </div>
        <div class="modal-actions">
          <button class="btn" type="button" @click="closeGenerate">{{ t('view.apikeys.cancel') }}</button>
          <button class="btn btn--primary" type="button" :disabled="saving" @click="generate">
            {{ saving ? t('view.apikeys.generating') : t('view.apikeys.generate') }}
          </button>
        </div>
      </div>
    </div>

    <!-- 生成后显示完整 Key(仅一次) -->
    <div v-if="showKeyResult" v-modal-a11y class="modal-overlay" @click.self="closeKeyResult" @modal:escape="closeKeyResult">
      <div class="modal" role="document">
        <button class="modal-close" type="button" :aria-label="t('common.close')" @click="closeKeyResult">
          <AppIcon name="x" :size="16" aria-hidden="true" />
        </button>
        <h3>{{ t('view.apikeys.generated') }}</h3>
        <div class="key-result">
          <AppIcon name="alert-triangle" :size="18" class="key-warn-icon" />
          <p class="key-warn">{{ t('view.apikeys.generatedDesc') }}</p>
          <div class="key-box">
            <code class="key-full">{{ generatedKey }}</code>
            <button class="btn btn--sm" type="button" @click="copyKey">
              <AppIcon name="clipboard" :size="13" /> {{ t('view.apikeys.copyKey') }}
            </button>
          </div>
        </div>
        <div class="modal-actions">
          <button class="btn btn--primary" type="button" @click="closeKeyResult">{{ t('view.apikeys.done') }}</button>
        </div>
      </div>
    </div>

    <!-- 编辑对话框 -->
    <div v-if="showEdit" v-modal-a11y class="modal-overlay" @click.self="closeEdit" @modal:escape="closeEdit">
      <div class="modal" role="document">
        <button class="modal-close" type="button" :aria-label="t('common.close')" @click="closeEdit">
          <AppIcon name="x" :size="16" aria-hidden="true" />
        </button>
        <h3>{{ t('view.apikeys.editTitle') }}</h3>
        <div class="form">
          <label class="form-label">
            <span>{{ t('view.apikeys.name') }}</span>
            <input v-model="editForm.name" class="input" type="text" />
          </label>
          <div class="form-label">
            <span>{{ t('view.apikeys.scopes') }}</span>
            <div class="scope-groups">
              <div v-for="g in SCOPE_GROUPS" :key="g.group" class="scope-group">
                <div class="scope-group__head">{{ t('view.apikeys.scopeGroup.' + g.group) }}</div>
                <label v-for="s in g.scopes" :key="s" class="scope-chip">
                  <input v-model="editForm.scopes" type="checkbox" :value="s" />
                  <span>{{ s }}</span>
                </label>
              </div>
            </div>
          </div>
          <label class="form-label">
            <span>{{ t('view.apikeys.rateLimit') }}</span>
            <input v-model.number="editForm.rate_limit" class="input" type="number" min="0" />
          </label>
          <label class="form-label">
            <span>{{ t('view.apikeys.ipWhitelist') }}</span>
            <input v-model="editForm.ip_whitelist" class="input" type="text" />
          </label>
        </div>
        <div class="modal-actions">
          <button class="btn" type="button" @click="closeEdit">{{ t('view.apikeys.cancel') }}</button>
          <button class="btn btn--primary" type="button" :disabled="saving" @click="saveEdit">
            {{ saving ? t('view.apikeys.saving') : t('view.apikeys.save') }}
          </button>
        </div>
      </div>
    </div>

    <!-- 详情面板 -->
    <DetailDrawer :open="showDetail" :title="t('view.apikeys.detailTitle')" icon="shield" @close="closeDetail">
      <div v-if="detail" class="detail-content">
        <!-- 基本信息 -->
        <section class="detail-section">
          <h4 class="detail-section__title">{{ t('view.apikeys.basicInfo') }}</h4>
          <dl class="detail-dl">
            <dt>{{ t('view.apikeys.name') }}</dt><dd>{{ detail.name }}</dd>
            <dt>{{ t('view.apikeys.keyId') }}</dt><dd><code class="ak-mono">{{ detail.key_id }}</code></dd>
            <dt>{{ t('view.apikeys.keyPrefix') }}</dt><dd><code class="ak-mono">{{ detail.key_prefix }}</code></dd>
            <dt>{{ t('view.apikeys.status') }}</dt><dd><Badge :tone="statusTone(detail.status)">{{ statusLabel(detail.status) }}</Badge></dd>
            <dt>{{ t('view.apikeys.createdAt') }}</dt><dd>{{ formatAbs(detail.created_at) }}</dd>
            <dt>{{ t('view.apikeys.lastUsed') }}</dt><dd>{{ detail.last_used_at ? formatAbs(detail.last_used_at) : t('view.apikeys.never') }}</dd>
            <dt>{{ t('view.apikeys.expiresAt') }}</dt><dd>{{ detail.expires_at ? formatAbs(detail.expires_at) : t('view.apikeys.noExpiry') }}</dd>
          </dl>
        </section>

        <!-- 权限范围 -->
        <section class="detail-section">
          <h4 class="detail-section__title">{{ t('view.apikeys.scopes') }}</h4>
          <div v-if="(detail.scopes || []).length" class="scope-badges">
            <Badge v-for="s in detail.scopes" :key="s" tone="brand">{{ s }}</Badge>
          </div>
          <p v-else class="ak-muted">{{ t('view.apikeys.noScopes') }}</p>
        </section>

        <!-- 速率限制 + IP 白名单 -->
        <section class="detail-section">
          <h4 class="detail-section__title">{{ t('view.apikeys.rateLimitLabel') }}</h4>
          <p class="detail-value">
            {{ detail.rate_limit ? detail.rate_limit + ' ' + t('view.apikeys.reqPerMin') : t('view.apikeys.unlimited') }}
          </p>
          <h4 class="detail-section__title">{{ t('view.apikeys.ipWhitelistLabel') }}</h4>
          <p class="detail-value">{{ detail.ip_whitelist || t('view.apikeys.anyIp') }}</p>
        </section>

        <!-- 使用统计 -->
        <section class="detail-section">
          <h4 class="detail-section__title">{{ t('view.apikeys.usageStats') }}</h4>
          <div class="stat-summary">
            <div class="stat-item">
              <span class="stat-item__label">{{ t('view.apikeys.totalCalls') }}</span>
              <span class="stat-item__value">{{ statsTotal }}</span>
            </div>
            <div class="stat-item">
              <span class="stat-item__label">{{ t('view.apikeys.successRate') }}</span>
              <span class="stat-item__value">{{ statsSuccessRate }}%</span>
            </div>
            <div class="stat-item">
              <span class="stat-item__label">{{ t('view.apikeys.avgLatency') }}</span>
              <span class="stat-item__value">{{ statsAvgLatency }} {{ t('view.apikeys.ms') }}</span>
            </div>
          </div>

          <!-- 调用量趋势:简易柱状图 -->
          <div class="chart-block">
            <div class="chart-block__title">{{ t('view.apikeys.callTrend') }}</div>
            <div v-if="callTrend.length" class="bar-chart">
              <div v-for="(pt, i) in callTrend" :key="i" class="bar-chart__col" :title="pt.date + ': ' + pt.count">
                <div class="bar-chart__bar" :style="{ height: barHeight(pt.count) + '%' }"></div>
              </div>
            </div>
            <p v-else class="ak-muted">{{ t('view.apikeys.noRecentCalls') }}</p>
          </div>

          <!-- 状态码分布 -->
          <div class="chart-block">
            <div class="chart-block__title">{{ t('view.apikeys.statusDist') }}</div>
            <div class="dist-rows">
              <div v-for="r in statusDist" :key="r.label" class="dist-row">
                <span class="dist-row__label">{{ r.label }}</span>
                <div class="dist-row__bar"><div class="dist-row__fill" :class="r.tone" :style="{ width: r.pct + '%' }"></div></div>
                <span class="dist-row__count">{{ r.count }}</span>
              </div>
            </div>
          </div>
        </section>

        <!-- 最近调用记录 -->
        <section class="detail-section">
          <h4 class="detail-section__title">{{ t('view.apikeys.recentCalls') }}</h4>
          <DataTable
            v-if="recentCalls.length"
            :columns="callCols"
            :rows="recentCalls"
            row-key="ts"
            :empty-text="t('view.apikeys.noRecentCalls')"
            compact
          />
          <p v-else class="ak-muted">{{ t('view.apikeys.noRecentCalls') }}</p>
        </section>
      </div>
    </DetailDrawer>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue';
import { useApiStore } from '../stores/api.js';
import { useToast } from '../composables/useToast.js';
import { useI18n } from '../i18n/index.js';
import Badge from '../components/Badge.vue';
import ListPageLayout from '../components/ListPageLayout.vue';
import DetailDrawer from '../components/DetailDrawer.vue';
import DataTable from '../components/DataTable.vue';
import AppIcon from '../components/AppIcon.vue';

const { t } = useI18n();
const api = useApiStore();
const toast = useToast();

// 权限范围分组(对齐 RBAC 权限模型)
const SCOPE_GROUPS = [
  { group: 'agents', scopes: ['agents:read', 'agents:write', 'agents:execute'] },
  { group: 'config', scopes: ['config:read', 'config:write'] },
  { group: 'memory', scopes: ['memory:read', 'memory:write'] },
  { group: 'models', scopes: ['models:read', 'models:write'] },
  { group: 'cost', scopes: ['cost:read'] },
  { group: 'audit', scopes: ['audit:read'] },
  { group: 'tenant', scopes: ['tenant:read', 'tenant:write', 'tenant:admin'] },
  { group: 'rbac', scopes: ['rbac:read', 'rbac:write'] },
  { group: 'system', scopes: ['system:admin'] },
];

// ── 列表状态 ──
const keys = ref([]);
const loading = ref(true);
const error = ref('');

// ── 生成对话框 ──
const showGenerate = ref(false);
const saving = ref(false);
const form = ref({ name: '', scopes: [], rate_limit: 0, ip_whitelist: '', expires_in: '30d' });

// ── 生成后 Key 展示 ──
const showKeyResult = ref(false);
const generatedKey = ref('');

// ── 编辑对话框 ──
const showEdit = ref(false);
const editForm = ref({ key_id: '', name: '', scopes: [], rate_limit: 0, ip_whitelist: '' });

// ── 详情面板 ──
const showDetail = ref(false);
const detail = ref(null);

// ── 详情派生数据 ──
const callTrend = computed(() => (detail.value && detail.value.stats && detail.value.stats.call_trend) || []);
const statusDist = computed(() => {
  const s = (detail.value && detail.value.stats) || {};
  const dist = s.status_dist || {};
  const total = Object.values(dist).reduce((a, b) => a + (Number(b) || 0), 0);
  return ['2xx', '4xx', '5xx'].map((label) => {
    const count = Number(dist[label]) || 0;
    return {
      label,
      count,
      pct: total ? Math.round((count / total) * 100) : 0,
      tone: label === '2xx' ? 'is-ok' : label === '4xx' ? 'is-warn' : 'is-fail',
    };
  });
});
const recentCalls = computed(() => (detail.value && detail.value.recent_calls) || []);
const statsTotal = computed(() => {
  const s = (detail.value && detail.value.stats) || {};
  return Number(s.total_calls) || callTrend.value.reduce((a, p) => a + (Number(p.count) || 0), 0);
});
const statsSuccessRate = computed(() => {
  const s = (detail.value && detail.value.stats) || {};
  if (s.success_rate !== undefined && s.success_rate !== null) return Math.round(Number(s.success_rate) * 100) / 100;
  const dist = s.status_dist || {};
  const ok = Number(dist['2xx']) || 0;
  const total = ['2xx', '4xx', '5xx'].reduce((a, k) => a + (Number(dist[k]) || 0), 0);
  return total ? Math.round((ok / total) * 1000) / 10 : 0;
});
const statsAvgLatency = computed(() => {
  const s = (detail.value && detail.value.stats) || {};
  return Math.round(Number(s.avg_latency_ms) || 0);
});

const callCols = computed(() => [
  { key: 'time', label: t('view.apikeys.callTime'), type: 'time' },
  { key: 'status', label: t('view.apikeys.callStatus'), type: 'badge' },
  { key: 'latency_ms', label: t('view.apikeys.callLatency'), type: 'num' },
  { key: 'ip', label: t('view.apikeys.callIp') },
]);

// ── 工具函数 ──
function statusTone(s) {
  if (s === 'active') return 'success';
  if (s === 'revoked') return 'fail';
  if (s === 'expired') return 'warn';
  return 'neutral';
}
function statusLabel(s) {
  if (s === 'active') return t('view.apikeys.statusActive');
  if (s === 'revoked') return t('view.apikeys.statusRevoked');
  if (s === 'expired') return t('view.apikeys.statusExpired');
  return s || '—';
}
function formatRel(ts) {
  if (ts === null || ts === undefined || ts === '') return '—';
  const d = new Date(typeof ts === 'number' ? ts : String(ts));
  if (isNaN(d.getTime())) return String(ts);
  const diff = Math.max(0, Math.floor((Date.now() - d.getTime()) / 1000));
  if (diff < 60) return diff + 's ago';
  if (diff < 3600) return Math.floor(diff / 60) + 'm ago';
  if (diff < 86400) return Math.floor(diff / 3600) + 'h ago';
  return Math.floor(diff / 86400) + 'd ago';
}
function formatAbs(ts) {
  if (ts === null || ts === undefined || ts === '') return '—';
  const d = new Date(typeof ts === 'number' ? ts : String(ts));
  if (isNaN(d.getTime())) return String(ts);
  return d.toLocaleString();
}
function expiresToTtl(v) {
  const days = { '7d': 7, '30d': 30, '90d': 90 };
  return days[v] ? days[v] * 86400 : null; // 'never' → null (永不过期)
}
function barHeight(count) {
  const max = Math.max(1, ...callTrend.value.map((p) => Number(p.count) || 0));
  return max ? Math.round(((Number(count) || 0) / max) * 100) : 0;
}

// ── 数据加载 ──
async function load() {
  loading.value = true;
  error.value = '';
  try {
    const d = await api.get('/api/api-keys');
    keys.value = Array.isArray(d) ? d : (d.keys || []);
  } catch (e) {
    error.value = e.message || String(e);
    keys.value = [];
  } finally {
    loading.value = false;
  }
}

// ── 生成 ──
function openGenerate() {
  form.value = { name: '', scopes: [], rate_limit: 0, ip_whitelist: '', expires_in: '30d' };
  showGenerate.value = true;
}
function closeGenerate() {
  showGenerate.value = false;
}
async function generate() {
  if (!form.value.name.trim()) {
    toast.warn(t('view.apikeys.nameRequired'));
    return;
  }
  saving.value = true;
  try {
    const d = await api.post('/api/api-keys', {
      name: form.value.name.trim(),
      scopes: form.value.scopes,
      rate_limit: Number(form.value.rate_limit) || 0,
      ip_whitelist: (form.value.ip_whitelist || '').split(',').map((s) => s.trim()).filter(Boolean),
      ttl_s: expiresToTtl(form.value.expires_in),
    });
    generatedKey.value = d.key || d.plaintext_key || d.api_key || '';
    showGenerate.value = false;
    showKeyResult.value = true;
    await load();
  } catch (e) {
    toast.error(e.message || t('view.apikeys.generateFailed'));
  } finally {
    saving.value = false;
  }
}
function closeKeyResult() {
  showKeyResult.value = false;
  generatedKey.value = '';
}
async function copyKey() {
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(generatedKey.value);
    } else if (typeof document !== 'undefined') {
      const ta = document.createElement('textarea');
      ta.value = generatedKey.value;
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      document.body.removeChild(ta);
    }
    toast.success(t('view.apikeys.copied'));
  } catch {
    toast.error(t('view.apikeys.copyFailed'));
  }
}

// ── 编辑 ──
function openEdit(k) {
  editForm.value = {
    key_id: k.key_id,
    name: k.name || '',
    scopes: [...(k.scopes || [])],
    rate_limit: Number(k.rate_limit) || 0,
    ip_whitelist: k.ip_whitelist || '',
  };
  showEdit.value = true;
}
function closeEdit() {
  showEdit.value = false;
}
async function saveEdit() {
  if (!editForm.value.name.trim()) {
    toast.warn(t('view.apikeys.nameRequired'));
    return;
  }
  saving.value = true;
  try {
    await api.put('/api/api-keys/' + encodeURIComponent(editForm.value.key_id), {
      name: editForm.value.name.trim(),
      scopes: editForm.value.scopes,
      rate_limit: Number(editForm.value.rate_limit) || 0,
      ip_whitelist: (editForm.value.ip_whitelist || '').split(',').map((s) => s.trim()).filter(Boolean),
    });
    toast.success(t('view.apikeys.saved'));
    showEdit.value = false;
    await load();
  } catch (e) {
    toast.error(e.message || t('view.apikeys.saveFailed'));
  } finally {
    saving.value = false;
  }
}

// ── 吊销 ──
async function revoke(k) {
  if (typeof confirm === 'function' && !confirm(t('view.apikeys.revokeConfirm', { name: k.name }))) return;
  try {
    await api.post('/api/api-keys/' + encodeURIComponent(k.key_id) + '/revoke', {});
    toast.success(t('view.apikeys.revoked', { name: k.name }));
    await load();
  } catch (e) {
    toast.error(e.message || t('view.apikeys.revokeFailed'));
  }
}

// ── 详情 ──
async function openDetail(k) {
  showDetail.value = true;
  detail.value = { ...k };
  try {
    const d = await api.get('/api/api-keys/' + encodeURIComponent(k.key_id));
    detail.value = d || detail.value;
  } catch {
    // 保留列表中的基础信息,统计区为空
  }
}
function closeDetail() {
  showDetail.value = false;
  detail.value = null;
}

onMounted(load);
</script>

<style scoped>
.apikeys-page { display: flex; flex-direction: column; }

/* ── 操作按钮 ── */
.btn {
  display: inline-flex; align-items: center; gap: 5px;
  background: var(--surface-2); color: var(--text); border: 1px solid var(--border);
  border-radius: var(--r-md); padding: 7px 12px; font-size: 12px; font-weight: 600;
  cursor: pointer; transition: opacity var(--motion) var(--ease);
}
.btn:hover { opacity: .9; }
.btn--primary {
  background: var(--brand); color: var(--brand-contrast); border: none;
}
.btn--primary:disabled { opacity: .5; cursor: not-allowed; }
.btn--sm { padding: 4px 8px; font-size: 11px; }
.btn-icon {
  display: grid; place-items: center;
  width: 28px; height: 28px;
  background: var(--surface-2); border: 1px solid var(--border);
  border-radius: var(--r-sm); color: var(--text-muted); cursor: pointer;
  transition: color var(--motion) var(--ease), border-color var(--motion) var(--ease);
}
.btn-icon:hover { color: var(--text); border-color: var(--border-strong); }
.btn-icon--danger:hover { color: var(--fail); border-color: var(--fail); }

/* ── 列表表格 ── */
.ak-table {
  background: var(--surface); border: 1px solid var(--border); border-radius: var(--r-lg);
  overflow: hidden;
}
.ak-row {
  display: grid;
  grid-template-columns: 1.4fr 1.2fr 1.6fr 0.8fr 1fr 1fr 110px;
  align-items: center;
  padding: 10px 14px;
  border-bottom: 1px solid var(--border);
  font-size: 13px;
}
.ak-row:last-child { border-bottom: none; }
.ak-row--head {
  background: var(--surface-2);
  font-size: 11px; font-weight: 700; color: var(--text-faint);
  text-transform: uppercase; letter-spacing: .05em;
}
.ak-cell { padding: 0 4px; }
.ak-cell--scopes { display: flex; align-items: center; gap: 4px; flex-wrap: wrap; }
.ak-cell--actions { display: flex; gap: 6px; justify-content: flex-end; }
.ak-name { font-weight: 600; color: var(--text); }
.ak-mono { font-family: var(--font-mono); font-size: 12px; color: var(--text); background: var(--surface-2); padding: 1px 6px; border-radius: var(--r-sm); }
.ak-more { font-size: 11px; color: var(--text-muted); }
.ak-muted { color: var(--text-muted); font-size: 12px; }
.ak-time { color: var(--text-muted); white-space: nowrap; font-size: 12px; }

/* ── 模态 ── */
.modal-overlay {
  position: fixed; inset: 0; background: rgba(0,0,0,.5);
  display: flex; align-items: center; justify-content: center;
  z-index: var(--z-modal, 200);
}
.modal {
  position: relative;
  background: var(--surface); border: 1px solid var(--border);
  border-radius: var(--r-lg); padding: 24px;
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
.modal h3 { margin: 0 0 16px; font-size: 16px; color: var(--text); }
.modal-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 16px; }

/* ── 表单 ── */
.form { display: flex; flex-direction: column; gap: 12px; }
.form-label { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--text-muted); }
.input {
  background: var(--bg); border: 1px solid var(--border);
  border-radius: var(--r-md); padding: 8px 10px; color: var(--text); font-size: 13px;
}
.input:focus { outline: none; border-color: var(--brand); }

/* ── 权限范围分组 ── */
.scope-groups { display: flex; flex-wrap: wrap; gap: 8px; }
.scope-group {
  background: var(--surface-2); border: 1px solid var(--border);
  border-radius: var(--r-md); padding: 8px 10px;
  min-width: 140px;
}
.scope-group__head { font-size: 11px; font-weight: 700; color: var(--text); margin-bottom: 6px; text-transform: uppercase; letter-spacing: .04em; }
.scope-chip {
  display: inline-flex; align-items: center; gap: 4px;
  padding: 3px 6px; margin: 2px 2px 0 0;
  background: var(--surface); border: 1px solid var(--border);
  border-radius: var(--r-sm); font-size: 11px; cursor: pointer; color: var(--text);
}
.scope-chip input { margin: 0; }

/* ── 生成后 Key 展示 ── */
.key-result { display: flex; flex-direction: column; gap: 10px; }
.key-warn-icon { color: var(--warn); align-self: flex-start; }
.key-warn { font-size: 12px; color: var(--text-muted); margin: 0; line-height: 1.5; }
.key-box {
  display: flex; align-items: center; gap: 8px;
  background: var(--surface-2); border: 1px solid var(--border);
  border-radius: var(--r-md); padding: 10px 12px;
}
.key-full {
  flex: 1; font-family: var(--font-mono); font-size: 12px; color: var(--text);
  word-break: break-all; white-space: pre-wrap;
}

/* ── 详情面板 ── */
.detail-content { display: flex; flex-direction: column; gap: var(--sp-4); }
.detail-section { display: flex; flex-direction: column; gap: 6px; }
.detail-section__title { font-size: 12px; font-weight: 700; color: var(--text); margin: 8px 0 4px; text-transform: uppercase; letter-spacing: .04em; }
.detail-dl { display: grid; grid-template-columns: auto 1fr; gap: 4px 12px; font-size: 12px; }
.detail-dl dt { color: var(--text-muted); }
.detail-dl dd { color: var(--text); margin: 0; }
.detail-value { font-size: 13px; color: var(--text); margin: 0 0 8px; }
.scope-badges { display: flex; flex-wrap: wrap; gap: 4px; }

/* ── 统计摘要 ── */
.stat-summary { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; margin-bottom: 12px; }
.stat-item {
  background: var(--surface-2); border: 1px solid var(--border);
  border-radius: var(--r-md); padding: 8px 10px; text-align: center;
}
.stat-item__label { display: block; font-size: 10px; color: var(--text-muted); text-transform: uppercase; letter-spacing: .04em; }
.stat-item__value { display: block; font-size: 16px; font-weight: 700; color: var(--text); margin-top: 2px; font-variant-numeric: tabular-nums; }

/* ── 柱状图(调用量趋势) ── */
.chart-block { margin-top: 12px; }
.chart-block__title { font-size: 11px; font-weight: 600; color: var(--text-muted); margin-bottom: 6px; }
.bar-chart { display: flex; align-items: flex-end; gap: 2px; height: 60px; }
.bar-chart__col { flex: 1; display: flex; align-items: flex-end; height: 100%; }
.bar-chart__bar {
  width: 100%; min-height: 2px; border-radius: 2px 2px 0 0;
  background: var(--brand);
  transition: height var(--motion) var(--ease);
}

/* ── 状态码分布 ── */
.dist-rows { display: flex; flex-direction: column; gap: 4px; }
.dist-row { display: grid; grid-template-columns: 40px 1fr 40px; align-items: center; gap: 8px; font-size: 11px; }
.dist-row__label { color: var(--text-muted); font-family: var(--font-mono); }
.dist-row__bar { height: 8px; background: var(--surface-2); border-radius: var(--r-full); overflow: hidden; }
.dist-row__fill { height: 100%; border-radius: var(--r-full); transition: width var(--motion) var(--ease); }
.dist-row__fill.is-ok { background: var(--success); }
.dist-row__fill.is-warn { background: var(--warn); }
.dist-row__fill.is-fail { background: var(--fail); }
.dist-row__count { color: var(--text); text-align: right; font-variant-numeric: tabular-nums; }

@media (max-width: 900px) {
  .ak-row { grid-template-columns: 1fr 1fr 0.8fr 90px; }
  .ak-cell--scopes, .ak-cell--lastused, .ak-cell--created { display: none; }
  .stat-summary { grid-template-columns: 1fr; }
}
</style>