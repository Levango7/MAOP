<template>
  <div class="subagents-view">
    <ListPageLayout>
      <template #actions>
        <Segmented
          :model-value="activeTab"
          :options="tabOptions"
          size="sm"
          @update:model-value="activeTab = $event"
        />
        <button class="btn-ghost" :class="{ 'is-busy': loading }" :disabled="loading" @click="loadList">
          <AppIcon name="refresh" :size="15" />
          <span>{{ t('view.subagents.active.refresh') }}</span>
        </button>
      </template>
      <template #content>
        <!-- ── Active subagents ────────────────────────────────── -->
        <div v-show="activeTab === 'active'">
          <Card :title="t('view.subagents.active.title')" icon="bot" margin-bottom="var(--sp-4)">
            <div v-if="loading" class="blk">
              <Skeleton block height="44px" />
              <Skeleton block height="44px" />
              <Skeleton block height="44px" />
            </div>
            <EmptyState
              v-else-if="errors.list"
              icon="alert-triangle"
              tone="fail"
              :title="t('view.subagents.active.failedLoad')"
              :description="errors.list"
            />
            <EmptyState
              v-else-if="!agents.length"
              icon="bot"
              :title="t('view.subagents.active.empty')"
              :description="t('view.subagents.active.emptyHint')"
            />
            <div v-else class="sa-table" role="table" :aria-label="t('view.subagents.active.title')">
              <div class="sa-table__head" role="row">
                <div class="sa-table__th" role="columnheader">{{ t('view.subagents.col.agentId') }}</div>
                <div class="sa-table__th" role="columnheader">{{ t('view.subagents.col.agent') }}</div>
                <div class="sa-table__th" role="columnheader">{{ t('view.subagents.col.task') }}</div>
                <div class="sa-table__th sa-table__th--model" role="columnheader">{{ t('view.subagents.col.model') }}</div>
                <div class="sa-table__th" role="columnheader">{{ t('view.subagents.col.status') }}</div>
                <div class="sa-table__th sa-table__th--act" role="columnheader">{{ t('view.subagents.col.actions') }}</div>
              </div>
              <div v-for="a in agents" :key="a.agent_id" class="sa-table__row" role="row">
                <div class="sa-table__td" role="cell">
                  <span class="sa-table__id mono">{{ a.agent_id }}</span>
                </div>
                <div class="sa-table__td" role="cell">
                  <span class="sa-table__agent">{{ a.agent || a.agent_name || '—' }}</span>
                </div>
                <div class="sa-table__td" role="cell">
                  <span class="sa-table__task">{{ a.task || '—' }}</span>
                </div>
                <div class="sa-table__td sa-table__td--model" role="cell">
                  <span class="sa-table__model muted">{{ a.model || '—' }}</span>
                </div>
                <div class="sa-table__td" role="cell">
                  <Badge :tone="statusTone(a.status)">
                    {{ statusLabel(a.status) }}
                  </Badge>
                </div>
                <div class="sa-table__td sa-table__td--act" role="cell">
                  <button
                    v-if="isRunning(a)"
                    class="icon-btn"
                    type="button"
                    :disabled="busyId === a.agent_id"
                    :aria-label="t('view.subagents.action.wait')"
                    @click="openWait(a)"
                  >
                    <AppIcon name="clock" :size="14" />
                  </button>
                  <button
                    class="icon-btn"
                    type="button"
                    :aria-label="t('view.subagents.action.transcript')"
                    @click="openTranscript(a)"
                  >
                    <AppIcon name="file-text" :size="14" />
                  </button>
                  <button
                    v-if="isRunning(a)"
                    class="icon-btn icon-btn--danger"
                    type="button"
                    :disabled="busyId === a.agent_id"
                    :aria-label="t('view.subagents.action.cancel')"
                    @click="doCancel(a)"
                  >
                    <AppIcon name="x" :size="14" />
                  </button>
                </div>
              </div>
            </div>
          </Card>
        </div>

        <!-- ── Spawn form ─────────────────────────────────────── -->
        <div v-show="activeTab === 'spawn'">
          <Card :title="t('view.subagents.spawn.title')" icon="plus" margin-bottom="var(--sp-4)">
            <p class="spawn-hint">{{ t('view.subagents.spawn.hint') }}</p>
            <div class="spawn-form">
              <label class="field">
                <span class="field__label">{{ t('view.subagents.spawn.fieldAgent') }}</span>
                <input
                  v-model="spawnForm.agent"
                  class="field__input"
                  type="text"
                  :placeholder="t('view.subagents.spawn.fieldAgentHint')"
                />
              </label>
              <label class="field">
                <span class="field__label">{{ t('view.subagents.spawn.fieldTask') }}</span>
                <textarea
                  v-model="spawnForm.task"
                  class="field__textarea"
                  rows="3"
                  :placeholder="t('view.subagents.spawn.fieldTaskHint')"
                />
              </label>
              <label class="field">
                <span class="field__label">{{ t('view.subagents.spawn.fieldContext') }}</span>
                <textarea
                  v-model="spawnForm.context"
                  class="field__textarea"
                  rows="3"
                  :placeholder="t('view.subagents.spawn.fieldContextHint')"
                />
              </label>
              <label class="field">
                <span class="field__label">{{ t('view.subagents.spawn.fieldModel') }}</span>
                <input
                  v-model="spawnForm.model"
                  class="field__input"
                  type="text"
                  :placeholder="t('view.subagents.spawn.fieldModelHint')"
                />
              </label>
              <div class="spawn-form__actions">
                <button
                  class="btn-primary"
                  type="button"
                  :disabled="spawning"
                  @click="doSpawn"
                >
                  <AppIcon name="plus" :size="14" />
                  <span>{{ spawning ? t('view.subagents.spawn.spawning') : t('view.subagents.spawn.submit') }}</span>
                </button>
              </div>
            </div>
          </Card>
        </div>
      </template>
    </ListPageLayout>

    <!-- ── Wait Modal ───────────────────────────────────────── -->
    <Teleport to="body">
      <div
        v-if="showWaitModal"
        v-modal-a11y
        class="modal-overlay"
        @click.self="closeWait"
        @modal:escape="closeWait"
      >
        <div class="modal modal--sm" role="dialog" aria-modal="true">
          <div class="modal__head">
            <h3>{{ t('view.subagents.wait.title') }}</h3>
            <button class="modal__x" type="button" :aria-label="t('common.close')" @click="closeWait">
              <AppIcon name="x" :size="16" />
            </button>
          </div>
          <div class="modal__body">
            <p v-if="waitTarget" class="modal__target mono">{{ waitTarget.agent_id }}</p>
            <label class="field">
              <span class="field__label">{{ t('view.subagents.wait.timeout') }}</span>
              <input
                v-model.number="waitTimeout"
                class="field__input"
                type="number"
                min="1"
                max="3600"
              />
            </label>
            <div v-if="waitResult" class="wait-result">
              <span class="field__label">{{ t('view.subagents.wait.result') }}</span>
              <pre class="wait-result__body mono">{{ formatResult(waitResult) }}</pre>
            </div>
          </div>
          <div class="modal__foot">
            <button class="btn-ghost" type="button" @click="closeWait">{{ t('common.close') }}</button>
            <button
              class="btn-primary"
              type="button"
              :disabled="waiting"
              @click="doWait"
            >
              <span>{{ waiting ? t('view.subagents.action.waiting') : t('view.subagents.wait.submit') }}</span>
            </button>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- ── Transcript Drawer ────────────────────────────────── -->
    <DetailDrawer
      :open="showTranscript"
      :title="t('view.subagents.transcript.title')"
      icon="file-text"
      @close="closeTranscript"
    >
      <div v-if="transcriptTarget" class="transcript-content">
        <p class="transcript-id mono">{{ transcriptTarget.agent_id }}</p>
        <div v-if="transcriptLoading" class="blk">
          <Skeleton block height="20px" />
          <Skeleton block height="20px" />
          <Skeleton block height="20px" />
        </div>
        <EmptyState
          v-else-if="transcriptError"
          icon="alert-triangle"
          tone="fail"
          :title="t('view.subagents.transcript.failedLoad')"
          :description="transcriptError"
        />
        <EmptyState
          v-else-if="!transcriptText"
          icon="file-text"
          :title="t('view.subagents.transcript.empty')"
        />
        <pre v-else class="transcript-body mono">{{ transcriptText }}</pre>
      </div>
    </DetailDrawer>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue';
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
import DetailDrawer from '../components/DetailDrawer.vue';

