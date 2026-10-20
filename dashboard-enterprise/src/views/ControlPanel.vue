<template>
  <div class="control-panel">
    <!-- 嵌入式(被 Run.vue 托管 Tab)时隐藏自身页头,避免双层标题 -->
    <PageHeader v-if="!embedded">
      <button class="btn-refresh" :disabled="loading" @click="refreshAll">
        <AppIcon name="refresh" :size="15" :class="{ spinning: loading }" /> {{ t('common.refresh') }}
      </button>
    </PageHeader>

    <Card :title="t('view.control.newTask')" icon="play" margin-bottom="var(--sp-4)">
      <div class="task-composer">
        <label class="task-composer__label" for="maop-task-input">{{ t('view.control.taskLabel') }}</label>
        <textarea
          id="maop-task-input"
          v-model="taskText"
          class="task-composer__input"
          rows="4"
          :placeholder="t('view.control.taskPlaceholder')"
          :disabled="submitting"
          @keydown.ctrl.enter.prevent="runComposedTask"
          @keydown.meta.enter.prevent="runComposedTask"
        ></textarea>
        <div class="task-composer__actions">
          <span class="task-composer__hint">{{ t('view.control.taskHint') }}</span>
          <button
            class="ctrl-btn tone-green task-composer__run"
            :disabled="submitting || !taskText.trim()"
            @click="runComposedTask"
          >
            <AppIcon name="play" :size="16" /> {{ t('view.control.runTask') }}
          </button>
        </div>
        <div v-if="lastJobId" class="result-bar ok">
          <AppIcon name="check-circle" :size="14" />
          <span>{{ t('view.control.jobStarted', { id: lastJobId }) }}</span>
          <button class="act-btn small" @click="openJob(lastJobId)">
            {{ t('view.control.viewJob') }}
          </button>
        </div>
      </div>
    </Card>

    <Card :title="t('view.control.executionControls')" icon="play" margin-bottom="var(--sp-4)">
      <div class="btn-grid">
        <button
          v-for="a in execActions" :key="a.action" class="ctrl-btn" :class="'tone-' + a.tone"
          :disabled="loading" @click="execAction(a.action)">
          <AppIcon :name="a.icon" :size="16" /> {{ t(a.label) }}
        </button>
      </div>
      <div v-if="execResult" class="result-bar" :class="execResult.ok ? 'ok' : 'err'">
        <AppIcon :name="execResult.ok ? 'check-circle' : 'x-circle'" :size="14" /> {{ execResult.msg }}
      </div>
    </Card>

    <Card :title="t('view.control.maintenanceActions')" icon="wrench" margin-bottom="var(--sp-4)">
      <div class="btn-grid">
        <button
          v-for="m in maintActions" :key="m.action" class="ctrl-btn"
          :class="m.tone ? 'tone-' + m.tone : ''"
          :disabled="loading" @click="maintainAction(m.action)">
          <AppIcon :name="m.icon" :size="16" /> {{ t(m.label) }}
        </button>
      </div>
      <div v-if="maintResult" class="result-bar" :class="maintResult.ok ? 'ok' : 'err'">
        <AppIcon :name="maintResult.ok ? 'check-circle' : 'x-circle'" :size="14" /> {{ maintResult.msg }}
      </div>
    </Card>

    <Card :title="t('view.control.runningJobs')" icon="activity" margin-bottom="var(--sp-4)">
      <div v-if="jobs.length" class="row-list">
        <div v-for="j in jobs" :key="j.id" class="row-item" :data-status="j.status">
          <div class="row-main">
            <div class="row-name">
              <AppIcon name="activity" :size="14" /> {{ j.name }}
              <Badge :tone="statusTone(j.status)">{{ j.status }}</Badge>
            </div>
            <span class="row-sub">{{ j.started_at }}</span>
          </div>
          <button class="act-btn small" @click="openJob(j.id)">
            <AppIcon name="file-text" :size="12" /> {{ t('view.control.viewJob') }}
          </button>
          <button class="act-btn small" :disabled="loading" @click="execAction('stop', j.name)">
            <AppIcon name="square" :size="12" /> {{ t('view.control.stop') }}
          </button>
        </div>
      </div>
      <EmptyState
        v-else-if="!loading" icon="activity" :title="t('view.control.noRunningJobs')"
        :description="t('view.control.noRunningJobsDesc')" />
      <Skeleton v-else height="80px" />
    </Card>

    <Card :title="t('view.control.runHistory')" icon="list" margin-bottom="var(--sp-4)">
      <p class="muted run-history__hint">{{ t('view.control.runHistoryDesc') }}</p>
      <div v-if="runs.length" class="row-list">
        <div v-for="r in runs" :key="r.trace_id" class="row-item">
          <div class="row-main">
            <div class="row-name">
              <AppIcon name="flag" :size="14" /> {{ r.task || r.trace_id }}
              <Badge :tone="runTone(r)">{{ runStatusLabel(r) }}</Badge>
            </div>
            <span class="row-sub">
              {{ r.started_at }} · {{ r.agent || '—' }} ·
              {{ t('view.control.runSteps', { n: r.steps }) }} · {{ r.duration_ms || 0 }}ms
            </span>
          </div>
          <button class="act-btn small" @click="openRun(r.trace_id)">
            <AppIcon name="file-text" :size="12" /> {{ t('view.control.viewRun') }}
          </button>
        </div>
      </div>
      <EmptyState
        v-else-if="!runsLoading" icon="list" :title="t('view.control.noRuns')"
        :description="t('view.control.noRunsDesc')" />
      <Skeleton v-else height="80px" />
    </Card>

    <Card :title="t('view.control.agentUpgrade')" icon="refresh" margin-bottom="var(--sp-4)">
      <button class="btn-check" :disabled="upgradeLoading" @click="checkUpgrade">
        <AppIcon name="refresh" :size="15" :class="{ spinning: loading }" /> {{ t('view.control.checkUpgrades') }}
      </button>
      <div v-if="agents.length" class="row-list">
        <div v-for="a in agents" :key="a.name" class="row-item" :data-status="a.status">
          <div class="row-main">
            <div class="row-name">
              <AppIcon name="bot" :size="14" /> {{ a.name }}
              <Badge :tone="upgradeTone(a.status)">{{ a.status }}</Badge>
            </div>
            <div class="agent-fields">
              <span class="agent-field"><span class="field-label">{{ t('view.control.current') }}</span> <span class="field-val">{{ a.current }}</span></span>
              <span class="agent-field">→</span>
              <span class="agent-field"><span class="field-label">{{ t('view.control.latest') }}</span> <span class="field-val">{{ a.latest }}</span></span>
            </div>
          </div>
          <button
            class="act-btn small" :disabled="upgradeLoading || a.status === 'up-to-date'"
            @click="upgradeAgent(a.name)">
            <AppIcon name="upload" :size="12" /> {{ t('view.control.upgrade') }}
          </button>
        </div>
      </div>
      <EmptyState
        v-else-if="!upgradeLoading" icon="refresh" :title="t('view.control.noUpgradeInfo')"
        :description="t('view.control.noUpgradeInfoDesc')" />
      <Skeleton v-else height="120px" />
    </Card>

    <DetailDrawer
      :open="jobDrawer.open"
      :title="t('view.control.jobDetailTitle', { id: jobDrawer.id })"
      icon="play"
      @close="closeJob"
    >
      <div v-if="jobDrawer.loading"><Skeleton height="120px" /></div>
      <div v-else-if="jobDrawer.job" class="job-detail">
        <div class="job-detail__meta">
          <Badge :tone="statusTone(jobDrawer.job.status)">{{ jobDrawer.job.status }}</Badge>
          <span class="job-detail__task">{{ jobDrawer.job.task }}</span>
        </div>
        <dl class="job-detail__facts">
          <dt>{{ t('view.control.jobStartedAt') }}</dt><dd>{{ jobDrawer.job.start || '—' }}</dd>
          <dt>{{ t('view.control.jobFinishedAt') }}</dt><dd>{{ jobDrawer.job.finished_at || '—' }}</dd>
          <dt>{{ t('view.control.jobExitCode') }}</dt><dd>{{ jobDrawer.job.exit_code ?? '—' }}</dd>
        </dl>
        <template v-if="jobDrawer.job.trace_id">
          <h4 class="job-detail__heading">{{ t('view.control.runTimeline') }}</h4>
          <RunTimeline :events="runTimeline" :tone-fn="statusTone" />
        </template>
        <h4 class="job-detail__heading">{{ t('view.control.jobStdout') }}</h4>
        <pre class="job-detail__output">{{ jobDrawer.job.stdout || t('view.control.jobNoOutput') }}</pre>
        <template v-if="jobDrawer.job.stderr">
          <h4 class="job-detail__heading">{{ t('view.control.jobStderr') }}</h4>
          <pre class="job-detail__output job-detail__output--err">{{ jobDrawer.job.stderr }}</pre>
        </template>
      </div>
      <template #footer>
        <button class="act-btn small" :disabled="jobDrawer.loading" @click="loadJob(jobDrawer.id)">
          <AppIcon name="refresh" :size="12" /> {{ t('common.refresh') }}
        </button>
      </template>
    </DetailDrawer>

    <DetailDrawer
      :open="runDrawer.open"
      :title="t('view.control.runDetailTitle', { id: runDrawer.traceId })"
      icon="flag"
      @close="closeRun"
    >
      <div v-if="runDrawer.loading"><Skeleton height="120px" /></div>
      <div v-else-if="runDrawer.run" class="job-detail">
        <div class="job-detail__meta">
          <Badge :tone="runTone(runDrawer.run.summary)">{{ runStatusLabel(runDrawer.run.summary) }}</Badge>
          <span class="job-detail__task">{{ runDrawer.run.summary.task }}</span>
        </div>
        <dl class="job-detail__facts">
          <dt>{{ t('view.control.runAgent') }}</dt><dd>{{ runDrawer.run.summary.agent || '—' }}</dd>
          <dt>{{ t('view.control.runStartedAt') }}</dt><dd>{{ runDrawer.run.summary.started_at || '—' }}</dd>
          <dt>{{ t('view.control.runSteps', { n: runDrawer.run.summary.steps }) }}</dt>
          <dd>{{ runDrawer.run.summary.duration_ms || 0 }}ms</dd>
          <dt>{{ t('view.control.runVerify') }}</dt>
          <dd>{{ runDrawer.run.summary.verify_status || '—' }}</dd>
        </dl>
        <h4 class="job-detail__heading">{{ t('view.control.runTimeline') }}</h4>
        <RunTimeline :events="runDrawer.run.events" :tone-fn="statusTone" />
      </div>
      <p v-else class="muted">{{ runDrawer.error || t('view.control.runTimelineEmpty') }}</p>
      <template #footer>
        <button class="act-btn small" :disabled="runDrawer.loading" @click="loadRun(runDrawer.traceId)">
          <AppIcon name="refresh" :size="12" /> {{ t('common.refresh') }}
        </button>
      </template>
    </DetailDrawer>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted, onUnmounted } from 'vue';
