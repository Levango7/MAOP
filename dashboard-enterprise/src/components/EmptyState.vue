<template>
  <div class="empty">
    <div v-if="icon" class="empty__icon" :class="tone ? `empty__icon--${tone}` : ''"><AppIcon :name="icon" :size="34" /></div>
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
  tone: { type: String, default: '' },
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
  /* 56px 为 EmptyState 图标容器视觉规格固定值 */
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
/* max-width 320px 为 EmptyState 描述文本视觉规格固定值 */
.empty__desc { font-size: var(--fs-sm); max-width: 320px; line-height: 1.55; }
.empty__actions { margin-top: var(--sp-3); display: flex; gap: var(--sp-2); }
/* tone 修饰符: 根据语义调整图标颜色 */
.empty__icon--fail { color: var(--fail); }
.empty__icon--success { color: var(--success); }
.empty__icon--warn { color: var(--warn); }
.empty__icon--info { color: var(--info); }
</style>
