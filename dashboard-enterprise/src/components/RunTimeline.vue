<template>
  <div v-if="events.length" class="run-timeline">
    <!-- PEV 三阶段分组：把平铺的事件流按 Plan / Execute / Verify 归组，
         让"跑在哪一阶段、重试了几次、门禁过没过"一眼可见。 -->
    <section v-for="g in groups" :key="g.key" class="run-timeline__group">
      <header class="run-timeline__group-head">
        <span class="run-timeline__phase">{{ t(`view.control.${g.labelKey}`) }}</span>
        <span class="run-timeline__group-meta muted">
          {{ t('view.control.pevEvents', { n: g.items.length }) }} ·
          {{ t('view.control.pevDuration', { ms: g.durationMs }) }}
        </span>
      </header>
      <ol class="run-timeline__list">
        <li v-for="e in g.items" :key="e.seq" class="run-timeline__item">
          <span class="run-timeline__kind">{{ label(e.kind) }}</span>
          <span v-if="attemptOf(e)" class="run-timeline__attempt">
            {{ t('view.control.pevAttempt', { n: attemptOf(e) }) }}
          </span>
          <Badge v-if="e.status" :tone="toneFn(e.status)">{{ e.status }}</Badge>
          <span class="run-timeline__meta muted">{{ e.agent || '—' }} · {{ e.duration_ms || 0 }}ms</span>
          <pre v-if="detail(e)" class="run-timeline__detail">{{ detail(e) }}</pre>
          <div v-if="gatesOf(e).length" class="run-timeline__gates">
            <span class="run-timeline__gates-label muted">{{ t('view.control.pevGates') }}</span>
            <Badge
              v-for="gate in gatesOf(e)"
              :key="gate.name || gate.id || gate"
              :tone="gate.passed ? 'success' : 'danger'"
            >
              {{ gate.name || gate.id || gate }}
            </Badge>
          </div>
        </li>
      </ol>
    </section>
  </div>
  <p v-else class="muted">{{ t('view.control.runTimelineEmpty') }}</p>
</template>

<script setup>
import { computed } from 'vue';
import Badge from './Badge.vue';
import { useI18n } from '../i18n';

const { t } = useI18n();

const props = defineProps({
  // run_events 的原始行；顺序由后端按 seq 给出，组件不再排序。
  events: { type: Array, default: () => [] },
  // 状态→色调的映射留在调用方：作业抽屉与运行列表各有各的口径。
  toneFn: { type: Function, required: true },
});

// PEV 三阶段 + "其他"（run.cost / run.finished / run.started 等元信息）。
// 阶段顺序即展示顺序，与 MAOP 的 Plan-Execute-Verify 循环一致。
const PHASES = [
  { key: 'plan', labelKey: 'pevPhasePlan', kinds: ['run.plan'] },
  { key: 'execute', labelKey: 'pevPhaseExecute', kinds: ['run.step'] },
  { key: 'verify', labelKey: 'pevPhaseVerify', kinds: ['run.verify'] },
  { key: 'other', labelKey: 'pevPhaseOther', kinds: [] },
];

const groups = computed(() => {
  const byKey = new Map(PHASES.map((p) => [p.key, []]));
  for (const e of props.events) {
    const phase = PHASES.find((p) => p.kinds.includes(e.kind));
    (byKey.get(phase ? phase.key : 'other') ?? byKey.get('other')).push(e);
  }
  return PHASES.map((p) => ({
    key: p.key,
    labelKey: p.labelKey,
    items: byKey.get(p.key),
    durationMs: byKey.get(p.key).reduce((sum, e) => sum + (e.duration_ms || 0), 0),
  })).filter((g) => g.items.length);
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

// 第几次尝试 —— 来自 `run.step` 的 payload.attempt（feedback_cycles + 1）。
// 一次过的事件没有 attempt 字段，返回空串而不是 1，
// 避免把"没重试"显示成"第 1 次重试"。
function attemptOf(event) {
  const n = event.payload?.attempt;
  return typeof n === 'number' && n > 1 ? n : '';
}

// verify 的门禁列表（payload.gates[]）；非 verify 事件返回空数组。
function gatesOf(event) {
  if (event.kind !== 'run.verify') return [];
  return Array.isArray(event.payload?.gates) ? event.payload.gates : [];
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
  font-size: var(--fs-sm);
}
.run-timeline__group + .run-timeline__group {
  margin-top: var(--sp-3);
}
.run-timeline__group-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--sp-2);
  padding-bottom: var(--sp-1);
  border-bottom: 1px solid var(--border);
}
.run-timeline__phase {
  font-weight: 700;
  font-size: var(--fs-sm);
}
.run-timeline__group-meta { font-size: var(--fs-xs); }
.run-timeline__list {
  margin: var(--sp-2) 0 0;
  padding-left: var(--sp-4);
  list-style: none;
}
.run-timeline__item {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: var(--sp-2);
  margin-bottom: var(--sp-2);
}
.run-timeline__kind { font-weight: 600; }
.run-timeline__attempt {
  padding: 0 var(--sp-2);
  border: 1px solid var(--border);
  border-radius: var(--r-sm);
  font-size: var(--fs-xs);
}
.run-timeline__meta { font-size: var(--fs-xs); }
.run-timeline__detail {
  flex: 1 1 100%;
  margin: 0;
  padding: var(--sp-2);
  border: 1px solid var(--border);
  border-radius: var(--r-sm);
  background: var(--bg);
  font-size: var(--fs-xs);
  white-space: pre-wrap;
  word-break: break-word;
}
.run-timeline__gates {
  display: flex;
  flex: 1 1 100%;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--sp-2);
}
.run-timeline__gates-label { font-size: var(--fs-xs); }
</style>
