<template>
  <div class="scheduling-view">
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
        <!-- ── Agent health ─────────────────────────────────────── -->
        <div v-show="activeTab === 'agents'">
          <Card :title="t('view.scheduling.agents.title')" icon="activity" margin-bottom="var(--sp-4)">
            <template #actions>
              <button class="btn-ghost" :disabled="resetting" @click="resetAll">
                <AppIcon name="rotate-ccw" :size="14" />
                <span>{{ t('view.scheduling.agents.resetAll') }}</span>
              </button>
            </template>
            <div v-if="loading" class="blk">
              <Skeleton block height="44px" />
              <Skeleton block height="44px" />
              <Skeleton block height="44px" />
            </div>
            <EmptyState
              v-else-if="error"
              icon="alert-triangle"
              tone="fail"
              :title="t('view.scheduling.agents.failedLoad')"
              :description="error"
            />
            <EmptyState
              v-else-if="!agents.length"
              icon="activity"
              :title="t('view.scheduling.agents.empty')"
              :description="t('view.scheduling.agents.emptyHint')"
            />
            <div v-else class="agent-table" role="table" :aria-label="t('view.scheduling.agents.title')">
              <div class="agent-table__head" role="row">
                <div class="agent-table__th" role="columnheader">{{ t('view.scheduling.col.agent') }}</div>
                <div class="agent-table__th" role="columnheader">{{ t('view.scheduling.col.status') }}</div>
                <div class="agent-table__th agent-table__th--num" role="columnheader">{{ t('view.scheduling.col.failureRate') }}</div>
                <div class="agent-table__th agent-table__th--num" role="columnheader">{{ t('view.scheduling.col.avgLatency') }}</div>
                <div class="agent-table__th agent-table__th--num" role="columnheader">{{ t('view.scheduling.col.weight') }}</div>
                <div class="agent-table__th agent-table__th--act" role="columnheader">{{ t('view.scheduling.col.actions') }}</div>
              </div>
              <div v-for="a in agents" :key="a.agent_id" class="agent-table__row" role="row">
                <div class="agent-table__td" role="cell">
                  <span class="agent-table__name">{{ a.agent_id || '—' }}</span>
                </div>
                <div class="agent-table__td" role="cell">
                  <Badge :tone="statusTone(a.status)">{{ statusLabel(a.status) }}</Badge>
                </div>
                <div class="agent-table__td agent-table__td--num" role="cell">
                  <span :class="{ 'is-warn': failureRatePercent(a) >= 30, 'is-fail': failureRatePercent(a) >= 50 }">
                    {{ formatPercent(a.failure_rate) }}
                  </span>
                </div>
                <div class="agent-table__td agent-table__td--num" role="cell">{{ formatLatency(a.avg_latency) }}</div>
                <div class="agent-table__td agent-table__td--num" role="cell">{{ formatWeight(a.weight) }}</div>
                <div class="agent-table__td agent-table__td--act" role="cell">
                  <button
                    class="icon-btn"
                    type="button"
                    :aria-label="t('view.scheduling.agents.reset')"
                    :disabled="resetting === a.agent_id"
                    @click="resetAgent(a.agent_id)"
                  >
                    <AppIcon name="rotate-ccw" :size="14" />
                  </button>
                </div>
              </div>
            </div>
          </Card>
        </div>

        <!-- ── Configuration ────────────────────────────────────── -->
        <div v-show="activeTab === 'config'">
          <Card :title="t('view.scheduling.config.title')" icon="gear" margin-bottom="var(--sp-4)">
            <div v-if="loading" class="blk">
              <Skeleton block height="80px" />
              <Skeleton block height="80px" />
            </div>
            <EmptyState
              v-else-if="error"
              icon="alert-triangle"
              tone="fail"
              :title="t('view.scheduling.agents.failedLoad')"
              :description="error"
            />
            <EmptyState
              v-else-if="!hasConfig"
              icon="gear"
              :title="t('view.scheduling.config.empty')"
              :description="t('view.scheduling.config.emptyHint')"
            />
            <div v-else class="config-body">
              <div class="config-overview">
                <div class="config-card">
                  <span class="config-card__label">{{ t('view.scheduling.config.totalAgents') }}</span>
                  <span class="config-card__value">{{ totalAgents }}</span>
                </div>
              </div>
              <div class="config-grid">
                <div class="config-item">
                  <div class="config-item__head">
                    <span class="config-item__label">{{ t('view.scheduling.config.windowSize') }}</span>
                    <span class="config-item__value">{{ config.window_size ?? '—' }}</span>
                  </div>
                  <p class="config-item__hint muted">{{ t('view.scheduling.config.windowSizeHint') }}</p>
                </div>
                <div class="config-item">
                  <div class="config-item__head">
                    <span class="config-item__label">{{ t('view.scheduling.config.failureThreshold') }}</span>
                    <span class="config-item__value">{{ formatPercent(config.failure_rate_threshold) }}</span>
                  </div>
                  <p class="config-item__hint muted">{{ t('view.scheduling.config.failureThresholdHint') }}</p>
                </div>
                <div class="config-item">
                  <div class="config-item__head">
                    <span class="config-item__label">{{ t('view.scheduling.config.timeoutThreshold') }}</span>
                    <span class="config-item__value">{{ formatTimeout(config.timeout_threshold) }}</span>
                  </div>
                  <p class="config-item__hint muted">{{ t('view.scheduling.config.timeoutThresholdHint') }}</p>
                </div>
                <div class="config-item">
                  <div class="config-item__head">
                    <span class="config-item__label">{{ t('view.scheduling.config.recoverySuccesses') }}</span>
                    <span class="config-item__value">{{ config.recovery_consecutive_successes ?? '—' }}</span>
                  </div>
                  <p class="config-item__hint muted">{{ t('view.scheduling.config.recoverySuccessesHint') }}</p>
                </div>
              </div>
            </div>
          </Card>
        </div>
      </template>
    </ListPageLayout>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue';
