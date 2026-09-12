<template>
  <div class="integrations-view">
    <ListPageLayout>
      <template #badges>
        <Badge tone="brand">{{ t('view.webhooks.enterprise') }}</Badge>
      </template>
      <template #actions>
        <Segmented
          :model-value="activeTab"
          :options="tabOptions"
          size="sm"
          @update:model-value="activeTab = $event"
        />
        <button class="btn-ghost" :class="{ 'is-busy': loading }" :disabled="loading" @click="loadAll">
          <AppIcon name="refresh" :size="15" />
          <span>{{ t('view.integrations.workflows.refresh') }}</span>
        </button>
      </template>
      <template #content>
        <!-- ── Workflows ───────────────────────────────────────── -->
        <div v-show="activeTab === 'workflows'">
          <Card :title="t('view.integrations.workflows.title')" icon="route" margin-bottom="var(--sp-4)">
            <div v-if="loading" class="blk">
              <Skeleton block height="44px" />
              <Skeleton block height="44px" />
              <Skeleton block height="44px" />
            </div>
            <EmptyState
              v-else-if="errors.workflows"
              icon="alert-triangle"
              tone="fail"
              :title="t('view.integrations.workflows.failedLoad')"
              :description="errors.workflows"
            />
            <EmptyState
              v-else-if="!workflows.length"
              icon="route"
              :title="t('view.integrations.workflows.empty')"
              :description="t('view.integrations.workflows.emptyHint')"
            />
            <div v-else class="wf-table" role="table" :aria-label="t('view.integrations.workflows.title')">
              <div class="wf-table__head" role="row">
                <div class="wf-table__th" role="columnheader">{{ t('view.integrations.col.name') }}</div>
                <div class="wf-table__th" role="columnheader">{{ t('view.integrations.col.active') }}</div>
                <div class="wf-table__th wf-table__th--num" role="columnheader">{{ t('view.integrations.col.nodes') }}</div>
                <div class="wf-table__th" role="columnheader">{{ t('view.integrations.col.lastExec') }}</div>
                <div class="wf-table__th wf-table__th--act" role="columnheader">{{ t('view.integrations.col.actions') }}</div>
              </div>
              <div v-for="wf in workflows" :key="wf.id" class="wf-table__row" role="row">
                <div class="wf-table__td" role="cell">
                  <div class="wf-table__name">{{ wf.name || '—' }}</div>
                  <div class="wf-table__id mono">{{ wf.id }}</div>
                </div>
                <div class="wf-table__td" role="cell">
                  <Badge :tone="wf.active || wf.enabled ? 'success' : 'neutral'">
                    {{ wf.active || wf.enabled ? t('common.on') : t('common.off') }}
                  </Badge>
                </div>
                <div class="wf-table__td wf-table__td--num" role="cell">{{ wf.nodes ?? (wf.node_count ?? 0) }}</div>
                <div class="wf-table__td" role="cell">
                  <span class="wf-table__time">{{ formatRel(wf.last_execution_started_at || wf.lastExecutedAt || wf.updated_at) }}</span>
                </div>
                <div class="wf-table__td wf-table__td--act" role="cell">
                  <button
                    class="icon-btn"
                    type="button"
                    :aria-label="t('view.integrations.workflows.trigger')"
                    :disabled="triggeringId === wf.id"
                    @click="openTrigger(wf)"
                  >
                    <AppIcon name="play" :size="14" />
                  </button>
                </div>
              </div>
            </div>
          </Card>

          <!-- Stats overview -->
          <Card :title="t('view.integrations.stats.title')" icon="activity" margin-bottom="var(--sp-4)">
            <EmptyState
              v-if="errors.workflows"
              icon="alert-triangle"
              tone="fail"
              :title="t('view.integrations.workflows.failedLoad')"
              :description="errors.workflows"
            />
            <EmptyState
              v-else-if="!workflows.length"
              icon="activity"
              :title="t('view.integrations.stats.empty')"
            />
            <div v-else class="stats-grid">
              <div class="stats-card">
                <span class="stats-card__label">{{ t('view.integrations.stats.totalWorkflows') }}</span>
                <span class="stats-card__value">{{ workflows.length }}</span>
              </div>
              <div class="stats-card">
                <span class="stats-card__label">{{ t('view.integrations.stats.activeWorkflows') }}</span>
                <span class="stats-card__value">{{ activeWorkflowCount }}</span>
              </div>
              <div class="stats-card">
                <span class="stats-card__label">{{ t('view.integrations.stats.recentExecutions') }}</span>
                <span class="stats-card__value">{{ executions.length }}</span>
              </div>
              <div class="stats-card">
                <span class="stats-card__label">{{ t('view.integrations.stats.successRate') }}</span>
                <span class="stats-card__value" :class="successRateTone">{{ successRate }}%</span>
              </div>
            </div>
          </Card>
        </div>

        <!-- ── Executions ──────────────────────────────────────── -->
        <div v-show="activeTab === 'executions'">
          <Card :title="t('view.integrations.executions.title')" icon="clock" margin-bottom="var(--sp-4)">
            <template #actions>
              <div class="exec-lookup">
                <input
                  v-model="execLookupId"
                  class="field__input exec-lookup__input"
                  type="text"
                  :placeholder="t('view.integrations.executions.idPlaceholder')"
                  @keydown.enter="lookupExecution"
                />
                <button class="btn-ghost" type="button" :disabled="execLoading" @click="lookupExecution">
                  <AppIcon name="search" :size="14" />
                  <span>{{ t('view.integrations.executions.lookup') }}</span>
                </button>
              </div>
            </template>
            <EmptyState
              v-if="execError"
              icon="alert-triangle"
              tone="fail"
              :title="t('view.integrations.executions.failedLoad')"
              :description="execError"
            />
            <EmptyState
              v-else-if="!executions.length"
              icon="clock"
              :title="t('view.integrations.executions.empty')"
              :description="t('view.integrations.executions.emptyHint')"
            />
            <div v-else class="exec-table" role="table" :aria-label="t('view.integrations.executions.title')">
              <div class="exec-table__head" role="row">
                <div class="exec-table__th" role="columnheader">{{ t('view.integrations.executions.col.id') }}</div>
                <div class="exec-table__th" role="columnheader">{{ t('view.integrations.executions.col.status') }}</div>
                <div class="exec-table__th" role="columnheader">{{ t('view.integrations.executions.col.started') }}</div>
                <div class="exec-table__th" role="columnheader">{{ t('view.integrations.executions.col.mode') }}</div>
              </div>
              <div v-for="ex in executions" :key="ex.id" class="exec-table__row" role="row">
                <div class="exec-table__td mono" role="cell">{{ ex.id }}</div>
                <div class="exec-table__td" role="cell">
                  <Badge :tone="execStatusTone(ex)">{{ execStatusLabel(ex) }}</Badge>
                </div>
                <div class="exec-table__td" role="cell">
                  <span class="exec-table__time">{{ formatRel(ex.started_at || ex.started) }}</span>
                </div>
                <div class="exec-table__td" role="cell">{{ ex.mode || '—' }}</div>
              </div>
            </div>
          </Card>
        </div>

        <!-- ── Health ──────────────────────────────────────────── -->
        <div v-show="activeTab === 'health'">
          <Card :title="t('view.integrations.health.title')" icon="shield" margin-bottom="var(--sp-4)">
            <p class="form-hint">{{ t('view.integrations.health.hint') }}</p>
            <div class="health-body">
              <button
                class="btn-primary"
                type="button"
                :disabled="healthChecking"
                @click="runHealthCheck"
              >
                <AppIcon name="activity" :size="14" />
                <span>{{ healthChecking ? t('view.integrations.health.checking') : t('view.integrations.health.check') }}</span>
              </button>
              <div v-if="healthError" class="form-error">{{ healthError }}</div>
              <div v-if="healthResult" class="health-result">
                <div class="health-row">
                  <span class="health-row__label">{{ t('view.integrations.health.baseUrl') }}</span>
                  <code class="mono">{{ healthResult.base_url || '—' }}</code>
                </div>
                <div class="health-row">
                  <span class="health-row__label">{{ t('common.status') }}</span>
                  <Badge :tone="healthResult.n8n_reachable ? 'success' : 'fail'">
                    {{ healthResult.n8n_reachable ? t('view.integrations.health.reachable') : t('view.integrations.health.unreachable') }}
                  </Badge>
                </div>
                <div class="health-row">
                  <span class="health-row__label">{{ t('view.integrations.health.lastCheck') }}</span>
                  <span class="muted">{{ formatRel(healthCheckedAt) }}</span>
                </div>
              </div>
            </div>
          </Card>

          <!-- Webhook endpoint info -->
          <Card :title="t('view.integrations.webhook.title')" icon="link" margin-bottom="var(--sp-4)">
            <p class="form-hint">{{ t('view.integrations.webhook.hint') }}</p>
            <div class="webhook-info">
              <div class="health-row">
                <span class="health-row__label">{{ t('view.integrations.webhook.url') }}</span>
                <code class="mono">/api/n8n/webhook</code>
              </div>
              <div class="health-row">
                <span class="health-row__label">{{ t('view.integrations.webhook.method') }}</span>
                <Badge tone="info">POST</Badge>
              </div>
              <div class="health-row">
                <span class="health-row__label">{{ t('view.integrations.webhook.signature') }}</span>
                <code class="mono">X-N8N-Signature</code>
              </div>
            </div>
          </Card>
        </div>
      </template>
    </ListPageLayout>

    <!-- ── Trigger Workflow Modal ─────────────────────────────── -->
    <Teleport to="body">
      <div
        v-if="showTriggerModal"
        v-modal-a11y
        class="modal-overlay"
        @click.self="closeTrigger"
        @modal:escape="closeTrigger"
      >
        <div class="modal" role="dialog" aria-modal="true">
          <div class="modal__head">
            <h3>{{ t('view.integrations.trigger.title') }}</h3>
            <button class="modal__x" type="button" :aria-label="t('common.close')" @click="closeTrigger">
              <AppIcon name="x" :size="16" />
            </button>
          </div>
          <div class="modal__body">
            <div class="field">
              <span class="field__label">{{ t('view.integrations.trigger.workflow') }}</span>
              <div class="trigger-wf">
                <code class="mono">{{ triggerWorkflow && triggerWorkflow.id }}</code>
                <span class="trigger-wf__name">{{ triggerWorkflow && triggerWorkflow.name }}</span>
              </div>
            </div>
            <label class="field">
              <span class="field__label">{{ t('view.integrations.trigger.data') }}</span>
              <textarea
                v-model="triggerForm.dataRaw"
                class="field__input field__input--area"
                rows="4"
                :placeholder="t('view.integrations.trigger.dataPlaceholder')"
              ></textarea>
            </label>
            <label class="field field--inline">
              <input v-model="triggerForm.wait" type="checkbox" />
              <span>{{ t('view.integrations.trigger.wait') }}</span>
            </label>
            <div v-if="triggerError" class="form-error">{{ triggerError }}</div>
          </div>
          <div class="modal__foot">
            <button class="btn-ghost" type="button" @click="closeTrigger">{{ t('view.integrations.trigger.cancel') }}</button>
            <button class="btn-primary" type="button" :disabled="triggering" @click="submitTrigger">
              <AppIcon name="play" :size="14" />
              <span>{{ triggering ? t('view.integrations.workflows.triggering') : t('view.integrations.trigger.submit') }}</span>
            </button>
          </div>
        </div>
      </div>
    </Teleport>
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

