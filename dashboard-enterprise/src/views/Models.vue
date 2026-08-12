<template>
  <div class="models-view">
    <PageHeader>
      <button class="btn-ghost" :disabled="loading" @click="loadAll">
        <AppIcon name="refresh" :size="15" /> {{ t('common.refresh') }}
      </button>
    </PageHeader>

    <!-- Registry overview -->
    <section v-if="!registryError.value" class="stat-row">
      <StatCard :label="t('view.models.stat.totalModels')" :value="registry.total_models" icon="cpu" tone="brand" :loading="loading" />
      <StatCard :label="t('view.models.enabled')" :value="registry.enabled_models" icon="check-circle" tone="success" :loading="loading" />
      <StatCard :label="t('view.models.stat.providers')" :value="registry.total_providers" icon="server" tone="info" :loading="loading" />
      <StatCard :label="t('view.models.stat.thinkingCapable')" :value="registry.thinking_capable" icon="sparkles" tone="warn" :loading="loading" />
    </section>

    <div v-if="registryError.value" class="grid-2">
      <EmptyState
icon="alert-triangle" :title="t('view.models.couldNotLoadRegistry')"
        :description="registryError.value" />
    </div>

    <!-- Model registry -->
    <Card
:title="t('view.models.modelRegistry')" icon="cpu" :margin-bottom="16"
      :subtitle="`${models.length} ` + t('view.models.registeredModels')">
      <div v-if="modelsError.value"><EmptyState icon="alert-triangle" :title="t('view.models.failedLoadModels')" :description="modelsError.value" /></div>
      <Skeleton v-else-if="loading" :lines="6" block />
      <DataTable
v-else :columns="modelCols" :rows="modelRows" :loading="false"
        :empty-text="t('view.models.noModels')" />
    </Card>

    <!-- Providers -->
    <Card
:title="t('view.models.providerHealth')" icon="activity" :margin-bottom="16"
      :subtitle="`${providers.length} ` + t('view.models.providersLabel')">
      <div v-if="providersError.value"><EmptyState icon="alert-triangle" :title="t('view.models.failedLoadProviders')" :description="providersError.value" /></div>
      <Skeleton v-else-if="loading && !providers.length" :lines="5" block />
      <DataTable v-else :columns="providerCols" :rows="providerRows" :empty-text="t('view.models.noProviders')" />
    </Card>

    <!-- Agents -->
    <Card
:title="t('view.models.agentDrivers')" icon="bot" :margin-bottom="16"
      :subtitle="`${agents.length} ` + t('view.models.agentsLabel')">
      <div v-if="agentsError.value"><EmptyState icon="alert-triangle" :title="t('view.models.failedLoadAgents')" :description="agentsError.value" /></div>
      <Skeleton v-else-if="loading && !agents.length" :lines="5" block />
      <DataTable v-else :columns="agentCols" :rows="agentRows" :empty-text="t('view.models.noAgents')" />
    </Card>

    <div class="grid-2">
      <!-- Budget -->
      <Card :title="t('view.models.budget')" icon="dollar" :margin-bottom="16">
        <div v-if="budgetError.value"><EmptyState icon="alert-triangle" :title="t('view.models.failedLoadBudget')" :description="budgetError.value" /></div>
        <Skeleton v-else-if="loading && !budget.data" :lines="5" block />
        <div v-else-if="budget.data" class="metric-grid">
          <div class="metric"><span class="metric-k">{{ t('view.models.metric.daily') }}</span><span class="metric-v">${{ fmt(budget.data.daily_spend) }} <em>/ ${{ fmt(budget.data.daily_limit) }}</em></span></div>
          <div class="metric"><span class="metric-k">{{ t('view.models.metric.monthly') }}</span><span class="metric-v">${{ fmt(budget.data.monthly_spend) }} <em>/ ${{ fmt(budget.data.monthly_limit) }}</em></span></div>
          <div class="metric"><span class="metric-k">{{ t('view.models.metric.utilization') }}</span><span class="metric-v">{{ pct(budget.data.daily_utilization) }}%</span></div>
          <div class="metric"><span class="metric-k">{{ t('view.models.metric.alertAt') }}</span><span class="metric-v">{{ pct(budget.data.alert_threshold) }}%</span></div>
          <div class="metric"><span class="metric-k">{{ t('view.models.metric.hardStop') }}</span><span class="metric-v"><Badge :tone="budget.data.hard_stop ? 'fail' : 'neutral'">{{ budget.data.hard_stop ? t('view.models.enabled') : t('view.models.disabled') }}</Badge></span></div>
        </div>
        <EmptyState v-else icon="info" :title="t('view.models.noBudgetData')" />
      </Card>

      <!-- CLI availability (real /api/model/quota shape) -->
      <Card :title="t('view.models.agentCliAvailability')" icon="wrench" :margin-bottom="16">
        <div v-if="quotaError.value"><EmptyState icon="alert-triangle" :title="t('view.models.failedLoadAvailability')" :description="quotaError.value" /></div>
        <Skeleton v-else-if="loading && !quota.rows.length" :lines="5" block />
        <DataTable v-else :columns="quotaCols" :rows="quota.rows" :empty-text="t('view.models.noAvailability')" />
      </Card>
    </div>

    <!-- Routing policies -->
    <Card
