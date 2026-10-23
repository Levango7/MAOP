<template>
  <div class="alerts-page">
    <ListPageLayout
      :loading="loading"
      :error="error"
      :empty="!rules.length && !inbound.length"
      :error-title="t('view.alerts.loadError')"
      :empty-title="requiresEnterprise ? t('view.alerts.requiresEnterprise') : t('view.alerts.empty')"
      :empty-desc="requiresEnterprise ? t('view.alerts.requiresEnterpriseDesc') : t('view.alerts.emptyDesc')"
      :loading-lines="6"
    >
      <template #badges>
        <Badge tone="brand">{{ t('view.alerts.enterprise') }}</Badge>
      </template>
      <template #actions>
        <button class="btn btn--primary" @click="openCreate">
          <AppIcon name="plus" :size="15" /> {{ t('view.alerts.createBtn') }}
        </button>
        <button class="btn" :disabled="loading" :title="t('view.alerts.refreshBtn')" @click="loadAll">
          <AppIcon name="refresh" :size="14" :class="{ spinning: loading }" aria-hidden="true" />
        </button>
      </template>

      <template #stats>
        <StatCard
          :label="t('view.alerts.statTotalRules')"
          :value="statTotalRules"
          icon="alert-triangle"
          tone="brand"
          :loading="loading"
        />
        <StatCard
          :label="t('view.alerts.statActiveRules')"
          :value="statActiveRules"
          icon="check-circle"
          tone="success"
          :loading="loading"
        />
        <StatCard
          :label="t('view.alerts.statTriggeredToday')"
          :value="statTriggeredToday"
          icon="activity"
          tone="info"
          :loading="loading"
        />
        <StatCard
          :label="t('view.alerts.statFalsePositiveRate')"
          :value="statFalsePositiveRate + '%'"
          icon="gauge"
          tone="warn"
          :loading="loading"
        />
      </template>

      <template #content>
        <p v-if="rules.length" class="al-hint">{{ t('view.alerts.hint') }}</p>
        <div class="al-table" role="table" :aria-label="t('view.alerts.subtitle')">
          <div class="al-row al-row--head" role="row">
            <div class="al-cell al-cell--name" role="columnheader">{{ t('view.alerts.colName') }}</div>
            <div class="al-cell al-cell--metric" role="columnheader">{{ t('view.alerts.colMetric') }}</div>
            <div class="al-cell al-cell--cond" role="columnheader">{{ t('view.alerts.colCondition') }}</div>
            <div class="al-cell al-cell--threshold" role="columnheader">{{ t('view.alerts.colThreshold') }}</div>
            <div class="al-cell al-cell--channel" role="columnheader">{{ t('view.alerts.colChannel') }}</div>
            <div class="al-cell al-cell--status" role="columnheader">{{ t('view.alerts.colStatus') }}</div>
            <div class="al-cell al-cell--actions" role="columnheader">{{ t('view.alerts.colActions') }}</div>
          </div>
          <div v-for="r in rules" :key="r.id" class="al-row" role="row">
            <div class="al-cell al-cell--name" role="cell">
              <span class="al-name">{{ r.name }}</span>
            </div>
            <div class="al-cell al-cell--metric" role="cell">
              <span class="al-text">{{ metricLabel(r.metric) }}</span>
            </div>
            <div class="al-cell al-cell--cond" role="cell">
              <code class="al-mono">{{ conditionLabel(r.condition) }}</code>
            </div>
            <div class="al-cell al-cell--threshold" role="cell">
              <span class="al-num">{{ r.threshold }}</span>
            </div>
            <div class="al-cell al-cell--channel" role="cell">
              <Badge tone="info">{{ channelLabel(r.channel) }}</Badge>
            </div>
            <div class="al-cell al-cell--status" role="cell">
              <Badge :tone="r.enabled ? 'success' : 'neutral'">
                {{ r.enabled ? t('view.alerts.statusEnabled') : t('view.alerts.statusDisabled') }}
              </Badge>
            </div>
            <div class="al-cell al-cell--actions" role="cell">
              <button class="btn-icon" type="button" :title="t('view.alerts.actionHistory')" :aria-label="t('view.alerts.actionHistory')" @click="openHistory(r)">
                <AppIcon name="clock" :size="14" aria-hidden="true" />
              </button>
              <button class="btn-icon" type="button" :title="t('view.alerts.actionEdit')" :aria-label="t('view.alerts.actionEdit')" @click="openEdit(r)">
                <AppIcon name="gear" :size="14" aria-hidden="true" />
              </button>
              <button
                class="btn-icon btn-icon--danger"
                type="button"
                :title="t('view.alerts.actionDelete')"
                :aria-label="t('view.alerts.actionDelete')"
                @click="removeRule(r)"
              >
                <AppIcon name="trash" :size="14" aria-hidden="true" />
              </button>
            </div>
          </div>
        </div>

        <section class="al-inbound" aria-labelledby="al-inbound-heading">
          <h3 id="al-inbound-heading" class="al-section-title">
            {{ t('view.alerts.inboundTitle') }}
            <span v-if="!inboundUnavailable" class="al-muted">{{ inboundTotal }}</span>
          </h3>
          <p class="al-hint">{{ t('view.alerts.inboundDesc') }}</p>
          <p v-if="inboundUnavailable" class="al-hint al-inbound__warn">{{ t('view.alerts.inboundUnavailable') }}</p>
          <p v-else-if="!inbound.length" class="al-hint">{{ t('view.alerts.inboundEmpty') }}</p>
          <div v-else class="ai-table" role="table" :aria-label="t('view.alerts.inboundTitle')">
            <div class="ai-row ai-row--head" role="row">
              <div class="al-cell" role="columnheader">{{ t('view.alerts.inboundColAlert') }}</div>
              <div class="al-cell" role="columnheader">{{ t('view.alerts.inboundColSeverity') }}</div>
              <div class="al-cell" role="columnheader">{{ t('view.alerts.inboundColStatus') }}</div>
              <div class="al-cell" role="columnheader">{{ t('view.alerts.inboundColInstance') }}</div>
              <div class="al-cell" role="columnheader">{{ t('view.alerts.inboundColSummary') }}</div>
              <div class="al-cell" role="columnheader">{{ t('view.alerts.inboundColTime') }}</div>
            </div>
            <div
              v-for="a in inbound"
              :key="`${a.fingerprint}|${a.status}|${a.starts_at}`"
              class="ai-row"
              role="row"
            >
              <div class="al-cell" role="cell"><span class="al-name">{{ a.alertname }}</span></div>
              <div class="al-cell" role="cell"><Badge :tone="severityTone(a.severity)">{{ a.severity || '—' }}</Badge></div>
              <div class="al-cell" role="cell">
                <Badge :tone="a.status === 'resolved' ? 'success' : 'warn'">{{ inboundStatusLabel(a.status) }}</Badge>
              </div>
              <div class="al-cell" role="cell"><code class="al-mono">{{ a.instance || '—' }}</code></div>
              <div class="al-cell" role="cell"><span class="al-text">{{ a.summary || a.description || '—' }}</span></div>
              <div class="al-cell" role="cell"><span class="al-num">{{ formatTs(a.received_at) }}</span></div>
            </div>
          </div>
        </section>
      </template>
    </ListPageLayout>

    <!-- 创建/编辑对话框 -->
    <Teleport to="body">
    <div v-if="showForm" v-modal-a11y class="modal-overlay" @click.self="closeForm" @modal:escape="closeForm">
      <div class="modal" role="dialog" aria-modal="true">
        <button class="modal-close" type="button" :aria-label="t('common.close')" @click="closeForm">
          <AppIcon name="x" :size="16" aria-hidden="true" />
        </button>
        <h3>{{ editingId ? t('view.alerts.dialogTitleEdit') : t('view.alerts.dialogTitleCreate') }}</h3>
        <div class="form">
          <label class="form-label">
            <span>{{ t('view.alerts.fieldName') }}</span>
            <input v-model="form.name" class="input" type="text" :placeholder="t('view.alerts.fieldName')" />
          </label>
          <label class="form-label">
            <span>{{ t('view.alerts.fieldDescription') }}</span>
            <input v-model="form.description" class="input" type="text" :placeholder="t('view.alerts.fieldDescription')" />
          </label>
          <div class="form-row">
            <label class="form-label">
              <span>{{ t('view.alerts.fieldMetric') }}</span>
              <select v-model="form.metric" class="input">
                <option v-for="m in METRICS" :key="m" :value="m">{{ metricLabel(m) }}</option>
              </select>
            </label>
            <label class="form-label">
              <span>{{ t('view.alerts.fieldCondition') }}</span>
              <select v-model="form.condition" class="input">
                <option value=">">{{ t('view.alerts.condGt') }}</option>
                <option value="<">{{ t('view.alerts.condLt') }}</option>
                <option value=">=">{{ t('view.alerts.condGte') }}</option>
                <option value="<=">{{ t('view.alerts.condLte') }}</option>
                <option value="=">{{ t('view.alerts.condEq') }}</option>
              </select>
            </label>
          </div>
          <label class="form-label">
            <span>{{ t('view.alerts.fieldThreshold') }}</span>
            <input v-model="form.threshold" class="input" type="number" step="any" :placeholder="t('view.alerts.placeholderThreshold')" />
          </label>
          <label class="form-label">
            <span>{{ t('view.alerts.fieldChannel') }}</span>
            <select v-model="form.channel" class="input">
              <option value="email">{{ t('view.alerts.channelEmail') }}</option>
              <option value="webhook">{{ t('view.alerts.channelWebhook') }}</option>
              <option value="inline">{{ t('view.alerts.channelInline') }}</option>
            </select>
          </label>
          <label v-if="form.channel === 'email'" class="form-label">
            <span>{{ t('view.alerts.fieldEmail') }}</span>
            <input v-model="form.email" class="input" type="text" :placeholder="t('view.alerts.placeholderEmail')" />
          </label>
          <label v-if="form.channel === 'webhook'" class="form-label">
            <span>{{ t('view.alerts.fieldWebhook') }}</span>
            <select v-model="form.webhook_id" class="input">
              <option value="">{{ t('view.alerts.fieldWebhook') }}…</option>
              <option v-for="w in webhooks" :key="w.id" :value="w.id">{{ w.name }}</option>
            </select>
          </label>
          <label class="form-label">
            <span>{{ t('view.alerts.fieldCooldown') }}</span>
            <input v-model.number="form.cooldown_s" class="input" type="number" min="0" placeholder="300" />
          </label>
          <label class="form-label form-label--inline">
            <input v-model="form.enabled" type="checkbox" />
            <span>{{ t('view.alerts.fieldEnabled') }}</span>
          </label>
        </div>
        <div class="modal-actions">
          <button class="btn" type="button" @click="closeForm">{{ t('view.alerts.btnCancel') }}</button>
          <button class="btn btn--primary" type="button" :disabled="saving" @click="save">
            {{ saving ? t('view.alerts.saving') : t('view.alerts.btnSave') }}
          </button>
        </div>
      </div>
    </div>
    </Teleport>

    <!-- 告警历史详情面板 -->
    <DetailDrawer :open="showHistory" :title="t('view.alerts.historyTitle')" icon="alert-triangle" @close="closeHistory">
      <div v-if="historyRule" class="detail-content">
        <section class="detail-section">
          <h4 class="detail-section__title">{{ t('view.alerts.colName') }}</h4>
          <p class="detail-value">{{ historyRule.name }}</p>
          <h4 class="detail-section__title">{{ t('view.alerts.colCondition') }}</h4>
          <p class="detail-value">
            {{ metricLabel(historyRule.metric) }}
            {{ conditionLabel(historyRule.condition) }}
            {{ historyRule.threshold }}
          </p>
        </section>
        <section class="detail-section">
          <h4 class="detail-section__title">{{ t('view.alerts.historyTitle') }}</h4>
          <div v-if="historyLoading" class="al-muted">{{ t('view.alerts.loading') }}</div>
          <div v-else-if="historyRecords.length">
            <DataTable
              :columns="historyCols"
              :rows="historyRecords"
              row-key="id"
              :empty-text="t('view.alerts.historyEmpty')"
              compact
            />
          </div>
          <p v-else class="al-muted">{{ t('view.alerts.historyEmpty') }}</p>
        </section>
      </div>
    </DetailDrawer>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue';
