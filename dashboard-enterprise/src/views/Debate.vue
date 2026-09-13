<template>
  <div class="debate-view">
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
          <span>{{ t('view.debate.history.refresh') }}</span>
        </button>
      </template>
      <template #content>
        <!-- ── History ─────────────────────────────────────────── -->
        <div v-show="activeTab === 'history'">
          <Card :title="t('view.debate.history.title')" icon="activity" margin-bottom="var(--sp-4)">
            <div v-if="loading" class="blk">
              <Skeleton block height="44px" />
              <Skeleton block height="44px" />
              <Skeleton block height="44px" />
            </div>
            <EmptyState
              v-else-if="errors.history"
              icon="alert-triangle"
              tone="fail"
              :title="t('view.debate.history.failedLoad')"
              :description="errors.history"
            />
            <EmptyState
              v-else-if="!verdicts.length"
              icon="activity"
              :title="t('view.debate.history.empty')"
              :description="t('view.debate.history.emptyHint')"
            />
            <div v-else class="deb-table" role="table" :aria-label="t('view.debate.history.title')">
              <div class="deb-table__head" role="row">
                <div class="deb-table__th" role="columnheader">{{ t('view.debate.col.id') }}</div>
                <div class="deb-table__th" role="columnheader">{{ t('view.debate.col.question') }}</div>
                <div class="deb-table__th deb-table__th--num" role="columnheader">{{ t('view.debate.col.rounds') }}</div>
                <div class="deb-table__th deb-table__th--num" role="columnheader">{{ t('view.debate.col.consensus') }}</div>
                <div class="deb-table__th" role="columnheader">{{ t('view.debate.col.verdict') }}</div>
                <div class="deb-table__th deb-table__th--act" role="columnheader">{{ t('view.debate.col.actions') }}</div>
              </div>
              <div v-for="v in verdicts" :key="v.debate_id || v.id" class="deb-table__row" role="row">
                <div class="deb-table__td mono" role="cell">{{ shortId(v.debate_id || v.id) }}</div>
                <div class="deb-table__td" role="cell">
                  <div class="deb-table__question">{{ v.question || '—' }}</div>
                  <div class="deb-table__participants muted">{{ formatParticipants(v.participants) }}</div>
                </div>
                <div class="deb-table__td deb-table__td--num" role="cell">{{ v.rounds_executed ?? v.rounds ?? '—' }}</div>
                <div class="deb-table__td deb-table__td--num" role="cell">{{ formatConsensus(v.consensus_score) }}</div>
                <div class="deb-table__td" role="cell">
                  <Badge :tone="verdictTone(v)">{{ verdictLabel(v) }}</Badge>
                </div>
                <div class="deb-table__td deb-table__td--act" role="cell">
                  <button class="icon-btn" type="button" :aria-label="t('common.details')" @click="openVerdict(v)">
                    <AppIcon name="search" :size="14" />
                  </button>
                </div>
              </div>
            </div>
          </Card>
        </div>

        <!-- ── Start ───────────────────────────────────────────── -->
        <div v-show="activeTab === 'start'">
          <Card :title="t('view.debate.start.title')" icon="sparkles" margin-bottom="var(--sp-4)">
            <p class="form-hint">{{ t('view.debate.start.hint') }}</p>
            <div class="form">
              <label class="field">
                <span class="field__label">{{ t('view.debate.start.question') }}</span>
                <textarea
                  v-model="startForm.question"
                  class="field__input field__input--area"
                  rows="2"
                  :placeholder="t('view.debate.start.questionPlaceholder')"
                ></textarea>
              </label>
              <label class="field">
                <span class="field__label">{{ t('view.debate.start.participants') }}</span>
                <input
                  v-model="startForm.participantsRaw"
                  class="field__input"
                  type="text"
                  :placeholder="t('view.debate.start.participantsPlaceholder')"
                />
                <span class="field__hint">{{ t('view.debate.start.participantsHint') }}</span>
              </label>
              <div class="field-row">
                <label class="field">
                  <span class="field__label">{{ t('view.debate.start.routingKey') }}</span>
                  <input
                    v-model="startForm.routing_key"
                    class="field__input"
                    type="text"
                    :placeholder="t('view.debate.start.routingKeyPlaceholder')"
                  />
                </label>
                <label class="field">
                  <span class="field__label">{{ t('view.debate.start.maxRounds') }}</span>
                  <input
                    v-model.number="startForm.max_rounds"
                    class="field__input"
                    type="number"
                    min="1"
                    max="20"
                  />
                </label>
                <label class="field">
                  <span class="field__label">{{ t('view.debate.start.consensusThreshold') }}</span>
                  <input
                    v-model.number="startForm.consensus_threshold"
                    class="field__input"
                    type="number"
                    min="0"
                    max="1"
                    step="0.05"
                  />
                </label>
              </div>
              <div class="form-actions">
                <button
                  class="btn-primary"
                  type="button"
                  :disabled="starting"
                  @click="startDebate"
                >
                  <AppIcon name="play" :size="14" />
                  <span>{{ starting ? t('view.debate.start.starting') : t('view.debate.start.submit') }}</span>
                </button>
              </div>
            </div>
          </Card>
        </div>

        <!-- ── Config ──────────────────────────────────────────── -->
        <div v-show="activeTab === 'config'">
          <Card :title="t('view.debate.config.title')" icon="gear" margin-bottom="var(--sp-4)">
            <p class="form-hint">{{ t('view.debate.config.hint') }}</p>
            <div v-if="loading && !configLoaded" class="blk">
              <Skeleton block height="44px" />
              <Skeleton block height="44px" />
            </div>
            <div v-else class="form">
              <div class="field-row">
                <label class="field">
                  <span class="field__label">{{ t('view.debate.config.maxRounds') }}</span>
                  <input v-model.number="configForm.max_rounds" class="field__input" type="number" min="1" max="20" />
                </label>
                <label class="field">
                  <span class="field__label">{{ t('view.debate.config.minRounds') }}</span>
                  <input v-model.number="configForm.min_rounds" class="field__input" type="number" min="1" max="20" />
                </label>
                <label class="field">
                  <span class="field__label">{{ t('view.debate.config.consensusThreshold') }}</span>
                  <input v-model.number="configForm.consensus_threshold" class="field__input" type="number" min="0" max="1" step="0.05" />
                </label>
              </div>
              <div class="field-row">
                <label class="field">
                  <span class="field__label">{{ t('view.debate.config.agentTimeout') }}</span>
                  <input v-model.number="configForm.agent_timeout_s" class="field__input" type="number" min="1" step="0.5" />
                </label>
                <label class="field">
                  <span class="field__label">{{ t('view.debate.config.roundTimeout') }}</span>
                  <input v-model.number="configForm.round_timeout_s" class="field__input" type="number" min="1" step="0.5" />
                </label>
                <label class="field">
                  <span class="field__label">{{ t('view.debate.config.maxTokens') }}</span>
                  <input v-model.number="configForm.max_debate_tokens" class="field__input" type="number" min="1000" step="1000" />
                </label>
              </div>
              <div class="field-row">
                <label class="field field--inline">
                  <input v-model="configForm.early_exit_on_unanimous" type="checkbox" />
                  <span>{{ t('view.debate.config.earlyExit') }}</span>
                </label>
                <label class="field">
                  <span class="field__label">{{ t('view.debate.config.retentionDays') }}</span>
                  <input v-model.number="configForm.retention_days" class="field__input" type="number" min="1" />
                </label>
              </div>
              <div v-if="errors.config" class="form-error">{{ errors.config }}</div>
              <div class="form-actions">
                <button
                  class="btn-primary"
                  type="button"
                  :disabled="configSaving"
                  @click="saveConfig"
                >
                  <AppIcon name="check" :size="14" />
                  <span>{{ configSaving ? t('view.debate.config.saving') : t('view.debate.config.save') }}</span>
                </button>
              </div>
            </div>
          </Card>
        </div>
      </template>
    </ListPageLayout>

    <!-- ── Verdict Detail Drawer ──────────────────────────────── -->
    <DetailDrawer :open="showDetail" :title="t('view.debate.detail.title')" icon="activity" @close="closeDetail">
      <div v-if="detailVerdict" class="detail-content">
        <section class="detail-section">
          <h4 class="detail-section__title">{{ t('view.debate.detail.question') }}</h4>
          <p class="detail-value">{{ detailVerdict.question || '—' }}</p>
        </section>
        <section class="detail-section">
          <h4 class="detail-section__title">{{ t('view.debate.detail.verdict') }}</h4>
          <p class="detail-value">
            <Badge :tone="verdictTone(detailVerdict)">{{ verdictLabel(detailVerdict) }}</Badge>
          </p>
          <h4 class="detail-section__title">{{ t('view.debate.detail.consensus') }}</h4>
          <p class="detail-value">{{ formatConsensus(detailVerdict.consensus_score) }}</p>
          <h4 class="detail-section__title">{{ t('view.debate.detail.rounds') }}</h4>
          <p class="detail-value">{{ detailVerdict.rounds_executed ?? detailVerdict.rounds ?? '—' }}</p>
          <h4 class="detail-section__title">{{ t('view.debate.detail.participants') }}</h4>
          <p class="detail-value">{{ formatParticipants(detailVerdict.participants) }}</p>
        </section>
        <section class="detail-section">
          <h4 class="detail-section__title">{{ t('view.debate.detail.trajectory') }}</h4>
          <div v-if="trajectory.length" class="trajectory">
            <div v-for="(turn, i) in trajectory" :key="i" class="trajectory__item">
              <div class="trajectory__head">
                <span class="trajectory__round">{{ t('view.debate.detail.round', { n: turn.round ?? (i + 1) }) }}</span>
                <span class="trajectory__speaker">{{ turn.speaker || turn.agent || '—' }}</span>
                <Badge v-if="turn.stance" :tone="stanceTone(turn.stance)">{{ stanceLabel(turn.stance) }}</Badge>
              </div>
              <p v-if="turn.argument || turn.message" class="trajectory__arg">{{ turn.argument || turn.message }}</p>
            </div>
          </div>
          <p v-else class="muted">{{ t('view.debate.detail.emptyTrajectory') }}</p>
        </section>
      </div>
    </DetailDrawer>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue';
