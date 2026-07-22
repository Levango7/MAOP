<template>
  <div class="search-page">
    <div class="topbar">
      <h1>Unified Search</h1>
    </div>

    <div class="tab-bar">
      <button v-for="t in tabs" :key="t.key" :class="['tab-btn', { active: activeTab === t.key }]" @click="switchTab(t.key)">
        <span class="tab-icon">{{ t.icon }}</span>{{ t.label }}
      </button>
    </div>

    <div class="search-panel">
      <div class="search-row">
        <input class="search-input" v-model="query" :placeholder="placeholder" @keydown.enter="doSearch" />
        <button class="search-btn" @click="doSearch" :disabled="searching || !query.trim()">
          {{ searching ? 'Searching...' : '🔍 Search' }}
        </button>
      </div>

      <div class="options-row">
        <template v-if="activeTab === 'memory'">
          <div class="opt-group">
            <label>Type</label>
            <select v-model="memoryType">
              <option value="all">All</option>
              <option value="episode">Episode</option>
              <option value="skill">Skill</option>
              <option value="config">Config</option>
              <option value="error">Error</option>
            </select>
          </div>
          <div class="opt-group">
            <label>Top K</label>
            <select v-model.number="memoryTopK">
              <option :value="10">10</option>
              <option :value="20">20</option>
              <option :value="50">50</option>
            </select>
          </div>
        </template>

        <template v-if="activeTab === 'vector'">
          <div class="opt-group">
            <label>Top K</label>
            <select v-model.number="vectorTopK">
              <option :value="5">5</option>
              <option :value="10">10</option>
              <option :value="20">20</option>
            </select>
          </div>
        </template>

        <template v-if="activeTab === 'graph'">
          <div class="opt-group">
            <label>Mode</label>
            <select v-model="graphMode">
              <option value="neighbors">Neighbors</option>
              <option value="nodes">Nodes</option>
              <option value="edges">Edges</option>
            </select>
          </div>
        </template>

        <template v-if="activeTab === 'log'">
          <div class="opt-group">
            <label>Log Type</label>
            <select v-model="logType">
              <option value="all">All</option>
              <option value="dashboard">Dashboard</option>
              <option value="delegations">Delegations</option>
              <option value="checker">Checker</option>
            </select>
          </div>
        </template>

        <template v-if="activeTab === 'agent'">
          <div class="opt-group">
            <label>Status</label>
            <select v-model="agentStatus">
              <option value="all">All</option>
              <option value="ok">OK</option>
              <option value="error">Error</option>
            </select>
          </div>
        </template>
      </div>
    </div>

    <div class="index-stats" v-if="hasStats">
      <div class="stat-chip" v-for="s in indexStatItems" :key="s.label">
        <span class="chip-icon">{{ s.icon }}</span>
        <span class="chip-val">{{ s.value }}</span>
        <span class="chip-lbl">{{ s.label }}</span>
      </div>
    </div>

    <div class="results-area" v-if="results.length > 0">
      <div class="results-header">
        <span>{{ results.length }} results</span>
        <span class="query-time" v-if="searchTime">in {{ searchTime }}ms</span>
      </div>

      <div v-if="activeTab === 'graph' && graphMode === 'edges'" class="edge-list">
        <div class="edge-row" v-for="(e, i) in results" :key="i">
          <span class="edge-src">{{ e.source }}</span>
          <span class="edge-arrow">→</span>
          <span class="edge-tgt">{{ e.target }}</span>
        </div>
      </div>

      <div v-else-if="activeTab === 'graph' && graphMode === 'neighbors'" class="neighbor-list">
        <div class="neighbor-card" v-for="(n, i) in results" :key="i">
          <div class="neighbor-name">{{ n.name }}</div>
          <div class="neighbor-deps">
            <span class="dep-tag" v-for="d in (n.deps || [])" :key="d">{{ d }}</span>
            <span class="no-deps" v-if="!n.deps || n.deps.length === 0">No dependencies</span>
          </div>
        </div>
      </div>

      <div v-else-if="activeTab === 'graph' && graphMode === 'nodes'" class="node-list">
        <div class="node-row" v-for="(n, i) in results" :key="i">
          <span class="node-name">{{ n.name }}</span>
          <span class="node-dep-count">{{ (n.deps || []).length }} deps</span>
        </div>
      </div>

      <div v-else-if="activeTab === 'log'" class="log-results">
        <pre class="log-text">{{ logText }}</pre>
      </div>

      <div v-else-if="activeTab === 'agent'" class="agent-results">
        <div class="agent-card" v-for="a in results" :key="a.name">
          <div class="agent-top">
            <span class="agent-name">{{ a.name }}</span>
            <span class="status-badge" :class="a.status === 'error' ? 'error' : 'ok'">{{ a.status || 'ok' }}</span>
          </div>
          <div class="agent-meta">
            <span v-if="a.model">{{ a.model }}</span>
            <span v-if="a.cli">CLI: {{ a.cli }}</span>
          </div>
          <div class="agent-caps" v-if="a.capabilities && a.capabilities.length">
            <span class="cap-tag" v-for="c in a.capabilities" :key="c">{{ c }}</span>
          </div>
        </div>
      </div>

      <div v-else class="result-cards">
        <div class="result-card" v-for="(r, i) in results" :key="i">
          <div class="result-header">
            <span class="result-rank">#{{ i + 1 }}</span>
            <span v-if="r.score != null" class="result-score" :style="{ background: scoreBg(r.score) }">{{ (r.score * 100).toFixed(1) }}%</span>
            <span v-if="r.type" class="result-type">{{ r.type }}</span>
            <span v-if="r.key || r.id" class="result-id">{{ r.key || r.id }}</span>
          </div>
          <div v-if="r.score != null && activeTab === 'vector'" class="score-bar">
            <div class="score-fill" :style="{ width: Math.min(r.score * 100, 100) + '%', background: scoreBarColor(r.score) }"></div>
          </div>
          <div class="result-content">{{ r.content || r.text || '' }}</div>
        </div>
      </div>
    </div>

    <div class="empty-state" v-else-if="searched">
      <p>No results found for "{{ lastQuery }}"</p>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue';