import { useApiStore } from '../stores/api.js';
import { useEditionStore } from '../stores/edition.js';
import { useToast } from '../composables/useToast.js';
import { useConfirm } from '../composables/useConfirm.js';
import { useI18n } from '../i18n';
import Badge from '../components/Badge.vue';
import ListPageLayout from '../components/ListPageLayout.vue';
import DetailDrawer from '../components/DetailDrawer.vue';
import DataTable from '../components/DataTable.vue';
import StatCard from '../components/StatCard.vue';
import AppIcon from '../components/AppIcon.vue';

const { t } = useI18n();
const api = useApiStore();
const edition = useEditionStore();
const toast = useToast();
const { showConfirm } = useConfirm();

// 2026-09-23: 后端 /api/audit/alert/* 由 FeatureFlag.AUDIT_LOG 门禁
// （observability_service._require_audit_feature 抛 404），而 AUDIT_LOG 属
// 企业版能力。个人版下此页无数据可加载，直接展示版本提示比让用户看一条
// 404 报错更清楚。模式与 Overview.vue / Evolve.vue 的 edition.isEnterprise
// 判断一致。
const requiresEnterprise = computed(() => !edition.isEnterprise);

// 可选指标
const METRICS = [
  'cpu_usage',
  'memory_usage',
  'latency',
  'error_rate',
  'cost_daily',
  'task_queue',
  'agent_offline',
];

