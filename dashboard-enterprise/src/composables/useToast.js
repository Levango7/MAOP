import { reactive } from 'vue';

// P2 fix: toast 队列数量上限。短时间内大量 toast 调用会堆积大量 DOM,
// 导致 UI 崁溃。超过上限时移除最旧的 toast (FIFO 淘汰)。
const MAX_TOASTS = 5;

export const toastState = reactive({ items: [] });
let _id = 0;

export function useToast() {
  function dismiss(tid) {
    const i = toastState.items.findIndex((x) => x.id === tid);
    if (i >= 0) {
      const item = toastState.items[i];
      if (item._timer) clearTimeout(item._timer);
      toastState.items.splice(i, 1);
    }
  }
  function show(message, opts = {}) {
    const t = {
      id: ++_id,
      message,
      tone: opts.tone || 'info',
      timeout: opts.timeout === null ? 3200 : opts.timeout,
    };
    // P2 fix: 超过上限时移除最旧的 toast (FIFO), 并清理其定时器避免泄漏。
    while (toastState.items.length >= MAX_TOASTS) {
      const oldest = toastState.items.shift();
      if (oldest && oldest._timer) clearTimeout(oldest._timer);
    }
    toastState.items.push(t);
    if (t.timeout) t._timer = setTimeout(() => dismiss(t.id), t.timeout);
    return t.id;
  }
  return {
    show,
    dismiss,
    success: (m, o) => show(m, { ...o, tone: 'success' }),
    error: (m, o) => show(m, { ...o, tone: 'fail' }),
    warn: (m, o) => show(m, { ...o, tone: 'warn' }),
    info: (m, o) => show(m, { ...o, tone: 'info' }),
  };
}