const api = useApiStore();
const { t } = useI18n();
const toast = useToast();

// ── Tab state ───────────────────────────────────────────────────
const activeTab = ref('workflows');
const tabOptions = [
  { value: 'workflows', label: t('view.integrations.tab.workflows'), icon: 'route' },
  { value: 'executions', label: t('view.integrations.tab.executions'), icon: 'clock' },
  { value: 'health', label: t('view.integrations.tab.health'), icon: 'shield' },
];

// ── Data state ──────────────────────────────────────────────────
const loading = ref(false);
const errors = ref({ workflows: null });

const workflows = ref([]);
const executions = ref([]);

// ── Health ──────────────────────────────────────────────────────
const healthChecking = ref(false);
const healthResult = ref(null);
const healthError = ref('');
const healthCheckedAt = ref('');

async function runHealthCheck() {
  healthChecking.value = true;
  healthError.value = '';
  try {
    const data = await api.get('/api/n8n/health');
    healthResult.value = {
      n8n_reachable: !!data.n8n_reachable,
      base_url: data.base_url || '',
    };
    healthCheckedAt.value = new Date().toISOString();
  } catch (err) {
    healthError.value = (err && err.message) || t('view.integrations.health.failed');
  } finally {
    healthChecking.value = false;
  }
}

// ── Trigger modal ───────────────────────────────────────────────
const showTriggerModal = ref(false);
const triggering = ref(false);
const triggeringId = ref('');
const triggerWorkflow = ref(null);
const triggerError = ref('');
const triggerForm = reactive({
  dataRaw: '',
  wait: false,
});

