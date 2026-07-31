<template>
  <div class="empty">
    <div class="empty__icon" v-if="icon"><AppIcon :name="icon" :size="34" /></div>
    <div class="empty__title" v-if="title">{{ title }}</div>
    <div class="empty__desc" v-if="description"><slot>{{ description }}</slot></div>
    <div class="empty__actions" v-if="$slots.actions"><slot name="actions" /></div>
  </div>
</template>

<script setup>
import AppIcon from './AppIcon.vue';
defineProps({
  icon: { type: String, default: 'info' },
  title: { type: String, default: '' },
  description: { type: String, default: '' },
});
</script>

<style scoped>
.empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
  padding: var(--sp-8) var(--sp-5);
  color: var(--text-faint);
  gap: var(--sp-2);
}
.empty__icon { color: var(--text-faint); opacity: .7; margin-bottom: var(--sp-2); }
.empty__title { font-size: var(--fs-md); font-weight: 600; color: var(--text-muted); }
.empty__desc { font-size: var(--fs-sm); max-width: 320px; }
.empty__actions { margin-top: var(--sp-3); display: flex; gap: var(--sp-2); }
</style>
