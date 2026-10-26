<template>
  <div class="agent-versions">
    <PageHeader
      icon="layers"
      :title="t('view.agentVersions.title')"
      :subtitle="t('view.agentVersions.subtitle')"
    />

    <div class="av-filters">
      <input
        v-model="agentId"
        type="text"
        class="input"
        :placeholder="t('view.agentVersions.filter.agentPlaceholder')"
        @keyup.enter="load"
      />
      <select v-model="status" class="input" @change="load">
        <option value="">{{ t('view.agentVersions.filter.allStatuses') }}</option>
        <option v-for="s in STATUSES" :key="s" :value="s">{{ s }}</option>
      </select>
      <button class="btn-ghost" :disabled="loading" @click="load">
        <AppIcon name="refresh" :size="14" />
        <span>{{ t('common.refresh') }}</span>
      </button>
    </div>

    <p v-if="error" class="av-error">{{ error }}</p>
    <p v-if="notice" class="av-notice">{{ notice }}</p>

    <Card :title="t('view.agentVersions.listTitle')" icon="list">
      <p v-if="loading" class="muted">{{ t('common.loading') }}</p>
      <p v-else-if="!versions.length" class="muted">{{ t('view.agentVersions.empty') }}</p>
      <table v-else class="av-table">
        <thead>
          <tr>
            <th>{{ t('view.agentVersions.col.version') }}</th>
            <th>{{ t('view.agentVersions.col.agent') }}</th>
            <th>{{ t('view.agentVersions.col.status') }}</th>
            <th>{{ t('view.agentVersions.col.created') }}</th>
            <th>{{ t('view.agentVersions.col.actions') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="v in versions" :key="v.version_id">
            <td class="av-table__num">{{ v.version_number }}</td>
            <td>{{ v.agent_id }}</td>
            <td>
              <Badge :tone="statusTone(v.status)">{{ v.status }}</Badge>
            </td>
            <td class="muted">{{ v.created_at || '—' }}</td>
            <td class="av-table__actions">
              <!-- 非 active：可以激活、可以配灰度（灰度是"待发布版本"的动作） -->
              <template v-if="v.status !== 'active'">
                <button
                  class="act-btn small"
                  :disabled="busy === v.version_id"
                  @click="activate(v)"
                >
                  {{ t('view.agentVersions.action.activate') }}
                </button>
                <button
                  class="act-btn small"
                  :disabled="busy === v.version_id"
                  @click="openCanary(v)"
                >
                  {{ t('view.agentVersions.action.canary') }}
                </button>
              </template>
              <!-- active：回滚是"从当前版本退回去"，所以按钮只出现在这一行 -->
              <template v-else>
                <span class="muted">{{ t('view.agentVersions.activeNow') }}</span>
                <button
                  class="act-btn small"
                  :disabled="busy === v.version_id"
                  @click="openRollback(v)"
                >
                  {{ t('view.agentVersions.action.rollback') }}
                </button>
              </template>
            </td>
          </tr>
        </tbody>
      </table>
      <p v-if="meta.total > meta.page_size" class="av-pager muted">
        {{ t('view.agentVersions.pager', { total: meta.total, page: meta.page }) }}
        <button class="act-btn small" :disabled="meta.page <= 1 || loading" @click="go(meta.page - 1)">
          {{ t('view.agentVersions.prev') }}
        </button>
        <button
          class="act-btn small"
          :disabled="meta.page * meta.page_size >= meta.total || loading"
          @click="go(meta.page + 1)"
        >
          {{ t('view.agentVersions.next') }}
        </button>
      </p>
    </Card>

    <!-- 回滚：把当前 active 版本置 retired，把选中的目标版本置 active -->
    <div v-if="rollbackTarget" v-modal-a11y class="av-overlay" @click.self="rollbackTarget = null">
      <div class="av-dialog" role="dialog" aria-modal="true">
        <h3>{{ t('view.agentVersions.rollback.title') }}</h3>
        <p class="muted">
          {{ t('view.agentVersions.rollback.body', { version: rollbackTarget.version_number }) }}
        </p>

        <label class="av-dialog__field">
          <span class="av-dialog__label">{{ t('view.agentVersions.rollback.target') }} *</span>
          <select v-model="rollbackForm.target_version_id" class="input">
            <option value="">{{ t('view.agentVersions.rollback.targetPlaceholder') }}</option>
            <option v-for="c in rollbackCandidates" :key="c.version_id" :value="c.version_id">
              {{ c.version_number }} ({{ c.status }})
            </option>
          </select>
        </label>

        <label class="av-dialog__field">
          <span class="av-dialog__label">{{ t('view.agentVersions.rollback.reason') }}</span>
          <input v-model="rollbackForm.reason" type="text" class="input" :placeholder="t('view.agentVersions.rollback.reasonHint')" />
        </label>

        <p v-if="dialogError" class="av-error">{{ dialogError }}</p>

        <div class="av-dialog__actions">
          <button class="act-btn small" @click="rollbackTarget = null">{{ t('common.cancel') }}</button>
          <button class="act-btn small act-btn--danger" :disabled="submitting" @click="submitRollback">
            {{ t('view.agentVersions.rollback.submit') }}
          </button>
        </div>
      </div>
    </div>

    <!-- 灰度：percentage / strategy / user_groups / duration -->
    <div v-if="canaryTarget" v-modal-a11y class="av-overlay" @click.self="canaryTarget = null">
      <div class="av-dialog" role="dialog" aria-modal="true">
        <h3>{{ t('view.agentVersions.canary.title') }}</h3>
        <p class="muted">
          {{ t('view.agentVersions.canary.body', { version: canaryTarget.version_number }) }}
        </p>

        <label class="av-dialog__field">
          <span class="av-dialog__label">{{ t('view.agentVersions.canary.percentage') }} *</span>
          <input v-model.number="canaryForm.percentage" type="number" min="0" max="100" class="input" />
        </label>

        <label class="av-dialog__field">
          <span class="av-dialog__label">{{ t('view.agentVersions.canary.strategy') }}</span>
          <select v-model="canaryForm.strategy" class="input">
            <option v-for="s in CANARY_STRATEGIES" :key="s" :value="s">{{ s }}</option>
          </select>
        </label>

        <label class="av-dialog__field">
          <span class="av-dialog__label">{{ t('view.agentVersions.canary.groups') }}</span>
          <input
            v-model="canaryForm.groupsText"
            type="text"
            class="input"
            :placeholder="t('view.agentVersions.canary.groupsHint')"
          />
        </label>

        <label class="av-dialog__field">
          <span class="av-dialog__label">{{ t('view.agentVersions.canary.duration') }}</span>
          <input v-model.number="canaryForm.duration_seconds" type="number" min="0" class="input" :placeholder="t('view.agentVersions.canary.durationHint')" />
        </label>

        <p v-if="dialogError" class="av-error">{{ dialogError }}</p>

        <div class="av-dialog__actions">
          <button class="act-btn small" @click="canaryTarget = null">{{ t('common.cancel') }}</button>
          <button class="btn-primary" :disabled="submitting" @click="submitCanary">
            {{ t('view.agentVersions.canary.submit') }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue';
import { useApiStore } from '../stores/api.js';
import { useI18n } from '../i18n';
import PageHeader from '../components/PageHeader.vue';
import Card from '../components/Card.vue';
import Badge from '../components/Badge.vue';
import AppIcon from '../components/AppIcon.vue';

const { t } = useI18n();
const api = useApiStore();

const STATUSES = ['draft', 'active', 'retired', 'deleted'];

const agentId = ref('');
const status = ref('');
const loading = ref(false);
const error = ref('');
const versions = ref([]);
const meta = ref({ total: 0, page: 1, page_size: 20 });
const busy = ref('');
const notice = ref('');
const dialogError = ref('');
const submitting = ref(false);

function statusTone(s) {
  if (s === 'active') return 'success';
  if (s === 'draft') return 'brand';
  if (s === 'deleted') return 'danger';
  return 'neutral';
}

async function load(page = 1) {
  loading.value = true;
  error.value = '';
  try {
    const params = new URLSearchParams();
    if (agentId.value.trim()) params.set('agent_id', agentId.value.trim());
    if (status.value) params.set('status', status.value);
    params.set('page', String(page));
    params.set('page_size', String(meta.value.page_size));
    const data = await api.get(`/api/agent-versions?${params.toString()}`);
    versions.value = data.data || [];
    meta.value = data.meta || { total: 0, page, page_size: 20 };
  } catch {
    // 具体异常进日志；界面上"加载失败"已足够让用户重试。
    error.value = t('view.agentVersions.loadFailed');
  } finally {
    loading.value = false;
  }
}

function go(page) {
  load(page);
}

async function activate(v) {
  busy.value = v.version_id;
  error.value = '';
  try {
    await api.post(`/api/agent-versions/${encodeURIComponent(v.version_id)}/activate`, {});
    await load(meta.value.page);
  } catch {
    error.value = t('view.agentVersions.activateFailed');
  } finally {
    busy.value = '';
  }
}

// ── 回滚 ──────────────────────────────────────────────────────
// 语义：POST /{version_id}/rollback + target ⇒ version_id 置 retired、
// target 置 active。所以入口只放在 active 行上，选"退回到哪个版本"。
const rollbackTarget = ref(null);
const rollbackCandidates = ref([]);
const rollbackForm = reactive({ target_version_id: '', reason: '' });

async function openRollback(v) {
  rollbackTarget.value = v;
  rollbackForm.target_version_id = '';
  rollbackForm.reason = '';
  dialogError.value = '';
  rollbackCandidates.value = [];
  try {
    // 当前页可能只有 20 条，同 agent 的旧版本未必在内 —— 单独按 agent_id 拉一次，
    // 否则"回滚目标"列表会缺项。失败则退回当前页数据。
    const data = await api.get(
      `/api/agent-versions?agent_id=${encodeURIComponent(v.agent_id)}&page_size=200`,
    );
    rollbackCandidates.value = (data.data || []).filter(
      (c) => c.version_id !== v.version_id && c.status !== 'deleted',
    );
  } catch {
    rollbackCandidates.value = versions.value.filter(
      (c) => c.version_id !== v.version_id && c.status !== 'deleted',
    );
  }
}

async function submitRollback() {
  if (submitting.value) return;
  dialogError.value = '';
  if (!rollbackForm.target_version_id) {
    dialogError.value = t('view.agentVersions.rollback.targetRequired');
    return;
  }
  submitting.value = true;
  try {
    await api.post(
      `/api/agent-versions/${encodeURIComponent(rollbackTarget.value.version_id)}/rollback`,
      {
        target_version_id: rollbackForm.target_version_id,
        reason: rollbackForm.reason.trim(),
      },
    );
    notice.value = t('view.agentVersions.rollback.done');
    rollbackTarget.value = null;
    await load(meta.value.page);
  } catch {
    dialogError.value = t('view.agentVersions.rollback.failed');
  } finally {
    submitting.value = false;
  }
}

// ── 灰度 ──────────────────────────────────────────────────────
const CANARY_STRATEGIES = ['percentage', 'user_group', 'both'];
const canaryTarget = ref(null);
const canaryForm = reactive({
  percentage: 10,
  strategy: 'percentage',
  groupsText: '',
  duration_seconds: 0,
});

function openCanary(v) {
  canaryTarget.value = v;
  canaryForm.percentage = 10;
  canaryForm.strategy = 'percentage';
  canaryForm.groupsText = '';
  canaryForm.duration_seconds = 0;
  dialogError.value = '';
}

async function submitCanary() {
  if (submitting.value) return;
  dialogError.value = '';
  const pct = Number(canaryForm.percentage);
  if (!Number.isFinite(pct) || pct < 0 || pct > 100) {
    dialogError.value = t('view.agentVersions.canary.percentageInvalid');
    return;
  }
  submitting.value = true;
  try {
    const payload = {
      percentage: pct,
      strategy: canaryForm.strategy,
      target_user_groups: canaryForm.groupsText
        .split(',')
        .map((s) => s.trim())
        .filter(Boolean),
    };
    // duration_seconds 后端要求 >=1 或省略；填 0 表示不设时限。
    const dur = Number(canaryForm.duration_seconds);
    if (Number.isFinite(dur) && dur >= 1) payload.duration_seconds = dur;
    await api.post(
      `/api/agent-versions/${encodeURIComponent(canaryTarget.value.version_id)}/canary`,
      payload,
    );
    notice.value = t('view.agentVersions.canary.done');
    canaryTarget.value = null;
    await load(meta.value.page);
  } catch {
    dialogError.value = t('view.agentVersions.canary.failed');
  } finally {
    submitting.value = false;
  }
}

onMounted(() => load());
</script>

<style scoped>
.av-filters {
  display: flex;
  flex-wrap: wrap;
  gap: var(--sp-2);
  margin-bottom: var(--sp-4);
}
.av-error {
  color: var(--danger, #dc2626);
  font-size: 13px;
}
.av-table {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--fs-sm);
}
.av-table th,
.av-table td {
  padding: var(--sp-2);
  text-align: left;
  border-bottom: 1px solid var(--border);
}
.av-table__num {
  font-family: var(--font-mono, monospace);
}
.av-pager {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  margin-top: var(--sp-3);
  font-size: var(--fs-xs);
}
.av-notice {
  color: var(--success, #16a34a);
  font-size: 13px;
}
.av-table__actions {
  display: flex;
  gap: var(--sp-2);
  align-items: center;
}

/* ── 回滚 / 灰度弹窗 ─────────────────────────────────────── */
.av-overlay {
  position: fixed;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(0, 0, 0, 0.4);
  z-index: 50;
}
.av-dialog {
  width: min(480px, 92vw);
  max-height: 86vh;
  overflow-y: auto;
  padding: var(--sp-4);
  border-radius: var(--r-md, 8px);
  background: var(--surface, #fff);
}
.av-dialog__field {
  display: flex;
  flex-direction: column;
  gap: var(--sp-1);
  margin-bottom: var(--sp-3);
}
.av-dialog__label {
  font-size: var(--fs-sm);
  font-weight: 600;
}
.av-dialog__actions {
  display: flex;
  justify-content: flex-end;
  gap: var(--sp-2);
  margin-top: var(--sp-3);
}
</style>