import { useApiStore } from '../stores/api.js';
import { useToast } from '../composables/useToast.js';
import AppIcon from '../components/AppIcon.vue';
import PageHeader from '../components/PageHeader.vue';
import Card from '../components/Card.vue';
import Badge from '../components/Badge.vue';
import Skeleton from '../components/Skeleton.vue';
import EmptyState from '../components/EmptyState.vue';
import DetailDrawer from '../components/DetailDrawer.vue';
import RunTimeline from '../components/RunTimeline.vue';
import { useI18n } from '../i18n';

const { t } = useI18n();
// 嵌入式模式: 作为 Run.vue 的 Tab 子视图时, 隐藏自身 PageHeader
defineProps({
  embedded: { type: Boolean, default: false },
});
const api = useApiStore();
const toast = useToast();


const loading = ref(false);
const jobs = ref([]);
const agents = ref([]);
const execResult = ref(null);
const maintResult = ref(null);
const taskText = ref('');
const submitting = ref(false);
const upgradeLoading = ref(false);
const lastJobId = ref('');
const jobDrawer = reactive({ open: false, id: '', loading: false, job: null });
const runDrawer = reactive({ open: false, traceId: '', loading: false, run: null, error: '' });
const runTimeline = ref([]);
const runs = ref([]);
const runsLoading = ref(false);
let jobPollTimer = null;
const TERMINAL_JOB_STATES = new Set(['completed', 'failed', 'stopped', 'cancelled', 'interrupted']);

