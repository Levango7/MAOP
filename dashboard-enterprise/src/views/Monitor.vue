<template>
  <div class="monitor-page">
    <div class="topbar">
      <h1>Monitor & Maintenance</h1>
      <div class="tab-bar">
        <button v-for="t in tabs" :key="t.key" :class="['tab-btn', { active: activeTab === t.key }]" @click="activeTab = t.key">{{ t.icon }} {{ t.label }}</button>
      </div>
      <span class="sse-indicator" :class="sse.connected.value ? 'on' : 'off'" :title="sse.connected.value ? 'SSE connected' : 'SSE disconnected'">
        <span class="sse-dot"></span>
        <span class="sse-text">SSE</span>
      </span>
    </div>

    <div v-if="activeTab === 'monitor'">
      <div class="metrics-grid">
        <StatCard
          v-for="m in metrics"
          :key="m.label"
          :label="m.label"
          :value="m.value"
          :icon="m.icon"
          :icon-bg="m.bg"
          :sparkline="m.sparkline"
          :sparkline-color="m.color"
        />
      </div>

      <div class="two-col">
        <Panel title="Live Agent Status" :margin-bottom="0">
          <div class="agent-status-list">
            <div class="agent-row" v-for="a in agentStatuses" :key="a.name">
              <span class="agent-dot" :style="{ background: a.healthy ? 'var(--success)' : 'var(--fail)' }"></span>
              <span class="agent-name">{{ a.name }}</span>
              <span class="agent-queue">Queue: {{ a.queue }}</span>
              <div class="agent-bar"><div class="bar-fill" :style="{ width: a.load + '%', background: a.load > 80 ? 'var(--warn)' : 'var(--accent)' }"></div></div>
              <span class="agent-load">{{ a.load }}%</span>
            </div>
          </div>
        </Panel>

        <Panel title="System Resources" :margin-bottom="0">
          <div class="resource-list">
            <!-- C-7 修复：加载中状态 -->
            <div v-if="resourcesLoading" class="resource-loading">Loading...</div>
            <!-- C-7 修复：加载失败或无数据时显示失败提示，避免假数据 -->
            <div v-else-if="!resources.length" class="resource-loading resource-loading-fail">Failed to load</div>
            <div class="resource-item" v-for="r in resources" :key="r.name">
              <span class="res-name">{{ r.name }}</span>
              <div class="res-bar"><div class="res-fill" :style="{ width: r.pct + '%', background: r.pct > 80 ? 'var(--fail)' : r.pct > 60 ? 'var(--warn)' : 'var(--success)' }"></div></div>
              <span class="res-val">{{ r.used }} / {{ r.total }}</span>
              <span class="res-pct">{{ r.pct }}%</span>
            </div>
          </div>
        </Panel>
      </div>

      <Panel title="Event Stream">
        <template #actions>
          <span v-if="sseEvents.length" class="event-count">{{ sseEvents.length }}</span>
        </template>
        <div class="event-list" ref="eventList">
          <div v-for="e in sseEvents" :key="e.id" class="event-row" :class="e.level">
            <span class="event-time">{{ e.time }}</span>
            <span class="event-level">{{ e.level }}</span>
            <span class="event-msg">{{ e.message }}</span>
          </div>
          <div v-if="!sseEvents.length" class="event-row info">
            <span class="event-time">—</span>
            <span class="event-level">SSE</span>
            <span class="event-msg">Waiting for /api/stream events…</span>
          </div>
        </div>
      </Panel>
    </div>

    <div v-if="activeTab === 'maintenance'">
      <div class="maint-grid">
        <div class="maint-card" v-for="m in maintActions" :key="m.title" @click="runMaint(m)">
          <div class="maint-icon">{{ m.icon }}</div>
          <h4>{{ m.title }}</h4>
          <p>{{ m.desc }}</p>
          <span class="maint-status" :class="m.status">{{ m.statusText }}</span>
        </div>
      </div>

      <Panel title="System Diagnostics">
        <div class="diag-list">
          <!-- C-7 修复：加载中状态 -->
          <div v-if="diagnosticsLoading" class="diag-loading">Loading...</div>
          <!-- C-7 修复：加载失败或无数据时显示失败提示，避免假数据 -->
          <div v-else-if="!diagnostics.length" class="diag-loading diag-loading-fail">Failed to load</div>
          <div class="diag-item" v-for="d in diagnostics" :key="d.name">
            <span class="diag-dot" :style="{ background: d.ok ? 'var(--success)' : 'var(--fail)' }"></span>
            <span class="diag-name">{{ d.name }}</span>
            <span class="diag-result" :class="{ 'diag-result-fail': !d.ok }">{{ d.result }}</span>
            <span class="diag-detail" v-if="d.detail">{{ d.detail }}</span>
          </div>
        </div>
      </Panel>
    </div>
  </div>
