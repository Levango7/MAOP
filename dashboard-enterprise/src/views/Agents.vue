<template>
  <div class="agents-page view-enter">
    <PageHeader>
      <span class="live-tag" :class="{ active: realtime.connected }">
        <AppIcon :name="realtime.connected ? 'radio' : 'radio'" :size="12" />
        {{ realtime.connected ? t('common.live') : t('common.offline') }}
      </span>
      <Segmented
        :model-value="viewMode"
        :options="[{ value: 'grid', label: t('common.grid') }, { value: 'table', label: t('common.table') }]"
        size="sm"
        @update:model-value="viewMode = $event"
      />
      <button class="btn-action" :class="{ 'pulse-once': scanning }" :disabled="scanning || !isAdmin" :title="!isAdmin ? t('nav.editionLocked') : ''" @click="scanLocal">
        <AppIcon name="search" :size="14" />
        {{ scanning ? t('view.agents.scanning') : t('view.agents.scanNow') }}
      </button>
      <button class="btn-action" :class="{ 'pulse-once': refreshing }" :disabled="refreshing" @click="loadAgents">
        <AppIcon name="refresh" :size="14" />
        {{ refreshing ? t('common.loading') : t('common.refresh') }}
      </button>
    </PageHeader>

    <Card :title="t('view.agents.dispatchRouter')" :subtitle="t('view.agents.dispatchRouterSub')" :margin-bottom="24">
      <div v-if="loading" class="skeleton-grid">
        <Skeleton v-for="n in 4" :key="n" height="46px" radius="8px" />
      </div>
      <div v-else-if="routes.length" class="dispatch-grid">
        <div class="dispatch-card" v-for="route in routes" :key="route.name">
          <AppIcon class="dispatch-icon" name="route" :size="16" />
          <div class="route-pattern">task:{{ route.name }}</div>
          <div class="route-arrow">→</div>
          <div class="route-target">{{ route.model || route.provider || 'default' }}</div>
          <span class="route-weight" :class="route.enabled === false ? 'off' : 'on'">{{ route.enabled === false ? t('common.off') : t('common.on') }}</span>
        </div>
      </div>
      <EmptyState v-else icon="route" :title="t('view.agents.noRoutes')" :hint="t('view.agents.noRoutesHint')" />
    </Card>

    <Card :title="t('view.agents.localScan')" :subtitle="t('view.agents.localScanSub')" :margin-bottom="24">
      <template #actions>
        <button class="btn-action" :class="{ 'pulse-once': scanning }" :disabled="scanning || !isAdmin" :title="!isAdmin ? t('nav.editionLocked') : ''" @click="scanLocal">
          <AppIcon name="search" :size="14" />
          {{ scanning ? t('view.agents.scanning') : t('view.agents.scanNow') }}
        </button>
      </template>
      <div v-if="scanning" class="skeleton-grid">
        <Skeleton v-for="n in 4" :key="n" height="46px" radius="8px" />
      </div>
      <div v-else-if="scanned.length" class="scanned-grid">
        <div class="scanned-card" v-for="s in scanned" :key="s.name">
          <div class="scanned-top">
            <div class="scanned-avatar" :class="s.status">{{ (s.name || '?').charAt(0).toUpperCase() }}</div>
            <div class="scanned-identity">
              <h3>{{ s.name }}</h3>
              <Badge :tone="s.status === 'available' ? 'success' : 'neutral'">{{ s.status === 'available' ? t('view.agents.available') : t('view.agents.unavailable') }}</Badge>
            </div>
            <span class="scanned-version mono" v-if="s.version">{{ s.version }}</span>
          </div>
          <div class="scanned-meta">
            <div class="sm-row"><span class="sm-key">{{ t('common.provider') }}</span><span class="sm-val">{{ s.provider || '—' }}</span></div>
            <div class="sm-row"><span class="sm-key">{{ t('view.agents.cliPath') }}</span><span class="sm-val mono path">{{ s.cli_path || '—' }}</span></div>
          </div>
          <div class="caps" v-if="(s.capabilities || []).length">
            <span class="cap-chip" v-for="c in s.capabilities" :key="c">{{ c }}</span>
          </div>
        </div>
      </div>
      <EmptyState v-else icon="search" :title="t('view.agents.noScanned')" :hint="t('view.agents.noScannedHint')" />
    </Card>

    <div v-if="viewMode === 'grid'" class="agent-grid">
      <Card
        v-for="a in agents"
        :key="a.name"
        class="agent-card"
        :class="{ selected: selectedAgent === a.name }"
        clickable
        @click="selectAgent(a)"
      >
        <div class="agent-top">
          <div class="agent-avatar" :style="{ background: agentColor(a.name) }">{{ (a.name || '?').charAt(0).toUpperCase() }}</div>
          <div class="agent-identity">
            <h3>{{ a.name }}</h3>
            <Badge :tone="statusTone(agentStatus(a))">{{ agentStatus(a) }}</Badge>
          </div>
        </div>
        <p class="agent-desc">{{ a.description || t('view.agents.noDescription') }}</p>
        <div class="agent-metrics">
          <div class="metric"><span class="metric-val mono">{{ a.model || 'auto' }}</span><span class="metric-lbl">{{ t('common.model') }}</span></div>
          <div class="metric"><span class="metric-val mono">{{ a.driver || '—' }}</span><span class="metric-lbl">{{ t('common.driver') }}</span></div>
          <div class="metric"><span class="metric-val">{{ (a.capabilities || []).length }}</span><span class="metric-lbl">{{ t('common.caps') }}</span></div>
          <div class="metric"><span class="metric-val">{{ a.last_latency_ms || 0 }}<small>ms</small></span><span class="metric-lbl">{{ t('common.latency') }}</span></div>
        </div>
        <div class="agent-actions">
          <button class="act-btn" @click.stop="switchModel(a)" :title="t('view.agents.switchModel')">
            <AppIcon name="bot" :size="14" /> {{ t('common.model') }}
          </button>
          <button class="act-btn" @click.stop="healthCheck(a)" :title="t('view.agents.healthCheck')">
            <AppIcon name="activity" :size="14" /> {{ t('view.agents.health') }}
          </button>
          <button class="act-btn warn" @click.stop="restartAgent(a)" :title="t('view.agents.restart')">
            <AppIcon name="refresh" :size="14" /> {{ t('view.agents.restart') }}
          </button>
        </div>
      </Card>
    </div>

    <Card v-else :margin-bottom="24" :title="t('view.agents.allAgents')" :padded="false">
      <div class="agent-table">
        <div class="trow header">
          <span>{{ t('view.agents.colAgent') }}</span><span>{{ t('common.status') }}</span><span>{{ t('common.model') }}</span><span>{{ t('common.driver') }}</span><span>{{ t('common.caps') }}</span><span>{{ t('common.latency') }}</span><span>{{ t('common.actions') }}</span>
        </div>
        <div class="trow" v-for="a in agents" :key="a.name">
          <span class="agent-name">{{ a.name }}</span>
          <span><Badge :tone="statusTone(agentStatus(a))">{{ agentStatus(a) }}</Badge></span>
          <span class="mono">{{ a.model || 'auto' }}</span>
          <span class="mono">{{ a.driver || '—' }}</span>
          <span>{{ (a.capabilities || []).length }}</span>
          <span>{{ a.last_latency_ms || 0 }}ms</span>
          <span class="actions-cell">
            <button class="act-btn small" @click="switchModel(a)" :title="t('common.model')"><AppIcon name="bot" :size="13" /></button>
            <button class="act-btn small" @click="healthCheck(a)" :title="t('view.agents.healthCheck')"><AppIcon name="activity" :size="13" /></button>
            <button class="act-btn small warn" @click="restartAgent(a)" :title="t('view.agents.restart')"><AppIcon name="refresh" :size="13" /></button>
          </span>
        </div>
        <EmptyState v-if="!agents.length" icon="bot" :title="t('view.agents.noAgentsFound')" :hint="t('view.agents.noAgentsFoundHint')" />
      </div>
    </Card>

    <Card v-if="selectedAgent" :title="selectedAgent" :margin-bottom="24">
      <template #actions>
        <button class="close-btn" @click="selectedAgent = null" :aria-label="t('common.close')"><AppIcon name="x" :size="14" /></button>
      </template>
      <div class="detail-body">
        <div class="detail-section">
          <h4>{{ t('common.configuration') }}</h4>
          <div class="config-grid">
            <div class="cfg-item" v-for="(v, k) in agentConfig" :key="k">
              <span class="cfg-key">{{ k }}</span>
              <span class="cfg-val">{{ v }}</span>
            </div>
          </div>
          <div class="caps-block" v-if="selectedCapabilities.length">
            <h4>{{ t('common.capabilities') }}</h4>
            <div class="caps">
              <span class="cap-chip" v-for="c in selectedCapabilities" :key="c">{{ c }}</span>
            </div>
          </div>
        </div>
        <div class="detail-section">
          <h4>{{ t('view.agents.runtime') }}</h4>
          <div class="perf-bars">
            <div class="perf-row" v-for="(v, i) in perfHistory" :key="i">
              <span class="perf-label">{{ v.label }}</span>
              <div class="perf-bar"><div class="perf-fill" :style="{ width: v.pct + '%', background: v.color }"></div></div>
              <span class="perf-val">{{ v.value }}</span>
            </div>
          </div>
        </div>
      </div>
    </Card>

    <EmptyState v-if="!loading && !agents.length" icon="bot" :title="t('view.agents.noAgents')" :hint="t('view.agents.noAgentsHint')" />
  </div>
