<template>
  <div class="monitor-page view-enter">
    <PageHeader>
      <div class="tab-bar">
        <button v-for="tab in tabs" :key="tab.key" :class="['tab-btn', { active: activeTab === tab.key }]" @click="activeTab = tab.key">
          <AppIcon :name="tab.icon" :size="16" /> {{ t(tab.labelKey) }}
        </button>
      </div>
      <span class="sse-indicator" :class="realtimeConnected ? 'on' : 'off'" :title="realtimeConnected ? t('view.monitor.sseConnected') : t('view.monitor.sseDisconnected')">
        <span class="sse-dot"></span>
        <span class="sse-text">{{ t('status.live') }}</span>
      </span>
    </PageHeader>

    <div v-if="activeTab === 'monitor'">
      <div class="metrics-grid">
        <StatCard
          v-for="m in metrics"
          :key="m.labelKey"
          :label="t(m.labelKey)"
          :value="m.value"
          :unit="m.unit"
          :icon="m.icon"
          :tone="m.tone"
          :loading="firstLoad"
        />
      </div>

      <div class="two-col">
        <Card :title="t('view.monitor.liveAgentStatus')" icon="bot" :margin-bottom="0">
          <div v-if="agentStatuses.length" class="agent-status-list">
            <div v-for="a in agentStatuses" :key="a.name" class="agent-row">
              <span class="agent-dot" :class="a.healthy ? 'ok' : 'bad'"></span>
              <span class="agent-name">{{ a.name }}</span>
              <span class="agent-queue">Queue: {{ a.queue }}</span>
              <div class="agent-bar"><div class="bar-fill" :style="{ width: a.load + '%', background: a.load > 80 ? 'var(--warn)' : 'var(--brand)' }"></div></div>
              <span class="agent-load">{{ a.load }}%</span>
            </div>
          </div>
          <EmptyState v-else icon="bot" :title="t('view.monitor.noAgentStatus')" :hint="t('view.monitor.noAgentStatusHint')" />
        </Card>

        <Card :title="t('view.monitor.systemResources')" icon="server" :margin-bottom="0">
          <div v-if="resourcesLoading" class="resource-skel">
            <Skeleton v-for="n in 4" :key="n" height="20px" />
          </div>
          <div v-else-if="adminRequired" class="resource-fail admin">
            <AppIcon name="shield" :size="16" /> {{ t('view.monitor.adminRequiredResources') }}
          </div>
          <div v-else-if="!resources.length" class="resource-fail">
            <AppIcon name="alert-triangle" :size="16" /> {{ t('view.monitor.failedResources') }}
            <div v-if="statsError" class="resource-err-detail">{{ statsError }}</div>
          </div>
          <div v-else class="resource-list">
            <div v-for="r in resources" :key="r.name" class="resource-item">
              <span class="res-name">{{ r.name }}</span>
              <div class="res-bar"><div class="res-fill" :style="{ width: r.pct + '%', background: r.pct > 80 ? 'var(--fail)' : r.pct > 60 ? 'var(--warn)' : 'var(--success)' }"></div></div>
              <span class="res-val">{{ r.used }} / {{ r.total }}</span>
              <span class="res-pct">{{ r.pct }}%</span>
            </div>
          </div>
        </Card>
      </div>

      <Card :title="t('view.monitor.eventStream')" icon="activity" class="mt">
        <template #actions>
          <span v-if="sseEvents.length" class="event-count">{{ sseEvents.length }}</span>
        </template>
        <div ref="eventList" class="event-list">
          <div v-for="e in sseEvents" :key="e.id" class="event-row" :class="e.level">
            <span class="event-time">{{ e.time }}</span>
            <span class="event-level">{{ e.level }}</span>
            <span class="event-msg">{{ e.message }}</span>
          </div>
          <div v-if="!sseEvents.length" class="event-row info">
            <span class="event-time">—</span>
            <span class="event-level">SSE</span>
            <span class="event-msg">{{ t('view.monitor.waitingStream') }}</span>
          </div>
        </div>
      </Card>

      <!-- F1-02: Agent 健康度面板（异常自适应调度） -->
      <Card :title="t('view.monitor.healthTitle')" icon="heart-pulse" class="mt">
        <template #actions>
          <button
            class="agent-health-refresh"
            :disabled="agentHealthLoading"
            @click="loadAgentHealth"
          >{{ t('common.refresh') }}</button>
        </template>
        <div v-if="agentHealthLoading && !agentHealth.length" class="resource-skel">
          <Skeleton v-for="n in 3" :key="n" height="22px" />
        </div>
        <div v-else-if="!agentHealth.length" class="agent-health-empty">
          <AppIcon name="heart-pulse" :size="16" />
          <span>{{ t('view.monitor.noHealthData') }}</span>
        </div>
        <div v-else class="agent-health-list">
          <div class="agent-health-row agent-health-header">
            <span class="ah-name">Agent</span>
            <span class="ah-failure">{{ t('view.monitor.healthFailureRate') }}</span>
            <span class="ah-latency">{{ t('view.monitor.healthLatency') }}</span>
            <span class="ah-timeout">{{ t('view.monitor.healthTimeoutRate') }}</span>
            <span class="ah-weight">{{ t('view.monitor.healthWeight') }}</span>
            <span class="ah-status">{{ t('view.monitor.healthStatus') }}</span>
          </div>
          <div
            v-for="a in agentHealth"
            :key="a.agent_id"
            class="agent-health-row"
            :class="`row-${a.status}`"
          >
            <span class="ah-name" :title="a.agent_id">{{ a.agent_id }}</span>
            <span class="ah-failure">
              <div class="mini-bar">
                <div
                  class="mini-fill"
                  :style="{ width: (a.failure_rate * 100) + '%', background: a.failure_rate > 0.3 ? 'var(--fail)' : a.failure_rate > 0.1 ? 'var(--warn)' : 'var(--success)' }"
                ></div>
              </div>
              <span class="mini-val">{{ (a.failure_rate * 100).toFixed(1) }}%</span>
            </span>
            <span class="ah-latency">{{ a.avg_latency.toFixed(2) }}s</span>
            <span class="ah-timeout">{{ (a.timeout_rate * 100).toFixed(1) }}%</span>
            <span class="ah-weight">
              <div class="mini-bar">
                <div
                  class="mini-fill"
                  :style="{ width: (a.weight * 100) + '%', background: a.weight === 0 ? 'var(--fail)' : a.weight < 1 ? 'var(--warn)' : 'var(--success)' }"
                ></div>
              </div>
              <span class="mini-val">{{ a.weight.toFixed(2) }}</span>
            </span>
            <span class="ah-status">
              <span class="status-pill" :class="`pill-${a.status}`">
                <span class="pill-dot"></span>
                {{ statusLabel(a.status) }}
              </span>
            </span>
          </div>
          <div v-if="agentHealthConfig" class="agent-health-config">
            <span>{{ t('view.monitor.healthWindow') }}: {{ agentHealthConfig.window_size }}</span>
            <span>{{ t('view.monitor.healthDrainThreshold') }}: {{ (agentHealthConfig.failure_rate_threshold * 100).toFixed(0) }}%</span>
            <span>{{ t('view.monitor.healthTimeoutThreshold') }}: {{ agentHealthConfig.timeout_threshold }}s</span>
            <span>{{ t('view.monitor.healthRecoverySuccesses') }}: {{ agentHealthConfig.recovery_consecutive_successes }}</span>
          </div>
        </div>
      </Card>

      <!-- v4.5.0: DAG execution progress streaming -->
      <Card title="DAG Execution Progress" icon="network" class="mt">
        <template #actions>
          <input
            v-model="dagExecutionId"
            class="dag-exec-input"
            placeholder="execution_id (trace_id)"
            @keyup.enter="dagExecInput = dagExecutionId"
          />
        </template>
        <DagGraph
          v-if="dagExecutionId"
          :execution-id="dagExecutionId"
          :nodes="dagNodes"
          :edges="dagEdges"
          transport="sse"
        />
        <EmptyState
          v-else
          icon="network"
          title="No DAG subscription"
          hint="Enter an execution_id above to stream real-time DAG node status."
        />
      </Card>
    </div>

    <div v-if="activeTab === 'maintenance'">
      <div class="maint-grid">
        <button v-for="m in maintActions" :key="m.titleKey" class="maint-card" @click="runMaint(m)">
          <span class="maint-icon"><AppIcon :name="m.icon" :size="24" /></span>
          <h4>{{ t(m.titleKey) }}</h4>
          <p>{{ t(m.descKey) }}</p>
          <span class="maint-status" :class="m.status">
            <AppIcon v-if="m.status === 'done'" name="check" :size="12" />
            <AppIcon v-else-if="m.status === 'error'" name="x" :size="12" />
            {{ m.statusText }}
          </span>
        </button>
      </div>

      <Card :title="t('view.monitor.systemDiagnostics')" icon="clipboard" class="mt">
        <div v-if="diagnosticsLoading" class="resource-skel">
          <Skeleton v-for="n in 5" :key="n" height="20px" />
        </div>
          <div v-else-if="adminRequired" class="resource-fail admin">
            <AppIcon name="shield" :size="16" /> {{ t('view.monitor.adminRequiredDiag') }}
          </div>
          <div v-else-if="!diagnostics.length" class="resource-fail">
            <AppIcon name="alert-triangle" :size="16" /> {{ t('view.monitor.failedDiag') }}
          </div>
        <div v-else class="diag-list">
          <div v-for="d in diagnostics" :key="d.name" class="diag-item">
            <span class="diag-dot" :class="d.ok ? 'ok' : 'bad'"></span>
            <span class="diag-name">{{ d.name }}</span>
            <span class="diag-result" :class="{ 'diag-result-fail': !d.ok }">{{ d.result }}</span>
          </div>
        </div>
      </Card>
    </div>
  </div>