const execActions = [
  // "run" lives in the composer above; keeping a blank run button here was a
  // one-click way to POST an empty task and get a 400.
  { action: 'pause', label: 'view.control.pause', icon: 'pause', tone: 'orange' },
  { action: 'resume', label: 'view.control.resume', icon: 'rotate-ccw', tone: 'blue' },
  { action: 'stop', label: 'view.control.stop', icon: 'square', tone: 'red' },
  { action: 'validate', label: 'view.control.validateConfig', icon: 'check', tone: 'purple' },
  { action: 'status', label: 'view.control.viewStatus', icon: 'activity', tone: 'teal' },
];
const maintActions = [
  { action: 'log-rotate', label: 'view.control.logRotate', icon: 'scroll', tone: 'cyan' },
  { action: 'prune', label: 'view.control.memoryPrune', icon: 'trash', tone: 'pink' },
  { action: 'health', label: 'view.control.healthCheck', icon: 'check-circle', tone: 'lime' },
  { action: 'backup', label: 'view.control.backup', icon: 'database', tone: 'sky' },
  { action: 'cache-clear', label: 'view.control.cacheClear', icon: 'x-circle', tone: 'slate' },
  { action: 'reload', label: 'view.control.configReload', icon: 'refresh', tone: 'amber' },
];

