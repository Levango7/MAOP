<template>
  <div class="plugins-view">
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
        <!-- ── Plugin list ─────────────────────────────────────── -->
        <div v-show="activeTab === 'list'">
          <Card :title="t('view.plugins.list.title')" icon="plug" margin-bottom="var(--sp-4)">
            <template #actions>
              <button class="btn-ghost btn-ghost--sm" :disabled="bulkBusy" @click="bulkLoad">
                <AppIcon name="download" :size="14" /><span>{{ t('view.plugins.list.loadAll') }}</span>
              </button>
              <button class="btn-ghost btn-ghost--sm" :disabled="bulkBusy" @click="bulkStart">
                <AppIcon name="play" :size="14" /><span>{{ t('view.plugins.list.startAll') }}</span>
              </button>
              <button class="btn-ghost btn-ghost--sm" :disabled="bulkBusy" @click="bulkStop">
                <AppIcon name="square" :size="14" /><span>{{ t('view.plugins.list.stopAll') }}</span>
              </button>
            </template>
            <FilterBar
              :model-value="filters"
              :schema="filterSchema"
              :results-label="resultsLabel"
            />
            <div v-if="loading" class="blk">
              <Skeleton block height="44px" />
              <Skeleton block height="44px" />
              <Skeleton block height="44px" />
            </div>
            <EmptyState
              v-else-if="errors.list"
              icon="alert-triangle"
              tone="fail"
              :title="t('view.plugins.list.failedLoad')"
              :description="errors.list"
            />
            <EmptyState
              v-else-if="!plugins.length"
              icon="plug"
              :title="t('view.plugins.list.empty')"
              :description="t('view.plugins.list.emptyHint')"
            />
            <div v-else class="pl-table" role="table" :aria-label="t('view.plugins.list.title')">
              <div class="pl-table__head" role="row">
                <div class="pl-table__th" role="columnheader">{{ t('common.name') }}</div>
                <div class="pl-table__th pl-table__th--ver" role="columnheader">{{ t('view.plugins.col.version') }}</div>
                <div class="pl-table__th" role="columnheader">{{ t('view.plugins.col.state') }}</div>
                <div class="pl-table__th pl-table__th--act" role="columnheader">{{ t('view.plugins.col.actions') }}</div>
              </div>
              <div v-for="p in filteredPlugins" :key="p.id" class="pl-table__row" role="row">
                <div class="pl-table__td" role="cell">
                  <div class="pl-table__name">{{ p.name || p.id }}</div>
                  <div class="pl-table__id mono">{{ p.id }}</div>
                </div>
                <div class="pl-table__td pl-table__td--ver" role="cell">
                  <span class="pl-table__ver mono">{{ p.version || '—' }}</span>
                </div>
                <div class="pl-table__td" role="cell">
                  <Badge :tone="stateTone(p.state)">
                    {{ stateLabel(p.state) }}
                  </Badge>
                </div>
                <div class="pl-table__td pl-table__td--act" role="cell">
                  <button
                    v-if="canLoad(p)"
                    class="icon-btn"
                    type="button"
                    :disabled="busyId === p.id"
                    :aria-label="t('view.plugins.action.load')"
                    @click="doLoad(p)"
                  >
                    <AppIcon name="download" :size="14" />
                  </button>
                  <button
                    v-if="canStart(p)"
                    class="icon-btn"
                    type="button"
                    :disabled="busyId === p.id"
                    :aria-label="t('view.plugins.action.start')"
                    @click="doStart(p)"
                  >
                    <AppIcon name="play" :size="14" />
                  </button>
                  <button
                    v-if="canStop(p)"
                    class="icon-btn"
                    type="button"
                    :disabled="busyId === p.id"
                    :aria-label="t('view.plugins.action.stop')"
                    @click="doStop(p)"
                  >
                    <AppIcon name="square" :size="14" />
                  </button>
                  <button
                    v-if="canReload(p)"
                    class="icon-btn"
                    type="button"
                    :disabled="busyId === p.id"
                    :aria-label="t('view.plugins.action.reload')"
                    @click="doReload(p)"
                  >
                    <AppIcon name="rotate-ccw" :size="14" />
                  </button>
                  <button
                    v-if="canConfig(p)"
                    class="icon-btn"
                    type="button"
                    :aria-label="t('view.plugins.action.config')"
                    @click="openConfig(p)"
                  >
                    <AppIcon name="gear" :size="14" />
                  </button>
                </div>
              </div>
            </div>
          </Card>
        </div>

        <!-- ── Discover ───────────────────────────────────────── -->
        <div v-show="activeTab === 'discover'">
          <Card :title="t('view.plugins.discover.title')" icon="search" margin-bottom="var(--sp-4)">
            <template #actions>
              <button
                class="btn-ghost btn-ghost--sm"
                :disabled="discovering"
                @click="runDiscover"
              >
                <AppIcon name="refresh" :size="14" :class="{ spinning: discovering }" />
                <span>{{ discovering ? t('view.plugins.discover.scanning') : t('view.plugins.discover.scanBtn') }}</span>
              </button>
            </template>
            <div v-if="discovering" class="blk">
              <Skeleton block height="44px" />
              <Skeleton block height="44px" />
            </div>
            <EmptyState
              v-else-if="errors.discover"
              icon="alert-triangle"
              tone="fail"
              :title="t('view.plugins.discover.failedLoad')"
              :description="errors.discover"
            />
            <EmptyState
              v-else-if="!discovered.length"
              icon="search"
              :title="t('view.plugins.discover.empty')"
              :description="t('view.plugins.discover.emptyHint')"
            />
            <div v-else class="dc-list">
              <div v-for="p in discovered" :key="p.id" class="dc-item">
                <div class="dc-item__main">
                  <AppIcon name="plug" :size="15" class="dc-item__icon" />
                  <span class="dc-item__name">{{ p.name || p.id }}</span>
                  <Badge v-if="p.version" tone="neutral">{{ p.version }}</Badge>
                </div>
                <div class="dc-item__meta">
                  <span class="dc-item__id mono">{{ p.id }}</span>
                  <button
                    class="btn-ghost btn-ghost--sm"
                    type="button"
                    :disabled="busyId === p.id"
                    @click="doLoad(p)"
                  >
                    <AppIcon name="download" :size="13" />
                    <span>{{ t('view.plugins.action.load') }}</span>
                  </button>
                </div>
              </div>
            </div>
          </Card>
        </div>
      </template>
    </ListPageLayout>

    <!-- ── Config Modal ─────────────────────────────────────── -->
    <Teleport to="body">
      <div
        v-if="showConfigModal"
        v-modal-a11y
        class="modal-overlay"
        @click.self="closeConfig"
        @modal:escape="closeConfig"
      >
        <div class="modal" role="dialog" aria-modal="true">
          <div class="modal__head">
            <h3>{{ t('view.plugins.modal.configTitle') }}</h3>
            <button class="modal__x" type="button" :aria-label="t('common.close')" @click="closeConfig">
              <AppIcon name="x" :size="16" />
            </button>
          </div>
          <div class="modal__body">
            <p v-if="configTarget" class="modal__target mono">{{ configTarget.id }}</p>
            <p class="modal__hint">{{ t('view.plugins.modal.configHint') }}</p>
            <textarea
              v-model="configText"
              class="field__textarea mono"
              rows="10"
              spellcheck="false"
              :placeholder="'{}'"
            />
            <p v-if="configError" class="modal__err">{{ configError }}</p>
          </div>
          <div class="modal__foot">
            <button class="btn-ghost" type="button" @click="closeConfig">{{ t('common.cancel') }}</button>
            <button
              class="btn-primary"
              type="button"
              :disabled="configSaving"
              @click="submitConfig"
            >
              {{ configSaving ? t('view.plugins.modal.saving') : t('common.save') }}
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
import FilterBar from '../components/FilterBar.vue';
import Segmented from '../components/Segmented.vue';
import Skeleton from '../components/Skeleton.vue';
import EmptyState from '../components/EmptyState.vue';

