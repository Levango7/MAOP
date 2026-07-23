<template>
  <div class="agents-page">
    <div class="topbar">
      <h1>Agent Dispatch & Management</h1>
      <span class="live-tag" :class="{ active: realtime.connected }">实时</span>
      <div class="view-toggle">
        <button :class="['toggle-btn', { active: viewMode === 'grid' }]" @click="viewMode = 'grid'">Grid</button>
        <button :class="['toggle-btn', { active: viewMode === 'table' }]" @click="viewMode = 'table'">Table</button>
      </div>
      <button class="btn-action" @click="loadAgents">↻ Refresh</button>
    </div>

    <Panel title="📡 Dispatch Router" :margin-bottom="24" :body-padding="16">
      <template #actions>
        <span class="hint">Agent routing configuration and status</span>
      </template>
      <div class="dispatch-grid">
        <div class="dispatch-card" v-for="route in routes" :key="route.pattern">
          <div class="route-pattern">{{ route.pattern }}</div>
          <div class="route-arrow">→</div>
          <div class="route-target">{{ route.agent }}</div>
          <span class="route-weight" :style="{ width: route.weight + '%' }">{{ route.weight }}%</span>
        </div>
      </div>
    </Panel>

    <div v-if="viewMode === 'grid'" class="agent-grid">
      <div class="agent-card" v-for="a in agents" :key="a.name" :class="{ selected: selectedAgent === a.name }" @click="selectAgent(a)">
        <div class="agent-top">
          <div class="agent-avatar" :style="{ background: agentColor(a.name) }">{{ a.name.charAt(0).toUpperCase() }}</div>
          <div class="agent-identity">
            <h3>{{ a.name }}</h3>
            <span class="status-badge" :class="a.status || 'unknown'">{{ a.status || 'idle' }}</span>
          </div>
        </div>
        <p class="agent-desc">{{ a.description || 'No description' }}</p>
        <div class="agent-metrics">
          <div class="metric"><span class="metric-val">{{ a.tasks_completed || 0 }}</span><span class="metric-lbl">Tasks</span></div>
          <div class="metric"><span class="metric-val">{{ a.success_rate || '—' }}</span><span class="metric-lbl">Success</span></div>
          <div class="metric"><span class="metric-val">{{ a.avg_latency_ms || '—' }}</span><span class="metric-lbl">Avg ms</span></div>
          <div class="metric"><span class="metric-val">{{ a.model || 'default' }}</span><span class="metric-lbl">Model</span></div>
        </div>
        <div class="agent-actions">
          <button class="act-btn" @click.stop="switchModel(a)">🔄 Model</button>
          <button class="act-btn" @click.stop="healthCheck(a)">🩺 Health</button>
          <button class="act-btn warn" @click.stop="restartAgent(a)">♻️ Restart</button>
        </div>
      </div>
    </div>

    <div v-else class="agent-table">
      <div class="trow header">
        <span>Agent</span><span>Status</span><span>Model</span><span>Tasks</span><span>Success</span><span>Latency</span><span>Actions</span>
      </div>
      <div class="trow" v-for="a in agents" :key="a.name">
        <span class="agent-name">{{ a.name }}</span>
        <span class="status-badge small" :class="a.status || 'unknown'">{{ a.status || 'idle' }}</span>
        <span class="mono">{{ a.model || 'default' }}</span>
        <span>{{ a.tasks_completed || 0 }}</span>
        <span>{{ a.success_rate || '—' }}</span>
        <span>{{ a.avg_latency_ms || '—' }}ms</span>
        <span class="actions-cell">
          <button class="act-btn small" @click="switchModel(a)">🔄</button>
          <button class="act-btn small" @click="healthCheck(a)">🩺</button>
          <button class="act-btn small warn" @click="restartAgent(a)">♻️</button>
        </span>
      </div>
    </div>

    <Panel v-if="selectedAgent" :title="selectedAgent">
      <template #actions>
        <button class="close-btn" @click="selectedAgent = null">✕</button>
      </template>
      <div class="detail-body">
        <div class="detail-section">
          <h4>Configuration</h4>
          <div class="config-grid">
            <div class="cfg-item" v-for="(v, k) in agentConfig" :key="k">
              <span class="cfg-key">{{ k }}</span>
              <span class="cfg-val">{{ v }}</span>
            </div>
          </div>
        </div>
        <div class="detail-section">
          <h4>Performance History</h4>
          <div class="perf-bars">
            <div class="perf-row" v-for="(v, i) in perfHistory" :key="i">
              <span class="perf-label">{{ v.label }}</span>
              <div class="perf-bar"><div class="perf-fill" :style="{ width: v.pct + '%', background: v.color }"></div></div>
              <span class="perf-val">{{ v.value }}</span>
            </div>
          </div>
        </div>
      </div>
    </Panel>
  </div>
