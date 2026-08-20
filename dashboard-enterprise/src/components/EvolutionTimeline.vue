<template>
  <!-- ── 模式 A：阶段时间线（phases prop）────────────────────────── -->
  <div v-if="phaseNodes.length" class="evo-timeline evo-timeline--phases" :class="{ compact }" role="list">
    <div class="evo-timeline__track" aria-hidden="true"></div>

    <template v-for="(node, i) in phaseNodes" :key="node.key">
      <div
        class="evo-timeline__node evo-timeline__node--phase"
        :class="{ first: i === 0, last: i === phaseNodes.length - 1, 'is-ok': node.success, 'is-fail': !node.success }"
        role="listitem"
      >
        <span class="evo-timeline__dot" :class="node.success ? 'is-ok' : 'is-fail'">
          <AppIcon :name="node.success ? 'check' : 'x'" :size="12" />
        </span>
        <div class="evo-timeline__card">
          <div class="evo-timeline__head">
            <span class="evo-timeline__version">{{ node.label }}</span>
            <span class="evo-timeline__duration mono">{{ node.duration }}</span>
          </div>
          <div class="evo-timeline__status">
            <span class="evo-timeline__badge" :class="node.success ? 'is-ok' : 'is-fail'">
              {{ node.success ? t('view.evolutionHistory.phase.success') : t('view.evolutionHistory.phase.fail') }}
            </span>
          </div>
          <div v-if="node.metrics.length" class="evo-timeline__metrics">
            <div v-for="(m, mi) in node.metrics" :key="mi" class="evo-timeline__metric">
              <span class="evo-timeline__metric-label">{{ m.label }}</span>
              <span class="evo-timeline__metric-value mono" :class="m.tone">{{ m.value }}</span>
            </div>
          </div>
          <div v-if="node.error" class="evo-timeline__error" :title="node.error">{{ node.error }}</div>
          <div v-if="i < phaseNodes.length - 1" class="evo-timeline__arrow" aria-hidden="true">
            <AppIcon name="chevron-right" :size="14" />
          </div>
        </div>
      </div>
    </template>
  </div>

  <!-- ── 模式 B：世系时间线（items prop，向后兼容）────────────── -->
  <div v-else-if="nodes.length" class="evo-timeline" :class="{ compact }" role="list">
    <div class="evo-timeline__track" aria-hidden="true"></div>

    <template v-for="(node, i) in nodes" :key="node.key">
      <div class="evo-timeline__node" :class="{ first: i === 0, last: i === nodes.length - 1, improved: node.improved }" role="listitem">
        <span class="evo-timeline__dot" :class="node.improved ? 'is-gain' : 'is-flat'">
          <AppIcon :name="node.improved ? 'arrow-up' : 'arrow-down'" :size="10" />
        </span>
        <div class="evo-timeline__card">
          <div class="evo-timeline__head">
            <span class="evo-timeline__version mono">v{{ node.version }}</span>
            <span class="evo-timeline__agent">{{ node.agent }}</span>
            <span class="evo-timeline__time">{{ node.time }}</span>
          </div>
          <div v-if="node.change" class="evo-timeline__change" :title="node.change">{{ node.change }}</div>
          <div v-if="i < nodes.length - 1" class="evo-timeline__arrow" aria-hidden="true">
            <AppIcon name="chevron-right" :size="14" />
          </div>
        </div>
      </div>
    </template>
  </div>

  <EmptyState v-else icon="git-branch" :title="emptyTitle || t('view.evolve.lineage.empty')" />
</template>

