<template>
  <div class="protocols-view">
    <ListPageLayout>
      <template #actions>
        <Segmented
          :model-value="activeTab"
          :options="tabOptions"
          size="sm"
          @update:model-value="activeTab = $event"
        />
        <button class="btn-ghost" :class="{ 'is-busy': loading }" :disabled="loading" @click="loadProtocols">
          <AppIcon name="refresh" :size="15" />
          <span>{{ t('common.refresh') }}</span>
        </button>
      </template>
      <template #content>
        <!-- ── Protocol list ────────────────────────────────────── -->
        <div v-show="activeTab === 'protocols'">
          <Card :title="t('view.protocols.list.title')" icon="network" margin-bottom="var(--sp-4)">
            <template #actions>
              <button class="btn-ghost" @click="openAdd">
                <AppIcon name="plus" :size="15" />
                <span>{{ t('view.protocols.list.add') }}</span>
              </button>
            </template>
            <div v-if="loading" class="blk">
              <Skeleton block height="44px" />
              <Skeleton block height="44px" />
              <Skeleton block height="44px" />
            </div>
            <EmptyState
              v-else-if="errors.protocols"
              icon="alert-triangle"
              tone="fail"
              :title="t('view.protocols.list.failedLoad')"
              :description="errors.protocols"
            />
            <EmptyState
              v-else-if="!protocols.length"
              icon="network"
              :title="t('view.protocols.list.empty')"
              :description="t('view.protocols.list.emptyHint')"
            />
            <div v-else class="proto-table" role="table" :aria-label="t('view.protocols.list.title')">
              <div class="proto-table__head" role="row">
                <div class="proto-table__th" role="columnheader">{{ t('view.protocols.col.name') }}</div>
                <div class="proto-table__th" role="columnheader">{{ t('view.protocols.col.version') }}</div>
                <div class="proto-table__th" role="columnheader">{{ t('view.protocols.col.participants') }}</div>
                <div class="proto-table__th" role="columnheader">{{ t('view.protocols.col.description') }}</div>
                <div class="proto-table__th proto-table__th--act" role="columnheader">{{ t('view.protocols.col.actions') }}</div>
              </div>
              <div v-for="(p, i) in protocols" :key="p.name + '-' + p.version + '-' + i" class="proto-table__row" role="row">
                <div class="proto-table__td" role="cell">
                  <span class="proto-table__name">{{ p.name || '—' }}</span>
                </div>
                <div class="proto-table__td" role="cell">
                  <Badge tone="info">v{{ p.version || '1.0' }}</Badge>
                </div>
                <div class="proto-table__td" role="cell">
                  <template v-if="(p.participants || []).length">
                    <Badge v-for="part in (p.participants || []).slice(0, 2)" :key="part" tone="neutral">{{ part }}</Badge>
                    <span v-if="(p.participants || []).length > 2" class="proto-table__more">+{{ p.participants.length - 2 }}</span>
                  </template>
                  <span v-else class="muted">—</span>
                </div>
                <div class="proto-table__td" role="cell">
                  <span class="proto-table__desc">{{ p.description || '—' }}</span>
                </div>
                <div class="proto-table__td proto-table__td--act" role="cell">
                  <button
                    class="icon-btn icon-btn--danger"
                    type="button"
                    :aria-label="t('common.delete')"
                    @click="removeProtocol(p)"
                  >
                    <AppIcon name="trash" :size="14" />
                  </button>
                </div>
              </div>
            </div>
          </Card>
        </div>

        <!-- ── Messages ─────────────────────────────────────────── -->
        <div v-show="activeTab === 'messages'">
          <Card :title="t('view.protocols.messages.title')" icon="message-square" margin-bottom="var(--sp-4)">
            <template #actions>
              <div class="msg-search">
                <input
                  v-model="recipientFilter"
                  class="msg-search__input"
                  type="text"
                  :placeholder="t('view.protocols.messages.recipient')"
                  @keyup.enter="loadMessages"
                />
                <button class="btn-ghost" :disabled="msgLoading" @click="loadMessages">
                  <AppIcon name="search" :size="14" />
                  <span>{{ t('view.protocols.messages.search') }}</span>
                </button>
              </div>
            </template>
            <div v-if="msgLoading" class="blk">
              <Skeleton block height="44px" />
              <Skeleton block height="44px" />
            </div>
            <EmptyState
              v-else-if="errors.messages"
              icon="alert-triangle"
              tone="fail"
              :title="t('view.protocols.messages.failedLoad')"
              :description="errors.messages"
            />
            <EmptyState
              v-else-if="!messages.length"
              icon="message-square"
              :title="t('view.protocols.messages.empty')"
              :description="t('view.protocols.messages.emptyHint')"
            />
            <div v-else class="msg-list">
              <details v-for="(m, i) in messages" :key="i" class="msg-item">
                <summary class="msg-item__head">
                  <div class="msg-item__main">
                    <AppIcon name="send" :size="14" class="msg-item__icon" />
                    <span class="msg-item__sender">{{ m.sender || '—' }}</span>
                    <AppIcon name="chevron-right" :size="12" class="msg-item__arrow" />
                    <span class="msg-item__recipient">{{ m.recipient || '—' }}</span>
                  </div>
                  <div class="msg-item__meta">
                    <Badge tone="info">{{ m.protocol || '—' }}</Badge>
                    <span class="msg-item__time">{{ formatTime(m.timestamp || m.ts) }}</span>
                    <AppIcon name="chevrondown" :size="14" class="msg-item__chevron" />
                  </div>
                </summary>
                <div class="msg-item__body">
                  <div class="msg-item__payload-label">{{ t('view.protocols.col.payload') }}</div>
                  <pre class="msg-item__payload mono">{{ formatPayload(m.payload) }}</pre>
                </div>
              </details>
            </div>
          </Card>
        </div>
      </template>
    </ListPageLayout>

    <!-- ── Register protocol modal ────────────────────────────── -->
    <Teleport to="body">
      <div
        v-if="showAddModal"
        v-modal-a11y
        class="modal-overlay"
        @click.self="closeAddModal"
        @modal:escape="closeAddModal"
      >
        <div class="modal" role="dialog" aria-modal="true">
          <div class="modal__head">
            <h3>{{ t('view.protocols.modal.addTitle') }}</h3>
            <button class="modal__x" type="button" :aria-label="t('common.close')" @click="closeAddModal">
              <AppIcon name="x" :size="16" />
            </button>
          </div>
          <div class="modal__body">
            <div class="field-row">
              <label class="field">
                <span class="field__label">{{ t('view.protocols.modal.name') }}</span>
                <input v-model="protoForm.name" class="field__input" :placeholder="t('view.protocols.modal.name')" />
              </label>
              <label class="field">
                <span class="field__label">{{ t('view.protocols.modal.version') }}</span>
                <input v-model="protoForm.version" class="field__input" placeholder="1.0" />
              </label>
            </div>
            <label class="field">
              <span class="field__label">{{ t('view.protocols.modal.participants') }}</span>
              <textarea
                v-model="protoForm.participantsText"
                class="field__input field__textarea"
                rows="3"
                :placeholder="t('view.protocols.modal.participants')"
              />
            </label>
            <label class="field">
              <span class="field__label">{{ t('view.protocols.modal.description') }}</span>
              <textarea
                v-model="protoForm.description"
                class="field__input field__textarea"
                rows="2"
                :placeholder="t('view.protocols.modal.description')"
              />
            </label>
            <label class="field">
              <span class="field__label">{{ t('view.protocols.modal.schema') }}</span>
              <textarea
                v-model="protoForm.schemaText"
                class="field__input field__textarea mono"
                rows="4"
                placeholder='{}'
              />
            </label>
          </div>
          <div class="modal__foot">
            <button class="btn-ghost" type="button" @click="closeAddModal">{{ t('common.cancel') }}</button>
            <button
              class="btn-primary"
              type="button"
              :disabled="!protoForm.name.trim() || saving"
              @click="submitProtocol"
            >
              {{ saving ? t('view.protocols.modal.saving') : t('common.save') }}
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
const activeTab = ref('protocols');
const tabOptions = [
  { value: 'protocols', label: t('view.protocols.tab.protocols'), icon: 'network' },
  { value: 'messages', label: t('view.protocols.tab.messages'), icon: 'message-square' },
];

