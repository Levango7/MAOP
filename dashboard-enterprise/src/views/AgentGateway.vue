<template>
  <div class="agentgateway-view">
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
          <span>{{ t('view.agentGateway.permissions.refresh') }}</span>
        </button>
      </template>
      <template #content>
        <!-- ── Permissions ─────────────────────────────────────── -->
        <div v-show="activeTab === 'permissions'">
          <Card :title="t('view.agentGateway.permissions.title')" icon="shield" margin-bottom="var(--sp-4)">
            <template #actions>
              <button class="btn-ghost btn-ghost--sm" type="button" @click="openAddForm">
                <AppIcon name="plus" :size="14" />
                <span>{{ t('view.agentGateway.permissions.add') }}</span>
              </button>
            </template>
            <div v-if="loading" class="blk">
              <Skeleton block height="44px" />
              <Skeleton block height="44px" />
              <Skeleton block height="44px" />
            </div>
            <EmptyState
              v-else-if="errors.permissions"
              icon="alert-triangle"
              tone="fail"
              :title="t('view.agentGateway.permissions.failedLoad')"
              :description="errors.permissions"
            />
            <EmptyState
              v-else-if="!permissions.length"
              icon="shield"
              :title="t('view.agentGateway.permissions.empty')"
              :description="t('view.agentGateway.permissions.emptyHint')"
            />
            <div v-else class="gw-table" role="table" :aria-label="t('view.agentGateway.permissions.title')">
              <div class="gw-table__head" role="row">
                <div class="gw-table__th" role="columnheader">{{ t('view.agentGateway.permissions.col.pattern') }}</div>
                <div class="gw-table__th" role="columnheader">{{ t('view.agentGateway.permissions.col.allowed') }}</div>
                <div class="gw-table__th gw-table__th--num" role="columnheader">{{ t('view.agentGateway.permissions.col.maxTokens') }}</div>
                <div class="gw-table__th gw-table__th--num" role="columnheader">{{ t('view.agentGateway.permissions.col.dailyLimit') }}</div>
                <div class="gw-table__th gw-table__th--num" role="columnheader">{{ t('view.agentGateway.permissions.col.priority') }}</div>
                <div class="gw-table__th gw-table__th--act" role="columnheader">{{ t('view.agentGateway.permissions.col.actions') }}</div>
              </div>
              <div v-for="p in permissions" :key="p.model_pattern" class="gw-table__row" role="row">
                <div class="gw-table__td" role="cell">
                  <span class="gw-table__name mono">{{ p.model_pattern }}</span>
                </div>
                <div class="gw-table__td" role="cell">
                  <Badge :tone="p.allowed ? 'success' : 'fail'">
                    {{ p.allowed ? t('view.agentGateway.form.allowed') : t('view.agentGateway.form.denied') }}
                  </Badge>
                </div>
                <div class="gw-table__td gw-table__td--num" role="cell">
                  <span class="mono">{{ formatLimit(p.max_tokens_per_call) }}</span>
                </div>
                <div class="gw-table__td gw-table__td--num" role="cell">
                  <span class="mono">{{ formatLimit(p.daily_token_limit) }}</span>
                </div>
                <div class="gw-table__td gw-table__td--num" role="cell">
                  <span class="mono">{{ p.priority ?? 100 }}</span>
                </div>
                <div class="gw-table__td gw-table__td--act" role="cell">
                  <button
                    class="icon-btn"
                    type="button"
                    :aria-label="t('view.agentGateway.permissions.edit')"
                    @click="openEditForm(p)"
                  >
                    <AppIcon name="gear" :size="14" />
                  </button>
                  <button
                    class="icon-btn"
                    type="button"
                    :aria-label="t('view.agentGateway.permissions.delete')"
                    @click="deletePermission(p)"
                  >
                    <AppIcon name="trash" :size="14" />
                  </button>
                </div>
              </div>
            </div>
          </Card>
        </div>

        <!-- ── Access Check ────────────────────────────────────── -->
        <div v-show="activeTab === 'accessCheck'">
          <Card :title="t('view.agentGateway.accessCheck.title')" icon="search" margin-bottom="var(--sp-4)">
            <p class="ac-hint">{{ t('view.agentGateway.accessCheck.hint') }}</p>
            <div class="ac-form">
              <label class="field">
                <span class="field__label">{{ t('view.agentGateway.accessCheck.model') }}</span>
                <input
                  v-model="checkForm.model"
                  type="text"
                  class="field__input"
                  :placeholder="t('view.agentGateway.accessCheck.modelHint')"
                />
              </label>
              <label class="field">
                <span class="field__label">{{ t('view.agentGateway.accessCheck.agent') }}</span>
                <input v-model="checkForm.agent" type="text" class="field__input" />
              </label>
              <label class="field">
                <span class="field__label">{{ t('view.agentGateway.accessCheck.sessionId') }}</span>
                <input v-model="checkForm.session_id" type="text" class="field__input" />
              </label>
              <div class="ac-form__actions">
                <button
                  class="btn-primary"
                  type="button"
                  :disabled="checking"
                  @click="runCheck"
                >
                  <AppIcon name="search" :size="14" />
                  <span>{{ checking ? t('view.agentGateway.accessCheck.checking') : t('view.agentGateway.accessCheck.check') }}</span>
                </button>
              </div>
              <p v-if="checkError" class="ac-err">{{ checkError }}</p>
              <div v-if="checkResult" class="ac-result">
                <span class="field__label">{{ t('view.agentGateway.accessCheck.result') }}</span>
                <div class="ac-result__grid">
                  <div class="ac-result__row">
                    <span class="ac-result__key">{{ t('view.agentGateway.permissions.col.allowed') }}</span>
                    <span class="ac-result__val">
                      <Badge :tone="checkResult.allowed ? 'success' : 'fail'">
                        {{ checkResult.allowed ? t('view.agentGateway.accessCheck.allowed') : t('view.agentGateway.accessCheck.denied') }}
                      </Badge>
                    </span>
                  </div>
                  <div class="ac-result__row">
                    <span class="ac-result__key">{{ t('view.agentGateway.accessCheck.matchedRule') }}</span>
                    <span class="ac-result__val mono">{{ checkResult.matched_rule || t('view.agentGateway.accessCheck.noMatch') }}</span>
                  </div>
                  <div class="ac-result__row">
                    <span class="ac-result__key">{{ t('view.agentGateway.accessCheck.maxTokens') }}</span>
                    <span class="ac-result__val mono">{{ formatLimit(checkResult.max_tokens_per_call) }}</span>
                  </div>
                  <div class="ac-result__row">
                    <span class="ac-result__key">{{ t('view.agentGateway.accessCheck.remaining') }}</span>
                    <span class="ac-result__val mono">{{ formatLimit(checkResult.remaining_daily_quota) }}</span>
                  </div>
                  <div v-if="checkResult.reason" class="ac-result__row">
                    <span class="ac-result__key">{{ t('view.agentGateway.accessCheck.reason') }}</span>
                    <span class="ac-result__val">{{ checkResult.reason }}</span>
                  </div>
                </div>
              </div>
            </div>
          </Card>
        </div>

        <!-- ── Usage ───────────────────────────────────────────── -->
        <div v-show="activeTab === 'usage'">
          <Card :title="t('view.agentGateway.usage.todayTitle')" icon="gauge" margin-bottom="var(--sp-4)">
            <template #actions>
              <button
                class="btn-ghost btn-ghost--sm"
                type="button"
                :disabled="clearing"
                @click="clearTodayUsage"
              >
                <AppIcon name="trash" :size="14" />
                <span>{{ clearing ? t('view.agentGateway.usage.clearing') : t('view.agentGateway.usage.clearToday') }}</span>
              </button>
            </template>
            <div v-if="loading" class="blk">
              <Skeleton block height="44px" />
              <Skeleton block height="44px" />
            </div>
            <EmptyState
              v-else-if="errors.usage"
              icon="alert-triangle"
              tone="fail"
              :title="t('view.agentGateway.usage.failedLoad')"
              :description="errors.usage"
            />
            <EmptyState
              v-else-if="!usageRows.length"
              icon="chart"
              :title="t('view.agentGateway.usage.empty')"
              :description="t('view.agentGateway.usage.emptyHint')"
            />
            <div v-else class="gw-table" role="table" :aria-label="t('view.agentGateway.usage.todayTitle')">
              <div class="gw-table__head" role="row">
                <div class="gw-table__th" role="columnheader">{{ t('view.agentGateway.usage.col.model') }}</div>
                <div class="gw-table__th gw-table__th--num" role="columnheader">{{ t('view.agentGateway.usage.col.tokens') }}</div>
                <div class="gw-table__th" role="columnheader">{{ t('view.agentGateway.usage.col.agent') }}</div>
              </div>
              <div v-for="(u, i) in usageRows" :key="u.model + '-' + i" class="gw-table__row" role="row">
                <div class="gw-table__td" role="cell">
                  <span class="gw-table__name mono">{{ u.model }}</span>
                </div>
                <div class="gw-table__td gw-table__td--num" role="cell">
                  <span class="mono">{{ u.tokens ?? u.token_count ?? 0 }}</span>
                </div>
                <div class="gw-table__td" role="cell">
                  <span class="muted">{{ u.agent || '—' }}</span>
                </div>
              </div>
            </div>
          </Card>

          <Card :title="t('view.agentGateway.usage.configTitle')" icon="gear" margin-bottom="var(--sp-4)">
            <div class="cfg-form">
              <label class="field">
                <span class="field__label">{{ t('view.agentGateway.usage.globalLimit') }}</span>
                <input
                  v-model.number="configForm.global_daily_token_limit"
                  type="number"
                  min="0"
                  class="field__input"
                  :placeholder="t('view.agentGateway.usage.globalLimitHint')"
                />
                <span class="field__hint">{{ t('view.agentGateway.usage.globalLimitHint') }}</span>
              </label>
              <label class="field">
                <span class="field__label">{{ t('view.agentGateway.usage.defaultPolicy') }}</span>
                <Segmented
                  :model-value="configForm.default_policy"
                  :options="policyOptions"
                  size="sm"
                  @update:model-value="configForm.default_policy = $event"
                />
              </label>
              <div class="cfg-form__actions">
                <button
                  class="btn-primary"
                  type="button"
                  :disabled="updatingConfig"
                  @click="updateConfig"
                >
                  <AppIcon name="check" :size="14" />
                  <span>{{ updatingConfig ? t('view.agentGateway.usage.updating') : t('view.agentGateway.usage.updateConfig') }}</span>
                </button>
              </div>
            </div>
          </Card>
        </div>
      </template>
    </ListPageLayout>

    <!-- ── Permission Form Modal ──────────────────────────────── -->
    <Teleport to="body">
      <div
        v-if="showFormModal"
        v-modal-a11y
        class="modal-overlay"
        @click.self="closeForm"
        @modal:escape="closeForm"
      >
        <div class="modal" role="dialog" aria-modal="true">
          <div class="modal__head">
            <h3>{{ editing ? t('view.agentGateway.form.titleEdit') : t('view.agentGateway.form.titleAdd') }}</h3>
            <button class="modal__x" type="button" :aria-label="t('common.close')" @click="closeForm">
              <AppIcon name="x" :size="16" />
            </button>
          </div>
          <div class="modal__body">
            <label class="field">
              <span class="field__label">{{ t('view.agentGateway.form.modelPattern') }}</span>
              <input
                v-model="formData.model_pattern"
                type="text"
                class="field__input mono"
                :placeholder="t('view.agentGateway.form.modelPatternHint')"
              />
            </label>
            <label class="field">
              <span class="field__label">{{ t('view.agentGateway.permissions.col.allowed') }}</span>
              <Segmented
                :model-value="formData.allowed ? 'allow' : 'deny'"
                :options="decisionOptions"
                size="sm"
                @update:model-value="formData.allowed = $event === 'allow'"
              />
            </label>
            <label class="field">
              <span class="field__label">{{ t('view.agentGateway.form.maxTokensPerCall') }}</span>
              <input
                v-model.number="formData.max_tokens_per_call"
                type="number"
                min="0"
                class="field__input"
              />
              <span class="field__hint">{{ t('view.agentGateway.form.maxTokensPerCallHint') }}</span>
            </label>
            <label class="field">
              <span class="field__label">{{ t('view.agentGateway.form.dailyTokenLimit') }}</span>
              <input
                v-model.number="formData.daily_token_limit"
                type="number"
                min="0"
                class="field__input"
              />
              <span class="field__hint">{{ t('view.agentGateway.form.dailyTokenLimitHint') }}</span>
            </label>
            <label class="field">
              <span class="field__label">{{ t('view.agentGateway.form.priority') }}</span>
              <input
                v-model.number="formData.priority"
                type="number"
                class="field__input"
              />
              <span class="field__hint">{{ t('view.agentGateway.form.priorityHint') }}</span>
            </label>
            <p v-if="formError" class="modal__err">{{ formError }}</p>
          </div>
          <div class="modal__foot">
            <button class="btn-ghost" type="button" @click="closeForm">{{ t('view.agentGateway.form.cancel') }}</button>
            <button
              class="btn-primary"
              type="button"
              :disabled="saving"
              @click="submitForm"
            >
              {{ saving ? t('common.loading') : t('view.agentGateway.form.submit') }}
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
const toast = useToast();
const { t } = useI18n();

