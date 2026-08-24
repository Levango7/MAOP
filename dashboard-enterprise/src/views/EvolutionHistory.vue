<template>
  <div class="evo-history-page">
    <PageHeader v-if="!embedded">
      <span class="subtitle muted">{{ t('view.evolutionHistory.subtitle') }}</span>
      <Segmented
        v-model="activeTab"
        :options="tabOptions"
        size="sm"
        class="tab-switch"
      />
      <button class="btn-ghost" :class="{ 'is-busy': loading }" :disabled="loading" @click="loadAll">
        <AppIcon name="refresh" :size="15" />
        <span>{{ t('view.evolutionHistory.refresh') }}</span>
      </button>
    </PageHeader>

    <!-- 嵌入式模式: tab 切换条单独显示 -->
    <div v-if="embedded" class="embedded-tabs">
      <Segmented
        v-model="activeTab"
        :options="tabOptions"
        size="sm"
      />
    </div>

    <!-- ════════════ History Tab ════════════ -->
    <template v-if="activeTab === 'history'">
      <div class="stat-row">
        <StatCard
          :label="t('view.evolutionHistory.stat.totalCycles')"
          :value="cycles.length"
          icon="activity"
          tone="brand"
          :loading="loading"
        />
        <StatCard
          :label="t('view.evolutionHistory.stat.promotions')"
          :value="promotionCount"
          icon="arrow-up"
          tone="success"
          :loading="loading"
        />
        <StatCard
          :label="t('view.evolutionHistory.stat.rollbacks')"
          :value="rollbackCount"
          icon="arrow-down"
          tone="fail"
          :loading="loading"
        />
        <StatCard
          :label="t('view.evolutionHistory.stat.pending')"
          :value="pending.length"
          icon="clock"
          tone="warn"
          :loading="loading"
        />
      </div>

      <!-- 演化循环历史 -->
      <Card :title="t('view.evolutionHistory.cycles.title')" icon="activity" :margin-bottom="16">
        <div class="card-desc muted">{{ t('view.evolutionHistory.cycles.desc') }}</div>
        <DataTable
          v-if="cycles.length"
          :columns="cycleCols"
          :rows="cycleRows"
          row-key="cycle_id"
          :loading="loading"
          :empty-text="t('view.evolutionHistory.noData')"
          clickable
          @row-click="onCycleClick"
        />
        <EmptyState
          v-else-if="!loading"
          icon="activity"
          :title="t('view.evolutionHistory.noData')"
          :description="t('view.evolutionHistory.noDataDesc')"
        />
        <Skeleton v-else height="160px" />
      </Card>

      <!-- 选中周期的阶段时间线 + 建议详情（迭代 C）-->
      <Card
        v-if="selectedCycle"
        :title="t('view.evolutionHistory.phases.title')"
        icon="git-branch"
        :margin-bottom="16"
      >
        <template #actions>
          <button class="btn-ghost btn-sm" @click="selectedCycle = null">
            <AppIcon name="x" :size="12" />
          </button>
        </template>
        <div class="card-desc muted">{{ t('view.evolutionHistory.phases.desc') }}</div>
        <div class="selected-cycle-tag">
          <span class="mono">{{ selectedCycle.cycle_id }}</span>
          <span v-if="selectedCycle.experiment" class="muted">· {{ selectedCycle.experiment }}</span>
        </div>
        <EvolutionTimeline
          v-if="selectedCycle.phases && selectedCycle.phases.length"
          :phases="selectedCycle.phases"
          :empty-title="t('view.evolutionHistory.phases.empty')"
        />
        <EmptyState
          v-else
          icon="git-branch"
          :title="t('view.evolutionHistory.phases.empty')"
        />
      </Card>

      <!-- 建议详情展开卡片（迭代 C）-->
      <Card
        v-if="selectedCycle && selectedSuggestions.length"
        :title="t('view.evolutionHistory.suggestions.title')"
        icon="sparkles"
        :margin-bottom="16"
      >
        <div class="card-desc muted">{{ t('view.evolutionHistory.suggestions.desc') }}</div>
        <div class="suggestion-list">
          <div
            v-for="sug in selectedSuggestions"
            :key="sug.id || sug.description"
            class="suggestion-item"
            :class="{ 'is-open': openSuggestions.has(sug.id || sug.description) }"
          >
            <button
              class="suggestion-item__head"
              :aria-expanded="openSuggestions.has(sug.id || sug.description)"
              @click="toggleSuggestion(sug.id || sug.description)"
            >
              <AppIcon
                :name="openSuggestions.has(sug.id || sug.description) ? 'chevrondown' : 'chevron-right'"
                :size="14"
                class="suggestion-item__caret"
              />
              <span class="suggestion-item__id mono">{{ sug.id || '—' }}</span>
              <Badge :tone="severityTone(sug.severity)">{{ sug.severity || 'MEDIUM' }}</Badge>
              <span class="suggestion-item__desc">{{ sug.description || '—' }}</span>
              <Badge v-if="sug.auto_applicable" tone="success" class="suggestion-item__auto">
                {{ t('view.evolutionHistory.suggestions.autoApplicable') }}
              </Badge>
            </button>
            <div
              v-if="openSuggestions.has(sug.id || sug.description)"
              class="suggestion-item__body"
            >
              <div class="suggestion-grid">
                <div class="suggestion-field">
                  <span class="suggestion-field__label">{{ t('view.evolutionHistory.suggestions.id') }}</span>
                  <span class="suggestion-field__value mono">{{ sug.id || '—' }}</span>
                </div>
                <div class="suggestion-field">
                  <span class="suggestion-field__label">{{ t('view.evolutionHistory.suggestions.source') }}</span>
                  <span class="suggestion-field__value">{{ sug.source || '—' }}</span>
                </div>
                <div class="suggestion-field">
                  <span class="suggestion-field__label">{{ t('view.evolutionHistory.suggestions.category') }}</span>
                  <span class="suggestion-field__value">{{ sug.category || sug.suggestion_type || '—' }}</span>
                </div>
                <div class="suggestion-field">
                  <span class="suggestion-field__label">{{ t('view.evolutionHistory.suggestions.mutation') }}</span>
                  <span class="suggestion-field__value mono">{{ sug.mutation_type || sug.type || '—' }}</span>
                </div>
                <div class="suggestion-field">
                  <span class="suggestion-field__label">{{ t('view.evolutionHistory.suggestions.severity') }}</span>
                  <span class="suggestion-field__value">{{ sug.severity || '—' }}</span>
                </div>
                <div class="suggestion-field">
                  <span class="suggestion-field__label">{{ t('view.evolutionHistory.suggestions.target') }}</span>
                  <span class="suggestion-field__value">
                    {{ sug.target_type || '—' }} / {{ sug.target_name || '—' }}
                  </span>
                </div>
                <div class="suggestion-field">
                  <span class="suggestion-field__label">{{ t('view.evolutionHistory.suggestions.autoApplicable') }}</span>
                  <span class="suggestion-field__value">
                    {{ sug.auto_applicable ? t('view.evolutionHistory.suggestions.yes') : t('view.evolutionHistory.suggestions.no') }}
                  </span>
                </div>
                <div class="suggestion-field">
                  <span class="suggestion-field__label">{{ t('view.evolutionHistory.suggestions.applied') }}</span>
                  <span class="suggestion-field__value">
                    {{ sug.applied ? t('view.evolutionHistory.suggestions.yes') : t('view.evolutionHistory.suggestions.no') }}
                  </span>
                </div>
                <div class="suggestion-field suggestion-field--full">
                  <span class="suggestion-field__label">{{ t('view.evolutionHistory.suggestions.description') }}</span>
                  <span class="suggestion-field__value">{{ sug.description || '—' }}</span>
                </div>
                <div class="suggestion-field suggestion-field--full">
                  <span class="suggestion-field__label">{{ t('view.evolutionHistory.suggestions.params') }}</span>
                  <pre class="suggestion-field__code">{{ formatJson(sug.mutation_params) }}</pre>
                </div>
              </div>
            </div>
          </div>
        </div>
      </Card>

      <!-- A/B 实验 -->
      <Card :title="t('view.evolutionHistory.ab.title')" icon="beaker" :margin-bottom="16">
        <div class="card-desc muted">{{ t('view.evolutionHistory.ab.desc') }}</div>
        <DataTable
          v-if="abRows.length"
          :columns="abCols"
          :rows="abRows"
          row-key="name"
          :empty-text="t('view.evolutionHistory.noData')"
        />
        <EmptyState
          v-else
          icon="beaker"
          :title="t('view.evolutionHistory.noData')"
          :description="t('view.evolutionHistory.noDataDesc')"
        />
      </Card>

      <!-- 部署历史 -->
      <Card :title="t('view.evolutionHistory.deploy.title')" icon="rotate-ccw" :margin-bottom="16">
        <div class="card-desc muted">{{ t('view.evolutionHistory.deploy.desc') }}</div>
        <DataTable
          v-if="deployRows.length"
          :columns="deployCols"
          :rows="deployRows"
          row-key="id"
          :empty-text="t('view.evolutionHistory.noData')"
        />
        <EmptyState
          v-else
          icon="rotate-ccw"
          :title="t('view.evolutionHistory.noData')"
          :description="t('view.evolutionHistory.noDataDesc')"
        />
      </Card>

      <!-- 待批准（人工 gate） -->
      <Card :title="t('view.evolutionHistory.pending.title')" icon="clock" :margin-bottom="16">
        <div class="card-desc muted">{{ t('view.evolutionHistory.pending.desc') }}</div>
        <div v-if="pending.length" class="pending-list">
          <div v-for="item in pending" :key="item.cycle_id" class="pending-item">
            <div class="pending-item__main">
              <span class="pending-item__exp">{{ item.experiment }}</span>
              <span class="pending-item__cycle muted">{{ item.cycle_id }}</span>
            </div>
            <div class="pending-item__detail muted">{{ item.detail }}</div>
            <button class="btn-action" :disabled="approving === item.cycle_id" @click="approve(item)">
              <AppIcon name="check-circle" :size="14" />
              {{ t('view.evolutionHistory.approve') }}
            </button>
          </div>
        </div>
        <EmptyState
          v-else
          icon="clock"
          :title="t('view.evolutionHistory.noData')"
          :description="t('view.evolutionHistory.pending.desc')"
        />
      </Card>
    </template>

    <!-- ════════════ Prompt Diff Tab ════════════ -->
    <template v-else-if="activeTab === 'compare'">
      <Card :title="t('view.evolutionHistory.compare.title')" icon="git-compare" :margin-bottom="16">
        <div class="card-desc muted">{{ t('view.evolutionHistory.compare.desc') }}</div>
        <div class="compare-controls">
          <label class="compare-field">
            <span class="compare-field__label">{{ t('view.evolutionHistory.compare.base') }}</span>
            <select v-model="compareBase" class="compare-select">
              <option value="">{{ t('view.evolutionHistory.compare.empty') }}</option>
              <option v-for="c in cycles" :key="c.cycle_id" :value="c.cycle_id">
                {{ c.cycle_id }} · {{ c.experiment || '—' }}
              </option>
            </select>
          </label>
          <button
            class="btn-ghost btn-sm"
            :disabled="!compareBase || !compareTarget"
            :title="t('view.evolutionHistory.compare.swap')"
            @click="swapCompare"
          >
            <AppIcon name="arrow-left-right" :size="14" />
          </button>
          <label class="compare-field">
            <span class="compare-field__label">{{ t('view.evolutionHistory.compare.target') }}</span>
            <select v-model="compareTarget" class="compare-select">
              <option value="">{{ t('view.evolutionHistory.compare.empty') }}</option>
              <option v-for="c in cycles" :key="c.cycle_id" :value="c.cycle_id">
                {{ c.cycle_id }} · {{ c.experiment || '—' }}
              </option>
            </select>
          </label>
          <button
            class="btn-action"
            :disabled="!canCompare"
            @click="runCompare"
          >
            <AppIcon name="search" :size="14" />
            {{ t('view.evolutionHistory.compare.run') }}
          </button>
        </div>

        <div v-if="diffRows.length" class="diff-result">
          <div class="diff-stats">
            <Badge tone="success">
              {{ t('view.evolutionHistory.compare.stat.added') }}: {{ diffStat.added }}
            </Badge>
            <Badge tone="fail">
              {{ t('view.evolutionHistory.compare.stat.removed') }}: {{ diffStat.removed }}
            </Badge>
            <Badge tone="neutral">
              {{ t('view.evolutionHistory.compare.stat.unchanged') }}: {{ diffStat.unchanged }}
            </Badge>
          </div>
          <div class="diff-view">
            <div
              v-for="(row, i) in diffRows"
              :key="i"
              class="diff-line"
              :class="'diff-line--' + row.type"
            >
              <span class="diff-line__gutter">{{ row.type === 'added' ? '+' : row.type === 'removed' ? '-' : ' ' }}</span>
              <span class="diff-line__text">{{ row.text }}</span>
            </div>
          </div>
        </div>
        <EmptyState
          v-else-if="!compareBase || !compareTarget"
          icon="git-compare"
          :title="t('view.evolutionHistory.compare.empty')"
        />
        <EmptyState
          v-else
          icon="check"
          :title="t('view.evolutionHistory.compare.noDiff')"
        />
      </Card>
    </template>

    <!-- ════════════ Narrative Tab ════════════ -->
    <template v-else-if="activeTab === 'narrative'">
      <Card :title="t('view.evolutionHistory.narrative.title')" icon="scroll" :margin-bottom="16">
        <div class="card-desc muted">{{ t('view.evolutionHistory.narrative.desc') }}</div>
        <div class="narrative-controls">
          <label class="compare-field">
            <span class="compare-field__label">{{ t('view.evolutionHistory.narrative.select') }}</span>
            <select v-model="narrativeCycleId" class="compare-select">
              <option value="">{{ t('view.evolutionHistory.narrative.empty') }}</option>
              <option v-for="c in cycles" :key="c.cycle_id" :value="c.cycle_id">
                {{ c.cycle_id }} · {{ c.experiment || '—' }}
              </option>
            </select>
          </label>
          <button
            class="btn-action"
            :disabled="!narrativeCycleId || narrativeLoading"
            @click="loadNarrative"
          >
            <AppIcon name="scroll" :size="14" />
            {{ narrativeLoading ? t('view.evolutionHistory.narrative.loading') : t('view.evolutionHistory.narrative.load') }}
          </button>
        </div>

        <div v-if="narrativeLoading" class="narrative-loading">
          <Skeleton height="200px" />
        </div>
        <div v-else-if="narrativeHtml" class="narrative-view markdown-body" v-html="narrativeHtml"></div>
        <EmptyState
          v-else-if="narrativeError"
          icon="alert-triangle"
          :title="t('view.evolutionHistory.narrative.failed')"
          :description="narrativeError"
        />
        <EmptyState
          v-else
          icon="scroll"
          :title="t('view.evolutionHistory.narrative.empty')"
        />
      </Card>
    </template>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, reactive } from 'vue';
