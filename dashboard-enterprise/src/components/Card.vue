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
  background: var(--card-sheen), var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r-lg);
  box-shadow: var(--shadow-sm);
  overflow: visible;
  transition: border-color var(--motion) var(--ease), box-shadow var(--motion) var(--ease), transform var(--motion) var(--ease);
}
/* 顶部 hairline 渐变线，强化卡片顶部边界 */
.card::before {
  content: "";
  position: absolute;
  top: 0; left: 0; right: 0;
  height: 1px;
  background: linear-gradient(90deg, transparent, var(--card-hairline) 15%, var(--brand-faint) 50%, var(--card-hairline) 85%, transparent);
  pointer-events: none;
  border-radius: var(--r-lg) var(--r-lg) 0 0;
}
.card:hover { border-color: var(--border-strong); box-shadow: var(--shadow-md); }
.card--clickable { cursor: pointer; }
.card--clickable:hover { transform: translateY(-2px); box-shadow: var(--shadow-pop); border-color: var(--brand); }
.card--bare .card__body { padding: 0; }
.card:not(.card--bare) .card__body { padding: var(--sp-5); }
.card__head {
  display: flex;
  align-items: center;
  gap: var(--sp-3);
  padding: var(--sp-4) var(--sp-5);
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
