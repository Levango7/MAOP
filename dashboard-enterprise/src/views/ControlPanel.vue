<template>
  <div class="control-panel">
    <div class="topbar">
      <h1>Control Panel</h1>
      <button class="btn-refresh" @click="refreshAll">↻ Refresh</button>
    </div>

    <div class="section">
      <h3>Execution Controls</h3>
      <div class="btn-grid">
        <button class="ctrl-btn green" @click="execAction('run')" :disabled="loading">▶ Run Task</button>
        <button class="ctrl-btn orange" @click="execAction('pause')" :disabled="loading">⏸ Pause</button>
        <button class="ctrl-btn blue" @click="execAction('resume')" :disabled="loading">⏵ Resume</button>
        <button class="ctrl-btn red" @click="execAction('stop')" :disabled="loading">⏹ Stop</button>
        <button class="ctrl-btn purple" @click="execAction('validate')" :disabled="loading">✓ Validate Config</button>
        <button class="ctrl-btn blue" @click="execAction('status')" :disabled="loading">◉ View Status</button>
      </div>
      <div v-if="execResult" class="result-bar" :class="execResult.ok ? 'ok' : 'err'">{{ execResult.msg }}</div>
    </div>

    <div class="section">
      <h3>Maintenance Actions</h3>
      <div class="btn-grid">
        <button class="ctrl-btn" @click="maintainAction('log-rotate')" :disabled="loading">📄 Log Rotate</button>
        <button class="ctrl-btn" @click="maintainAction('prune')" :disabled="loading">🧹 Memory Prune</button>
        <button class="ctrl-btn" @click="maintainAction('health')" :disabled="loading">🩺 Health Check</button>
        <button class="ctrl-btn" @click="maintainAction('backup')" :disabled="loading">💾 Backup</button>
        <button class="ctrl-btn" @click="maintainAction('cache-clear')" :disabled="loading">🗑 Cache Clear</button>
        <button class="ctrl-btn" @click="maintainAction('reload')" :disabled="loading">🔄 Config Reload</button>
      </div>
      <div v-if="maintResult" class="result-bar" :class="maintResult.ok ? 'ok' : 'err'">{{ maintResult.msg }}</div>
    </div>

    <div class="section">
      <h3>Running Jobs</h3>
      <div v-if="jobs.length === 0" class="empty">No running jobs</div>
      <div v-else class="job-table">
        <div class="jrow header">
          <span>Job</span><span>Status</span><span>Started</span><span>Action</span>
        </div>
        <div class="jrow" v-for="j in jobs" :key="j.id || j.name">
          <span class="job-name">{{ j.name || j.id }}</span>
          <span class="status-badge" :class="statusClass(j.status)">{{ j.status || 'unknown' }}</span>
          <span class="mono">{{ j.started_at || '—' }}</span>
          <span class="actions-cell">
            <button class="act-btn small" @click="execAction('stop', j.name || j.id)">⏹ Stop</button>
          </span>
        </div>
      </div>
    </div>

    <div class="section">
      <h3>Agent Upgrade</h3>
      <button class="btn-check" @click="checkUpgrade" :disabled="loading">Check for Upgrades</button>
      <div v-if="agents.length === 0 && !loading" class="empty">No upgrade info available</div>
      <div v-else class="upgrade-table">
        <div class="jrow header">
          <span>Agent</span><span>Current</span><span>Latest</span><span>Status</span><span>Action</span>
        </div>
        <div class="jrow" v-for="a in agents" :key="a.name">
          <span class="job-name">{{ a.name }}</span>
          <span class="mono">{{ a.current || '—' }}</span>
          <span class="mono">{{ a.latest || '—' }}</span>
          <span class="status-badge" :class="upgradeStatusClass(a.status)">{{ a.status || '—' }}</span>
          <span class="actions-cell">
            <button class="act-btn small" @click="upgradeAgent(a.name)" :disabled="a.status === 'up-to-date'">⬆ Upgrade</button>
          </span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue';
import { useApiStore } from '../stores/api.js';

const api = useApiStore();
const loading = ref(false);
const jobs = ref([]);
const agents = ref([]);
const execResult = ref(null);
const maintResult = ref(null);

function statusClass(status) {
  if (!status) return 'unknown';
  const s = status.toLowerCase();
  if (s === 'running' || s === 'active') return 'running';
  if (s === 'paused') return 'paused';
  if (s === 'completed' || s === 'success') return 'success';
  if (s === 'failed' || s === 'error') return 'error';
  if (s === 'pending' || s === 'queued') return 'pending';
  return 'unknown';
}

function upgradeStatusClass(status) {
  if (!status) return 'unknown';
  const s = status.toLowerCase();
  if (s === 'up-to-date') return 'success';
  if (s === 'upgrade-available') return 'pending';
  if (s === 'upgrading') return 'running';
  if (s === 'error') return 'error';
  return 'unknown';
}

async function execAction(action, task) {
  loading.value = true;
  execResult.value = null;
  try {
    // F-P0-5 fix: dispatch to correct endpoint per action
    const body = {};
    if (task) body.task = task;
    let r;
    if (action === 'status') {
      // GET /api/control/status
      r = await api.get('/api/control/status');
    } else {
      // POST /api/control/{action}
      const validActions = ['run', 'pause', 'resume', 'stop', 'validate', 'doctor'];
      if (!validActions.includes(action)) {
        throw new Error(`Unknown action: ${action}`);
      }
      r = await api.post(`/api/control/${action}`, body);
    }
    execResult.value = { ok: true, msg: r.message || r.detail || `${action} executed` };
    await loadJobs();
  } catch (e) {
    execResult.value = { ok: false, msg: e.message || `${action} failed` };
  } finally {
    loading.value = false;
  }
}