:title="t('view.models.routingPolicies')" icon="route" :margin-bottom="16"
      :subtitle="`${policies.length} ` + t('view.models.policiesLabel')">
      <div v-if="policiesError.value"><EmptyState icon="alert-triangle" :title="t('view.models.failedLoadPolicies')" :description="policiesError.value" /></div>
      <Skeleton v-else-if="loading && !policies.length" :lines="4" block />
      <DataTable v-else :columns="policyCols" :rows="policyRows" :empty-text="t('view.models.noPolicies')" />
    </Card>

    <!-- Model switch -->
    <Card :title="t('view.models.modelSwitch')" icon="refresh" :subtitle="t('view.models.modelSwitchSub')">
      <form class="switch-form" @submit.prevent="doSwitch">
        <div class="switch-fields">
          <div class="field">
            <label>{{ t('view.models.agent') }}</label>
            <select v-model="switchForm.agent" :disabled="loading" class="switch-select">
              <option value="" disabled>{{ t('view.models.selectAgent') }}</option>
              <option v-for="a in agents" :key="a.name" :value="a.name">{{ a.name }}</option>
            </select>
          </div>
          <div class="field">
            <label>{{ t('common.model') }}</label>
            <select v-model="switchForm.model" :disabled="loading" class="switch-select">
              <option value="" disabled>{{ t('view.models.selectModel') }}</option>
              <option v-for="m in models" :key="m.name" :value="m.name">{{ m.name }} · {{ m.provider }}</option>
            </select>
          </div>
        </div>
        <div class="switch-actions">
          <button type="submit" class="btn btn--primary" :disabled="switching || !switchForm.agent || !switchForm.model">
            <AppIcon v-if="switching" name="refresh" :size="14" :class="{ spinning: switching }" />
            {{ switching ? t('view.models.switching') : t('view.models.switchModel') }}
          </button>
          <span v-if="switchResult" class="switch-result" :class="switchResult.ok ? 'is-ok' : 'is-fail'">
            <AppIcon :name="switchResult.ok ? 'check-circle' : 'x-circle'" :size="14" /> {{ switchResult.msg }}
          </span>
        </div>
      </form>
    </Card>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onUnmounted } from 'vue';
import { useApiStore } from '../stores/api.js';
import { useI18n } from '../i18n';
import { Card, StatCard, Badge, DataTable, Skeleton, EmptyState, AppIcon, PageHeader } from '../components/index.js';

const api = useApiStore();
const { t } = useI18n();

const loading = ref(true);
const switching = ref(false);
const switchForm = reactive({ agent: '', model: '' });
const switchResult = ref(null);
let _switchTimer = null;

const models = ref([]);
const modelsError = ref('');
const providers = ref([]);
const providersError = ref('');
const agents = ref([]);
const agentsError = ref('');
const quota = reactive({ rows: [], error: '' });
const quotaError = ref('');
const policies = ref([]);
const policiesError = ref('');
const budget = reactive({ data: null, error: '' });
const budgetError = ref('');
const registry = reactive({ error: '', total_models: 0, enabled_models: 0, total_providers: 0, thinking_capable: 0 });
const registryError = ref('');

const fmt = (n) => (n === null ? '0.00' : Number(n).toFixed(2));
const pct = (n) => (n === null || n === undefined ? 0 : Number(n) > 1 ? Math.round(Number(n)) : Math.round(Number(n) * 100));

/* ---- columns ---- */
const modelCols = [
  { key: 'name', label: t('common.name') },
  { key: 'provider', label: t('view.models.col.provider') },
  { key: 'family', label: t('view.models.col.family') },
  { key: 'context_window', label: t('view.models.col.ctx'), align: 'right', type: 'num' },
  { key: 'quality_tier', label: t('view.models.col.quality') },
  { key: 'latency_tier', label: t('common.latency') },
  { key: 'provider_healthy', label: t('view.models.col.health'), type: 'badge' },
  { key: 'enabled', label: t('view.models.enabled'), type: 'badge' },
];
const providerCols = [
  { key: 'name', label: t('common.name') },
  { key: 'type', label: t('common.type') },
  { key: 'protocol', label: t('view.models.col.protocol') },
  { key: 'healthy', label: t('view.models.col.health'), type: 'badge' },
  { key: 'has_key', label: t('view.models.col.apiKey'), type: 'badge' },
  { key: 'enabled', label: t('view.models.enabled'), type: 'badge' },
];
const agentCols = [
  { key: 'name', label: t('common.name') },
  { key: 'driver', label: t('common.driver') },
  { key: 'model', label: t('common.model') },
  { key: 'capabilities', label: t('common.capabilities') },
  { key: 'cli_available', label: t('view.models.col.cli'), type: 'badge' },
];
const quotaCols = [
  { key: 'agent', label: t('view.models.agent') },
  { key: 'model', label: t('common.model') },
  { key: 'driver', label: t('common.driver') },
  { key: 'available', label: t('view.models.col.available'), type: 'badge' },
];
const policyCols = [
  { key: 'name', label: t('common.name') },
  { key: 'strategy', label: t('view.models.col.strategy') },
  { key: 'max_cost_per_task', label: t('view.models.col.maxTask'), align: 'right', type: 'num' },
  { key: 'prefer_low_latency', label: t('view.models.col.lowLatency'), type: 'badge' },
  { key: 'fallback_on_error', label: t('view.models.col.fallback'), type: 'badge' },
];

