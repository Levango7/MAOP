<template>
  <div class="routing-view">
    <ListPageLayout>
      <template #actions>
        <Segmented
          :model-value="activeTab"
          :options="tabOptions"
          size="sm"
          @update:model-value="activeTab = $event"
        />
        <button class="btn-ghost" :class="{ 'is-busy': loading }" :disabled="loading" @click="loadAll">
          <AppIcon name="refresh" :size="15" />
          <span>{{ t('common.refresh') }}</span>
        </button>
      </template>
      <template #content>
        <!-- ── Recent decisions ─────────────────────────────────── -->
        <div v-show="activeTab === 'recent'">
          <Card :title="t('view.routing.recent.title')" icon="route" margin-bottom="var(--sp-4)">
            <template #actions>
              <select v-model="stageFilter" class="stage-select" @change="loadRecent">
                <option value="">{{ t('view.routing.recent.filterStage') }}</option>
                <option v-for="s in STAGES" :key="s" :value="s">{{ stageLabel(s) }}</option>
              </select>
            </template>
            <div v-if="loading" class="blk">
              <Skeleton block height="44px" />
              <Skeleton block height="44px" />
              <Skeleton block height="44px" />
            </div>
            <EmptyState
              v-else-if="errors.recent"
              icon="alert-triangle"
              tone="fail"
              :title="t('view.routing.recent.failedLoad')"
              :description="errors.recent"
            />
            <EmptyState
              v-else-if="!decisions.length"
              icon="route"
              :title="t('view.routing.recent.empty')"
              :description="t('view.routing.recent.emptyHint')"
            />
            <div v-else class="dec-table" role="table" :aria-label="t('view.routing.recent.title')">
              <div class="dec-table__head" role="row">
                <div class="dec-table__th" role="columnheader">{{ t('view.routing.col.traceId') }}</div>
                <div class="dec-table__th" role="columnheader">{{ t('view.routing.col.stage') }}</div>
                <div class="dec-table__th" role="columnheader">{{ t('view.routing.col.agent') }}</div>
                <div class="dec-table__th dec-table__th--num" role="columnheader">{{ t('view.routing.col.score') }}</div>
                <div class="dec-table__th dec-table__th--act" role="columnheader">{{ t('view.routing.col.actions') }}</div>
              </div>
              <div v-for="(d, i) in decisions" :key="d.trace_id + '-' + d.stage + '-' + i" class="dec-table__row" role="row">
                <div class="dec-table__td" role="cell">
                  <div class="dec-table__trace mono">{{ d.trace_id || '—' }}</div>
                  <div class="dec-table__time">{{ formatTime(d.timestamp || d.ts) }}</div>
                </div>
                <div class="dec-table__td" role="cell">
                  <Badge :tone="stageTone(d.stage)">{{ stageLabel(d.stage) }}</Badge>
                </div>
                <div class="dec-table__td" role="cell">
                  <span class="dec-table__agent">{{ d.agent_id || d.agent || '—' }}</span>
                  <span v-if="d.model" class="dec-table__model muted">{{ d.model }}</span>
                </div>
                <div class="dec-table__td dec-table__td--num" role="cell">{{ formatScore(d.score) }}</div>
                <div class="dec-table__td dec-table__td--act" role="cell">
                  <button
                    class="icon-btn"
                    type="button"
                    :aria-label="t('view.routing.recent.viewTrace')"
                    @click="openTrace(d.trace_id)"
                  >
                    <AppIcon name="chevron-right" :size="14" />
                  </button>
                </div>
              </div>
            </div>
          </Card>
        </div>

        <!-- ── Statistics ───────────────────────────────────────── -->
        <div v-show="activeTab === 'stats'">
          <Card :title="t('view.routing.stats.title')" icon="activity" margin-bottom="var(--sp-4)">
            <div v-if="loading" class="blk">
              <Skeleton block height="120px" />
              <Skeleton block height="200px" />
            </div>
            <EmptyState
              v-else-if="errors.stats"
              icon="alert-triangle"
              tone="fail"
              :title="t('view.routing.stats.failedLoad')"
              :description="errors.stats"
            />
            <EmptyState
              v-else-if="!hasStatsData"
              icon="activity"
              :title="t('view.routing.stats.empty')"
              :description="t('view.routing.stats.emptyHint')"
            />
            <div v-else class="stats-body">
              <!-- 概览卡片 -->
              <div class="stats-overview">
                <div class="stats-card">
                  <span class="stats-card__label">{{ t('view.routing.stats.total') }}</span>
                  <span class="stats-card__value">{{ stats.total ?? 0 }}</span>
                </div>
                <div class="stats-card">
                  <span class="stats-card__label">{{ t('view.routing.stats.last24h') }}</span>
                  <span class="stats-card__value">{{ stats.last_24h ?? 0 }}</span>
                </div>
                <div class="stats-card">
                  <span class="stats-card__label">{{ t('view.routing.stats.byStage') }}</span>
                  <span class="stats-card__value">{{ stageCount }}</span>
                </div>
              </div>

              <!-- 阶段分布图（内联 SVG 柱状图） -->
              <div class="stats-chart">
                <div class="stats-chart__head">
                  <span class="stats-chart__title">{{ t('view.routing.stats.distribution') }}</span>
                </div>
                <svg
                  v-if="stageBars.length"
                  class="stats-chart__svg"
                  :viewBox="`0 0 ${CHART_W} ${CHART_H}`"
                  preserveAspectRatio="xMidYMid meet"
                  role="img"
                  :aria-label="t('view.routing.stats.distribution')"
                >
                  <g v-for="(bar, i) in stageBars" :key="bar.stage">
                    <rect
                      :x="bar.x"
                      :y="bar.y"
                      :width="bar.w"
                      :height="bar.h"
                      :class="['stats-chart__bar', `stats-chart__bar--${i % 4}`]"
                      rx="3"
                    />
                    <text :x="bar.x + bar.w / 2" :y="CHART_H - 8" text-anchor="middle" class="stats-chart__label">{{ stageLabel(bar.stage) }}</text>
                    <text :x="bar.x + bar.w / 2" :y="bar.y - 6" text-anchor="middle" class="stats-chart__value-text">{{ bar.count }}</text>
                  </g>
                </svg>
                <div v-else class="stats-chart__empty muted">{{ t('view.routing.stats.empty') }}</div>
              </div>
            </div>
          </Card>
        </div>
      </template>
    </ListPageLayout>

    <!-- ── Trace detail modal ─────────────────────────────────── -->
    <Teleport to="body">
      <div
        v-if="showTraceModal"
        v-modal-a11y
        class="modal-overlay"
        @click.self="closeTraceModal"
        @modal:escape="closeTraceModal"
      >
        <div class="modal" role="dialog" aria-modal="true">
          <div class="modal__head">
            <h3>{{ t('view.routing.trace.title') }}</h3>
            <button class="modal__x" type="button" :aria-label="t('view.routing.trace.close')" @click="closeTraceModal">
              <AppIcon name="x" :size="16" />
            </button>
          </div>
          <div class="modal__body">
            <div v-if="traceLoading" class="blk">
              <Skeleton block height="44px" />
              <Skeleton block height="44px" />
            </div>
            <EmptyState
              v-else-if="traceError"
              icon="alert-triangle"
              tone="fail"
              :title="t('view.routing.trace.failedLoad')"
              :description="traceError"
            />
            <EmptyState
              v-else-if="!traceDecisions.length"
              icon="route"
              :title="t('view.routing.trace.empty')"
            />
            <div v-else class="trace-chain">
              <div class="trace-chain__id mono">{{ traceId }}</div>
              <div
                v-for="(d, i) in traceDecisions"
                :key="i"
                class="trace-step"
              >
                <div class="trace-step__idx">{{ i + 1 }}</div>
                <div class="trace-step__body">
                  <div class="trace-step__head">
                    <Badge :tone="stageTone(d.stage)">{{ stageLabel(d.stage) }}</Badge>
                    <span class="trace-step__agent">{{ d.agent_id || d.agent || '—' }}</span>
                    <span v-if="d.model" class="trace-step__model muted">{{ d.model }}</span>
                  </div>
                  <div class="trace-step__time muted">{{ formatTime(d.timestamp || d.ts) }}</div>
                  <div v-if="d.reason || d.detail" class="trace-step__reason">{{ d.reason || d.detail }}</div>
                </div>
              </div>
            </div>
          </div>
          <div class="modal__foot">
            <button class="btn-ghost" type="button" @click="closeTraceModal">{{ t('common.close') }}</button>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue';
