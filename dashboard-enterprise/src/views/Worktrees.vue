<template>
  <div class="wt-view">
    <ListPageLayout>
      <template #actions>
        <button class="btn-ghost" :class="{ 'is-busy': loading }" :disabled="loading" @click="loadBranches">
          <AppIcon name="refresh" :size="15" />
          <span>{{ t('common.refresh') }}</span>
        </button>
        <button class="btn-primary" type="button" @click="openCreateRoot">
          <AppIcon name="plus" :size="15" />
          <span>{{ t('view.worktrees.branches.add') }}</span>
        </button>
      </template>
      <template #content>
        <Card :title="t('view.worktrees.branches.title')" icon="git-branch" margin-bottom="var(--sp-4)">
          <template #actions>
            <label class="wt-toggle">
              <input type="checkbox" :checked="activeOnly" @change="toggleActiveOnly" />
              <span>{{ t('view.worktrees.branches.activeOnly') }}</span>
            </label>
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
            :title="t('view.worktrees.branches.failedLoad')"
            :description="error"
          />
          <EmptyState
            v-else-if="!branches.length"
            icon="git-branch"
            :title="t('view.worktrees.branches.empty')"
            :description="t('view.worktrees.branches.emptyHint')"
          />
          <div v-else class="wt-table" role="table" :aria-label="t('view.worktrees.branches.title')">
            <div class="wt-table__head" role="row">
              <div class="wt-table__th" role="columnheader">{{ t('view.worktrees.col.name') }}</div>
              <div class="wt-table__th" role="columnheader">{{ t('view.worktrees.col.status') }}</div>
              <div class="wt-table__th wt-table__th--parent" role="columnheader">{{ t('view.worktrees.col.parent') }}</div>
              <div class="wt-table__th wt-table__th--desc" role="columnheader">{{ t('view.worktrees.col.description') }}</div>
              <div class="wt-table__th wt-table__th--act" role="columnheader">{{ t('view.worktrees.col.actions') }}</div>
            </div>
            <div v-for="b in branches" :key="b.id || b.node_id" class="wt-table__row" role="row">
              <div class="wt-table__td" role="cell">
                <div class="wt-table__name">{{ b.name || b.id || '—' }}</div>
                <div class="wt-table__id mono">{{ b.id || b.node_id || '' }}</div>
              </div>
              <div class="wt-table__td" role="cell">
                <Badge :tone="statusTone(b)">{{ statusLabel(b) }}</Badge>
              </div>
              <div class="wt-table__td wt-table__td--parent" role="cell">
                <span class="mono">{{ b.parent_id || '—' }}</span>
              </div>
              <div class="wt-table__td wt-table__td--desc" role="cell">
                <span class="wt-table__desc">{{ b.description || b.task || '—' }}</span>
              </div>
              <div class="wt-table__td wt-table__td--act" role="cell">
                <button class="icon-btn" type="button" :aria-label="t('view.worktrees.modal.branchTitle')" :title="t('view.worktrees.modal.branchTitle')" @click="openBranchModal(b)">
                  <AppIcon name="git-branch" :size="14" />
                </button>
                <button class="icon-btn" type="button" :aria-label="t('view.worktrees.modal.checkpointTitle')" :title="t('view.worktrees.modal.checkpointTitle')" @click="openCheckpointModal(b)">
                  <AppIcon name="archive" :size="14" />
                </button>
                <button class="icon-btn" type="button" :aria-label="t('view.worktrees.modal.mergeTitle')" :title="t('view.worktrees.modal.mergeTitle')" @click="openMergeModal(b)">
                  <AppIcon name="git-compare" :size="14" />
                </button>
                <button class="icon-btn" type="button" :aria-label="t('view.worktrees.modal.rollbackTitle')" :title="t('view.worktrees.modal.rollbackTitle')" @click="openRollbackModal(b)">
                  <AppIcon name="rotate-ccw" :size="14" />
                </button>
                <button class="icon-btn icon-btn--danger" type="button" :aria-label="t('common.delete')" :title="t('common.delete')" @click="confirmAbandon(b)">
                  <AppIcon name="trash" :size="14" />
                </button>
              </div>
            </div>
          </div>
        </Card>
      </template>
    </ListPageLayout>

    <!-- ── Create Root Modal ─────────────────────────────────── -->
    <Teleport to="body">
      <div
        v-if="showCreateRootModal"
        v-modal-a11y
        class="modal-overlay"
        @click.self="closeCreateRootModal"
        @modal:escape="closeCreateRootModal"
      >
        <div class="modal" role="dialog" aria-modal="true">
          <div class="modal__head">
            <h3>{{ t('view.worktrees.modal.createRootTitle') }}</h3>
            <button class="modal__x" type="button" :aria-label="t('common.close')" @click="closeCreateRootModal">
              <AppIcon name="x" :size="16" />
            </button>
          </div>
          <div class="modal__body">
            <label class="field">
              <span class="field__label">{{ t('view.worktrees.modal.task') }}</span>
              <input v-model="rootForm.task" class="field__input" :placeholder="t('view.worktrees.modal.taskHint')" />
            </label>
            <label class="field">
              <span class="field__label">{{ t('view.worktrees.modal.description') }}</span>
              <textarea v-model="rootForm.description" class="field__input field__textarea" :placeholder="t('view.worktrees.modal.descriptionHint')" rows="3"></textarea>
            </label>
          </div>
          <div class="modal__foot">
            <button class="btn-ghost" type="button" @click="closeCreateRootModal">{{ t('common.cancel') }}</button>
            <button
              class="btn-primary"
              type="button"
              :disabled="!rootForm.task.trim() || rootSaving"
              @click="submitCreateRoot"
            >
              {{ rootSaving ? t('view.worktrees.modal.saving') : t('common.save') }}
            </button>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- ── Branch Modal ─────────────────────────────────────── -->
    <Teleport to="body">
      <div
        v-if="showBranchModal"
        v-modal-a11y
        class="modal-overlay"
        @click.self="closeBranchModal"
        @modal:escape="closeBranchModal"
      >
        <div class="modal" role="dialog" aria-modal="true">
          <div class="modal__head">
            <h3>{{ t('view.worktrees.modal.branchTitle') }}</h3>
            <button class="modal__x" type="button" :aria-label="t('common.close')" @click="closeBranchModal">
              <AppIcon name="x" :size="16" />
            </button>
          </div>
          <div class="modal__body">
            <label class="field">
              <span class="field__label">{{ t('view.worktrees.modal.parent') }}</span>
              <input class="field__input" :value="branchForm.parent_id" disabled />
            </label>
            <label class="field">
              <span class="field__label">{{ t('view.worktrees.modal.branchName') }}</span>
              <input v-model="branchForm.name" class="field__input" :placeholder="t('view.worktrees.modal.branchNameHint')" />
            </label>
            <label class="field">
              <span class="field__label">{{ t('view.worktrees.modal.description') }}</span>
              <textarea v-model="branchForm.description" class="field__input field__textarea" :placeholder="t('view.worktrees.modal.descriptionHint')" rows="3"></textarea>
            </label>
          </div>
          <div class="modal__foot">
            <button class="btn-ghost" type="button" @click="closeBranchModal">{{ t('common.cancel') }}</button>
            <button
              class="btn-primary"
              type="button"
              :disabled="!branchForm.name.trim() || branchSaving"
              @click="submitBranch"
            >
              {{ branchSaving ? t('view.worktrees.modal.saving') : t('common.save') }}
            </button>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- ── Merge Modal ──────────────────────────────────────── -->
    <Teleport to="body">
      <div
        v-if="showMergeModal"
        v-modal-a11y
        class="modal-overlay"
        @click.self="closeMergeModal"
        @modal:escape="closeMergeModal"
      >
        <div class="modal" role="dialog" aria-modal="true">
          <div class="modal__head">
            <h3>{{ t('view.worktrees.modal.mergeTitle') }}</h3>
            <button class="modal__x" type="button" :aria-label="t('common.close')" @click="closeMergeModal">
              <AppIcon name="x" :size="16" />
            </button>
          </div>
          <div class="modal__body">
            <label class="field">
              <span class="field__label">{{ t('view.worktrees.modal.sourceBranch') }}</span>
              <input class="field__input" :value="mergeForm.source_branch" disabled />
            </label>
            <label class="field">
              <span class="field__label">{{ t('view.worktrees.modal.targetBranch') }}</span>
              <input v-model="mergeForm.target_branch" class="field__input" :placeholder="t('view.worktrees.modal.targetBranchHint')" />
            </label>
          </div>
          <div class="modal__foot">
            <button class="btn-ghost" type="button" @click="closeMergeModal">{{ t('common.cancel') }}</button>
            <button
              class="btn-primary"
              type="button"
              :disabled="!mergeForm.source_branch || mergeSaving"
              @click="submitMerge"
            >
              {{ mergeSaving ? t('view.worktrees.modal.saving') : t('common.save') }}
            </button>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- ── Checkpoint Modal ─────────────────────────────────── -->
    <Teleport to="body">
      <div
        v-if="showCheckpointModal"
        v-modal-a11y
        class="modal-overlay"
        @click.self="closeCheckpointModal"
        @modal:escape="closeCheckpointModal"
      >
        <div class="modal modal--sm" role="dialog" aria-modal="true">
          <div class="modal__head">
            <h3>{{ t('view.worktrees.modal.checkpointTitle') }}</h3>
            <button class="modal__x" type="button" :aria-label="t('common.close')" @click="closeCheckpointModal">
              <AppIcon name="x" :size="16" />
            </button>
          </div>
          <div class="modal__body">
            <label class="field">
              <span class="field__label">{{ t('view.worktrees.modal.checkpointLabel') }}</span>
              <input v-model="checkpointForm.label" class="field__input" :placeholder="t('view.worktrees.modal.checkpointLabelHint')" />
            </label>
          </div>
          <div class="modal__foot">
            <button class="btn-ghost" type="button" @click="closeCheckpointModal">{{ t('common.cancel') }}</button>
            <button
              class="btn-primary"
              type="button"
              :disabled="checkpointSaving"
              @click="submitCheckpoint"
            >
              {{ checkpointSaving ? t('view.worktrees.modal.saving') : t('common.save') }}
            </button>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- ── Rollback Modal ───────────────────────────────────── -->
    <Teleport to="body">
      <div
        v-if="showRollbackModal"
        v-modal-a11y
        class="modal-overlay"
        @click.self="closeRollbackModal"
        @modal:escape="closeRollbackModal"
      >
        <div class="modal modal--sm" role="dialog" aria-modal="true">
          <div class="modal__head">
            <h3>{{ t('view.worktrees.modal.rollbackTitle') }}</h3>
            <button class="modal__x" type="button" :aria-label="t('common.close')" @click="closeRollbackModal">
              <AppIcon name="x" :size="16" />
            </button>
          </div>
          <div class="modal__body">
            <label class="field">
              <span class="field__label">{{ t('view.worktrees.modal.checkpointId') }}</span>
              <input v-model="rollbackForm.checkpoint_id" class="field__input" :placeholder="t('view.worktrees.modal.checkpointId')" />
            </label>
          </div>
          <div class="modal__foot">
            <button class="btn-ghost" type="button" @click="closeRollbackModal">{{ t('common.cancel') }}</button>
            <button
              class="btn-primary"
              type="button"
              :disabled="!rollbackForm.checkpoint_id.trim() || rollbackSaving"
              @click="submitRollback"
            >
              {{ rollbackSaving ? t('view.worktrees.modal.saving') : t('common.save') }}
            </button>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- ── Confirm Abandon Modal ────────────────────────────── -->
    <Teleport to="body">
      <div
        v-if="showAbandonConfirm"
        v-modal-a11y
        class="modal-overlay"
        @click.self="closeAbandonConfirm"
        @modal:escape="closeAbandonConfirm"
      >
        <div class="modal modal--sm" role="dialog" aria-modal="true">
          <div class="modal__head">
            <h3>{{ t('common.confirm') }}</h3>
            <button class="modal__x" type="button" :aria-label="t('common.close')" @click="closeAbandonConfirm">
              <AppIcon name="x" :size="16" />
            </button>
          </div>
          <div class="modal__body">
            <p>{{ t('view.worktrees.abandonConfirm') }}</p>
            <p v-if="abandoningBranch" class="modal__target mono">{{ abandoningBranch.name || abandoningBranch.id }}</p>
          </div>
          <div class="modal__foot">
            <button class="btn-ghost" type="button" @click="closeAbandonConfirm">{{ t('common.cancel') }}</button>
            <button class="btn-danger" type="button" :disabled="abandoning" @click="doAbandon">
              {{ abandoning ? t('common.loading') : t('common.delete') }}
            </button>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue';