const api = useApiStore();
const toast = useToast();
const { showConfirm } = useConfirm();
const { t } = useI18n();

// ── Tab state ───────────────────────────────────────────────────
const activeTab = ref('active');
const tabOptions = [
  { value: 'active', label: t('view.subagents.tab.active'), icon: 'bot' },
  { value: 'spawn', label: t('view.subagents.tab.spawn'), icon: 'plus' },
];

// ── Data state ──────────────────────────────────────────────────
const loading = ref(false);
const errors = ref({ list: null });
const agents = ref([]);
const busyId = ref('');

// ── Spawn form ──────────────────────────────────────────────────
const spawning = ref(false);
const spawnForm = reactive({
  agent: '',
  task: '',
  context: '',
  model: '',
});

function resetSpawnForm() {
  spawnForm.agent = '';
  spawnForm.task = '';
  spawnForm.context = '';
  spawnForm.model = '';
}

async function doSpawn() {
  const agent = spawnForm.agent.trim();
  const task = spawnForm.task.trim();
  if (!agent) {
    toast.warn(t('view.subagents.spawn.validateAgent'));
    return;
  }
  if (!task) {
    toast.warn(t('view.subagents.spawn.validateTask'));
    return;
  }
  spawning.value = true;
  try {
    const payload = { agent, task };
    if (spawnForm.context.trim()) payload.context = spawnForm.context.trim();
    if (spawnForm.model.trim()) payload.model = spawnForm.model.trim();
    const data = await api.post('/api/subagent/spawn', payload);
    toast.success(t('view.subagents.spawn.success', { id: data.agent_id || '' }));
    resetSpawnForm();
    activeTab.value = 'active';
    await loadList();
  } catch (err) {
    toast.error((err && err.message) || t('view.subagents.spawn.failed'));
  } finally {
    spawning.value = false;
  }
}