</template>

<script setup>
import { ref, watch, onMounted, onUnmounted } from 'vue';
import { useApiStore } from '../stores/api.js';
import { useSSE } from '../composables/useSSE.js';
import { StatCard, Panel } from '../components/index.js';

const api = useApiStore();
const activeTab = ref('monitor');
const eventList = ref(null);
let pollTimer = null;

// t21: SSE live updates from /api/stream (provider.py:306 pushes `state` events
// containing agents / success_rate / delegations). Polling remains as fallback
// for metrics the SSE endpoint doesn't push.
const sse = useSSE({ url: '/api/stream', events: ['state'] });
const sseEvents = ref([]);
let sseEventCounter = 0;

watch(() => sse.lastEvent.value, (payload) => {
  if (payload == null) return;
  sseEventCounter += 1;
  const ts = new Date();
  const time = ts.toLocaleTimeString();
  // The backend's /api/stream pushes: { event: 'state', agents, success_rate, delegations }
  const agents = typeof payload.agents === 'number' ? payload.agents : null;
  const successRate = typeof payload.success_rate === 'number' ? payload.success_rate : null;
  const delegations = typeof payload.delegations === 'number' ? payload.delegations : null;
  const parts = [];
  if (agents !== null) parts.push(`agents=${agents}`);
  if (successRate !== null) parts.push(`success=${successRate}%`);
  if (delegations !== null) parts.push(`delegations=${delegations}`);
  const message = parts.length ? `state: ${parts.join(', ')}` : JSON.stringify(payload);
  sseEvents.value.unshift({ id: sseEventCounter, time, level: 'info', message });
  // Update the "Active Agents" metric in real time when the SSE payload includes it.
  if (agents !== null) metrics.value[1].value = String(agents);
  // Cap the event log to avoid unbounded growth.
  if (sseEvents.value.length > 50) sseEvents.value = sseEvents.value.slice(0, 50);
}, { deep: true });

watch(() => sse.error.value, (err) => {
  if (!err) return;
  sseEventCounter += 1;
  sseEvents.value.unshift({
    id: sseEventCounter,
    time: new Date().toLocaleTimeString(),
    level: 'error',
    message: `SSE error: ${err.type || 'unknown'} (will reconnect)`,
  });
  if (sseEvents.value.length > 50) sseEvents.value = sseEvents.value.slice(0, 50);
});

const tabs = [
  { key: 'monitor', label: 'Monitor', icon: '📈' },
  { key: 'maintenance', label: 'Maintenance', icon: '🔧' },
];

const metrics = ref([
  { icon: '⚡', label: 'Requests/min', value: '0', bg: 'rgba(59,130,246,.12)', color: '#3b82f6', sparkline: '' },
  { icon: '🤖', label: 'Active Agents', value: '0', bg: 'rgba(167,139,250,.12)', color: '#a78bfa', sparkline: '' },
  { icon: '📝', label: 'Queue Depth', value: '0', bg: 'rgba(249,115,22,.12)', color: '#f59e0b', sparkline: '' },
  { icon: '💰', label: 'Cost/hr', value: '$0.00', bg: 'rgba(34,197,94,.12)', color: '#22c55e', sparkline: '' },
]);

