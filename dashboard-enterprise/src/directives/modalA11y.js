/**
 * v-modal-a11y — 模态可达性标记指令(声明式;只打 ARIA/DOM 标记)。
 *
 * 在现有 modal-overlay 元素上加一行即可:
 *   <div v-if="show" v-modal-a11y class="modal-overlay"
 *        @click.self="close" @modal:escape="close">
 *
 * 指令只做两件事:
 *   1. 打 role="dialog" + aria-modal="true" + data-modal-root 标记
 *   2. 未设 tabindex 时补 tabindex="-1",让 overlay 可被程序聚焦
 *
 * 明确不做(勿依赖本指令):
 *   - 焦点定向 / 关闭后焦点还原 / focus trap:由各组件自己实现,或接
 *     composables/useModalA11y.js。那是另一套独立机制,不是本指令的依赖。
 *   - Esc 关闭:App.vue 的 onGlobalEsc 选中最后一个 [data-modal-root="true"]
 *     并在其上派发冒泡的 'modal:escape';视图必须在同一 overlay 上用
 *     @modal:escape 接住并调用自己的关闭逻辑。指令不接收参数
 *     (传了也会被忽略),不要把关闭回调写进 v-modal-a11y="..."。
 *
 * 守卫见 __tests__/modal-escape-coverage.test.js。
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