import { useApiStore } from '../stores/api.js';
import { useI18n } from '../i18n';
import ListPageLayout from '../components/ListPageLayout.vue';
import AppIcon from '../components/AppIcon.vue';
import Card from '../components/Card.vue';
import Badge from '../components/Badge.vue';
import Segmented from '../components/Segmented.vue';
import Skeleton from '../components/Skeleton.vue';
import EmptyState from '../components/EmptyState.vue';

const api = useApiStore();
const { t } = useI18n();

// ── Tab state ───────────────────────────────────────────────────
const activeTab = ref('recent');
const tabOptions = [
  { value: 'recent', label: t('view.routing.tab.recent'), icon: 'route' },
  { value: 'stats', label: t('view.routing.tab.stats'), icon: 'activity' },
];

// ── Data state ──────────────────────────────────────────────────
const loading = ref(false);
const errors = ref({ recent: null, stats: null });

const decisions = ref([]);
const stats = ref({ total: 0, by_stage: {}, last_24h: 0 });

const STAGES = ['route_scorer', 'load_balancer', 'model_selector', 'dispatcher'];

// ── Stage filter ────────────────────────────────────────────────
const stageFilter = ref('');

// ── Trace detail modal ──────────────────────────────────────────
const showTraceModal = ref(false);
const traceLoading = ref(false);
const traceError = ref(null);
const traceId = ref('');
const traceDecisions = ref([]);

