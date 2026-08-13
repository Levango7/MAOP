<template>
  <div
    class="segmented"
    :class="['seg--' + size, { 'seg--equal': equal }]"
    role="radiogroup"
    @keydown.left.prevent="move(-1)"
    @keydown.right.prevent="move(1)"
    @keydown.up.prevent="move(-1)"
    @keydown.down.prevent="move(1)"
    @keydown.home.prevent="moveTo(0)"
    @keydown.end.prevent="moveTo(options.length - 1)"
  >
    <button
      v-for="opt in options"
      :key="opt.value"
      type="button"
      class="seg__item"
      :class="{ active: opt.value === modelValue }"
      role="radio"
      :aria-checked="opt.value === modelValue"
      :tabindex="opt.value === modelValue ? 0 : -1"
      :aria-label="opt.label || opt.value"
      @click="$emit('update:modelValue', opt.value)"
    >
      <AppIcon v-if="opt.icon" :name="opt.icon" :size="14" aria-hidden="true" />
      <span v-if="opt.label">{{ opt.label }}</span>
    </button>
  </div>
</template>

<script setup>
import AppIcon from './AppIcon.vue';
const props = defineProps({
  modelValue: { type: [String, Number], default: '' },
  options: { type: Array, required: true }, // [{ value, label?, icon? }]
  size: { type: String, default: 'md' }, // sm | md
  equal: { type: Boolean, default: false }, // equal-width buttons (language-independent)
});
const emit = defineEmits(['update:modelValue']);

// roving tabindex: 箭头键在选项间移动并选中
function move(delta) {
  const idx = props.options.findIndex((o) => o.value === props.modelValue);
  if (idx < 0) return;
  const next = (idx + delta + props.options.length) % props.options.length;
  emit('update:modelValue', props.options[next].value);
}
function moveTo(i) {
  if (i < 0 || i >= props.options.length) return;
  emit('update:modelValue', props.options[i].value);
}
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
  justify-content: center;
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
/* Equal-width mode: all buttons share the same width regardless of label length */
.seg--equal { display: inline-flex; }
.seg--equal .seg__item { flex: 1 1 0; min-width: 0; }
</style>
