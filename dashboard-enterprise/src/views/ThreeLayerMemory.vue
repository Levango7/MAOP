<template>
  <div class="memory-page">
    <PageHeader>
      <button class="btn-refresh" :disabled="loading" @click="refreshAll">
        <AppIcon name="refresh" :size="15" :class="{ spinning: loading }" /> {{ t('common.refresh') }}
      </button>
      <button class="btn-primary" @click="showAdd = true">
        <AppIcon name="plus" :size="15" /> {{ t('view.tlmemory.addMemory') }}
      </button>
    </PageHeader>

    <div class="layers">
      <div class="layer-card layer-card--1">
        <div class="layer-card__head">
          <AppIcon name="zap" :size="18" />
          <span class="layer-card__meta">{{ t('view.tlmemory.layer1Meta') }}</span>
        </div>
        <h3 class="layer-card__name">{{ t('view.tlmemory.layer1Name') }}</h3>
        <p class="layer-card__desc">{{ t('view.tlmemory.layer1Desc') }}</p>
      </div>
      <div class="layer-card layer-card--2">
        <div class="layer-card__head">
          <AppIcon name="clock" :size="18" />
          <span class="layer-card__meta">{{ t('view.tlmemory.layer2Meta') }}</span>
        </div>
        <h3 class="layer-card__name">{{ t('view.tlmemory.layer2Name') }}</h3>
        <p class="layer-card__desc">{{ t('view.tlmemory.layer2Desc') }}</p>
      </div>
      <div class="layer-card layer-card--3">
        <div class="layer-card__head">
          <AppIcon name="archive" :size="18" />
          <span class="layer-card__meta">{{ t('view.tlmemory.layer3Meta') }}</span>
        </div>
        <h3 class="layer-card__name">{{ t('view.tlmemory.layer3Name') }}</h3>
        <p class="layer-card__desc">{{ t('view.tlmemory.layer3Desc') }}</p>
      </div>
    </div>
    <p class="layers-note">{{ t('view.tlmemory.layersNote') }}</p>

    <div class="stats-row">
      <StatCard :label="t('view.tlmemory.totalEntries')" :value="stats.total_entries" icon="database" tone="brand" :loading="loading" />
      <StatCard :label="t('view.tlmemory.totalTraces')" :value="stats.total_traces" icon="route" tone="info" :loading="loading" />
      <StatCard :label="t('view.tlmemory.trajectorySteps')" :value="stats.total_trajectory_steps" icon="activity" tone="warn" :loading="loading" />
      <StatCard :label="t('view.tlmemory.episodicCount')" :value="stats.episodic_count" icon="layers" tone="success" :loading="loading" />
    </div>

    <div v-if="memError" class="mem-error-banner">
      <AppIcon name="alert-triangle" :size="14" /> {{ memError }}
    </div>

    <div class="breakdown">
      <Card :title="t('view.tlmemory.byTopic')" icon="scroll" :margin-bottom="16">
        <div v-if="topicEntries.length" class="chip-list">
          <button
v-for="t in topicEntries" :key="t.key" class="chip" :class="{ active: query === t.key }"
                  @click="searchTopic(t.key)">
            {{ t.key }} <span class="chip-count">{{ t.value }}</span>
          </button>
        </div>
        <EmptyState v-else icon="scroll" :title="t('view.tlmemory.noTopics')" :description="t('view.tlmemory.noTopicDesc')" />
      </Card>
      <Card :title="t('view.tlmemory.byAgent')" icon="bot" :margin-bottom="16">
        <div v-if="agentEntries.length" class="chip-list">
          <span v-for="a in agentEntries" :key="a.key" class="chip chip--static">
            {{ a.key }} <span class="chip-count">{{ a.value }}</span>
          </span>
        </div>
        <EmptyState v-else icon="bot" :title="t('view.tlmemory.noAgents')" :description="t('view.tlmemory.noAgentDesc')" />
      </Card>
      <Card :title="t('view.tlmemory.episodicByOutcome')" icon="activity" :margin-bottom="16">
        <div v-if="outcomeEntries.length" class="chip-list">
          <span v-for="o in outcomeEntries" :key="o.key" class="chip chip--static">
            {{ o.key }} <span class="chip-count">{{ o.value }}</span>
          </span>
        </div>
        <EmptyState v-else icon="activity" :title="t('view.tlmemory.noOutcomes')" :description="t('view.tlmemory.noOutcomeDesc')" />
      </Card>
    </div>

    <Card :title="t('view.tlmemory.memoryEntries')" icon="search" :margin-bottom="16">
      <div class="search-bar">
        <span class="search-icon"><AppIcon name="search" :size="16" /></span>
        <input v-model="query" :placeholder="t('view.tlmemory.searchPlaceholder')" @keyup.enter="runSearch" />
        <button class="search-btn" :disabled="loading" @click="runSearch">
          <AppIcon name="search" :size="15" /> {{ t('common.search') }}
        </button>
      </div>

      <div v-if="entries.length" class="entries-list">
        <div v-for="e in entries" :key="e.id" class="entry-card">
          <div class="entry-header">
            <Badge tone="brand">{{ e.agent || 'system' }}</Badge>
            <Badge tone="info">{{ e.topic || '—' }}</Badge>
            <Badge v-if="e.layer" tone="warn">{{ e.layer }}</Badge>
            <Badge v-if="e.outcome" tone="success">{{ e.outcome }}</Badge>
            <span class="entry-score">{{ t('view.tlmemory.score') }} {{ formatScore(e.score) }}</span>
            <span class="entry-time">{{ formatTime(e.timestamp) }}</span>
          </div>
          <div v-if="e.task" class="entry-task">{{ e.task }}</div>
          <div class="entry-body">{{ e.snippet || e.content || '' }}</div>
          <div v-if="e.tags" class="entry-footer">
            <span v-for="t in e.tags" :key="t" class="tag">{{ t }}</span>
          </div>
        </div>
      </div>
      <EmptyState