import { useApiStore } from '../stores/api.js';
import { useToast } from '../composables/useToast.js';
import { useI18n } from '../i18n';
import { useMarkdown } from '../composables/useMarkdown.js';
import { useTextDiff } from '../composables/useTextDiff.js';
import AppIcon from '../components/AppIcon.vue';
import PageHeader from '../components/PageHeader.vue';
import Card from '../components/Card.vue';
import StatCard from '../components/StatCard.vue';
import DataTable from '../components/DataTable.vue';
import Skeleton from '../components/Skeleton.vue';
import EmptyState from '../components/EmptyState.vue';
import Badge from '../components/Badge.vue';
import Segmented from '../components/Segmented.vue';
import EvolutionTimeline from '../components/EvolutionTimeline.vue';

const { t } = useI18n();
const { render: renderMarkdown } = useMarkdown();
const { diff: diffText, stats: diffStats } = useTextDiff();
// 嵌入式模式: 作为 Evolve.vue 的 history 子视图时, 隐藏自身 PageHeader
defineProps({
  embedded: { type: Boolean, default: false },
});
const api = useApiStore();
const toast = useToast();

const loading = ref(false);
const cycles = ref([]);
const abExperiments = ref([]);
const deployments = ref([]);
const pending = ref([]);
const approving = ref('');