function statusTone(s) {
  const v = (s || '').toLowerCase();
  if (v === 'running' || v === 'active') return 'info';
  if (v === 'paused') return 'warn';
  if (v === 'completed' || v === 'success') return 'success';
  if (v === 'failed' || v === 'error') return 'fail';
  if (v === 'pending' || v === 'queued') return 'neutral';
  return 'neutral';
}
function upgradeTone(s) {
  const v = (s || '').toLowerCase();
  if (v === 'up-to-date') return 'success';
  if (v === 'upgrade-available') return 'warn';
  if (v === 'upgrading') return 'info';
  if (v === 'error' || v === 'unavailable') return 'fail';
  return 'neutral';
}

async function runComposedTask() {
  const task = taskText.value.trim();
  if (!task || submitting.value) return;
  submitting.value = true;
  execResult.value = null;
  try {
    const r = await api.post('/api/control/run', { task });
    lastJobId.value = r.job_id || '';
    taskText.value = '';
    await loadJobs();
    setTimeout(() => loadJobs(), 1500);
    openJob(lastJobId.value);
  } catch (e) {
    execResult.value = { ok: false, msg: e.message || t('view.control.actionFailed', { action: 'run' }) };
    toast.error(execResult.value.msg);
  } finally {
    submitting.value = false;
  }
}

function closeJob() {
  jobDrawer.open = false;
  if (jobPollTimer) {
    clearTimeout(jobPollTimer);
    jobPollTimer = null;
  }
}

function openJob(jobId) {
  jobDrawer.id = jobId;
  jobDrawer.open = true;
  jobDrawer.job = null;
  runDrawer.open = false; // 一次只开一个抽屉，两个叠在一起会盖住关闭按钮
  loadJob(jobId);
}

async function loadJob(jobId) {
  if (!jobId || !jobDrawer.open || jobDrawer.id !== jobId) return;
  if (jobPollTimer) {
    clearTimeout(jobPollTimer);
    jobPollTimer = null;
  }
  jobDrawer.loading = true;
  try {
    const data = await api.get(`/api/control/jobs/${encodeURIComponent(jobId)}`);
    jobDrawer.job = data.job || null;
    await loadRunTimeline(jobDrawer.job?.trace_id || '');
    const status = jobDrawer.job?.status;
    if (status && !TERMINAL_JOB_STATES.has(status)) {
      jobPollTimer = setTimeout(() => loadJob(jobId), 2000);
    } else {
      loadJobs();
    }
  } catch (e) {
    toast.error(e.message || t('view.control.jobLoadFailed'));
    jobDrawer.job = null;
  } finally {
    jobDrawer.loading = false;
  }
}

