<template>
  <div class="overview">
    <div class="topbar">
      <h1>Overview</h1>
      <span class="edition-badge">{{ edition.edition }}</span>
      <span class="uptime">{{ uptime }}</span>
    </div>
    <div class="stats-grid">
      <StatCard
        v-for="s in stats"
        :key="s.label"
        :icon="s.icon"
        :icon-bg="s.bg"
        :label="s.label"
        :value="s.value"
      />
    </div>
    <div class="row">
      <Panel title="System Health">
        <div class="health-bars">
          <div class="health-item" v-for="h in health" :key="h.name">
            <span class="health-name">{{ h.name }}</span>
            <div class="health-bar"><div class="health-fill" :style="{ width: (h.pct === '--' ? 0 : h.pct) + '%', background: h.color }"></div></div>
            <span class="health-pct">{{ h.pct === '--' ? '--' : h.pct + '%' }}</span>
          </div>
        </div>
      </Panel>
      <Panel title="Recent Activity">
        <div class="activity-list">
          <div class="activity-item" v-for="a in activities" :key="a.id">
            <span class="activity-time">{{ a.time }}</span>
            <span class="activity-dot" :style="{ background: a.color }"></span>
            <span class="activity-text">{{ a.text }}</span>
          </div>
          <div v-if="!activities.length" class="activity-empty">暂无活动</div>
        </div>
      </Panel>
    </div>
    <div class="degradation-panel" v-if="edition.hasDegradations">
      <h3>⚠️ Active Degradations</h3>
      <div class="degradation-item" v-for="d in edition.degradations" :key="d.backend">
        {{ d.backend }}: {{ d.requested }} → {{ d.fallback }} ({{ d.reason }})
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted, computed } from 'vue';
import { useApiStore } from '../stores/api.js';
import { useEditionStore } from '../stores/edition.js';
import { StatCard, Panel } from '../components/index.js';

const api = useApiStore();
const edition = useEditionStore();
const uptime = ref('--');
const agentCount = ref(0);
const memEntries = ref(0);
const costToday = ref('0.00');
const taskCount = ref(0);

const stats = computed(() => [
  { icon: '🤖', label: 'Active Agents', value: agentCount.value, bg: 'rgba(59,130,246,.12)' },
  { icon: '🧠', label: 'Memory Entries', value: memEntries.value, bg: 'rgba(167,139,250,.12)' },
  { icon: '💰', label: 'Cost Today', value: '$' + costToday.value, bg: 'rgba(34,197,94,.12)' },
  { icon: '⚡', label: 'Tasks Run', value: taskCount.value, bg: 'rgba(249,115,22,.12)' },
]);

const health = ref([
  { name: 'API Server', pct: '--', color: '#22c55e' },
  { name: 'Memory Store', pct: '--', color: '#3b82f6' },
  { name: 'Agent Pool', pct: '--', color: '#a78bfa' },
  { name: 'Queue', pct: '--', color: '#f59e0b' },
  { name: 'CPU', pct: '--', color: '#ef4444' },
]);

const activities = ref([]);

let refreshTimer = null;

function formatRelativeTime(ts) {
  if (!ts) return '';
  const d = new Date(ts);
  if (isNaN(d.getTime())) return '';
  const diff = Math.max(0, Math.floor((Date.now() - d.getTime()) / 1000));
  if (diff < 60) return diff + 's ago';
  if (diff < 3600) return Math.floor(diff / 60) + 'm ago';
  if (diff < 86400) return Math.floor(diff / 3600) + 'h ago';
  return Math.floor(diff / 86400) + 'd ago';
}

function levelColor(level) {
  const l = (level || '').toLowerCase();
  if (l === 'error' || l === 'err') return '#ef4444';
  if (l === 'warn' || l === 'warning') return '#f59e0b';
  if (l === 'info') return '#3b82f6';
  return '#a78bfa';
}

