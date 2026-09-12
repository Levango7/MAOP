// MAOP 特效组合式函数 (composables)
// ==========================================================================
// 统一管理 MAOP 微交互特效的触发与状态:
//   - useLoadingBar(): 顶部加载条控制 (派发全局事件)
//   - useNumberFlip(): 数字翻牌动画 (带状态与定时器清理)
//   - useToast(): Toast 通知 (派发全局事件, 由 MaopToast.vue 接收)
//
// 设计规范: 所有动画统一使用 cubic-bezier(0.4, 0, 0.2, 1) 缓动,
//           时长 0.15s/0.3s/2s, 色彩用 CSS 变量, 尊重 prefers-reduced-motion。
//
// 生命周期清理: useNumberFlip 的 setTimeout 在 onUnmounted 中 clearTimeout,
//   避免组件卸载后仍修改 ref (经验来源:
//   2026-09-10-vue-composable-lifecycle-cleanup-env-safety-checklist)
import { ref, onUnmounted } from 'vue'

// ── 页面加载条控制 ──────────────────────────────────────────────────────────
// 派发全局事件, 由 MaopLoadingBar.vue 监听。SSR 安全: 检查 typeof window。
export function useLoadingBar() {
  const start = () => {
    if (typeof window !== 'undefined') {
      window.dispatchEvent(new Event('maop-loading-start'))
    }
  }
  const stop = () => {
    if (typeof window !== 'undefined') {
      window.dispatchEvent(new Event('maop-loading-stop'))
    }
  }
  return { start, stop }
}

// ── 数字翻牌 ────────────────────────────────────────────────────────────────
// 旧值上滑出 + 新值下滑入 (maop-number-flip 动画 0.3s)。
// setTimeout 在 onUnmounted 中清理, 避免卸载后修改 ref。
export function useNumberFlip() {
  const displayValue = ref(0)
  const flipClass = ref('')
  let _timer = null

  function update(newValue) {
    flipClass.value = 'maop-number-flip'
    displayValue.value = newValue
    // 清理上一个定时器, 避免堆叠
    if (_timer) clearTimeout(_timer)
    // 动画时长 0.3s, 之后移除 class 以便下次重新触发
    _timer = setTimeout(() => {
      flipClass.value = ''
      _timer = null
    }, 300)
  }

  onUnmounted(() => {
    if (_timer) clearTimeout(_timer)
  })

  return { displayValue, flipClass, update }
}

// ── Toast 通知 ──────────────────────────────────────────────────────────────
// 派发全局 CustomEvent, 由 MaopToast.vue 监听并显示。SSR 安全。
// type: 'info' | 'success' | 'warn' | 'error'
export function useToast() {
  function show(message, type = 'info') {
    if (typeof window !== 'undefined') {
      window.dispatchEvent(new CustomEvent('maop-toast', { detail: { message, type } }))
    }
  }
  return { show }
}