</template>

<script setup>
import { ref, watch, onMounted, onUnmounted, computed } from 'vue';
import { useApiStore } from '../stores/api.js';
import { useRealtimeStore } from '../stores/realtime.js';
import { useI18n } from '../i18n';
import { StatCard, Card, Skeleton, EmptyState, AppIcon, PageHeader, DagGraph } from '../components/index.js';

const api = useApiStore();
const realtime = useRealtimeStore();
const realtimeConnected = computed(() => realtime.connected);
const { t } = useI18n();
const activeTab = ref('monitor');
const eventList = ref(null);
let pollTimer = null;
const firstLoad = ref(true);

// v4.5.0: DAG progress streaming — user enters an execution_id to subscribe.
const dagExecutionId = ref('');
const dagNodes = ref([]);   // populated if DAG structure is known; auto-discovered from events otherwise
const dagEdges = ref([]);

const sseEvents = ref([]);
let sseEventCounter = 0;
const statsError = ref('');
let agentHealthTimer = null;

// api.get throws a plain Error whose message is "API <url>: <status>".
// Detect 403 (admin role required) from either a .status field or the message.
function isForbidden(e) {
  return e?.status === 403 || (e && typeof e.message === 'string' && e.message.includes(': 403'));
}

// Subscribe to the shared realtime snapshot (single global WS, no second SSE
// channel). The backend pushes the same `state` event on /ws that it used to
// push on /api/stream, so consuming it here removes the duplicate connection.
watch(() => realtime.snapshot, (payload) => {
  if (payload === null) return;
  sseEventCounter += 1;
  const ts = new Date();
  const time = ts.toLocaleTimeString();
  const agents = typeof payload.agents === 'number' ? payload.agents : null;
  const successRate = typeof payload.success_rate === 'number' ? payload.success_rate : null;
  // Backend pushes `delegations` as an array (stream.py); show its length.
  const delegations = Array.isArray(payload.delegations)
    ? payload.delegations.length
    : (typeof payload.delegations === 'number' ? payload.delegations : null);
  const parts = [];
  if (agents !== null) parts.push(`agents=${agents}`);
  if (successRate !== null) parts.push(`success=${successRate}%`);
  if (delegations !== null) parts.push(`delegations=${delegations}`);
  const message = parts.length ? `state: ${parts.join(', ')}` : JSON.stringify(payload);
  sseEvents.value.unshift({ id: sseEventCounter, time, level: 'info', message });
  if (agents !== null) metrics.value[1].value = String(agents);
  if (sseEvents.value.length > 50) sseEvents.value = sseEvents.value.slice(0, 50);
}, { deep: true });