async function loadRunTimeline(traceId) {
  runTimeline.value = [];
  if (!traceId) return;
  try {
    const data = await api.get(`/api/runs/${encodeURIComponent(traceId)}`);
    runTimeline.value = data.data?.events || [];
  } catch {
    // 时间线是增强信息：拿不到时抽屉仍显示输出，不弹错误打扰用户。
    runTimeline.value = [];
  }
}

async function loadRuns() {
  runsLoading.value = true;
  try {
    const data = await api.get('/api/runs?limit=20');
    runs.value = data.data?.items || [];
  } catch {
    // 运行记录是账本投影：读不到就留空由 EmptyState 说明，
    // 不能因为它把面板其余部分一起弄红。
    runs.value = [];
  } finally {
    runsLoading.value = false;
  }
}

function openRun(traceId) {
  runDrawer.traceId = traceId;
  runDrawer.open = true;
  runDrawer.run = null;
  runDrawer.error = '';
  jobDrawer.open = false; // 一次只开一个抽屉
  loadRun(traceId);
}

async function loadRun(traceId) {
  if (!traceId || !runDrawer.open || runDrawer.traceId !== traceId) return;
  runDrawer.loading = true;
  try {
    const data = await api.get(`/api/runs/${encodeURIComponent(traceId)}`);
    runDrawer.run = data.data || null;
    if (!runDrawer.run) runDrawer.error = t('view.control.runNotFound');
  } catch (e) {
    runDrawer.error = e.message || t('view.control.runLoadFailed');
    runDrawer.run = null;
  } finally {
    runDrawer.loading = false;
  }
}

function closeRun() {
  runDrawer.open = false;
  runDrawer.run = null;
  runDrawer.error = '';
}

// 账本里的运行只有三种可呈现结论：跑完且成功、跑完且失败、还没写 run.finished。
function runStatusLabel(summary) {
  if (!summary || summary.status !== 'finished') return t('view.control.runIncomplete');
  return summary.success ? t('view.control.runSuccess') : t('view.control.runFailed');
}

function runTone(summary) {
  if (!summary || summary.status !== 'finished') return 'neutral';
  return summary.success ? 'success' : 'fail';
}

onUnmounted(closeJob);

async function execAction(action, task) {
  loading.value = true;
  execResult.value = null;
  try {
    if (action === 'status') {
      await api.get('/api/control/status');
      execResult.value = { ok: true, msg: t('view.control.statusRefreshed') };
    } else {
      const validActions = ['run', 'pause', 'resume', 'stop', 'validate', 'doctor'];
      if (!validActions.includes(action)) throw new Error(`Unknown action: ${action}`);
      const body = task ? { task } : {};
      const r = await api.post(`/api/control/${action}`, body);
      execResult.value = { ok: true, msg: r.msg || r.message || r.detail || t('view.control.actionExecuted', { action }) };
    }
    await loadJobs();
  } catch (e) {
    execResult.value = { ok: false, msg: e.message || t('view.control.actionFailed', { action }) };
    toast.error(e.message || t('view.control.actionFailed', { action }));
  } finally {
    loading.value = false;
  }
}

async function maintainAction(action) {
  loading.value = true;
  maintResult.value = null;
  try {
    const r = await api.post('/api/control/maintain', { action });
    maintResult.value = { ok: true, msg: r.msg || r.message || r.detail || t('view.control.actionCompleted', { action }) };
    toast.success(t('view.control.actionCompleted', { action }));
  } catch (e) {
    maintResult.value = { ok: false, msg: e.message || t('view.control.actionFailed', { action }) };
    toast.error(e.message || t('view.control.actionFailed', { action }));
  } finally {
    loading.value = false;
  }
}

