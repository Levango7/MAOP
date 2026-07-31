<template>
  <div class="skeleton-wrap" :class="{ block }">
    <template v-if="lines > 1">
      <span
        v-for="n in lines"
        :key="n"
        class="skeleton"
        :style="{ width: n === lines ? '60%' : '100%', height: height, borderRadius: radius }"
      ></span>
    </template>
    <span
      v-else
      class="skeleton"
      :class="{ circle }"
      :style="{ width, height, borderRadius: radius }"
    ></span>
  </div>
</template>

<script setup>
defineProps({
  width: { type: String, default: '100%' },
  height: { type: String, default: '14px' },
  radius: { type: String, default: '6px' },
  circle: { type: Boolean, default: false },
  lines: { type: Number, default: 1 },
  block: { type: Boolean, default: false },
});
</script>

<style scoped>
.skeleton-wrap { display: flex; flex-direction: column; gap: 8px; }
.skeleton-wrap.block { width: 100%; }
.skeleton {
  display: block;
  position: relative;
  overflow: hidden;
  background: var(--surface-2);
}
.skeleton.circle { border-radius: 50% !important; }
.skeleton::after {
  content: "";
  position: absolute;
  inset: 0;
  transform: translateX(-100%);
  background: linear-gradient(90deg, transparent, color-mix(in srgb, var(--text-faint) 22%, transparent), transparent);
  animation: maop-shimmer 1.2s infinite;
}
</style>
