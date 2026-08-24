<template>
  <div class="fm-page">
    <PageHeader>
      <button class="btn-refresh" :disabled="loading" @click="refreshActiveTab">
        <AppIcon name="refresh" :size="15" :class="{ spinning: loading }" /> {{ t('view.funnelMemory.refresh') }}
      </button>
    </PageHeader>

    <!-- Tabs -->
    <Segmented
      v-model="activeTab"
      :options="tabOptions"
      class="fm-tabs"
    />

    <!-- Error banner -->
    <div v-if="error" class="fm-error-banner">
      <AppIcon name="alert-triangle" :size="14" /> {{ error }}
    </div>

    <!-- ── Tab 1: L0 证据 ───────────────────────────────────── -->
    <section v-if="activeTab === 'evidence'" class="fm-tab">
      <div class="fm-stats-row">
        <StatCard
          :label="t('view.funnelMemory.l0Total')"
          :value="l0Stats.total"
          icon="database"
          tone="brand"
          :loading="loading"
        />
        <StatCard
          :label="t('view.funnelMemory.spilled')"
          :value="l0Stats.spilled"
          icon="archive"
          tone="warn"
          :loading="loading"
        />
        <StatCard
          :label="t('view.funnelMemory.totalChars')"
          :value="l0Stats.totalChars"
          icon="file-text"
          tone="info"
          :loading="loading"
        />
        <StatCard
          :label="t('view.funnelMemory.symbolicSessions')"
          :value="symbolicStats.sessions"
          icon="route"
          tone="success"
          :loading="loading"
        />
      </div>

      <div class="fm-breakdown">
        <Card :title="t('view.funnelMemory.byKind')" icon="box" :margin-bottom="16">
          <div v-if="kindEntries.length" class="fm-chip-list">
            <button
              v-for="k in kindEntries"
              :key="k.key"
              class="fm-chip"
              :class="{ active: evidenceKindFilter === k.key }"
              @click="toggleKindFilter(k.key)"
            >
              {{ k.key || '(empty)' }} <span class="fm-chip-count">{{ k.value }}</span>
            </button>
          </div>
          <EmptyState
            v-else
            icon="box"
            :title="t('view.funnelMemory.evidenceEmpty')"
            :description="t('view.funnelMemory.evidenceEmptyDesc')"
          />
        </Card>
        <Card :title="t('view.funnelMemory.bySession')" icon="route" :margin-bottom="16">
          <div v-if="sessionEntries.length" class="fm-chip-list">
            <button
              v-for="s in sessionEntries"
              :key="s.key"
              class="fm-chip"
              :class="{ active: evidenceSessionFilter === s.key }"
              @click="toggleSessionFilter(s.key)"
            >
              {{ s.key || '(empty)' }} <span class="fm-chip-count">{{ s.value }}</span>
            </button>
          </div>
          <EmptyState
            v-else
            icon="route"
            :title="t('view.funnelMemory.evidenceEmpty')"
            :description="t('view.funnelMemory.evidenceEmptyDesc')"
          />
        </Card>
      </div>

      <Card :title="t('view.funnelMemory.evidenceList')" icon="scroll" :margin-bottom="16">
        <template #actions>
          <button class="btn-danger" :disabled="pruning" @click="openPruneModal">
            <AppIcon name="trash" :size="14" /> {{ t('view.funnelMemory.prune') }}
          </button>
        </template>

        <div v-if="evidence.length" class="fm-table-wrap">
          <table class="fm-table">
            <thead>
              <tr>
                <th>{{ t('view.funnelMemory.colRefId') }}</th>
                <th>{{ t('view.funnelMemory.colSession') }}</th>
                <th>{{ t('view.funnelMemory.colKind') }}</th>
                <th>{{ t('view.funnelMemory.colCreatedAt') }}</th>
                <th>{{ t('view.funnelMemory.colSummary') }}</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="e in evidence" :key="e.ref_id">
                <td class="fm-mono">{{ e.ref_id }}</td>
                <td class="fm-mono fm-truncate" :title="e.session_id">{{ e.session_id || '—' }}</td>
                <td><Badge v-if="e.kind" tone="info">{{ e.kind }}</Badge><span v-else>—</span></td>
                <td class="fm-mono">{{ formatTime(e.created_at) }}</td>
                <td class="fm-summary">{{ e.summary || '—' }}</td>
                <td>
                  <button class="btn-link" @click="viewEvidenceDetail(e.ref_id)">
                    <AppIcon name="external" :size="13" /> {{ t('view.funnelMemory.viewDetail') }}
                  </button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <EmptyState
          v-else-if="!loading"
          icon="scroll"
          :title="t('view.funnelMemory.evidenceEmpty')"
          :description="t('view.funnelMemory.evidenceEmptyDesc')"
        />
        <Skeleton v-else height="200px" />

        <Pagination
          v-if="evidenceTotal > 0"
          :limit="evidenceLimit"
          :offset="evidenceOffset"
          :total="evidenceTotal"
          :loading="loading"
          :prev-label="t('view.funnelMemory.prev')"
          :next-label="t('view.funnelMemory.next')"
          :page-label="t('view.funnelMemory.page')"
          @change="onEvidencePageChange"
        />
      </Card>
    </section>

    <!-- ── Tab 2: L1 原子事实 ───────────────────────────────── -->
    <section v-else-if="activeTab === 'facts'" class="fm-tab">
      <div class="fm-stats-row">
        <StatCard
          :label="t('view.funnelMemory.l1Total')"
          :value="l1Stats.total"
          icon="database"
          tone="brand"
          :loading="loading"
        />
        <StatCard
          :label="t('view.funnelMemory.symbolicNodes')"
          :value="symbolicStats.nodes"
          icon="activity"
          tone="warn"
          :loading="loading"
        />
        <StatCard
          :label="t('view.funnelMemory.byTopic')"
          :value="topicEntries.length"
          icon="scroll"
          tone="info"
          :loading="loading"
        />
        <StatCard
          :label="t('view.funnelMemory.colAccess')"
          :value="totalAccess"
          icon="zap"
          tone="success"
          :loading="loading"
        />
      </div>

      <Card :title="t('view.funnelMemory.byTopic')" icon="scroll" :margin-bottom="16">
        <div v-if="topicEntries.length" class="fm-chip-list">
          <button
            v-for="tp in topicEntries"
            :key="tp.key"
            class="fm-chip"
            :class="{ active: factsTopicFilter === tp.key }"
            @click="toggleTopicFilter(tp.key)"
          >
            {{ tp.key || '(empty)' }} <span class="fm-chip-count">{{ tp.value }}</span>
          </button>
        </div>
        <EmptyState
          v-else
          icon="scroll"
          :title="t('view.funnelMemory.factsEmpty')"
          :description="t('view.funnelMemory.factsEmptyDesc')"
        />
      </Card>

      <Card :title="t('view.funnelMemory.factsList')" icon="database" :margin-bottom="16">
        <template #actions>
          <button
            v-if="selectedFactIds.size > 0"
            class="btn-primary"
            :disabled="promoting"
            @click="promoteSelected"
          >
            <AppIcon name="arrow-up" :size="14" /> {{ t('view.funnelMemory.promote') }} ({{ selectedFactIds.size }})
          </button>
          <button
            v-else
            class="btn-primary"
            :disabled="promoting"
            @click="promoteByAccess"
          >
            <AppIcon name="arrow-up" :size="14" /> {{ t('view.funnelMemory.promote') }}
          </button>
        </template>

        <div class="fm-search-bar">
          <span class="fm-search-icon"><AppIcon name="search" :size="16" /></span>
          <input
            v-model="factsQuery"
            :placeholder="t('view.funnelMemory.searchPlaceholder')"
            @keyup.enter="searchFacts"
          />
          <button class="fm-search-btn" :disabled="loading" @click="searchFacts">
            <AppIcon name="search" :size="15" /> {{ t('common.search') }}
          </button>
          <button v-if="factsQuery" class="btn-link" @click="clearFactsSearch">
            <AppIcon name="x" :size="14" />
          </button>
        </div>

        <div v-if="facts.length" class="fm-table-wrap">
          <table class="fm-table">
            <thead>
              <tr>
                <th class="fm-col-check">
                  <input
                    type="checkbox"
                    :checked="allFactsSelected"
                    :indeterminate.prop="someFactsSelected"
                    @change="toggleSelectAllFacts"
                  />
                </th>
                <th>{{ t('view.funnelMemory.colSubject') }}</th>
                <th>{{ t('view.funnelMemory.colPredicate') }}</th>
                <th>{{ t('view.funnelMemory.colObject') }}</th>
                <th>{{ t('view.funnelMemory.colTopic') }}</th>
                <th>{{ t('view.funnelMemory.colConfidence') }}</th>
                <th>{{ t('view.funnelMemory.colAccess') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="f in facts" :key="f.id">
                <td class="fm-col-check">
                  <input
                    type="checkbox"
                    :checked="selectedFactIds.has(f.id)"
                    @change="toggleFactSelection(f.id)"
                  />
                </td>
                <td class="fm-truncate" :title="f.subject">{{ f.subject || '—' }}</td>
                <td class="fm-truncate" :title="f.predicate">{{ f.predicate || '—' }}</td>
                <td class="fm-truncate" :title="f.object_value">{{ f.object_value || '—' }}</td>
                <td><Badge v-if="f.topic" tone="brand">{{ f.topic }}</Badge><span v-else>—</span></td>
                <td class="fm-mono">{{ formatConfidence(f.confidence) }}</td>
                <td class="fm-mono">{{ f.access_count ?? 0 }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <EmptyState
          v-else-if="!loading"
          icon="database"
          :title="t('view.funnelMemory.factsEmpty')"
          :description="t('view.funnelMemory.factsEmptyDesc')"
        />
        <Skeleton v-else height="200px" />

        <Pagination
          v-if="factsTotal > 0 && !factsSearchMode"
          :limit="factsLimit"
          :offset="factsOffset"
          :total="factsTotal"
          :loading="loading"
          :prev-label="t('view.funnelMemory.prev')"
          :next-label="t('view.funnelMemory.next')"
          :page-label="t('view.funnelMemory.page')"
          @change="onFactsPageChange"
        />
      </Card>
    </section>

    <!-- ── Tab 3: 任务状态图 ─────────────────────────────────── -->
    <section v-else-if="activeTab === 'taskmap'" class="fm-tab">
      <Card :title="t('view.funnelMemory.sessionPicker')" icon="route" :margin-bottom="16">
        <div class="fm-session-bar">
          <input
            v-model="taskSessionId"
            :placeholder="t('view.funnelMemory.sessionPlaceholder')"
            class="fm-input"
            @keyup.enter="loadTaskMap"
          />
          <button class="btn-primary" :disabled="loading || !taskSessionId.trim()" @click="loadTaskMap">
            <AppIcon name="search" :size="14" /> {{ t('view.funnelMemory.loadMap') }}
          </button>
        </div>
      </Card>

      <Card :title="t('view.funnelMemory.mermaidSource')" icon="git-branch" :margin-bottom="16">
        <template #actions>
          <button
            v-if="mermaidSource"
            class="btn-link"
            @click="copyMermaid"
          >
            <AppIcon name="clipboard" :size="14" /> {{ copied ? t('view.funnelMemory.copied') : t('view.funnelMemory.copyMermaid') }}
          </button>
        </template>
        <div v-if="mermaidSource" class="fm-mermaid-wrap">
          <pre class="fm-mermaid-pre">{{ mermaidSource }}</pre>
        </div>
        <EmptyState
          v-else-if="!loading"
          icon="git-branch"
          :title="t('view.funnelMemory.mermaidEmpty')"
          :description="t('view.funnelMemory.mermaidEmptyDesc')"
        />
        <Skeleton v-else height="200px" />
      </Card>

      <Card :title="t('view.funnelMemory.colNodeId')" icon="activity" :margin-bottom="16">
        <div v-if="taskNodes.length" class="fm-table-wrap">
          <table class="fm-table">
            <thead>
              <tr>
                <th>{{ t('view.funnelMemory.colNodeId') }}</th>
                <th>{{ t('view.funnelMemory.colStatus') }}</th>
                <th>{{ t('view.funnelMemory.colDescription') }}</th>
                <th>{{ t('view.funnelMemory.colParent') }}</th>
                <th>{{ t('view.funnelMemory.colEvidenceRef') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="n in taskNodes" :key="n.node_id">
                <td class="fm-mono">{{ n.node_id }}</td>
                <td><Badge :tone="statusTone(n.status)">{{ n.status || '—' }}</Badge></td>
                <td class="fm-summary">{{ n.description || '—' }}</td>
                <td class="fm-mono fm-truncate" :title="n.parent_id">{{ n.parent_id || '—' }}</td>
                <td class="fm-mono fm-truncate" :title="n.evidence_ref">{{ n.evidence_ref || '—' }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <EmptyState
          v-else-if="!loading"
          icon="activity"
          :title="t('view.funnelMemory.nodesEmpty')"
        />
        <Skeleton v-else height="160px" />
      </Card>
    </section>

    <!-- ── Prune Modal ───────────────────────────────────────── -->
    <Teleport to="body">
      <div
        v-if="showPruneModal"
        v-modal-a11y
        class="modal-mask"
        @click.self="showPruneModal = false"
        @modal:escape="showPruneModal = false"
      >
        <div class="modal" role="dialog" aria-modal="true">
          <div class="modal__head">
            <h3>{{ t('view.funnelMemory.prune') }}</h3>
            <button class="modal__x" type="button" aria-label="Close" @click="showPruneModal = false">×</button>
          </div>
          <div class="modal__body">
            <label class="field">
              <span class="field__label">{{ t('view.funnelMemory.pruneDays') }}</span>
              <input v-model.number="pruneForm.older_than_days" type="number" min="0" class="field__input" />
            </label>
            <label class="field">
              <span class="field__label">{{ t('view.funnelMemory.pruneSession') }}</span>
              <input v-model="pruneForm.session_id" class="field__input" />
            </label>
            <label class="field">
              <span class="field__label">{{ t('view.funnelMemory.pruneKind') }}</span>
              <input v-model="pruneForm.kind" class="field__input" />
            </label>
          </div>
          <div class="modal__foot">
            <button class="btn-ghost" type="button" @click="showPruneModal = false">{{ t('common.cancel') }}</button>
            <button class="btn-danger" type="button" :disabled="pruning" @click="confirmPrune">
              {{ pruning ? t('view.funnelMemory.loading') : t('view.funnelMemory.prune') }}
            </button>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- ── Evidence Detail Modal ─────────────────────────────── -->
    <Teleport to="body">
      <div
        v-if="evidenceDetail"
        v-modal-a11y
        class="modal-mask"
        @click.self="evidenceDetail = null"
        @modal:escape="evidenceDetail = null"
      >
        <div class="modal modal--wide" role="dialog" aria-modal="true">
          <div class="modal__head">
            <h3>{{ t('view.funnelMemory.detail') }}: {{ evidenceDetail.ref_id }}</h3>
            <button class="modal__x" type="button" aria-label="Close" @click="evidenceDetail = null">×</button>
          </div>
          <div class="modal__body">
            <div class="fm-detail-grid">
              <div class="fm-detail-row">
                <span class="fm-detail-label">{{ t('view.funnelMemory.colSession') }}</span>
                <span class="fm-mono">{{ evidenceDetail.session_id || '—' }}</span>
              </div>
              <div class="fm-detail-row">
                <span class="fm-detail-label">{{ t('view.funnelMemory.colKind') }}</span>
                <Badge v-if="evidenceDetail.kind" tone="info">{{ evidenceDetail.kind }}</Badge>
                <span v-else>—</span>
              </div>
              <div class="fm-detail-row">
                <span class="fm-detail-label">{{ t('view.funnelMemory.colCreatedAt') }}</span>
                <span class="fm-mono">{{ formatTime(evidenceDetail.created_at) }}</span>
              </div>
              <div class="fm-detail-row">
                <span class="fm-detail-label">{{ t('view.funnelMemory.colSummary') }}</span>
                <span>{{ evidenceDetail.summary || '—' }}</span>
              </div>
            </div>
            <div class="fm-detail-section">
              <div class="fm-detail-label">{{ t('view.funnelMemory.content') }}</div>
              <pre class="fm-detail-pre">{{ evidenceDetail.content || '—' }}</pre>
            </div>
            <div v-if="evidenceDetail.metadata && Object.keys(evidenceDetail.metadata).length" class="fm-detail-section">
              <div class="fm-detail-label">{{ t('view.funnelMemory.metadata') }}</div>
              <pre class="fm-detail-pre">{{ JSON.stringify(evidenceDetail.metadata, null, 2) }}</pre>
            </div>
          </div>
          <div class="modal__foot">
            <button class="btn-ghost" type="button" @click="evidenceDetail = null">{{ t('view.funnelMemory.close') }}</button>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, watch } from 'vue';
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
import Segmented from '../components/Segmented.vue';
import Pagination from '../components/Pagination.vue';

const { t } = useI18n();
const api = useApiStore();
const toast = useToast();

// ── Tab state ──────────────────────────────────────────────────
const activeTab = ref('evidence');
const tabOptions = computed(() => [
  { value: 'evidence', label: t('view.funnelMemory.tabEvidence'), icon: 'scroll' },
  { value: 'facts', label: t('view.funnelMemory.tabFacts'), icon: 'database' },
  { value: 'taskmap', label: t('view.funnelMemory.tabTaskMap'), icon: 'git-branch' },
]);

const loading = ref(false);
const error = ref('');

// ── Stats ──────────────────────────────────────────────────────
const l0Stats = reactive({ total: 0, by_kind: {}, spilled: 0, total_chars: 0 });
const l1Stats = reactive({ total: 0, by_topic: {}, top_facts: [] });
const symbolicStats = reactive({ sessions: 0, nodes: 0, by_status: {} });

const kindEntries = computed(() => Object.entries(l0Stats.by_kind || {}).map(([k, v]) => ({ key: k, value: v })));
const sessionEntries = computed(() => Object.entries(l0Stats.by_session || {}).map(([k, v]) => ({ key: k, value: v })));
const topicEntries = computed(() => Object.entries(l1Stats.by_topic || {}).map(([k, v]) => ({ key: k, value: v })));
const totalAccess = computed(() => (l1Stats.top_facts || []).reduce((sum, f) => sum + (f.access_count || 0), 0));

// ── L0 Evidence ────────────────────────────────────────────────
const evidence = ref([]);
const evidenceTotal = ref(0);
const evidenceLimit = ref(20);
const evidenceOffset = ref(0);
const evidenceKindFilter = ref('');
const evidenceSessionFilter = ref('');

// ── L1 Facts ───────────────────────────────────────────────────
const facts = ref([]);
const factsTotal = ref(0);
const factsLimit = ref(20);
const factsOffset = ref(0);
const factsTopicFilter = ref('');
const factsQuery = ref('');
const factsSearchMode = ref(false);
const selectedFactIds = reactive(new Set());

const allFactsSelected = computed(() => facts.value.length > 0 && facts.value.every((f) => selectedFactIds.has(f.id)));
const someFactsSelected = computed(() => facts.value.some((f) => selectedFactIds.has(f.id)) && !allFactsSelected.value);

// ── Task Map ───────────────────────────────────────────────────
const taskSessionId = ref('');
const mermaidSource = ref('');
const taskNodes = ref([]);
const copied = ref(false);

// ── Prune modal ────────────────────────────────────────────────
const showPruneModal = ref(false);
const pruning = ref(false);
const pruneForm = reactive({ older_than_days: 90, session_id: '', kind: '' });

// ── Evidence detail modal ──────────────────────────────────────
const evidenceDetail = ref(null);

// ── Promote ────────────────────────────────────────────────────
const promoting = ref(false);

// ══════════════════════════════════════════════════════════════
// Helpers
// ══════════════════════════════════════════════════════════════
function formatTime(ts) {
  if (!ts) return '—';
  try {
    const d = new Date(String(ts));
    if (isNaN(d.getTime())) return String(ts);
    return d.toLocaleString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
  } catch {
    return String(ts);
  }
}

function formatConfidence(c) {
  if (c === null || c === undefined) return '—';
  const n = Number(c);
  if (isNaN(n)) return '—';
  return n.toFixed(3);
}

function statusTone(status) {
  return ({
    done: 'success',
    active: 'brand',
    todo: 'neutral',
    failed: 'fail',
  })[status] || 'neutral';
}

function withErrorHandling(label, fn) {
  return async (...args) => {
    try {
      return await fn(...args);
    } catch (e) {
      console.error(`[funnel-memory] ${label} failed:`, e);
      error.value = `${t('view.funnelMemory.loadFailed')}: ${(e && e.message) ? e.message : String(e)}`;
      return null;
    }
  };
}

// ══════════════════════════════════════════════════════════════
// Stats
// ══════════════════════════════════════════════════════════════
async function loadStats() {
  const data = await withErrorHandling('stats', () => api.get('/api/memory/funnel/stats'))();
  if (!data || data.status !== 'ok') return;
  const s = data.stats || {};
  const l0 = s.l0_evidence || {};
  l0Stats.total = l0.total || 0;
  l0Stats.by_kind = l0.by_kind || {};
  l0Stats.spilled = l0.spilled || 0;
  l0Stats.total_chars = l0.total_chars || 0;
  // by_session 不在 stats 中，从 evidence 列表推断（保留字段以兼容未来扩展）
  l0Stats.by_session = l0.by_session || {};

  const l1 = s.l1_atoms || {};
  l1Stats.total = l1.total || 0;
  l1Stats.by_topic = l1.by_topic || {};
  l1Stats.top_facts = l1.top_facts || [];

  const sym = s.symbolic || {};
  symbolicStats.sessions = sym.sessions || 0;
  symbolicStats.nodes = sym.nodes || 0;
  symbolicStats.by_status = sym.by_status || {};
}

// ══════════════════════════════════════════════════════════════
// L0 Evidence
// ══════════════════════════════════════════════════════════════
async function loadEvidence() {
  loading.value = true;
  error.value = '';
  const params = new URLSearchParams({
    limit: String(evidenceLimit.value),
    offset: String(evidenceOffset.value),
  });
  if (evidenceSessionFilter.value) params.set('session_id', evidenceSessionFilter.value);
  if (evidenceKindFilter.value) params.set('kind', evidenceKindFilter.value);

  const data = await withErrorHandling('evidence', () => api.get(`/api/memory/funnel/evidence?${params}`))();
  loading.value = false;
  if (!data || data.status !== 'ok') {
    evidence.value = [];
    evidenceTotal.value = 0;
    return;
  }
  evidence.value = data.items || [];
  evidenceTotal.value = data.total || 0;
}

function toggleKindFilter(kind) {
  evidenceKindFilter.value = evidenceKindFilter.value === kind ? '' : kind;
  evidenceOffset.value = 0;
  loadEvidence();
}

function toggleSessionFilter(sid) {
  evidenceSessionFilter.value = evidenceSessionFilter.value === sid ? '' : sid;
  evidenceOffset.value = 0;
  loadEvidence();
}

function onEvidencePageChange({ limit, offset }) {
  evidenceLimit.value = limit;
  evidenceOffset.value = offset;
  loadEvidence();
}

async function viewEvidenceDetail(refId) {
  const data = await withErrorHandling('evidence-detail', () => api.get(`/api/memory/funnel/evidence/${encodeURIComponent(refId)}`))();
  if (!data || data.status !== 'ok') return;
  evidenceDetail.value = data.evidence || null;
}

function openPruneModal() {
  pruneForm.older_than_days = 90;
  pruneForm.session_id = evidenceSessionFilter.value || '';
  pruneForm.kind = evidenceKindFilter.value || '';
  showPruneModal.value = true;
}

async function confirmPrune() {
  pruning.value = true;
  try {
    const body = {
      older_than_days: Number(pruneForm.older_than_days) || 90,
      session_id: pruneForm.session_id.trim(),
      kind: pruneForm.kind.trim(),
      limit: 1000,
    };
    const data = await api.post('/api/memory/funnel/evidence/prune', body);
    const deleted = (data && data.deleted) || 0;
    toast.success(t('view.funnelMemory.pruneSuccess', { n: deleted }));
    showPruneModal.value = false;
    await loadStats();
    await loadEvidence();
  } catch (e) {
    toast.error(`${t('view.funnelMemory.loadFailed')}: ${(e && e.message) ? e.message : String(e)}`);
  } finally {
    pruning.value = false;
  }
}

// ══════════════════════════════════════════════════════════════
// L1 Atom Facts
// ══════════════════════════════════════════════════════════════
async function loadFacts() {
  loading.value = true;
  error.value = '';
  const params = new URLSearchParams({
    limit: String(factsLimit.value),
    offset: String(factsOffset.value),
  });
  if (factsTopicFilter.value) params.set('topic', factsTopicFilter.value);

  const data = await withErrorHandling('facts', () => api.get(`/api/memory/funnel/facts?${params}`))();
  loading.value = false;
  if (!data || data.status !== 'ok') {
    facts.value = [];
    factsTotal.value = 0;
    return;
  }
  facts.value = data.items || [];
  factsTotal.value = data.total || 0;
  // 清理已不在列表中的选中项
  const validIds = new Set(facts.value.map((f) => f.id));
  for (const id of [...selectedFactIds]) {
    if (!validIds.has(id)) selectedFactIds.delete(id);
  }
}

async function searchFacts() {
  const q = factsQuery.value.trim();
  if (!q) {
    factsSearchMode.value = false;
    await loadFacts();
    return;
  }
  loading.value = true;
  error.value = '';
  factsSearchMode.value = true;
  const params = new URLSearchParams({
    query: q,
    limit: String(factsLimit.value),
  });
  if (factsTopicFilter.value) params.set('topic', factsTopicFilter.value);

  const data = await withErrorHandling('facts-search', () => api.get(`/api/memory/funnel/facts/search?${params}`))();
  loading.value = false;
  if (!data || data.status !== 'ok') {
    facts.value = [];
    return;
  }
  facts.value = data.items || [];
  factsTotal.value = data.count || facts.value.length;
}

function clearFactsSearch() {
  factsQuery.value = '';
  factsSearchMode.value = false;
  loadFacts();
}

function toggleTopicFilter(topic) {
  factsTopicFilter.value = factsTopicFilter.value === topic ? '' : topic;
  factsOffset.value = 0;
  if (factsSearchMode.value) searchFacts();
  else loadFacts();
}

function onFactsPageChange({ limit, offset }) {
  factsLimit.value = limit;
  factsOffset.value = offset;
  loadFacts();
}

function toggleFactSelection(id) {
  if (selectedFactIds.has(id)) selectedFactIds.delete(id);
  else selectedFactIds.add(id);
}

function toggleSelectAllFacts() {
  if (allFactsSelected.value) {
    for (const f of facts.value) selectedFactIds.delete(f.id);
  } else {
    for (const f of facts.value) selectedFactIds.add(f.id);
  }
}

async function promoteSelected() {
  if (selectedFactIds.size === 0) return;
  if (!window.confirm(t('view.funnelMemory.promoteConfirm', { n: selectedFactIds.size }))) return;
  promoting.value = true;
  try {
    const data = await api.post('/api/memory/funnel/facts/promote', {
      fact_ids: [...selectedFactIds],
    });
    const n = (data && data.promoted) || 0;
    toast.success(t('view.funnelMemory.promoteSuccess', { n }));
    selectedFactIds.clear();
    await loadStats();
    await loadFacts();
  } catch (e) {
    toast.error(`${t('view.funnelMemory.loadFailed')}: ${(e && e.message) ? e.message : String(e)}`);
  } finally {
    promoting.value = false;
  }
}

async function promoteByAccess() {
  if (!window.confirm(t('view.funnelMemory.promoteConfirm', { n: 0 }))) return;
  promoting.value = true;
  try {
    const data = await api.post('/api/memory/funnel/facts/promote', {
      fact_ids: [],
      min_access: 3,
      top: 50,
    });
    const n = (data && data.promoted) || 0;
    toast.success(t('view.funnelMemory.promoteSuccess', { n }));
    await loadStats();
    await loadFacts();
  } catch (e) {
    toast.error(`${t('view.funnelMemory.loadFailed')}: ${(e && e.message) ? e.message : String(e)}`);
  } finally {
    promoting.value = false;
  }
}

// ══════════════════════════════════════════════════════════════
// Task Map
// ══════════════════════════════════════════════════════════════
async function loadTaskMap() {
  const sid = taskSessionId.value.trim();
  if (!sid) return;
  loading.value = true;
  error.value = '';
  copied.value = false;
  const [mapData, nodesData] = await Promise.all([
    withErrorHandling('task-map', () => api.get(`/api/memory/funnel/task-map/${encodeURIComponent(sid)}`))(),
    withErrorHandling('task-map-nodes', () => api.get(`/api/memory/funnel/task-map/${encodeURIComponent(sid)}/nodes`))(),
  ]);
  loading.value = false;
  mermaidSource.value = (mapData && mapData.status === 'ok') ? (mapData.mermaid || '') : '';
  taskNodes.value = (nodesData && nodesData.status === 'ok') ? (nodesData.nodes || []) : [];
}

async function copyMermaid() {
  if (!mermaidSource.value) return;
  try {
    await navigator.clipboard.writeText(mermaidSource.value);
    copied.value = true;
    setTimeout(() => { copied.value = false; }, 2000);
  } catch {
    toast.error(t('view.funnelMemory.loadFailed'));
  }
}

// ══════════════════════════════════════════════════════════════
// Tab orchestration
// ══════════════════════════════════════════════════════════════
async function refreshActiveTab() {
  error.value = '';
  await loadStats();
  if (activeTab.value === 'evidence') await loadEvidence();
  else if (activeTab.value === 'facts') {
    if (factsSearchMode.value) await searchFacts();
    else await loadFacts();
  } else if (activeTab.value === 'taskmap' && taskSessionId.value.trim()) await loadTaskMap();
}

async function initTab(tab) {
  error.value = '';
  if (tab === 'evidence') {
    if (evidence.value.length === 0 || evidenceTotal.value === 0) await loadEvidence();
  } else if (tab === 'facts') {
    if (facts.value.length === 0) await loadFacts();
  } else if (tab === 'taskmap') {
    // 任务图需要用户输入 session ID，不自动加载
  }
}

watch(activeTab, (tab) => { initTab(tab); });

onMounted(async () => {
  await loadStats();
  await loadEvidence();
});
</script>

<style scoped>
.fm-page {
  display: flex;
  flex-direction: column;
  gap: var(--sp-4);
}

.fm-tabs {
  align-self: flex-start;
}

.fm-error-banner {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  background: var(--fail-soft);
  border: 1px solid var(--fail);
  border-radius: var(--r-md);
  color: var(--fail, #f85149);
  font-size: 12px;
  font-family: var(--font-mono);
  word-break: break-word;
}

.fm-stats-row {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: var(--sp-3);
}

.fm-breakdown {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--sp-4);
}

@media (max-width: 900px) {
  .fm-breakdown { grid-template-columns: 1fr; }
}

.fm-chip-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.fm-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: var(--r-full);
  font-size: var(--fs-xs);
  font-weight: 600;
  color: var(--text-muted);
  cursor: pointer;
  transition: background var(--motion) var(--ease), color var(--motion) var(--ease), border-color var(--motion) var(--ease);
}

.fm-chip:hover { color: var(--text); border-color: var(--border-strong); }

.fm-chip.active {
  background: var(--brand-soft);
  color: var(--brand-strong);
  border-color: color-mix(in srgb, var(--brand) 40%, transparent);
}

.fm-chip-count {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 18px;
  padding: 0 5px;
  height: 16px;
  background: var(--surface);
  border: 1px solid var(--border-subtle, var(--border));
  border-radius: var(--r-full);
  font-size: 10px;
  font-weight: 700;
  color: var(--text-muted);
}

/* ── Table ─────────────────────────────────────────────────── */
.fm-table-wrap {
  overflow-x: auto;
  margin: 0 calc(-1 * var(--sp-4));
  padding: 0 var(--sp-4);
}

.fm-table {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--fs-sm);
}

.fm-table thead th {
  text-align: left;
  padding: 8px 10px;
  font-weight: 600;
  color: var(--text-muted);
  border-bottom: 1px solid var(--border);
  white-space: nowrap;
  text-transform: uppercase;
  font-size: var(--fs-xs);
  letter-spacing: .04em;
}

.fm-table tbody td {
  padding: 8px 10px;
  border-bottom: 1px solid var(--border-subtle, var(--border));
  vertical-align: top;
}

.fm-table tbody tr:last-child td { border-bottom: none; }
.fm-table tbody tr:hover { background: var(--surface-2); }

.fm-mono { font-family: var(--font-mono); font-size: var(--fs-xs); }
.fm-truncate { max-width: 220px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.fm-summary { max-width: 360px; line-height: 1.45; color: var(--text-muted); }
.fm-col-check { width: 32px; text-align: center; }

/* ── Buttons ───────────────────────────────────────────────── */
.btn-refresh, .btn-primary, .btn-danger, .btn-ghost, .btn-link, .fm-search-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px;
  border-radius: var(--r-md);
  font-size: var(--fs-sm);
  font-weight: 600;
  cursor: pointer;
  border: 1px solid transparent;
  transition: background var(--motion) var(--ease), border-color var(--motion) var(--ease), color var(--motion) var(--ease);
}

.btn-refresh { background: var(--surface-2); border-color: var(--border); color: var(--text); }
.btn-refresh:hover { border-color: var(--border-strong); }
.btn-refresh:disabled { opacity: .5; cursor: not-allowed; }

.btn-primary { background: var(--brand); color: var(--brand-contrast); }
.btn-primary:hover { background: color-mix(in srgb, var(--brand) 88%, black); }
.btn-primary:disabled { opacity: .5; cursor: not-allowed; }

.btn-danger { background: var(--fail-soft); color: var(--fail); border-color: color-mix(in srgb, var(--fail) 30%, transparent); }
.btn-danger:hover { background: color-mix(in srgb, var(--fail) 18%, transparent); }
.btn-danger:disabled { opacity: .5; cursor: not-allowed; }

.btn-ghost { background: transparent; color: var(--text-muted); border-color: var(--border); }
.btn-ghost:hover { color: var(--text); border-color: var(--border-strong); }

.btn-link { background: transparent; color: var(--brand-strong); padding: 4px 8px; }
.btn-link:hover { color: var(--brand); }

.fm-search-btn { background: var(--brand); color: var(--brand-contrast); }
.fm-search-btn:hover { background: color-mix(in srgb, var(--brand) 88%, black); }
.fm-search-btn:disabled { opacity: .5; cursor: not-allowed; }

.spinning { animation: fm-spin 0.8s linear infinite; }
@keyframes fm-spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }

/* ── Search bar ────────────────────────────────────────────── */
.fm-search-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: var(--sp-3);
}

.fm-search-icon { color: var(--text-muted); display: inline-flex; }

.fm-search-bar input {
  flex: 1;
  padding: 6px 10px;
  border: 1px solid var(--border);
  border-radius: var(--r-md);
  background: var(--surface);
  color: var(--text);
  font-size: var(--fs-sm);
}

.fm-search-bar input:focus { outline: none; border-color: var(--brand); }

/* ── Session bar ───────────────────────────────────────────── */
.fm-session-bar {
  display: flex;
  gap: 8px;
}

.fm-input {
  flex: 1;
  padding: 6px 10px;
  border: 1px solid var(--border);
  border-radius: var(--r-md);
  background: var(--surface);
  color: var(--text);
  font-size: var(--fs-sm);
}

.fm-input:focus { outline: none; border-color: var(--brand); }

/* ── Mermaid ───────────────────────────────────────────────── */
.fm-mermaid-wrap {
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: var(--r-md);
  padding: var(--sp-3);
  overflow: auto;
  max-height: 480px;
}

.fm-mermaid-pre {
  margin: 0;
  font-family: var(--font-mono);
  font-size: var(--fs-xs);
  color: var(--text);
  white-space: pre-wrap;
  word-break: break-word;
  line-height: 1.55;
}

/* ── Modal ─────────────────────────────────────────────────── */
.modal-mask {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
  padding: var(--sp-4);
}

.modal {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r-lg);
  width: min(560px, 100%);
  max-height: 90vh;
  display: flex;
  flex-direction: column;
  box-shadow: var(--shadow-lg, 0 10px 40px rgba(0, 0, 0, 0.18));
}

