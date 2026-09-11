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
 * 多模态支持: 传入第三个参数 containerEl 可指定当前模态的根元素，
 * 避免 document.querySelector 全局查询命中其他模态。containerEl 支持：
 *   - HTMLElement: 直接使用
 *   - Vue ref (有 .value): 自动解包
 *   - getter 函数: 调用获取元素（惰性求值，适合元素动态挂载场景）
 *   - undefined/null: fallback 到全局查询（向后兼容）
 *
 * 注意: 现有 modal 的根元素是普通 <div>,不可聚焦,focus trap 靠
 * 把焦点定向到内部第一个 [autofocus]/button/input/[tabindex] 元素实现。
 */
import { watch, onBeforeUnmount } from 'vue';

const FOCUSABLE_SELECTOR =
  'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), ' +
  'select:not([disabled]), [tabindex]:not([tabindex="-1"])';

const MODAL_ROOT_SELECTOR = '[data-modal-root="true"][aria-modal="true"]';

export function useModalA11y(isOpen, onClose, containerEl) {
  let previousFocus = null;

  // L14 fix: focusable 元素缓存，避免 handleFocusTrap 每次按键都执行
  // querySelectorAll（模态内元素多时性能开销显著）。仅在 root 变化时重新查询。
  let _focusableCache = null;
  let _focusableCacheRoot = null;
  // H-3 fix: MutationObserver 监听缓存 root 内的 DOM 变化，动态内容
  //（v-if、异步加载、表单展开等）变化时自动失效缓存，避免 focus trap
  // 命中陈旧元素列表导致焦点跳到已移除/隐藏的元素上。
  let _focusableObserver = null;

  function invalidateFocusableCache() {
    _focusableCache = null;
    _focusableCacheRoot = null;
  }

  function getFocusables(root) {
    if (_focusableCacheRoot === root && _focusableCache) {
      return _focusableCache;
    }
    _focusableCacheRoot = root;
    _focusableCache = Array.from(root.querySelectorAll(FOCUSABLE_SELECTOR))
      .filter((el) => el.offsetParent !== null); // visible only
    // H-3 fix: 为新缓存的 root 挂载 MutationObserver，监听子树变化自动失效缓存。
    // 先断开旧 observer（root 变化场景），再为新 root 建立 observer。
    if (_focusableObserver) {
      _focusableObserver.disconnect();
      _focusableObserver = null;
    }
    if (typeof MutationObserver !== 'undefined') {
      _focusableObserver = new MutationObserver(invalidateFocusableCache);
      _focusableObserver.observe(root, {
        childList: true,
        subtree: true,
        attributes: true,
        attributeFilter: ['disabled', 'tabindex', 'hidden', 'style', 'class'],
      });
    }
    return _focusableCache;
  }

  /**
   * 解析当前模态根元素：优先使用传入的 containerEl，fallback 到全局查询。
   * 支持 HTMLElement / Vue ref / getter 函数三种形式。
   * @returns {HTMLElement|null}
   */
  function resolveRoot() {
    if (containerEl) {
      // getter 函数: 惰性求值，适合元素动态挂载场景
      if (typeof containerEl === 'function') return containerEl();
      // Vue ref: 自动解包 .value
      if (containerEl.value) return containerEl.value;
      // M4 fix: 当 containerEl 是 Vue ref 且 .value 为 null 时（元素尚未挂载
      // 或已卸载），返回 null 而非返回 ref 对象本身——否则后续
      // root.querySelector / root.hasAttribute 会因 root 不是 HTMLElement 而抛错。
      // 仅当 containerEl 是真正的 HTMLElement 时才直接使用。
      if (containerEl instanceof HTMLElement) return containerEl;
      return null;
    }
    // fallback: 全局查询（向后兼容单模态场景）
    return document.querySelector(MODAL_ROOT_SELECTOR);
  }

  function handleKeydown(e) {
    if (!isOpen()) return;
    if (e.key === 'Escape') {
      e.preventDefault();
      onClose();
    }
  }

  function handleFocusTrap(e) {
    if (!isOpen() || e.key !== 'Tab') return;
    const root = resolveRoot();
    if (!root) return;
    // L14 fix: 使用缓存的 focusable 元素列表，避免每次 Tab 按键都执行 querySelectorAll。
    const focusables = getFocusables(root);
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
  // SSR-safe: guard window access so the composable can be imported in a
  // server/SSR or test (jsdom-less) context without throwing ReferenceError.
  const hasWindow = typeof window !== 'undefined';
  if (hasWindow) {
    window.addEventListener('keydown', handleKeydown);
    window.addEventListener('keydown', handleFocusTrap, true);
  }

  watch(isOpen, (open) => {
    if (open) {
      previousFocus = document.activeElement;
      // 下一拍再聚焦,等 v-if 渲染完成
      requestAnimationFrame(() => {
        const root = resolveRoot();
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
    // SSR-safe: mirror the mount-time guard so removal never throws if window
    // was undefined at setup (e.g. composable imported but never mounted in SSR).
    if (hasWindow) {
      window.removeEventListener('keydown', handleKeydown);
      window.removeEventListener('keydown', handleFocusTrap, true);
    }
    // H-3 fix: 断开 MutationObserver，释放引用避免泄漏。
    if (_focusableObserver) {
      _focusableObserver.disconnect();
      _focusableObserver = null;
    }
  });
}
