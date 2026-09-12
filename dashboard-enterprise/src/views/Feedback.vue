<template>
  <div class="fb-view">
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
        <button class="btn-primary" type="button" @click="openSubmitModal">
          <AppIcon name="plus" :size="15" />
          <span>{{ t('view.feedback.list.add') }}</span>
        </button>
      </template>
      <template #content>
        <!-- ── Feedback List ─────────────────────────────────────── -->
        <div v-show="activeTab === 'list'">
          <Card :title="t('view.feedback.list.title')" icon="message-square" margin-bottom="var(--sp-4)">
            <template #actions>
              <button class="btn-ghost btn-ghost--sm" type="button" :disabled="exporting" @click="exportFeedback('csv')">
                <AppIcon name="download" :size="13" />
                <span>{{ t('view.feedback.list.exportCsv') }}</span>
              </button>
              <button class="btn-ghost btn-ghost--sm" type="button" :disabled="exporting" @click="exportFeedback('json')">
                <AppIcon name="download" :size="13" />
                <span>{{ t('view.feedback.list.exportJson') }}</span>
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
              :title="t('view.feedback.list.failedLoad')"
              :description="errors.list"
            />
            <EmptyState
              v-else-if="!feedbackRows.length"
              icon="message-square"
              :title="t('view.feedback.list.empty')"
              :description="t('view.feedback.list.emptyHint')"
            />
            <div v-else class="fb-table" role="table" :aria-label="t('view.feedback.list.title')">
              <div class="fb-table__head" role="row">
                <div class="fb-table__th" role="columnheader">{{ t('view.feedback.col.targetType') }}</div>
                <div class="fb-table__th" role="columnheader">{{ t('view.feedback.col.target') }}</div>
                <div class="fb-table__th fb-table__th--rating" role="columnheader">{{ t('view.feedback.col.rating') }}</div>
                <div class="fb-table__th fb-table__th--comment" role="columnheader">{{ t('view.feedback.col.comment') }}</div>
                <div class="fb-table__th fb-table__th--tags" role="columnheader">{{ t('view.feedback.col.tags') }}</div>
                <div class="fb-table__th fb-table__th--act" role="columnheader">{{ t('view.feedback.col.actions') }}</div>
              </div>
              <div v-for="f in feedbackRows" :key="f.feedback_id" class="fb-table__row" role="row">
                <div class="fb-table__td" role="cell">
                  <Badge :tone="targetTypeTone(f.target_type)">{{ targetTypeLabel(f.target_type) }}</Badge>
                </div>
                <div class="fb-table__td" role="cell">
                  <span class="fb-table__target mono">{{ f.target_id }}</span>
                </div>
                <div class="fb-table__td fb-table__td--rating" role="cell">
                  <span class="fb-rating" :class="ratingTone(f.rating)">
                    <AppIcon name="star" :size="13" />
                    <span>{{ f.rating }}</span>
                  </span>
                </div>
                <div class="fb-table__td fb-table__td--comment" role="cell">
                  <span class="fb-table__comment">{{ f.comment || '—' }}</span>
                </div>
                <div class="fb-table__td fb-table__td--tags" role="cell">
                  <template v-if="(f.tags || []).length">
                    <Badge v-for="tag in (f.tags || []).slice(0, 2)" :key="tag" tone="neutral">{{ tag }}</Badge>
                    <span v-if="(f.tags || []).length > 2" class="fb-more">+{{ f.tags.length - 2 }}</span>
                  </template>
                  <span v-else class="muted">—</span>
                </div>
                <div class="fb-table__td fb-table__td--act" role="cell">
                  <button class="icon-btn" type="button" :aria-label="t('common.edit')" @click="openEditModal(f)">
                    <AppIcon name="gear" :size="14" />
                  </button>
                  <button class="icon-btn icon-btn--danger" type="button" :aria-label="t('common.delete')" @click="confirmDelete(f)">
                    <AppIcon name="trash" :size="14" />
                  </button>
                </div>
              </div>
            </div>
            <div v-if="totalPages > 1" class="fb-pager" role="navigation">
              <button
                class="fb-pager__btn"
                type="button"
                :disabled="page === 1"
                @click="goPrev"
              >‹</button>
              <span class="fb-pager__info">{{ page }} / {{ totalPages }}</span>
              <button
                class="fb-pager__btn"
                type="button"
                :disabled="page === totalPages"
                @click="goNext"
              >›</button>
            </div>
          </Card>
        </div>

        <!-- ── Summary ───────────────────────────────────────────── -->
        <div v-show="activeTab === 'summary'">
          <Card :title="t('view.feedback.summary.title')" icon="activity" margin-bottom="var(--sp-4)">
            <div v-if="loading" class="blk">
              <Skeleton block height="120px" />
              <Skeleton block height="200px" />
            </div>
            <EmptyState
              v-else-if="errors.summary"
              icon="alert-triangle"
              tone="fail"
              :title="t('view.feedback.summary.failedLoad')"
              :description="errors.summary"
            />
            <EmptyState
              v-else-if="!hasSummaryData"
              icon="activity"
              :title="t('view.feedback.summary.empty')"
              :description="t('view.feedback.summary.emptyHint')"
            />
            <div v-else class="fb-summary">
              <div class="fb-kpi-grid">
                <div class="fb-kpi">
                  <span class="fb-kpi__label">{{ t('view.feedback.summary.total') }}</span>
                  <span class="fb-kpi__value">{{ summaryData.total ?? 0 }}</span>
                </div>
                <div class="fb-kpi">
                  <span class="fb-kpi__label">{{ t('view.feedback.summary.avgRating') }}</span>
                  <span class="fb-kpi__value" :class="avgRatingTone">{{ formatAvg(summaryData.average_rating) }}</span>
                </div>
                <div class="fb-kpi">
                  <span class="fb-kpi__label">{{ t('view.feedback.summary.commentCount') }}</span>
                  <span class="fb-kpi__value">{{ summaryData.comment_count ?? 0 }}</span>
                </div>
              </div>

              <div class="fb-distribution">
                <h4 class="fb-distribution__title">{{ t('view.feedback.summary.distribution') }}</h4>
                <div class="fb-distribution__bars">
                  <div v-for="r in [5, 4, 3, 2, 1]" :key="r" class="fb-distribution__row">
                    <span class="fb-distribution__label">
                      <AppIcon name="star" :size="13" />
                      <span>{{ r }}</span>
                    </span>
                    <div class="fb-distribution__bar">
                      <div class="fb-distribution__fill" :style="{ width: distributionPct(r) + '%' }"></div>
                    </div>
                    <span class="fb-distribution__count">{{ distributionCount(r) }}</span>
                  </div>
                </div>
              </div>

              <div v-if="tagFrequencyEntries.length" class="fb-tags">
                <h4 class="fb-tags__title">{{ t('view.feedback.summary.tagFrequency') }}</h4>
                <div class="fb-tags__cloud">
                  <Badge
                    v-for="[tag, count] in tagFrequencyEntries"
                    :key="tag"
                    tone="info"
                  >{{ tag }} · {{ count }}</Badge>
                </div>
              </div>
            </div>
          </Card>
        </div>
      </template>
    </ListPageLayout>

    <!-- ── Submit/Edit Modal ────────────────────────────────────── -->
    <Teleport to="body">
      <div
        v-if="showFormModal"
        v-modal-a11y
        class="modal-overlay"
        @click.self="closeFormModal"
        @modal:escape="closeFormModal"
      >
        <div class="modal" role="dialog" aria-modal="true">
          <div class="modal__head">
            <h3>{{ editingId ? t('view.feedback.modal.editTitle') : t('view.feedback.modal.submitTitle') }}</h3>
            <button class="modal__x" type="button" :aria-label="t('common.close')" @click="closeFormModal">
              <AppIcon name="x" :size="16" />
            </button>
          </div>
          <div class="modal__body">
            <div class="field-row">
              <label class="field">
                <span class="field__label">{{ t('view.feedback.modal.targetType') }}</span>
                <select v-model="form.target_type" class="field__input field__select" :disabled="!!editingId">
                  <option value="agent">{{ t('view.feedback.targetType.agent') }}</option>
                  <option value="task">{{ t('view.feedback.targetType.task') }}</option>
                  <option value="result">{{ t('view.feedback.targetType.result') }}</option>
                </select>
              </label>
              <label class="field">
                <span class="field__label">{{ t('view.feedback.modal.targetId') }}</span>
                <input v-model="form.target_id" class="field__input" :placeholder="t('view.feedback.modal.targetIdHint')" :disabled="!!editingId" />
              </label>
            </div>
            <label class="field">
              <span class="field__label">{{ t('view.feedback.modal.rating') }}</span>
              <div class="fb-rating-input">
                <button
                  v-for="r in [1, 2, 3, 4, 5]"
                  :key="r"
                  type="button"
                  class="fb-rating-input__star"
                  :class="{ 'is-active': r <= form.rating }"
                  :aria-label="String(r)"
                  @click="form.rating = r"
                >
                  <AppIcon name="star" :size="20" />
                </button>
              </div>
            </label>
            <label class="field">
              <span class="field__label">{{ t('view.feedback.modal.comment') }}</span>
              <textarea v-model="form.comment" class="field__input field__textarea" :placeholder="t('view.feedback.modal.commentHint')" rows="3"></textarea>
            </label>
            <label class="field">
              <span class="field__label">{{ t('view.feedback.modal.tags') }}</span>
              <input v-model="form.tagsInput" class="field__input" :placeholder="t('view.feedback.modal.tagsHint')" />
            </label>
          </div>
          <div class="modal__foot">
            <button class="btn-ghost" type="button" @click="closeFormModal">{{ t('common.cancel') }}</button>
            <button
              class="btn-primary"
              type="button"
              :disabled="!canSubmit || formSaving"
              @click="submitForm"
            >
              {{ formSaving ? t('view.feedback.modal.saving') : t('common.save') }}
            </button>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- ── Confirm Delete Modal ─────────────────────────────────── -->
    <Teleport to="body">
      <div
        v-if="showDeleteConfirm"
        v-modal-a11y
        class="modal-overlay"
        @click.self="closeDeleteConfirm"
        @modal:escape="closeDeleteConfirm"
      >
        <div class="modal modal--sm" role="dialog" aria-modal="true">
          <div class="modal__head">
            <h3>{{ t('common.confirm') }}</h3>
            <button class="modal__x" type="button" :aria-label="t('common.close')" @click="closeDeleteConfirm">
              <AppIcon name="x" :size="16" />
            </button>
          </div>
          <div class="modal__body">
            <p>{{ t('view.feedback.deleteConfirm') }}</p>
            <p v-if="deletingFeedback" class="modal__target mono">{{ deletingFeedback.feedback_id }}</p>
          </div>
          <div class="modal__foot">
            <button class="btn-ghost" type="button" @click="closeDeleteConfirm">{{ t('common.cancel') }}</button>
            <button class="btn-danger" type="button" :disabled="deleting" @click="doDelete">
              {{ deleting ? t('common.loading') : t('common.delete') }}
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
import { useI18n } from '../i18n';
import ListPageLayout from '../components/ListPageLayout.vue';
import AppIcon from '../components/AppIcon.vue';
import Card from '../components/Card.vue';
import Badge from '../components/Badge.vue';
import DataTable from '../components/DataTable.vue';
import FilterBar from '../components/FilterBar.vue';
import Segmented from '../components/Segmented.vue';
import Skeleton from '../components/Skeleton.vue';
import EmptyState from '../components/EmptyState.vue';