import { useApiStore } from '../stores/api.js';
import { useToast } from '../composables/useToast.js';
import { useConfirm } from '../composables/useConfirm.js';
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
const toast = useToast();
const { showConfirm } = useConfirm();

// ── Tab state ───────────────────────────────────────────────────
const activeTab = ref('agents');
const tabOptions = [
  { value: 'agents', label: t('view.scheduling.tab.agents'), icon: 'activity' },
  { value: 'config', label: t('view.scheduling.tab.config'), icon: 'gear' },
];

// ── Data state ──────────────────────────────────────────────────
const loading = ref(false);
const error = ref(null);

const agents = ref([]);
const config = ref({});
const totalAgents = ref(0);

const resetting = ref(false);

// ── Helpers ─────────────────────────────────────────────────────
function statusLabel(status) {
  if (!status) return t('view.scheduling.status.unknown');
  const key = `view.scheduling.status.${status}`;
  const label = t(key);
  return label === key ? status : label;
}

function statusTone(status) {
  const map = {
    normal: 'success',
    degraded: 'warn',
    drained: 'fail',
    recovering: 'info',
  };
  return map[status] || 'neutral';
}

function failureRatePercent(a) {
  const r = Number(a.failure_rate ?? 0);
  return isNaN(r) ? 0 : r * 100;
}

function formatPercent(rate) {
  if (rate === null || rate === undefined) return '—';
  const n = Number(rate);
  if (isNaN(n)) return String(rate);
  // 如果值 > 1，假设已经是百分比；否则是 0-1 的小数
  const pct = n > 1 ? n : n * 100;
  return `${pct.toFixed(1)}%`;
}

function formatLatency(lat) {
  if (lat === null || lat === undefined) return '—';
  const n = Number(lat);
  if (isNaN(n)) return String(lat);
  if (n < 1000) return `${Math.round(n)}ms`;
  return `${(n / 1000).toFixed(2)}s`;
}

function formatWeight(w) {
  if (w === null || w === undefined) return '—';
  const n = Number(w);
  if (isNaN(n)) return String(w);
  return n.toFixed(2);
}

function formatTimeout(val) {
  if (val === null || val === undefined) return '—';
  const n = Number(val);
  if (isNaN(n)) return String(val);
  return `${n}s`;
}

// ── Config computed ─────────────────────────────────────────────
const hasConfig = computed(() => {
  const c = config.value;
  return c && Object.keys(c).length > 0;
});

// ── Data loading ────────────────────────────────────────────────
async function loadAll() {
  loading.value = true;
  error.value = null;
  try {
    const data = await api.get('/api/scheduling/failure-stats');
    // M-1 fix: 后端返回 { status, data } 统一格式
    const payload = data.data || data;
    agents.value = payload.agents || [];
    config.value = payload.config || {};
    totalAgents.value = payload.total_agents ?? agents.value.length;
  } catch (err) {
    agents.value = [];
    config.value = {};
    totalAgents.value = 0;
    error.value = (err && err.message) || t('view.scheduling.agents.failedLoad');
  } finally {
    loading.value = false;
  }
}

// ── Reset actions ───────────────────────────────────────────────
async function resetAgent(agentId) {
  const ok = await showConfirm({
    message: t('view.scheduling.agents.resetConfirm'),
    tone: 'danger',
  });
  if (!ok) return;
  resetting.value = agentId;
  try {
    await api.post('/api/scheduling/failure-stats/reset', { agent_id: agentId });
    toast.success(t('view.scheduling.agents.resetSuccess'));
    await loadAll();
  } catch (err) {
    toast.error((err && err.message) || t('view.scheduling.agents.resetFailed'));
  } finally {
    resetting.value = false;
  }
}