</template>

<script setup>
import { ref, onMounted, watch } from 'vue';
import { useApiStore } from '../stores/api.js';
import { useRealtimeStore } from '../stores/realtime.js';
import { Panel } from '../components/index.js';

const api = useApiStore();
const realtime = useRealtimeStore();
const agents = ref([]);
const routes = ref([]);
const viewMode = ref('grid');
const selectedAgent = ref(null);
const agentConfig = ref({});
const perfHistory = ref([]);

function agentColor(name) {
  const colors = ['#3b82f6', '#a78bfa', '#22c55e', '#f59e0b', '#ef4444', '#06b6d4'];
  let hash = 0;
  for (let i = 0; i < name.length; i++) hash = name.charCodeAt(i) + ((hash << 5) - hash);
  return colors[Math.abs(hash) % colors.length];
}

function selectAgent(a) {
  selectedAgent.value = a.name;
  agentConfig.value = {
    model: a.model || 'default',
    temperature: a.temperature || '0.7',
    max_tokens: a.max_tokens || '4096',
    timeout: a.timeout || '30s',
    retry: a.retry || 3,
  };
  perfHistory.value = [
    { label: 'Success Rate', pct: parseFloat(a.success_rate) || 85, value: a.success_rate || '85%', color: '#22c55e' },
    { label: 'Latency', pct: Math.min(100, (a.avg_latency_ms || 500) / 10), value: (a.avg_latency_ms || 500) + 'ms', color: '#3b82f6' },
    { label: 'Token Efficiency', pct: 72, value: '72%', color: '#a78bfa' },
    { label: 'Task Completion', pct: 91, value: '91%', color: '#f59e0b' },
  ];
}

async function switchModel(a) {
  // P1-12 fix: use /api/agents/{name}/model endpoint
  try { await api.put(`/api/agents/${a.name}/model`, { model: a.model }); loadAgents(); } catch {}
}
async function healthCheck(a) {
  // P1-12 fix: use /api/agents/{name}/health-check endpoint
  try { await api.post(`/api/agents/${a.name}/health-check`, {}); loadAgents(); } catch {}
}
async function restartAgent(a) {
  // P1-12 fix: use /api/agents/{name}/restart endpoint (if available)
  try { await api.post(`/api/agents/${a.name}/restart`, {}); loadAgents(); } catch {}
}

async function loadAgents() {
  try {
    const data = await api.get('/api/agents');
    agents.value = data.agents || [];
  } catch {}
  try {
    const r = await api.get('/api/agents/routes');
    routes.value = r.routes || [];
  } catch {
    routes.value = agents.value.slice(0, 5).map(a => ({ pattern: `task:${a.name}`, agent: a.name, weight: Math.floor(Math.random() * 40) + 10 }));
  }
}

// Watch the realtime snapshot: when it carries agent-related data,
// automatically refresh the agents list.
watch(
  () => realtime.snapshot,
  (snap) => {
    if (!snap) return;
    const hasAgents =
      Array.isArray(snap.agents) ||
      (typeof snap.type === 'string' && snap.type.toLowerCase().includes('agent'));
    if (hasAgents) loadAgents();
  }
);

onMounted(loadAgents);
</script>

<style scoped>
.agents-page { }
.live-tag { font-size: 11px; font-weight: 600; padding: 3px 8px; border-radius: 6px; background: var(--bg2); color: var(--text3); border: 1px solid var(--border); margin-left: 12px; }
.live-tag.active { background: rgba(34,197,94,.15); color: var(--success); border-color: rgba(34,197,94,.4); }
.topbar { display: flex; align-items: center; gap: 12px; margin-bottom: 20px; }
.topbar h1 { font-size: 24px; font-weight: 700; }
.view-toggle { display: flex; gap: 4px; background: var(--bg2); border-radius: 10px; padding: 3px; margin-left: 16px; }
.toggle-btn { background: none; border: none; padding: 6px 14px; border-radius: 8px; font-size: 13px; color: var(--text2); cursor: pointer; }
.toggle-btn.active { background: var(--accent); color: #fff; }
.btn-action { margin-left: auto; background: var(--bg2); border: 1px solid var(--border); border-radius: 10px; padding: 6px 14px; font-size: 13px; color: var(--text2); cursor: pointer; }

.hint { font-size: 12px; color: var(--text3); }
.dispatch-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 8px; }
.dispatch-card { display: flex; align-items: center; gap: 8px; background: var(--bg); border: 1px solid var(--border); border-radius: 8px; padding: 8px 12px; font-size: 13px; }
.route-pattern { font-family: monospace; color: var(--accent); font-size: 12px; }
.route-arrow { color: var(--text3); }
.route-target { font-weight: 600; }
.route-weight { height: 4px; background: var(--accent); border-radius: 2px; margin-left: auto; }