// ── 列表状态 ──
const rules = ref([]);
const webhooks = ref([]);
const stats = ref(null);
const loading = ref(true);
const error = ref('');

// ── Alertmanager 入站告警（个人版也能用，与上面的企业版规则是两条来源）──
const inbound = ref([]);
const inboundTotal = ref(0);
const inboundUnavailable = ref(false);

// ── 创建/编辑对话框 ──
const showForm = ref(false);
const saving = ref(false);
const editingId = ref('');
const form = ref(defaultForm());

// ── 告警历史 ──
const showHistory = ref(false);
const historyRule = ref(null);
const historyLoading = ref(false);
const historyRecords = ref([]);

function defaultForm() {
  return {
    name: '',
    description: '',
    metric: 'cpu_usage',
    condition: '>',
    threshold: '',
    channel: 'email',
    email: '',
    webhook_id: '',
    cooldown_s: 300,
    enabled: true,
  };
}

const historyCols = computed(() => [
  { key: 'time', label: t('view.alerts.historyColTime'), type: 'time' },
  { key: 'rule', label: t('view.alerts.historyColRule') },
  { key: 'severity', label: t('view.alerts.historyColSeverity'), type: 'badge' },
  { key: 'message', label: t('view.alerts.historyColMessage') },
  { key: 'status', label: t('view.alerts.historyColStatus') },
]);