// ── 迭代 C：tab 切换 ────────────────────────────────────────────
const activeTab = ref('history');
const tabOptions = computed(() => [
  { value: 'history', label: t('view.evolutionHistory.tab.history'), icon: 'activity' },
  { value: 'compare', label: t('view.evolutionHistory.tab.compare'), icon: 'git-compare' },
  { value: 'narrative', label: t('view.evolutionHistory.tab.narrative'), icon: 'scroll' },
]);

// ── 迭代 C：选中周期 + 建议展开 ────────────────────────────────
const selectedCycle = ref(null);
const openSuggestions = reactive(new Set());

function onCycleClick(row) {
  // row 是 cycleRows 中的展开行, 用 cycle_id 找回原始 cycle
  const cycle = cycles.value.find((c) => c.cycle_id === row.cycle_id);
  if (!cycle) return;
  // 同一行再次点击 → 收起
  if (selectedCycle.value && selectedCycle.value.cycle_id === cycle.cycle_id) {
    selectedCycle.value = null;
    openSuggestions.clear();
    return;
  }
  selectedCycle.value = cycle;
  openSuggestions.clear();
}

function toggleSuggestion(key) {
  if (openSuggestions.has(key)) openSuggestions.delete(key);
  else openSuggestions.add(key);
}

const selectedSuggestions = computed(() => {
  const cycle = selectedCycle.value;
  if (!cycle || !cycle.phases) return [];
  const suggestPhase = cycle.phases.find((p) => p.phase === 'suggest');
  if (!suggestPhase || !suggestPhase.details) return [];
  const list = suggestPhase.details.suggestions || [];
  return Array.isArray(list) ? list : [];
});