const agentStatuses = ref([]);

// C-7 修复：System Resources 与 System Diagnostics 改为运行时从后端拉取，
// 不再使用硬编码假数据。初始为空数组，加载中/失败时由模板展示对应状态。
const resources = ref([]);
const resourcesLoading = ref(true);

const diagnostics = ref([]);
const diagnosticsLoading = ref(true);

const maintActions = ref([
  { icon: '🗑️', title: 'Prune Memory', desc: 'Remove stale memory entries older than 30 days', status: 'idle', statusText: 'Ready' },
  { icon: '💾', title: 'Backup Database', desc: 'Create a timestamped backup of maop.db', status: 'idle', statusText: 'Ready' },
  { icon: '🔄', title: 'Reload Config', desc: 'Hot-reload agents.yaml and settings', status: 'idle', statusText: 'Ready' },
  { icon: '🧹', title: 'Clear Cache', desc: 'Flush all LRU cache entries', status: 'idle', statusText: 'Ready' },
  { icon: '🔍', title: 'Rebuild Vector Index', desc: 'Re-index all vector entries', status: 'idle', statusText: 'Ready' },
  { icon: '📊', title: 'Compact Database', desc: 'VACUUM SQLite to reclaim space', status: 'idle', statusText: 'Ready' },
]);

function genSparkline() {
  const pts = [];
  for (let i = 0; i < 20; i++) pts.push(`${i * 5},${30 - Math.random() * 25}`);
  return pts.join(' ');
}

// C-7 修复：从后端 /api/system/resources 与 /api/system/diagnostics 拉取真实数据。
// 任一接口失败均降级为空数组，由模板展示 "Failed to load"，避免显示假数据。
async function loadSystemStats() {
  resourcesLoading.value = true;
  diagnosticsLoading.value = true;

  // 并行拉取两个接口；任一失败返回 null，便于后续判断
  const [resRes, diagRes] = await Promise.all([
    fetch('/api/system/resources').then(r => r.ok ? r.json() : Promise.reject(new Error('HTTP ' + r.status))).catch(() => null),
    fetch('/api/system/diagnostics').then(r => r.ok ? r.json() : Promise.reject(new Error('HTTP ' + r.status))).catch(() => null),
  ]);

  // ── 系统资源：成功且包含 memory_store 字段才填充 ──
  if (resRes && !resRes.error && resRes.memory_store) {
    // 后端 pct 为 0-1 之间的小数，前端展示需乘以 100 转百分比
    resources.value = [
      { name: 'Memory Store', pct: Math.round((resRes.memory_store?.pct ?? 0) * 100), used: `${(resRes.memory_store?.used_mb ?? 0).toFixed(1)} MB`, total: `${resRes.memory_store?.total_mb ?? 0} MB` },
      { name: 'SQLite DB', pct: Math.round((resRes.sqlite_db?.pct ?? 0) * 100), used: `${(resRes.sqlite_db?.used_mb ?? 0).toFixed(1)} MB`, total: `${resRes.sqlite_db?.total_mb ?? 0} MB` },
      { name: 'Vector Index', pct: Math.round((resRes.vector_index?.pct ?? 0) * 100), used: `${(resRes.vector_index?.used_mb ?? 0).toFixed(1)} MB`, total: `${resRes.vector_index?.total_mb ?? 0} MB` },
      { name: 'Log Files', pct: Math.round((resRes.log_files?.pct ?? 0) * 100), used: `${(resRes.log_files?.used_mb ?? 0).toFixed(1)} MB`, total: `${resRes.log_files?.total_mb ?? 0} MB` },
    ];
  } else {
    // 拉取失败或返回 error 字段：清空数组，触发模板显示 "Failed to load"
    resources.value = [];
  }
  resourcesLoading.value = false;

  // ── 系统诊断：成功且至少有一项数据才填充 ──
  if (diagRes && !diagRes.error && Object.keys(diagRes).length > 0) {
    // 后端返回的 key 为 snake_case，这里映射为更友好的展示名
    const nameMap = {
      database: 'Database Connection',
      agent_registry: 'Agent Registry',
      memory_store: 'Memory Store',
      vector_index: 'Vector Index',
      config_loader: 'Config Loader',
      audit_log: 'Audit Log',
    };
    diagnostics.value = Object.entries(diagRes).map(([k, v]) => ({
      name: nameMap[k] || k,
      ok: !!v.ok,
      // result 字段展示后端返回的状态文本（如 "OK (maop.db)"、"12 agents"）
      result: v.result || (v.ok ? 'OK' : 'FAIL'),
      // detail 字段为空，因为后端 result 已包含完整信息
      detail: '',
    }));
  } else {
    diagnostics.value = [];
  }
  diagnosticsLoading.value = false;
}

