<template>
  <div class="stat" :class="{ 'is-loading': loading, 'is-accent': accent }" :style="accent ? { '--accent': accent } : null">
    <div v-if="icon" class="stat__icon" :style="{ background: toneSoft, color: toneColor }">
      <AppIcon :name="icon" :size="18" />
    </div>
    <div class="stat__body">
      <div class="stat__label">{{ label }}</div>
      <template v-if="loading">
        <Skeleton width="72%" height="22px" />
      </template>
      <template v-else>
        <div class="stat__main-row">
          <div class="stat__value">
            {{ value }}<span v-if="unit" class="stat__unit">{{ unit }}</span>
          </div>
          <div v-if="(yoy !== null && yoy !== undefined) || (mom !== null && mom !== undefined)" class="stat__trends">
            <span v-if="yoy !== null && yoy !== undefined" class="trend" :class="trendClass(yoy)">
              <AppIcon :name="yoy >= 0 ? 'arrow-up' : 'arrow-down'" :size="10" /> {{ yoyLabel }} {{ Math.abs(yoy) }}{{ trendSuffix }}
            </span>
            <span v-if="mom !== null && mom !== undefined" class="trend" :class="trendClass(mom)">
              <AppIcon :name="mom >= 0 ? 'arrow-up' : 'arrow-down'" :size="10" /> {{ momLabel }} {{ Math.abs(mom) }}{{ trendSuffix }}
            </span>
          </div>
        </div>
        <div v-if="delta !== null && delta !== undefined" class="stat__delta" :class="deltaClass">
          <AppIcon :name="delta >= 0 ? 'chevron-right' : 'chevron-right'" :size="11" class="stat__delta-arrow" />
          {{ Math.abs(delta) }}{{ deltaSuffix }}
        </div>
      </template>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue';
import AppIcon from './AppIcon.vue';
import Skeleton from './Skeleton.vue';

const props = defineProps({
  label: { type: String, required: true },
  value: { type: [String, Number], default: '—' },
  unit: { type: String, default: '' },
  delta: { type: Number, default: null },
  deltaSuffix: { type: String, default: '%' },
  yoy: { type: Number, default: null }, // year-over-year change (%)
  mom: { type: Number, default: null }, // month-over-month change (%)
  yoyLabel: { type: String, default: 'YoY' },
  momLabel: { type: String, default: 'MoM' },
  trendSuffix: { type: String, default: '%' },
  icon: { type: String, default: '' },
  tone: { type: String, default: 'brand' }, // brand|success|warn|fail|info|neutral
  accent: { type: String, default: '' }, // optional explicit left-border color (dedup palette)
  loading: { type: Boolean, default: false },
});

const TONE = {
  brand:   { soft: 'var(--brand-soft)',     color: 'var(--brand-strong)' },
  success: { soft: 'var(--success-soft)',    color: 'var(--success)' },
  warn:    { soft: 'var(--warn-soft)',       color: 'var(--warn)' },
  fail:    { soft: 'var(--fail-soft)',        color: 'var(--fail)' },
  info:    { soft: 'var(--info-soft)',       color: 'var(--info, #4cc2ff)' },
  neutral: { soft: 'var(--surface-2)',        color: 'var(--text-muted)' },
};
const toneSoft = computed(() => (TONE[props.tone] || TONE.neutral).soft);
const toneColor = computed(() => (TONE[props.tone] || TONE.neutral).color);
const deltaClass = computed(() => {
  if (props.delta === null || props.delta === undefined) return '';
  if (props.delta > 0) return 'is-up';
  if (props.delta < 0) return 'is-down';
  return 'is-flat';
});
const trendClass = (v) => (v > 0 ? 'is-up' : v < 0 ? 'is-down' : 'is-flat');
</script>

<style scoped>
.stat {
  display: flex;
  align-items: center;
  gap: var(--sp-3);
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r-lg);
  padding: var(--sp-4);
  /* JB 精修: 去 sheen / hairline / 常驻阴影 — 描边与明度差承担层级 */
  transition: border-color var(--motion) var(--ease);
  position: relative;
}
/* JB 精修: hover 只改描边, 不要"飘浮"(translateY/shadow-pop 是模板感来源) */
.stat:hover { border-color: var(--border-strong); }
.stat.is-accent { border-left: 3px solid var(--accent); }
.stat.is-accent::after {
  content: "";
  position: absolute;
  left: 0; top: 0; bottom: 0;
  width: 3px;
  background: var(--accent);
  border-radius: var(--r-lg) 0 0 var(--r-lg);
}
.stat__icon {
  width: 42px; height: 42px; border-radius: var(--r-md);
  display: grid; place-items: center; flex-shrink: 0;
  box-shadow: inset 0 1px 0 var(--border-subtle);
}
.stat__body { display: flex; flex-direction: column; gap: 3px; min-width: 0; flex: 1; }
.stat__label { font-size: var(--fs-xs); color: var(--text-muted); font-weight: 600; text-transform: uppercase; letter-spacing: .05em; }
.stat__main-row { display: flex; align-items: center; justify-content: space-between; gap: var(--sp-2); }
.stat__value { font-size: var(--fs-xl); font-weight: 700; color: var(--text); line-height: 1.1; letter-spacing: -0.02em; font-variant-numeric: tabular-nums; }
.stat__unit { font-size: var(--fs-sm); font-weight: 600; color: var(--text-muted); margin-left: 3px; }
.stat__delta { display: inline-flex; align-items: center; gap: 2px; font-size: var(--fs-xs); font-weight: 600; }
.stat__delta.is-up { color: var(--success); }
.stat__delta.is-down { color: var(--fail); }
.stat__delta.is-flat { color: var(--text-faint); }
.stat__delta-arrow { transform: rotate(-90deg); }
.stat__delta.is-down .stat__delta-arrow { transform: rotate(90deg); }
.stat__trends { display: flex; flex-wrap: wrap; gap: 4px; justify-content: flex-end; }
.trend { display: inline-flex; align-items: center; gap: 2px; font-size: 10px; font-weight: 700; padding: 1px 6px; border-radius: var(--r-full); line-height: 1.4; white-space: nowrap; }
.trend.is-up { color: var(--success); background: var(--success-soft); }
.trend.is-down { color: var(--fail); background: var(--fail-soft); }
.trend.is-flat { color: var(--text-faint); background: var(--surface-2); }
</style>