function severityTone(sev) {
  const s = String(sev || '').toUpperCase();
  if (s === 'HIGH') return 'fail';
  if (s === 'MEDIUM') return 'warn';
  if (s === 'LOW') return 'info';
  return 'neutral';
}

function formatJson(v) {
  if (v === null || v === undefined) return '—';
  if (typeof v === 'object') {
    try { return JSON.stringify(v, null, 2); } catch { return String(v); }
  }
  return String(v);
}

// ── 迭代 C：Prompt 版本对比 ────────────────────────────────────
const compareBase = ref('');
const compareTarget = ref('');
const diffRows = ref([]);
const diffStat = computed(() => diffStats(diffRows.value));

const canCompare = computed(
  () => compareBase.value && compareTarget.value && compareBase.value !== compareTarget.value,
);

function swapCompare() {
  const a = compareBase.value;
  compareBase.value = compareTarget.value;
  compareTarget.value = a;
}

/**
 * 从 cycle 提取 prompt/config 文本用于 diff。
 * 优先提取 SUGGEST 阶段中 mutation_type="adjust_prompt" 的建议；
 * 若无 prompt 类型建议，回退为该 cycle 全部建议的序列化文本，
 * 使两个周期的调优差异可视化。
 */
function extractPromptText(cycle) {
  if (!cycle || !cycle.phases) return '';
  const suggestPhase = cycle.phases.find((p) => p.phase === 'suggest');
  if (!suggestPhase || !suggestPhase.details) return '';
  const all = suggestPhase.details.suggestions || [];
  if (!Array.isArray(all) || !all.length) return '';
  const promptSugs = all.filter(
    (s) => s.mutation_type === 'adjust_prompt' || s.type === 'adjust_prompt',
  );
  const target = promptSugs.length ? promptSugs : all;
  return target.map((s) => {
    const lines = [];
    if (s.id) lines.push(`# ${s.id}`);
    if (s.description) lines.push(s.description);
    if (s.target_type || s.target_name) {
      lines.push(`target: ${s.target_type || ''}/${s.target_name || ''}`);
    }
    if (s.severity) lines.push(`severity: ${s.severity}`);
    const params = s.mutation_params || {};
    for (const [k, v] of Object.entries(params)) {
      lines.push(`${k}: ${typeof v === 'object' ? JSON.stringify(v) : v}`);
    }
    return lines.join('\n');
  }).join('\n---\n');
}