// ── Data state ──────────────────────────────────────────────────
const loading = ref(false);
const errors = ref({ protocols: null, messages: null });

const protocols = ref([]);
const messages = ref([]);

// ── Messages filter ─────────────────────────────────────────────
const recipientFilter = ref('');
const msgLoading = ref(false);

// ── Add protocol modal ──────────────────────────────────────────
const showAddModal = ref(false);
const saving = ref(false);
const protoForm = reactive({
  name: '',
  version: '1.0',
  participantsText: '',
  description: '',
  schemaText: '{}',
});

function resetForm() {
  protoForm.name = '';
  protoForm.version = '1.0';
  protoForm.participantsText = '';
  protoForm.description = '';
  protoForm.schemaText = '{}';
}

function openAdd() {
  resetForm();
  showAddModal.value = true;
}

function closeAddModal() {
  showAddModal.value = false;
}

// ── Helpers ─────────────────────────────────────────────────────
function formatTime(ts) {
  if (ts === null || ts === undefined || ts === '') return '—';
  const d = new Date(typeof ts === 'number' ? ts : String(ts));
  if (isNaN(d.getTime())) return String(ts);
  return d.toLocaleString();
}

function formatPayload(payload) {
  if (payload === null || payload === undefined) return '—';
  if (typeof payload === 'string') return payload;
  try {
    return JSON.stringify(payload, null, 2);
  } catch {
    return String(payload);
  }
}

