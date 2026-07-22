<template>
  <div class="logs-page">
    <div class="topbar">
      <h1>Logs & Analysis</h1>
      <div class="type-bar">
        <button v-for="t in logTypes" :key="t.key" :class="['type-btn', { active: logType === t.key }]" @click="logType = t.key">{{ t.label }}</button>
      </div>
      <button class="btn-action" @click="loadLogs">↻ Refresh</button>
    </div>

    <div class="filter-bar">
      <input class="filter-input" v-model="filter" placeholder="Filter logs..." />
      <div class="time-range">
        <label>Start</label>
        <input type="datetime-local" v-model="startTime" />
        <label>End</label>
        <input type="datetime-local" v-model="endTime" />
      </div>
    </div>

    <div class="two-col">
      <Panel title="Log Output" :bodyPadding="0" class="log-panel">
        <template #actions>
          <span class="line-count">{{ displayLines.length }} / {{ maxLines }}</span>
        </template>
        <div class="log-content" ref="logContainer">
          <div class="log-line" v-for="(line, i) in displayLines" :key="i" :class="lineClass(line)">{{ line }}</div>
          <div class="log-empty" v-if="displayLines.length === 0">No logs to display</div>
        </div>
      </Panel>

      <Panel title="Log Analysis" :bodyPadding="16" class="analysis-panel">
        <div class="stat-cards">
          <StatCard label="Total" :value="analysis.total" centered />
          <StatCard label="Success" :value="analysis.by_status.success" centered variant="success" />
          <StatCard label="Failure" :value="analysis.by_status.failure" centered variant="fail" />
          <StatCard label="Timeout" :value="analysis.by_status.timeout" centered variant="warn" />
        </div>

        <div class="section">
          <h4>By Agent</h4>
          <div class="agent-dist">
            <div class="dist-row" v-for="(count, name) in analysis.by_agent" :key="name">
              <span class="dist-name">{{ name }}</span>
              <div class="dist-bar"><div class="dist-fill" :style="{ width: agentPct(count) + '%' }"></div></div>
              <span class="dist-count">{{ count }}</span>
            </div>
            <div class="empty-hint" v-if="Object.keys(analysis.by_agent).length === 0">No data</div>
          </div>
        </div>

        <div class="section">
          <h4>Error Patterns (Top 10)</h4>
          <div class="error-patterns">
            <div class="err-row" v-for="(ep, i) in analysis.error_patterns" :key="i">
              <span class="err-rank">{{ i + 1 }}</span>
              <span class="err-msg">{{ ep[0] }}</span>
              <span class="err-count">{{ ep[1] }}</span>
            </div>
            <div class="empty-hint" v-if="analysis.error_patterns.length === 0">No errors</div>
          </div>
        </div>
      </Panel>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, nextTick } from 'vue';
import { StatCard, Panel } from '../components/index.js';
import { useApiStore } from '../stores/api.js';

const api = useApiStore();
const logType = ref('all');
const filter = ref('');
const startTime = ref('');
const endTime = ref('');
const rawLogs = ref('');
const logContainer = ref(null);
const maxLines = 500;

const analysis = ref({
  total: 0,
  by_status: { success: 0, failure: 0, timeout: 0 },
  by_agent: {},
  error_patterns: [],
});

const logTypes = [
  { key: 'all', label: 'All' },
  { key: 'dashboard', label: 'Dashboard' },
  { key: 'delegations', label: 'Delegations' },
  { key: 'checker', label: 'Checker' },
];

const allLines = computed(() => {
  let text = '';
  if (typeof rawLogs.value === 'string') {
    text = rawLogs.value;
  } else if (rawLogs.value && rawLogs.value.content) {
    text = rawLogs.value.content;
  } else if (Array.isArray(rawLogs.value)) {
    text = rawLogs.value.map(l => (typeof l === 'string' ? l : JSON.stringify(l))).join('\n');
  } else if (rawLogs.value && rawLogs.value.logs) {
    text = rawLogs.value.logs.map(l => (typeof l === 'string' ? l : JSON.stringify(l))).join('\n');
  }
  return text.split('\n');
});

const displayLines = computed(() => {
  let lines = allLines.value;
  if (filter.value.trim()) {
    const q = filter.value.toLowerCase();
    lines = lines.filter(l => l.toLowerCase().includes(q));
  }
  if (startTime.value || endTime.value) {
    lines = lines.filter(l => {
      const ts = extractTimestamp(l);
      if (!ts) return true;
      if (startTime.value && ts < startTime.value) return false;
      if (endTime.value && ts > endTime.value) return false;
      return true;
    });
  }
  return lines.slice(-maxLines);
});

function extractTimestamp(line) {
  const m = line.match(/\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}/);
  return m ? m[0].replace('T', ' ') : null;
}

function lineClass(line) {
  const l = line.toLowerCase();
  if (l.includes('error') || l.includes('fail') || l.includes('exception')) return 'line-error';
  if (l.includes('warn')) return 'line-warn';
  if (l.includes('success') || l.includes('ok') || l.includes('done')) return 'line-success';
  return '';
}