import { useApiStore } from '../stores/api.js';
import { useToast } from '../composables/useToast.js';
import { useI18n } from '../i18n';
import ListPageLayout from '../components/ListPageLayout.vue';
import AppIcon from '../components/AppIcon.vue';
import Card from '../components/Card.vue';
import Badge from '../components/Badge.vue';
import Segmented from '../components/Segmented.vue';
import Skeleton from '../components/Skeleton.vue';
import EmptyState from '../components/EmptyState.vue';
import DetailDrawer from '../components/DetailDrawer.vue';

const api = useApiStore();
const { t } = useI18n();
const toast = useToast();

// ── Tab state ───────────────────────────────────────────────────
const activeTab = ref('history');
const tabOptions = [
  { value: 'history', label: t('view.debate.tab.history'), icon: 'activity' },
  { value: 'start', label: t('view.debate.tab.start'), icon: 'sparkles' },
  { value: 'config', label: t('view.debate.tab.config'), icon: 'gear' },
];

// ── Data state ──────────────────────────────────────────────────
const loading = ref(false);
const errors = ref({ history: null, config: null });

const verdicts = ref([]);
const configLoaded = ref(false);

// ── Start form ──────────────────────────────────────────────────
const starting = ref(false);
const startForm = reactive({
  question: '',
  participantsRaw: '',
  routing_key: '',
  max_rounds: 3,
  consensus_threshold: 0.7,
});

