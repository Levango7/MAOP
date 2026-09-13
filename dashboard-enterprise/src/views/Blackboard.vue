<template>
  <div class="blackboard-view">
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
        <!-- ── Snapshot ────────────────────────────────────────── -->
        <div v-show="activeTab === 'snapshot'">
          <Card :title="t('view.blackboard.snapshot.title')" icon="database" margin-bottom="var(--sp-4)">
            <div v-if="loading" class="blk">
              <Skeleton block height="60px" />
              <Skeleton block height="60px" />
            </div>
            <EmptyState
              v-else-if="errors.snapshot"
              icon="alert-triangle"
              tone="fail"
              :title="t('view.blackboard.snapshot.failedLoad')"
              :description="errors.snapshot"
            />
            <EmptyState
              v-else-if="!snapshotDomains.length"
              icon="database"
              :title="t('view.blackboard.snapshot.empty')"
              :description="t('view.blackboard.snapshot.emptyHint')"
            />
            <div v-else class="snap-grid">
              <div v-for="dom in snapshotDomains" :key="dom" class="snap-card" @click="selectDomainFromSnapshot(dom)">
                <div class="snap-card__head">
                  <AppIcon name="box" :size="14" class="snap-card__icon" />
                  <span class="snap-card__domain">{{ dom }}</span>
                </div>
                <div class="snap-card__count">
                  <span class="snap-card__num">{{ snapshot[dom].length }}</span>
                  <span class="snap-card__label">{{ t('view.blackboard.snapshot.entries') }}</span>
                </div>
              </div>
            </div>
          </Card>
        </div>

        <!-- ── Domains ─────────────────────────────────────────── -->
        <div v-show="activeTab === 'domains'">
          <Card :title="t('view.blackboard.domains.title')" icon="box" margin-bottom="var(--sp-4)">
            <template #actions>
              <button class="btn-ghost" @click="openWrite">
                <AppIcon name="plus" :size="15" /><span>{{ t('view.blackboard.domains.add') }}</span>
              </button>
            </template>
            <FilterBar
              :model-value="domainFilters"
              :schema="domainFilterSchema"
              :results-label="domainResultsLabel"
            />
            <div v-if="loading" class="blk">
              <Skeleton block height="44px" />
              <Skeleton block height="44px" />
            </div>
            <EmptyState
              v-else-if="errors.domains"
              icon="alert-triangle"
              tone="fail"
              :title="t('view.blackboard.domains.failedLoad')"
              :description="errors.domains"
            />
            <EmptyState
              v-else-if="!domainEntries.length"
              icon="box"
              :title="t('view.blackboard.domains.empty')"
              :description="t('view.blackboard.domains.emptyHint')"
            />
            <div v-else class="bb-table" role="table" :aria-label="t('view.blackboard.domains.title')">
              <div class="bb-table__head" role="row">
                <div class="bb-table__th" role="columnheader">{{ t('view.blackboard.col.content') }}</div>
                <div class="bb-table__th" role="columnheader">{{ t('view.blackboard.col.contributor') }}</div>
                <div class="bb-table__th bb-table__th--num" role="columnheader">{{ t('view.blackboard.col.confidence') }}</div>
                <div class="bb-table__th" role="columnheader">{{ t('view.blackboard.col.timestamp') }}</div>
              </div>
              <div v-for="(e, i) in domainEntries" :key="e.id || i" class="bb-table__row" role="row">
                <div class="bb-table__td" role="cell">
                  <pre class="bb-table__content mono">{{ formatContent(e.content) }}</pre>
                </div>
                <div class="bb-table__td" role="cell">{{ e.contributor || '—' }}</div>
                <div class="bb-table__td bb-table__td--num" role="cell">
                  <Badge :tone="confidenceTone(e.confidence)">{{ formatConfidence(e.confidence) }}</Badge>
                </div>
                <div class="bb-table__td" role="cell">
                  <span class="bb-table__time">{{ formatRel(e.timestamp || e.ts || e.created_at) }}</span>
                </div>
              </div>
            </div>
            <div v-if="selectedDomain" class="domain-clear">
              <button class="btn-danger-ghost" type="button" @click="clearDomain">
                <AppIcon name="trash" :size="14" />
                <span>{{ t('view.blackboard.clear.action') }}</span>
              </button>
            </div>
          </Card>
        </div>

        <!-- ── History ─────────────────────────────────────────── -->
        <div v-show="activeTab === 'history'">
          <Card :title="t('view.blackboard.history.title')" icon="clock" margin-bottom="var(--sp-4)">
            <div v-if="loading" class="blk">
              <Skeleton block height="40px" />
              <Skeleton block height="40px" />
            </div>
            <EmptyState
              v-else-if="errors.history"
              icon="alert-triangle"
              tone="fail"
              :title="t('view.blackboard.history.failedLoad')"
              :description="errors.history"
            />
            <EmptyState
              v-else-if="!history.length"
              icon="clock"
              :title="t('view.blackboard.history.empty')"
            />
            <div v-else class="hist-list">
              <div v-for="(h, i) in history" :key="(h.id != null ? h.id : (h.timestamp || h.ts || h.time) + '-' + (h.actor || h.contributor || '') + '-' + i)" class="hist-item">
                <div class="hist-item__head">
                  <Badge :tone="historyTone(h)">{{ h.op || h.operation || h.action || '—' }}</Badge>
                  <span class="hist-item__actor muted">{{ h.actor || h.contributor || '—' }}</span>
                  <span class="hist-item__time">{{ formatRel(h.timestamp || h.ts || h.time) }}</span>
                </div>
                <div v-if="h.domain || h.target" class="hist-item__target">
                  <span class="muted">{{ t('view.blackboard.col.domain') }}:</span>
                  <code class="mono">{{ h.domain || h.target || '—' }}</code>
                </div>
              </div>
            </div>
          </Card>
        </div>

        <!-- ── Stats ───────────────────────────────────────────── -->
        <div v-show="activeTab === 'stats'">
          <Card :title="t('view.blackboard.stats.title')" icon="activity" margin-bottom="var(--sp-4)">
            <div v-if="loading" class="blk">
              <Skeleton block height="80px" />
              <Skeleton block height="80px" />
            </div>
            <EmptyState
              v-else-if="errors.stats"
              icon="alert-triangle"
              tone="fail"
              :title="t('view.blackboard.stats.failedLoad')"
              :description="errors.stats"
            />
            <EmptyState
              v-else-if="!hasStats"
              icon="activity"
              :title="t('view.blackboard.stats.empty')"
            />
            <div v-else class="stats-grid">
              <div class="stats-card">
                <span class="stats-card__label">{{ t('view.blackboard.stats.totalEntries') }}</span>
                <span class="stats-card__value">{{ stats.total_entries ?? 0 }}</span>
              </div>
              <div class="stats-card">
                <span class="stats-card__label">{{ t('view.blackboard.stats.activeDomains') }}</span>
                <span class="stats-card__value">{{ (stats.active_domains || []).length }}</span>
              </div>
              <div class="stats-card">
                <span class="stats-card__label">{{ t('view.blackboard.stats.eventBus') }}</span>
                <span class="stats-card__value">
                  <Badge :tone="stats.event_bus_enabled ? 'success' : 'neutral'">
                    {{ stats.event_bus_enabled ? t('view.blackboard.stats.eventBusEnabled') : t('view.blackboard.stats.eventBusDisabled') }}
                  </Badge>
                </span>
              </div>
              <div class="stats-card stats-card--wide">
                <span class="stats-card__label">{{ t('view.blackboard.stats.allowedDomains') }}</span>
                <div class="stats-card__domains">
                  <Badge v-for="d in (stats.allowed_domains || [])" :key="d" tone="neutral">{{ d }}</Badge>
                </div>
              </div>
            </div>
          </Card>
        </div>
      </template>
    </ListPageLayout>

    <!-- ── Write Entry Modal ──────────────────────────────────── -->
    <Teleport to="body">
      <div
        v-if="showWriteModal"
        v-modal-a11y
        class="modal-overlay"
        @click.self="closeWrite"
        @modal:escape="closeWrite"
      >
        <div class="modal" role="dialog" aria-modal="true">
          <div class="modal__head">
            <h3>{{ t('view.blackboard.write.title') }}</h3>
            <button class="modal__x" type="button" :aria-label="t('common.close')" @click="closeWrite">
              <AppIcon name="x" :size="16" />
            </button>
          </div>
          <div class="modal__body">
            <label class="field">
              <span class="field__label">{{ t('view.blackboard.write.domain') }}</span>
              <select v-model="writeForm.domain" class="field__input field__select">
                <option value="">{{ t('view.blackboard.domains.select') }}</option>
                <option v-for="d in allowedDomains" :key="d" :value="d">{{ d }}</option>
              </select>
            </label>
            <label class="field">
              <span class="field__label">{{ t('view.blackboard.write.content') }}</span>
              <textarea
                v-model="writeForm.contentRaw"
                class="field__input field__input--area"
                rows="4"
                :placeholder="t('view.blackboard.write.contentPlaceholder')"
              ></textarea>
            </label>
            <div class="field-row">
              <label class="field">
                <span class="field__label">{{ t('view.blackboard.write.contributor') }}</span>
                <input v-model="writeForm.contributor" class="field__input" type="text" :placeholder="t('view.blackboard.write.contributorPlaceholder')" />
              </label>
              <label class="field">
                <span class="field__label">{{ t('view.blackboard.write.confidence') }}</span>
                <input v-model.number="writeForm.confidence" class="field__input" type="number" min="0" max="1" step="0.1" />
              </label>
            </div>
            <label class="field">
              <span class="field__label">{{ t('view.blackboard.write.metadata') }}</span>
              <input v-model="writeForm.metadataRaw" class="field__input" type="text" :placeholder="t('view.blackboard.write.metadataPlaceholder')" />
            </label>
            <div v-if="writeError" class="form-error">{{ writeError }}</div>
          </div>
          <div class="modal__foot">
            <button class="btn-ghost" type="button" @click="closeWrite">{{ t('common.cancel') }}</button>
            <button class="btn-primary" type="button" :disabled="writing" @click="submitWrite">
              <AppIcon name="check" :size="14" />
              <span>{{ writing ? t('view.blackboard.write.writing') : t('view.blackboard.write.submit') }}</span>
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
import { useConfirm } from '../composables/useConfirm.js';
import { useI18n } from '../i18n';
import ListPageLayout from '../components/ListPageLayout.vue';
import AppIcon from '../components/AppIcon.vue';
import Card from '../components/Card.vue';
import Badge from '../components/Badge.vue';
import Segmented from '../components/Segmented.vue';
import Skeleton from '../components/Skeleton.vue';
import EmptyState from '../components/EmptyState.vue';
import FilterBar from '../components/FilterBar.vue';

