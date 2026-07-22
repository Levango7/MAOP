<template>
  <div class="memory-page">
    <div class="topbar">
      <h1>Three-Layer Memory</h1>
      <div class="layer-tabs">
        <button v-for="l in layers" :key="l.key" :class="['tab-btn', { active: activeLayer === l.key }]" @click="activeLayer = l.key">
          <span class="tab-icon">{{ l.icon }}</span>{{ l.label }}
        </button>
      </div>
      <button class="btn-action" @click="consolidate">🔄 Consolidate</button>
    </div>

    <div class="layer-overview">
      <div v-for="l in layerStats" :key="l.key" class="layer-card" :class="{ active: activeLayer === l.key }" @click="activeLayer = l.key">
        <div class="layer-icon" :style="{ background: l.bg }">{{ l.icon }}</div>
        <div class="layer-info">
          <span class="layer-name">{{ l.label }}</span>
          <span class="layer-desc">{{ l.desc }}</span>
        </div>
        <div class="layer-stat">
          <span class="stat-num">{{ l.count }}</span>
          <span class="stat-unit">entries</span>
        </div>
        <div class="layer-bar"><div class="bar-fill" :style="{ width: l.pct + '%', background: l.color }"></div></div>
      </div>
    </div>

    <div class="memory-content">
      <div class="search-bar">
        <input v-model="searchQuery" placeholder="Search memories..." @input="onSearch" />
        <select v-model="sortBy">
          <option value="recent">Most Recent</option>
          <option value="relevance">Relevance</option>
          <option value="importance">Importance</option>
        </select>
      </div>

      <div class="entries-list">
        <div v-for="e in filteredEntries" :key="e.id" class="entry-card">
          <div class="entry-header">
            <span class="entry-agent">{{ e.agent || 'system' }}</span>
            <span class="entry-layer" :style="{ background: layerColor(e.layer) }">{{ e.layer }}</span>
            <span class="entry-time">{{ e.timestamp }}</span>
          </div>
          <div class="entry-body">{{ e.content }}</div>
          <div class="entry-footer">
            <span class="entry-tags" v-if="e.tags">
              <span v-for="t in (Array.isArray(e.tags) ? e.tags : e.tags.split(','))" :key="t" class="tag">{{ t }}</span>
            </span>
            <span class="entry-importance" v-if="e.importance">⭐ {{ e.importance }}</span>
          </div>
        </div>
        <div v-if="filteredEntries.length === 0" class="empty-state">
          <p>No memories found</p>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue';
import { useApiStore } from '../stores/api.js';

const api = useApiStore();
const activeLayer = ref('all');
const searchQuery = ref('');
const sortBy = ref('recent');
const entries = ref([]);
const stats = ref({ short_term: 0, mid_term: 0, long_term: 0 });

const layers = [
  { key: 'all', label: 'All', icon: '🧠' },
  { key: 'short', label: 'Short-Term', icon: '⚡' },
  { key: 'mid', label: 'Mid-Term', icon: '📝' },
  { key: 'long', label: 'Long-Term', icon: '🏛️' },
];

const layerStats = computed(() => [
  { key: 'short', label: 'Short-Term', icon: '⚡', desc: 'Working context, recent interactions', count: stats.value.short_term, pct: Math.min(100, stats.value.short_term), color: '#3b82f6', bg: 'rgba(59,130,246,.12)' },
  { key: 'mid', label: 'Mid-Term', icon: '📝', desc: 'Consolidated patterns, session summaries', count: stats.value.mid_term, pct: Math.min(100, stats.value.mid_term), color: '#a78bfa', bg: 'rgba(167,139,250,.12)' },
  { key: 'long', label: 'Long-Term', icon: '🏛️', desc: 'Core knowledge, persistent facts', count: stats.value.long_term, pct: Math.min(100, stats.value.long_term), color: '#22c55e', bg: 'rgba(34,197,94,.12)' },
]);

const filteredEntries = computed(() => {
  let list = entries.value;
  if (activeLayer.value !== 'all') list = list.filter(e => e.layer === activeLayer.value);
  if (searchQuery.value) {
    const q = searchQuery.value.toLowerCase();
    list = list.filter(e => (e.content || '').toLowerCase().includes(q) || (e.agent || '').toLowerCase().includes(q));
  }
  return list;
});

function layerColor(layer) {
  const m = { short: '#3b82f6', mid: '#a78bfa', long: '#22c55e' };
  return m[layer] || '#64748b';
}

let searchTimer;
function onSearch() { clearTimeout(searchTimer); searchTimer = setTimeout(loadEntries, 300); }