function parseParticipants(raw) {
  return String(raw || '')
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean);
}

async function startDebate() {
  const question = startForm.question.trim();
  if (!question) {
    toast.warn(t('view.debate.validate.questionRequired'));
    return;
  }
  const participants = parseParticipants(startForm.participantsRaw);
  if (participants.length < 3) {
    toast.warn(t('view.debate.validate.participantsRequired'));
    return;
  }
  const threshold = Number(startForm.consensus_threshold);
  if (isNaN(threshold) || threshold < 0 || threshold > 1) {
    toast.warn(t('view.debate.validate.consensusRange'));
    return;
  }
  starting.value = true;
  try {
    const payload = {
      question,
      participants,
      context: {},
      routing_key: startForm.routing_key.trim(),
      max_rounds: Number(startForm.max_rounds) || 3,
      consensus_threshold: threshold,
    };
    const data = await api.post('/api/debate/start', payload);
    toast.success(t('view.debate.start.success'));
    // Prepend the new verdict to the history list (best-effort)
    const newVerdict = data && data.verdict ? data.verdict : null;
    if (newVerdict) {
      verdicts.value = [newVerdict, ...verdicts.value];
    }
    // Reset question field for next debate, keep participants
    startForm.question = '';
    // Switch to history tab to show the result
    activeTab.value = 'history';
  } catch (err) {
    toast.error((err && err.message) || t('view.debate.start.failed'));
  } finally {
    starting.value = false;
  }
}