// ── Status helpers ──────────────────────────────────────────────
function normalizeStatus(s) {
  return String(s || '').toLowerCase();
}

function statusLabel(s) {
  const n = normalizeStatus(s);
  if (n === 'running' || n === 'active' || n === 'pending') return t('view.subagents.status.running');
  if (n === 'done' || n === 'completed' || n === 'success') return t('view.subagents.status.done');
  if (n === 'error' || n === 'failed') return t('view.subagents.status.error');
  if (n === 'cancelled' || n === 'canceled') return t('view.subagents.status.cancelled');
  return t('view.subagents.status.unknown');
}

function statusTone(s) {
  const n = normalizeStatus(s);
  if (n === 'running' || n === 'active' || n === 'pending') return 'info';
  if (n === 'done' || n === 'completed' || n === 'success') return 'success';
  if (n === 'error' || n === 'failed') return 'fail';
  if (n === 'cancelled' || n === 'canceled') return 'neutral';
  return 'neutral';
}

function isRunning(a) {
  const n = normalizeStatus(a.status);
  return n === 'running' || n === 'active' || n === 'pending' || n === '';
}

// ── Cancel ──────────────────────────────────────────────────────
async function doCancel(a) {
  const ok = await showConfirm({
    message: t('view.subagents.cancel.confirm'),
    tone: 'danger',
  });
  if (!ok) return;
  busyId.value = a.agent_id;
  try {
    await api.post('/api/subagent/cancel', { agent_id: a.agent_id });
    toast.success(t('view.subagents.cancel.success'));
    await loadList();
  } catch (err) {
    toast.error((err && err.message) || t('view.subagents.cancel.failed'));
  } finally {
    busyId.value = '';
  }
}

// ── Wait modal ──────────────────────────────────────────────────
const showWaitModal = ref(false);
const waitTarget = ref(null);
const waitTimeout = ref(120);
const waiting = ref(false);
const waitResult = ref(null);

function openWait(a) {
  waitTarget.value = a;
  waitTimeout.value = 120;
  waitResult.value = null;
  showWaitModal.value = true;
}

function closeWait() {
  showWaitModal.value = false;
  waitTarget.value = null;
  waitResult.value = null;
}

async function doWait() {
  if (!waitTarget.value) return;
  waiting.value = true;
  try {
    const data = await api.post('/api/subagent/wait', {
      agent_id: waitTarget.value.agent_id,
      timeout: Math.max(1, Math.min(3600, Number(waitTimeout.value) || 120)),
    });
    waitResult.value = data.result ?? data;
    toast.success(t('view.subagents.wait.success'));
    await loadList();
  } catch (err) {
    toast.error((err && err.message) || t('view.subagents.wait.failed'));
  } finally {
    waiting.value = false;
  }
}

function formatResult(r) {
  if (r == null) return '';
  if (typeof r === 'string') return r;
  try { return JSON.stringify(r, null, 2); } catch { return String(r); }
}