// ── Helpers ─────────────────────────────────────────────────────
function stageLabel(stage) {
  if (!stage) return '—';
  const key = `view.routing.stage.${stage}`;
  const label = t(key);
  return label === key ? stage : label;
}

function stageTone(stage) {
  const map = {
    route_scorer: 'brand',
    load_balancer: 'info',
    model_selector: 'warn',
    dispatcher: 'success',
  };
  return map[stage] || 'neutral';
}

function formatTime(ts) {
  if (ts === null || ts === undefined || ts === '') return '—';
  const d = new Date(typeof ts === 'number' ? ts : String(ts));
  if (isNaN(d.getTime())) return String(ts);
  return d.toLocaleString();
}

function formatScore(score) {
  if (score === null || score === undefined) return '—';
  const n = Number(score);
  if (isNaN(n)) return String(score);
  return n.toFixed(2);
}

// ── Stats computed ──────────────────────────────────────────────
const hasStatsData = computed(() => {
  const s = stats.value;
  return (s.total ?? 0) > 0 || (s.last_24h ?? 0) > 0 || Object.keys(s.by_stage || {}).length > 0;
});

const stageCount = computed(() => Object.keys(stats.value.by_stage || {}).length);

// ── Chart (inline SVG bar chart) ────────────────────────────────
const CHART_W = 480;
const CHART_H = 200;
const CHART_PAD = 32;
const BAR_GAP = 16;

const stageBars = computed(() => {
  const byStage = stats.value.by_stage || {};
  const entries = Object.entries(byStage).filter(([, v]) => v > 0);
  if (!entries.length) return [];
  const maxVal = Math.max(...entries.map(([, v]) => v), 1);
  const n = entries.length;
  const availW = CHART_W - CHART_PAD * 2;
  const barW = Math.max(8, (availW - BAR_GAP * (n - 1)) / n);
  const maxBarH = CHART_H - CHART_PAD - 28; // 留出 label 空间
  return entries.map(([stage, count], i) => ({
    stage,
    count,
    x: CHART_PAD + i * (barW + BAR_GAP),
    y: CHART_PAD + maxBarH - (count / maxVal) * maxBarH,
    w: barW,
    h: (count / maxVal) * maxBarH,
  }));
});

// ── Data loading ────────────────────────────────────────────────
async function loadRecent() {
  try {
    const params = new URLSearchParams({ limit: '100' });
    if (stageFilter.value) params.set('stage', stageFilter.value);
    const data = await api.get(`/api/routing/decisions/recent?${params.toString()}`);
    decisions.value = data.decisions || [];
    errors.value = { ...errors.value, recent: null };
  } catch (err) {
    decisions.value = [];
    errors.value = { ...errors.value, recent: (err && err.message) || t('view.routing.recent.failedLoad') };
  }
}

async function loadStats() {
  try {
    const data = await api.get('/api/routing/decisions/stats');
    stats.value = {
      total: data.total ?? 0,
      by_stage: data.by_stage || {},
      last_24h: data.last_24h ?? 0,
    };
    errors.value = { ...errors.value, stats: null };
  } catch (err) {
    stats.value = { total: 0, by_stage: {}, last_24h: 0 };
    errors.value = { ...errors.value, stats: (err && err.message) || t('view.routing.stats.failedLoad') };
  }
}

async function loadAll() {
  loading.value = true;
  await Promise.allSettled([loadRecent(), loadStats()]);
  loading.value = false;
}

// ── Trace detail ────────────────────────────────────────────────
async function openTrace(tid) {
  if (!tid) return;
  traceId.value = tid;
  showTraceModal.value = true;
  traceLoading.value = true;
  traceError.value = null;
  traceDecisions.value = [];
  try {
    const data = await api.get(`/api/routing/decisions/${encodeURIComponent(tid)}`);
    traceDecisions.value = data.decisions || [];
  } catch (err) {
    traceError.value = (err && err.message) || t('view.routing.trace.failedLoad');
  } finally {
    traceLoading.value = false;
  }
}