const api = useApiStore();
const { t } = useI18n();
const toast = useToast();
const { showConfirm } = useConfirm();

// ── Tab state ───────────────────────────────────────────────────
const activeTab = ref('snapshot');
const tabOptions = [
  { value: 'snapshot', label: t('view.blackboard.tab.snapshot'), icon: 'database' },
  { value: 'domains', label: t('view.blackboard.tab.domains'), icon: 'box' },
  { value: 'history', label: t('view.blackboard.tab.history'), icon: 'clock' },
  { value: 'stats', label: t('view.blackboard.tab.stats'), icon: 'activity' },
];

// ── Data state ──────────────────────────────────────────────────
const loading = ref(false);
const errors = ref({ snapshot: null, domains: null, history: null, stats: null });

const snapshot = ref({});          // { domain: [entry, ...] }
const allowedDomains = ref([]);    // whitelist from /domains
const activeDomains = ref([]);     // active domains from /domains
const domainEntries = ref([]);     // entries for selected domain
const history = ref([]);
const stats = ref({});

// ── Domain filter ───────────────────────────────────────────────
const domainFilters = reactive({ domain: '' });
const selectedDomain = ref('');

const domainFilterSchema = computed(() => [
  {
    key: 'domain',
    label: t('view.blackboard.domains.select'),
    options: allowedDomains.value.map((d) => ({ value: d, label: d })),
  },
]);