async function pollData() {
  try {
    const h = await api.get('/api/health');
    metrics.value[1].value = String(h.active_agents || 0);
  } catch {}
  try {
    const data = await api.get('/api/live');
    metrics.value[0].value = String(data.requests_per_min || 0);
    metrics.value[2].value = String(data.queue_depth || 0);
    metrics.value[3].value = '$' + (data.cost_per_hour || 0).toFixed(2);
    metrics.value.forEach(m => { if (!m.sparkline) m.sparkline = genSparkline(); });
    if (data.agents) {
      agentStatuses.value = data.agents.map(a => ({
        name: a.name, healthy: a.healthy !== false, queue: a.queue || 0, load: a.load || Math.floor(Math.random() * 60) + 20,
      }));
    }
  } catch {}
}

async function runMaint(m) {
  m.status = 'running';
  m.statusText = 'Running...';
  // P1-11 fix: use correct /api/control/maintain endpoint and action names
  const endpoints = {
    'Prune Memory': '/api/control/maintain',
    'Backup Database': '/api/control/maintain',
    'Reload Config': '/api/control/maintain',
    'Clear Cache': '/api/control/maintain',
    'Rebuild Vector Index': '/api/control/maintain',
    'Compact Database': '/api/control/maintain',
  };
  try {
    const body = m.title === 'Prune Memory' ? { action: 'prune' }
      : m.title === 'Backup Database' ? { action: 'backup' }
      : m.title === 'Reload Config' ? { action: 'reload' }
      : m.title === 'Clear Cache' ? { action: 'cache-clear' }
      : m.title === 'Rebuild Vector Index' ? { action: 'reindex' }
      : m.title === 'Compact Database' ? { action: 'vacuum' }
      : {};
    await api.post(endpoints[m.title] || '/api/health', body);
    m.status = 'done';
    m.statusText = 'Done ✓';
  } catch {
    m.status = 'error';
    m.statusText = 'Failed ✗';
  }
  setTimeout(() => { m.status = 'idle'; m.statusText = 'Ready'; }, 3000);
}

onMounted(() => {
  pollData();
  pollTimer = setInterval(pollData, 10000);
  // C-7 修复：挂载时拉取系统资源与诊断数据；不轮询（运维页面静态信息）
  loadSystemStats();
});
onUnmounted(() => { if (pollTimer) clearInterval(pollTimer); });
</script>

<style scoped>
.monitor-page { }
.topbar { display: flex; align-items: center; gap: 12px; margin-bottom: 20px; }
.topbar h1 { font-size: 24px; font-weight: 700; }
.tab-bar { display: flex; gap: 4px; background: var(--bg2); border-radius: 10px; padding: 3px; margin-left: 16px; }
.tab-btn { background: none; border: none; padding: 6px 14px; border-radius: 8px; font-size: 13px; color: var(--text2); cursor: pointer; }
.tab-btn.active { background: var(--accent); color: #fff; }

.metrics-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 24px; }

.two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 24px; }
.panel { margin-bottom: 16px; }