<script setup>
/**
 * EvolutionTimeline — 横向时间线组件（迭代 B1 + C）。
 *
 * 两种渲染模式（互斥，按 prop 优先级自动选择）：
 *
 *  ① 阶段时间线（phases prop 非空）— 迭代 C 新增
 *     输入: phases 数组, 每项 { phase, success, duration_s, details, error }
 *     每个阶段一个节点: 显示阶段名 / 成功-失败色点 / 耗时 / 关键指标变化
 *     指标提取规则:
 *       - OBSERVE  → hotspot_count, top_patterns
 *       - HEAL     → attempts / successes
 *       - SUGGEST  → count
 *       - EVALUATE → total / approved
 *       - APPLY    → applied / total (+ dry-run 标记)
 *       - VALIDATE → baseline → current (+ improved 箭头)
 *       - CONSOLIDATE → candidates / consolidated / errors
 *
 *  ② 世系时间线（items prop 非空）— 迭代 B1 原有
 *     输入: lineage 数组, 每项 { agent, version, from_config, to_config, applied_at, improved }
 *     improved=true 节点用绿色向上的增益点, 否则灰色向下。
 *
 * 纯展示组件: 无 API 调用, 无副作用, 数据由父组件喂入。
 * 渲染用 flex-wrap 保底, 视口窄时自然换行, 不强制横向滚动。
 */
import { computed } from 'vue';
import AppIcon from './AppIcon.vue';
import EmptyState from './EmptyState.vue';
import { useI18n } from '../i18n';

const props = defineProps({
  // 模式 B: lineage 世系（向后兼容）
  items: { type: Array, default: () => [] },
  // 模式 A: cycle 阶段（迭代 C 新增）
  phases: { type: Array, default: () => [] },
  compact: { type: Boolean, default: false },
  emptyTitle: { type: String, default: '' },
});

const { t } = useI18n();

// ── 阶段中文名映射（与后端 narrative._PHASE_CN 对齐）────────────
const PHASE_LABEL_KEY = {
  observe: 'view.evolutionHistory.phase.observe',
  heal: 'view.evolutionHistory.phase.heal',
  suggest: 'view.evolutionHistory.phase.suggest',
  evaluate: 'view.evolutionHistory.phase.evaluate',
  apply: 'view.evolutionHistory.phase.apply',
  validate: 'view.evolutionHistory.phase.validate',
  consolidate: 'view.evolutionHistory.phase.consolidate',
};

// 七段闭环的标准顺序，用于排序（后端可能乱序返回）
const PHASE_ORDER = ['observe', 'heal', 'suggest', 'evaluate', 'apply', 'validate', 'consolidate'];