const domainResultsLabel = computed(() => `${domainEntries.value.length}`);

// ── Snapshot helpers ────────────────────────────────────────────
const snapshotDomains = computed(() => Object.keys(snapshot.value || {}).filter((d) => Array.isArray(snapshot.value[d]) && snapshot.value[d].length));

function selectDomainFromSnapshot(dom) {
  selectedDomain.value = dom;
  domainFilters.domain = dom;
  activeTab.value = 'domains';
  loadDomainEntries(dom);
}

// ── Stats helpers ───────────────────────────────────────────────
const hasStats = computed(() => {
  const s = stats.value || {};
  return (s.total_entries != null) || (s.active_domains && s.active_domains.length) || (s.allowed_domains && s.allowed_domains.length);
});

// ── Write modal ─────────────────────────────────────────────────
const showWriteModal = ref(false);
const writing = ref(false);
const writeError = ref('');
const writeForm = reactive({
  domain: '',
  contentRaw: '',
  contributor: '',
  confidence: 1.0,
  metadataRaw: '',
});

function openWrite() {
  writeForm.domain = selectedDomain.value || '';
  writeForm.contentRaw = '';
  writeForm.contributor = '';
  writeForm.confidence = 1.0;
  writeForm.metadataRaw = '';
  writeError.value = '';
  showWriteModal.value = true;
}

