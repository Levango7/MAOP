<template>
  <div class="toast-host" aria-live="polite" aria-atomic="false">
    <transition-group name="toast">
      <div
        v-for="t in toastState.items"
        :key="t.id"
        class="toast"
        :class="'toast--' + t.tone"
        role="status"
        @click="dismiss(t.id)"
      >
        <AppIcon :name="iconFor(t.tone)" :size="16" />
        <span class="toast__msg">{{ t.message }}</span>
      </div>
    </transition-group>
  </div>
</template>

<script setup>
import { toastState, useToast } from '../composables/useToast.js';
import AppIcon from './AppIcon.vue';

const { dismiss } = useToast();
function iconFor(tone) {
  return tone === 'success' ? 'check-circle'
    : tone === 'fail' ? 'x-circle'
    : tone === 'warn' ? 'alert-triangle'
    : 'info';
}
</script>

<style scoped>
.toast-host {
  position: fixed;
  right: var(--sp-5);
  bottom: var(--sp-5);
  z-index: var(--z-toast);
  display: flex;
  flex-direction: column;
  gap: var(--sp-2);
  max-width: 360px;
  pointer-events: none;
}
.toast {
  pointer-events: auto;
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  padding: var(--sp-3) var(--sp-4);
  background: var(--surface);
  border: 1px solid var(--border);
  border-left-width: 3px;
  border-radius: var(--r-md);
  box-shadow: var(--shadow-md);
  font-size: var(--fs-sm);
  color: var(--text);
  cursor: pointer;
}
.toast--success { border-left-color: var(--success); }
.toast--success .toast__msg, .toast--success :deep(svg) { color: var(--success); }
.toast--fail { border-left-color: var(--fail); }
.toast--fail :deep(svg) { color: var(--fail); }
.toast--warn { border-left-color: var(--warn); }
.toast--warn :deep(svg) { color: var(--warn); }
.toast--info { border-left-color: var(--brand); }
.toast--info :deep(svg) { color: var(--brand-strong); }
.toast__msg { flex: 1; }

.toast-enter-active, .toast-leave-active { transition: all var(--motion) var(--ease-out); }
.toast-enter-from { opacity: 0; transform: translateX(16px); }
.toast-leave-to { opacity: 0; transform: translateX(16px); }
</style>
