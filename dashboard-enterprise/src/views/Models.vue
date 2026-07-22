<template>
  <div class="models-page">
    <div class="topbar">
      <h1>Models & Performance</h1>
      <button class="btn-action" @click="loadAll">↻ Refresh</button>
    </div>

    <div class="two-col">
      <div class="panel">
        <h3>Model Registry</h3>
        <div class="model-list" v-if="models.length">
          <div class="model-row" v-for="m in models" :key="m.id || m.name">
            <div class="model-id">
              <span class="model-name">{{ m.name }}</span>
              <span class="model-provider">{{ m.provider || m.vendor || '' }}</span>
            </div>
            <span class="status-badge" :class="modelStatusClass(m)">{{ m.status || 'available' }}</span>
          </div>
        </div>
        <div class="empty" v-else>No models loaded</div>
      </div>

      <div class="panel">
        <h3>Provider Health</h3>
        <div class="provider-list" v-if="providers.length">
          <div class="provider-row" v-for="p in providers" :key="p.name || p.id">
            <span class="provider-name">{{ p.name }}</span>
            <span class="status-badge" :class="p.healthy !== false ? 'healthy' : 'unhealthy'">{{ p.healthy !== false ? 'healthy' : 'unhealthy' }}</span>
            <span class="provider-latency" v-if="p.latency_ms">{{ p.latency_ms }}ms</span>
            <span class="provider-models" v-if="p.models">{{ p.models.length || p.models }} models</span>
          </div>
        </div>
        <div class="empty" v-else>No providers loaded</div>
      </div>
    </div>

    <div class="panel">
      <h3>Model Switch</h3>
      <div class="switch-form">
        <div class="form-group">
          <label>Agent</label>
          <select v-model="switchForm.agent">
            <option value="" disabled>Select agent</option>
            <option v-for="a in agents" :key="a.name || a.agent" :value="a.name || a.agent">{{ a.name || a.agent }}</option>
          </select>
        </div>
        <div class="form-group">
          <label>Model</label>
          <select v-model="switchForm.model">
            <option value="" disabled>Select model</option>
            <option v-for="m in models" :key="m.id || m.name" :value="m.id || m.name">{{ m.name }}</option>
          </select>
        </div>
        <button class="btn-primary" @click="doSwitch" :disabled="switching || !switchForm.agent || !switchForm.model">
          {{ switching ? 'Switching...' : 'Switch Model' }}
        </button>
        <span class="switch-result" v-if="switchResult" :class="switchResult.ok ? 'ok' : 'fail'">{{ switchResult.msg }}</span>
      </div>
    </div>

    <div class="two-col">
      <div class="panel">
        <h3>Quota</h3>
        <div class="quota-grid" v-if="quota">
          <div class="quota-item" v-for="(v, k) in quotaDisplay" :key="k">
            <span class="quota-key">{{ k }}</span>
            <span class="quota-val">{{ v }}</span>
          </div>
        </div>
        <div class="empty" v-else>No quota data</div>
      </div>

      <div class="panel">
        <h3>Budget</h3>
        <div class="budget-grid" v-if="budget">
          <div class="budget-item" v-for="(v, k) in budgetDisplay" :key="k">
            <span class="budget-key">{{ k }}</span>
            <span class="budget-val">{{ v }}</span>
          </div>
        </div>
        <div class="empty" v-else>No budget data</div>
      </div>
    </div>

    <div class="panel">
      <h3>Routing Policies</h3>
      <div class="policy-table" v-if="policies.length">
        <div class="policy-header">
          <span>Name</span><span>Pattern</span><span>Target</span><span>Priority</span><span>Enabled</span>
        </div>
        <div class="policy-row" v-for="p in policies" :key="p.name || p.id">
          <span class="policy-name">{{ p.name }}</span>
          <span class="mono">{{ p.pattern || p.match || '—' }}</span>
          <span>{{ p.target || p.model || '—' }}</span>
          <span>{{ p.priority ?? '—' }}</span>
          <span class="status-badge small" :class="p.enabled !== false ? 'healthy' : 'unhealthy'">{{ p.enabled !== false ? 'on' : 'off' }}</span>
        </div>
      </div>
      <div class="empty" v-else>No policies configured</div>
    </div>

    <div class="panel">
      <h3>Performance Metrics</h3>
      <div class="perf-grid" v-if="timeseries.length">
        <div class="perf-chart">
          <svg viewBox="0 0 600 120" preserveAspectRatio="none" class="perf-svg">
            <defs>
              <linearGradient id="perfGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stop-color="var(--accent)" stop-opacity="0.3" />
                <stop offset="100%" stop-color="var(--accent)" stop-opacity="0" />
              </linearGradient>
            </defs>
            <polygon :points="areaPoints" fill="url(#perfGrad)" />
            <polyline :points="linePoints" fill="none" stroke="var(--accent)" stroke-width="2" />
          </svg>
        </div>
        <div class="perf-stats">
          <div class="perf-stat" v-for="s in perfSummary" :key="s.label">
            <span class="perf-stat-val">{{ s.value }}</span>
            <span class="perf-stat-lbl">{{ s.label }}</span>
          </div>
        </div>
      </div>
      <div class="empty" v-else>No performance data</div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue';
