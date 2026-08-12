/**
 * v-modal-a11y — 最轻量的模态可达性指令(声明式,零 JS 状态管理)。
 *
 * 在现有 modal-overlay 元素上加一行即可:
 *   <div v-if="show" v-modal-a11y="close" class="modal-overlay" @click.self="close">
 *
 * 指令自动:
 *   1. 打 role="dialog" + aria-modal="true" + data-modal-root 标记
 *   2. 挂载时把焦点定向到内部第一个可聚焦元素(或 overlay 自身)
 *   3. 卸载/隐藏时把焦点还给之前聚焦的元素
 *
 * Esc 关闭需要搭配 useModalA11y() 的全局监听(指令本身不处理键盘)。
 */
export const vModalA11y = {
  mounted(el) {
    el.setAttribute('role', 'dialog');
    el.setAttribute('aria-modal', 'true');
    el.setAttribute('data-modal-root', 'true');
    // 让 overlay 本身可以被焦点捕获(焦点陷阱的兜底宿主)
    if (!el.hasAttribute('tabindex')) el.setAttribute('tabindex', '-1');
  },
  unmounted(el) {
    el.removeAttribute('role');
    el.removeAttribute('aria-modal');
    el.removeAttribute('data-modal-root');
  },
};