function parseSchema(text) {
  if (!text || !text.trim()) return {};
  try {
    return JSON.parse(text);
  } catch {
    return {};
  }
}

// ── Data loading ────────────────────────────────────────────────
async function loadProtocols() {
  loading.value = true;
  try {
    const data = await api.get('/api/protocol/list');
    protocols.value = data.protocols || [];
    errors.value = { ...errors.value, protocols: null };
  } catch (err) {
    protocols.value = [];
    errors.value = { ...errors.value, protocols: (err && err.message) || t('view.protocols.list.failedLoad') };
  } finally {
    loading.value = false;
  }
}

async function loadMessages() {
  const recipient = recipientFilter.value.trim();
  if (!recipient) {
    toast.warn(t('view.protocols.messages.recipientRequired'));
    return;
  }
  msgLoading.value = true;
  try {
    const params = new URLSearchParams({ recipient, limit: '100' });
    const data = await api.get(`/api/protocol/messages?${params.toString()}`);
    messages.value = data.messages || [];
    errors.value = { ...errors.value, messages: null };
  } catch (err) {
    messages.value = [];
    errors.value = { ...errors.value, messages: (err && err.message) || t('view.protocols.messages.failedLoad') };
  } finally {
    msgLoading.value = false;
  }
}

// ── Register protocol ───────────────────────────────────────────
async function submitProtocol() {
  const name = protoForm.name.trim();
  if (!name) return;
  saving.value = true;
  try {
    const participants = protoForm.participantsText
      .split('\n')
      .map((s) => s.trim())
      .filter(Boolean);
    const payload = {
      name,
      version: protoForm.version.trim() || '1.0',
      schema: parseSchema(protoForm.schemaText),
      participants,
      description: protoForm.description.trim(),
    };
    await api.post('/api/protocol/register', payload);
    toast.success(t('view.protocols.list.saved'));
    showAddModal.value = false;
    await loadProtocols();
  } catch (err) {
    toast.error((err && err.message) || t('view.protocols.list.saveFailed'));
  } finally {
    saving.value = false;
  }
}

// ── Unregister protocol ─────────────────────────────────────────
async function removeProtocol(p) {
  const ok = await showConfirm({
    message: t('view.protocols.list.deleteConfirm'),
    tone: 'danger',
  });
  if (!ok) return;
  try {
    await api.post('/api/protocol/unregister', { name: p.name, version: p.version || '1.0' });
    toast.success(t('view.protocols.list.deleted'));
    await loadProtocols();
  } catch (err) {
    toast.error((err && err.message) || t('view.protocols.list.deleteFailed'));
  }
}

onMounted(() => {
  loadProtocols();
});
</script>

<style scoped>
/* ── Layout helpers ────────────────────────────────────────────── */
.blk { display: flex; flex-direction: column; gap: var(--sp-2); }
.muted { color: var(--text-muted); }
.mono { font-family: var(--font-mono); font-size: var(--fs-xs); }

/* ── Ghost / primary buttons ──────────────────────────────────── */
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

