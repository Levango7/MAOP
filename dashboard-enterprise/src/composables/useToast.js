import { reactive } from 'vue';

export const toastState = reactive({ items: [] });
// CSR-only: 模块级自增 ID。本应用为纯客户端渲染（SPA），整个生命周期内只有一个
// 模块实例，不存在跨请求 ID 冲突。若未来引入 SSR，需改为 per-request 隔离的
// ID 生成（如 crypto.randomUUID() 或工厂函数封装实例级计数器）。
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