const api = useApiStore();
const { t } = useI18n();

// ── Tab state ───────────────────────────────────────────────────
const activeTab = ref('list');
const tabOptions = [
  { value: 'list', label: t('view.feedback.tab.list'), icon: 'message-square' },
  { value: 'summary', label: t('view.feedback.tab.summary'), icon: 'activity' },
];

// ── Data state ──────────────────────────────────────────────────
const loading = ref(false);
const exporting = ref(false);
const errors = ref({ list: null, summary: null });

const feedbackRows = ref([]);
const totalCount = ref(0);
const page = ref(1);
const pageSize = ref(20);

const summaryData = ref({});

// ── Filters ─────────────────────────────────────────────────────
const filters = reactive({ target_type: '', rating: '' });

const filterSchema = computed(() => [
  {
    key: 'target_type',
    label: t('view.feedback.filter.targetType'),
    options: [
      { value: 'agent', label: t('view.feedback.targetType.agent') },
      { value: 'task', label: t('view.feedback.targetType.task') },
      { value: 'result', label: t('view.feedback.targetType.result') },
    ],
  },
  {
    key: 'rating',
    label: t('view.feedback.filter.rating'),
    options: [
      { value: '5', label: '5 ★' },
      { value: '4', label: '4 ★' },
      { value: '3', label: '3 ★' },
      { value: '2', label: '2 ★' },
      { value: '1', label: '1 ★' },
    ],
  },
]);