v-else-if="!loading" icon="search" :title="t('view.tlmemory.noMemories')"
                  :description="query ? t('view.tlmemory.noResults') + ' “' + query + '”.' : t('view.tlmemory.runSearchHint')" />
      <Skeleton v-else height="200px" />
    </Card>

    <!-- Add Memory Modal -->
    <Teleport to="body">
      <div v-if="showAdd" v-modal-a11y class="modal-mask" @click.self="showAdd = false" @modal:escape="showAdd = false">
        <div class="modal" role="dialog" aria-modal="true">
          <div class="modal__head">
            <h3>{{ t('view.tlmemory.addMemoryTitle') }}</h3>
            <button class="modal__x" type="button" aria-label="Close" @click="showAdd = false">×</button>
          </div>
          <div class="modal__body">
            <label class="field">
              <span class="field__label">{{ t('view.tlmemory.layer') }}</span>
              <select v-model="addForm.layer" class="field__input">
                <option value="working">{{ t('view.tlmemory.layer1Name') }}</option>
                <option value="episodic">{{ t('view.tlmemory.layer2Name') }}</option>
                <option value="semantic">{{ t('view.tlmemory.layer3Name') }}</option>
              </select>
            </label>
            <label class="field">
              <span class="field__label">{{ t('view.tlmemory.content') }} *</span>
              <textarea
v-model="addForm.content" class="field__input" rows="4"
                :placeholder="t('view.tlmemory.contentPlaceholder')"></textarea>
            </label>
            <div style="display:grid; grid-template-columns:1fr 1fr; gap:var(--sp-4);">
              <label class="field">
                <span class="field__label">{{ t('view.tlmemory.topic') }}</span>
                <input v-model="addForm.topic" class="field__input" :placeholder="t('view.tlmemory.topicPlaceholder')" />
              </label>
              <label class="field">
                <span class="field__label">{{ t('view.tlmemory.agent') }}</span>
                <input v-model="addForm.agent" class="field__input" :placeholder="t('view.tlmemory.agentPlaceholder')" />
              </label>
            </div>
            <label class="field">
              <span class="field__label">{{ t('view.tlmemory.tags') }}</span>
              <input v-model="addForm.tags" class="field__input" :placeholder="t('view.tlmemory.tagsPlaceholder')" />
            </label>
          </div>
          <div class="modal__foot">
            <button class="btn-ghost" type="button" @click="showAdd = false">{{ t('common.cancel') }}</button>
            <button class="btn-primary" type="button" :disabled="!addForm.content.trim() || adding" @click="submitMemory">
              {{ adding ? t('view.tlmemory.submitting') : t('common.submit') }}
            </button>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue';
import { useI18n } from '../i18n';
import { useApiStore } from '../stores/api.js';
import { useToast } from '../composables/useToast.js';
import AppIcon from '../components/AppIcon.vue';
import PageHeader from '../components/PageHeader.vue';
import Card from '../components/Card.vue';
import StatCard from '../components/StatCard.vue';
import Badge from '../components/Badge.vue';
import Skeleton from '../components/Skeleton.vue';
import EmptyState from '../components/EmptyState.vue';

