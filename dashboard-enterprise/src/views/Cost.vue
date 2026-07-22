<template>
  <div>
    <div class="topbar">
      <h1>Cost</h1>
      <div class="topbar-actions">
        <select v-model="period" @change="load" class="select-sm">
          <option value="7d">Last 7 days</option>
          <option value="30d">Last 30 days</option>
          <option value="90d">Last 90 days</option>
        </select>
        <button class="btn-primary" @click="load">↻ Refresh</button>
      </div>
    </div>

    <div class="stats-row">
      <StatCard accent="var(--accent)" :value="'$' + (summary.total_cost_usd?.toFixed(4) || '0.0000')" label="Total Cost" />
      <StatCard accent="var(--success)" :value="formatNum(summary.total_tokens)" label="Total Tokens" />
      <StatCard accent="var(--warn)" :value="summary.total_calls || 0" label="Total Calls" />
      <StatCard accent="var(--text3)" :value="(summary.avg_latency_ms?.toFixed(0) || 0) + 'ms'" label="Avg Latency" />
    </div>

    <div class="panels-row">
      <Panel title="Budget Status">
        <div class="budget-section">
          <div class="budget-row">
            <span class="budget-label">Daily</span>
            <div class="budget-bar">
              <div class="budget-fill" :style="{ width: dailyPct + '%', background: budget.daily_over_budget ? 'var(--fail)' : 'var(--accent)' }"></div>
            </div>
            <span class="budget-val">${{ budget.daily_spent_usd?.toFixed(2) || '0.00' }} / ${{ budget.daily_limit_usd?.toFixed(2) || '∞' }}</span>
          </div>
          <div class="budget-row">
            <span class="budget-label">Monthly</span>
            <div class="budget-bar">
              <div class="budget-fill" :style="{ width: monthlyPct + '%', background: budget.monthly_over_budget ? 'var(--fail)' : 'var(--accent)' }"></div>
            </div>
            <span class="budget-val">${{ budget.monthly_spent_usd?.toFixed(2) || '0.00' }} / ${{ budget.monthly_limit_usd?.toFixed(2) || '∞' }}</span>
          </div>
        </div>
      </Panel>

      <Panel title="Cost by Model">
        <div class="breakdown-list">
          <div class="breakdown-item" v-for="(info, model) in summary.by_model" :key="model">
            <span class="breakdown-name">{{ model }}</span>
            <div class="breakdown-bar">
              <div class="breakdown-fill" :style="{ width: modelPct(model) + '%' }"></div>
            </div>
            <span class="breakdown-val">${{ info.cost?.toFixed(4) || '0.0000' }}</span>
          </div>
          <div v-if="!summary.by_model || Object.keys(summary.by_model).length === 0" class="empty-msg">No data</div>
        </div>
      </Panel>
    </div>

    <Panel title="Cost by Agent" style="margin-top:16px">
      <div class="agent-cost-grid">
        <div class="agent-cost-card" v-for="(info, agent) in summary.by_agent" :key="agent">
          <span class="agent-cost-name">{{ agent || 'unknown' }}</span>
          <span class="agent-cost-value">${{ info.cost?.toFixed(4) || '0.0000' }}</span>
          <span class="agent-cost-tokens">{{ formatNum(info.tokens) }} tokens · {{ info.calls }} calls</span>
        </div>
        <div v-if="!summary.by_agent || Object.keys(summary.by_agent).length === 0" class="empty-msg">No data</div>
      </div>
    </Panel>

    <Panel title="Recent Entries" style="margin-top:16px">
      <table class="data-table">
        <thead>
          <tr><th>Time</th><th>Agent</th><th>Model</th><th>Tokens</th><th>Cost</th><th>Latency</th></tr>
        </thead>
        <tbody>
          <tr v-for="e in entries" :key="e.id">
            <td>{{ formatTime(e.created_at) }}</td>
            <td>{{ e.agent || '—' }}</td>
            <td><span class="model-tag">{{ e.model || '—' }}</span></td>
            <td>{{ formatNum(e.total_tokens) }}</td>
            <td class="cost-val">${{ e.cost_usd?.toFixed(6) || '0' }}</td>
            <td>{{ e.latency_ms }}ms</td>
          </tr>
          <tr v-if="entries.length === 0"><td colspan="6" class="empty-msg">No entries</td></tr>
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
const period = ref('7d');
const summary = ref({});
const budget = ref({});
const entries = ref([]);