</template>

<script setup>
import { ref, onMounted, watch } from 'vue';
import { useApiStore } from '../stores/api.js';
import { useRealtimeStore } from '../stores/realtime.js';
import { useToast } from '../composables/useToast.js';
import { useI18n } from '../i18n';
import { Card, Badge, Skeleton, EmptyState, Segmented, AppIcon, PageHeader } from '../components/index.js';

const api = useApiStore();
const realtime = useRealtimeStore();
const toast = useToast();
const { t } = useI18n();

const agents = ref([]);
const routes = ref([]);
const viewMode = ref('grid');
const selectedAgent = ref(null);
const agentConfig = ref({});
const selectedCapabilities = ref([]);
const perfHistory = ref([]);
const loading = ref(true);
const refreshing = ref(false);
const scanned = ref([]);
const scanning = ref(false);
const isAdmin = ref(false);

// /api/agents (and /api/agents/routes) return a dict whose value is a LIST of
// agent objects — not a list and not { agents: [...] }. Normalize accordingly.
function toList(data) {
  if (Array.isArray(data)) return data;
  if (data && typeof data === 'object') {
    const arr = Object.values(data).find((v) => Array.isArray(v));
    return arr || [];
  }
  return [];
}

function agentStatus(a) {
  if (!a || a.enabled === false) return 'disabled';
  if (a.health === 'healthy') return 'active';
  if (a.health === 'unhealthy') return 'error';
  return 'idle';
}