.modal--wide { width: min(820px, 100%); }

.modal__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--sp-3) var(--sp-4);
  border-bottom: 1px solid var(--border);
}

.modal__head h3 { margin: 0; font-size: var(--fs-md); font-weight: 600; }

.modal__x {
  background: transparent;
  border: none;
  font-size: 20px;
  line-height: 1;
  color: var(--text-muted);
  cursor: pointer;
  padding: 4px 8px;
  border-radius: var(--r-sm);
}

.modal__x:hover { background: var(--surface-2); color: var(--text); }

.modal__body {
  padding: var(--sp-4);
  overflow: auto;
  display: flex;
  flex-direction: column;
  gap: var(--sp-3);
}

.modal__foot {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding: var(--sp-3) var(--sp-4);
  border-top: 1px solid var(--border);
}

.field { display: flex; flex-direction: column; gap: 4px; }
.field__label { font-size: var(--fs-xs); color: var(--text-muted); font-weight: 600; }
.field__input {
  padding: 6px 10px;
  border: 1px solid var(--border);
  border-radius: var(--r-md);
  background: var(--surface);
  color: var(--text);
  font-size: var(--fs-sm);
}
.field__input:focus { outline: none; border-color: var(--brand); }

/* ── Detail modal ──────────────────────────────────────────── */
.fm-detail-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--sp-3);
}

@media (max-width: 600px) {
  .fm-detail-grid { grid-template-columns: 1fr; }
}

.fm-detail-row {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.fm-detail-label {
  font-size: var(--fs-xs);
  color: var(--text-muted);
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: .04em;
}

.fm-detail-section {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.fm-detail-pre {
  margin: 0;
  padding: var(--sp-3);
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: var(--r-md);
  font-family: var(--font-mono);
  font-size: var(--fs-xs);
  color: var(--text);
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 320px;
  overflow: auto;
  line-height: 1.55;
}
</style>