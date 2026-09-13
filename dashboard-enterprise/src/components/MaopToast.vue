<template>
  <!-- MAOP Toast 通知容器: 多 Toast 堆叠, 从上到下排列。
       通过监听全局 maop-toast 事件显示通知, 使用 maop-toast-in / maop-toast-out 动画。 -->
  <div class="maop-toast-host" aria-live="polite" aria-atomic="false">
    <div
      v-for="item in items"
      :key="item.id"
      class="maop-toast"
      :class="['maop-toast--' + item.type, item.leaving ? 'maop-toast-out' : 'maop-toast-in']"
      role="status"
    >
      <span class="maop-toast__icon" aria-hidden="true">{{ iconFor(item.type) }}</span>
      <span class="maop-toast__msg">{{ item.message }}</span>
      <button
        class="maop-toast__close"
        :aria-label="t('a11y.closeNotification')"
        @click="dismiss(item.id)"
      >×</button>
    </div>
  </div>
</template>

<script setup>
// MAOP Toast 通知组件
// --------------------------------------------------------------------------
// 设计意图: 非 AI 模板的 fade+scale 弹出, 而是从右侧 16px 滑入 (maop-toast-in),
// 关闭时滑出 (maop-toast-out)。多 Toast 从上到下堆叠, 3 秒自动滑出, 支持手动关闭。
// 语义色由 CSS 变量驱动 (--brand / --success / --warn / --fail), 不用硬编码颜色。
//
// 生命周期清理: 监听 window 的 maop-toast 事件使用命名 handler, 在 onUnmounted
// 中配对 removeEventListener; 每个 toast 的自动关闭定时器在 dismiss 时 clearTimeout。
// (经验来源: 2026-09-10-vue-composable-lifecycle-cleanup-env-safety-checklist)
import { ref, onUnmounted } from 'vue'
import { useI18n } from '../i18n'

const { t } = useI18n()

const items = ref([])
// 模块级自增 ID (纯客户端 SPA, 无 SSR 跨请求冲突风险)
let _id = 0

// 语义图标 (纯文本符号, 遵循"禁止 emoji"规范 — 用几何符号代替)
function iconFor(type) {
  return type === 'success' ? '✓'
    : type === 'error' ? '✕'
    : type === 'warn' ? '!'
    : 'i'
}

// 添加一条 Toast: 启动 3 秒自动关闭定时器
function push(message, type = 'info') {
  const id = ++_id
  const item = { id, message, type, leaving: false, _timer: null }
  items.value.push(item)
  item._timer = setTimeout(() => dismiss(id), 3000)
  return id
}

// 关闭一条 Toast: 先标记 leaving 触发滑出动画, 动画结束后真正移除
function dismiss(id) {
  const item = items.value.find((x) => x.id === id)
  if (!item) return
  // 清理自动关闭定时器, 避免重复触发
  if (item._timer) { clearTimeout(item._timer); item._timer = null }
  // 已在离场中, 不重复触发
  if (item.leaving) return
  item.leaving = true
  // maop-toast-out 动画时长 0.3s, 之后真正移除
  setTimeout(() => {
    const idx = items.value.findIndex((x) => x.id === id)
    if (idx >= 0) items.value.splice(idx, 1)
  }, 300)
}

// 全局事件 handler: 接收 useMaopEffects.useToast().show 派发的 CustomEvent
function onMaopToast(e) {
  const d = e.detail || {}
  push(d.message, d.type || 'info')
}

// 仅在浏览器环境注册 (SSR 安全)
if (typeof window !== 'undefined') {
  window.addEventListener('maop-toast', onMaopToast)
}

onUnmounted(() => {
  if (typeof window !== 'undefined') {
    window.removeEventListener('maop-toast', onMaopToast)
  }
  // 清理所有未关闭 toast 的定时器
  items.value.forEach((item) => {
    if (item._timer) clearTimeout(item._timer)
  })
})
</script>

<style scoped>
/* Toast 容器: 固定在右下角, 从下到上堆叠 */
.maop-toast-host {
  position: fixed;
  top: auto;
  right: var(--sp-5);
  bottom: var(--sp-5);
  z-index: var(--z-toast);
  display: flex;
  flex-direction: column;
  gap: var(--sp-2);
  /* max-width 360px 为 Toast 视觉规格固定值 */
  max-width: 360px;
  pointer-events: none;
}

/* 单条 Toast: 语义色由 CSS 变量驱动, 1px 描边分隔 (JetBrains 风格) */
.maop-toast {
  pointer-events: auto;
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  padding: var(--sp-3) var(--sp-4);
  background: var(--surface);
  border: 1px solid var(--border);
  border-left-width: 3px;
  border-radius: var(--r-md);
  box-shadow: var(--shadow-md);
  font-size: var(--fs-sm);
  color: var(--text);
}

/* 语义色: 用 CSS 变量控制左边框与图标颜色, 不用硬编码 */
.maop-toast--info {
  border-left-color: var(--brand);
}
.maop-toast--info .maop-toast__icon {
  color: var(--brand);
}
.maop-toast--success {
  border-left-color: var(--success);
}
.maop-toast--success .maop-toast__icon {
  color: var(--success);
}
.maop-toast--warn {
  border-left-color: var(--warn);
}
.maop-toast--warn .maop-toast__icon {
  color: var(--warn);
}
.maop-toast--error {
  border-left-color: var(--fail);
}
.maop-toast--error .maop-toast__icon {
  color: var(--fail);
}

/* 图标: 几何符号, 居中显示 */
.maop-toast__icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  font-size: var(--fs-sm);
  font-weight: 600;
  font-style: normal;
  flex-shrink: 0;
}
.maop-toast__msg {
  flex: 1;
  line-height: 1.4;
}

/* 关闭按钮: 极简, 不抢视觉 */
.maop-toast__close {
  flex-shrink: 0;
  width: 20px;
  height: 20px;
  padding: 0;
  border: none;
  background: transparent;
  color: var(--text-muted);
  font-size: var(--fs-lg);
  line-height: 1;
  cursor: pointer;
  border-radius: 4px;
  transition: background var(--motion-fast) var(--ease),
              color var(--motion-fast) var(--ease);
}
.maop-toast__close:hover {
  background: var(--surface-3);
  color: var(--text);
}
.maop-toast__close:focus-visible {
  outline: 2px solid var(--brand);
  outline-offset: 2px;
}
</style>