const tabs = [
  { key: 'monitor', labelKey: 'view.monitor.tabMonitor', icon: 'activity' },
  { key: 'maintenance', labelKey: 'view.monitor.tabMaintenance', icon: 'wrench' },
];

const metrics = ref([
  { labelKey: 'view.monitor.metricRequests', icon: 'activity', value: '0', unit: '', tone: 'brand' },
  { labelKey: 'view.monitor.metricActiveAgents', icon: 'bot', value: '0', unit: '', tone: 'brand' },
  { labelKey: 'view.monitor.metricQueueDepth', icon: 'server', value: '0', unit: '', tone: 'warn' },
  { labelKey: 'view.monitor.metricCostHr', icon: 'dollar', value: '$0.00', unit: '', tone: 'success' },
]);

const agentStatuses = ref([]);

const resources = ref([]);
const resourcesLoading = ref(true);

const diagnostics = ref([]);
const diagnosticsLoading = ref(true);

// F1-02 (异常自适应调度): Agent 健康度面板状态
const agentHealth = ref([]);
const agentHealthConfig = ref(null);
const agentHealthLoading = ref(false);

function statusLabel(status) {
  if (status === 'normal') return t('view.monitor.healthStatusNormal');
  if (status === 'drained') return t('view.monitor.healthStatusDrained');
  if (status === 'recovering') return t('view.monitor.healthStatusRecovering');
  return status;
}