const resultsLabel = computed(() => `${feedbackRows.value.length} / ${totalCount.value}`);

const totalPages = computed(() =>
  totalCount.value > 0 ? Math.max(1, Math.ceil(totalCount.value / pageSize.value)) : 1,
);

function goPrev() {
  if (page.value > 1) {
    page.value -= 1;
    loadFeedback();
  }
}
function goNext() {
  if (page.value < totalPages.value) {
    page.value += 1;
    loadFeedback();
  }
}

// ── Submit/Edit modal ───────────────────────────────────────────
const showFormModal = ref(false);
const editingId = ref('');
const formSaving = ref(false);
const form = reactive({
  target_type: 'agent',
  target_id: '',
  rating: 5,
  comment: '',
  tagsInput: '',
});

const canSubmit = computed(() =>
  form.target_type && form.target_id.trim() && form.rating >= 1 && form.rating <= 5,
);

function openSubmitModal() {
  editingId.value = '';
  form.target_type = 'agent';
  form.target_id = '';
  form.rating = 5;
  form.comment = '';
  form.tagsInput = '';
  showFormModal.value = true;
}

function openEditModal(f) {
  editingId.value = f.feedback_id;
  form.target_type = f.target_type || 'agent';
  form.target_id = f.target_id || '';
  form.rating = f.rating || 5;
  form.comment = f.comment || '';
  form.tagsInput = Array.isArray(f.tags) ? f.tags.join(', ') : '';
  showFormModal.value = true;
}