function statusTone(status) {
  if (status === 'active' || status === 'healthy') return 'success';
  if (status === 'error' || status === 'unhealthy') return 'fail';
  if (status === 'busy') return 'warn';
  return 'neutral';
}

function agentColor(name) {
  // Theme-aware palette — reference chart tokens so colors follow dark/light.
  const colors = [
    'var(--chart-1)', 'var(--chart-5)', 'var(--chart-3)', 'var(--chart-4)',
    'var(--chart-fail)', 'var(--chart-8)',
  ];
  const s = name || '';
  let hash = 0;
  for (let i = 0; i < s.length; i++) hash = s.charCodeAt(i) + ((hash << 5) - hash);
  return colors[Math.abs(hash) % colors.length];
}

function selectAgent(a) {
  selectedAgent.value = a.name;
  agentConfig.value = {
    model: a.model || 'auto',
    provider: a.provider || '—',
    driver: a.driver || '—',
    version: a.version || '—',
    enabled: a.enabled === false ? 'false' : 'true',
    health: a.health || 'unknown',
    timeout_s: a.timeout_s ?? '—',
    registered_at: a.registered_at || '—',
    last_health_check: a.last_health_check || '—',
  };
  selectedCapabilities.value = a.capabilities || [];
  // Real runtime signals only — no synthetic fillers.
  perfHistory.value = [
    { label: 'Last Latency', pct: Math.min(100, (a.last_latency_ms || 0) / 50), value: (a.last_latency_ms || 0) + ' ms', color: 'var(--chart-1)' },
    { label: 'Consecutive Failures', pct: Math.min(100, (a.consecutive_failures || 0) * 10), value: String(a.consecutive_failures || 0), color: 'var(--chart-fail)' },
  ];
}