function closeWrite() {
  showWriteModal.value = false;
}

function parseContent(raw) {
  const s = String(raw || '').trim();
  if (!s) return null;
  // Try JSON first; fall back to raw string
  try { return JSON.parse(s); } catch { return s; }
}

function parseMetadata(raw) {
  const s = String(raw || '').trim();
  if (!s) return {};
  try { return JSON.parse(s); } catch { return null; /* invalid */ }
}

async function submitWrite() {
  writeError.value = '';
  if (!writeForm.domain) {
    writeError.value = t('view.blackboard.validate.domainRequired');
    return;
  }
  const content = parseContent(writeForm.contentRaw);
  if (content === null || content === '') {
    writeError.value = t('view.blackboard.validate.contentRequired');
    return;
  }
  const confidence = Number(writeForm.confidence);
  if (isNaN(confidence) || confidence < 0 || confidence > 1) {
    writeError.value = t('view.blackboard.validate.confidenceRange');
    return;
  }
  const metadata = parseMetadata(writeForm.metadataRaw);
  if (metadata === null) {
    writeError.value = t('view.blackboard.validate.metadataInvalid');
    return;
  }
  writing.value = true;
  try {
    const payload = {
      domain: writeForm.domain,
      content,
      contributor: writeForm.contributor.trim(),
      confidence,
      metadata,
    };
    await api.post('/api/blackboard/write', payload);
    toast.success(t('view.blackboard.write.success'));
    showWriteModal.value = false;
    // Refresh affected views
    await Promise.allSettled([loadSnapshot(), loadDomainEntries(writeForm.domain), loadStats()]);
  } catch (err) {
    writeError.value = (err && err.message) || t('view.blackboard.write.failed');
  } finally {
    writing.value = false;
  }
}

// ── Clear domain ────────────────────────────────────────────────
async function clearDomain() {
  const dom = selectedDomain.value || domainFilters.domain;
  if (!dom) return;
  const ok = await showConfirm({
    message: t('view.blackboard.clear.confirm', { domain: dom }),
    tone: 'danger',
  });
  if (!ok) return;
  try {
    const data = await api.post(`/api/blackboard/clear/${encodeURIComponent(dom)}`, {});
    const cleared = (data && data.cleared) || 0;
    toast.success(t('view.blackboard.clear.success', { count: cleared }));
    domainEntries.value = [];
    await Promise.allSettled([loadSnapshot(), loadStats()]);
  } catch (err) {
    toast.error((err && err.message) || t('view.blackboard.clear.failed'));
  }
}

// ── Helpers ─────────────────────────────────────────────────────
function formatContent(c) {
  if (c == null) return '—';
  if (typeof c === 'string') return c;
  try { return JSON.stringify(c, null, 2); } catch { return String(c); }
}

function formatConfidence(v) {
  if (v == null) return '—';
  const n = Number(v);
  if (isNaN(n)) return '—';
  return n.toFixed(2);
}

function confidenceTone(v) {
  const n = Number(v);
  if (isNaN(n)) return 'neutral';
  if (n >= 0.8) return 'success';
  if (n >= 0.5) return 'warn';
  return 'fail';
}