import { useApiStore } from '../stores/api.js';

const api = useApiStore();
const activeTab = ref('memory');
const query = ref('');
const searching = ref(false);
const searched = ref(false);
const lastQuery = ref('');
const results = ref([]);
const searchTime = ref(0);
const logText = ref('');

const memoryType = ref('all');
const memoryTopK = ref(10);
const vectorTopK = ref(10);
const graphMode = ref('neighbors');
const logType = ref('all');
const agentStatus = ref('all');

const memoryStats = ref({});
const vectorStats = ref({});
const graphStats = ref({});

const tabs = [
  { key: 'memory', label: 'Memory', icon: '🧠' },
  { key: 'vector', label: 'Vector', icon: '🔢' },
  { key: 'graph', label: 'Graph', icon: '🕸️' },
  { key: 'log', label: 'Log', icon: '📋' },
  { key: 'agent', label: 'Agent', icon: '🤖' },
];

const placeholder = computed(() => {
  const map = {
    memory: 'Search memory entries...',
    vector: 'Search by semantic similarity...',
    graph: 'Enter node name...',
    log: 'Search logs...',
    agent: 'Search agents...',
  };
  return map[activeTab.value] || 'Search...';
});

const hasStats = computed(() => {
  return Object.keys(memoryStats.value).length > 0
    || Object.keys(vectorStats.value).length > 0
    || Object.keys(graphStats.value).length > 0;
});

const indexStatItems = computed(() => {
  const items = [];
  const ms = memoryStats.value;
  if (Object.keys(ms).length > 0) {
    items.push({ icon: '🧠', label: 'Memory Entries', value: ms.total_entries || ms.count || 0 });
  }
  const vs = vectorStats.value;
  if (Object.keys(vs).length > 0) {
    items.push({ icon: '🔢', label: 'Vectors', value: vs.total_vectors || vs.count || 0 });
    items.push({ icon: '📐', label: 'Dimensions', value: vs.dimensions || 384 });
  }
  const gs = graphStats.value;
  if (Object.keys(gs).length > 0) {
    items.push({ icon: '🔵', label: 'Nodes', value: gs.total_nodes || gs.nodes || 0 });
    items.push({ icon: '🔗', label: 'Edges', value: gs.total_edges || gs.edges || 0 });
  }
  return items;
});

function scoreBg(s) {
  if (s >= 0.8) return 'rgba(34,197,94,.15)';
  if (s >= 0.5) return 'rgba(59,130,246,.15)';
  return 'rgba(239,68,68,.15)';
}

function scoreBarColor(s) {
  if (s >= 0.8) return 'var(--success)';
  if (s >= 0.5) return 'var(--accent)';
  return 'var(--fail)';
}