// ── Config form ─────────────────────────────────────────────────
const configSaving = ref(false);
const configForm = reactive({
  max_rounds: 3,
  min_rounds: 1,
  consensus_threshold: 0.7,
  agent_timeout_s: 60,
  round_timeout_s: 120,
  max_debate_tokens: 50000,
  early_exit_on_unanimous: true,
  retention_days: 30,
});

async function saveConfig() {
  configSaving.value = true;
  errors.value = { ...errors.value, config: null };
  try {
    const payload = {
      max_rounds: Number(configForm.max_rounds) || 3,
      min_rounds: Number(configForm.min_rounds) || 1,
      consensus_threshold: Number(configForm.consensus_threshold) || 0.7,
      agent_timeout_s: Number(configForm.agent_timeout_s) || 60,
      round_timeout_s: Number(configForm.round_timeout_s) || 120,
      max_debate_tokens: Number(configForm.max_debate_tokens) || 50000,
      early_exit_on_unanimous: !!configForm.early_exit_on_unanimous,
      retention_days: Number(configForm.retention_days) || 30,
    };
    await api.post('/api/debate/config', payload);
    toast.success(t('view.debate.config.saved'));
  } catch (err) {
    const msg = (err && err.message) || t('view.debate.config.saveFailed');
    errors.value = { ...errors.value, config: msg };
    toast.error(msg);
  } finally {
    configSaving.value = false;
  }
}

// ── Verdict detail drawer ───────────────────────────────────────
const showDetail = ref(false);
const detailVerdict = ref(null);

const trajectory = computed(() => {
  const v = detailVerdict.value;
  if (!v) return [];
  // Support multiple shape aliases: trajectory, turns, rounds
  if (Array.isArray(v.trajectory)) return v.trajectory;
  if (Array.isArray(v.turns)) return v.turns;
  if (Array.isArray(v.rounds)) return v.rounds;
  return [];
});

function openVerdict(v) {
  // Use the already-loaded verdict summary; fetch full detail for trajectory.
  detailVerdict.value = v;
  showDetail.value = true;
  const id = v.debate_id || v.id;
  if (!id) return;
  // Best-effort fetch of full verdict (includes trajectory)
  api.get(`/api/debate/${encodeURIComponent(id)}`)
    .then((data) => {
      if (data && data.verdict) {
        detailVerdict.value = { ...detailVerdict.value, ...data.verdict };
      }
    })
    .catch(() => { /* keep summary on failure */ });
}

function closeDetail() {
  showDetail.value = false;
  detailVerdict.value = null;
}

// ── Helpers ─────────────────────────────────────────────────────
function shortId(id) {
  if (!id) return '—';
  const s = String(id);
  return s.length > 12 ? s.slice(0, 8) + '…' : s;
}

function formatParticipants(p) {
  if (!p) return '';
  if (Array.isArray(p)) return p.join(', ');
  if (typeof p === 'string') return p;
  try { return JSON.stringify(p); } catch { return String(p); }
}

function formatConsensus(score) {
  if (score == null) return '—';
  const n = Number(score);
  if (isNaN(n)) return '—';
  // Accept either 0-1 or 0-100; normalize to percentage
  const pct = n <= 1 ? n * 100 : n;
  return `${pct.toFixed(1)}%`;
}

function verdictLabel(v) {
  const raw = String(v.verdict || v.status || v.outcome || '').toLowerCase();
  if (/(agree|consensus|unanimous|yes|positive|ok)/.test(raw)) return t('view.debate.verdict.consensus');
  if (/(disagree|split|no|negative|fail)/.test(raw)) return t('view.debate.verdict.split');
  if (/(uncertain|inconclusive|unknown)/.test(raw)) return t('view.debate.verdict.uncertain');
  // Fall back to consensus score heuristic
  const score = Number(v.consensus_score);
  if (!isNaN(score)) {
    const pct = score <= 1 ? score : score / 100;
    if (pct >= 0.7) return t('view.debate.verdict.consensus');
    if (pct >= 0.4) return t('view.debate.verdict.uncertain');
    return t('view.debate.verdict.split');
  }
  return t('view.debate.verdict.unknown');
}

function verdictTone(v) {
  const label = verdictLabel(v);
  if (label === t('view.debate.verdict.consensus')) return 'success';
  if (label === t('view.debate.verdict.split')) return 'fail';
  if (label === t('view.debate.verdict.uncertain')) return 'warn';
  return 'neutral';
}