async function loadAgentHealth() {
  agentHealthLoading.value = true;
  try {
    const data = await api.get('/api/scheduling/failure-stats');
    if (data && !data.error) {
      agentHealth.value = Array.isArray(data.agents) ? data.agents : [];
      agentHealthConfig.value = data.config || null;
    }
  } catch (e) {
    // 静默失败 — 健康度面板是辅助信息，不应弹错
    console.warn('[monitor] agent health fetch failed:', e && e.message);
  } finally {
    agentHealthLoading.value = false;
  }
}

// Set when a required endpoint returns 403 (admin role needed). Surfaced as a
// clear "admin access required" state instead of a silent empty panel.
const adminRequired = ref(false);

const maintActions = ref([
  { icon: 'trash', titleKey: 'view.monitor.maintPrune', descKey: 'view.monitor.descPrune', status: 'idle', statusText: t('view.monitor.statusReady') },
  { icon: 'database', titleKey: 'view.monitor.maintBackup', descKey: 'view.monitor.descBackup', status: 'idle', statusText: t('view.monitor.statusReady') },
  { icon: 'refresh', titleKey: 'view.monitor.maintReload', descKey: 'view.monitor.descReload', status: 'idle', statusText: t('view.monitor.statusReady') },
  { icon: 'cpu', titleKey: 'view.monitor.maintClearCache', descKey: 'view.monitor.descClearCache', status: 'idle', statusText: t('view.monitor.statusReady') },
  { icon: 'search', titleKey: 'view.monitor.maintReindex', descKey: 'view.monitor.descReindex', status: 'idle', statusText: t('view.monitor.statusReady') },
  { icon: 'activity', titleKey: 'view.monitor.maintCompact', descKey: 'view.monitor.descCompact', status: 'idle', statusText: t('view.monitor.statusReady') },
]);