// ── Transcript drawer ───────────────────────────────────────────
const showTranscript = ref(false);
const transcriptTarget = ref(null);
const transcriptLoading = ref(false);
const transcriptText = ref('');
const transcriptError = ref('');

async function openTranscript(a) {
  transcriptTarget.value = a;
  transcriptText.value = '';
  transcriptError.value = '';
  showTranscript.value = true;
  transcriptLoading.value = true;
  try {
    const data = await api.get(`/api/subagent/transcript?agent_id=${encodeURIComponent(a.agent_id)}`);
    const tr = data.transcript;
    if (typeof tr === 'string') {
      transcriptText.value = tr;
    } else if (Array.isArray(tr)) {
      transcriptText.value = tr.map((line) => (typeof line === 'string' ? line : JSON.stringify(line))).join('\n');
    } else if (tr && typeof tr === 'object') {
      try { transcriptText.value = JSON.stringify(tr, null, 2); } catch { transcriptText.value = String(tr); }
    } else {
      transcriptText.value = '';
    }
  } catch (err) {
    transcriptError.value = (err && err.message) || t('view.subagents.transcript.failedLoad');
  } finally {
    transcriptLoading.value = false;
  }
}

function closeTranscript() {
  showTranscript.value = false;
  transcriptTarget.value = null;
  transcriptText.value = '';
  transcriptError.value = '';
}

// ── Data loading ────────────────────────────────────────────────
async function loadList() {
  loading.value = true;
  errors.value = { list: null };
  try {
    const data = await api.get('/api/subagent/list');
    agents.value = data.agents || [];
  } catch (err) {
    agents.value = [];
    errors.value = { list: (err && err.message) || t('view.subagents.active.failedLoad') };
  } finally {
    loading.value = false;
  }
}

onMounted(() => {
  loadList();
});
</script>

<style scoped>
/* ── Layout helpers ────────────────────────────────────────────── */
.blk { display: flex; flex-direction: column; gap: var(--sp-2); }
.muted { color: var(--text-muted); }
.mono { font-family: var(--font-mono); font-size: var(--fs-xs); }

/* ── Buttons ───────────────────────────────────────────────────── */
.btn-ghost {
  display: inline-flex; align-items: center; gap: var(--sp-1);
  background: var(--surface-2); color: var(--text);
  border: 1px solid var(--border); border-radius: var(--r-md);
  padding: 7px 12px; font-size: var(--fs-sm); font-weight: 600;
  cursor: pointer; transition: opacity var(--motion) var(--ease);
}
.btn-ghost:hover { opacity: .9; }
.btn-ghost:disabled { opacity: .5; cursor: not-allowed; }
.btn-primary {
  display: inline-flex; align-items: center; gap: var(--sp-1);
  background: var(--brand); color: var(--brand-contrast);
  border: none; border-radius: var(--r-md);
  padding: 7px 16px; font-size: var(--fs-sm); font-weight: 600;
  cursor: pointer; transition: opacity var(--motion) var(--ease);
}
.btn-primary:hover { opacity: .9; }
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
.icon-btn--danger:hover { color: var(--fail); border-color: var(--fail); }

