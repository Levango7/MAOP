<template>
  <div class="control-panel">
    <!-- 嵌入式(被 Run.vue 托管 Tab)时隐藏自身页头,避免双层标题 -->
    <PageHeader v-if="!embedded">
      <button class="btn-refresh" :disabled="loading" @click="refreshAll">
        <AppIcon name="refresh" :size="15" :class="{ spinning: loading }" /> {{ t('common.refresh') }}
      </button>
    </PageHeader>

    <Card :title="t('view.control.executionControls')" icon="play" :margin-bottom="16">
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

    <Card :title="t('view.control.maintenanceActions')" icon="wrench" :margin-bottom="16">
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

    <Card :title="t('view.control.runningJobs')" icon="activity" :margin-bottom="16">
      <div v-if="jobs.length" class="row-list">
        <div v-for="j in jobs" :key="j.id" class="row-item" :data-status="j.status">
          <div class="row-main">
            <div class="row-name">
              <AppIcon name="activity" :size="14" /> {{ j.name }}
              <Badge :tone="statusTone(j.status)">{{ j.status }}</Badge>
            </div>
            <span class="row-sub">{{ j.started_at }}</span>
          </div>
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

    <Card :title="t('view.control.agentUpgrade')" icon="refresh" :margin-bottom="16">
      <button class="btn-check" :disabled="loading" @click="checkUpgrade">
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
class="act-btn small" :disabled="loading || a.status === 'up-to-date'"
                  @click="upgradeAgent(a.name)">
            <AppIcon name="upload" :size="12" /> {{ t('view.control.upgrade') }}
          </button>
        </div>
      </div>
      <EmptyState
v-else-if="!loading" icon="refresh" :title="t('view.control.noUpgradeInfo')"
                  :description="t('view.control.noUpgradeInfoDesc')" />
      <Skeleton v-else height="120px" />
    </Card>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue';
import { useApiStore } from '../stores/api.js';
import { useToast } from '../composables/useToast.js';
import AppIcon from '../components/AppIcon.vue';
import PageHeader from '../components/PageHeader.vue';
import Card from '../components/Card.vue';
import Badge from '../components/Badge.vue';
import Skeleton from '../components/Skeleton.vue';
import EmptyState from '../components/EmptyState.vue';
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

const execActions = [
  { action: 'run', label: 'view.control.runTask', icon: 'play', tone: 'green' },
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

async function execAction(action, task) {
  loading.value = true;
  execResult.value = null;
  try {
    if (action === 'status') {
      await api.get('/api/control/status');
      execResult.value = { ok: true, msg: 'Status refreshed' };
    } else {
      const validActions = ['run', 'pause', 'resume', 'stop', 'validate', 'doctor'];
      if (!validActions.includes(action)) throw new Error(`Unknown action: ${action}`);
      const body = task ? { task } : {};
      const r = await api.post(`/api/control/${action}`, body);
      execResult.value = { ok: true, msg: r.msg || r.message || r.detail || `${action} executed` };
    }
    await loadJobs();
  } catch (e) {
    execResult.value = { ok: false, msg: e.message || `${action} failed` };
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
    maintResult.value = { ok: true, msg: r.msg || r.message || r.detail || `${action} completed` };
    toast.success(t('view.control.actionCompleted', { action }));
  } catch (e) {
    maintResult.value = { ok: false, msg: e.message || `${action} failed` };
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
      id: j.id || j.name || `job-${index}`,
      name: j.name || j.id || '—',
      status: j.status || 'unknown',
      started_at: j.started_at || '—',
    }));
  } catch {
    jobs.value = [];
  }
}

async function checkUpgrade() {
  loading.value = true;
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
    loading.value = false;
  }
}

async function upgradeAgent(name) {
  loading.value = true;
  try {
    await api.post('/api/agent/upgrade?agent=' + encodeURIComponent(name), {});
    toast.success(t('view.control.upgradeTriggered', { name }));
    await checkUpgrade();
  } catch (e) {
    toast.error(e.message || t('view.control.upgradeFailed'));
  } finally {
    loading.value = false;
  }
}

async function refreshAll() {
  loading.value = true;
  await Promise.allSettled([loadJobs(), checkUpgrade()]);
  loading.value = false;
}

onMounted(refreshAll);
</script>

<style scoped>
</style>
