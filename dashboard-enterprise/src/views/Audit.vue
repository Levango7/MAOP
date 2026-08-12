<template>
  <div class="audit-view">
    <ListPageLayout
      v-model:filters="filters"
      :loading="loading"
      :error="events.error || summary.error"
      :empty="!visibleRows.length"
      :filter-schema="filterSchema"
      search-key="actor"
      :search-placeholder="t('view.audit.filterActor')"
      :results-label="`${visibleRows.length} / ${events.value.length}`"
      :error-title="t('view.audit.eventsError')"
      :empty-title="t('view.audit.noMatch')"
      class="audit-list"
    >
      <template #badges>
        <Badge tone="brand" icon="shield">{{ t('view.audit.enterprise') }}</Badge>
      </template>
      <template #actions>
        <span v-if="lastUpdated" class="last-updated">{{ t('view.audit.updated') }} {{ lastUpdated }}</span>
      </template>

      <template #stats>
        <StatCard :label="t('view.audit.totalEvents')" :value="summary.data.total" icon="scroll" tone="brand" :loading="loading" />
        <StatCard :label="t('view.audit.distinctActions')" :value="Object.keys(summary.data.by_action || {}).length" icon="clipboard" tone="info" :loading="loading" />
        <StatCard :label="t('view.audit.distinctActors')" :value="Object.keys(summary.data.by_actor || {}).length" icon="bot" tone="warn" :loading="loading" />
      </template>

      <template #content>
        <DataTable
:columns="cols" :rows="visibleRows" :loading="false"
          :empty-text="t('view.audit.noMatch')" />
      </template>
    </ListPageLayout>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue';
import { useApiStore } from '../stores/api.js';
import { useI18n } from '../i18n';
import { StatCard, Badge, DataTable } from '../components/index.js';
import ListPageLayout from '../components/ListPageLayout.vue';

const { t } = useI18n();

const api = useApiStore();

const loading = ref(true);
const lastUpdated = ref('');
const events = reactive({ value: [], error: '' });
const summary = reactive({ data: { total: 0, by_action: {}, by_actor: {} }, error: '' });

// filters 由 ListPageLayout 的 FilterBar 收集(声明式), 视图在此计算可见行
const filters = reactive({ action: '', level: '', actor: '' });

const filterSchema = computed(() => [
  {
    key: 'action',
    label: t('view.audit.allActions'),
    options: actionOptions.value.map((a) => ({ value: a, label: a })),
  },
  {
    key: 'level',
    label: t('view.audit.allLevels'),
    options: [
      { value: 'info', label: t('view.audit.info') },
      { value: 'warning', label: t('view.audit.warning') },
      { value: 'critical', label: t('view.audit.critical') },
    ],
  },
]);

const cols = [
  { key: 'time', label: t('view.audit.time'), type: 'time' },
  { key: 'action', label: t('common.actions') },
  { key: 'actor', label: t('view.audit.actor') },
  { key: 'target', label: t('view.audit.target') },
  { key: 'level', label: t('view.audit.level'), type: 'badge' },
];

const actionOptions = computed(() => {
  const set = new Set();
  events.value.forEach(e => e.action && set.add(e.action));
  return [...set].sort();
});

const visibleRows = computed(() => {
  const fa = filters.action, fl = filters.level, fo = (filters.actor || '').trim().toLowerCase();
  return events.value.filter(e => {
    if (fa && e.action !== fa) return false;
    if (fl && (e.level || 'info') !== fl) return false;
    if (fo && !(e.actor || '').toLowerCase().includes(fo)) return false;
    return true;
  });
});

async function loadSummary() {
  try {
    const d = await api.get('/api/audit/summary');
    const s = d.summary || d;
    summary.data = {
      total: s.total || s.total_events || 0,
      by_action: s.by_action || {},
      by_actor: s.by_actor || {},
    };
    summary.error = '';
  }
  catch (e) { summary.error = e.message || 'Summary unavailable'; }
}
async function loadEvents() {
  try {
    const d = await api.get('/api/audit/events');
    events.value = (d.events || []).map(e => ({
      ...e,
      time: e.time || e.timestamp,
      level: e.level || e.severity || 'info',
      target: e.target || e.resource || '',
    }));
    events.error = '';
  }
  catch (e) { events.error = e.message || 'Events unavailable'; }
}

async function loadAll() {
  loading.value = true;
  await Promise.allSettled([loadEvents(), loadSummary()]);
  loading.value = false;
  lastUpdated.value = new Date().toLocaleTimeString();
}

onMounted(loadAll);
</script>

<style scoped>
</style>