// ── Tab state ───────────────────────────────────────────────────
const activeTab = ref('permissions');
const tabOptions = [
  { value: 'permissions', label: t('view.agentGateway.tab.permissions'), icon: 'shield' },
  { value: 'accessCheck', label: t('view.agentGateway.tab.accessCheck'), icon: 'search' },
  { value: 'usage', label: t('view.agentGateway.tab.usage'), icon: 'gauge' },
];

const decisionOptions = computed(() => [
  { value: 'allow', label: t('view.agentGateway.form.allowed') },
  { value: 'deny', label: t('view.agentGateway.form.denied') },
]);

const policyOptions = computed(() => [
  { value: 'allow', label: t('view.agentGateway.usage.defaultPolicyAllow') },
  { value: 'deny', label: t('view.agentGateway.usage.defaultPolicyDeny') },
]);

// ── Data state ──────────────────────────────────────────────────
const loading = ref(false);
const errors = ref({ permissions: null, usage: null });
const permissions = ref([]);
const usageRows = ref([]);

// ── Helpers ─────────────────────────────────────────────────────
function formatLimit(n) {
  if (n == null) return '—';
  const num = Number(n);
  if (isNaN(num)) return '—';
  if (num === 0) return t('view.agentGateway.accessCheck.unlimited');
  return String(num);
}