async function resetAll() {
  const ok = await showConfirm({
    message: t('view.scheduling.agents.resetAllConfirm'),
    tone: 'danger',
  });
  if (!ok) return;
  resetting.value = true;
  try {
    await api.post('/api/scheduling/failure-stats/reset', {});
    toast.success(t('view.scheduling.agents.resetSuccess'));
    await loadAll();
  } catch (err) {
    toast.error((err && err.message) || t('view.scheduling.agents.resetFailed'));
  } finally {
    resetting.value = false;
  }
}

// 暴露 error 给模板（通过 computed 兼容 ListPageLayout 的 error 插槽语义）
// 此处直接用 error ref 在 Card 内部处理三态，不依赖 ListPageLayout 的 error prop。

onMounted(() => {
  loadAll();
});
</script>

<style scoped>
/* ── Layout helpers ────────────────────────────────────────────── */
.blk { display: flex; flex-direction: column; gap: var(--sp-2); }
.muted { color: var(--text-muted); }

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
.icon-btn:disabled { opacity: .5; cursor: not-allowed; }

/* ── Agent table ──────────────────────────────────────────────── */
.agent-table {
  width: 100%;
  border: 1px solid var(--border-subtle);
  border-radius: var(--r-md);
  overflow: hidden;
}
.agent-table__head,
.agent-table__row {
  display: grid;
  grid-template-columns: 1fr auto 90px 90px 80px 64px;
  align-items: center;
  gap: var(--sp-2);
}
.agent-table__head {
  background: var(--surface-2);
  padding: var(--sp-2) var(--sp-3);
  border-bottom: 1px solid var(--border-strong);
}
.agent-table__th {
  font-size: var(--fs-xs);
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: .03em;
  color: var(--text-muted);
}
.agent-table__th--num { text-align: right; }
.agent-table__th--act { text-align: right; }
.agent-table__row {
  padding: var(--sp-3);
  border-bottom: 1px solid var(--border-subtle);
  transition: background var(--motion) var(--ease);
}
.agent-table__row:hover { background: var(--surface-2); }
.agent-table__row:last-child { border-bottom: none; }
.agent-table__td { min-width: 0; }
.agent-table__td--num { text-align: right; font-variant-numeric: tabular-nums; font-family: var(--font-mono); }
.agent-table__td--act { display: flex; justify-content: flex-end; gap: var(--sp-1); }
.agent-table__name { font-weight: 600; color: var(--text); }
.is-warn { color: var(--warn); }
.is-fail { color: var(--fail); }

/* ── Config ───────────────────────────────────────────────────── */
.config-body { display: flex; flex-direction: column; gap: var(--sp-4); }
.config-overview { display: grid; grid-template-columns: 1fr; gap: var(--sp-3); }
.config-card {
  display: flex;
  flex-direction: column;
  gap: var(--sp-1);
  padding: var(--sp-3) var(--sp-4);
  background: var(--surface-2);
  border: 1px solid var(--border-subtle);
  border-radius: var(--r-md);
}
.config-card__label { font-size: var(--fs-xs); text-transform: uppercase; letter-spacing: .03em; color: var(--text-muted); }
.config-card__value { font-size: var(--fs-xl); font-weight: 600; color: var(--text); font-variant-numeric: tabular-nums; }

.config-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: var(--sp-3);
}
.config-item {
  padding: var(--sp-3) var(--sp-4);
  background: var(--surface-2);
  border: 1px solid var(--border-subtle);
  border-radius: var(--r-md);
}
.config-item__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--sp-2);
  margin-bottom: var(--sp-1);
}
.config-item__label { font-size: var(--fs-sm); font-weight: 600; color: var(--text); }
.config-item__value { font-size: var(--fs-lg); font-weight: 600; color: var(--brand-strong); font-variant-numeric: tabular-nums; font-family: var(--font-mono); }
.config-item__hint { font-size: var(--fs-xs); line-height: 1.5; margin: 0; }

/* ── Responsive ───────────────────────────────────────────────── */
@media (max-width: 900px) {
  .config-grid { grid-template-columns: 1fr; }
  .agent-table__head,
  .agent-table__row { grid-template-columns: 1fr auto 90px 64px; }
  .agent-table__th:nth-child(4),
  .agent-table__td:nth-child(4),
  .agent-table__th:nth-child(5),
  .agent-table__td:nth-child(5) { display: none; }
}

@media (max-width: 640px) {
  .agent-table__head,
  .agent-table__row { grid-template-columns: 1fr 64px; }
  .agent-table__th:nth-child(2),
  .agent-table__td:nth-child(2),
  .agent-table__th:nth-child(3),
  .agent-table__td:nth-child(3) { display: none; }
}
</style>