function closeFormModal() {
  showFormModal.value = false;
}

function parseTags(input) {
  return input.split(',').map((s) => s.trim()).filter(Boolean);
}

async function submitForm() {
  if (!canSubmit.value) return;
  formSaving.value = true;
  try {
    const payload = {
      rating: Number(form.rating),
      comment: form.comment.trim(),
      tags: parseTags(form.tagsInput),
    };
    if (editingId.value) {
      await api.put(`/api/feedback/${encodeURIComponent(editingId.value)}`, payload);
    } else {
      payload.target_type = form.target_type;
      payload.target_id = form.target_id.trim();
      await api.post('/api/feedback', payload);
    }
    showFormModal.value = false;
    await loadFeedback();
    await loadSummary();
  } catch (err) {
    errors.value = { ...errors.value, list: (err && err.message) || t('view.feedback.saveFailed') };
  } finally {
    formSaving.value = false;
  }
}

// ── Delete ──────────────────────────────────────────────────────
const showDeleteConfirm = ref(false);
const deletingFeedback = ref(null);
const deleting = ref(false);

function confirmDelete(f) {
  deletingFeedback.value = f;
  showDeleteConfirm.value = true;
}
function closeDeleteConfirm() {
  showDeleteConfirm.value = false;
  deletingFeedback.value = null;
}
async function doDelete() {
  if (!deletingFeedback.value) return;
  deleting.value = true;
  try {
    await api.delete(`/api/feedback/${encodeURIComponent(deletingFeedback.value.feedback_id)}`);
    showDeleteConfirm.value = false;
    deletingFeedback.value = null;
    await loadFeedback();
    await loadSummary();
  } catch (err) {
    errors.value = { ...errors.value, list: (err && err.message) || t('view.feedback.deleteFailed') };
  } finally {
    deleting.value = false;
  }
}

// ── Export ──────────────────────────────────────────────────────
async function exportFeedback(format) {
  exporting.value = true;
  try {
    const params = new URLSearchParams();
    if (filters.target_type) params.set('target_type', filters.target_type);
    params.set('format', format);
    const url = `/api/feedback/export?${params.toString()}`;
    const res = await fetch(url, { credentials: 'include' });
    if (!res.ok) throw new Error(`Export failed: ${res.status}`);
    const blob = await res.blob();
    const filename = `feedback.${format}`;
    const downloadUrl = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = downloadUrl;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(downloadUrl);
  } catch (err) {
    errors.value = { ...errors.value, list: (err && err.message) || t('view.feedback.exportFailed') };
  } finally {
    exporting.value = false;
  }
}