// ── Permission form modal ───────────────────────────────────────
const showFormModal = ref(false);
const editing = ref(false);
const saving = ref(false);
const formError = ref('');
const formData = reactive({
  model_pattern: '',
  allowed: true,
  max_tokens_per_call: 0,
  daily_token_limit: 0,
  priority: 100,
});

function resetForm() {
  formData.model_pattern = '';
  formData.allowed = true;
  formData.max_tokens_per_call = 0;
  formData.daily_token_limit = 0;
  formData.priority = 100;
}

function openAddForm() {
  editing.value = false;
  resetForm();
  formError.value = '';
  showFormModal.value = true;
}

function openEditForm(p) {
  editing.value = true;
  formData.model_pattern = p.model_pattern || '';
  formData.allowed = p.allowed !== false;
  formData.max_tokens_per_call = p.max_tokens_per_call ?? 0;
  formData.daily_token_limit = p.daily_token_limit ?? 0;
  formData.priority = p.priority ?? 100;
  formError.value = '';
  showFormModal.value = true;
}

function closeForm() {
  showFormModal.value = false;
  formError.value = '';
}

async function submitForm() {
  const pattern = formData.model_pattern.trim();
  if (!pattern) {
    formError.value = t('view.agentGateway.form.validatePattern');
    return;
  }
  saving.value = true;
  formError.value = '';
  try {
    const payload = {
      model_pattern: pattern,
      allowed: formData.allowed,
      max_tokens_per_call: Number(formData.max_tokens_per_call) || 0,
      daily_token_limit: Number(formData.daily_token_limit) || 0,
      priority: Number(formData.priority) || 100,
    };
    if (editing.value) {
      await api.put(`/api/model-gateway/permissions/${encodeURIComponent(pattern)}`, payload);
      toast.success(t('view.agentGateway.form.successUpdate'));
    } else {
      await api.post('/api/model-gateway/permissions', payload);
      toast.success(t('view.agentGateway.form.successAdd'));
    }
    showFormModal.value = false;
    await loadPermissions();
  } catch (err) {
    const msg = (err && err.message) || t('view.agentGateway.form.failedSave');
    formError.value = msg;
    toast.error(msg);
  } finally {
    saving.value = false;
  }
}