function openTrigger(wf) {
  triggerWorkflow.value = wf;
  triggerForm.dataRaw = '';
  triggerForm.wait = false;
  triggerError.value = '';
  showTriggerModal.value = true;
}

function closeTrigger() {
  showTriggerModal.value = false;
  triggerWorkflow.value = null;
}

function parseTriggerData(raw) {
  const s = String(raw || '').trim();
  if (!s) return {};
  try { return JSON.parse(s); } catch { return null; }
}

async function submitTrigger() {
  const wf = triggerWorkflow.value;
  if (!wf || !wf.id) return;
  triggerError.value = '';
  const data = parseTriggerData(triggerForm.dataRaw);
  if (data === null) {
    triggerError.value = t('view.integrations.trigger.validateData');
    return;
  }
  triggering.value = true;
  triggeringId.value = wf.id;
  try {
    const payload = { data, wait: !!triggerForm.wait };
    const result = await api.post(`/api/n8n/workflows/${encodeURIComponent(wf.id)}/trigger`, payload);
    const execId = result && (result.execution_id || result.id);
    toast.success(t('view.integrations.workflows.triggerSuccess', { id: execId || '—' }));
    showTriggerModal.value = false;
    // Prepend execution to the list
    if (execId) {
      executions.value = [
        {
          id: execId,
          status: result.status || 'running',
          started_at: result.started_at || new Date().toISOString(),
          mode: result.mode || 'manual',
          workflow_id: wf.id,
          workflow_name: wf.name,
        },
        ...executions.value,
      ];
    }
  } catch (err) {
    triggerError.value = (err && err.message) || t('view.integrations.workflows.triggerFailed');
  } finally {
    triggering.value = false;
    triggeringId.value = '';
  }
}

