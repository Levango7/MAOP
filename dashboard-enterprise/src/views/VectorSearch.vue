<template>
  <div class="vector-page">
    <div class="topbar">
      <h1>Vector Search</h1>
      <div class="search-mode">
        <button v-for="m in modes" :key="m.key" :class="['mode-btn', { active: mode === m.key }]" @click="mode = m.key">{{ m.label }}</button>
      </div>
    </div>

    <div class="search-panel">
      <div class="search-input-area">
        <textarea v-model="query" placeholder="Enter text to search semantically..." rows="3" @keydown.ctrl.enter="doSearch"></textarea>
        <div class="search-controls">
          <div class="control-group">
            <label>Top K</label>
            <input type="number" v-model.number="topK" min="1" max="100" />
          </div>
          <div class="control-group">
            <label>Threshold</label>
            <input type="range" v-model.number="threshold" min="0" max="1" step="0.05" />
            <span class="range-val">{{ threshold.toFixed(2) }}</span>
          </div>
          <button class="search-btn" @click="doSearch" :disabled="searching || !query.trim()">
            {{ searching ? 'Searching...' : '🔍 Search' }}
          </button>
        </div>
      </div>
    </div>

    <div class="results-area" v-if="results.length > 0">
      <div class="results-header">
        <span>{{ results.length }} results</span>
        <span class="query-time" v-if="searchTime">in {{ searchTime }}ms</span>
      </div>
      <div class="result-card" v-for="(r, i) in results" :key="i">
        <div class="result-header">
          <span class="result-rank">#{{ i + 1 }}</span>
          <span class="result-score" :style="{ background: scoreColor(r.score) }">{{ (r.score * 100).toFixed(1) }}%</span>
          <span class="result-id">{{ r.id || r.entry_id || '' }}</span>
          <span class="result-meta" v-if="r.agent">{{ r.agent }}</span>
        </div>
        <div class="result-content">{{ r.content || r.text || r.chunk || '' }}</div>
        <div class="result-footer">
          <span class="result-tags" v-if="r.tags">
            <span v-for="t in (typeof r.tags === 'string' ? r.tags.split(',') : r.tags)" :key="t" class="tag">{{ t }}</span>
          </span>
          <span class="result-ts" v-if="r.timestamp">{{ r.timestamp }}</span>
        </div>
      </div>
    </div>

    <div class="empty-state" v-else-if="searched">
      <p>No results found for "{{ lastQuery }}"</p>
    </div>

    <div class="vector-stats">
      <div class="panel">
        <h3>Index Statistics</h3>
        <div class="stat-grid">
          <div class="vstat"><span class="vstat-val">{{ indexStats.total_vectors }}</span><span class="vstat-lbl">Vectors</span></div>
          <div class="vstat"><span class="vstat-val">{{ indexStats.dimensions }}</span><span class="vstat-lbl">Dimensions</span></div>
          <div class="vstat"><span class="vstat-val">{{ indexStats.index_type }}</span><span class="vstat-lbl">Index Type</span></div>
          <div class="vstat"><span class="vstat-val">{{ indexStats.last_indexed }}</span><span class="vstat-lbl">Last Indexed</span></div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue';
import { useApiStore } from '../stores/api.js';

const api = useApiStore();
const query = ref('');
const topK = ref(10);
const threshold = ref(0.5);
const mode = ref('semantic');
const searching = ref(false);
const searched = ref(false);
const lastQuery = ref('');
const results = ref([]);
const searchTime = ref(0);
const indexStats = ref({ total_vectors: 0, dimensions: 384, index_type: 'Flat', last_indexed: '--' });

const modes = [
  { key: 'semantic', label: 'Semantic' },
  { key: 'keyword', label: 'Keyword' },
  { key: 'hybrid', label: 'Hybrid' },
];

function scoreColor(s) {
  if (s >= 0.8) return 'rgba(34,197,94,.15)';
  if (s >= 0.5) return 'rgba(59,130,246,.15)';
  return 'rgba(239,68,68,.15)';
}