async function deletePermission(p) {
  const pattern = p.model_pattern;
  if (!pattern) return;
  // 使用原生 confirm 对话框——权限规则删除是低频操作，无需自定义模态框
  if (!window.confirm(t('view.agentGateway.permissions.confirmDelete', { pattern }))) return;
  try {
    await api.delete(`/api/model-gateway/permissions/${encodeURIComponent(pattern)}`);
    toast.success(t('view.agentGateway.permissions.deleteSuccess'));
    await loadPermissions();
  } catch (err) {
    toast.error((err && err.message) || t('view.agentGateway.permissions.deleteFailed'));
  }
}

// ── Access check ────────────────────────────────────────────────
const checking = ref(false);
const checkError = ref('');
const checkResult = ref(null);
const checkForm = reactive({
  model: '',
  agent: '',
  session_id: '',
});

async function runCheck() {
  const model = checkForm.model.trim();
  if (!model) {
    toast.warn(t('view.agentGateway.accessCheck.validateModel'));
    return;
  }
  checking.value = true;
  checkError.value = '';
  checkResult.value = null;
  try {
    const payload = { model };
    if (checkForm.agent.trim()) payload.agent = checkForm.agent.trim();
    if (checkForm.session_id.trim()) payload.session_id = checkForm.session_id.trim();
    const data = await api.post('/api/model-gateway/check', payload);
    checkResult.value = data.result ?? data;
  } catch (err) {
    const msg = (err && err.message) || t('view.agentGateway.accessCheck.failed');
    checkError.value = msg;
    toast.error(msg);
  } finally {
    checking.value = false;
  }
}