import { useApiStore } from '../stores/api.js';
import { useI18n } from '../i18n';
import ListPageLayout from '../components/ListPageLayout.vue';
import AppIcon from '../components/AppIcon.vue';
import Card from '../components/Card.vue';
import Badge from '../components/Badge.vue';
import Skeleton from '../components/Skeleton.vue';
import EmptyState from '../components/EmptyState.vue';

const api = useApiStore();
const { t } = useI18n();

// ── Data state ──────────────────────────────────────────────────
const loading = ref(false);
const error = ref('');
const branches = ref([]);
const activeOnly = ref(false);

// ── Create root modal ───────────────────────────────────────────
const showCreateRootModal = ref(false);
const rootSaving = ref(false);
const rootForm = reactive({ task: '', description: '' });

function openCreateRoot() {
  rootForm.task = '';
  rootForm.description = '';
  showCreateRootModal.value = true;
}
function closeCreateRootModal() {
  showCreateRootModal.value = false;
}
async function submitCreateRoot() {
  const task = rootForm.task.trim();
  if (!task) return;
  rootSaving.value = true;
  try {
    await api.post('/api/worktree/create-root', {
      task,
      description: rootForm.description.trim(),
    });
    showCreateRootModal.value = false;
    await loadBranches();
  } catch (err) {
    error.value = (err && err.message) || t('view.worktrees.error.createRoot');
  } finally {
    rootSaving.value = false;
  }
}