function closeTraceModal() {
  showTraceModal.value = false;
  traceId.value = '';
  traceDecisions.value = [];
  traceError.value = null;
}

onMounted(() => {
  loadAll();
});
</script>

<style scoped>
/* ── Layout helpers ────────────────────────────────────────────── */
.blk { display: flex; flex-direction: column; gap: var(--sp-2); }
.muted { color: var(--text-muted); }
.mono { font-family: var(--font-mono); font-size: var(--fs-xs); }

/* ── Stage select ──────────────────────────────────────────────── */
.stage-select {
  padding: var(--sp-1) var(--sp-7) var(--sp-1) var(--sp-3);
  background-color: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: var(--r-md);
  color: var(--text);
  font-size: var(--fs-sm);
  font-family: inherit;
  cursor: pointer;
  appearance: none;
  -webkit-appearance: none;
  background-image: var(--icon-chevron);
  background-repeat: no-repeat;
  background-position: right var(--sp-2) center;
}
.stage-select:focus { outline: none; border-color: var(--brand); box-shadow: 0 0 0 3px var(--brand-soft); }

/* ── Decisions table ───────────────────────────────────────────── */
.dec-table {
  width: 100%;
  border: 1px solid var(--border-subtle);
  border-radius: var(--r-md);
  overflow: hidden;
}
.dec-table__head,
.dec-table__row {
  display: grid;
  grid-template-columns: 1fr auto 1fr 80px 64px;
  align-items: center;
  gap: var(--sp-2);
}
.dec-table__head {
  background: var(--surface-2);
  padding: var(--sp-2) var(--sp-3);
  border-bottom: 1px solid var(--border-strong);
}
.dec-table__th {
  font-size: var(--fs-xs);
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: .03em;
  color: var(--text-muted);
}
.dec-table__th--num { text-align: right; }
.dec-table__th--act { text-align: right; }
.dec-table__row {
  padding: var(--sp-3);
  border-bottom: 1px solid var(--border-subtle);
  transition: background var(--motion) var(--ease);
}
.dec-table__row:hover { background: var(--surface-2); }
.dec-table__row:last-child { border-bottom: none; }
.dec-table__td { min-width: 0; }
.dec-table__td--num { text-align: right; font-variant-numeric: tabular-nums; font-family: var(--font-mono); }
.dec-table__td--act { display: flex; justify-content: flex-end; gap: var(--sp-1); }
.dec-table__trace { color: var(--text); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dec-table__time { color: var(--text-faint); margin-top: 2px; font-size: var(--fs-xs); }
.dec-table__agent { font-weight: 600; color: var(--text); }
.dec-table__model { margin-left: var(--sp-2); font-size: var(--fs-xs); }

/* ── Icon buttons ─────────────────────────────────────────────── */
.icon-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border: 1px solid var(--border);
  border-radius: var(--r-md);
  background: var(--surface);
  color: var(--text-muted);
  cursor: pointer;
  transition: background var(--motion) var(--ease), color var(--motion) var(--ease), border-color var(--motion) var(--ease);
}
.icon-btn:hover { background: var(--surface-2); color: var(--text); border-color: var(--border-strong); }

/* ── Ghost button ─────────────────────────────────────────────── */
.btn-ghost {
  display: inline-flex;
  align-items: center;
  gap: var(--sp-1);
  padding: var(--sp-1) var(--sp-3);
  border: 1px solid var(--border);
  border-radius: var(--r-md);
  background: var(--surface);
  color: var(--text);
  font-size: var(--fs-sm);
  cursor: pointer;
  transition: background var(--motion) var(--ease), border-color var(--motion) var(--ease);
}
.btn-ghost:hover { background: var(--surface-2); border-color: var(--border-strong); }
.btn-ghost:disabled { opacity: .5; cursor: not-allowed; }
.btn-ghost.is-busy { opacity: .6; }

/* ── Stats ────────────────────────────────────────────────────── */
.stats-body { display: flex; flex-direction: column; gap: var(--sp-4); }
.stats-overview { display: grid; grid-template-columns: repeat(3, 1fr); gap: var(--sp-3); }
.stats-card {
  display: flex;
  flex-direction: column;
  gap: var(--sp-1);
  padding: var(--sp-3) var(--sp-4);
  background: var(--surface-2);
  border: 1px solid var(--border-subtle);
  border-radius: var(--r-md);
}
.stats-card__label { font-size: var(--fs-xs); text-transform: uppercase; letter-spacing: .03em; color: var(--text-muted); }
.stats-card__value { font-size: var(--fs-xl); font-weight: 600; color: var(--text); font-variant-numeric: tabular-nums; }