// ── Usage config ────────────────────────────────────────────────
const updatingConfig = ref(false);
const clearing = ref(false);
const configForm = reactive({
  global_daily_token_limit: 0,
  default_policy: 'allow',
});

async function updateConfig() {
  updatingConfig.value = true;
  try {
    await api.put('/api/model-gateway/config', {
      global_daily_token_limit: Number(configForm.global_daily_token_limit) || 0,
      default_policy: configForm.default_policy,
    });
    toast.success(t('view.agentGateway.usage.successConfig'));
  } catch (err) {
    toast.error((err && err.message) || t('view.agentGateway.usage.failedConfig'));
  } finally {
    updatingConfig.value = false;
  }
}

async function clearTodayUsage() {
  // 使用原生 confirm 对话框——清空使用量是低频操作
  if (!window.confirm(t('view.agentGateway.usage.confirmClear'))) return;
  clearing.value = true;
  try {
    await api.delete('/api/model-gateway/usage');
    toast.success(t('view.agentGateway.usage.successClear'));
    await loadUsage();
  } catch (err) {
    toast.error((err && err.message) || t('view.agentGateway.usage.failedClear'));
  } finally {
    clearing.value = false;
  }
}

// ── Data loading ────────────────────────────────────────────────
async function loadPermissions() {
  try {
    const data = await api.get('/api/model-gateway/permissions');
    permissions.value = data.permissions || data.rules || [];
    errors.value = { ...errors.value, permissions: null };
  } catch (err) {
    permissions.value = [];
    errors.value = { ...errors.value, permissions: (err && err.message) || t('view.agentGateway.permissions.failedLoad') };
  }
}

async function loadUsage() {
  try {
    const data = await api.get('/api/model-gateway/usage');
    usageRows.value = data.usage || data.records || [];
    if (data.config) {
      configForm.global_daily_token_limit = data.config.global_daily_token_limit ?? 0;
      configForm.default_policy = data.config.default_policy || 'allow';
    }
    errors.value = { ...errors.value, usage: null };
  } catch (err) {
    usageRows.value = [];
    errors.value = { ...errors.value, usage: (err && err.message) || t('view.agentGateway.usage.failedLoad') };
  }
}