/* ---- row transforms (status booleans -> tone-friendly strings) ---- */
const modelRows = computed(() => models.value.map(m => ({
  name: m.name,
  provider: m.provider || '—',
  family: m.family || '—',
  context_window: m.context_window ?? '—',
  quality_tier: m.quality_tier || '—',
  latency_tier: m.latency_tier || '—',
  provider_healthy: m.provider_healthy ? 'healthy' : 'unhealthy',
  enabled: m.enabled ? 'enabled' : 'disabled',
})));
const providerRows = computed(() => providers.value.map(p => ({
  name: p.name,
  type: p.type || '—',
  protocol: p.protocol || '—',
  healthy: p.healthy ? 'healthy' : 'unhealthy',
  has_key: p.has_api_key ? 'yes' : 'no',
  enabled: p.enabled ? 'enabled' : 'disabled',
})));
const agentRows = computed(() => agents.value.map(a => ({
  name: a.name,
  driver: a.driver || '—',
  model: a.model || '—',
  capabilities: (a.capabilities || []).join(', ') || '—',
  cli_available: a.cli_available ? 'yes' : 'no',
})));
const policyRows = computed(() => policies.value.map(p => ({
  name: p.name,
  strategy: p.strategy || '—',
  max_cost_per_task: p.max_cost_per_task !== null && p.max_cost_per_task !== undefined ? `$${Number(p.max_cost_per_task).toFixed(3)}` : '—',
  prefer_low_latency: p.prefer_low_latency ? 'yes' : 'no',
  fallback_on_error: p.fallback_on_error ? 'yes' : 'no',
})));

async function loadRegistry() {
  try {
    const d = await api.get('/api/model/registry');
    const s = d.stats || {};
    registry.total_models = s.total_models ?? 0;
    registry.enabled_models = s.enabled_models ?? 0;
    registry.total_providers = s.total_providers ?? 0;
    registry.thinking_capable = s.thinking_capable ?? 0;
    registryError.value = '';
  } catch (e) { registryError.value = e.message || 'Registry unavailable'; }
}
async function loadModels() {
  try { const d = await api.get('/api/model/list'); models.value = d.models || []; modelsError.value = ''; }
  catch (e) { modelsError.value = e.message || 'Models unavailable'; }
}
async function loadProviders() {
  try { const d = await api.get('/api/model/providers'); providers.value = d.providers || []; providersError.value = ''; }
  catch (e) { providersError.value = e.message || 'Providers unavailable'; }
}
async function loadAgents() {
  try { const d = await api.get('/api/model/agents'); agents.value = d.agents || []; agentsError.value = ''; }
  catch (e) { agentsError.value = e.message || 'Agents unavailable'; }
}
async function loadQuota() {
  try {
    const d = await api.get('/api/model/quota');
    const list = d.agents || [];
    quota.rows = list.map(a => ({
      agent: a.agent || a.name || '—',
      model: a.model || '—',
      driver: a.driver || '—',
      available: a.available ? 'ok' : 'fail',
    }));
    quotaError.value = '';
  } catch (e) { quotaError.value = e.message || 'Availability unavailable'; }
}
async function loadPolicies() {
  try { const d = await api.get('/api/model/policies'); policies.value = d.policies || []; policiesError.value = ''; }
  catch (e) { policiesError.value = e.message || 'Policies unavailable'; }
}
async function loadBudget() {
  try { const d = await api.get('/api/model/budget'); budget.data = d.budget || null; budgetError.value = ''; }
  catch (e) { budgetError.value = e.message || 'Budget unavailable'; }
}

async function loadAll() {
  loading.value = true;
  await Promise.allSettled([
    loadRegistry(), loadModels(), loadProviders(), loadAgents(),
    loadQuota(), loadPolicies(), loadBudget(),
  ]);
  loading.value = false;
}

async function doSwitch() {
  if (!switchForm.agent || !switchForm.model) return;
  switching.value = true;
  switchResult.value = null;
  try {
    await api.post('/api/model/switch', { agent: switchForm.agent, model: switchForm.model });
    switchResult.value = { ok: true, msg: t('view.models.switchSuccess') };
  } catch (e) {
    switchResult.value = { ok: false, msg: e.message || t('view.models.switchFailed') };
  }
  switching.value = false;
  _switchTimer = setTimeout(() => { switchResult.value = null; }, 4000);
}

onMounted(loadAll);

onUnmounted(() => { if (_switchTimer) clearTimeout(_switchTimer); });
</script>

<style scoped>
</style>