// ── Helpers ─────────────────────────────────────────────────────
function targetTypeLabel(ty) {
  if (ty === 'agent') return t('view.feedback.targetType.agent');
  if (ty === 'task') return t('view.feedback.targetType.task');
  if (ty === 'result') return t('view.feedback.targetType.result');
  return ty || '—';
}
function targetTypeTone(ty) {
  if (ty === 'agent') return 'brand';
  if (ty === 'task') return 'info';
  if (ty === 'result') return 'success';
  return 'neutral';
}
function ratingTone(r) {
  if (r >= 5) return 'is-5';
  if (r >= 4) return 'is-4';
  if (r >= 3) return 'is-3';
  if (r >= 2) return 'is-2';
  return 'is-1';
}
function formatAvg(v) {
  if (v == null) return '—';
  return Number(v).toFixed(2);
}

// ── Summary helpers ─────────────────────────────────────────────
const hasSummaryData = computed(() => Object.keys(summaryData.value).length > 0 && (summaryData.value.total ?? 0) > 0);

const avgRatingTone = computed(() => {
  const v = Number(summaryData.value.average_rating);
  if (isNaN(v)) return '';
  if (v >= 4.5) return 'is-ok';
  if (v >= 3.5) return 'is-warn';
  return 'is-fail';
});

function distributionCount(rating) {
  const dist = summaryData.value.rating_distribution || {};
  return Number(dist[String(rating)] || dist[rating] || 0);
}
function distributionPct(rating) {
  const total = summaryData.value.total || 0;
  if (total <= 0) return 0;
  return (distributionCount(rating) / total) * 100;
}

const tagFrequencyEntries = computed(() => {
  const freq = summaryData.value.tag_frequency || {};
  return Object.entries(freq).slice(0, 20);
});

// ── Data loading ────────────────────────────────────────────────
async function loadFeedback() {
  try {
    const params = new URLSearchParams();
    if (filters.target_type) params.set('target_type', filters.target_type);
    if (filters.rating) params.set('rating', filters.rating);
    params.set('page', String(page.value));
    params.set('page_size', String(pageSize.value));
    const data = await api.get(`/api/feedback?${params.toString()}`);
    feedbackRows.value = data.data || [];
    totalCount.value = data.total || 0;
    errors.value = { ...errors.value, list: null };
  } catch (err) {
    feedbackRows.value = [];
    totalCount.value = 0;
    errors.value = { ...errors.value, list: (err && err.message) || t('view.feedback.list.failedLoad') };
  }
}

async function loadSummary() {
  try {
    const params = new URLSearchParams();
    if (filters.target_type) params.set('target_type', filters.target_type);
    const data = await api.get(`/api/feedback/summary?${params.toString()}`);
    summaryData.value = data.summary || {};
    errors.value = { ...errors.value, summary: null };
  } catch (err) {
    summaryData.value = {};
    errors.value = { ...errors.value, summary: (err && err.message) || t('view.feedback.summary.failedLoad') };
  }
}