// ── Execution lookup ────────────────────────────────────────────
const execLookupId = ref('');
const execLoading = ref(false);
const execError = ref('');

async function lookupExecution() {
  const id = execLookupId.value.trim();
  if (!id) return;
  execLoading.value = true;
  execError.value = '';
  try {
    const data = await api.get(`/api/n8n/executions/${encodeURIComponent(id)}`);
    const ex = {
      id: data.id || id,
      status: data.status || 'unknown',
      started_at: data.started_at || data.started,
      mode: data.mode || '—',
      workflow_id: data.workflow_id || data.workflowId,
    };
    // Prepend or replace
    const idx = executions.value.findIndex((e) => String(e.id) === String(ex.id));
    if (idx >= 0) executions.value[idx] = ex;
    else executions.value = [ex, ...executions.value];
  } catch (err) {
    execError.value = (err && err.message) || t('view.integrations.executions.failedLoad');
  } finally {
    execLoading.value = false;
  }
}

// ── Stats helpers ───────────────────────────────────────────────
const activeWorkflowCount = computed(() => workflows.value.filter((w) => w.active || w.enabled).length);

const successRate = computed(() => {
  if (!executions.value.length) return '—';
  const ok = executions.value.filter((e) => {
    const s = String(e.status || '').toLowerCase();
    return /(success|ok|done|completed)/.test(s);
  }).length;
  return ((ok / executions.value.length) * 100).toFixed(1);
});

const successRateTone = computed(() => {
  const v = parseFloat(successRate.value);
  if (isNaN(v)) return '';
  if (v >= 95) return 'is-ok';
  if (v >= 80) return 'is-warn';
  return 'is-fail';
});

// ── Helpers ─────────────────────────────────────────────────────
function execStatusLabel(ex) {
  const s = String(ex.status || '').toLowerCase();
  if (/(running|active|processing)/.test(s)) return t('view.integrations.exec.status.running');
  if (/(success|ok|done|completed)/.test(s)) return t('view.integrations.exec.status.success');
  if (/(fail|error|crash)/.test(s)) return t('view.integrations.exec.status.failed');
  if (/(wait|pending|idle)/.test(s)) return t('view.integrations.exec.status.waiting');
  return t('view.integrations.exec.status.unknown');
}

function execStatusTone(ex) {
  const s = String(ex.status || '').toLowerCase();
  if (/(running|active|processing)/.test(s)) return 'info';
  if (/(success|ok|done|completed)/.test(s)) return 'success';
  if (/(fail|error|crash)/.test(s)) return 'fail';
  if (/(wait|pending|idle)/.test(s)) return 'warn';
  return 'neutral';
}