function stanceLabel(stance) {
  const s = String(stance || '').toLowerCase();
  if (/(agree|support|pro|yes|positive)/.test(s)) return t('view.debate.verdict.agree');
  if (/(disagree|oppose|con|no|negative)/.test(s)) return t('view.debate.verdict.disagree');
  return t('view.debate.verdict.uncertain');
}

function stanceTone(stance) {
  const s = String(stance || '').toLowerCase();
  if (/(agree|support|pro|yes|positive)/.test(s)) return 'success';
  if (/(disagree|oppose|con|no|negative)/.test(s)) return 'fail';
  return 'warn';
}

// ── Data loading ────────────────────────────────────────────────
async function loadHistory() {
  try {
    const data = await api.get('/api/debate/history?limit=20');
    verdicts.value = data.verdicts || [];
    errors.value = { ...errors.value, history: null };
  } catch (err) {
    verdicts.value = [];
    errors.value = { ...errors.value, history: (err && err.message) || t('view.debate.history.failedLoad') };
  }
}

async function loadAll() {
  loading.value = true;
  await Promise.allSettled([loadHistory()]);
  // Config is loaded lazily when the config tab is first opened
  loading.value = false;
}

// Load config the first time the config tab is shown
import { watch } from 'vue';
watch(activeTab, async (tab) => {
  if (tab === 'config' && !configLoaded.value) {
    configLoaded.value = true;
    // Best-effort: the API only exposes POST /config (write).
    // There is no GET /config endpoint, so we keep defaults.
    // If a GET endpoint is added in the future, load it here.
  }
});

onMounted(() => {
  loadAll();
});
</script>

<style scoped>
/* ── Layout helpers ────────────────────────────────────────────── */
.blk { display: flex; flex-direction: column; gap: var(--sp-2); }
.muted { color: var(--text-muted); }
.mono { font-family: var(--font-mono); font-size: var(--fs-xs); }