const dailyPct = computed(() => {
  if (!budget.value.daily_limit_usd) return 0;
  return Math.min(100, (budget.value.daily_spent_usd / budget.value.daily_limit_usd) * 100);
});
const monthlyPct = computed(() => {
  if (!budget.value.monthly_limit_usd) return 0;
  return Math.min(100, (budget.value.monthly_spent_usd / budget.value.monthly_limit_usd) * 100);
});

function modelPct(model) {
  const info = summary.value.by_model?.[model];
  if (!info || !summary.value.total_cost_usd) return 0;
  return Math.min(100, (info.cost / summary.value.total_cost_usd) * 100);
}

function formatNum(n) {
  if (!n) return '0';
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + 'M';
  if (n >= 1_000) return (n / 1_000).toFixed(1) + 'K';
  return String(n);
}

function formatTime(iso) {
  if (!iso) return '—';
  try { return new Date(iso).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }); }
  catch { return iso; }
}

async function load() {
  const days = { '7d': 7, '30d': 30, '90d': 90 }[period.value] || 7;
  const start = new Date(Date.now() - days * 86400000).toISOString().slice(0, 10);
  try { summary.value = await api.get(`/api/cost/summary?start_date=${start}`); } catch { summary.value = {}; }
  try { budget.value = await api.get('/api/cost/budget'); } catch { budget.value = {}; }
  try { entries.value = (await api.get(`/api/cost/entries?start_date=${start}&limit=50`)).entries || []; } catch { entries.value = []; }
}

onMounted(load);
</script>
<style scoped>
.topbar { display: flex; align-items: center; gap: 12px; margin-bottom: 24px; }
.topbar h1 { font-size: 24px; font-weight: 700; }
.topbar-actions { margin-left: auto; display: flex; gap: 8px; align-items: center; }
.select-sm { padding: 6px 10px; border: 1px solid var(--border); border-radius: 8px; background: var(--bg2); font-size: 13px; color: var(--text1); }
.btn-primary { background: var(--accent); color: #fff; border: none; padding: 8px 16px; border-radius: 8px; cursor: pointer; font-size: 13px; }
.stats-row { display: flex; gap: 16px; margin-bottom: 24px; flex-wrap: wrap; }
.panels-row { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.budget-section { display: flex; flex-direction: column; gap: 12px; }
.budget-row { display: flex; align-items: center; gap: 10px; }
.budget-label { width: 60px; font-size: 13px; color: var(--text2); }
.budget-bar { flex: 1; height: 8px; background: var(--bg3); border-radius: 4px; overflow: hidden; }
.budget-fill { height: 100%; border-radius: 4px; transition: width .3s; }
.budget-val { font-size: 12px; color: var(--text3); white-space: nowrap; }
.breakdown-list { display: flex; flex-direction: column; gap: 8px; }
.breakdown-item { display: flex; align-items: center; gap: 10px; }
.breakdown-name { width: 120px; font-size: 13px; color: var(--text2); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.breakdown-bar { flex: 1; height: 6px; background: var(--bg3); border-radius: 3px; overflow: hidden; }
.breakdown-fill { height: 100%; background: var(--accent); border-radius: 3px; transition: width .3s; }
.breakdown-val { width: 80px; font-size: 12px; color: var(--text3); text-align: right; }
.agent-cost-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px; }
.agent-cost-card { background: var(--bg1); border: 1px solid var(--border); border-radius: 8px; padding: 12px; display: flex; flex-direction: column; gap: 4px; }
.agent-cost-name { font-size: 14px; font-weight: 600; }
.agent-cost-value { font-size: 18px; font-weight: 700; color: var(--accent); }
.agent-cost-tokens { font-size: 11px; color: var(--text3); }
.data-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.data-table th { text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--border); color: var(--text3); font-weight: 600; font-size: 12px; }
.data-table td { padding: 8px 10px; border-bottom: 1px solid var(--bg3); }
.model-tag { background: var(--bg3); padding: 2px 6px; border-radius: 4px; font-size: 11px; }
.cost-val { font-weight: 600; color: var(--accent); }
.empty-msg { text-align: center; color: var(--text3); padding: 20px; font-size: 13px; }
@media (max-width: 768px) { .panels-row { grid-template-columns: 1fr; } }
</style>