function formatRel(ts) {
  if (ts === null || ts === undefined || ts === '') return '—';
  const d = new Date(typeof ts === 'number' ? ts : String(ts));
  if (isNaN(d.getTime())) return String(ts);
  const diff = Math.max(0, Math.floor((Date.now() - d.getTime()) / 1000));
  if (diff < 60) return t('common.secondsAgo', { n: diff });
  if (diff < 3600) return t('common.minutesAgo', { n: Math.floor(diff / 60) });
  if (diff < 86400) return t('common.hoursAgo', { n: Math.floor(diff / 3600) });
  return t('common.daysAgo', { n: Math.floor(diff / 86400) });
}

// ── Data loading ────────────────────────────────────────────────
async function loadWorkflows() {
  try {
    const data = await api.get('/api/n8n/workflows');
    workflows.value = data.workflows || [];
    errors.value = { ...errors.value, workflows: null };
  } catch (err) {
    workflows.value = [];
    errors.value = { ...errors.value, workflows: (err && err.message) || t('view.integrations.workflows.failedLoad') };
  }
}

async function loadAll() {
  loading.value = true;
  await Promise.allSettled([loadWorkflows()]);
  loading.value = false;
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

/* ── Workflow table ────────────────────────────────────────────── */
.wf-table {
  width: 100%;
  border: 1px solid var(--border-subtle);
  border-radius: var(--r-md);
  overflow: hidden;
}
.wf-table__head,
.wf-table__row {
  display: grid;
  grid-template-columns: 1fr 100px 80px 130px 72px;
  align-items: center;
  gap: var(--sp-2);
}
.wf-table__head {
  background: var(--surface-2);
  padding: var(--sp-2) var(--sp-3);
  border-bottom: 1px solid var(--border-strong);
}
.wf-table__th {
  font-size: var(--fs-xs);
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: .03em;
  color: var(--text-muted);
}
.wf-table__th--num { text-align: right; }
.wf-table__th--act { text-align: right; }
.wf-table__row {
  padding: var(--sp-3);
  border-bottom: 1px solid var(--border-subtle);
  transition: background var(--motion) var(--ease);
}
.wf-table__row:last-child { border-bottom: none; }
.wf-table__row:hover { background: var(--surface-2); }
.wf-table__td { min-width: 0; }
.wf-table__td--num { text-align: right; font-variant-numeric: tabular-nums; }
.wf-table__td--act { display: flex; gap: 6px; justify-content: flex-end; }
.wf-table__name { font-weight: 600; color: var(--text); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.wf-table__id { margin-top: 2px; color: var(--text-faint); }
.wf-table__time { color: var(--text-muted); white-space: nowrap; font-size: var(--fs-sm); }

/* ── Execution table ───────────────────────────────────────────── */
.exec-lookup { display: flex; gap: var(--sp-2); align-items: center; }
.exec-lookup__input { width: 200px; padding: 6px 10px; }
.exec-table {
  width: 100%;
  border: 1px solid var(--border-subtle);
  border-radius: var(--r-md);
  overflow: hidden;
}
.exec-table__head,
.exec-table__row {
  display: grid;
  grid-template-columns: 1fr 120px 150px 100px;
  align-items: center;
  gap: var(--sp-2);
}
.exec-table__head {
  background: var(--surface-2);
  padding: var(--sp-2) var(--sp-3);
  border-bottom: 1px solid var(--border-strong);
}
.exec-table__th {
  font-size: var(--fs-xs);
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: .03em;
  color: var(--text-muted);
}
.exec-table__row {
  padding: var(--sp-3);
  border-bottom: 1px solid var(--border-subtle);
  transition: background var(--motion) var(--ease);
}
.exec-table__row:last-child { border-bottom: none; }
.exec-table__row:hover { background: var(--surface-2); }
.exec-table__td { min-width: 0; }
.exec-table__time { color: var(--text-muted); white-space: nowrap; font-size: var(--fs-sm); }

/* ── Stats grid ────────────────────────────────────────────────── */
.stats-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: var(--sp-3);
}
.stats-card {
  padding: var(--sp-3);
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: var(--r-md);
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.stats-card__label { font-size: var(--fs-xs); color: var(--text-muted); text-transform: uppercase; letter-spacing: .04em; }
.stats-card__value { font-size: var(--fs-xl); font-weight: 700; color: var(--text); font-variant-numeric: tabular-nums; }
.stats-card__value.is-ok { color: var(--success); }
.stats-card__value.is-warn { color: var(--warn); }
.stats-card__value.is-fail { color: var(--fail); }

/* ── Health ────────────────────────────────────────────────────── */
.health-body { display: flex; flex-direction: column; gap: var(--sp-3); align-items: flex-start; }
.health-result, .webhook-info {
  width: 100%;
  padding: var(--sp-3);
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: var(--r-md);
  display: flex;
  flex-direction: column;
  gap: var(--sp-2);
}
.health-row { display: flex; align-items: center; gap: var(--sp-2); }
.health-row__label { font-size: var(--fs-sm); font-weight: 600; color: var(--text-muted); min-width: 120px; }

/* ── Buttons ───────────────────────────────────────────────────── */
.btn-ghost {
  display: inline-flex; align-items: center; gap: 6px;
  background: var(--surface-2); color: var(--text);
  border: 1px solid var(--border); border-radius: var(--r-md);
  padding: 6px var(--sp-3); font-size: var(--fs-sm); font-weight: 600;
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
.icon-btn:disabled { opacity: .5; cursor: not-allowed; }

/* ── Modal ─────────────────────────────────────────────────────── */
.modal-overlay {
  position: fixed; inset: 0; background: var(--overlay-scrim);
  display: flex; align-items: center; justify-content: center;
  z-index: var(--z-modal);
}
.modal {
  position: relative;
  background: var(--surface); border: 1px solid var(--border);
  border-radius: var(--r-lg); padding: var(--sp-6);
  width: calc(100% - 32px); max-width: 520px; max-height: 88vh; overflow-y: auto;
  box-shadow: var(--shadow-lg);
}
.modal__head { display: flex; align-items: center; justify-content: space-between; margin-bottom: var(--sp-4); }
.modal__head h3 { margin: 0; font-size: var(--fs-lg); color: var(--text); }
.modal__x {
  display: grid; place-items: center;
  width: 30px; height: 30px;
  background: transparent; border: none; border-radius: var(--r-md);
  color: var(--text-muted); cursor: pointer;
  transition: color var(--motion) var(--ease), background var(--motion) var(--ease);
}
.modal__x:hover { color: var(--text); background: var(--surface-2); }
.modal__body { display: flex; flex-direction: column; gap: var(--sp-3); }
.modal__foot { display: flex; justify-content: flex-end; gap: var(--sp-2); margin-top: var(--sp-4); }

/* ── Form ──────────────────────────────────────────────────────── */
.form-hint { font-size: var(--fs-sm); color: var(--text-muted); margin: 0 0 var(--sp-2); line-height: 1.5; }
.field { display: flex; flex-direction: column; gap: var(--sp-1); font-size: var(--fs-sm); font-weight: 600; color: var(--text-muted); }
.field--inline { flex-direction: row; align-items: center; gap: 6px; }
.field__label { color: var(--text-muted); }
.field__input {
  font-family: inherit; font-size: var(--fs-base);
  background: var(--surface-2); color: var(--text);
  border: 1px solid var(--border); border-radius: var(--r-md);
  padding: 7px 10px; width: 100%;
  transition: border-color var(--motion) var(--ease);
}
.field__input:focus { outline: none; border-color: var(--brand); box-shadow: 0 0 0 3px var(--brand-soft); }
.field__input--area { resize: vertical; min-height: 80px; font-family: var(--font-mono); font-size: var(--fs-sm); }
.form-error { color: var(--fail); font-size: var(--fs-sm); }
.trigger-wf { display: flex; align-items: center; gap: var(--sp-2); }
.trigger-wf__name { font-size: var(--fs-sm); color: var(--text); font-weight: 600; }

/* ── Responsive ────────────────────────────────────────────────── */
@media (max-width: 900px) {
  .wf-table__head,
  .wf-table__row {
    grid-template-columns: 1fr 90px 100px 64px;
  }
  .wf-table__th:nth-child(3),
  .wf-table__td:nth-child(3) { display: none; }
  .exec-table__head,
  .exec-table__row {
    grid-template-columns: 1fr 110px 100px;
  }
  .exec-table__th:nth-child(4),
  .exec-table__td:nth-child(4) { display: none; }
  .stats-grid { grid-template-columns: 1fr 1fr; }
}
@media (max-width: 640px) {
  .wf-table__head,
  .wf-table__row {
    grid-template-columns: 1fr 90px 64px;
  }
  .wf-table__th:nth-child(4),
  .wf-table__td:nth-child(4) { display: none; }
  .exec-table__head,
  .exec-table__row {
    grid-template-columns: 1fr 110px;
  }
  .exec-table__th:nth-child(3),
  .exec-table__td:nth-child(3) { display: none; }
  .stats-grid { grid-template-columns: 1fr; }
  .exec-lookup { flex-wrap: wrap; }
  .exec-lookup__input { width: 100%; }
}
</style>