// ── 统计 ──
const statTotalRules = computed(() => rules.value.length);
const statActiveRules = computed(() => rules.value.filter((r) => r.enabled).length);
const statTriggeredToday = computed(() => {
  const s = stats.value || {};
  return Number(s.triggered_today) || 0;
});
const statFalsePositiveRate = computed(() => {
  const s = stats.value || {};
  if (s.false_positive_rate !== undefined && s.false_positive_rate !== null) {
    return Math.round(Number(s.false_positive_rate) * 100) / 100;
  }
  return 0;
});

// ── 工具函数 ──
function metricLabel(m) {
  const map = {
    cpu_usage: t('view.alerts.metricCpuUsage'),
    memory_usage: t('view.alerts.metricMemoryUsage'),
    latency: t('view.alerts.metricLatency'),
    error_rate: t('view.alerts.metricErrorRate'),
    cost_daily: t('view.alerts.metricCostDaily'),
    task_queue: t('view.alerts.metricTaskQueue'),
    agent_offline: t('view.alerts.metricAgentOffline'),
  };
  return map[m] || m;
}

function conditionLabel(c) {
  const map = {
    '>': t('view.alerts.condGt'),
    '<': t('view.alerts.condLt'),
    '>=': t('view.alerts.condGte'),
    '<=': t('view.alerts.condLte'),
    '=': t('view.alerts.condEq'),
  };
  return map[c] || c;
}

function channelLabel(ch) {
  const map = {
    email: t('view.alerts.channelEmail'),
    webhook: t('view.alerts.channelWebhook'),
    inline: t('view.alerts.channelInline'),
  };
  return map[ch] || ch;
}

