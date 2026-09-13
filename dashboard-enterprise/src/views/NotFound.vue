<template>
  <!-- P1-3 fix: 404 提示页 —— 替代原静默重定向，给用户明确反馈 -->
  <div class="not-found">
    <div class="not-found__card">
      <h1 class="not-found__code">404</h1>
      <p class="not-found__title">页面未找到</p>
      <p class="not-found__desc">{{ countdown }} 秒后自动返回首页…</p>
      <button class="not-found__btn" @click="goHome">立即返回首页</button>
    </div>
  </div>
</template>

<script setup>
// P1-3 fix: 404 提示页 —— 显示"页面未找到，3秒后返回首页"，
// 替代原 catch-all 静默重定向（用户无感知）。
import { ref, onMounted, onUnmounted } from 'vue';
import { useRouter } from 'vue-router';

const router = useRouter();
const countdown = ref(3);
let timer = null;

function goHome() {
  if (timer) {
    clearInterval(timer);
    timer = null;
  }
  // 使用命名路由 'overview' 而非硬编码路径，与项目惯例一致
  router.push({ name: 'overview' });
}

onMounted(() => {
  timer = setInterval(() => {
    countdown.value -= 1;
    if (countdown.value <= 0) goHome();
  }, 1000);
});

onUnmounted(() => {
  if (timer) clearInterval(timer);
});
</script>

<style scoped>
/* F4: BEM 命名 — block: not-found, element: __* */
.not-found {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 60vh;
  padding: var(--sp-6, 2rem);
}
.not-found__card {
  text-align: center;
  max-width: 400px;
}
.not-found__code {
  font-size: 4rem;
  font-weight: 700;
  color: var(--text-muted, #64748b);
  margin: 0 0 var(--sp-2, 0.5rem);
  line-height: 1;
}
.not-found__title {
  font-size: 1.25rem;
  font-weight: 600;
  color: var(--text, #0f172a);
  margin: 0 0 var(--sp-2, 0.5rem);
}
.not-found__desc {
  font-size: var(--fs-sm, 0.875rem);
  color: var(--text-muted, #64748b);
  margin: 0 0 var(--sp-4, 1rem);
}
.not-found__btn {
  background: var(--brand, #2563eb);
  color: var(--brand-contrast, #fff);
  border: none;
  border-radius: var(--r-md, 6px);
  padding: var(--sp-2, 0.5rem) var(--sp-4, 1rem);
  font-size: var(--fs-sm, 0.875rem);
  cursor: pointer;
  transition: opacity 0.15s ease;
}
.not-found__btn:hover {
  opacity: 0.9;
}
.not-found__btn:focus-visible {
  outline: 2px solid var(--brand, #2563eb);
  outline-offset: 2px;
}
</style>