function fmtTs(ts) {
  if (ts === null || ts === undefined || ts === '') return '—';
  const d = new Date(typeof ts === 'number' ? ts * 1000 : ts);
  if (isNaN(d.getTime())) return String(ts);
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function fmtDuration(s) {
  if (s === null || s === undefined) return '—';
  const n = Number(s);
  if (!isFinite(n)) return '—';
  if (n < 1) return (n * 1000).toFixed(0) + 'ms';
  return n.toFixed(2) + 's';
}

// 变更摘要: 优先 to_config, 取一段可读文本
function changeSummary(l) {
  const raw = l.to_config || l.from_config || '';
  if (typeof raw === 'string') return raw.length > 60 ? raw.slice(0, 60) + '…' : raw;
  if (raw && typeof raw === 'object') {
    try { const s = JSON.stringify(raw); return s.length > 60 ? s.slice(0, 60) + '…' : s; }
    catch { return '—'; }
  }
  return '—';
}

// ── 阶段指标提取（与后端 narrative._phase_findings 对齐）────────
function extractPhaseMetrics(phase, details) {
  const metrics = [];
  const push = (label, value, tone = '') => metrics.push({ label, value: String(value), tone });

  switch (phase) {
    case 'observe': {
      push('hotspot_count', details.hotspot_count ?? 0, 'tone-fail');
      const top = details.top_patterns || [];
      if (top.length) {
        const pats = top.slice(0, 3).map((p) => `${p.pattern || '?'}×${p.count || 0}`).join(', ');
        push('top_patterns', pats);
      }
      break;
    }
    case 'heal': {
      push('attempts', details.attempts ?? 0);
      push('successes', details.successes ?? 0, 'tone-ok');
      break;
    }
    case 'suggest': {
      push('count', details.count ?? 0);
      break;
    }
    case 'evaluate': {
      const total = details.total ?? 0;
      const approved = details.approved_count ?? (details.approved || []).length;
      push('total', total);
      push('approved', approved, 'tone-ok');
      break;
    }
    case 'apply': {
      const applied = details.applied ?? 0;
      const total = details.total ?? 0;
      push('applied', `${applied}/${total}`, details.dry_run ? 'tone-warn' : 'tone-ok');
      if (details.dry_run) push('mode', 'dry-run', 'tone-warn');
      break;
    }
    case 'validate': {
      const base = details.baseline;
      const cur = details.current;
      if (base !== undefined && cur !== undefined) {
        push('hotspots', `${base} → ${cur}`, details.improved ? 'tone-ok' : 'tone-flat');
      }
      if (details.improved !== undefined) {
        push('improved', details.improved ? '↓' : '→', details.improved ? 'tone-ok' : 'tone-flat');
      }
      break;
    }
    case 'consolidate': {
      push('candidates', details.candidates ?? 0);
      push('consolidated', details.consolidated ?? 0, 'tone-ok');
      if (details.errors) push('errors', details.errors, 'tone-fail');
      break;
    }
    default: {
      // 未知阶段：展示 details 的键值摘要（最多 4 项）
      const keys = Object.keys(details || {}).slice(0, 4);
      for (const k of keys) {
        const v = details[k];
        if (v === null || typeof v === 'object') continue;
        push(k, v);
      }
    }
  }
  return metrics;
}

// ── 模式 A：阶段节点 ────────────────────────────────────────────
const phaseNodes = computed(() => {
  if (!props.phases || !props.phases.length) return [];
  const sorted = [...props.phases].sort((a, b) => {
    const ia = PHASE_ORDER.indexOf(a.phase);
    const ib = PHASE_ORDER.indexOf(b.phase);
    return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
  });
  return sorted.map((p) => {
    const phaseKey = p.phase || '';
    const labelKey = PHASE_LABEL_KEY[phaseKey];
    return {
      key: `phase-${phaseKey}-${p.phase}`,
      phase: phaseKey,
      label: labelKey ? t(labelKey) : phaseKey,
      success: !!p.success,
      duration: fmtDuration(p.duration_s),
      metrics: extractPhaseMetrics(phaseKey, p.details || {}),
      error: p.error || '',
    };
  });
});

// ── 模式 B：世系节点（向后兼容）────────────────────────────────
const nodes = computed(() => {
  if (!props.items || !props.items.length) return [];
  const sorted = [...props.items].sort((a, b) => {
    const ta = a.applied_at || 0;
    const tb = b.applied_at || 0;
    return (typeof ta === 'number' ? ta : 0) - (typeof tb === 'number' ? tb : 0);
  });
  return sorted.map((l) => ({
    key: `${l.agent}-${l.version}`,
    agent: l.agent,
    version: l.version ?? '?',
    time: fmtTs(l.applied_at),
    change: changeSummary(l),
    improved: !!l.improved,
  }));
});
</script>

<style scoped>
.evo-timeline {
  position: relative;
  display: flex;
  flex-wrap: wrap;
  gap: var(--sp-4);
  padding: var(--sp-4) 0 var(--sp-2);
}
/* 水平轨道线: 淡化, 作为基线背景 */
.evo-timeline__track {
  position: absolute;
  top: 14px;
  left: 0;
  right: 0;
  height: 2px;
  border-radius: 1px;
  background: linear-gradient(90deg, transparent, var(--border-subtle) 8%, var(--border-subtle) 92%, transparent);
  pointer-events: none;
}
.evo-timeline__node {
  position: relative;
  flex: 0 1 240px;
  min-width: 200px;
}
.evo-timeline__dot {
  position: relative;
  z-index: 1;
  display: grid;
  place-items: center;
  width: 28px;
  height: 28px;
  border-radius: 50%;
  border: 1px solid var(--border);
  background: var(--surface);
  margin-bottom: var(--sp-2);
}
.evo-timeline__dot.is-gain { color: var(--success-strong); border-color: var(--success); background: var(--success-soft); }
.evo-timeline__dot.is-flat { color: var(--text-faint); border-color: var(--border-strong); }
/* 阶段模式：成功/失败色点（迭代 C）*/
.evo-timeline__dot.is-ok { color: var(--success-strong); border-color: var(--success); background: var(--success-soft); }
.evo-timeline__dot.is-fail { color: var(--fail); border-color: var(--fail); background: var(--fail-soft, rgba(239,68,68,.12)); }
.evo-timeline__card {
  border: 1px solid var(--border);
  border-radius: var(--r-md);
  background: var(--surface);
  padding: var(--sp-3);
  min-height: 64px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  transition: border-color var(--motion) var(--ease);
}
.evo-timeline__card:hover { border-color: var(--border-strong); }
.evo-timeline__node--phase.is-fail .evo-timeline__card { border-color: color-mix(in srgb, var(--fail) 35%, var(--border)); }
.evo-timeline__node--phase.is-ok .evo-timeline__card { border-color: color-mix(in srgb, var(--success) 30%, var(--border)); }
.evo-timeline__head { display: flex; align-items: center; gap: var(--sp-2); flex-wrap: wrap; }
.evo-timeline__version { font-weight: 700; color: var(--text); }
.evo-timeline__agent { font-size: var(--fs-xs); color: var(--text-muted); }
.evo-timeline__time { font-size: var(--fs-xs); color: var(--text-faint); margin-left: auto; white-space: nowrap; }
.evo-timeline__duration { font-size: var(--fs-xs); color: var(--text-faint); margin-left: auto; white-space: nowrap; }
.evo-timeline__change {
  font-size: var(--fs-xs);
  color: var(--text-muted);
  line-height: 1.45;
  overflow: hidden;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}
.evo-timeline__arrow {
  position: absolute;
  right: -16px;
  top: 8px;
  color: var(--text-faint);
  /* 视口窄时节点换行, 箭头不再水平指; 无视觉损失 */
}
.evo-timeline__node:last-child .evo-timeline__arrow { display: none; }
.compact .evo-timeline__card { min-height: 0; padding: var(--sp-2); }

/* ── 阶段模式专属样式（迭代 C）────────────────────────────────── */
.evo-timeline--phases .evo-timeline__node--phase { flex: 0 1 220px; min-width: 180px; }
.evo-timeline__status { display: flex; align-items: center; gap: var(--sp-2); }
.evo-timeline__badge {
  display: inline-flex;
  align-items: center;
  padding: 1px 8px;
  border-radius: var(--r-full);
  font-size: var(--fs-xs);
  font-weight: 600;
  border: 1px solid transparent;
}
.evo-timeline__badge.is-ok {
  color: var(--success);
  background: var(--success-soft);
  border-color: color-mix(in srgb, var(--success) 30%, transparent);
}
.evo-timeline__badge.is-fail {
  color: var(--fail);
  background: var(--fail-soft, rgba(239,68,68,.12));
  border-color: color-mix(in srgb, var(--fail) 30%, transparent);
}
.evo-timeline__metrics {
  display: flex;
  flex-direction: column;
  gap: 3px;
  margin-top: 2px;
  padding-top: 6px;
  border-top: 1px solid var(--border-subtle, var(--border));
}
.evo-timeline__metric {
  display: flex;
  align-items: baseline;
  gap: 6px;
  font-size: var(--fs-xs);
  line-height: 1.4;
}
.evo-timeline__metric-label {
  color: var(--text-faint);
  flex: 0 0 auto;
  min-width: 64px;
}
.evo-timeline__metric-value {
  color: var(--text);
  word-break: break-all;
}
.evo-timeline__metric-value.tone-ok { color: var(--success); }
.evo-timeline__metric-value.tone-fail { color: var(--fail); }
.evo-timeline__metric-value.tone-warn { color: var(--warn); }
.evo-timeline__metric-value.tone-flat { color: var(--text-muted); }
.evo-timeline__error {
  font-size: var(--fs-xs);
  color: var(--fail);
  margin-top: 2px;
  overflow: hidden;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  word-break: break-all;
}
</style>
