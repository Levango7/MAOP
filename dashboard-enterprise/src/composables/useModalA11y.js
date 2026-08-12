/**
 * useModalA11y — 模态可达性增强(零 DOM 改动接入)。
 *
 * 对 11 处手写 modal-overlay 统一补齐:
 *   1. role="dialog" + aria-modal="true"(屏幕阅读器识别为模态)
 *   2. Esc 键关闭(与用户习惯的"点遮罩关闭"行为对齐: @click.self)
 *   3. 打开时焦点移入模态内第一个可聚焦元素;关闭时焦点还原到触发前元素
 *   4. 简易 focus trap: Tab 在模态内循环,不会跑到模态背后
 *
 * 用法(在 setup 顶层调用一次):
 *   const dlg = useModalA11y(() => showDialog.value, () => (showDialog.value = false));
 *   // 模态根元素加 ref: <div ref="dlg.rootRef" class="modal-overlay" @click.self="...">
 *
 * 注意: 现有 modal 的根元素是普通 <div>,不可聚焦,focus trap 靠
 * 把焦点定向到内部第一个 [autofocus]/button/input/[tabindex] 元素实现。
 */
import { watch, onBeforeUnmount } from 'vue';

const FOCUSABLE_SELECTOR =
  'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), ' +
  'select:not([disabled]), [tabindex]:not([tabindex="-1"])';

export function useModalA11y(isOpen, onClose) {
  let previousFocus = null;

  function handleKeydown(e) {
    if (!isOpen()) return;
    if (e.key === 'Escape') {
      e.preventDefault();
      onClose();
    }
  }

  function handleFocusTrap(e) {
    if (!isOpen() || e.key !== 'Tab') return;
    const root = document.querySelector('[data-modal-root="true"][aria-modal="true"]');
    if (!root) return;
    const focusables = Array.from(root.querySelectorAll(FOCUSABLE_SELECTOR))
      .filter((el) => el.offsetParent !== null); // visible only
    if (!focusables.length) return;
    const first = focusables[0];
    const last = focusables[focusables.length - 1];
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
  }

  // global Esc listener (always mounted while composable is alive)
  window.addEventListener('keydown', handleKeydown);
  window.addEventListener('keydown', handleFocusTrap, true);

  watch(isOpen, (open) => {
    if (open) {
      previousFocus = document.activeElement;
      // 下一拍再聚焦,等 v-if 渲染完成
      requestAnimationFrame(() => {
        const root = document.querySelector('[data-modal-root="true"][aria-modal="true"]');
        if (!root) return;
        if (!root.hasAttribute('role')) root.setAttribute('role', 'dialog');
        const target =
          root.querySelector('[autofocus]') ||
          root.querySelector(FOCUSABLE_SELECTOR);
        if (target) target.focus({ preventScroll: true });
      });
    } else if (previousFocus && typeof previousFocus.focus === 'function') {
      // 关闭后焦点还原
      requestAnimationFrame(() => {
        try { previousFocus.focus({ preventScroll: true }); } catch { /* 元素可能已卸载 */ }
        previousFocus = null;
      });
    }
  });

  onBeforeUnmount(() => {
    window.removeEventListener('keydown', handleKeydown);
    window.removeEventListener('keydown', handleFocusTrap, true);
  });
}
