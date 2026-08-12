<template>
  <div v-if="nodes.length" class="evo-timeline" :class="{ compact }" role="list">
    <!-- 横向时间轨道 -->
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
  <EmptyState v-else icon="git-branch" :title="t('view.evolve.lineage.empty')" />
</template>

<script setup>
/**
 * EvolutionTimeline — 把 Agent 版本世系画成横向时间线(迭代 B1)。
 *
 * 输入: lineage 数组,每项 { agent, version, from_config, to_config, applied_at, improved }
 * 布局: 节点沿水平轨道顺序排布, 每个节点一张卡片展示版本/变更摘要;
 *       improved=true 节点用绿色向上的增益点, 否则灰色向下。
 *
 * 纯展示组件: 无 API 调用, 无副作用, 数据由父组件(Evolve.vue)喂入。
 * 渲染用 flex-wrap 保底, 视口窄时自然换行, 不强制横向滚动。
 */
import { computed } from 'vue';
import AppIcon from './AppIcon.vue';
import EmptyState from './EmptyState.vue';
import { useI18n } from '../i18n';

const props = defineProps({
  items: { type: Array, default: () => [] },
  compact: { type: Boolean, default: false },
});

const { t } = useI18n();

function fmtTs(ts) {
  if (ts === null || ts === undefined || ts === '') return '—';
  const d = new Date(typeof ts === 'number' ? ts * 1000 : ts);
  if (isNaN(d.getTime())) return String(ts);
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
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

// 按 agent → applied_at 排序, 相同 agent 聚在一起形成时间线
const nodes = computed(() => {
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
.evo-timeline__head { display: flex; align-items: center; gap: var(--sp-2); flex-wrap: wrap; }
.evo-timeline__version { font-weight: 700; color: var(--text); }
.evo-timeline__agent { font-size: var(--fs-xs); color: var(--text-muted); }
.evo-timeline__time { font-size: var(--fs-xs); color: var(--text-faint); margin-left: auto; white-space: nowrap; }
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
</style>