import { useApiStore } from '../stores/api.js';

const api = useApiStore();

const models = ref([]);
const providers = ref([]);
const agents = ref([]);
const quota = ref(null);
const budget = ref(null);
const policies = ref([]);
const timeseries = ref([]);

const switchForm = ref({ agent: '', model: '' });
const switching = ref(false);
const switchResult = ref(null);

function modelStatusClass(m) {
  if (m.status === 'error' || m.status === 'unavailable') return 'unhealthy';
  if (m.status === 'deprecated') return 'warn';
  return 'healthy';
}

const quotaDisplay = computed(() => {
  if (!quota.value) return {};
  const q = quota.value;
  return {
    'Limit': q.limit ?? q.total ?? '—',
    'Used': q.used ?? q.consumed ?? '—',
    'Remaining': q.remaining ?? q.left ?? '—',
    'Period': q.period ?? q.window ?? '—',
  };
});

const budgetDisplay = computed(() => {
  if (!budget.value) return {};
  const b = budget.value;
  return {
    'Total': b.total ?? b.budget ?? '—',
    'Spent': b.spent ?? b.used ?? '—',
    'Remaining': b.remaining ?? b.left ?? '—',
    'Alert': b.alert_threshold ? (b.alert_threshold + '%') : '—',
  };
});

const linePoints = computed(() => {
  if (!timeseries.value.length) return '';
  const vals = timeseries.value.map(t => t.value ?? t.count ?? t.requests ?? 0);
  const max = Math.max(...vals, 1);
  return vals.map((v, i) => `${(i / (vals.length - 1 || 1)) * 600},${120 - (v / max) * 100}`).join(' ');
});

const areaPoints = computed(() => {
  if (!linePoints.value) return '';
  return `0,120 ${linePoints.value} 600,120`;
});

const perfSummary = computed(() => {
  if (!timeseries.value.length) return [];
  const vals = timeseries.value.map(t => t.value ?? t.count ?? t.requests ?? 0);
  const avg = (vals.reduce((a, b) => a + b, 0) / vals.length).toFixed(1);
  const peak = Math.max(...vals);
  const last = vals[vals.length - 1];
  return [
    { label: 'Avg', value: avg },
    { label: 'Peak', value: peak },
    { label: 'Latest', value: last },
  ];
});

async function doSwitch() {
  if (!switchForm.value.agent || !switchForm.value.model) return;
  switching.value = true;
  switchResult.value = null;
  try {
    await api.post('/api/model/switch', { agent: switchForm.value.agent, model: switchForm.value.model });
    switchResult.value = { ok: true, msg: 'Switched successfully' };
    loadAgents();
  } catch (e) {
    switchResult.value = { ok: false, msg: e.message || 'Switch failed' };
  }
  switching.value = false;
  setTimeout(() => { switchResult.value = null; }, 4000);
}

async function loadModels() {
  try { const d = await api.get('/api/model/list'); models.value = d.models || d || []; } catch {}
  try { const d = await api.get('/api/model/registry'); if (d.models || d.registry) models.value = d.models || d.registry || models.value; } catch {}
}

async function loadProviders() {
  try { const d = await api.get('/api/model/providers'); providers.value = d.providers || d || []; } catch {}
}

async function loadAgents() {
  try { const d = await api.get('/api/model/agents'); agents.value = d.agents || d || []; } catch {}
}

async function loadQuota() {
  try { const d = await api.get('/api/model/quota'); quota.value = d.quota || d; } catch {}
}

async function loadBudget() {
  try { const d = await api.get('/api/model/budget'); budget.value = d.budget || d; } catch {}
}

async function loadPolicies() {
  try { const d = await api.get('/api/model/policies'); policies.value = d.policies || d || []; } catch {}
}

async function loadTimeseries() {
  try {
    const d = await api.get('/api/overview');
    timeseries.value = d.timeseries || d.metrics || [];
  } catch {}
}

function loadAll() {
  loadModels();
  loadProviders();
  loadAgents();
  loadQuota();
  loadBudget();
  loadPolicies();
  loadTimeseries();
}