.agent-row { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; }
.agent-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.agent-name { width: 100px; font-size: 13px; font-weight: 500; }
.agent-queue { font-size: 11px; color: var(--text3); width: 60px; }
.agent-bar { flex: 1; height: 6px; background: var(--bg); border-radius: 3px; overflow: hidden; }
.bar-fill { height: 100%; border-radius: 3px; transition: width .3s; }
.agent-load { width: 36px; font-size: 12px; text-align: right; color: var(--text3); }

.resource-item { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; }
.res-name { width: 100px; font-size: 13px; }
.res-bar { flex: 1; height: 6px; background: var(--bg); border-radius: 3px; overflow: hidden; }
.res-fill { height: 100%; border-radius: 3px; transition: width .3s; }
.res-val { font-size: 12px; color: var(--text3); width: 100px; }
.res-pct { font-size: 12px; font-weight: 600; width: 36px; text-align: right; }

/* C-7 修复：资源/诊断面板加载中与失败提示样式 */
.resource-loading, .diag-loading { padding: 16px 0; text-align: center; font-size: 13px; color: var(--text3); font-style: italic; }
.resource-loading-fail, .diag-loading-fail { color: var(--fail); }

.event-list { max-height: 300px; overflow-y: auto; }
.event-row { display: flex; align-items: center; gap: 10px; padding: 6px 0; border-bottom: 1px solid var(--border); font-size: 13px; }
.event-time { font-size: 11px; color: var(--text3); width: 60px; font-family: monospace; }
.event-level { font-size: 10px; padding: 2px 8px; border-radius: 4px; font-weight: 700; width: 50px; text-align: center; }
.event-row.info .event-level { background: rgba(59,130,246,.1); color: var(--accent); }
.event-row.warn .event-level { background: rgba(245,158,11,.1); color: var(--warn); }
.event-row.error .event-level { background: rgba(239,68,68,.1); color: var(--fail); }
.event-msg { flex: 1; color: var(--text2); }

.maint-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin-bottom: 24px; }
.maint-card { background: var(--bg2); border: 1px solid var(--border); border-radius: var(--radius); padding: 16px; cursor: pointer; transition: all .15s; text-align: center; }
.maint-card:hover { border-color: var(--accent); }
.maint-icon { font-size: 28px; margin-bottom: 8px; }
.maint-card h4 { font-size: 14px; font-weight: 600; margin-bottom: 4px; }
.maint-card p { font-size: 12px; color: var(--text3); margin-bottom: 8px; line-height: 1.4; }
.maint-status { font-size: 11px; padding: 2px 10px; border-radius: 6px; }
.maint-status.idle { background: var(--bg); color: var(--text3); }
.maint-status.running { background: rgba(59,130,246,.1); color: var(--accent); }
.maint-status.done { background: rgba(34,197,94,.1); color: var(--success); }
.maint-status.error { background: rgba(239,68,68,.1); color: var(--fail); }

.diag-item { display: flex; align-items: center; gap: 10px; padding: 8px 0; border-bottom: 1px solid var(--border); }
.diag-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.diag-name { font-size: 13px; font-weight: 500; width: 160px; }
.diag-result { font-size: 12px; font-weight: 600; color: var(--success); }
/* C-7 修复：诊断失败时 result 文本显示为红色 */
.diag-result.diag-result-fail { color: var(--fail); }
.diag-detail { font-size: 12px; color: var(--text3); margin-left: auto; }

/* t21: SSE indicator + event count badge */
.sse-indicator { display: flex; align-items: center; gap: 6px; margin-left: auto; padding: 4px 10px; border-radius: 8px; background: var(--bg2); border: 1px solid var(--border); font-size: 11px; color: var(--text3); }
.sse-indicator .sse-dot { width: 6px; height: 6px; border-radius: 50%; background: var(--text3); }
.sse-indicator.on .sse-dot { background: var(--success); box-shadow: 0 0 4px var(--success); }
.sse-indicator.off .sse-dot { background: var(--fail); opacity: .6; }
.event-count { display: inline-block; margin-left: 6px; padding: 1px 6px; border-radius: 4px; font-size: 10px; font-weight: 600; background: rgba(59,130,246,.12); color: var(--accent); }
</style>