async function loadSystemStats() {
  resourcesLoading.value = true;
  diagnosticsLoading.value = true;

  let resRes = null;
  let diagRes = null;
  try {
    [resRes, diagRes] = await Promise.all([
      api.get('/api/system/resources').catch((e) => (isForbidden(e) ? 'FORBIDDEN' : null)),
      api.get('/api/system/diagnostics').catch((e) => (isForbidden(e) ? 'FORBIDDEN' : null)),
    ]);
  } catch (e) {
    resRes = null; diagRes = null;
    statsError.value = (e && e.message) ? e.message : String(e);
  }

  // Either endpoint responded 403 → admin role required.
  if (resRes === 'FORBIDDEN' || diagRes === 'FORBIDDEN') {
    adminRequired.value = true;
    resources.value = [];
    diagnostics.value = [];
    resourcesLoading.value = false;
    diagnosticsLoading.value = false;
    return;
  }
  adminRequired.value = false;
  statsError.value = '';

  if (resRes && !resRes.error && resRes.memory_store) {
    resources.value = [
      { name: 'Memory Store', pct: Math.round((resRes.memory_store?.pct ?? 0) * 100), used: `${(resRes.memory_store?.used_mb ?? 0).toFixed(1)} MB`, total: `${resRes.memory_store?.total_mb ?? 0} MB` },
      { name: 'SQLite DB', pct: Math.round((resRes.sqlite_db?.pct ?? 0) * 100), used: `${(resRes.sqlite_db?.used_mb ?? 0).toFixed(1)} MB`, total: `${resRes.sqlite_db?.total_mb ?? 0} MB` },
      { name: 'Vector Index', pct: Math.round((resRes.vector_index?.pct ?? 0) * 100), used: `${(resRes.vector_index?.used_mb ?? 0).toFixed(1)} MB`, total: `${resRes.vector_index?.total_mb ?? 0} MB` },
      { name: 'Log Files', pct: Math.round((resRes.log_files?.pct ?? 0) * 100), used: `${(resRes.log_files?.used_mb ?? 0).toFixed(1)} MB`, total: `${resRes.log_files?.total_mb ?? 0} MB` },
    ];
  } else {
    resources.value = [];
  }
  resourcesLoading.value = false;

  if (diagRes && !diagRes.error && Object.keys(diagRes).length > 0) {
    const diagNameKeys = {
      database: 'view.monitor.diagDatabase',
      agent_registry: 'view.monitor.diagAgentRegistry',
      memory_store: 'view.monitor.diagMemoryStore',
      vector_index: 'view.monitor.diagVectorIndex',
      config_loader: 'view.monitor.diagConfigLoader',
      audit_log: 'view.monitor.diagAuditLog',
    };
    diagnostics.value = Object.entries(diagRes).map(([k, v]) => ({
      name: t(diagNameKeys[k] || k),
      ok: !!v.ok,
      result: v.result || (v.ok ? t('view.monitor.statusOk') : t('view.monitor.statusFail')),
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
  } catch (e) {
    // Polling failure: do not spam the UI, but log for debugging.
    console.warn('[monitor] health poll failed:', e && e.message);
  }
  try {
    const data = await api.get('/api/live');
    metrics.value[0].value = String(data.requests_per_min || 0);
    metrics.value[2].value = String(data.queue_depth || 0);
    metrics.value[3].value = '$' + (data.cost_per_hour || 0).toFixed(2);
    if (data.agents) {
      // No synthetic load — fall back to 0 when the backend omits it.
      agentStatuses.value = data.agents.map(a => ({
        name: a.name, healthy: a.healthy !== false, queue: a.queue || 0, load: a.load || 0,
      }));
    }
  } catch (e) {
    if (isForbidden(e)) adminRequired.value = true;
  }
  firstLoad.value = false;
}

async function runMaint(m) {
  m.status = 'running';
  m.statusText = t('view.monitor.statusRunning');
  const endpoints = {
    'Prune Memory': '/api/control/maintain',
    'Backup Database': '/api/control/maintain',
    'Reload Config': '/api/control/maintain',
    'Clear Cache': '/api/control/maintain',
    'Rebuild Vector Index': '/api/control/maintain',
    'Compact Database': '/api/control/maintain',
  };
  try {
    const body = m.titleKey === 'view.monitor.maintPrune' ? { action: 'prune' }
      : m.titleKey === 'view.monitor.maintBackup' ? { action: 'backup' }
      : m.titleKey === 'view.monitor.maintReload' ? { action: 'reload' }
      : m.titleKey === 'view.monitor.maintClearCache' ? { action: 'cache-clear' }
      : m.titleKey === 'view.monitor.maintReindex' ? { action: 'reindex' }
      : m.titleKey === 'view.monitor.maintCompact' ? { action: 'vacuum' }
      : {};
    await api.post(endpoints[m.titleKey === 'view.monitor.maintPrune' ? 'Prune Memory'
      : m.titleKey === 'view.monitor.maintBackup' ? 'Backup Database'
      : m.titleKey === 'view.monitor.maintReload' ? 'Reload Config'
      : m.titleKey === 'view.monitor.maintClearCache' ? 'Clear Cache'
      : m.titleKey === 'view.monitor.maintReindex' ? 'Rebuild Vector Index'
      : m.titleKey === 'view.monitor.maintCompact' ? 'Compact Database'
      : 'Prune Memory'] || '/api/health', body);
    m.status = 'done';
    m.statusText = t('view.monitor.statusDone');
  } catch {
    m.status = 'error';
    m.statusText = t('view.monitor.statusFailed');
  }
  setTimeout(() => { m.status = 'idle'; m.statusText = t('view.monitor.statusReady'); }, 3000);
}

onMounted(() => {
  pollData();
  pollTimer = setInterval(pollData, 10000);
  loadSystemStats();
  // F1-02: Agent 健康度面板 — 5s 轮询（轻量 GET，且权重变化需要近实时反映）
  loadAgentHealth();
  agentHealthTimer = setInterval(loadAgentHealth, 5000);
});
onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer);
  if (agentHealthTimer) clearInterval(agentHealthTimer);
});
</script>

