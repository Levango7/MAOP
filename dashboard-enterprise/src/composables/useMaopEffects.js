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
import { ref, onUnmounted, getCurrentInstance } from 'vue'

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

// ── 路由懒加载进度条绑定 ─────────────────────────────────────────────────
// 2026-11-08 实测缺陷：router 有 55 处动态 import()（路由级分包），首次进入
// 某路由时 chunk 未到位，`<router-view>` 整块空白 4–10 秒——用户看到的是
// "页面坏了"而不是"在加载"（截图证据 docs/audits/frontend-runtime-spotcheck）。
//
// MaopLoadingBar.vue / 全局事件 / useLoadingBar() 三样此前都已存在且组件已
// 渲染在 App.vue，缺的只是"谁在导航时触发它"——全仓零调用点，属于零件齐全的
// 死接线。本组合式函数补上触发器，并把它从 App.vue 里抽出来以便单测。
//
// 两处刻意的设计：
//   ① `afterEach` 之前**还有** beforeEach——同一路径的重复导航不闪进度条；
//   ② 兜底定时器（默认 15s）——chunk 脚本 404 之类 afterEach 不来的场景，
//      进度条不能永远转下去。afterEach 与 onError 都会先清掉它。
export function useRouteLoadingBar(router, maxMs = 15000) {
  const { start, stop } = useLoadingBar()
  let timer = null

  function clearTimer() {
    if (timer !== null) { clearTimeout(timer); timer = null }
  }
  function show() {
    start()
    clearTimer()
    timer = setTimeout(stop, maxMs)
  }
  function hide() {
    clearTimer()
    stop()
  }

  // 注意用返回值而非已弃用的 next() 回调（vue-router 4 新写法，R0025）。
  const removeGuard = router.beforeEach((to, from) => {
    if (to.fullPath !== from.fullPath) show()
  })
  const removeAfter = router.afterEach(() => hide())
  const removeError = router.onError(() => hide())

  // 返回 disposer：调用方（含单测）自己决定何时摘除。组件内调用时额外挂
  // onUnmounted——但必须有活跃实例才挂，否则在纯 router 单测里 Vue 会警告
  // "onUnmounted is called when there is no active component instance"。
  function dispose() {
    removeGuard()
    removeAfter()
    removeError()
    clearTimer()
  }
  if (getCurrentInstance()) {
    onUnmounted(dispose)
  }
  return dispose
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