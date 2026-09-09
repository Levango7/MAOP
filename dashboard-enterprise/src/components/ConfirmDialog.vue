<template>
  <div
    v-if="confirmState.visible"
    class="confirm-overlay"
    data-modal-root="true"
    aria-modal="true"
    role="dialog"
    @click.self="onCancel"
  >
    <div class="confirm-dialog" @keydown.esc="onCancel">
      <div v-if="confirmState.title" class="confirm-title">{{ confirmState.title }}</div>
      <div class="confirm-message">{{ confirmState.message }}</div>
      <div class="confirm-actions">
        <button class="btn-cancel" @click="onCancel">
          {{ confirmState.cancelText || t('common.cancel') }}
        </button>
        <button
          class="btn-confirm"
          :class="'btn-confirm--' + confirmState.tone"
          @click="onConfirm"
        >
          {{ confirmState.confirmText || t('common.confirm') }}
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { useI18n } from '../i18n/index.js';
import { confirmState, useConfirm } from '../composables/useConfirm.js';

const { t } = useI18n();
const { resolve } = useConfirm();

function onConfirm() { resolve(true); }
function onCancel() { resolve(false); }
</script>

<style scoped>
.confirm-overlay {
  position: fixed;
  inset: 0;
  z-index: var(--z-modal);
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--overlay-scrim);
}
.confirm-dialog {
  min-width: 320px;
  max-width: 480px;
  padding: var(--sp-5);
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r-lg);
  box-shadow: var(--shadow-modal);
  display: flex;
  flex-direction: column;
  gap: var(--sp-3);
}
.confirm-title {
  font-size: var(--fs-lg);
  font-weight: 600;
  color: var(--text);
}
.confirm-message {
  font-size: var(--fs-base);
  line-height: 1.5;
  color: var(--text-muted);
}
.confirm-actions {
  display: flex;
  justify-content: flex-end;
  gap: var(--sp-2);
  margin-top: var(--sp-2);
}
.btn-cancel,
.btn-confirm {
  padding: var(--sp-2) var(--sp-4);
  border-radius: var(--r-md);
  font-size: var(--fs-base);
  cursor: pointer;
  transition: background var(--motion) var(--ease);
}
.btn-cancel {
  background: transparent;
  border: 1px solid var(--border);
  color: var(--text-muted);
}
.btn-cancel:hover {
  background: var(--bg-hover);
}
.btn-confirm {
  border: none;
  color: var(--brand-contrast);
}
.btn-confirm--danger {
  background: var(--fail);
}
.btn-confirm--danger:hover {
  background: var(--fail-strong);
}
.btn-confirm--info {
  background: var(--brand);
}
.btn-confirm--info:hover {
  background: var(--brand-strong);
}
</style>