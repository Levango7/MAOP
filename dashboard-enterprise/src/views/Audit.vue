<template>
  <div>
    <div class="topbar">
      <h1>Audit Log</h1>
      <span class="badge">Enterprise</span>
      <div class="filters">
        <select v-model="filters.action" class="input-sm" @change="loadEvents">
          <option value="">All Actions</option>
          <option v-for="a in actions" :key="a" :value="a">{{ a }}</option>
        </select>
        <select v-model="filters.severity" class="input-sm" @change="loadEvents">
          <option value="">All Severity</option>
          <option value="info">Info</option><option value="warning">Warning</option><option value="critical">Critical</option>
        </select>
        <input v-model="filters.actor" class="input-sm" placeholder="Filter by actor..." @change="loadEvents" />
      </div>
    </div>
    <div class="summary-row">
      <StatCard
        v-for="s in summary"
        :key="s.label"
        :label="s.label"
        :value="s.value"
        :accent="s.color"
      />
    </div>
    <Panel overflow="auto">
      <table class="data-table">
        <thead><tr><th>Time</th><th>Action</th><th>Actor</th><th>Tenant</th><th>Resource</th><th>Result</th><th>Severity</th></tr></thead>
        <tbody>
          <tr v-for="e in events" :key="e.event_id" :class="{ critical: e.severity === 'critical' }">
            <td class="mono">{{ formatTime(e.timestamp) }}</td>
            <td><span class="action-badge">{{ e.action }}</span></td>
            <td>{{ e.actor || '—' }}</td>
            <td>{{ e.tenant_id || '—' }}</td>
            <td>{{ e.resource }}</td>
            <td><span :class="e.result === 'success' ? 'text-success' : 'text-fail'">{{ e.result }}</span></td>
            <td><span class="severity-badge" :class="e.severity">{{ e.severity }}</span></td>
          </tr>
          <tr v-if="!events.length"><td colspan="7" class="empty">No audit events found</td></tr>
        </tbody>
      </table>
    </Panel>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue';
import { useApiStore } from '../stores/api.js';
import { StatCard, Panel } from '../components/index.js';

const api = useApiStore();
const events = ref([]);
const filters = ref({ action: '', severity: '', actor: '' });
const summaryData = ref({ total_events: 0, by_action: {}, critical_count: 0 });

const actions = ['login', 'logout', 'api_call', 'agent_execute', 'config_change', 'permission_change', 'tenant_create', 'data_export', 'secret_access', 'system_admin'];

const summary = computed(() => [
  { label: 'Total Events', value: summaryData.value.total_events, color: 'var(--accent)' },
  { label: 'Critical', value: summaryData.value.critical_count, color: 'var(--fail)' },
  { label: 'Actions', value: Object.keys(summaryData.value.by_action || {}).length, color: 'var(--warn)' },
]);

function formatTime(ts) {
  if (!ts) return '—';
  const d = new Date(ts * 1000);
  return d.toLocaleTimeString();
}

async function loadEvents() {
  try {
    const params = new URLSearchParams();
    if (filters.value.action) params.set('action', filters.value.action);
    if (filters.value.severity) params.set('severity', filters.value.severity);
    if (filters.value.actor) params.set('actor', filters.value.actor);
    const data = await api.get('/api/audit/events?' + params.toString());
    events.value = data.events || [];
  } catch { events.value = []; }
  try {
    const s = await api.get('/api/audit/summary');
    summaryData.value = s;
  } catch {}
}

onMounted(loadEvents);
</script>

<style scoped>
.topbar { display: flex; align-items: center; gap: 12px; margin-bottom: 24px; flex-wrap: wrap; }
.topbar h1 { font-size: 24px; font-weight: 700; }
.badge { background: #7c3aed; color: #fff; padding: 2px 10px; border-radius: 6px; font-size: 11px; font-weight: 600; }
.filters { margin-left: auto; display: flex; gap: 8px; }
.input-sm { padding: 6px 10px; border: 1px solid var(--border); border-radius: 6px; background: var(--bg2); color: var(--text); font-size: 12px; }
.summary-row { display: flex; gap: 16px; margin-bottom: 24px; }
.data-table { width: 100%; border-collapse: collapse; }
.data-table th, .data-table td { padding: 10px 12px; text-align: left; border-bottom: 1px solid var(--border); font-size: 13px; white-space: nowrap; }
.data-table th { color: var(--text3); font-size: 11px; text-transform: uppercase; }
.data-table tr.critical { background: rgba(239,68,68,.04); }
.mono { font-family: 'SF Mono', monospace; font-size: 12px; color: var(--text3); }
.action-badge { background: color-mix(in srgb, var(--accent) 12%, transparent); color: var(--accent); padding: 2px 8px; border-radius: 4px; font-size: 11px; }
.severity-badge { padding: 2px 6px; border-radius: 4px; font-size: 11px; font-weight: 600; }
.severity-badge.info { background: rgba(59,130,246,.1); color: var(--accent); }
.severity-badge.warning { background: rgba(245,158,11,.1); color: var(--warn); }
.severity-badge.critical { background: rgba(239,68,68,.1); color: var(--fail); }
.text-success { color: var(--success); }
.text-fail { color: var(--fail); }
.empty { text-align: center; color: var(--text3); padding: 20px; }
</style>