// ── Branch modal ────────────────────────────────────────────────
const showBranchModal = ref(false);
const branchSaving = ref(false);
const branchForm = reactive({ parent_id: '', name: '', description: '' });

function openBranchModal(parent) {
  branchForm.parent_id = parent.id || parent.node_id || '';
  branchForm.name = '';
  branchForm.description = '';
  showBranchModal.value = true;
}
function closeBranchModal() {
  showBranchModal.value = false;
}
async function submitBranch() {
  const name = branchForm.name.trim();
  if (!name || !branchForm.parent_id) return;
  branchSaving.value = true;
  try {
    await api.post('/api/worktree/branch', {
      parent_id: branchForm.parent_id,
      name,
      description: branchForm.description.trim(),
    });
    showBranchModal.value = false;
    await loadBranches();
  } catch (err) {
    error.value = (err && err.message) || t('view.worktrees.error.branch');
  } finally {
    branchSaving.value = false;
  }
}

// ── Merge modal ─────────────────────────────────────────────────
const showMergeModal = ref(false);
const mergeSaving = ref(false);
const mergeForm = reactive({ source_branch: '', target_branch: '' });

function openMergeModal(branch) {
  mergeForm.source_branch = branch.name || branch.id || '';
  mergeForm.target_branch = '';
  showMergeModal.value = true;
}
function closeMergeModal() {
  showMergeModal.value = false;
}
async function submitMerge() {
  if (!mergeForm.source_branch) return;
  mergeSaving.value = true;
  try {
    await api.post('/api/worktree/merge', {
      source_branch: mergeForm.source_branch,
      target_branch: mergeForm.target_branch.trim(),
    });
    showMergeModal.value = false;
    await loadBranches();
  } catch (err) {
    error.value = (err && err.message) || t('view.worktrees.error.merge');
  } finally {
    mergeSaving.value = false;
  }
}