async function consolidate() {
  try { await api.post('/api/memory/consolidate', {}); loadEntries(); } catch {}
}

async function loadEntries() {
  try {
    const data = await api.get('/api/memory/search?query=*&limit=50');
    entries.value = (data.results || data.entries || []).map(e => ({
      id: e.id || Math.random().toString(36).slice(2),
      agent: e.agent || '',
      content: e.content || e.text || '',
      layer: e.layer || e.topic || 'mid',
      tags: e.tags || [],
      importance: e.importance || e.score || null,
      timestamp: e.timestamp || e.created_at || '',
    }));
  } catch {}
  try {
    const s = await api.get('/api/memory/stats');
    stats.value = {
      short_term: s.short_term_count || s.short_term || 0,
      mid_term: s.mid_term_count || s.mid_term || 0,
      long_term: s.long_term_count || s.long_term || 0,
    };
  } catch {}
}

onMounted(loadEntries);
</script>

<style scoped>
.memory-page { }
.topbar { display: flex; align-items: center; gap: 12px; margin-bottom: 20px; flex-wrap: wrap; }
.topbar h1 { font-size: 24px; font-weight: 700; }
.layer-tabs { display: flex; gap: 4px; background: var(--bg2); border-radius: 10px; padding: 3px; }
.tab-btn { background: none; border: none; padding: 6px 14px; border-radius: 8px; font-size: 13px; color: var(--text2); cursor: pointer; display: flex; align-items: center; gap: 6px; transition: all .15s; }
.tab-btn.active { background: var(--accent); color: #fff; }
.tab-icon { font-size: 14px; }
.btn-action { margin-left: auto; background: var(--bg2); border: 1px solid var(--border); border-radius: 10px; padding: 6px 14px; font-size: 13px; color: var(--text2); cursor: pointer; }
.btn-action:hover { border-color: var(--accent); color: var(--accent); }

.layer-overview { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin-bottom: 24px; }
.layer-card { background: var(--bg2); border: 1px solid var(--border); border-radius: var(--radius); padding: 16px; cursor: pointer; transition: all .15s; }
.layer-card:hover, .layer-card.active { border-color: var(--accent); box-shadow: 0 0 0 1px var(--accent); }
.layer-card { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; }
.layer-icon { width: 36px; height: 36px; border-radius: 10px; display: flex; align-items: center; justify-content: center; font-size: 18px; }
.layer-info { flex: 1; min-width: 100px; }
.layer-name { font-size: 14px; font-weight: 600; display: block; }
.layer-desc { font-size: 11px; color: var(--text3); display: block; margin-top: 2px; }
.layer-stat { text-align: right; }
.stat-num { font-size: 22px; font-weight: 700; display: block; }
.stat-unit { font-size: 11px; color: var(--text3); }
.layer-bar { width: 100%; height: 4px; background: var(--bg3); border-radius: 2px; margin-top: 4px; }
.bar-fill { height: 100%; border-radius: 2px; transition: width .3s; }

.search-bar { display: flex; gap: 8px; margin-bottom: 16px; }
.search-bar input { flex: 1; background: var(--bg2); border: 1px solid var(--border); border-radius: 10px; padding: 8px 14px; font-size: 14px; color: var(--text); outline: none; }
.search-bar input:focus { border-color: var(--accent); }
.search-bar select { background: var(--bg2); border: 1px solid var(--border); border-radius: 10px; padding: 8px 12px; font-size: 13px; color: var(--text2); }

.entry-card { background: var(--bg2); border: 1px solid var(--border); border-radius: var(--radius); padding: 14px; margin-bottom: 10px; }
.entry-header { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
.entry-agent { font-size: 12px; font-weight: 600; color: var(--accent); }
.entry-layer { font-size: 10px; padding: 2px 8px; border-radius: 4px; color: #fff; font-weight: 600; text-transform: uppercase; }
.entry-time { margin-left: auto; font-size: 11px; color: var(--text3); }
.entry-body { font-size: 14px; line-height: 1.6; color: var(--text); }
.entry-footer { display: flex; align-items: center; gap: 8px; margin-top: 8px; }
.entry-tags { display: flex; gap: 4px; flex-wrap: wrap; }
.tag { font-size: 11px; background: var(--bg); padding: 2px 8px; border-radius: 4px; color: var(--text3); }
.entry-importance { font-size: 12px; color: var(--warn); margin-left: auto; }
.empty-state { text-align: center; padding: 40px; color: var(--text3); }
</style>