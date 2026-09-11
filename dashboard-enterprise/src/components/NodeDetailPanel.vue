<template>
  <div v-if="node" class="node-detail-panel">
    <div class="node-detail-panel__header">
      <span class="node-detail-panel__status-dot" :class="`node-detail-panel__status-${node.status}`"></span>
      <h4>{{ node.node_id }}</h4>
      <button class="node-detail-panel__close" :aria-label="t('common.close')" @click="$emit('close')">
        <AppIcon name="x" :size="16" />
      </button>
    </div>
    <div class="node-detail-panel__body">
      <div class="node-detail-panel__row">
        <span class="node-detail-panel__label">{{ t('view.nodedetailpanel.status') }}</span>
        <span class="node-detail-panel__value" :class="`node-detail-panel__status-${node.status}`">{{ node.status }}</span>
      </div>
      <div class="node-detail-panel__row">
        <span class="node-detail-panel__label">{{ t('view.nodedetailpanel.timestamp') }}</span>
        <span class="node-detail-panel__value">{{ formatTime(node.timestamp) }}</span>
      </div>
      <div v-if="meta.assigned_agent" class="node-detail-panel__row">
        <span class="node-detail-panel__label">{{ t('view.nodedetailpanel.agent') }}</span>
        <span class="node-detail-panel__value">{{ meta.assigned_agent }}</span>
      </div>
      <div v-if="meta.duration_ms" class="node-detail-panel__row">
        <span class="node-detail-panel__label">{{ t('view.nodedetailpanel.duration') }}</span>
        <span class="node-detail-panel__value">{{ meta.duration_ms }} ms</span>
      </div>
      <div v-if="meta.error" class="node-detail-panel__row node-detail-panel__error">
        <span class="node-detail-panel__label">{{ t('view.nodedetailpanel.error') }}</span>
        <span class="node-detail-panel__value node-detail-panel__mono">{{ meta.error }}</span>
      </div>
      <div v-if="meta.traceback" class="node-detail-panel__row node-detail-panel__traceback">
        <span class="node-detail-panel__label">{{ t('view.nodedetailpanel.traceback') }}</span>
        <pre class="node-detail-panel__traceback-pre">{{ meta.traceback }}</pre>
      </div>
      <div v-if="meta.reason" class="node-detail-panel__row">
        <span class="node-detail-panel__label">{{ t('view.nodedetailpanel.reason') }}</span>
        <span class="node-detail-panel__value">{{ meta.reason }}</span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue';
import { AppIcon } from './index.js';
import { useI18n } from '../i18n';

const { t } = useI18n();

const props = defineProps({
  node: { type: Object, default: null },
});

defineEmits(['close']);

const meta = computed(() => (props.node?.metadata) || {});

function formatTime(ts) {
  if (!ts) return '—';
  try {
    return new Date(ts).toLocaleString();
  } catch {
    return ts;
  }
}
</script>

<style scoped>
.node-detail-panel {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--r-lg);
  box-shadow: var(--shadow-card);
  overflow: hidden;
  font-size: var(--fs-base);
}
.node-detail-panel__header {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  padding: var(--sp-2) var(--sp-3);
  border-bottom: 1px solid var(--border);
  background: var(--bg-muted);
}
.node-detail-panel__header h4 {
  margin: 0;
  flex: 1;
  font-size: var(--fs-md);
  font-weight: 600;
}
.node-detail-panel__close {
  border: none;
  background: none;
  cursor: pointer;
  padding: var(--sp-1);
  border-radius: var(--r-sm);
  color: var(--text-muted);
  display: flex;
  align-items: center;
}
.node-detail-panel__close:hover { background: var(--surface-hover); }
.node-detail-panel__body { padding: var(--sp-2) var(--sp-3); }
.node-detail-panel__row {
  display: flex;
  gap: var(--sp-3);
  padding: var(--sp-1) 0;
  align-items: flex-start;
}
/* R9 修复: --border-light 统一为 --border-subtle (与其他组件一致) */
.node-detail-panel__row + .node-detail-panel__row { border-top: 1px solid var(--border-subtle); }
.node-detail-panel__label {
  width: 80px;
  flex-shrink: 0;
  color: var(--text-muted);
  font-weight: 500;
}
.node-detail-panel__value {
  flex: 1;
  word-break: break-word;
}
.node-detail-panel__mono {
  font-family: var(--font-mono);
  font-size: var(--fs-sm);
}
.node-detail-panel__error .node-detail-panel__value { color: var(--fail); }
.node-detail-panel__traceback { flex-direction: column; gap: var(--sp-1); }
.node-detail-panel__traceback-pre {
  margin: 0;
  padding: var(--sp-2);
  background: var(--bg-code);
  color: var(--text-code);
  border-radius: var(--r-sm);
  font-size: var(--fs-xs);
  font-family: var(--font-mono);
  overflow-x: auto;
  max-height: 200px;
  overflow-y: auto;
  white-space: pre-wrap;
}
.node-detail-panel__status-dot {
  width: 10px;
  height: 10px;
  border-radius: var(--r-full);
  flex-shrink: 0;
}
.node-detail-panel__status-pending { color: var(--text-faint); }
.node-detail-panel__status-pending .node-detail-panel__status-dot, .node-detail-panel__status-dot.status-pending { background: var(--text-faint); }
.node-detail-panel__status-running { color: var(--info); }
.node-detail-panel__status-running .node-detail-panel__status-dot, .node-detail-panel__status-dot.status-running { background: var(--info); }
.node-detail-panel__status-success { color: var(--success); }
.node-detail-panel__status-success .node-detail-panel__status-dot, .node-detail-panel__status-dot.status-success { background: var(--success); }
.node-detail-panel__status-failed { color: var(--fail); }
.node-detail-panel__status-failed .node-detail-panel__status-dot, .node-detail-panel__status-dot.status-failed { background: var(--fail); }
.node-detail-panel__status-skipped { color: var(--warn); }
.node-detail-panel__status-skipped .node-detail-panel__status-dot, .node-detail-panel__status-dot.status-skipped { background: var(--warn); }
</style>