function runCompare() {
  if (!canCompare.value) return;
  const baseCycle = cycles.value.find((c) => c.cycle_id === compareBase.value);
  const targetCycle = cycles.value.find((c) => c.cycle_id === compareTarget.value);
  const baseText = extractPromptText(baseCycle);
  const targetText = extractPromptText(targetCycle);
  diffRows.value = diffText(baseText, targetText);
}

// ── 迭代 C：叙事视图 ───────────────────────────────────────────
const narrativeCycleId = ref('');
const narrativeLoading = ref(false);
const narrativeHtml = ref('');
const narrativeError = ref('');

async function loadNarrative() {
  if (!narrativeCycleId.value) return;
  narrativeLoading.value = true;
  narrativeError.value = '';
  narrativeHtml.value = '';
  try {
    const res = await api.get(`/api/evolution/narrative/${encodeURIComponent(narrativeCycleId.value)}`);
    const md = res.markdown || '';
    narrativeHtml.value = renderMarkdown(md);
  } catch (e) {
    narrativeError.value = e.message || String(e);
  } finally {
    narrativeLoading.value = false;
  }
}

// ── 统计 ───────────────────────────────────────────────────────
const promotionCount = computed(() => cycles.value.filter((c) => c.promoted).length);
const rollbackCount = computed(() => cycles.value.filter((c) => c.rolled_back).length);