async function loadStats() {
  try {
    const h = await api.get('/api/health');
    const ms = h.uptime_ms || 0;
    const s = Math.floor(ms / 1000);
    uptime.value = s < 60 ? s + 's' : s < 3600 ? Math.floor(s / 60) + 'm' : Math.floor(s / 3600) + 'h ' + Math.floor((s % 3600) / 60) + 'm';
  } catch {}
  try {
    const r = await api.get('/api/report?hours=24');
    // F-P0-2 fix: use actual backend response fields
    agentCount.value = r.by_agent?.length || 0;
    memEntries.value = 0;  // not available in report
    costToday.value = '0.00';  // not available in report
    taskCount.value = r.total_delegations || 0;
  } catch {}
}

async function loadHealth() {
  try {
    // F-P0-2 fix: use /api/snapshot (has aggregated metrics)
    const snap = await api.get('/api/snapshot');
    health.value[0].pct = 100;
    health.value[1].pct = Math.min(100, Math.round(snap.memory_usage_pct || 0));
    health.value[2].pct = Math.min(100, Math.round((snap.healthy_agents || 0) / Math.max(1, snap.total_agents || 1) * 100));
    health.value[3].pct = Math.min(100, Math.round(snap.queue_health_pct || 0));
    health.value[4].pct = Math.min(100, Math.round(snap.cpu_pct || 0));
  } catch {
    for (const h of health.value) h.pct = '--';
  }
}

async function loadActivities() {
  try {
    const data = await api.get('/api/logs?limit=10');
    const logs = Array.isArray(data) ? data : (data && data.logs) || [];
    activities.value = logs.map((l, i) => ({
      id: i + 1,
      time: formatRelativeTime(l.ts),
      text: '[' + (l.level || 'info') + '] ' + (l.agent || 'system') + ': ' + (l.msg || ''),
      color: levelColor(l.level),
    }));
  } catch {
    activities.value = [];
  }
}

onMounted(async () => {
  await Promise.allSettled([loadStats(), loadHealth(), loadActivities()]);
  refreshTimer = setInterval(() => {
    loadStats();
    loadHealth();
  }, 30000);
});

onUnmounted(() => {
  if (refreshTimer) clearInterval(refreshTimer);
});
</script>

<style scoped>
.topbar { display: flex; align-items: center; gap: 12px; margin-bottom: 24px; }
.topbar h1 { font-size: 24px; font-weight: 700; }
.edition-badge { background: var(--accent); color: #fff; padding: 2px 10px; border-radius: 6px; font-size: 11px; font-weight: 600; text-transform: uppercase; }
.uptime { margin-left: auto; font-size: 12px; color: var(--text3); }
.stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; margin-bottom: 24px; }
.row { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 24px; }
.health-item { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; }
.health-name { width: 100px; font-size: 12px; color: var(--text2); }
.health-bar { flex: 1; height: 6px; background: var(--bg3); border-radius: 3px; overflow: hidden; }
.health-fill { height: 100%; border-radius: 3px; transition: width .3s; }
.health-pct { width: 36px; font-size: 12px; text-align: right; color: var(--text3); }
.activity-item { display: flex; align-items: center; gap: 8px; padding: 6px 0; border-bottom: 1px solid var(--border); font-size: 13px; }
.activity-item:last-child { border-bottom: none; }
.activity-time { font-size: 11px; color: var(--text3); width: 56px; }
.activity-dot { width: 6px; height: 6px; border-radius: 50%; flex-shrink: 0; }
.activity-empty { font-size: 13px; color: var(--text3); padding: 12px 0; text-align: center; }
.degradation-panel { background: rgba(245,158,11,.08); border: 1px solid rgba(245,158,11,.2); border-radius: var(--radius); padding: 16px; }
.degradation-panel h3 { font-size: 14px; color: var(--warn); margin-bottom: 8px; }
.degradation-item { font-size: 13px; color: var(--text2); padding: 4px 0; }
</style>
