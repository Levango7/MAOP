<template>
  <ol v-if="events.length" class="run-timeline">
    <li v-for="e in events" :key="e.seq" class="run-timeline__item">
      <span class="run-timeline__kind">{{ label(e.kind) }}</span>
      <Badge v-if="e.status" :tone="toneFn(e.status)">{{ e.status }}</Badge>
      <span class="run-timeline__meta muted">{{ e.agent || '—' }} · {{ e.duration_ms || 0 }}ms</span>
      <pre v-if="detail(e)" class="run-timeline__detail">{{ detail(e) }}</pre>
    </li>
  </ol>
  <p v-else class="muted">{{ t('view.control.runTimelineEmpty') }}</p>
</template>

<script setup>
import Badge from './Badge.vue';
import { useI18n } from '../i18n';

const { t } = useI18n();

defineProps({
  // run_events 的原始行；顺序由后端按 seq 给出，组件不再排序。
  events: { type: Array, default: () => [] },
  // 状态→色调的映射留在调用方：作业抽屉与运行列表各有各的口径。
  toneFn: { type: Function, required: true },
});

const KIND_KEYS = {
  'run.started': 'timelineStarted',
  'run.plan': 'timelinePlan',
  'run.step': 'timelineStep',
  'run.verify': 'timelineVerify',
  'run.cost': 'timelineCost',
  'run.finished': 'timelineFinished',
};

function label(kind) {
  const key = KIND_KEYS[kind];
  return key ? t(`view.control.${key}`) : kind;
}

function detail(event) {
  const payload = event.payload || {};
  if (event.kind === 'run.step') return String(payload.stdout || payload.error || '').slice(0, 400);
  if (event.kind === 'run.verify') return String(payload.summary || '');
  if (event.kind === 'run.cost') return String(payload.estimated_cost ?? '');
  if (event.kind === 'run.plan') return String(payload.routing_key || '');
  return '';
}
</script>

<style scoped>
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