// ── 数据加载 ──
async function loadAll() {
  loading.value = true;
  error.value = '';
  // 入站告警必须在规则读失败时也完成加载：个人版没有 /api/audit/alert/*（404），
  // 若把它排在 try 之后，整页就永远看不到已经收到的告警。
  const inboundPromise = loadInbound();
  try {
    // 2026-09-23 修复：路径原为 /api/alerts/*，但后端从未在此前缀下实现过
    // 告警规则（alerts.py 只提供 POST /api/alerts/webhook）。真正的实现在
    // audit.py 的 /api/audit/alert/*（其 docstring 明确设计为
    // "/api/audit/alert/rules — CRUD for alert rules"）。
    // 路径错配导致请求落到 SPA 兜底返回 HTML，前端报
    // "Unexpected token '<'" —— 整个页面不可用。
    const [rulesData, statsData] = await Promise.all([
      api.get('/api/audit/alert/rules'),
      api.get('/api/audit/alert/history').catch(() => null),
    ]);
    rules.value = normalizeRules(rulesData);
    // stats 可能从 history 端点或独立 stats 端点返回
    if (statsData) {
      stats.value = statsData.stats || statsData.summary || null;
      // 若 history 端点也返回了 webhooks 列表(供表单选择), 则保存
      if (statsData.webhooks) webhooks.value = statsData.webhooks;
    }
    // 加载 webhook 列表供渠道选择(失败不阻塞)
    try {
      const wh = await api.get('/api/hooks');
      // 后端返回 {hooks, count}；保留旧字段兜底以免其他部署形态空列表。
      webhooks.value = Array.isArray(wh) ? wh : (wh.hooks || wh.items || wh.webhooks || wh.data || []);
    } catch { /* webhook 列表可选 */ }
  } catch (e) {
    // 个人版没有企业版规则 API（404），那不是页面故障：当成"这里没有规则"，
    // 让下面的 Alertmanager 入站告警照常渲染。企业版下仍然报真错误。
    rules.value = [];
    if (!requiresEnterprise.value) error.value = e.message || String(e);
  } finally {
    await inboundPromise;
    loading.value = false;
  }
}

/**
 * 读 Alertmanager 推给我们的事件。这一段必须与规则 CRUD 分开失败：
 * 个人版没有 /api/audit/alert/*（404），但入站告警是有数据的；反过来入站读
 * 失败也不能把整页判成"没有告警"——空与读不到是两件事，混在一起就是假信息。
 */
async function loadInbound() {
  try {
    const d = await api.get('/api/alerts?limit=50');
    inbound.value = Array.isArray(d?.alerts) ? d.alerts : [];
    inboundTotal.value = Number(d?.total ?? inbound.value.length);
    inboundUnavailable.value = false;
  } catch {
    inbound.value = [];
    inboundTotal.value = 0;
    inboundUnavailable.value = true;
  }
}

function severityTone(s) {
  const key = String(s || '').toLowerCase();
  if (key === 'critical' || key === 'page') return 'fail';
  if (key === 'warning') return 'warn';
  if (key === 'info') return 'info';
  return 'neutral';
}

function inboundStatusLabel(s) {
  const map = {
    firing: t('view.alerts.inboundFiring'),
    resolved: t('view.alerts.alertResolved'),
  };
  return map[s] || s || '—';
}

function formatTs(ts) {
  if (!ts) return '—';
  try {
    const d = new Date(ts * 1000);
    return isNaN(d.getTime()) ? String(ts) : d.toLocaleString();
  } catch {
    return String(ts);
  }
}

function normalizeRules(d) {
  const items = Array.isArray(d) ? d : (d.items || d.rules || d.data || []);
  return items.map((r) => ({
    // AuditAlertRule keys by rule_id; reading `id` left every edit/delete
    // pointing at /api/audit/alert/rules/undefined.
    id: r.rule_id,
    name: r.name || '',
    description: r.description || '',
    metric: r.metric || '',
    // The engine keeps the operator and the value inside `condition`
    // ({op, value, window_s, filter}), not as flat columns.
    condition: (r.condition && r.condition.op) || '>=',
    threshold: r.condition ? r.condition.value : undefined,
    channel: r.channel || 'email',
    email: r.email || '',
    webhook_id: r.webhook_id || '',
    cooldown_s: r.cooldown_s || 0,
    enabled: r.enabled !== false,
  }));
}

// ── 创建/编辑 ──
function openCreate() {
  editingId.value = '';
  form.value = defaultForm();
  showForm.value = true;
}

function openEdit(r) {
  editingId.value = r.id;
  form.value = {
    name: r.name || '',
    description: r.description || '',
    metric: r.metric || 'cpu_usage',
    condition: r.condition || '>',
    threshold: r.threshold,
    channel: r.channel || 'email',
    email: r.email || '',
    webhook_id: r.webhook_id || '',
    cooldown_s: r.cooldown_s || 0,
    enabled: r.enabled !== false,
  };
  showForm.value = true;
}

