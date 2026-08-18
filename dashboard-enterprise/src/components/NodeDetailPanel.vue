<template>
  <div v-if="node" class="node-detail-panel">
    <div class="ndp-header">
      <span class="ndp-status-dot" :class="`status-${node.status}`"></span>
      <h4>{{ node.node_id }}</h4>
      <button class="ndp-close" @click="$emit('close')">
        <AppIcon name="x" :size="16" />
      </button>
    </div>
    <div class="ndp-body">
      <div class="ndp-row">
        <span class="ndp-label">{{ t('view.nodedetailpanel.status') }}</span>
        <span class="ndp-value" :class="`status-${node.status}`">{{ node.status }}</span>
      </div>
      <div class="ndp-row">
        <span class="ndp-label">{{ t('view.nodedetailpanel.timestamp') }}</span>
        <span class="ndp-value">{{ formatTime(node.timestamp) }}</span>
      </div>
      <div v-if="meta.assigned_agent" class="ndp-row">
        <span class="ndp-label">{{ t('view.nodedetailpanel.agent') }}</span>
        <span class="ndp-value">{{ meta.assigned_agent }}</span>
      </div>
      <div v-if="meta.duration_ms" class="ndp-row">
        <span class="ndp-label">{{ t('view.nodedetailpanel.duration') }}</span>
        <span class="ndp-value">{{ meta.duration_ms }} ms</span>
      </div>
      <div v-if="meta.error" class="ndp-row ndp-error">
        <span class="ndp-label">{{ t('view.nodedetailpanel.error') }}</span>
        <span class="ndp-value ndp-mono">{{ meta.error }}</span>
      </div>
      <div v-if="meta.traceback" class="ndp-row ndp-traceback">
        <span class="ndp-label">{{ t('view.nodedetailpanel.traceback') }}</span>
        <pre class="ndp-traceback-pre">{{ meta.traceback }}</pre>
      </div>
      <div v-if="meta.reason" class="ndp-row">
        <span class="ndp-label">{{ t('view.nodedetailpanel.reason') }}</span>
        <span class="ndp-value">{{ meta.reason }}</span>
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
  background: var(--bg-card, #fff);
  border: 1px solid var(--border, rgba(148,163,184,.35));
  border-radius: 8px;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.08);
  overflow: hidden;
  font-size: 13px;
}
.ndp-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 14px;
  border-bottom: 1px solid var(--border, rgba(148,163,184,.35));
  background: var(--bg-muted, rgba(148,163,184,.10));
}
.ndp-header h4 {
  margin: 0;
  flex: 1;
  font-size: 14px;
  font-weight: 600;
}
.ndp-close {
  border: none;
  background: none;
  cursor: pointer;
  padding: 4px;
  border-radius: 4px;
  color: var(--text-muted, #9aa3b2);
  display: flex;
  align-items: center;
}
.ndp-close:hover { background: var(--bg-hover, rgba(148,163,184,.16)); }
.ndp-body { padding: 10px 14px; }
.ndp-row {
  display: flex;
  gap: 12px;
  padding: 6px 0;
  align-items: flex-start;
}
.ndp-row + .ndp-row { border-top: 1px solid var(--border-light, rgba(148,163,184,.16)); }
.ndp-label {
  width: 80px;
  flex-shrink: 0;
  color: var(--text-muted, #9aa3b2);
  font-weight: 500;
}
.ndp-value {
  flex: 1;
  word-break: break-word;
}
.ndp-mono {
  font-family: 'SF Mono', 'Fira Code', monospace;
  font-size: 12px;
}
.ndp-error .ndp-value { color: var(--fail, #dc2626); }
.ndp-traceback { flex-direction: column; gap: 4px; }
.ndp-traceback-pre {
  margin: 0;
  padding: 8px;
  background: var(--bg-code, #1e293b);
  color: var(--text-code, rgba(148,163,184,.35));
  border-radius: 4px;
  font-size: 11px;
  font-family: 'SF Mono', 'Fira Code', monospace;
  overflow-x: auto;
  max-height: 200px;
  overflow-y: auto;
  white-space: pre-wrap;
}
.ndp-status-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  flex-shrink: 0;
}
.status-pending { color: var(--text-faint); }
.status-pending .ndp-status-dot, .ndp-status-dot.status-pending { background: var(--text-faint); }
.status-running { color: var(--info); }
.status-running .ndp-status-dot, .ndp-status-dot.status-running { background: var(--info); }
.status-success { color: var(--success); }
.status-success .ndp-status-dot, .ndp-status-dot.status-success { background: var(--success); }
.status-failed { color: var(--fail); }
.status-failed .ndp-status-dot, .ndp-status-dot.status-failed { background: var(--fail); }
.status-skipped { color: var(--warn); }
.status-skipped .ndp-status-dot, .ndp-status-dot.status-skipped { background: var(--warn); }
</style>