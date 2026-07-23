<template>
  <div class="evolve-page">
    <div class="topbar">
      <h1>Self-Evolution</h1>
      <span class="status-badge" :class="evolving ? 'running' : 'idle'">{{ evolving ? 'Evolving...' : 'Idle' }}</span>
      <button class="btn-action" @click="triggerEvolve" :disabled="evolving">🧬 Trigger Evolution</button>
    </div>

    <div class="stats-row">
      <StatCard
        v-for="s in statsCards"
        :key="s.label"
        :label="s.label"
        :value="s.value"
        :icon="s.icon"
        :icon-bg="s.bg"
      />
    </div>

    <div class="two-col">
      <Panel title="Evolution Strategies" :shadow="false">
        <div class="strategy-list">
          <div v-for="st in strategies" :key="st.name" class="strategy-item" :class="{ active: st.enabled }">
            <div class="strat-header">
              <span class="strat-icon">{{ st.icon }}</span>
              <span class="strat-name">{{ st.name }}</span>
              <span class="strat-badge" :class="st.enabled ? 'on' : 'off'">{{ st.enabled ? 'ON' : 'OFF' }}</span>
            </div>
            <p class="strat-desc">{{ st.description }}</p>
            <div class="strat-meta">
              <span>Runs: {{ st.runs }}</span>
              <span>Success: {{ st.success_rate }}%</span>
              <span>Last: {{ st.last_run }}</span>
            </div>
          </div>
        </div>
      </Panel>

      <Panel title="Evolution History" :shadow="false">
        <div class="history-timeline">
          <div v-for="h in history" :key="h.id" class="history-item">
            <div class="timeline-dot" :style="{ background: h.success ? 'var(--success)' : 'var(--fail)' }"></div>
            <div class="history-content">
              <div class="history-header">
                <span class="history-strategy">{{ h.strategy }}</span>
                <span class="history-time">{{ h.time }}</span>
              </div>
              <p class="history-desc">{{ h.description }}</p>
              <div class="history-delta" v-if="h.delta">
                <span v-for="(v, k) in h.delta" :key="k" class="delta-item" :class="v > 0 ? 'up' : 'down'">
                  {{ k }}: {{ v > 0 ? '+' : '' }}{{ v }}
                </span>
              </div>
            </div>
          </div>
        </div>
      </Panel>
    </div>

    <Panel title="Prompt Version History" :shadow="false">
      <div class="version-table">
        <div class="vrow header">
          <span>Version</span><span>Agent</span><span>Changes</span><span>Score Δ</span><span>Time</span>
        </div>
        <div v-for="v in versions" :key="v.id" class="vrow">
          <span class="vnum">v{{ v.version }}</span>
          <span>{{ v.agent }}</span>
          <span class="vchanges">{{ v.changes }}</span>
          <span :class="v.score_delta >= 0 ? 'up' : 'down'">{{ v.score_delta >= 0 ? '+' : '' }}{{ v.score_delta }}</span>
          <span class="vtime">{{ v.time }}</span>
        </div>
      </div>
    </Panel>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue';
import { useApiStore } from '../stores/api.js';
import { StatCard, Panel } from '../components/index.js';

const api = useApiStore();
const evolving = ref(false);
const totalEvolutions = ref(0);
const successRate = ref(0);
const activeStrategies = ref(0);
const lastEvolution = ref('--');

const strategies = ref([
  { name: 'Prompt Mutation', icon: '🧬', description: 'Automatically mutate and test prompt variations for better output quality', enabled: true, runs: 42, success_rate: 78, last_run: '2h ago' },
  { name: 'Agent Selection', icon: '🎯', description: 'Evolve agent routing weights based on task performance history', enabled: true, runs: 28, success_rate: 85, last_run: '4h ago' },
  { name: 'Context Compression', icon: '📦', description: 'Optimize context window usage by evolving compression strategies', enabled: false, runs: 15, success_rate: 62, last_run: '1d ago' },
  { name: 'Tool Selection', icon: '🔧', description: 'Evolve tool selection preferences based on success patterns', enabled: true, runs: 33, success_rate: 71, last_run: '6h ago' },
]);

const history = ref([
  { id: 1, strategy: 'Prompt Mutation', description: 'Improved code-review agent prompt clarity', success: true, time: '2h ago', delta: { quality: 12, speed: -3 } },
  { id: 2, strategy: 'Agent Selection', description: 'Rebalanced routing weights for debugging tasks', success: true, time: '4h ago', delta: { accuracy: 8, cost: -5 } },
  { id: 3, strategy: 'Tool Selection', description: 'Updated tool preferences for file operations', success: false, time: '6h ago', delta: {} },
  { id: 4, strategy: 'Prompt Mutation', description: 'Refined summarization agent output format', success: true, time: '1d ago', delta: { quality: 6, tokens: -15 } },
]);