const cycleCols = computed(() => [
  { key: 'cycle_id', label: t('view.evolutionHistory.colCycle') },
  { key: 'experiment', label: t('view.evolutionHistory.colExperiment') },
  { key: 'decision_label', label: t('view.evolutionHistory.colDecision'), type: 'badge' },
  { key: 'winner', label: t('view.evolutionHistory.colWinner') },
  { key: 'promoted', label: t('view.evolutionHistory.colPromoted'), type: 'num' },
  { key: 'rolled_back', label: t('view.evolutionHistory.colRolledBack'), type: 'num' },
  { key: 'suggestions_count', label: t('view.evolutionHistory.colSuggestions'), type: 'num' },
  { key: 'duration_s', label: t('view.evolutionHistory.colDuration'), type: 'num' },
  { key: 'time_label', label: t('view.evolutionHistory.colTime') },
]);

const cycleRows = computed(() =>
  cycles.value.map((c) => ({
    ...c,
    promoted: c.promoted ? 1 : 0,
    rolled_back: c.rolled_back ? 1 : 0,
    decision_label: t(`view.evolutionHistory.decision.${c.sprt_decision || 'continue'}`),
    time_label: formatTs(c.started_at),
    duration_s: (c.duration_s || c.total_duration_s || 0).toFixed(2) + 's',
    suggestions_count: c.suggestions_generated ?? 0,
  })),
);

const abCols = computed(() => [
  { key: 'name', label: t('view.evolutionHistory.colExperiment') },
  { key: 'decision_label', label: t('view.evolutionHistory.colDecision'), type: 'badge' },
  { key: 'winner', label: t('view.evolutionHistory.colWinner') },
  { key: 'samples', label: t('view.evolutionHistory.colSuggestions'), type: 'num' },
  { key: 'success_rate', label: 'Rate %', type: 'num' },
]);

const abRows = computed(() =>
  abExperiments.value.map((e) => ({
    name: e.name,
    decision_label: t(`view.evolutionHistory.decision.${e.decision || 'continue'}`),
    winner: e.winner || '—',
    samples: e.samples || 0,
    success_rate: e.success_rate !== null ? (e.success_rate * 100).toFixed(1) : '—',
  })),
);

const deployCols = computed(() => [
  { key: 'experiment', label: t('view.evolutionHistory.colExperiment') },
  { key: 'action', label: t('view.evolutionHistory.colAction'), type: 'badge' },
  { key: 'winner', label: t('view.evolutionHistory.colWinner') },
  { key: 'snapshot_id', label: t('view.evolutionHistory.colSnapshot') },
  { key: 'success_label', label: t('view.evolutionHistory.colSuccess'), type: 'bool-icon' },
  { key: 'time_label', label: t('view.evolutionHistory.colTime') },
]);

const deployRows = computed(() =>
  deployments.value.map((d) => ({
    ...d,
    success_label: d.success,
    time_label: formatTs(d.created_at),
    snapshot_id: d.snapshot_id ? d.snapshot_id.slice(0, 12) : '—',
  })),
);

function formatTs(ts) {
  if (!ts) return '—';
  try {
    const d = new Date(ts * 1000);
    return isNaN(d.getTime()) ? String(ts) : d.toLocaleString();
  } catch {
    return String(ts);
  }
}

async function loadCycles() {
  try {
    const res = await api.get('/api/evolution/cycles?limit=50');
    cycles.value = res.cycles || [];
  } catch {
    cycles.value = [];
  }
}

async function loadAb() {
  try {
    const res = await api.get('/api/evolution/ab/list');
    const names = res.experiments || [];
    abExperiments.value = await Promise.all(
      names.map(async (name) => {
        try {
          const r = await api.get(`/api/evolution/ab/evaluate/${encodeURIComponent(name)}`);
          const sprt = r.result?.sprt || {};
          return {
            name,
            decision: r.result?.decision || 'continue',
            winner: r.result?.winner || '',
            samples: sprt.samples || 0,
            success_rate: sprt.success_rate,
          };
        } catch {
          return { name, decision: 'continue', winner: '', samples: 0, success_rate: null };
        }
      }),
    );
  } catch {
    abExperiments.value = [];
  }
}

