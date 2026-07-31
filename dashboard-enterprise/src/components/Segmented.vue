<template>
  <div class="segmented" :class="'seg--' + size" role="tablist">
    <button
      v-for="opt in options"
      :key="opt.value"
      type="button"
      class="seg__item"
      :class="{ active: opt.value === modelValue }"
      role="tab"
      :aria-selected="opt.value === modelValue"
      @click="$emit('update:modelValue', opt.value)"
    >
      <AppIcon v-if="opt.icon" :name="opt.icon" :size="14" />
      <span v-if="opt.label">{{ opt.label }}</span>
    </button>
  </div>
</template>

<script setup>
import AppIcon from './AppIcon.vue';
defineProps({
  modelValue: { type: [String, Number], default: '' },
  options: { type: Array, required: true }, // [{ value, label?, icon? }]
  size: { type: String, default: 'md' }, // sm | md
});
defineEmits(['update:modelValue']);
</script>

<style scoped>
.segmented {
  display: inline-flex;
  padding: 3px;
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: var(--r-md);
  gap: 2px;
}
.seg__item {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  border: none;
  background: transparent;
  color: var(--text-muted);
  border-radius: calc(var(--r-md) - 3px);
  padding: 5px 10px;
  font-size: var(--fs-sm);
  font-weight: 600;
  transition: background var(--motion) var(--ease), color var(--motion) var(--ease);
  white-space: nowrap;
}
.seg--sm .seg__item { padding: 3px 8px; font-size: var(--fs-xs); }
.seg__item:hover { color: var(--text); }
.seg__item.active {
  background: var(--surface);
  color: var(--brand-strong);
  box-shadow: var(--shadow-sm);
}
</style>