/* ── Protocol table ───────────────────────────────────────────── */
.proto-table {
  width: 100%;
  border: 1px solid var(--border-subtle);
  border-radius: var(--r-md);
  overflow: hidden;
}
.proto-table__head,
.proto-table__row {
  display: grid;
  grid-template-columns: 1fr auto 1.2fr 1.5fr 64px;
  align-items: center;
  gap: var(--sp-2);
}
.proto-table__head {
  background: var(--surface-2);
  padding: var(--sp-2) var(--sp-3);
  border-bottom: 1px solid var(--border-strong);
}
.proto-table__th {
  font-size: var(--fs-xs);
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: .03em;
  color: var(--text-muted);
}
.proto-table__th--act { text-align: right; }
.proto-table__row {
  padding: var(--sp-3);
  border-bottom: 1px solid var(--border-subtle);
  transition: background var(--motion) var(--ease);
}
.proto-table__row:hover { background: var(--surface-2); }
.proto-table__row:last-child { border-bottom: none; }
.proto-table__td { min-width: 0; }
.proto-table__td--act { display: flex; justify-content: flex-end; gap: var(--sp-1); }
.proto-table__name { font-weight: 600; color: var(--text); }
.proto-table__more { font-size: var(--fs-xs); color: var(--text-muted); margin-left: var(--sp-1); }
.proto-table__desc { color: var(--text-muted); font-size: var(--fs-sm); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

/* ── Messages ─────────────────────────────────────────────────── */
.msg-search { display: flex; align-items: center; gap: var(--sp-2); }
.msg-search__input {
  padding: var(--sp-1) var(--sp-3);
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: var(--r-md);
  color: var(--text);
  font-size: var(--fs-sm);
  font-family: inherit;
  transition: border-color var(--motion) var(--ease);
}
.msg-search__input:focus { outline: none; border-color: var(--brand); box-shadow: 0 0 0 3px var(--brand-soft); }
.msg-search__input::placeholder { color: var(--text-faint); }

.msg-list { display: flex; flex-direction: column; gap: var(--sp-2); }
.msg-item {
  border: 1px solid var(--border-subtle);
  border-radius: var(--r-md);
  background: var(--surface);
  transition: border-color var(--motion) var(--ease);
}
.msg-item:hover { border-color: var(--border-strong); }
.msg-item__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--sp-3);
  padding: var(--sp-3);
  cursor: pointer;
  list-style: none;
  user-select: none;
}
.msg-item__head::-webkit-details-marker { display: none; }
.msg-item__main { display: flex; align-items: center; gap: var(--sp-2); min-width: 0; }
.msg-item__icon { color: var(--brand-strong); flex-shrink: 0; }
.msg-item__sender { font-weight: 600; color: var(--text); }
.msg-item__arrow { color: var(--text-faint); }
.msg-item__recipient { color: var(--text); }
.msg-item__meta { display: flex; align-items: center; gap: var(--sp-2); flex-shrink: 0; }
.msg-item__time { font-size: var(--fs-xs); color: var(--text-muted); }
.msg-item__chevron { color: var(--text-faint); transition: transform var(--motion) var(--ease); }
.msg-item[open] .msg-item__chevron { transform: rotate(180deg); }
.msg-item__body { padding: 0 var(--sp-3) var(--sp-3); border-top: 1px solid var(--border-subtle); }
.msg-item__payload-label { font-size: var(--fs-xs); font-weight: 600; text-transform: uppercase; letter-spacing: .03em; color: var(--text-muted); margin: var(--sp-2) 0 var(--sp-1); }
.msg-item__payload {
  background: var(--surface-2);
  border: 1px solid var(--border-subtle);
  border-radius: var(--r-sm);
  padding: var(--sp-2) var(--sp-3);
  font-size: var(--fs-xs);
  color: var(--text);
  overflow-x: auto;
  white-space: pre-wrap;
  max-height: 240px;
  overflow-y: auto;
  margin: 0;
}

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
.field__textarea { resize: vertical; min-height: 60px; }
.field-row { display: grid; grid-template-columns: 1fr 1fr; gap: var(--sp-3); }

/* ── Responsive ───────────────────────────────────────────────── */
@media (max-width: 900px) {
  .field-row { grid-template-columns: 1fr; }
  .proto-table__head,
  .proto-table__row { grid-template-columns: 1fr auto 1fr 64px; }
  .proto-table__th:nth-child(4),
  .proto-table__td:nth-child(4) { display: none; }
}

@media (max-width: 640px) {
  .proto-table__head,
  .proto-table__row { grid-template-columns: 1fr 64px; }
  .proto-table__th:nth-child(2),
  .proto-table__td:nth-child(2),
  .proto-table__th:nth-child(3),
  .proto-table__td:nth-child(3) { display: none; }
  .msg-search { flex-direction: column; align-items: stretch; }
  .msg-item__meta .msg-item__time { display: none; }
  .modal__body { padding: var(--sp-3); }
  .modal__foot { flex-direction: column-reverse; }
  .modal__foot .btn-ghost,
  .modal__foot .btn-primary { width: 100%; justify-content: center; }
}
</style>
