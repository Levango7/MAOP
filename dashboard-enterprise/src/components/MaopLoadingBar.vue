<template>
  <!-- 顶部 2px 品牌色加载条: 由全局事件 maop-loading-start / maop-loading-stop 控制 -->
  <div v-if="loading" class="maop-loading-bar" role="progressbar" :aria-label="t('a11y.loading')"></div>
</template>

<script setup>
// MAOP 顶部加载条组件
// 通过监听全局事件 maop-loading-start / maop-loading-stop 控制显隐,
// 配合 maop-motion.css 中的 .maop-loading-bar 流动动画。
// 所有 addEventListener 均在 onUnmounted 中配对 removeEventListener,
// handler 使用命名函数引用以保证移除成功 (经验: 匿名箭头函数无法移除)。
import { ref, onUnmounted } from 'vue'
import { useI18n } from '../i18n'

const { t } = useI18n()

const loading = ref(false)

// 命名 handler: 必须保存引用才能在 onUnmounted 中正确移除
function onStart() { loading.value = true }
function onStop() { loading.value = false }

// 仅在浏览器环境注册 (SSR 安全)
if (typeof window !== 'undefined') {
  window.addEventListener('maop-loading-start', onStart)
  window.addEventListener('maop-loading-stop', onStop)
}

onUnmounted(() => {
  if (typeof window !== 'undefined') {
    window.removeEventListener('maop-loading-start', onStart)
    window.removeEventListener('maop-loading-stop', onStop)
  }
})
</script>