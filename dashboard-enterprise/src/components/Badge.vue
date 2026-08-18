<template>
  <span class="badge" :class="'badge--' + tone">
    <AppIcon v-if="icon" :name="icon" :size="12" />
    <slot>{{ label }}</slot>
  </span>
</template>

<script setup>
import AppIcon from './AppIcon.vue';
defineProps({
  label: { type: String, default: '' },
  tone: { type: String, default: 'neutral' }, // neutral|brand|success|warn|fail|info
  icon: { type: String, default: '' },
});
</script>

<style scoped>
.badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 9px;
  border-radius: var(--r-full);
  font-size: var(--fs-xs);
  font-weight: 600;
  line-height: 1.6;
  border: 1px solid transparent;
  white-space: nowrap;
  letter-spacing: .01em;
}
.badge--neutral { background: var(--surface-2); color: var(--text-muted); border-color: var(--border-subtle, var(--border)); }
.badge--brand   { background: var(--brand-soft); color: var(--brand-strong); border-color: color-mix(in srgb, var(--brand) 30%, transparent); box-shadow: 0 1px 2px color-mix(in srgb, var(--brand) 12%, transparent); }
.badge--success { background: var(--success-soft); color: var(--success); border-color: color-mix(in srgb, var(--success) 30%, transparent); }
.badge--warn    { background: var(--warn-soft); color: var(--warn); border-color: color-mix(in srgb, var(--warn) 30%, transparent); }
.badge--fail    { background: var(--fail-soft); color: var(--fail); border-color: color-mix(in srgb, var(--fail) 30%, transparent); }
.badge--info    { background: var(--info-soft); color: var(--info); border-color: color-mix(in srgb, var(--info) 30%, transparent); }
</style>