async function doSearch() {
  if (!query.value.trim()) return;
  searching.value = true;
  searched.value = true;
  lastQuery.value = query.value;
  const start = performance.now();
  try {
    const data = await api.get(`/api/vector/search?query=${encodeURIComponent(query.value)}&top_k=${topK.value}&mode=${mode.value}`);
    results.value = data.results || data.entries || [];
    searchTime.value = Math.round(performance.now() - start);
  } catch {
    results.value = [];
    searchTime.value = 0;
  }
  searching.value = false;
}

onMounted(async () => {
  try {
    const s = await api.get('/api/vector/stats');
    indexStats.value = {
      total_vectors: s.total_vectors || s.count || 0,
      dimensions: s.dimensions || 384,
      index_type: s.index_type || 'Flat',
      last_indexed: s.last_indexed || '--',
    };
  } catch {}
});
</script>

<style scoped>
.vector-page { }
.topbar { display: flex; align-items: center; gap: 12px; margin-bottom: 20px; }
.topbar h1 { font-size: 24px; font-weight: 700; }
.search-mode { display: flex; gap: 4px; background: var(--bg2); border-radius: 10px; padding: 3px; margin-left: 16px; }
.mode-btn { background: none; border: none; padding: 6px 14px; border-radius: 8px; font-size: 13px; color: var(--text2); cursor: pointer; transition: all .15s; }
.mode-btn.active { background: var(--accent); color: #fff; }

.search-panel { background: var(--bg2); border: 1px solid var(--border); border-radius: var(--radius); padding: 20px; margin-bottom: 24px; }
.search-input-area textarea { width: 100%; background: var(--bg); border: 1px solid var(--border); border-radius: 10px; padding: 12px; font-size: 14px; color: var(--text); resize: none; outline: none; font-family: inherit; line-height: 1.5; }
.search-input-area textarea:focus { border-color: var(--accent); }
.search-controls { display: flex; align-items: center; gap: 16px; margin-top: 12px; }
.control-group { display: flex; align-items: center; gap: 6px; }
.control-group label { font-size: 12px; color: var(--text3); font-weight: 600; }
.control-group input[type="number"] { width: 60px; background: var(--bg); border: 1px solid var(--border); border-radius: 6px; padding: 4px 8px; font-size: 13px; color: var(--text); text-align: center; }
.control-group input[type="range"] { width: 100px; }
.range-val { font-size: 12px; color: var(--text3); font-family: monospace; min-width: 32px; }
.search-btn { margin-left: auto; background: var(--accent); color: #fff; border: none; border-radius: 10px; padding: 8px 20px; font-size: 14px; cursor: pointer; }
.search-btn:disabled { opacity: .5; }

.results-area { margin-bottom: 24px; }
.results-header { display: flex; justify-content: space-between; font-size: 13px; color: var(--text3); margin-bottom: 12px; }
.query-time { font-family: monospace; }
.result-card { background: var(--bg2); border: 1px solid var(--border); border-radius: var(--radius); padding: 14px; margin-bottom: 10px; }
.result-header { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
.result-rank { font-size: 13px; font-weight: 700; color: var(--text3); }
.result-score { font-size: 12px; padding: 2px 10px; border-radius: 6px; font-weight: 700; }
.result-id { font-size: 11px; color: var(--text3); font-family: monospace; }
.result-meta { margin-left: auto; font-size: 12px; color: var(--accent); }
.result-content { font-size: 14px; line-height: 1.6; color: var(--text); }
.result-footer { display: flex; align-items: center; gap: 8px; margin-top: 8px; }
.result-tags { display: flex; gap: 4px; }
.tag { font-size: 11px; background: var(--bg); padding: 2px 8px; border-radius: 4px; color: var(--text3); }
.result-ts { margin-left: auto; font-size: 11px; color: var(--text3); }
.empty-state { text-align: center; padding: 40px; color: var(--text3); }

.vector-stats { }
.panel { background: var(--bg2); border: 1px solid var(--border); border-radius: var(--radius); padding: 20px; }
.panel h3 { font-size: 14px; font-weight: 600; color: var(--text2); margin-bottom: 16px; }
.stat-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; }
.vstat { text-align: center; }
.vstat-val { font-size: 22px; font-weight: 700; display: block; }
.vstat-lbl { font-size: 11px; color: var(--text3); }
</style>