const api = useApiStore();
const toast = useToast();
const { t } = useI18n();

// ── Tab state ───────────────────────────────────────────────────
const activeTab = ref('list');
const tabOptions = [
  { value: 'list', label: t('view.plugins.tab.list'), icon: 'plug' },
  { value: 'discover', label: t('view.plugins.tab.discover'), icon: 'search' },
];

// ── Data state ──────────────────────────────────────────────────
const loading = ref(false);
const discovering = ref(false);
const errors = ref({ list: null, discover: null });
const plugins = ref([]);
const discovered = ref([]);
const busyId = ref('');
const bulkBusy = ref(false);

// ── Filters ─────────────────────────────────────────────────────
const filters = reactive({ state: '' });
const filterSchema = computed(() => [
  {
    key: 'state',
    label: t('view.plugins.filter.allStates'),
    options: [
      { value: 'loaded', label: t('view.plugins.state.loaded') },
      { value: 'started', label: t('view.plugins.state.started') },
      { value: 'stopped', label: t('view.plugins.state.stopped') },
      { value: 'unloaded', label: t('view.plugins.state.unloaded') },
      { value: 'error', label: t('view.plugins.state.error') },
    ],
  },
]);

const filteredPlugins = computed(() => {
  if (!filters.state) return plugins.value;
  return plugins.value.filter((p) => normalizeState(p.state) === filters.state);
});

