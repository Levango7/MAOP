<template>
  <div class="audit-view">
    <PageHeader>
      <template #badges>
        <Badge tone="brand" icon="shield">{{ t('view.audit.enterprise') }}</Badge>
      </template>
      <span class="last-updated" v-if="lastUpdated">{{ t('view.audit.updated') }} {{ lastUpdated }}</span>
    </PageHeader>

    <section class="stat-row" v-if="!summary.error">
      <StatCard :label="t('view.audit.totalEvents')" :value="summary.data.total" icon="scroll" tone="brand" :loading="loading" />
      <StatCard :label="t('view.audit.distinctActions')" :value="Object.keys(summary.data.by_action || {}).length" icon="clipboard" tone="info" :loading="loading" />
      <StatCard :label="t('view.audit.distinctActors')" :value="Object.keys(summary.data.by_actor || {}).length" icon="bot" tone="warn" :loading="loading" />
    </section>
    <div v-if="summary.error" class="stat-row">
      <EmptyState icon="alert-triangle" :title="t('view.audit.summaryError')" :description="summary.error" />
    </div>

    <Card icon="scroll" :margin-bottom="16">
      <template #actions>
        <select class="filter" v-model="filters.action" :aria-label="t('view.audit.allActions')">
          <option value="">{{ t('view.audit.allActions') }}</option>
          <option v-for="a in actionOptions" :key="a" :value="a">{{ a }}</option>
        </select>
        <select class="filter" v-model="filters.level" :aria-label="t('view.audit.allLevels')">
          <option value="">{{ t('view.audit.allLevels') }}</option>
          <option value="info">{{ t('view.audit.info') }}</option>
          <option value="warning">{{ t('view.audit.warning') }}</option>
          <option value="critical">{{ t('view.audit.critical') }}</option>
        </select>
        <input class="filter filter--text" v-model="filters.actor" :placeholder="t('view.audit.filterActor')"
          :aria-label="t('view.audit.filterActor')" />
      </template>

      <div v-if="events.error"><EmptyState icon="alert-triangle" :title="t('view.audit.eventsError')" :description="events.error" /></div>
      <Skeleton v-else-if="loading" :lines="7" block />
      <DataTable v-else :columns="cols" :rows="filtered" :loading="false"
        :empty-text="t('view.audit.noMatch')" />
    </Card>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue';
import { useApiStore } from '../stores/api.js';
import { useI18n } from '../i18n';
import { Card, StatCard, Badge, DataTable, Skeleton, EmptyState, AppIcon, PageHeader } from '../components/index.js';

const { t } = useI18n();

const api = useApiStore();

const loading = ref(true);
const lastUpdated = ref('');
const events = reactive({ value: [], error: '' });
const summary = reactive({ data: { total: 0, by_action: {}, by_actor: {} }, error: '' });
const filters = reactive({ action: '', level: '', actor: '' });

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

const filtered = computed(() => {
  const fa = filters.action, fl = filters.level, fo = filters.actor.trim().toLowerCase();
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