const { t } = useI18n();
const api = useApiStore();
const toast = useToast();
const loading = ref(false);
const query = ref('');
const entries = ref([]);
const memError = ref('');
const stats = reactive({ total_entries: 0, total_traces: 0, total_trajectory_steps: 0, by_agent: {}, by_topic: {}, episodic_count: 0, episodic_by_agent: {}, episodic_by_outcome: {} });

// ── Add Memory ──
const showAdd = ref(false);
const adding = ref(false);
const addForm = reactive({ layer: 'episodic', content: '', topic: '', agent: 'admin', tags: '' });

async function submitMemory() {
  const content = addForm.content.trim();
  if (!content) return;
  adding.value = true;
  try {
    await api.post('/api/memory/store', {
      layer: addForm.layer,
      content,
      topic: addForm.topic.trim(),
      agent: addForm.agent.trim() || 'admin',
      tags: addForm.tags.trim(),
    });
    showAdd.value = false;
    addForm.content = ''; addForm.topic = ''; addForm.tags = '';
    await refreshAll();
  } catch (e) {
    toast.error(t('view.tlmemory.storeFailed') + ((e && e.message) ? ': ' + e.message : ''));
  } finally {
    adding.value = false;
  }
}

const topicEntries = computed(() => Object.entries(stats.by_topic || {}).map(([k, v]) => ({ key: k, value: v })));
const agentEntries = computed(() => Object.entries(stats.by_agent || {}).map(([k, v]) => ({ key: k, value: v })));
const outcomeEntries = computed(() => Object.entries(stats.episodic_by_outcome || {}).map(([k, v]) => ({ key: k, value: v })));

function formatScore(s) {
  if (s === null) return '—';
  const n = Number(s);
  if (isNaN(n)) return '—';
  return n < 0.001 ? n.toExponential(1) : n.toFixed(3);
}
function formatTime(ts) {
  if (!ts) return '—';
  const d = new Date(String(ts));
  if (isNaN(d.getTime())) return String(ts);
  return d.toLocaleString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}
function splitTags(t) {
  if (!t) return [];
  return String(t).split(',').map((x) => x.trim()).filter(Boolean);
}

async function loadStats() {
  try {
    const s = await api.get('/api/memory/stats');
    stats.total_entries = s.total_entries || 0;
    stats.total_traces = s.total_traces || 0;
    stats.total_trajectory_steps = s.total_trajectory_steps || 0;
    stats.by_agent = s.by_agent || {};
    stats.by_topic = s.by_topic || {};
    stats.episodic_count = s.episodic_count || 0;
    stats.episodic_by_agent = s.episodic_by_agent || {};
    stats.episodic_by_outcome = s.episodic_by_outcome || {};
    memError.value = '';
  } catch (e) {
    console.error('[memory] loadStats failed:', e);
    memError.value = (e && e.message) ? e.message : String(e);
    stats.total_entries = 0; stats.total_traces = 0; stats.total_trajectory_steps = 0;
    stats.by_agent = {}; stats.by_topic = {};
    stats.episodic_count = 0; stats.episodic_by_agent = {}; stats.episodic_by_outcome = {};
  }
}

async function runSearch() {
  loading.value = true;
  try {
    const q = query.value.trim();
    const data = await api.get(`/api/memory/search?q=${encodeURIComponent(q)}&topk=50`);
    const results = (data && data.results) || [];
    entries.value = results.map((r) => ({
      id: r.id,
      agent: r.agent,
      topic: r.topic,
      task: r.task,
      content: r.content,
      snippet: r.snippet || r.content || '',
      tags: splitTags(r.tags),
      score: r.score,
      timestamp: r.timestamp,
      layer: r.layer || r.type || '',
      outcome: r.outcome || '',
    }));
  } catch (e) {
    console.error('[memory] runSearch failed:', e);
    toast.error(t('view.tlmemory.searchFailed') + (e && e.message ? ': ' + e.message : ''));
    entries.value = [];
  } finally {
    loading.value = false;
  }
}

function searchTopic(topic) {
  query.value = topic;
  runSearch();
}

async function refreshAll() {
  loading.value = true;
  await loadStats();
  // Default: search the dominant topic so the list populates with real data.
  const topTopic = [...topicEntries.value].sort((a, b) => b.value - a.value)[0];
  if (topTopic) {
    query.value = topTopic.key;
    await runSearch();
  } else {
    await runSearch();
  }
  loading.value = false;
}

onMounted(refreshAll);
</script>

<style scoped>
.mem-error-banner {
  display: flex; align-items: center; gap: 8px;
  padding: 8px 12px; margin-bottom: 16px;
  background: var(--fail-soft); border: 1px solid var(--fail);
  border-radius: var(--r-md); color: var(--fail, #f85149);
  font-size: 12px; font-family: var(--font-mono); word-break: break-word;
}
</style>