const resultsLabel = computed(() => {
  const n = filteredPlugins.value.length;
  return `${n} / ${plugins.value.length}`;
});

// ── State helpers ───────────────────────────────────────────────
function normalizeState(s) {
  return String(s || '').toLowerCase();
}

function stateLabel(s) {
  const n = normalizeState(s);
  if (n === 'loaded') return t('view.plugins.state.loaded');
  if (n === 'started' || n === 'running') return t('view.plugins.state.started');
  if (n === 'stopped') return t('view.plugins.state.stopped');
  if (n === 'unloaded' || n === 'discovered') return t('view.plugins.state.unloaded');
  if (n === 'error' || n === 'failed') return t('view.plugins.state.error');
  return t('view.plugins.state.unknown');
}

function stateTone(s) {
  const n = normalizeState(s);
  if (n === 'started' || n === 'running') return 'success';
  if (n === 'loaded') return 'info';
  if (n === 'stopped') return 'neutral';
  if (n === 'error' || n === 'failed') return 'fail';
  return 'neutral';
}

function canLoad(p) {
  const n = normalizeState(p.state);
  return n === 'unloaded' || n === 'discovered' || n === '';
}
function canStart(p) {
  const n = normalizeState(p.state);
  return n === 'loaded' || n === 'stopped';
}
function canStop(p) {
  const n = normalizeState(p.state);
  return n === 'started' || n === 'running';
}
function canReload(p) {
  const n = normalizeState(p.state);
  return n === 'loaded' || n === 'started' || n === 'running' || n === 'stopped';
}
function canConfig(p) {
  const n = normalizeState(p.state);
  return n !== 'unloaded' && n !== 'discovered' && n !== '';
}

// ── Single-plugin actions ───────────────────────────────────────
async function doLoad(p) {
  busyId.value = p.id;
  try {
    await api.post(`/api/plugins/${encodeURIComponent(p.id)}/load`);
    toast.success(t('view.plugins.toast.loaded'));
    await loadPlugins();
  } catch (err) {
    toast.error((err && err.message) || t('view.plugins.toast.loadFailed'));
  } finally {
    busyId.value = '';
  }
}

async function doStart(p) {
  busyId.value = p.id;
  try {
    await api.post(`/api/plugins/${encodeURIComponent(p.id)}/start`);
    toast.success(t('view.plugins.toast.started'));
    await loadPlugins();
  } catch (err) {
    toast.error((err && err.message) || t('view.plugins.toast.startFailed'));
  } finally {
    busyId.value = '';
  }
}

async function doStop(p) {
  busyId.value = p.id;
  try {
    await api.post(`/api/plugins/${encodeURIComponent(p.id)}/stop`);
    toast.success(t('view.plugins.toast.stopped'));
    await loadPlugins();
  } catch (err) {
    toast.error((err && err.message) || t('view.plugins.toast.stopFailed'));
  } finally {
    busyId.value = '';
  }
}

async function doReload(p) {
  busyId.value = p.id;
  try {
    await api.post(`/api/plugins/${encodeURIComponent(p.id)}/reload`);
    toast.success(t('view.plugins.toast.reloaded'));
    await loadPlugins();
  } catch (err) {
    toast.error((err && err.message) || t('view.plugins.toast.reloadFailed'));
  } finally {
    busyId.value = '';
  }
}

