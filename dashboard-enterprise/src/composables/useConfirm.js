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
 *
 * F22 修复: 采用队列模式避免竞态条件。并发 showConfirm 时，请求依次入队，
 * 每次只显示队首请求；resolve 后出队并显示下一个，确保每个 Promise 都会被 resolve。
 */
import { reactive } from 'vue';

// 请求队列：每个元素 { message, title, confirmText, cancelText, tone, _resolve }
const confirmQueue = [];

// 对外暴露的响应式状态（始终投影队首请求），保持与 ConfirmDialog.vue 的 API 兼容
export const confirmState = reactive({
  visible: false,
  message: '',
  title: '',
  confirmText: '',
  cancelText: '',
  tone: 'danger',
  _resolve: null,
});

/** 将队首请求投影到 confirmState；队列为空时隐藏对话框。 */
function applyHeadToState() {
  const head = confirmQueue[0];
  if (head) {
    confirmState.message = head.message;
    confirmState.title = head.title;
    confirmState.confirmText = head.confirmText;
    confirmState.cancelText = head.cancelText;
    confirmState.tone = head.tone;
    confirmState._resolve = head._resolve;
    confirmState.visible = true;
  } else {
    confirmState.visible = false;
    confirmState.message = '';
    confirmState.title = '';
    confirmState.confirmText = '';
    confirmState.cancelText = '';
    confirmState.tone = 'danger';
    confirmState._resolve = null;
  }
}

export function useConfirm() {
  function showConfirm(opts = {}) {
    return new Promise((resolve) => {
      const request = {
        message: opts.message || '',
        title: opts.title || '',
        confirmText: opts.confirmText || '',
        cancelText: opts.cancelText || '',
        tone: opts.tone || 'danger',
        _resolve: resolve,
      };
      confirmQueue.push(request);
      // 若此前没有正在显示的对话框，立即显示队首
      if (confirmQueue.length === 1) {
        applyHeadToState();
      }
    });
  }

  function resolve(ok) {
    // 出队当前请求并 resolve 其 Promise
    const head = confirmQueue.shift();
    if (head && head._resolve) {
      head._resolve(ok);
    }
    // 显示下一个请求（如果有）
    applyHeadToState();
  }

  return { showConfirm, resolve };
}
