/**
 * useConfirm — 全局确认对话框 composable (替代原生 window.confirm)。
 *
 * 用法 (在 App.vue 顶层挂载 <ConfirmDialog /> 一次):
 *   import { useConfirm } from './composables/useConfirm.js';
 *   const { showConfirm } = useConfirm();
 *   const ok = await showConfirm({ message: '确认删除？', tone: 'danger' });
 *   if (!ok) return;
 *
 * tone: 'danger' (默认, 红色确认键) | 'info' (蓝色确认键)
 * 返回 Promise<boolean>, true=确认, false=取消
 */
import { reactive } from 'vue';

export const confirmState = reactive({
  visible: false,
  message: '',
  title: '',
  confirmText: '',
  cancelText: '',
  tone: 'danger',
  _resolve: null,
});

export function useConfirm() {
  function showConfirm(opts = {}) {
    return new Promise((resolve) => {
      confirmState.message = opts.message || '';
      confirmState.title = opts.title || '';
      confirmState.confirmText = opts.confirmText || '';
      confirmState.cancelText = opts.cancelText || '';
      confirmState.tone = opts.tone || 'danger';
      confirmState._resolve = resolve;
      confirmState.visible = true;
    });
  }

  function resolve(ok) {
    if (confirmState._resolve) {
      confirmState._resolve(ok);
      confirmState._resolve = null;
    }
    confirmState.visible = false;
  }

  return { showConfirm, resolve };
}