// ── Checkpoint modal ────────────────────────────────────────────
const showCheckpointModal = ref(false);
const checkpointSaving = ref(false);
const checkpointForm = reactive({ node_id: '', label: '' });

function openCheckpointModal(branch) {
  checkpointForm.node_id = branch.id || branch.node_id || '';
  checkpointForm.label = '';
  showCheckpointModal.value = true;
}
function closeCheckpointModal() {
  showCheckpointModal.value = false;
}
async function submitCheckpoint() {
  if (!checkpointForm.node_id) return;
  checkpointSaving.value = true;
  try {
    await api.post('/api/worktree/checkpoint', {
      node_id: checkpointForm.node_id,
      label: checkpointForm.label.trim(),
    });
    showCheckpointModal.value = false;
  } catch (err) {
    error.value = (err && err.message) || t('view.worktrees.error.checkpoint');
  } finally {
    checkpointSaving.value = false;
  }
}

// ── Rollback modal ──────────────────────────────────────────────
const showRollbackModal = ref(false);
const rollbackSaving = ref(false);
const rollbackForm = reactive({ node_id: '', checkpoint_id: '' });

function openRollbackModal(branch) {
  rollbackForm.node_id = branch.id || branch.node_id || '';
  rollbackForm.checkpoint_id = '';
  showRollbackModal.value = true;
}
function closeRollbackModal() {
  showRollbackModal.value = false;
}
async function submitRollback() {
  if (!rollbackForm.node_id || !rollbackForm.checkpoint_id.trim()) return;
  rollbackSaving.value = true;
  try {
    await api.post('/api/worktree/rollback', {
      node_id: rollbackForm.node_id,
      checkpoint_id: rollbackForm.checkpoint_id.trim(),
    });
    showRollbackModal.value = false;
    await loadBranches();
  } catch (err) {
    error.value = (err && err.message) || t('view.worktrees.error.rollback');
  } finally {
    rollbackSaving.value = false;
  }
}

