<template>
  <div class="empty">
    <div v-if="icon" class="empty__icon"><AppIcon :name="icon" :size="34" /></div>
    <div v-if="title" class="empty__title">{{ title }}</div>
    <div v-if="description" class="empty__desc"><slot>{{ description }}</slot></div>
    <div v-if="$slots.actions" class="empty__actions"><slot name="actions" /></div>
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
.empty__icon {
  display: grid;
  place-items: center;
  width: 56px;
  height: 56px;
  margin-bottom: var(--sp-3);
  border-radius: var(--r-full);
  background: var(--surface-2);
  border: 1px solid var(--border);
  color: var(--text-faint);
  opacity: .85;
}
.empty__title { font-size: var(--fs-md); font-weight: 600; color: var(--text-muted); }
.empty__desc { font-size: var(--fs-sm); max-width: 320px; line-height: 1.55; }
.empty__actions { margin-top: var(--sp-3); display: flex; gap: var(--sp-2); }
</style>