function switchTab(key) {
  activeTab.value = key;
  results.value = [];
  logText.value = '';
  searched.value = false;
}

async function doSearch() {
  if (!query.value.trim()) return;
  searching.value = true;
  searched.value = true;
  lastQuery.value = query.value;
  results.value = [];
  logText.value = '';
  const start = performance.now();

  try {
    switch (activeTab.value) {
      case 'memory': {
        const q = `q=${encodeURIComponent(query.value)}&topk=${memoryTopK.value}`;
        const data = await api.get(`/api/memory/search?${q}`);
        let res = data.results || [];
        if (memoryType.value !== 'all') {
          res = res.filter(r => r.type === memoryType.value);
        }
        results.value = res;
        break;
      }
      case 'vector': {
        const data = await api.get(`/api/vector/search?q=${encodeURIComponent(query.value)}&topk=${vectorTopK.value}`);
        results.value = data.results || [];
        break;
      }
      case 'graph': {
        if (graphMode.value === 'neighbors') {
          const data = await api.get(`/api/graph/neighbors?node=${encodeURIComponent(query.value)}`);
          results.value = data.neighbors || [];
        } else if (graphMode.value === 'nodes') {
          const data = await api.get('/api/graph/nodes');
          results.value = data.nodes || [];
        } else {
          const data = await api.get('/api/graph/edges');
          results.value = data.edges || [];
        }
        break;
      }
      case 'log': {
        const data = await api.get(`/api/logs?type=${logType.value}`);
        if (typeof data === 'string') {
          logText.value = data;
        } else if (data && data.content) {
          logText.value = data.content;
        } else if (Array.isArray(data)) {
          logText.value = data.map(l => (typeof l === 'string' ? l : JSON.stringify(l))).join('\n');
        } else if (data && data.logs) {
          logText.value = data.logs.map(l => (typeof l === 'string' ? l : JSON.stringify(l))).join('\n');
        } else {
          logText.value = JSON.stringify(data, null, 2);
        }
        results.value = [{}];
        break;
      }
      case 'agent': {
        const data = await api.get('/api/agents');
        let agents = data.agents || [];
        if (agentStatus.value !== 'all') {
          agents = agents.filter(a => (a.status || 'ok') === agentStatus.value);
        }
        if (query.value.trim()) {
          const q = query.value.toLowerCase();
          agents = agents.filter(a =>
            a.name.toLowerCase().includes(q)
            || (a.model || '').toLowerCase().includes(q)
            || (a.capabilities || []).some(c => c.toLowerCase().includes(q))
          );
        }
        results.value = agents;
        break;
      }
    }
    searchTime.value = Math.round(performance.now() - start);
  } catch {
    results.value = [];
    searchTime.value = 0;
  }
  searching.value = false;
}

async function loadStats() {
  try { memoryStats.value = await api.get('/api/memory/stats'); } catch {}
  try { vectorStats.value = await api.get('/api/vector/stats'); } catch {}
  try { graphStats.value = await api.get('/api/graph/stats'); } catch {}
}

onMounted(loadStats);
</script>

<style scoped>
.search-page { }
.topbar { margin-bottom: 16px; }
.topbar h1 { font-size: 24px; font-weight: 700; }