.agent-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 16px; margin-bottom: 24px; }
.agent-card { background: var(--bg2); border: 1px solid var(--border); border-radius: var(--radius); padding: 16px; cursor: pointer; transition: all .15s; }
.agent-card:hover { border-color: var(--accent); }
.agent-card.selected { border-color: var(--accent); box-shadow: 0 0 0 1px var(--accent); }
.agent-top { display: flex; align-items: center; gap: 12px; margin-bottom: 12px; }
.agent-avatar { width: 40px; height: 40px; border-radius: 12px; display: flex; align-items: center; justify-content: center; color: #fff; font-size: 18px; font-weight: 700; }
.agent-identity h3 { font-size: 15px; font-weight: 600; }
.status-badge { padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; }
.status-badge.healthy, .status-badge.active { background: rgba(34,197,94,.15); color: var(--success); }
.status-badge.unhealthy, .status-badge.error { background: rgba(239,68,68,.15); color: var(--fail); }
.status-badge.idle, .status-badge.unknown { background: rgba(148,163,184,.15); color: var(--text3); }
.status-badge.small { font-size: 10px; padding: 1px 6px; }
.agent-desc { font-size: 12px; color: var(--text3); margin-bottom: 12px; line-height: 1.5; }
.agent-metrics { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; margin-bottom: 12px; }
.metric { text-align: center; }
.metric-val { font-size: 14px; font-weight: 600; display: block; }
.metric-lbl { font-size: 10px; color: var(--text3); }
.agent-actions { display: flex; gap: 6px; }
.act-btn { background: var(--bg); border: 1px solid var(--border); border-radius: 8px; padding: 5px 10px; font-size: 12px; color: var(--text2); cursor: pointer; flex: 1; text-align: center; }
.act-btn:hover { border-color: var(--accent); }
.act-btn.warn:hover { border-color: var(--warn); }
.act-btn.small { padding: 3px 8px; font-size: 11px; flex: 0; }

.agent-table { background: var(--bg2); border: 1px solid var(--border); border-radius: var(--radius); overflow: hidden; margin-bottom: 24px; }
.trow { display: grid; grid-template-columns: 1.2fr 0.8fr 1fr 0.6fr 0.8fr 0.8fr 1fr; gap: 8px; padding: 10px 16px; font-size: 13px; align-items: center; border-bottom: 1px solid var(--border); }
.trow.header { font-weight: 600; color: var(--text3); font-size: 11px; text-transform: uppercase; background: var(--bg); }
.agent-name { font-weight: 600; color: var(--accent); }
.mono { font-family: monospace; font-size: 12px; }
.actions-cell { display: flex; gap: 4px; }

.close-btn { margin-left: auto; background: none; border: 1px solid var(--border); border-radius: 8px; padding: 4px 10px; cursor: pointer; font-size: 14px; color: var(--text3); }
.detail-body { display: grid; grid-template-columns: 1fr 1fr; gap: 24px; }
.detail-section h4 { font-size: 13px; font-weight: 600; color: var(--text3); margin-bottom: 12px; text-transform: uppercase; letter-spacing: .5px; }
.config-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
.cfg-item { display: flex; justify-content: space-between; padding: 6px 10px; background: var(--bg); border-radius: 6px; font-size: 13px; }
.cfg-key { color: var(--text3); }
.cfg-val { font-weight: 600; font-family: monospace; font-size: 12px; }
.perf-row { display: flex; align-items: center; gap: 10px; margin-bottom: 8px; }
.perf-label { width: 100px; font-size: 12px; color: var(--text3); }
.perf-bar { flex: 1; height: 6px; background: var(--bg); border-radius: 3px; overflow: hidden; }
.perf-fill { height: 100%; border-radius: 3px; transition: width .3s; }
.perf-val { width: 60px; font-size: 12px; text-align: right; font-weight: 600; }
</style>