async function loadAll() {
  loading.value = true;
  await Promise.allSettled([loadFeedback(), loadSummary()]);
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
.btn-ghost--sm { padding: 2px var(--sp-2); font-size: var(--fs-xs); }

.btn-primary {
  display: inline-flex;
  align-items: center;
  gap: var(--sp-1);
  padding: var(--sp-2) var(--sp-4);
  border: none;
  border-radius: var(--r-md);
  background: var(--brand);
  color: var(--brand-contrast);
  font-size: var(--fs-sm);
  font-weight: 600;
  cursor: pointer;
  transition: background var(--motion) var(--ease);
}
.btn-primary:hover { background: var(--brand-strong); }
.btn-primary:disabled { opacity: .5; cursor: not-allowed; }

.btn-danger {
  display: inline-flex;
  align-items: center;
  gap: var(--sp-1);
  padding: var(--sp-2) var(--sp-4);
  border: none;
  border-radius: var(--r-md);
  background: var(--fail);
  color: var(--on-fail);
  font-size: var(--fs-sm);
  font-weight: 600;
  cursor: pointer;
  transition: background var(--motion) var(--ease);
}
.btn-danger:hover { background: var(--fail-strong); }
.btn-danger:disabled { opacity: .5; cursor: not-allowed; }

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
.icon-btn--danger:hover { color: var(--fail); border-color: var(--fail); }

/* ── Feedback table ───────────────────────────────────────────── */
.fb-table {
  width: 100%;
  border: 1px solid var(--border-subtle);
  border-radius: var(--r-md);
  overflow: hidden;
  margin-top: var(--sp-3);
}
.fb-table__head,
.fb-table__row {
  display: grid;
  grid-template-columns: 100px 1fr 80px 1fr 160px 96px;
  align-items: center;
  gap: var(--sp-2);
}
.fb-table__head {
  background: var(--surface-2);
  padding: var(--sp-2) var(--sp-3);
  border-bottom: 1px solid var(--border-strong);
}
.fb-table__th {
  font-size: var(--fs-xs);
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: .03em;
  color: var(--text-muted);
}
.fb-table__th--rating { text-align: center; }
.fb-table__th--comment,
.fb-table__th--tags { display: block; }
.fb-table__th--act { text-align: right; }
.fb-table__row {
  padding: var(--sp-3);
  border-bottom: 1px solid var(--border-subtle);
  transition: background var(--motion) var(--ease);
}
.fb-table__row:hover { background: var(--surface-2); }
.fb-table__row:last-child { border-bottom: none; }
.fb-table__td { min-width: 0; }
.fb-table__td--rating { text-align: center; }
.fb-table__td--comment,
.fb-table__td--tags {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.fb-table__td--act { display: flex; justify-content: flex-end; gap: var(--sp-1); }
.fb-table__target { color: var(--text); }
.fb-table__comment { color: var(--text-muted); font-size: var(--fs-sm); }
.fb-more { color: var(--text-faint); font-size: var(--fs-xs); margin-left: var(--sp-1); }

.fb-rating {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  font-size: var(--fs-sm);
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}
.fb-rating.is-5 { color: var(--success); }
.fb-rating.is-4 { color: var(--success); }
.fb-rating.is-3 { color: var(--warn); }
.fb-rating.is-2 { color: var(--warn); }
.fb-rating.is-1 { color: var(--fail); }

/* ── Pager ────────────────────────────────────────────────────── */
.fb-pager {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--sp-2);
  padding: var(--sp-3);
  margin-top: var(--sp-2);
}
.fb-pager__btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border: 1px solid var(--border);
  border-radius: var(--r-md);
  background: var(--surface);
  color: var(--text-muted);
  font-size: var(--fs-md);
  cursor: pointer;
  transition: background var(--motion) var(--ease), color var(--motion) var(--ease);
}
.fb-pager__btn:hover:not(:disabled) { background: var(--surface-2); color: var(--text); }
.fb-pager__btn:disabled { opacity: .45; cursor: not-allowed; }
.fb-pager__info { font-size: var(--fs-sm); color: var(--text-muted); font-variant-numeric: tabular-nums; min-width: 60px; text-align: center; }

/* ── Summary ──────────────────────────────────────────────────── */
.fb-summary { display: flex; flex-direction: column; gap: var(--sp-4); }
.fb-kpi-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: var(--sp-3);
}
.fb-kpi {
  display: flex;
  flex-direction: column;
  gap: var(--sp-1);
  padding: var(--sp-3) var(--sp-4);
  background: var(--surface-2);
  border: 1px solid var(--border-subtle);
  border-radius: var(--r-md);
}
.fb-kpi__label { font-size: var(--fs-xs); text-transform: uppercase; letter-spacing: .03em; color: var(--text-muted); }
.fb-kpi__value { font-size: var(--fs-xl); font-weight: 600; color: var(--text); font-variant-numeric: tabular-nums; }
.fb-kpi__value.is-ok { color: var(--success); }
.fb-kpi__value.is-warn { color: var(--warn); }
.fb-kpi__value.is-fail { color: var(--fail); }

