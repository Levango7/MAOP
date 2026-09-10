<template>
  <div
    v-if="confirmState.visible"
    ref="rootRef"
    class="confirm-overlay"
    data-modal-root="true"
    aria-modal="true"
    role="dialog"
    @click.self="onCancel"
  >
    <div class="confirm-dialog" @keydown.esc="onCancel">
      <div v-if="confirmState.title" class="confirm-dialog__title">{{ confirmState.title }}</div>
      <div class="confirm-dialog__message">{{ confirmState.message }}</div>
      <div class="confirm-dialog__actions">
        <button class="confirm-dialog__cancel" @click="onCancel">
          {{ confirmState.cancelText || t('common.cancel') }}
        </button>
        <button
          class="confirm-dialog__confirm"
          :class="'confirm-dialog__confirm--' + confirmState.tone"
          @click="onConfirm"
        >
          {{ confirmState.confirmText || t('common.confirm') }}
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue';
import { useI18n } from '../i18n';
import { confirmState, useConfirm } from '../composables/useConfirm.js';
import { useModalA11y } from '../composables/useModalA11y.js';

const { t } = useI18n();
const { resolve } = useConfirm();

const rootRef = ref(null);

// 焦点管理 + focus trap + Esc 关闭(复用 useModalA11y)
useModalA11y(
  () => confirmState.visible,
  () => onCancel(),
  () => rootRef.value,
);

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
.confirm-dialog__title {
  font-size: var(--fs-lg);
  font-weight: 600;
  color: var(--text);
}
.confirm-dialog__message {
  font-size: var(--fs-base);
  line-height: 1.5;
  color: var(--text-muted);
}
.confirm-dialog__actions {
  display: flex;
  justify-content: flex-end;
  gap: var(--sp-2);
  margin-top: var(--sp-2);
}
.confirm-dialog__cancel,
.confirm-dialog__confirm {
  padding: var(--sp-2) var(--sp-4);
  border-radius: var(--r-md);
  font-size: var(--fs-base);
  cursor: pointer;
  transition: background var(--motion) var(--ease);
}
.confirm-dialog__cancel {
  background: transparent;
  border: 1px solid var(--border);
  color: var(--text-muted);
}
.confirm-dialog__cancel:hover {
  background: var(--bg-hover);
}
.confirm-dialog__confirm {
  border: none;
  color: var(--brand-contrast);
}
.confirm-dialog__confirm--danger {
  background: var(--fail);
}
.confirm-dialog__confirm--danger:hover {
  background: var(--fail-strong);
}
.confirm-dialog__confirm--info {
  background: var(--brand);
}
.confirm-dialog__confirm--info:hover {
  background: var(--brand-strong);
}
</style>