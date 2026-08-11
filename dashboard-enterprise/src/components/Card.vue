<template>
  <section
    class="card"
    :class="{ 'card--bare': !padded, 'card--clickable': clickable }"
    :style="mbStyle"
    @click="clickable ? $emit('click', $event) : null"
  >
    <header v-if="title || subtitle || $slots.title || $slots.actions" class="card__head">
      <div class="card__title">
        <AppIcon v-if="icon" :name="icon" :size="16" class="card__icon" />
        <slot name="title">
          <h3>{{ title }}</h3>
          <Badge v-if="badge" :tone="badgeTone">{{ badge }}</Badge>
        </slot>
        <p v-if="subtitle" class="card__subtitle">{{ subtitle }}</p>
      </div>
      <div class="card__actions"><slot name="actions" /></div>
    </header>
    <div class="card__body"><slot /></div>
  </section>
</template>

<script setup>
import { computed } from 'vue';
import AppIcon from './AppIcon.vue';
import Badge from './Badge.vue';

const props = defineProps({
  title: { type: String, default: '' },
  subtitle: { type: String, default: '' },
  icon: { type: String, default: '' },
  badge: { type: String, default: '' },
  badgeTone: { type: String, default: 'neutral' },
  padded: { type: Boolean, default: true },
  marginBottom: { type: [String, Number], default: '' },
  clickable: { type: Boolean, default: false },
});

defineEmits(['click']);

const mbStyle = computed(() => {
  if (!props.marginBottom) return null;
  const v = typeof props.marginBottom === 'number' ? props.marginBottom + 'px' : props.marginBottom;
  return { marginBottom: v };
});
</script>

<style scoped>
.card {
  position: relative;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r-lg);
  /* JB 精修: 卡片不再带常驻阴影 — 层级由 1px 描边 + 明度差承担 */
  overflow: visible;
  transition: border-color var(--motion) var(--ease), background var(--motion) var(--ease);
}
/* hover 只加深描边, 不再上浮/投影 — "静止感"是工具感与模板感的分水岭 */
.card:hover { border-color: var(--border-strong); }
.card--clickable { cursor: pointer; }
.card--clickable:hover { border-color: var(--brand); background: var(--surface-2); }
.card--bare .card__body { padding: 0; }
.card:not(.card--bare) .card__body { padding: var(--sp-4); }
.card__head {
  display: flex;
  align-items: center;
  gap: var(--sp-3);
  padding: var(--sp-3) var(--sp-4);
  border-bottom: 1px solid var(--border-subtle, var(--border));
  min-height: var(--row-h);
}
.card__title {
  display: flex;
  align-items: baseline;
  gap: var(--sp-2);
  font-size: var(--fs-md);
  font-weight: 600;
  color: var(--text);
  flex-wrap: wrap;
  letter-spacing: -0.005em;
}
.card__subtitle { width: 100%; font-size: var(--fs-sm); font-weight: 400; color: var(--text-muted); margin-top: 2px; }
.card__icon {
  color: var(--brand-strong);
  align-self: center;
  width: 30px; height: 30px;
  display: grid; place-items: center;
  border-radius: var(--r-sm);
  background: var(--brand-soft);
  border: 1px solid var(--brand-faint);
}
.card__actions { margin-left: auto; display: flex; align-items: center; gap: var(--sp-2); }
</style>