.fb-distribution { display: flex; flex-direction: column; gap: var(--sp-2); }
.fb-distribution__title { font-size: var(--fs-sm); font-weight: 600; color: var(--text); margin: 0; }
.fb-distribution__bars { display: flex; flex-direction: column; gap: var(--sp-1); }
.fb-distribution__row {
  display: grid;
  grid-template-columns: 40px 1fr 40px;
  align-items: center;
  gap: var(--sp-2);
}
.fb-distribution__label { display: inline-flex; align-items: center; gap: 2px; font-size: var(--fs-sm); color: var(--text-muted); }
.fb-distribution__bar {
  height: 8px;
  background: var(--surface-2);
  border-radius: var(--r-full);
  overflow: hidden;
}
.fb-distribution__fill {
  height: 100%;
  background: var(--brand);
  border-radius: var(--r-full);
  transition: width var(--motion) var(--ease);
}
.fb-distribution__count { font-size: var(--fs-sm); color: var(--text-muted); text-align: right; font-variant-numeric: tabular-nums; }

.fb-tags { display: flex; flex-direction: column; gap: var(--sp-2); }
.fb-tags__title { font-size: var(--fs-sm); font-weight: 600; color: var(--text); margin: 0; }
.fb-tags__cloud { display: flex; flex-wrap: wrap; gap: var(--sp-2); }

/* ── Rating input ─────────────────────────────────────────────── */
.fb-rating-input { display: inline-flex; gap: var(--sp-1); }
.fb-rating-input__star {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: none;
  background: transparent;
  color: var(--text-faint);
  cursor: pointer;
  padding: var(--sp-1);
  transition: color var(--motion) var(--ease);
}
.fb-rating-input__star:hover { color: var(--warn); }
.fb-rating-input__star.is-active { color: var(--warn); }

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
  max-width: 520px;
  max-height: 90vh;
  overflow-y: auto;
  background: var(--surface);
  border: 1px solid var(--border-strong);
  border-radius: var(--r-lg);
  box-shadow: var(--shadow-modal);
}
.modal--sm { max-width: 400px; }
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
.modal__body p { color: var(--text); line-height: 1.55; }
.modal__target { margin-top: var(--sp-2); color: var(--text-muted); font-size: var(--fs-sm); }
.modal__foot {
  display: flex;
  justify-content: flex-end;
  gap: var(--sp-2);
  padding: var(--sp-3) var(--sp-4);
  border-top: 1px solid var(--border-subtle);
}

/* ── Form fields ──────────────────────────────────────────────── */
.field { display: flex; flex-direction: column; gap: var(--sp-1); }
.field__label { font-size: var(--fs-sm); font-weight: 600; color: var(--text); }
.field__input {
  padding: var(--sp-2) var(--sp-3);
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: var(--r-md);
  color: var(--text);
  font-size: var(--fs-sm);
  font-family: inherit;
  transition: border-color var(--motion) var(--ease);
}
.field__input:focus { outline: none; border-color: var(--brand); box-shadow: 0 0 0 3px var(--brand-soft); }
.field__input::placeholder { color: var(--text-faint); }
.field__input:disabled { opacity: .6; cursor: not-allowed; }
.field__select { cursor: pointer; appearance: none; -webkit-appearance: none; background-image: var(--icon-chevron); background-repeat: no-repeat; background-position: right var(--sp-2) center; padding-right: var(--sp-7); }
.field__textarea { resize: vertical; min-height: 60px; }
.field-row { display: grid; grid-template-columns: 1fr 1fr; gap: var(--sp-3); }

/* ── Responsive ───────────────────────────────────────────────── */
@media (max-width: 900px) {
  .fb-kpi-grid { grid-template-columns: 1fr; }
  .field-row { grid-template-columns: 1fr; }
  .fb-table__head,
  .fb-table__row { grid-template-columns: 100px 1fr 80px 96px; }
  .fb-table__th--comment,
  .fb-table__td--comment,
  .fb-table__th--tags,
  .fb-table__td--tags { display: none; }
}

@media (max-width: 640px) {
  .fb-table__head,
  .fb-table__row { grid-template-columns: 1fr 80px 96px; }
  .fb-table__th:nth-child(1),
  .fb-table__td:nth-child(1),
  .fb-table__th--comment,
  .fb-table__td--comment,
  .fb-table__th--tags,
  .fb-table__td--tags { display: none; }
  .modal__body { padding: var(--sp-3); }
  .modal__foot { flex-direction: column-reverse; }
  .modal__foot .btn-ghost,
  .modal__foot .btn-primary,
  .modal__foot .btn-danger { width: 100%; justify-content: center; }
}
</style>