.stats-chart { border: 1px solid var(--border-subtle); border-radius: var(--r-md); padding: var(--sp-3); }
.stats-chart__head { display: flex; align-items: center; justify-content: space-between; gap: var(--sp-3); margin-bottom: var(--sp-3); flex-wrap: wrap; }
.stats-chart__title { font-size: var(--fs-sm); font-weight: 600; color: var(--text); }
.stats-chart__svg { width: 100%; height: 200px; display: block; }
.stats-chart__bar { opacity: .85; }
.stats-chart__bar--0 { fill: var(--brand); }
.stats-chart__bar--1 { fill: var(--info); }
.stats-chart__bar--2 { fill: var(--warn); }
.stats-chart__bar--3 { fill: var(--success); }
.stats-chart__label { font-size: 11px; fill: var(--text-muted); font-family: inherit; }
.stats-chart__value-text { font-size: 11px; fill: var(--text); font-weight: 600; font-family: var(--font-mono); }
.stats-chart__empty { text-align: center; padding: var(--sp-4); }

/* ── Modal ────────────────────────────────────────────────────── */
.modal-overlay {
  position: fixed;
  inset: 0;
  z-index: var(--z-modal);
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--overlay-scrim);
  padding: var(--sp-4);
}
.modal {
  width: 100%;
  max-width: 560px;
  max-height: 90vh;
  overflow-y: auto;
  background: var(--surface);
  border: 1px solid var(--border-strong);
  border-radius: var(--r-lg);
  box-shadow: var(--shadow-modal);
}
.modal__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--sp-3) var(--sp-4);
  border-bottom: 1px solid var(--border-subtle);
}
.modal__head h3 { font-size: var(--fs-lg); font-weight: 600; color: var(--text); margin: 0; }
.modal__x {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border: none;
  border-radius: var(--r-sm);
  background: transparent;
  color: var(--text-muted);
  cursor: pointer;
}
.modal__x:hover { background: var(--surface-2); color: var(--text); }
.modal__body { padding: var(--sp-4); display: flex; flex-direction: column; gap: var(--sp-3); }
.modal__foot {
  display: flex;
  justify-content: flex-end;
  gap: var(--sp-2);
  padding: var(--sp-3) var(--sp-4);
  border-top: 1px solid var(--border-subtle);
}

/* ── Trace chain ──────────────────────────────────────────────── */
.trace-chain { display: flex; flex-direction: column; gap: var(--sp-3); }
.trace-chain__id { color: var(--text-muted); font-size: var(--fs-sm); margin-bottom: var(--sp-1); }
.trace-step {
  display: flex;
  gap: var(--sp-3);
  padding: var(--sp-3);
  background: var(--surface-2);
  border: 1px solid var(--border-subtle);
  border-radius: var(--r-md);
}
.trace-step__idx {
  display: grid;
  place-items: center;
  width: 28px;
  height: 28px;
  flex-shrink: 0;
  border-radius: var(--r-full);
  background: var(--brand-soft);
  color: var(--brand-strong);
  font-size: var(--fs-sm);
  font-weight: 600;
}
.trace-step__body { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: var(--sp-1); }
.trace-step__head { display: flex; align-items: center; gap: var(--sp-2); flex-wrap: wrap; }
.trace-step__agent { font-weight: 600; color: var(--text); }
.trace-step__model { font-size: var(--fs-xs); }
.trace-step__time { font-size: var(--fs-xs); }
.trace-step__reason { font-size: var(--fs-sm); color: var(--text); line-height: 1.5; margin-top: var(--sp-1); }

/* ── Responsive ───────────────────────────────────────────────── */
@media (max-width: 900px) {
  .stats-overview { grid-template-columns: 1fr; }
  .dec-table__head,
  .dec-table__row { grid-template-columns: 1fr auto 64px; }
  .dec-table__th:nth-child(3),
  .dec-table__td:nth-child(3),
  .dec-table__th--num,
  .dec-table__td--num { display: none; }
}

@media (max-width: 640px) {
  .dec-table__head,
  .dec-table__row { grid-template-columns: 1fr 64px; }
  .dec-table__th:nth-child(2),
  .dec-table__td:nth-child(2) { display: none; }
  .modal__body { padding: var(--sp-3); }
  .modal__foot { flex-direction: column-reverse; }
  .modal__foot .btn-ghost { width: 100%; justify-content: center; }
}
</style>