async function loadJobs() {
  try {
    const data = await api.get('/api/control/status');
    const arr = Array.isArray(data) ? data : (data.jobs || data.active_jobs || []);
    jobs.value = arr.map((j, index) => ({
      id: j.id || `job-${index}`,
      name: j.task || j.action || j.id || '—',
      status: j.status || 'unknown',
      started_at: j.start || j.started_at || '—',
    }));
  } catch {
    jobs.value = [];
  }
}

async function checkUpgrade() {
  upgradeLoading.value = true;
  try {
    const data = await api.get('/api/agent/upgrade');
    agents.value = (data.agents || []).map((a) => ({
      name: a.name,
      current: a.current || '—',
      latest: a.latest || '—',
      status: a.status || '—',
    }));
  } catch {
    agents.value = [];
  } finally {
    upgradeLoading.value = false;
  }
}

async function upgradeAgent(name) {
  upgradeLoading.value = true;
  try {
    await api.post(`/api/agent/upgrade?agent=${encodeURIComponent(name)}`, {});
    toast.success(t('view.control.upgradeTriggered', { name }));
    await checkUpgrade();
  } catch (e) {
    toast.error(e.message || t('view.control.upgradeFailed'));
  } finally {
    upgradeLoading.value = false;
  }
}

async function refreshAll() {
  loading.value = true;
  await loadJobs();
  loading.value = false;
  // 命令行发起的运行不在作业列表里，只能从账本读。不占用 loading，
  // 免得账本读得慢时把任务输入框一起锁住。
  loadRuns();
  // CLI version scanning can be slow; never block the task composer on it.
  checkUpgrade();
}

onMounted(refreshAll);
</script>

<style scoped>
.task-composer__label {
  display: block;
  margin-bottom: var(--sp-2);
  color: var(--text-secondary);
  font-size: var(--fs-sm);
}
.task-composer__input {
  width: 100%;
  resize: vertical;
  min-height: 96px;
  padding: var(--sp-3);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  background: var(--surface-sunken);
  color: var(--text-primary);
  font: inherit;
  line-height: 1.5;
}
.task-composer__input:focus {
  outline: 2px solid var(--brand);
  outline-offset: 1px;
}
.task-composer__actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--sp-3);
  margin-top: var(--sp-3);
}
.task-composer__hint {
  color: var(--text-muted);
  font-size: var(--fs-xs);
}
.task-composer__run {
  min-width: 120px;
}
.job-detail__meta {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  margin-bottom: var(--sp-3);
}
.job-detail__task {
  font-weight: 600;
  word-break: break-word;
}
.job-detail__facts {
  display: grid;
  grid-template-columns: max-content 1fr;
  gap: var(--sp-2) var(--sp-3);
  margin: 0 0 var(--sp-4);
  font-size: var(--fs-sm);
}
.job-detail__facts dt { color: var(--text-muted); }
.job-detail__facts dd { margin: 0; word-break: break-all; }
.job-detail__heading {
  margin: var(--sp-3) 0 var(--sp-2);
  font-size: var(--fs-sm);
  color: var(--text-secondary);
}
.job-detail__output {
  max-height: 280px;
  overflow: auto;
  margin: 0 0 var(--sp-3);
  padding: var(--sp-3);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--surface-sunken);
  color: var(--text-primary);
  font-size: var(--fs-xs);
  white-space: pre-wrap;
  word-break: break-word;
}
.job-detail__output--err { border-color: var(--danger); }
.run-timeline {
  margin: 0 0 var(--sp-4);
  padding-left: var(--sp-4);
  font-size: var(--fs-sm);
}
.run-timeline__item {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: var(--sp-2);
  margin-bottom: var(--sp-2);
}
.run-timeline__kind { font-weight: 600; }
.run-timeline__meta { font-size: var(--fs-xs); }
.run-timeline__detail {
  flex: 1 1 100%;
  margin: 0;
  padding: var(--sp-2);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--surface-sunken);
  font-size: var(--fs-xs);
  white-space: pre-wrap;
  word-break: break-word;
}
</style>