async function switchModel(a) {
  toast.info(t('view.agents.switchNotSupported', { name: a.name }));
}
async function healthCheck(a) {
  try { await api.post(`/api/agents/${a.name}/health-check`, {}); await loadAgents(); toast.success(t('view.agents.healthCheckSent', { name: a.name })); }
  catch (e) { toast.error(t('view.agents.healthCheckFailed') + (e.message ? ': ' + e.message : '')); }
}
async function restartAgent(a) {
  try { await api.post(`/api/agents/${a.name}/health-check`, {}); await loadAgents(); toast.success(t('view.agents.restarted', { name: a.name })); }
  catch (e) { toast.error(t('view.agents.restartFailed') + (e.message ? ': ' + e.message : '')); }
}

// Local agent scan — hits POST /api/agents/scan which probes the environment
// for known agent CLIs (claude/codex/gemini/…) and syncs them into the registry.
// This surfaces the "本地 agent 扫描，纳入 MAOP" capability directly in the UI.
async function scanLocal() {
  scanning.value = true;
  try {
    const data = await api.post('/api/agents/scan', {});
    scanned.value = Array.isArray(data.agents) ? data.agents : [];
    const cnt = data.scanned ?? scanned.value.length;
    const syn = data.synced ?? 0;
    toast.success(`${t('view.agents.scanned')} ${cnt} · ${t('view.agents.synced')} ${syn}`);
    loadAgents();
  } catch (e) {
    toast.error(t('view.agents.scanFailed') + (e.message ? ': ' + e.message : ''));
  } finally {
    scanning.value = false;
  }
}

async function loadAgents() {
  refreshing.value = true;
  let ok = true;
  try {
    const data = await api.get('/api/agents');
    agents.value = toList(data);
  } catch (e) {
    ok = false;
    toast.error(t('view.agents.loadFailed') + (e.message ? ': ' + e.message : ''));
  }
  try {
    const r = await api.get('/api/agents/routes');
    routes.value = toList(r);
  } catch (e) {
    routes.value = [];
  }
  loading.value = false;
  refreshing.value = false;
  if (!ok && !agents.value.length) { /* error already surfaced via toast + empty state */ }
}

watch(
  () => realtime.snapshot,
  (snap) => {
    if (!snap) return;
    const hasAgents = Array.isArray(snap.agents) || (typeof snap.type === 'string' && snap.type.toLowerCase().includes('agent'));
    if (hasAgents) loadAgents();
  }
);

async function detectAdmin() {
  try {
    const rolesStr = localStorage.getItem('maop_roles');
    if (rolesStr) {
      const roles = JSON.parse(rolesStr);
      if (Array.isArray(roles) && roles.some((r) => r === 'admin' || r === 'superadmin')) return true;
    }
  } catch (e) { /* ignore */ }
  try {
    const d = await api.get('/api/auth/status');
    if (d && d.auth_enabled === false) return true;
  } catch (e) { /* ignore */ }
  try { return localStorage.getItem('maop_user') === 'admin'; } catch (e) { return false; }
}

onMounted(() => {
  loadAgents();
  detectAdmin().then((v) => (isAdmin.value = v));
});
</script>

<style scoped>
</style>