const versions = ref([
  { id: 1, version: 12, agent: 'code-reviewer', changes: 'Added error pattern detection', score_delta: 8, time: '2h ago' },
  { id: 2, version: 11, agent: 'debugger', changes: 'Refined stack trace analysis', score_delta: 5, time: '4h ago' },
  { id: 3, version: 10, agent: 'summarizer', changes: 'Improved conciseness scoring', score_delta: -2, time: '1d ago' },
  { id: 4, version: 9, agent: 'coder', changes: 'Added test generation hints', score_delta: 12, time: '2d ago' },
]);

const statsCards = ref([
  { icon: '🧬', label: 'Total Evolutions', value: totalEvolutions, bg: 'rgba(59,130,246,.12)' },
  { icon: '✅', label: 'Success Rate', value: successRate.value + '%', bg: 'rgba(34,197,94,.12)' },
  { icon: '🎯', label: 'Active Strategies', value: activeStrategies, bg: 'rgba(167,139,250,.12)' },
  { icon: '⏱️', label: 'Last Evolution', value: lastEvolution, bg: 'rgba(249,115,22,.12)' },
]);

async function triggerEvolve() {
  evolving.value = true;
  try { await api.post('/api/evolve/analyze', { strategies: 'all' }); } catch {}
  setTimeout(() => { evolving.value = false; }, 3000);
}

onMounted(async () => {
  try {
    const resp = await api.get('/api/evolve/status');
    const data = resp.data || resp;
    totalEvolutions.value = data.total_evolutions || 0;
    successRate.value = data.success_rate || 0;
    activeStrategies.value = data.active_strategies || strategies.value.filter(s => s.enabled).length;
    lastEvolution.value = data.last_evolution || '--';
    if (data.strategies) strategies.value = data.strategies;
    if (data.history) history.value = data.history;
    statsCards.value[0].value = totalEvolutions.value;
    statsCards.value[1].value = successRate.value + '%';
    statsCards.value[2].value = activeStrategies.value;
    statsCards.value[3].value = lastEvolution.value;
  } catch {}
});
</script>

<style scoped>
.evolve-page { }
.topbar { display: flex; align-items: center; gap: 12px; margin-bottom: 20px; }
.topbar h1 { font-size: 24px; font-weight: 700; }
.status-badge { padding: 4px 12px; border-radius: 8px; font-size: 12px; font-weight: 600; }
.status-badge.running { background: rgba(59,130,246,.15); color: var(--accent); }
.status-badge.idle { background: var(--bg2); color: var(--text3); }
.btn-action { margin-left: auto; background: var(--accent); color: #fff; border: none; border-radius: 10px; padding: 8px 16px; font-size: 13px; cursor: pointer; }
.btn-action:disabled { opacity: .5; }

.stats-row { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 24px; }

.two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 24px; }

.strategy-item { padding: 12px; border: 1px solid var(--border); border-radius: 10px; margin-bottom: 10px; transition: all .15s; }
.strategy-item.active { border-color: var(--accent); background: color-mix(in srgb, var(--accent) 5%, var(--bg2)); }
.strat-header { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
.strat-icon { font-size: 18px; }
.strat-name { font-size: 14px; font-weight: 600; }
.strat-badge { margin-left: auto; font-size: 10px; padding: 2px 8px; border-radius: 4px; font-weight: 700; }
.strat-badge.on { background: rgba(34,197,94,.15); color: var(--success); }
.strat-badge.off { background: var(--bg); color: var(--text3); }
.strat-desc { font-size: 12px; color: var(--text3); margin-bottom: 6px; line-height: 1.5; }
.strat-meta { display: flex; gap: 12px; font-size: 11px; color: var(--text3); }

.history-timeline { position: relative; padding-left: 20px; }
.history-item { display: flex; gap: 12px; margin-bottom: 16px; position: relative; }
.timeline-dot { width: 10px; height: 10px; border-radius: 50%; position: absolute; left: -20px; top: 4px; }
.history-content { flex: 1; }
.history-header { display: flex; align-items: center; gap: 8px; margin-bottom: 4px; }
.history-strategy { font-size: 13px; font-weight: 600; }
.history-time { margin-left: auto; font-size: 11px; color: var(--text3); }
.history-desc { font-size: 13px; color: var(--text2); }
.history-delta { display: flex; gap: 8px; margin-top: 6px; }
.delta-item { font-size: 11px; padding: 2px 8px; border-radius: 4px; font-weight: 600; }
.delta-item.up { background: rgba(34,197,94,.1); color: var(--success); }
.delta-item.down { background: rgba(239,68,68,.1); color: var(--fail); }

.version-table { }
.vrow { display: grid; grid-template-columns: 60px 120px 1fr 80px 80px; gap: 8px; padding: 8px 0; border-bottom: 1px solid var(--border); font-size: 13px; align-items: center; }
.vrow.header { font-weight: 600; color: var(--text3); font-size: 11px; text-transform: uppercase; }
.vnum { font-family: monospace; color: var(--accent); font-weight: 600; }
.vchanges { color: var(--text2); }
.vtime { color: var(--text3); font-size: 12px; }
.up { color: var(--success); font-weight: 600; }
.down { color: var(--fail); font-weight: 600; }
</style>