// ── Abandon ─────────────────────────────────────────────────────
const showAbandonConfirm = ref(false);
const abandoningBranch = ref(null);
const abandoning = ref(false);

function confirmAbandon(branch) {
  abandoningBranch.value = branch;
  showAbandonConfirm.value = true;
}
function closeAbandonConfirm() {
  showAbandonConfirm.value = false;
  abandoningBranch.value = null;
}
async function doAbandon() {
  const target = abandoningBranch.value;
  if (!target) return;
  abandoning.value = true;
  try {
    await api.post('/api/worktree/abandon', { id: target.id || target.node_id });
    showAbandonConfirm.value = false;
    abandoningBranch.value = null;
    await loadBranches();
  } catch (err) {
    error.value = (err && err.message) || t('view.worktrees.abandonFailed');
  } finally {
    abandoning.value = false;
  }
}

// ── Helpers ─────────────────────────────────────────────────────
function statusLabel(b) {
  const s = String(b.status || b.state || '').toLowerCase();
  if (s === 'active' || s === 'ok' || s === '') return t('view.worktrees.status.active');
  if (s === 'abandoned') return t('view.worktrees.status.abandoned');
  if (s === 'merged') return t('view.worktrees.status.merged');
  return t('view.worktrees.status.unknown');
}
function statusTone(b) {
  const s = String(b.status || b.state || '').toLowerCase();
  if (s === 'abandoned') return 'neutral';
  if (s === 'merged') return 'info';
  if (s === 'active' || s === 'ok' || s === '') return 'success';
  return 'warn';
}