/* ── History table ─────────────────────────────────────────────── */
.deb-table {
  width: 100%;
  border: 1px solid var(--border-subtle);
  border-radius: var(--r-md);
  overflow: hidden;
}
.deb-table__head,
.deb-table__row {
  display: grid;
  grid-template-columns: 110px 1fr 70px 90px 120px 72px;
  align-items: center;
  gap: var(--sp-2);
}
.deb-table__head {
  background: var(--surface-2);
  padding: var(--sp-2) var(--sp-3);
  border-bottom: 1px solid var(--border-strong);
}
.deb-table__th {
  font-size: var(--fs-xs);
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: .03em;
  color: var(--text-muted);
}
.deb-table__th--num { text-align: right; }
.deb-table__th--act { text-align: right; }
.deb-table__row {
  padding: var(--sp-3);
  border-bottom: 1px solid var(--border-subtle);
  transition: background var(--motion) var(--ease);
}
.deb-table__row:last-child { border-bottom: none; }
.deb-table__row:hover { background: var(--surface-2); }
.deb-table__td { min-width: 0; }
.deb-table__td--num { text-align: right; font-variant-numeric: tabular-nums; }
.deb-table__td--act { display: flex; gap: 6px; justify-content: flex-end; }
.deb-table__question {
  font-weight: 600;
  color: var(--text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.deb-table__participants { font-size: var(--fs-xs); margin-top: 2px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

/* ── Buttons ───────────────────────────────────────────────────── */
/* NOTE: .btn-ghost/.btn-primary/.modal 等为多 view 共享样式，重复定义见
   AgentBilling/AgentGateway/AgentProxy/AgentRegistry/Analysis/Blackboard/Feedback。
   全局基础样式见 src/styles/pages.css（BEM 命名 .btn--ghost），此处 scoped 隔离不冲突。 */
.btn-ghost {
  display: inline-flex; align-items: center; gap: 6px;
  background: var(--surface-2); color: var(--text);
  border: 1px solid var(--border); border-radius: var(--r-md);
  padding: var(--sp-2) var(--sp-3); font-size: var(--fs-sm); font-weight: 600;
  cursor: pointer; transition: opacity var(--motion) var(--ease);
}
.btn-ghost:hover { opacity: .9; }
.btn-ghost:disabled, .btn-ghost.is-busy { opacity: .6; cursor: not-allowed; }
.btn-primary {
  display: inline-flex; align-items: center; gap: 6px;
  background: var(--brand); color: var(--brand-contrast);
  border: none; border-radius: var(--r-md);
  padding: var(--sp-2) var(--sp-4); font-size: var(--fs-sm); font-weight: 600;
  cursor: pointer; transition: opacity var(--motion) var(--ease);
}
.btn-primary:hover { opacity: .92; }
.btn-primary:disabled { opacity: .5; cursor: not-allowed; }
.icon-btn {
  display: grid; place-items: center;
  width: 28px; height: 28px;
  background: var(--surface-2); border: 1px solid var(--border);
  border-radius: var(--r-sm); color: var(--text-muted); cursor: pointer;
  transition: color var(--motion) var(--ease), border-color var(--motion) var(--ease);
}
.icon-btn:hover { color: var(--text); border-color: var(--border-strong); }

/* ── Form ──────────────────────────────────────────────────────── */
.form { display: flex; flex-direction: column; gap: var(--sp-3); }
.form-hint { font-size: var(--fs-sm); color: var(--text-muted); margin: 0 0 var(--sp-2); line-height: 1.5; }
.field { display: flex; flex-direction: column; gap: var(--sp-1); font-size: var(--fs-sm); font-weight: 600; color: var(--text-muted); }
.field--inline { flex-direction: row; align-items: center; gap: 6px; }
.field__label { color: var(--text-muted); }
.field__hint { font-size: var(--fs-xs); font-weight: 400; color: var(--text-faint); }
.field__input {
  font-family: inherit; font-size: var(--fs-base);
  background: var(--surface-2); color: var(--text);
  border: 1px solid var(--border); border-radius: var(--r-md);
  padding: 7px 10px; width: 100%;
  transition: border-color var(--motion) var(--ease);
}
.field__input:focus { outline: none; border-color: var(--brand); box-shadow: 0 0 0 3px var(--brand-soft); }
.field__input--area { resize: vertical; min-height: 60px; }
.field-row { display: grid; grid-template-columns: repeat(3, 1fr); gap: var(--sp-3); }
.form-actions { display: flex; justify-content: flex-end; gap: var(--sp-2); margin-top: var(--sp-2); }
.form-error { color: var(--fail); font-size: var(--fs-sm); }

/* ── Detail drawer ─────────────────────────────────────────────── */
.detail-content { display: flex; flex-direction: column; gap: var(--sp-4); }
.detail-section { display: flex; flex-direction: column; gap: 6px; }
.detail-section__title { font-size: var(--fs-sm); font-weight: 700; color: var(--text); margin: var(--sp-2) 0 var(--sp-1); text-transform: uppercase; letter-spacing: .04em; }
.detail-value { font-size: var(--fs-base); color: var(--text); margin: 0 0 var(--sp-2); word-break: break-word; }

/* ── Trajectory ────────────────────────────────────────────────── */
.trajectory { display: flex; flex-direction: column; gap: var(--sp-3); }
.trajectory__item {
  padding: var(--sp-3);
  background: var(--surface-2);
  border: 1px solid var(--border-subtle);
  border-radius: var(--r-md);
}
.trajectory__head { display: flex; align-items: center; gap: var(--sp-2); flex-wrap: wrap; margin-bottom: var(--sp-1); }
.trajectory__round { font-size: var(--fs-xs); font-weight: 700; color: var(--brand-strong); text-transform: uppercase; letter-spacing: .04em; }
.trajectory__speaker { font-size: var(--fs-sm); font-weight: 600; color: var(--text); }
.trajectory__arg { font-size: var(--fs-sm); color: var(--text); margin: var(--sp-1) 0 0; line-height: 1.55; }

/* ── Responsive ────────────────────────────────────────────────── */
@media (max-width: 900px) {
  .deb-table__head,
  .deb-table__row {
    grid-template-columns: 90px 1fr 80px 100px 64px;
  }
  .deb-table__th:nth-child(3),
  .deb-table__td:nth-child(3) { display: none; }
  .field-row { grid-template-columns: 1fr 1fr; }
}
@media (max-width: 640px) {
  .deb-table__head,
  .deb-table__row {
    grid-template-columns: 1fr 100px 64px;
  }
  .deb-table__th:nth-child(2),
  .deb-table__td:nth-child(2),
  .deb-table__th:nth-child(4),
  .deb-table__td:nth-child(4) { display: none; }
  .field-row { grid-template-columns: 1fr; }
}
</style>