/* ── Subagent table ────────────────────────────────────────────── */
.sa-table {
  width: 100%;
  border: 1px solid var(--border-subtle);
  border-radius: var(--r-md);
  overflow: hidden;
}
.sa-table__head,
.sa-table__row {
  display: grid;
  grid-template-columns: 140px 1fr 1.5fr 100px 110px 120px;
  align-items: center;
  gap: var(--sp-2);
}
.sa-table__head {
  background: var(--surface-2);
  padding: var(--sp-2) var(--sp-3);
  border-bottom: 1px solid var(--border-strong);
}
.sa-table__th {
  font-size: var(--fs-xs);
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: .03em;
  color: var(--text-muted);
}
.sa-table__th--model,
.sa-table__th--act { text-align: right; }
.sa-table__row {
  padding: var(--sp-3);
  border-bottom: 1px solid var(--border-subtle);
  transition: background var(--motion) var(--ease);
}
.sa-table__row:hover { background: var(--surface-2); }
.sa-table__row:last-child { border-bottom: none; }
.sa-table__td { min-width: 0; }
.sa-table__id { color: var(--text-faint); }
.sa-table__agent { font-weight: 600; color: var(--text); font-size: var(--fs-sm); }
.sa-table__task {
  color: var(--text-muted); font-size: var(--fs-sm);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.sa-table__td--model { text-align: right; }
.sa-table__model { font-size: var(--fs-xs); }
.sa-table__td--act {
  display: flex; gap: var(--sp-1); justify-content: flex-end; flex-wrap: wrap;
}

/* ── Spawn form ────────────────────────────────────────────────── */
.spawn-hint {
  font-size: var(--fs-sm); color: var(--text-muted);
  margin: 0 0 var(--sp-3); line-height: 1.5;
}
.spawn-form { display: flex; flex-direction: column; gap: var(--sp-3); }
.spawn-form__actions { display: flex; justify-content: flex-end; }
.field { display: flex; flex-direction: column; gap: var(--sp-1); }
.field__label {
  font-size: var(--fs-sm); font-weight: 600; color: var(--text-muted);
}
.field__input {
  font-family: inherit; font-size: var(--fs-base);
  background: var(--surface-2); color: var(--text);
  border: 1px solid var(--border); border-radius: var(--r-md);
  padding: 7px 10px; width: 100%;
  transition: border-color var(--motion) var(--ease);
}
.field__input:focus { outline: none; border-color: var(--brand); }
.field__textarea {
  font-family: inherit; font-size: var(--fs-base);
  background: var(--surface-2); color: var(--text);
  border: 1px solid var(--border); border-radius: var(--r-md);
  padding: 7px 10px; width: 100%;
  resize: vertical; min-height: 80px;
  transition: border-color var(--motion) var(--ease);
}
.field__textarea:focus { outline: none; border-color: var(--brand); }

/* ── Modal ─────────────────────────────────────────────────────── */
.modal-overlay {
  position: fixed; inset: 0; background: var(--overlay-scrim);
  display: flex; align-items: center; justify-content: center;
  z-index: var(--z-modal);
}
.modal {
  position: relative;
  background: var(--surface); border: 1px solid var(--border);
  border-radius: var(--r-lg); padding: 24px;
  width: calc(100% - 32px); max-width: 520px; max-height: 88vh; overflow-y: auto;
  box-shadow: var(--shadow-lg);
}
.modal--sm { max-width: 420px; }
.modal__head {
  display: flex; align-items: center; justify-content: space-between;
  margin-bottom: var(--sp-4);
}
.modal__head h3 { margin: 0; font-size: var(--fs-lg); color: var(--text); }
.modal__x {
  display: grid; place-items: center;
  width: 28px; height: 28px;
  background: transparent; border: none; border-radius: var(--r-sm);
  color: var(--text-muted); cursor: pointer;
  transition: color var(--motion) var(--ease), background var(--motion) var(--ease);
}
.modal__x:hover { color: var(--text); background: var(--surface-2); }
.modal__body { display: flex; flex-direction: column; gap: var(--sp-3); }
.modal__target { color: var(--text-muted); font-size: var(--fs-sm); }
.modal__foot {
  display: flex; justify-content: flex-end; gap: var(--sp-2);
  margin-top: var(--sp-4);
}
.wait-result { display: flex; flex-direction: column; gap: var(--sp-1); }
.wait-result__body {
  background: var(--surface-2); border: 1px solid var(--border-subtle);
  border-radius: var(--r-md); padding: var(--sp-2) var(--sp-3);
  max-height: 240px; overflow: auto; white-space: pre-wrap;
  font-size: var(--fs-xs); color: var(--text);
}

/* ── Transcript drawer ─────────────────────────────────────────── */
.transcript-content { display: flex; flex-direction: column; gap: var(--sp-3); }
.transcript-id { color: var(--text-muted); font-size: var(--fs-sm); }
.transcript-body {
  background: var(--surface-2); border: 1px solid var(--border-subtle);
  border-radius: var(--r-md); padding: var(--sp-3);
  max-height: 60vh; overflow: auto; white-space: pre-wrap;
  font-size: var(--fs-xs); color: var(--text); line-height: 1.5;
}

/* ── Responsive ────────────────────────────────────────────────── */
@media (max-width: 900px) {
  .sa-table__head,
  .sa-table__row {
    grid-template-columns: 120px 1fr 100px 120px;
  }
  .sa-table__th--model,
  .sa-table__td--model { display: none; }
  .sa-table__task { display: none; }
}
@media (max-width: 640px) {
  .sa-table__head,
  .sa-table__row {
    grid-template-columns: 1fr 100px;
  }
  .sa-table__th--act,
  .sa-table__td--act { display: none; }
  .sa-table__agent { display: none; }
}
</style>
