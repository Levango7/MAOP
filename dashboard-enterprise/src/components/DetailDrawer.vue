<template>
  <Teleport to="body">
    <Transition name="drawer">
      <div v-if="open" class="detail-drawer" @keydown.esc="onClose">
        <!-- 遮罩 -->
        <div class="detail-drawer__scrim" @click="onClose"></div>

        <!-- 面板 -->
        <aside
          ref="panelRef"
          class="detail-drawer__panel"
          role="dialog"
          aria-modal="true"
          :aria-label="title || t('common.details')"
          tabindex="-1"
        >
          <header class="detail-drawer__head">
            <AppIcon v-if="icon" :name="icon" :size="16" class="detail-drawer__icon" />
            <h3 class="detail-drawer__title">{{ title }}</h3>
            <button class="detail-drawer__close" type="button" :aria-label="t('action.close')" @click="onClose">
              <AppIcon name="x" :size="16" />
            </button>
          </header>
          <div class="detail-drawer__body">
            <slot />
          </div>
          <footer v-if="$slots.footer" class="detail-drawer__foot">
            <slot name="footer" />
          </footer>
        </aside>
      </div>
    </Transition>
  </Teleport>
</template>

<script setup>
/**
 * DetailDrawer — 统一"详情查看"交互(迭代 C2)。
 *
 * 迭代 C 的交互模板约定:
 *   - 查看详情 → DetailDrawer(右侧滑出, 480px)
 *   - 新建/编辑表单 → 全屏 Modal(保持现状)
 *   - 破坏性确认 → 小型居中 Modal(保持现状)
 *
 * 特性:
 *   - 打开时焦点移入面板, Esc/遮罩点击关闭, 关闭后焦点还原
 *   - 面板内键盘 Tab 焦点不泄漏到背后(scrim 拦截 + trap)
 *   - Teleport 到 body, 不依赖父布局的 z-index/overflow
 */
import { ref, onBeforeUnmount, watch, nextTick } from 'vue';
import AppIcon from './AppIcon.vue';
import { useI18n } from '../i18n';

const props = defineProps({
  open: { type: Boolean, default: false },
  title: { type: String, default: '' },
  icon: { type: String, default: '' },
});
const emit = defineEmits(['close']);

const { t } = useI18n();
const panelRef = ref(null);
let previousFocus = null;

function onClose() { emit('close'); }

function onKeydown(e) {
  if (!props.open) return;
  if (e.key === 'Escape') { onClose(); return; }
  if (e.key === 'Tab') {
    // 简易 trap: 焦点只留在面板内
    const panel = panelRef.value;
    if (!panel) return;
    const focusables = panel.querySelectorAll('button, a[href], input, select, textarea, [tabindex]:not([tabindex="-1"])');
    if (!focusables.length) return;
    const first = focusables[0];
    const last = focusables[focusables.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  }
}

watch(() => props.open, async (open) => {
  if (open) {
    previousFocus = document.activeElement;
    document.addEventListener('keydown', onKeydown);
    await nextTick();
    const panel = panelRef.value;
    if (panel) {
      panel.focus({ preventScroll: true });
      const first = panel.querySelector('button, a[href], input, select, textarea');
      if (first) first.focus({ preventScroll: true });
    }
  } else if (previousFocus) {
    document.removeEventListener('keydown', onKeydown);
    previousFocus.focus({ preventScroll: true });
    previousFocus = null;
  }
});

onBeforeUnmount(() => document.removeEventListener('keydown', onKeydown));
</script>

<style scoped>
.detail-drawer { position: fixed; inset: 0; z-index: calc(var(--z-modal) + 5); }
.detail-drawer__scrim { position: absolute; inset: 0; background: var(--overlay-scrim); }
.detail-drawer__panel {
  position: absolute;
  top: 0; right: 0; bottom: 0;
  width: min(480px, 92vw);
  background: var(--surface);
  border-left: 1px solid var(--border-strong);
  box-shadow: var(--shadow-lg);
  display: flex;
  flex-direction: column;
  outline: none;
}
.detail-drawer__head {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  padding: var(--sp-3) var(--sp-4);
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
}
.detail-drawer__icon { color: var(--brand-strong); }
.detail-drawer__title { font-size: var(--fs-md); font-weight: 600; color: var(--text); margin: 0; flex: 1; }
/* R7 修复: border-radius 从 var(--r-sm) 统一为 var(--r-md) (与 .close-btn / .modal__x 一致) */
.detail-drawer__close {
  display: grid; place-items: center;
  width: 30px; height: 30px;
  background: transparent; border: none; border-radius: var(--r-md);
  color: var(--text-muted); cursor: pointer;
  transition: background var(--motion) var(--ease), color var(--motion) var(--ease);
}
.detail-drawer__close:hover { background: var(--surface-2); color: var(--text); }
.detail-drawer__close:active { transform: scale(0.92); }
.detail-drawer__body { flex: 1; overflow-y: auto; padding: var(--sp-4); }
.detail-drawer__foot { flex-shrink: 0; padding: var(--sp-3) var(--sp-4); border-top: 1px solid var(--border); }

.drawer-enter-active, .drawer-leave-active { transition: opacity var(--motion) var(--ease); }
.drawer-enter-from, .drawer-leave-to { opacity: 0; }
.drawer-enter-active .detail-drawer__panel { transition: transform var(--motion-normal) var(--ease); }
.drawer-enter-from .detail-drawer__panel, .drawer-leave-to .detail-drawer__panel { transform: translateX(100%); }

/* a11y: 键盘焦点环 (P0 focus-visible 补充) */
.detail-drawer__close:focus-visible {
  outline: 2px solid var(--brand);
  outline-offset: 2px;
  border-radius: var(--r-sm);
}
</style>