// ── Bulk actions ────────────────────────────────────────────────
async function bulkLoad() {
  bulkBusy.value = true;
  try {
    await api.post('/api/plugins/load-all');
    toast.success(t('view.plugins.toast.loadAllOk'));
    await loadPlugins();
  } catch (err) {
    toast.error((err && err.message) || t('view.plugins.toast.loadAllFailed'));
  } finally {
    bulkBusy.value = false;
  }
}

async function bulkStart() {
  bulkBusy.value = true;
  try {
    await api.post('/api/plugins/start-all');
    toast.success(t('view.plugins.toast.startAllOk'));
    await loadPlugins();
  } catch (err) {
    toast.error((err && err.message) || t('view.plugins.toast.startAllFailed'));
  } finally {
    bulkBusy.value = false;
  }
}

async function bulkStop() {
  bulkBusy.value = true;
  try {
    await api.post('/api/plugins/stop-all');
    toast.success(t('view.plugins.toast.stopAllOk'));
    await loadPlugins();
  } catch (err) {
    toast.error((err && err.message) || t('view.plugins.toast.stopAllFailed'));
  } finally {
    bulkBusy.value = false;
  }
}

// ── Discover ────────────────────────────────────────────────────
async function runDiscover() {
  discovering.value = true;
  errors.value = { ...errors.value, discover: null };
  try {
    const data = await api.post('/api/plugins/discover');
    discovered.value = data.discovered || [];
    toast.success(t('view.plugins.toast.discoverOk', { n: discovered.value.length }));
  } catch (err) {
    discovered.value = [];
    errors.value = { ...errors.value, discover: (err && err.message) || t('view.plugins.toast.discoverFailed') };
    toast.error((err && err.message) || t('view.plugins.toast.discoverFailed'));
  } finally {
    discovering.value = false;
  }
}

// ── Config modal ────────────────────────────────────────────────
const showConfigModal = ref(false);
const configTarget = ref(null);
const configText = ref('{}');
const configError = ref('');
const configSaving = ref(false);

function openConfig(p) {
  configTarget.value = p;
  try {
    configText.value = JSON.stringify(p.config || {}, null, 2);
  } catch {
    configText.value = '{}';
  }
  configError.value = '';
  showConfigModal.value = true;
}

function closeConfig() {
  showConfigModal.value = false;
  configTarget.value = null;
  configError.value = '';
}

async function submitConfig() {
  if (!configTarget.value) return;
  let parsed;
  try {
    parsed = JSON.parse(configText.value || '{}');
    configError.value = '';
  } catch {
    configError.value = t('view.plugins.modal.configInvalid');
    return;
  }
  configSaving.value = true;
  try {
    await api.put(`/api/plugins/${encodeURIComponent(configTarget.value.id)}/config`, { config: parsed });
    toast.success(t('view.plugins.toast.configSaved'));
    showConfigModal.value = false;
    configTarget.value = null;
    await loadPlugins();
  } catch (err) {
    toast.error((err && err.message) || t('view.plugins.modal.saveFailed'));
  } finally {
    configSaving.value = false;
  }
}

// ── Data loading ────────────────────────────────────────────────
async function loadPlugins() {
  try {
    const data = await api.get('/api/plugins');
    plugins.value = data.plugins || [];
    errors.value = { ...errors.value, list: null };
  } catch (err) {
    plugins.value = [];
    errors.value = { ...errors.value, list: (err && err.message) || t('view.plugins.list.failedLoad') };
  }
}