onMounted(loadAll);
</script>

<style scoped>
.models-page { }
.topbar { display: flex; align-items: center; gap: 12px; margin-bottom: 20px; }
.topbar h1 { font-size: 24px; font-weight: 700; }
.btn-action { margin-left: auto; background: var(--bg2); border: 1px solid var(--border); border-radius: 10px; padding: 6px 14px; font-size: 13px; color: var(--text2); cursor: pointer; }

.two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 16px; }
.panel { background: var(--bg2); border: 1px solid var(--border); border-radius: var(--radius); padding: 20px; margin-bottom: 16px; }
.panel h3 { font-size: 14px; font-weight: 600; color: var(--text2); margin-bottom: 16px; }

.model-row { display: flex; align-items: center; justify-content: space-between; padding: 8px 12px; border-bottom: 1px solid var(--border); }
.model-row:last-child { border-bottom: none; }
.model-id { display: flex; align-items: center; gap: 10px; }
.model-name { font-weight: 600; font-size: 13px; }
.model-provider { font-size: 11px; color: var(--text2); background: var(--bg3); padding: 1px 8px; border-radius: 4px; }

.provider-row { display: flex; align-items: center; gap: 10px; padding: 8px 12px; border-bottom: 1px solid var(--border); }
.provider-row:last-child { border-bottom: none; }
.provider-name { font-weight: 600; font-size: 13px; min-width: 100px; }
.provider-latency { font-size: 11px; color: var(--text2); font-family: monospace; }
.provider-models { font-size: 11px; color: var(--text2); margin-left: auto; }

.status-badge { padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; }
.status-badge.healthy { background: rgba(34,197,94,.15); color: var(--success); }
.status-badge.unhealthy { background: rgba(239,68,68,.15); color: var(--fail); }
.status-badge.warn { background: rgba(245,158,11,.15); color: var(--warn); }
.status-badge.small { font-size: 10px; padding: 1px 6px; }

.switch-form { display: flex; align-items: flex-end; gap: 16px; flex-wrap: wrap; }
.form-group { display: flex; flex-direction: column; gap: 4px; }
.form-group label { font-size: 11px; color: var(--text2); font-weight: 600; text-transform: uppercase; letter-spacing: .5px; }
.form-group select { background: var(--bg); border: 1px solid var(--border); border-radius: 8px; padding: 8px 12px; font-size: 13px; color: var(--text1); min-width: 180px; }
.btn-primary { background: var(--accent); color: #fff; border: none; border-radius: 8px; padding: 8px 20px; font-size: 13px; font-weight: 600; cursor: pointer; }
.btn-primary:disabled { opacity: .5; cursor: not-allowed; }
.switch-result { font-size: 12px; font-weight: 600; }
.switch-result.ok { color: var(--success); }
.switch-result.fail { color: var(--fail); }

.quota-grid, .budget-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
.quota-item, .budget-item { display: flex; justify-content: space-between; padding: 8px 12px; background: var(--bg); border-radius: 6px; font-size: 13px; }
.quota-key, .budget-key { color: var(--text2); }
.quota-val, .budget-val { font-weight: 600; font-family: monospace; font-size: 12px; }

.policy-table { }
.policy-header { display: grid; grid-template-columns: 1.2fr 1.5fr 1fr 0.6fr 0.6fr; gap: 8px; padding: 8px 12px; font-size: 11px; font-weight: 600; color: var(--text2); text-transform: uppercase; letter-spacing: .5px; border-bottom: 1px solid var(--border); }
.policy-row { display: grid; grid-template-columns: 1.2fr 1.5fr 1fr 0.6fr 0.6fr; gap: 8px; padding: 8px 12px; font-size: 13px; align-items: center; border-bottom: 1px solid var(--border); }
.policy-row:last-child { border-bottom: none; }
.policy-name { font-weight: 600; color: var(--accent); }
.mono { font-family: monospace; font-size: 12px; }

.perf-grid { display: grid; grid-template-columns: 1fr 200px; gap: 16px; align-items: center; }
.perf-chart { }
.perf-svg { width: 100%; height: 120px; }
.perf-stats { display: flex; flex-direction: column; gap: 8px; }
.perf-stat { text-align: center; padding: 8px; background: var(--bg); border-radius: 8px; }
.perf-stat-val { font-size: 18px; font-weight: 700; display: block; }
.perf-stat-lbl { font-size: 10px; color: var(--text2); text-transform: uppercase; }

.empty { font-size: 13px; color: var(--text2); padding: 16px; text-align: center; }
</style>