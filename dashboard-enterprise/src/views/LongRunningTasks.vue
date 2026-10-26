<template>
  <div class="lrt">
    <PageHeader
      icon="timer"
      :title="t('view.longRunningTasks.title')"
      :subtitle="t('view.longRunningTasks.subtitle')"
    />

    <p v-if="error" class="lrt__error">{{ error }}</p>
    <p v-if="notice" class="lrt__notice">{{ notice }}</p>

    <Card :title="t('view.longRunningTasks.listTitle')" icon="activity">
      <template #actions>
        <button class="btn-ghost" :disabled="loading" @click="load">
          <AppIcon name="refresh" :size="14" />
          <span>{{ t('common.refresh') }}</span>
        </button>
      </template>

      <p v-if="loading" class="muted">{{ t('common.loading') }}</p>
      <p v-else-if="!tasks.length" class="muted">{{ t('view.longRunningTasks.empty') }}</p>
      <div v-else class="lrt__list">
        <div v-for="task in tasks" :key="task.task_id" class="lrt__item">
          <div class="lrt__head">
            <span class="lrt__agent">{{ task.agent_name || '—' }}</span>
            <Badge :tone="statusTone(task.status)">{{ task.status }}</Badge>
            <span class="lrt__pct muted">{{ Math.round((task.progress || 0) * 100) }}%</span>
          </div>
          <p class="lrt__task">{{ task.task }}</p>
          <div class="lrt__bar" role="progressbar" :aria-valuenow="Math.round((task.progress || 0) * 100)" aria-valuemin="0" aria-valuemax="100">
            <div class="lrt__bar-fill" :style="{ width: `${Math.round((task.progress || 0) * 100)}%` }" />
          </div>
          <p v-if="task.progress_message" class="lrt__msg muted">{{ task.progress_message }}</p>
          <div class="lrt__actions">
            <button
              class="act-btn small act-btn--danger"
              :disabled="busy === task.task_id || !isCancellable(task.status)"
              @click="askCancel(task)"
            >
              {{ t('view.longRunningTasks.action.cancel') }}
            </button>
          </div>
        </div>
      </div>
    </Card>

    <p class="lrt__note muted">{{ t('view.longRunningTasks.sseNote') }}</p>

    <div v-if="pending" v-modal-a11y class="lrt__overlay" @click.self="pending = null">
      <div class="lrt__confirm" role="dialog" aria-modal="true">
        <h3>{{ t('view.longRunningTasks.confirm.title') }}</h3>
        <p class="muted">{{ t('view.longRunningTasks.confirm.body', { agent: pending.agent_name }) }}</p>
        <div class="lrt__confirm-actions">
          <button class="act-btn small" @click="pending = null">{{ t('common.cancel') }}</button>
          <button class="act-btn small act-btn--danger" :disabled="busy !== ''" @click="confirmCancel">
            {{ t('view.longRunningTasks.confirm.proceed') }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue';
import { useApiStore } from '../stores/api.js';
import { useI18n } from '../i18n';
import PageHeader from '../components/PageHeader.vue';
import Card from '../components/Card.vue';
import Badge from '../components/Badge.vue';
import AppIcon from '../components/AppIcon.vue';

const { t } = useI18n();
const api = useApiStore();

const tasks = ref([]);
const loading = ref(false);
const error = ref('');
const notice = ref('');
const busy = ref('');
const pending = ref(null);

// 只有 pending / running 可取消；终态取消会被后端拒成 409，
// 所以按钮直接禁用，而不是让用户点出一个错误。
const CANCELLABLE = ['pending', 'running'];
function isCancellable(status) {
  return CANCELLABLE.includes(status);
}

function statusTone(s) {
  if (s === 'running') return 'brand';
  if (s === 'completed') return 'success';
  if (s === 'failed' || s === 'timeout') return 'danger';
  return 'neutral';
}

async function load() {
  loading.value = true;
  error.value = '';
  try {
    const data = await api.get('/api/long-running-tasks');
    tasks.value = data.tasks || [];
    syncStreams();
  } catch {
    error.value = t('view.longRunningTasks.loadFailed');
  } finally {
    loading.value = false;
  }
}

// ── SSE 实时进度 ──────────────────────────────────────────────
// 每个活跃任务一条 EventSource。三条硬约束：
//  1. **404 会无限重连**：EventSource 的 onerror 默认按 3s 重试，任务不存在时
//     永远重试下去 ⇒ 任何 error 都必须显式 close。
//  2. **终态必须 close**：收到 done 后流不会自己关（服务端关流会触发 onerror，
//     又回到第 1 条）。
//  3. **组件卸载必须 closeAll**：否则路由切走后连接泄漏。
const streams = new Map();

function closeStream(taskId) {
  const es = streams.get(taskId);
  if (es) {
    es.close();
    streams.delete(taskId);
  }
}

function closeAll() {
  for (const id of [...streams.keys()]) closeStream(id);
}

function subscribe(task) {
  if (streams.has(task.task_id)) return;
  if (typeof EventSource === 'undefined') return; // SSR / 测试环境兜底

  const es = new EventSource(
    `/api/long-running-tasks/${encodeURIComponent(task.task_id)}/progress`,
  );
  streams.set(task.task_id, es);

  es.addEventListener('progress', (event) => {
    let payload;
    try {
      payload = JSON.parse(event.data);
    } catch {
      return; // 半截帧（断流时可能发生）直接忽略，等下一次推送
    }
    const target = tasks.value.find((x) => x.task_id === payload.task_id);
    if (!target) return;
    if (typeof payload.progress === 'number') target.progress = payload.progress;
    if (payload.message !== undefined) target.progress_message = payload.message;
    if (payload.status) target.status = payload.status;
  });

  es.addEventListener('done', () => {
    closeStream(task.task_id);
    // 任务进终态后会从"活跃列表"里消失，需要重新拉一次
    load();
  });

  es.onerror = () => {
    closeStream(task.task_id);
  };
}

// 只为 pending / running 建流；已消失或已终态的任务关掉旧流。
function syncStreams() {
  const active = tasks.value.filter((x) => isCancellable(x.status));
  const activeIds = new Set(active.map((x) => x.task_id));

  for (const id of [...streams.keys()]) {
    if (!activeIds.has(id)) closeStream(id);
  }
  for (const task of active) subscribe(task);
}

function askCancel(task) {
  pending.value = task;
}

async function confirmCancel() {
  const task = pending.value;
  if (!task) return;
  busy.value = task.task_id;
  error.value = '';
  notice.value = '';
  try {
    await api.post(`/api/long-running-tasks/${encodeURIComponent(task.task_id)}/cancel`, {});
    notice.value = t('view.longRunningTasks.cancelled');
    pending.value = null;
    await load();
  } catch {
    error.value = t('view.longRunningTasks.cancelFailed');
  } finally {
    busy.value = '';
  }
}

onMounted(() => load());
onUnmounted(closeAll);
</script>

<style scoped>
.lrt__error { color: var(--danger, #dc2626); font-size: 13px; }
.lrt__notice { color: var(--success, #16a34a); font-size: 13px; }
.lrt__list { display: flex; flex-direction: column; gap: var(--sp-3); }
.lrt__item {
  padding: var(--sp-3);
  border: 1px solid var(--border);
  border-radius: 8px;
}
.lrt__head {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
}
.lrt__agent { font-weight: 600; }
.lrt__pct { margin-left: auto; font-size: var(--fs-xs); }
.lrt__task { margin: var(--sp-2) 0; font-size: var(--fs-sm); }
.lrt__bar {
  height: 6px;
  overflow: hidden;
  border-radius: 3px;
  background: var(--bg, #f3f4f6);
}
.lrt__bar-fill {
  height: 100%;
  background: var(--brand, #2563eb);
  transition: width 0.3s ease;
}
.lrt__msg { margin: var(--sp-2) 0 0; font-size: var(--fs-xs); }
.lrt__actions { margin-top: var(--sp-2); }
.lrt__note { margin-top: var(--sp-3); font-size: var(--fs-xs); }
.lrt__overlay {
  position: fixed;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(0, 0, 0, 0.4);
  z-index: 50;
}
.lrt__confirm {
  width: min(420px, 90vw);
  padding: var(--sp-4);
  border-radius: var(--r-md, 8px);
  background: var(--surface, #fff);
}
.lrt__confirm-actions {
  display: flex;
  justify-content: flex-end;
  gap: var(--sp-2);
  margin-top: var(--sp-3);
}
</style>