function closeForm() {
  showForm.value = false;
}

function validate() {
  if (!form.value.name.trim()) {
    toast.warn(t('view.alerts.validateNameRequired'));
    return false;
  }
  if (form.value.threshold === '' || form.value.threshold === null || form.value.threshold === undefined) {
    toast.warn(t('view.alerts.validateThresholdRequired'));
    return false;
  }
  if (isNaN(Number(form.value.threshold))) {
    toast.warn(t('view.alerts.validateThresholdNumber'));
    return false;
  }
  if (form.value.channel === 'email' && !form.value.email.trim()) {
    toast.warn(t('view.alerts.validateEmailRequired'));
    return false;
  }
  if (form.value.channel === 'email' && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(form.value.email.trim())) {
    toast.warn(t('view.alerts.validateEmailInvalid'));
    return false;
  }
  if (form.value.channel === 'webhook' && !form.value.webhook_id) {
    toast.warn(t('view.alerts.validateWebhookRequired'));
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
      description: form.value.description.trim(),
      metric: form.value.metric,
      condition: form.value.condition,
      threshold: Number(form.value.threshold),
      channel: form.value.channel,
      cooldown_s: Number(form.value.cooldown_s) || 0,
      enabled: form.value.enabled,
    };
    if (form.value.channel === 'email') payload.email = form.value.email.trim();
    if (form.value.channel === 'webhook') payload.webhook_id = form.value.webhook_id;

    if (editingId.value) {
      await api.put(`/api/audit/alert/rules/${encodeURIComponent(editingId.value)}`, payload);
    } else {
      await api.post('/api/audit/alert/rules', payload);
    }
    toast.success(t('view.alerts.saved'));
    showForm.value = false;
    await loadAll();
  } catch (e) {
    toast.error(e.message || t('view.alerts.saveError'));
  } finally {
    saving.value = false;
  }
}

// ── 删除 ──
async function removeRule(r) {
  const ok = await showConfirm({ message: t('view.alerts.deleteConfirm', { name: r.name }), tone: 'danger' });
  if (!ok) return;
  try {
    await api.delete(`/api/audit/alert/rules/${encodeURIComponent(r.id)}`);
    toast.success(t('view.alerts.deleted'));
    await loadAll();
  } catch (e) {
    toast.error(e.message || t('view.alerts.deleteError'));
  }
}

// ── 告警历史 ──
async function openHistory(r) {
  historyRule.value = r;
  showHistory.value = true;
  historyLoading.value = true;
  historyRecords.value = [];
  try {
    // api.get 的第二个参数是 fetch init（只有 .headers 会被用），不是查询参数：
    // 原先写成 { rule_id: r.id } 会被静默丢掉，抽屉于是把"最近 100 条全部规则
    // 的告警"当成这条规则的历史显示。参数必须进 URL。
    const d = await api.get(
      `/api/audit/alert/history?rule_id=${encodeURIComponent(r.id)}`,
    );
    const items = d.alerts || [];
    // AuditAlert: alert_id / rule_id / triggered_at / severity / message /
    // acknowledged / metadata. There is no per-alert metric value or delivery
    // channel on the wire, so the drawer shows what the alert actually says.
    historyRecords.value = items.map((h) => ({
      id: h.alert_id,
      time: h.triggered_at,
      rule: (h.metadata && h.metadata.rule_name) || r.name,
      severity: h.severity,
      message: h.message,
      status: h.acknowledged
        ? t('view.alerts.alertAcknowledged')
        : t('view.alerts.alertFired'),
    }));
  } catch (e) {
    toast.error(e.message || t('view.alerts.historyError'));
  } finally {
    historyLoading.value = false;
  }
}

function closeHistory() {
  showHistory.value = false;
  historyRule.value = null;
  historyRecords.value = [];
}

// 个人版下不发企业版规则请求：后端必然 404，只会产生一条无意义的报错。
// 但入站告警是个人版就有的数据源，不能跟着一起跳过 —— 否则这一页在个人版
// 永远是空壳，而 data/maop.db 里其实躺着收到的告警。
onMounted(() => {
  if (requiresEnterprise.value) {
    loading.value = true;
    loadInbound().finally(() => { loading.value = false; });
    return;
  }
  loadAll();
});
</script>