async function loadDeployments() {
  try {
    const res = await api.get('/api/evolution/deploy/history');
    deployments.value = res.history || [];
  } catch {
    deployments.value = [];
  }
}

async function loadPending() {
  try {
    const res = await api.get('/api/evolution/pending');
    pending.value = res.pending || [];
  } catch {
    pending.value = [];
  }
}

async function loadAll() {
  loading.value = true;
  await Promise.all([loadCycles(), loadAb(), loadDeployments(), loadPending()]);
  loading.value = false;
}

async function approve(item) {
  approving.value = item.cycle_id;
  try {
    await api.post('/api/evolution/approve', { experiment: item.experiment });
    toast.success(t('view.evolutionHistory.approved'));
    await loadAll();
  } catch (e) {
    toast.error(e.message || t('view.evolutionHistory.approveFailed'));
  } finally {
    approving.value = '';
  }
}

onMounted(() => {
  loadAll();
});
</script>

<style scoped>
.subtitle {
  font-size: 13px;
  margin-right: auto;
  padding-right: 12px;
}
.card-desc {
  font-size: 12px;
  margin-bottom: 10px;
}
.muted { color: var(--text-muted); }
.mono { font-family: var(--font-mono); font-variant-numeric: tabular-nums; }

.tab-switch { margin-right: 12px; }
.embedded-tabs { margin-bottom: 12px; }

.pending-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.pending-item {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 4px 12px;
  align-items: center;
  padding: 10px 12px;
  border: 1px solid var(--border-light, rgba(148,163,184,.35));
  border-radius: 6px;
}
.pending-item__main {
  display: flex;
  gap: 10px;
  align-items: baseline;
  font-weight: 600;
}
.pending-item__detail {
  grid-column: 1 / 2;
  font-size: 12px;
}
.btn-action {
  grid-row: 1 / 3;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px;
  border: 1px solid var(--border, rgba(148,163,184,.45));
  background: var(--surface, #fff);
  border-radius: 5px;
  cursor: pointer;
  font-size: 13px;
}
.btn-action:hover:not(:disabled) {
  background: var(--surface-2, rgba(148,163,184,.16));
}
.btn-action:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
.btn-ghost {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 5px 10px;
  border: 1px solid var(--border, rgba(148,163,184,.45));
  background: transparent;
  border-radius: 5px;
  cursor: pointer;
  font-size: 13px;
}
.btn-ghost:hover:not(:disabled) {
  background: var(--surface-2, rgba(148,163,184,.16));
}
.btn-ghost.is-busy {
  opacity: 0.6;
  cursor: progress;
}
.btn-sm { padding: 3px 8px; font-size: 12px; }

/* ── 选中周期标签 ─────────────────────────────────────────── */
.selected-cycle-tag {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 10px;
  padding: 4px 10px;
  background: var(--surface-2);
  border: 1px solid var(--border-subtle, var(--border));
  border-radius: var(--r-sm);
  font-size: var(--fs-sm);
  width: fit-content;
}

/* ── 建议详情展开卡片（迭代 C）────────────────────────────── */
.suggestion-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.suggestion-item {
  border: 1px solid var(--border);
  border-radius: var(--r-md);
  background: var(--surface);
  transition: border-color var(--motion) var(--ease);
}
.suggestion-item.is-open { border-color: var(--border-strong); }
.suggestion-item__head {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 8px 12px;
  border: none;
  background: transparent;
  cursor: pointer;
  text-align: left;
  font-size: var(--fs-sm);
  color: var(--text);
}
.suggestion-item__head:hover { background: var(--surface-2); }
.suggestion-item__caret { color: var(--text-faint); flex: 0 0 auto; }
.suggestion-item__id { font-size: var(--fs-xs); color: var(--text-muted); flex: 0 0 auto; }
.suggestion-item__desc {
  flex: 1 1 auto;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.suggestion-item__auto { flex: 0 0 auto; }
.suggestion-item__body {
  padding: 10px 12px;
  border-top: 1px solid var(--border-subtle, var(--border));
}
.suggestion-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px 16px;
}
.suggestion-field {
  display: flex;
  flex-direction: column;
  gap: 2px;
  font-size: var(--fs-xs);
}
.suggestion-field--full { grid-column: 1 / -1; }
.suggestion-field__label {
  color: var(--text-faint);
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: .03em;
  font-size: var(--fs-xs);
}
.suggestion-field__value { color: var(--text); word-break: break-all; }
.suggestion-field__code {
  margin: 2px 0 0;
  padding: 8px 10px;
  background: var(--surface-2);
  border: 1px solid var(--border-subtle, var(--border));
  border-radius: var(--r-sm);
  font-family: var(--font-mono);
  font-size: var(--fs-xs);
  color: var(--text);
  overflow-x: auto;
  white-space: pre-wrap;
  word-break: break-all;
}

/* ── Prompt 对比视图（迭代 C）────────────────────────────── */
.compare-controls {
  display: flex;
  align-items: flex-end;
  gap: 10px;
  flex-wrap: wrap;
  margin-bottom: 14px;
}
.compare-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: var(--fs-xs);
}
.compare-field__label {
  color: var(--text-muted);
  font-weight: 600;
}
.compare-select {
  padding: 5px 8px;
  border: 1px solid var(--border);
  border-radius: var(--r-sm);
  background: var(--surface);
  color: var(--text);
  font-size: var(--fs-sm);
  min-width: 220px;
}
.diff-result { display: flex; flex-direction: column; gap: 10px; }
.diff-stats { display: flex; gap: 8px; flex-wrap: wrap; }
.diff-view {
  border: 1px solid var(--border);
  border-radius: var(--r-md);
  background: var(--surface);
  overflow-x: auto;
  font-family: var(--font-mono);
  font-size: var(--fs-xs);
  line-height: 1.55;
}
.diff-line {
  display: flex;
  align-items: baseline;
  gap: 0;
  padding: 0 8px;
  white-space: pre;
}
.diff-line__gutter {
  flex: 0 0 24px;
  color: var(--text-faint);
  user-select: none;
  text-align: center;
}
.diff-line__text { color: var(--text); word-break: break-all; }
.diff-line--added { background: var(--success-soft, rgba(34,197,94,.10)); }
.diff-line--added .diff-line__text { color: var(--success-strong, var(--success)); }
.diff-line--removed { background: var(--fail-soft, rgba(239,68,68,.10)); }
.diff-line--removed .diff-line__text { color: var(--fail); }
.diff-line--unchanged .diff-line__text { color: var(--text-muted); }

