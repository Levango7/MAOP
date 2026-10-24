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
            <td>
              <button
                v-if="v.status !== 'active'"
                class="act-btn small"
                :disabled="busy === v.version_id"
                @click="activate(v)"
              >
                {{ t('view.agentVersions.action.activate') }}
              </button>
              <span v-else class="muted">{{ t('view.agentVersions.activeNow') }}</span>
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

    <p class="av-note muted">{{ t('view.agentVersions.rollbackNote') }}</p>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue';
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
.av-note {
  margin-top: var(--sp-3);
  font-size: var(--fs-xs);
}
</style>