function agentPct(count) {
  const max = Math.max(...Object.values(analysis.value.by_agent), 1);
  return Math.round((count / max) * 100);
}

async function loadLogs() {
  try {
    const data = await api.get(`/api/logs?type=${logType.value}`);
    rawLogs.value = data;
  } catch {
    rawLogs.value = '';
  }
  await nextTick();
  if (logContainer.value) {
    logContainer.value.scrollTop = logContainer.value.scrollHeight;
  }
}

async function loadAnalysis() {
  try {
    const data = await api.get('/api/logs/analysis');
    analysis.value = {
      total: data.total || 0,
      by_status: {
        success: data.by_status?.success || 0,
        failure: data.by_status?.failure || 0,
        timeout: data.by_status?.timeout || 0,
      },
      by_agent: data.by_agent || {},
      error_patterns: data.error_patterns || [],
    };
  } catch {}
}

onMounted(() => {
  loadLogs();
  loadAnalysis();
});
</script>

<style scoped>
.logs-page { }
.topbar { display: flex; align-items: center; gap: 12px; margin-bottom: 16px; }
.topbar h1 { font-size: 24px; font-weight: 700; }
.type-bar { display: flex; gap: 4px; background: var(--bg2); border-radius: 10px; padding: 3px; margin-left: 16px; }
.type-btn { background: none; border: none; padding: 6px 14px; border-radius: 8px; font-size: 13px; color: var(--text2); cursor: pointer; }
.type-btn.active { background: var(--accent); color: #fff; }
.btn-action { margin-left: auto; background: var(--bg2); border: 1px solid var(--border); border-radius: 10px; padding: 6px 14px; font-size: 13px; color: var(--text2); cursor: pointer; }

.filter-bar { display: flex; align-items: center; gap: 12px; margin-bottom: 16px; }
.filter-input { flex: 1; background: var(--bg2); border: 1px solid var(--border); border-radius: 10px; padding: 8px 14px; font-size: 13px; color: var(--text1); outline: none; }
.filter-input:focus { border-color: var(--accent); }
.time-range { display: flex; align-items: center; gap: 6px; }
.time-range label { font-size: 12px; color: var(--text2); font-weight: 600; }
.time-range input { background: var(--bg2); border: 1px solid var(--border); border-radius: 6px; padding: 4px 8px; font-size: 12px; color: var(--text1); }

.two-col { display: grid; grid-template-columns: 1fr 380px; gap: 16px; }

/* log-panel: Panel provides bg/border/radius; we add flex+max-height for scrollable log area */
.log-panel { display: flex; flex-direction: column; max-height: 600px; }
.log-panel :deep(.panel-header) { padding: 12px 16px; border-bottom: 1px solid var(--border); margin-bottom: 0; }
.log-panel :deep(.panel-body) { flex: 1; min-height: 0; display: flex; flex-direction: column; }

.line-count { font-size: 11px; color: var(--text2); font-family: monospace; }

.log-content { flex: 1; overflow-y: auto; padding: 8px 0; font-family: 'Cascadia Code', 'Fira Code', 'Consolas', monospace; font-size: 12px; line-height: 1.6; }
.log-line { padding: 1px 16px; white-space: pre-wrap; word-break: break-all; color: var(--text2); }
.log-line.line-error { color: var(--fail); }
.log-line.line-warn { color: var(--warn); }
.log-line.line-success { color: var(--success); }
.log-empty { padding: 40px; text-align: center; color: var(--text2); font-family: inherit; }

/* analysis-panel: Panel provides bg/border/radius/padding; we add max-height+overflow for scroll */
.analysis-panel { max-height: 600px; overflow-y: auto; }

.stat-cards { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; margin-bottom: 20px; }

.section { margin-bottom: 16px; }
.section h4 { font-size: 12px; font-weight: 600; color: var(--text2); margin-bottom: 10px; text-transform: uppercase; letter-spacing: .5px; }

.agent-dist { }
.dist-row { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
.dist-name { width: 80px; font-size: 12px; color: var(--text2); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dist-bar { flex: 1; height: 6px; background: var(--bg3); border-radius: 3px; overflow: hidden; }
.dist-fill { height: 100%; background: var(--accent); border-radius: 3px; transition: width .3s; }
.dist-count { width: 36px; font-size: 12px; text-align: right; font-weight: 600; }

.error-patterns { }
.err-row { display: flex; align-items: center; gap: 8px; padding: 6px 0; border-bottom: 1px solid var(--border); }
.err-rank { width: 20px; font-size: 11px; color: var(--text2); font-weight: 700; text-align: center; }
.err-msg { flex: 1; font-size: 12px; color: var(--text1); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-family: monospace; }
.err-count { font-size: 12px; font-weight: 600; color: var(--fail); }
.empty-hint { font-size: 12px; color: var(--text2); padding: 8px 0; text-align: center; }
</style>