/* ── 叙事视图（迭代 C）────────────────────────────────────── */
.narrative-controls {
  display: flex;
  align-items: flex-end;
  gap: 10px;
  flex-wrap: wrap;
  margin-bottom: 14px;
}
.narrative-loading { display: flex; flex-direction: column; gap: 8px; }
.narrative-view {
  padding: 14px 16px;
  border: 1px solid var(--border);
  border-radius: var(--r-md);
  background: var(--surface);
  font-size: var(--fs-sm);
  line-height: 1.65;
  color: var(--text);
  overflow-x: auto;
}
.narrative-view :deep(h1) { font-size: 1.4em; font-weight: 700; margin: 0 0 8px; }
.narrative-view :deep(h2) { font-size: 1.2em; font-weight: 700; margin: 14px 0 6px; }
.narrative-view :deep(h3) { font-size: 1.05em; font-weight: 600; margin: 12px 0 4px; }
.narrative-view :deep(h4) { font-size: 1em; font-weight: 600; margin: 10px 0 4px; }
.narrative-view :deep(p) { margin: 0 0 8px; }
.narrative-view :deep(ul),
.narrative-view :deep(ol) { margin: 0 0 8px; padding-left: 22px; }
.narrative-view :deep(li) { margin: 2px 0; }
.narrative-view :deep(strong) { font-weight: 700; }
.narrative-view :deep(.md-code-inline) {
  font-family: var(--font-mono);
  font-size: .9em;
  padding: 1px 5px;
  background: var(--surface-2);
  border-radius: var(--r-sm);
}
.narrative-view :deep(.md-code-block) {
  display: block;
  font-family: var(--font-mono);
  font-size: var(--fs-xs);
  padding: 10px 12px;
  background: var(--surface-2);
  border: 1px solid var(--border-subtle, var(--border));
  border-radius: var(--r-sm);
  overflow-x: auto;
  white-space: pre;
}
.narrative-view :deep(blockquote) {
  margin: 0 0 8px;
  padding: 6px 12px;
  border-left: 3px solid var(--border-strong);
  color: var(--text-muted);
}
.narrative-view :deep(hr) {
  border: none;
  border-top: 1px solid var(--border);
  margin: 10px 0;
}
</style>