<style scoped>
.alerts-page { display: flex; flex-direction: column; }

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
.btn-icon--danger:hover { color: var(--fail); border-color: var(--fail); }
.spinning { animation: maop-spin 1s linear infinite; }
@keyframes maop-spin { to { transform: rotate(360deg); } }

/* ── 提示 ── */
.al-hint {
  font-size: var(--fs-sm); color: var(--text-muted); margin: 0 0 var(--sp-3);
  line-height: 1.5;
}

/* ── 列表表格 ── */
.al-table {
  background: var(--surface); border: 1px solid var(--border); border-radius: var(--r-lg);
  overflow: hidden;
}
.al-row {
  display: grid;
  grid-template-columns: 1.3fr 1.2fr 0.6fr 0.8fr 0.9fr 0.8fr 110px;
  align-items: center;
  padding: 10px 14px;
  border-bottom: 1px solid var(--border);
  font-size: var(--fs-base);
}
.al-row:last-child { border-bottom: none; }
.al-row--head {
  background: var(--surface-2);
  font-size: var(--fs-xs); font-weight: 700; color: var(--text-faint);
  text-transform: uppercase; letter-spacing: .05em;
}
.al-cell { padding: 0 var(--sp-1); }
.al-cell--actions { display: flex; gap: 6px; justify-content: flex-end; }
.al-name { font-weight: 600; color: var(--text); }
.al-text { color: var(--text); }
.al-mono { font-family: var(--font-mono); font-size: var(--fs-sm); color: var(--text); background: var(--surface-2); padding: 1px 6px; border-radius: var(--r-sm); }
.al-num { font-variant-numeric: tabular-nums; font-family: var(--font-mono); color: var(--text); }
.al-muted { color: var(--text-muted); font-size: var(--fs-sm); }

/* ── Alertmanager 入站告警（个人版可用的唯一一块）── */
.al-inbound { margin-top: var(--sp-6); }
.al-section-title {
  display: flex; align-items: baseline; gap: var(--sp-2);
  margin: 0 0 var(--sp-1);
  font-size: var(--fs-base); font-weight: 700; color: var(--text);
}
.al-inbound__warn { color: var(--warn); }
.ai-table {
  background: var(--surface); border: 1px solid var(--border); border-radius: var(--r-lg);
  overflow: hidden;
}
.ai-row {
  display: grid;
  grid-template-columns: 1.2fr 0.7fr 0.7fr 1fr 1.6fr 1fr;
  align-items: center;
  padding: 10px 14px;
  border-bottom: 1px solid var(--border);
  font-size: var(--fs-base);
}
.ai-row:last-child { border-bottom: none; }
.ai-row--head {
  background: var(--surface-2);
  font-size: var(--fs-xs); font-weight: 700; color: var(--text-faint);
  text-transform: uppercase; letter-spacing: .05em;
}

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
.input:focus-visible { outline: none; box-shadow: 0 0 0 2px var(--brand); }

/* ── 详情面板 ── */
.detail-content { display: flex; flex-direction: column; gap: var(--sp-4); }
.detail-section { display: flex; flex-direction: column; gap: 6px; }
.detail-section__title { font-size: var(--fs-sm); font-weight: 700; color: var(--text); margin: var(--sp-2) 0 var(--sp-1); text-transform: uppercase; letter-spacing: .04em; }
.detail-value { font-size: var(--fs-base); color: var(--text); margin: 0 0 var(--sp-2); }

@media (max-width: 900px) {
  .al-row { grid-template-columns: 1fr 1fr 0.6fr 0.8fr 100px; }
  .al-cell--metric, .al-cell--channel { display: none; }
  .form-row { grid-template-columns: 1fr; }
  /* 入站表：先藏实例与接收时间，保留"哪条告警/什么级别/是否还在烧/说了什么" */
  .ai-row { grid-template-columns: 1.2fr 0.7fr 0.7fr 1.6fr; }
  .ai-row > .al-cell:nth-child(4), .ai-row > .al-cell:nth-child(6) { display: none; }
}
@media (max-width: 640px) {
  .al-row { grid-template-columns: 1fr 80px; }
  .al-cell--cond, .al-cell--threshold, .al-cell--status { display: none; }
  .ai-row { grid-template-columns: 1.2fr 0.7fr 0.7fr; }
  .ai-row > .al-cell:nth-child(5) { display: none; }
}
</style>