function toggleActiveOnly() {
  activeOnly.value = !activeOnly.value;
  loadBranches();
}

// ── Data loading ────────────────────────────────────────────────
async function loadBranches() {
  loading.value = true;
  error.value = '';
  try {
    const params = new URLSearchParams();
    if (activeOnly.value) params.set('active_only', 'true');
    const qs = params.toString();
    const url = `/api/worktree/list${qs ? '?' + qs : ''}`;
    const data = await api.get(url);
    branches.value = data.branches || [];
  } catch (err) {
    branches.value = [];
    error.value = (err && err.message) || t('view.worktrees.branches.failedLoad');
  } finally {
    loading.value = false;
  }
}

onMounted(() => {
  loadBranches();
});
</script>

<style scoped>
/* ── Layout helpers ────────────────────────────────────────────── */
.blk { display: flex; flex-direction: column; gap: var(--sp-2); }
.muted { color: var(--text-muted); }
.mono { font-family: var(--font-mono); font-size: var(--fs-xs); }

/* ── Toggle ────────────────────────────────────────────────────── */
.wt-toggle {
  display: inline-flex;
  align-items: center;
  gap: var(--sp-1);
  font-size: var(--fs-sm);
  color: var(--text-muted);
  cursor: pointer;
  user-select: none;
}
.wt-toggle input { cursor: pointer; }

/* ── Branches table ────────────────────────────────────────────── */
.wt-table {
  width: 100%;
  border: 1px solid var(--border-subtle);
  border-radius: var(--r-md);
  overflow: hidden;
}
.wt-table__head,
.wt-table__row {
  display: grid;
  grid-template-columns: 1fr auto 140px 1fr 200px;
  align-items: center;
  gap: var(--sp-2);
}
.wt-table__head {
  background: var(--surface-2);
  padding: var(--sp-2) var(--sp-3);
  border-bottom: 1px solid var(--border-strong);
}
.wt-table__th {
  font-size: var(--fs-xs);
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: .03em;
  color: var(--text-muted);
}
.wt-table__th--parent,
.wt-table__th--desc { display: block; }
.wt-table__th--act { text-align: right; }
.wt-table__row {
  padding: var(--sp-3);
  border-bottom: 1px solid var(--border-subtle);
  transition: background var(--motion) var(--ease);
}
.wt-table__row:hover { background: var(--surface-2); }
.wt-table__row:last-child { border-bottom: none; }
.wt-table__td { min-width: 0; }
.wt-table__td--parent,
.wt-table__td--desc {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.wt-table__td--act { display: flex; justify-content: flex-end; gap: var(--sp-1); flex-wrap: wrap; }
.wt-table__name { font-weight: 600; color: var(--text); }
.wt-table__id { color: var(--text-faint); margin-top: 2px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.wt-table__desc { color: var(--text-muted); font-size: var(--fs-sm); }

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

/* ── Ghost / primary / danger buttons ─────────────────────────── */
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
.field__textarea { resize: vertical; min-height: 60px; }

/* ── Responsive ───────────────────────────────────────────────── */
@media (max-width: 900px) {
  .wt-table__head,
  .wt-table__row { grid-template-columns: 1fr auto 160px; }
  .wt-table__th--parent,
  .wt-table__td--parent,
  .wt-table__th--desc,
  .wt-table__td--desc { display: none; }
}

@media (max-width: 640px) {
  .wt-table__head,
  .wt-table__row { grid-template-columns: 1fr 140px; }
  .wt-table__th--parent,
  .wt-table__td--parent,
  .wt-table__th--desc,
  .wt-table__td--desc { display: none; }
  .modal__body { padding: var(--sp-3); }
  .modal__foot { flex-direction: column-reverse; }
  .modal__foot .btn-ghost,
  .modal__foot .btn-primary,
  .modal__foot .btn-danger { width: 100%; justify-content: center; }
}
</style>