function historyTone(h) {
  const op = String(h.op || h.operation || h.action || '').toLowerCase();
  if (/(write|create|add|set)/.test(op)) return 'success';
  if (/(clear|delete|remove|reset)/.test(op)) return 'fail';
  if (/(update|modify|edit)/.test(op)) return 'warn';
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
async function loadSnapshot() {
  try {
    const data = await api.get('/api/blackboard/snapshot');
    snapshot.value = (data && data.data) || {};
    errors.value = { ...errors.value, snapshot: null };
  } catch (err) {
    snapshot.value = {};
    errors.value = { ...errors.value, snapshot: (err && err.message) || t('view.blackboard.snapshot.failedLoad') };
  }
}

async function loadDomains() {
  try {
    const data = await api.get('/api/blackboard/domains');
    const d = (data && data.data) || {};
    allowedDomains.value = d.allowed || [];
    activeDomains.value = d.active || [];
    errors.value = { ...errors.value, domains: null };
    // Auto-select first active domain if none selected
    if (!selectedDomain.value && activeDomains.value.length) {
      selectedDomain.value = activeDomains.value[0];
      domainFilters.domain = selectedDomain.value;
      await loadDomainEntries(selectedDomain.value);
    } else if (selectedDomain.value) {
      await loadDomainEntries(selectedDomain.value);
    }
  } catch (err) {
    allowedDomains.value = [];
    errors.value = { ...errors.value, domains: (err && err.message) || t('view.blackboard.domains.failedLoad') };
  }
}

async function loadDomainEntries(dom) {
  if (!dom) {
    domainEntries.value = [];
    return;
  }
  try {
    const data = await api.get(`/api/blackboard/domains/${encodeURIComponent(dom)}`);
    domainEntries.value = (data && data.data) || [];
    errors.value = { ...errors.value, domains: null };
  } catch (err) {
    domainEntries.value = [];
    errors.value = { ...errors.value, domains: (err && err.message) || t('view.blackboard.domains.failedLoad') };
  }
}

async function loadHistory() {
  try {
    const data = await api.get('/api/blackboard/history?limit=100');
    history.value = (data && data.data) || [];
    errors.value = { ...errors.value, history: null };
  } catch (err) {
    history.value = [];
    errors.value = { ...errors.value, history: (err && err.message) || t('view.blackboard.history.failedLoad') };
  }
}

async function loadStats() {
  try {
    const data = await api.get('/api/blackboard/stats');
    stats.value = (data && data.data) || {};
    errors.value = { ...errors.value, stats: null };
  } catch (err) {
    stats.value = {};
    errors.value = { ...errors.value, stats: (err && err.message) || t('view.blackboard.stats.failedLoad') };
  }
}

async function loadAll() {
  loading.value = true;
  await Promise.allSettled([loadSnapshot(), loadDomains(), loadHistory(), loadStats()]);
  loading.value = false;
}

// React to domain filter changes
import { watch } from 'vue';
watch(() => domainFilters.domain, async (dom) => {
  selectedDomain.value = dom;
  if (dom) {
    await loadDomainEntries(dom);
  } else {
    domainEntries.value = [];
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

/* ── Snapshot grid ─────────────────────────────────────────────── */
.snap-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: var(--sp-3);
}
.snap-card {
  padding: var(--sp-3);
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: var(--r-md);
  cursor: pointer;
  transition: border-color var(--motion) var(--ease), background var(--motion) var(--ease);
}
.snap-card:hover { border-color: var(--brand); background: var(--surface); }
.snap-card__head { display: flex; align-items: center; gap: var(--sp-2); margin-bottom: var(--sp-2); }
.snap-card__icon { color: var(--brand-strong); }
.snap-card__domain { font-size: var(--fs-sm); font-weight: 600; color: var(--text); }
.snap-card__count { display: flex; align-items: baseline; gap: 6px; }
.snap-card__num { font-size: var(--fs-xl); font-weight: 700; color: var(--text); font-variant-numeric: tabular-nums; }
.snap-card__label { font-size: var(--fs-xs); color: var(--text-muted); }

/* ── Domain table ──────────────────────────────────────────────── */
.bb-table {
  width: 100%;
  border: 1px solid var(--border-subtle);
  border-radius: var(--r-md);
  overflow: hidden;
}
.bb-table__head,
.bb-table__row {
  display: grid;
  grid-template-columns: 1fr 140px 90px 130px;
  align-items: center;
  gap: var(--sp-2);
}
.bb-table__head {
  background: var(--surface-2);
  padding: var(--sp-2) var(--sp-3);
  border-bottom: 1px solid var(--border-strong);
}
.bb-table__th {
  font-size: var(--fs-xs);
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: .03em;
  color: var(--text-muted);
}
.bb-table__th--num { text-align: right; }
.bb-table__row {
  padding: var(--sp-3);
  border-bottom: 1px solid var(--border-subtle);
  transition: background var(--motion) var(--ease);
}
.bb-table__row:last-child { border-bottom: none; }
.bb-table__row:hover { background: var(--surface-2); }
.bb-table__td { min-width: 0; }
.bb-table__td--num { text-align: right; }
.bb-table__content {
  margin: 0;
  max-height: 80px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-word;
  color: var(--text);
  background: var(--surface);
  padding: var(--sp-2);
  border-radius: var(--r-sm);
  border: 1px solid var(--border-subtle);
}
.bb-table__time { color: var(--text-muted); white-space: nowrap; font-size: var(--fs-sm); }

.domain-clear { display: flex; justify-content: flex-end; margin-top: var(--sp-3); }

/* ── History list ──────────────────────────────────────────────── */
.hist-list { display: flex; flex-direction: column; gap: var(--sp-2); }
.hist-item {
  padding: var(--sp-3);
  background: var(--surface-2);
  border: 1px solid var(--border-subtle);
  border-radius: var(--r-md);
}
.hist-item__head { display: flex; align-items: center; gap: var(--sp-2); flex-wrap: wrap; }
.hist-item__actor { font-size: var(--fs-sm); }
.hist-item__time { font-size: var(--fs-xs); color: var(--text-faint); margin-left: auto; }
.hist-item__target { margin-top: var(--sp-1); font-size: var(--fs-sm); display: flex; align-items: center; gap: 6px; }

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
.stats-card--wide { grid-column: 1 / -1; }
.stats-card__label { font-size: var(--fs-xs); color: var(--text-muted); text-transform: uppercase; letter-spacing: .04em; }
.stats-card__value { font-size: var(--fs-xl); font-weight: 700; color: var(--text); font-variant-numeric: tabular-nums; }
.stats-card__domains { display: flex; flex-wrap: wrap; gap: 6px; }

/* ── Buttons ───────────────────────────────────────────────────── */
/* NOTE: .btn-ghost/.btn-primary/.modal 等为多 view 共享样式，重复定义见
   AgentBilling/AgentGateway/AgentProxy/AgentRegistry/Analysis/Debate/Feedback。
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
.btn-danger-ghost {
  display: inline-flex; align-items: center; gap: 6px;
  background: transparent; color: var(--fail);
  border: 1px solid var(--fail); border-radius: var(--r-md);
  padding: 6px var(--sp-3); font-size: var(--fs-sm); font-weight: 600;
  cursor: pointer; transition: opacity var(--motion) var(--ease);
}
.btn-danger-ghost:hover { opacity: .85; }

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
.field { display: flex; flex-direction: column; gap: var(--sp-1); font-size: var(--fs-sm); font-weight: 600; color: var(--text-muted); }
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
.field__select { cursor: pointer; }
.field-row { display: grid; grid-template-columns: 1fr 1fr; gap: var(--sp-3); }
.form-error { color: var(--fail); font-size: var(--fs-sm); }

/* ── Responsive ────────────────────────────────────────────────── */
@media (max-width: 900px) {
  .bb-table__head,
  .bb-table__row {
    grid-template-columns: 1fr 120px 100px;
  }
  .bb-table__th:nth-child(4),
  .bb-table__td:nth-child(4) { display: none; }
  .field-row { grid-template-columns: 1fr; }
}
@media (max-width: 640px) {
  .bb-table__head,
  .bb-table__row {
    grid-template-columns: 1fr 100px;
  }
  .bb-table__th:nth-child(3),
  .bb-table__td:nth-child(3) { display: none; }
  .snap-grid { grid-template-columns: 1fr; }
  .stats-grid { grid-template-columns: 1fr; }
}
</style>