<style scoped>
.resource-err-detail {
  font-size: 11px;
  color: var(--text-faint);
  font-family: var(--font-mono);
  margin-top: 4px;
  word-break: break-word;
}
/* v4.5.0: DAG execution input */
.dag-exec-input {
  font-size: 12px;
  padding: 4px 8px;
  border: 1px solid var(--border, rgba(148,163,184,.35));
  border-radius: 4px;
  background: var(--bg-card, #fff);
  color: var(--text, #e8eaf0);
  width: 200px;
  outline: none;
}
.dag-exec-input:focus {
  border-color: var(--brand, #3574f0);
  box-shadow: 0 0 0 2px var(--brand-soft);
}

/* F1-02: Agent 健康度面板 */
.agent-health-refresh {
  font-size: 12px;
  padding: 4px 10px;
  border: 1px solid var(--border, rgba(148,163,184,.35));
  border-radius: 4px;
  background: var(--bg-card, #fff);
  color: var(--text, #e8eaf0);
  cursor: pointer;
}
.agent-health-refresh:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.agent-health-empty {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px;
  color: var(--text-faint, #94a3b8);
  font-size: 12px;
}
.agent-health-list {
  display: flex;
  flex-direction: column;
  gap: 0;
  font-size: 12px;
}
.agent-health-row {
  display: grid;
  grid-template-columns: 1.4fr 1.6fr 0.9fr 0.8fr 1.6fr 0.9fr;
  align-items: center;
  gap: 8px;
  padding: 6px 4px;
  border-bottom: 1px solid var(--border-light, rgba(148,163,184,.12));
}
.agent-health-row:last-child {
  border-bottom: none;
}
.agent-health-header {
  font-weight: 600;
  color: var(--text-faint, #94a3b8);
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  border-bottom: 1px solid var(--border, rgba(148,163,184,.25));
}
.row-drained .ah-name {
  color: var(--fail, #ef4444);
}
.row-recovering .ah-name {
  color: var(--warn, #f59e0b);
}
.ah-name {
  font-family: var(--font-mono);
  font-size: 11px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ah-failure, .ah-weight {
  display: flex;
  align-items: center;
  gap: 6px;
}
.mini-bar {
  flex: 1;
  height: 6px;
  background: var(--bg-elev, rgba(148,163,184,.15));
  border-radius: 3px;
  overflow: hidden;
}
.mini-fill {
  height: 100%;
  border-radius: 3px;
  transition: width 0.3s ease;
}
.mini-val {
  font-family: var(--font-mono);
  font-size: 11px;
  min-width: 42px;
  text-align: right;
}
.ah-latency, .ah-timeout {
  font-family: var(--font-mono);
  font-size: 11px;
}
.status-pill {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 8px;
  border-radius: 10px;
  font-size: 11px;
  font-weight: 500;
}
.pill-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
}
.pill-normal {
  background: var(--success-soft);
  color: var(--success, #22c55e);
}
.pill-normal .pill-dot {
  background: var(--success, #22c55e);
}
.pill-drained {
  background: var(--fail-soft);
  color: var(--fail, #ef4444);
}
.pill-drained .pill-dot {
  background: var(--fail, #ef4444);
}
.pill-recovering {
  background: var(--warn-soft);
  color: var(--warn, #f59e0b);
}
.pill-recovering .pill-dot {
  background: var(--warn, #f59e0b);
}
.agent-health-config {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  padding: 8px 4px 4px;
  margin-top: 6px;
  border-top: 1px dashed var(--border-light, rgba(148,163,184,.18));
  font-size: 11px;
  color: var(--text-faint, #94a3b8);
  font-family: var(--font-mono);
}
</style>