async function maintainAction(action) {
  loading.value = true;
  maintResult.value = null;
  try {
    const r = await api.post('/api/control/maintain', { action });
    maintResult.value = { ok: true, msg: r.message || r.detail || `${action} completed` };
  } catch (e) {
    maintResult.value = { ok: false, msg: e.message || `${action} failed` };
  } finally {
    loading.value = false;
  }
}

async function loadJobs() {
  try {
    const data = await api.get('/api/control/status');
    jobs.value = Array.isArray(data) ? data : (data.jobs || []);
  } catch {
    jobs.value = [];
  }
}

async function checkUpgrade() {
  loading.value = true;
  try {
    const data = await api.get('/api/agent/upgrade');
    agents.value = data.agents || [];
  } catch {
    agents.value = [];
  } finally {
    loading.value = false;
  }
}

async function upgradeAgent(name) {
  loading.value = true;
  try {
    await api.post('/api/agent/upgrade?agent=' + encodeURIComponent(name), {});
    await checkUpgrade();
  } catch {
  } finally {
    loading.value = false;
  }
}

async function refreshAll() {
  loading.value = true;
  await Promise.allSettled([loadJobs(), checkUpgrade()]);
  loading.value = false;
}

onMounted(refreshAll);
</script>

<style scoped>
.topbar { display: flex; align-items: center; gap: 12px; margin-bottom: 24px; }
.topbar h1 { font-size: 24px; font-weight: 700; }
.btn-refresh { margin-left: auto; background: var(--bg2); border: 1px solid var(--border); border-radius: 10px; padding: 6px 14px; font-size: 13px; color: var(--text2); cursor: pointer; }
.btn-refresh:hover { border-color: var(--accent); }

.section { background: var(--bg2); border: 1px solid var(--border); border-radius: var(--radius); padding: 20px; margin-bottom: 20px; }
.section h3 { font-size: 14px; font-weight: 600; color: var(--text2); margin-bottom: 14px; }

.btn-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: 10px; }
.ctrl-btn { background: var(--bg); border: 1px solid var(--border); border-radius: 10px; padding: 10px 14px; font-size: 13px; font-weight: 500; color: var(--text); cursor: pointer; transition: all .15s; text-align: center; }
.ctrl-btn:hover:not(:disabled) { border-color: var(--accent); background: color-mix(in srgb, var(--accent) 8%, var(--bg)); }
.ctrl-btn:disabled { opacity: .45; cursor: not-allowed; }
.ctrl-btn.green { border-left: 3px solid var(--success); }
.ctrl-btn.orange { border-left: 3px solid var(--warn); }
.ctrl-btn.blue { border-left: 3px solid var(--accent); }
.ctrl-btn.red { border-left: 3px solid var(--fail); }
.ctrl-btn.purple { border-left: 3px solid #a78bfa; }

.result-bar { margin-top: 12px; padding: 8px 14px; border-radius: 8px; font-size: 13px; }
.result-bar.ok { background: rgba(34,197,94,.1); color: var(--success); border: 1px solid rgba(34,197,94,.2); }
.result-bar.err { background: rgba(239,68,68,.1); color: var(--fail); border: 1px solid rgba(239,68,68,.2); }

.empty { font-size: 13px; color: var(--text3); padding: 8px 0; }

.job-table, .upgrade-table { background: var(--bg); border: 1px solid var(--border); border-radius: 8px; overflow: hidden; }
.jrow { display: grid; grid-template-columns: 1.5fr 1fr 1.2fr 0.8fr; gap: 8px; padding: 10px 16px; font-size: 13px; align-items: center; border-bottom: 1px solid var(--border); }
.upgrade-table .jrow { grid-template-columns: 1.2fr 0.8fr 0.8fr 1fr 0.8fr; }
.jrow.header { font-weight: 600; color: var(--text3); font-size: 11px; text-transform: uppercase; background: var(--bg2); }
.jrow:last-child { border-bottom: none; }
.job-name { font-weight: 600; color: var(--accent); }
.mono { font-family: monospace; font-size: 12px; }
.actions-cell { display: flex; gap: 4px; }

.status-badge { padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; }
.status-badge.running { background: rgba(59,130,246,.15); color: var(--accent); }
.status-badge.paused { background: rgba(245,158,11,.15); color: var(--warn); }
.status-badge.success { background: rgba(34,197,94,.15); color: var(--success); }
.status-badge.error { background: rgba(239,68,68,.15); color: var(--fail); }
.status-badge.pending { background: rgba(167,139,250,.15); color: #a78bfa; }
.status-badge.unknown { background: rgba(148,163,184,.15); color: var(--text3); }

.act-btn { background: var(--bg2); border: 1px solid var(--border); border-radius: 8px; padding: 5px 10px; font-size: 12px; color: var(--text2); cursor: pointer; }
.act-btn:hover:not(:disabled) { border-color: var(--accent); }
.act-btn:disabled { opacity: .45; cursor: not-allowed; }
.act-btn.small { padding: 3px 8px; font-size: 11px; }

.btn-check { background: var(--bg); border: 1px solid var(--border); border-radius: 10px; padding: 8px 16px; font-size: 13px; color: var(--text2); cursor: pointer; margin-bottom: 14px; }
.btn-check:hover:not(:disabled) { border-color: var(--accent); }
.btn-check:disabled { opacity: .45; cursor: not-allowed; }
</style>