async function loadAll() {
  loading.value = true;
  await Promise.allSettled([loadPermissions(), loadUsage()]);
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
/* NOTE: .btn-ghost/.btn-primary/.modal 等为多 view 共享样式，重复定义见
   AgentBilling/AgentProxy/AgentRegistry/Analysis/Blackboard/Debate/Feedback。
   全局基础样式见 src/styles/pages.css（BEM 命名 .btn--ghost），此处 scoped 隔离不冲突。 */
.btn-ghost {
  display: inline-flex; align-items: center; gap: var(--sp-1);
  background: var(--surface-2); color: var(--text);
  border: 1px solid var(--border); border-radius: var(--r-md);
  padding: var(--sp-2) var(--sp-3); font-size: var(--fs-sm); font-weight: 600;
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

/* ── Permission / Usage table ──────────────────────────────────── */
.gw-table {
  width: 100%;
  border: 1px solid var(--border-subtle);
  border-radius: var(--r-md);
  overflow: hidden;
}
.gw-table__head,
.gw-table__row {
  display: grid;
  grid-template-columns: 1fr 100px 120px 120px 90px 90px;
  align-items: center;
  gap: var(--sp-2);
}
.gw-table__head {
  background: var(--surface-2);
  padding: var(--sp-2) var(--sp-3);
  border-bottom: 1px solid var(--border-strong);
}
.gw-table__th {
  font-size: var(--fs-xs);
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: .03em;
  color: var(--text-muted);
}
.gw-table__th--num { text-align: right; }
.gw-table__th--act { text-align: right; }
.gw-table__row {
  padding: var(--sp-3);
  border-bottom: 1px solid var(--border-subtle);
  transition: background var(--motion) var(--ease);
}
.gw-table__row:hover { background: var(--surface-2); }
.gw-table__row:last-child { border-bottom: none; }
.gw-table__td { min-width: 0; }
.gw-table__name { font-weight: 600; color: var(--text); font-size: var(--fs-sm); }
.gw-table__td--num { text-align: right; }
.gw-table__td--act {
  display: flex; gap: var(--sp-1); justify-content: flex-end; flex-wrap: wrap;
}

/* ── Access check ─────────────────────────────────────────────── */
.ac-hint {
  font-size: var(--fs-sm); color: var(--text-muted);
  margin: 0 0 var(--sp-3); line-height: 1.5;
}
.ac-form { display: flex; flex-direction: column; gap: var(--sp-3); }
.ac-form__actions { display: flex; justify-content: flex-end; }
.ac-err { color: var(--fail); font-size: var(--fs-sm); margin: 0; }
.ac-result { display: flex; flex-direction: column; gap: var(--sp-2); }
.ac-result__grid {
  display: flex; flex-direction: column; gap: var(--sp-2);
  background: var(--surface-2); border: 1px solid var(--border-subtle);
  border-radius: var(--r-md); padding: var(--sp-3);
}
.ac-result__row {
  display: grid; grid-template-columns: 160px 1fr; gap: var(--sp-2);
  align-items: center;
}
.ac-result__key {
  font-size: var(--fs-sm); font-weight: 600; color: var(--text-muted);
}
.ac-result__val { font-size: var(--fs-sm); color: var(--text); min-width: 0; word-break: break-word; }

/* ── Config form ──────────────────────────────────────────────── */
.cfg-form { display: flex; flex-direction: column; gap: var(--sp-3); }
.cfg-form__actions { display: flex; justify-content: flex-end; }

/* ── Form fields ───────────────────────────────────────────────── */
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
.field__input:focus-visible { outline: none; box-shadow: 0 0 0 2px var(--brand); }
.field__hint {
  font-size: var(--fs-xs); color: var(--text-muted);
}

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
.modal__err { color: var(--fail); font-size: var(--fs-sm); margin: 0; }
.modal__foot {
  display: flex; justify-content: flex-end; gap: var(--sp-2);
  margin-top: var(--sp-4);
}

/* ── Responsive ────────────────────────────────────────────────── */
@media (max-width: 900px) {
  .gw-table__head,
  .gw-table__row {
    grid-template-columns: 1fr 100px 90px 90px;
  }
  .gw-table__th--num:nth-child(3),
  .gw-table__td--num:nth-child(3) { display: none; }
  .ac-result__row { grid-template-columns: 1fr; }
}
@media (max-width: 640px) {
  .gw-table__head,
  .gw-table__row {
    grid-template-columns: 1fr 100px;
  }
  .gw-table__th--num,
  .gw-table__td--num { display: none; }
  .gw-table__th--act,
  .gw-table__td--act { display: none; }
}
</style>