async function loadAll() {
  loading.value = true;
  await Promise.allSettled([loadPlugins()]);
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

/* ── Buttons ───────────────────────────────────────────────────── */
.btn-ghost {
  display: inline-flex; align-items: center; gap: var(--sp-1);
  background: var(--surface-2); color: var(--text);
  border: 1px solid var(--border); border-radius: var(--r-md);
  padding: 7px var(--sp-3); font-size: var(--fs-sm); font-weight: 600;
  cursor: pointer; transition: opacity var(--motion) var(--ease);
}
.btn-ghost:hover { opacity: .9; }
.btn-ghost:disabled { opacity: .5; cursor: not-allowed; }
.btn-ghost--sm { padding: var(--sp-1) var(--sp-2); font-size: var(--fs-xs); }
.btn-primary {
  display: inline-flex; align-items: center; gap: var(--sp-1);
  background: var(--brand); color: var(--brand-contrast);
  border: none; border-radius: var(--r-md);
  padding: 7px var(--sp-4); font-size: var(--fs-sm); font-weight: 600;
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
.spinning { animation: maop-spin 1s linear infinite; }
@keyframes maop-spin { to { transform: rotate(360deg); } }

/* ── Plugin table ──────────────────────────────────────────────── */
.pl-table {
  width: 100%;
  border: 1px solid var(--border-subtle);
  border-radius: var(--r-md);
  overflow: hidden;
}
.pl-table__head,
.pl-table__row {
  display: grid;
  grid-template-columns: 1fr 100px 120px 180px;
  align-items: center;
  gap: var(--sp-2);
}
.pl-table__head {
  background: var(--surface-2);
  padding: var(--sp-2) var(--sp-3);
  border-bottom: 1px solid var(--border-strong);
}
.pl-table__th {
  font-size: var(--fs-xs);
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: .03em;
  color: var(--text-muted);
}
.pl-table__th--ver { text-align: left; }
.pl-table__th--act { text-align: right; }
.pl-table__row {
  padding: var(--sp-3);
  border-bottom: 1px solid var(--border-subtle);
  transition: background var(--motion) var(--ease);
}
.pl-table__row:hover { background: var(--surface-2); }
.pl-table__row:last-child { border-bottom: none; }
.pl-table__td { min-width: 0; }
.pl-table__name { font-weight: 600; color: var(--text); font-size: var(--fs-sm); }
.pl-table__id { color: var(--text-faint); margin-top: 2px; }
.pl-table__ver { color: var(--text-muted); }
.pl-table__td--act {
  display: flex; gap: var(--sp-1); justify-content: flex-end; flex-wrap: wrap;
}

/* ── Discover list ─────────────────────────────────────────────── */
.dc-list { display: flex; flex-direction: column; gap: var(--sp-2); }
.dc-item {
  display: flex; align-items: center; justify-content: space-between;
  padding: var(--sp-3); border: 1px solid var(--border-subtle);
  border-radius: var(--r-md); background: var(--surface);
  transition: border-color var(--motion) var(--ease);
}
.dc-item:hover { border-color: var(--border-strong); }
.dc-item__main { display: flex; align-items: center; gap: var(--sp-2); }
.dc-item__icon { color: var(--brand-strong); }
.dc-item__name { font-weight: 600; color: var(--text); font-size: var(--fs-sm); }
.dc-item__meta { display: flex; align-items: center; gap: var(--sp-3); }
.dc-item__id { color: var(--text-faint); }

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
  width: calc(100% - 32px); max-width: 560px; max-height: 88vh; overflow-y: auto;
  box-shadow: var(--shadow-lg);
}
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
.modal__body { display: flex; flex-direction: column; gap: var(--sp-2); }
.modal__target { color: var(--text-muted); font-size: var(--fs-sm); }
.modal__hint { color: var(--text-muted); font-size: var(--fs-sm); margin: 0; }
.modal__err { color: var(--fail); font-size: var(--fs-sm); margin: 0; }
.modal__foot {
  display: flex; justify-content: flex-end; gap: var(--sp-2);
  margin-top: var(--sp-4);
}
.field__textarea {
  font-family: var(--font-mono); font-size: var(--fs-sm);
  background: var(--surface-2); color: var(--text);
  border: 1px solid var(--border); border-radius: var(--r-md);
  padding: var(--sp-2) var(--sp-3); width: 100%;
  resize: vertical; min-height: 160px;
  transition: border-color var(--motion) var(--ease);
}
.field__textarea:focus { outline: none; border-color: var(--brand); }

/* ── Responsive ────────────────────────────────────────────────── */
@media (max-width: 900px) {
  .pl-table__head,
  .pl-table__row {
    grid-template-columns: 1fr 120px 140px;
  }
  .pl-table__th--ver,
  .pl-table__td--ver { display: none; }
}
@media (max-width: 640px) {
  .pl-table__head,
  .pl-table__row {
    grid-template-columns: 1fr 120px;
  }
  .pl-table__th--act,
  .pl-table__td--act { display: none; }
  .dc-item { flex-direction: column; align-items: flex-start; gap: var(--sp-2); }
}
</style>