.tab-bar { display: flex; gap: 4px; background: var(--bg2); border-radius: 12px; padding: 4px; margin-bottom: 16px; }
.tab-btn { background: none; border: none; padding: 8px 18px; border-radius: 10px; font-size: 13px; color: var(--text2); cursor: pointer; display: flex; align-items: center; gap: 6px; transition: all .15s; }
.tab-btn.active { background: var(--accent); color: #fff; }
.tab-icon { font-size: 14px; }

.search-panel { background: var(--bg2); border: 1px solid var(--border); border-radius: var(--radius); padding: 16px; margin-bottom: 16px; }
.search-row { display: flex; gap: 10px; margin-bottom: 12px; }
.search-input { flex: 1; background: var(--bg); border: 1px solid var(--border); border-radius: 10px; padding: 10px 14px; font-size: 14px; color: var(--text1); outline: none; }
.search-input:focus { border-color: var(--accent); }
.search-btn { background: var(--accent); color: #fff; border: none; border-radius: 10px; padding: 10px 24px; font-size: 14px; cursor: pointer; white-space: nowrap; }
.search-btn:disabled { opacity: .5; cursor: not-allowed; }

.options-row { display: flex; align-items: center; gap: 16px; }
.opt-group { display: flex; align-items: center; gap: 6px; }
.opt-group label { font-size: 12px; color: var(--text2); font-weight: 600; }
.opt-group select { background: var(--bg); border: 1px solid var(--border); border-radius: 6px; padding: 4px 10px; font-size: 13px; color: var(--text1); cursor: pointer; }

.index-stats { display: flex; gap: 12px; margin-bottom: 16px; flex-wrap: wrap; }
.stat-chip { display: flex; align-items: center; gap: 6px; background: var(--bg2); border: 1px solid var(--border); border-radius: 8px; padding: 6px 14px; }
.chip-icon { font-size: 14px; }
.chip-val { font-size: 16px; font-weight: 700; }
.chip-lbl { font-size: 11px; color: var(--text2); }

.results-area { }
.results-header { display: flex; justify-content: space-between; font-size: 13px; color: var(--text2); margin-bottom: 12px; }
.query-time { font-family: monospace; }

.result-cards { }
.result-card { background: var(--bg2); border: 1px solid var(--border); border-radius: var(--radius); padding: 14px; margin-bottom: 10px; }
.result-header { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
.result-rank { font-size: 13px; font-weight: 700; color: var(--text2); }
.result-score { font-size: 12px; padding: 2px 10px; border-radius: 6px; font-weight: 700; }
.result-type { font-size: 11px; padding: 2px 8px; border-radius: 4px; background: var(--bg3); color: var(--text2); }
.result-id { font-size: 11px; color: var(--text2); font-family: monospace; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 200px; }
.score-bar { height: 4px; background: var(--bg3); border-radius: 2px; overflow: hidden; margin-bottom: 8px; }
.score-fill { height: 100%; border-radius: 2px; transition: width .3s; }
.result-content { font-size: 13px; line-height: 1.6; color: var(--text1); white-space: pre-wrap; word-break: break-word; }

.edge-list { }
.edge-row { display: flex; align-items: center; gap: 10px; padding: 8px 14px; background: var(--bg2); border: 1px solid var(--border); border-radius: 8px; margin-bottom: 6px; }
.edge-src { font-weight: 600; color: var(--accent); font-size: 13px; }
.edge-arrow { color: var(--text2); }
.edge-tgt { font-weight: 600; color: var(--accent); font-size: 13px; }

.neighbor-list { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 10px; }
.neighbor-card { background: var(--bg2); border: 1px solid var(--border); border-radius: var(--radius); padding: 14px; }
.neighbor-name { font-size: 14px; font-weight: 600; color: var(--accent); margin-bottom: 8px; }
.neighbor-deps { display: flex; flex-wrap: wrap; gap: 4px; }
.dep-tag { font-size: 11px; background: var(--bg3); padding: 2px 8px; border-radius: 4px; color: var(--text2); }
.no-deps { font-size: 12px; color: var(--text2); }

.node-list { }
.node-row { display: flex; align-items: center; justify-content: space-between; padding: 8px 14px; background: var(--bg2); border: 1px solid var(--border); border-radius: 8px; margin-bottom: 6px; }
.node-name { font-size: 13px; font-weight: 600; color: var(--accent); }
.node-dep-count { font-size: 12px; color: var(--text2); }

.log-results { }
.log-text { background: var(--bg2); border: 1px solid var(--border); border-radius: var(--radius); padding: 16px; font-family: 'Cascadia Code', 'Fira Code', 'Consolas', monospace; font-size: 12px; line-height: 1.6; color: var(--text2); white-space: pre-wrap; word-break: break-all; max-height: 500px; overflow-y: auto; margin: 0; }

.agent-results { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 12px; }
.agent-card { background: var(--bg2); border: 1px solid var(--border); border-radius: var(--radius); padding: 14px; }
.agent-top { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; }
.agent-name { font-size: 14px; font-weight: 600; }
.status-badge { font-size: 11px; padding: 2px 8px; border-radius: 4px; font-weight: 600; }
.status-badge.ok { background: rgba(34,197,94,.15); color: var(--success); }
.status-badge.error { background: rgba(239,68,68,.15); color: var(--fail); }
.agent-meta { font-size: 12px; color: var(--text2); margin-bottom: 8px; display: flex; gap: 12px; }
.agent-caps { display: flex; flex-wrap: wrap; gap: 4px; }
.cap-tag { font-size: 11px; background: var(--bg3); padding: 2px 8px; border-radius: 4px; color: var(--text2); }

.empty-state { text-align: center; padding: 40px; color: var(--text2); }
</style>