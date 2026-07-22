<template>
  <!--
    D4 (2026-07-22, Phase D): Reusable StatCard component.
    Merges three naming variants (stat-card / metric-card / summary-card)
    into one component. The root class "stat-card" is preserved so
    App.vue's light-theme override (.light-theme .stat-card) continues
    to apply without any global CSS changes.

    Layouts:
      - With icon   → icon + info (horizontal, Overview/Monitor pattern)
      - Without icon → value + label only (Cost/Logs pattern)
      - centered=true → text-align:center (Logs pattern)

    Variants (border-left color):
      - default → transparent (or accent if provided)
      - success/fail/warn/info → themed colors
  -->
  <div
    class="stat-card"
    :class="[variant, { 'has-accent': accent, 'centered': centered }]"
    :style="cardStyle"
  >
    <div v-if="icon || $slots.icon" class="stat-icon" :style="{ background: iconBg }">
      <slot name="icon">{{ icon }}</slot>
    </div>
    <div class="stat-info">
      <span class="stat-value"><slot name="value">{{ value }}</slot></span>
      <span class="stat-label">{{ label }}</span>
    </div>
    <div v-if="sparkline || $slots.sparkline" class="sparkline">
      <slot name="sparkline">
        <svg v-if="sparkline" viewBox="0 0 100 30" preserveAspectRatio="none">
          <polyline :points="sparkline" fill="none" :stroke="sparklineColor" stroke-width="1.5" />
        </svg>
      </slot>
    </div>
    <div v-if="$slots.footer" class="stat-footer"><slot name="footer" /></div>
  </div>
</template>

<script setup>
import { computed } from 'vue';

// Note: defineProps + computed are compiler macros / Vue APIs.
// No need for additional imports beyond `computed`.
const props = defineProps({
  /** Label text (required). */
  label: { type: String, required: true },
  /** Primary value (required). */
  value: { type: [String, Number], required: true },
  /** Emoji or single-character icon. When provided, renders the icon-left layout. */
  icon: { type: String, default: '' },
  /** Background color for the icon tile (CSS string). */
  iconBg: { type: String, default: 'rgba(59,130,246,.12)' },
  /** Left border color (CSS string like 'var(--accent)'). Overrides variant. */
  accent: { type: String, default: '' },
  /** Color variant for the left border. */
  variant: {
    type: String,
    default: 'default',
    validator: (v) => ['default', 'success', 'fail', 'warn', 'info'].includes(v),
  },
  /** When true, centers the value/label text (Logs.vue pattern). */
  centered: { type: Boolean, default: false },
  /** SVG polyline points string for the sparkline trend. */
  sparkline: { type: String, default: '' },
  /** Stroke color for the sparkline. */
  sparklineColor: { type: String, default: 'var(--accent)' },
});

const cardStyle = computed(() => {
  if (props.accent) {
    return { borderLeftColor: props.accent };
  }
  return {};
});
</script>

<style scoped>
.stat-card {
  background: var(--bg2);
  border: 1px solid var(--border);
  border-left: 3px solid transparent;
  border-radius: var(--radius);
  padding: 16px 20px;
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 140px;
  box-shadow: var(--shadow);
}

/* Variant colors (border-left). */
.stat-card.has-accent { border-left-color: var(--accent); }
.stat-card.success { border-left-color: var(--success); }
.stat-card.fail { border-left-color: var(--fail); }
.stat-card.warn { border-left-color: var(--warn); }
.stat-card.info { border-left-color: var(--accent); }

/* Centered layout (Logs.vue pattern). */
.stat-card.centered {
  text-align: center;
  flex-direction: column;
  gap: 4px;
  padding: 12px 8px;
  background: var(--bg3);
  min-width: 0;
}

/* Icon tile. */
.stat-icon {
  width: 36px;
  height: 36px;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 18px;
  flex-shrink: 0;
}

/* Info column. */
.stat-info {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.stat-card.centered .stat-info { align-items: center; }

.stat-value {
  font-size: 20px;
  font-weight: 700;
  color: var(--text);
  line-height: 1.2;
}
.stat-card.centered .stat-value { font-size: 22px; }

.stat-label {
  font-size: 12px;
  color: var(--text2);
}

/* Sparkline trend. */
.sparkline {
  width: 60px;
  height: 24px;
  margin-left: auto;
  flex-shrink: 0;
}
.sparkline svg { width: 100%; height: 100%; }

/* Footer slot. */
.stat-footer {
  margin-top: 8px;
  font-size: 11px;
  color: var(--text3);
  width: 100%;
}
</style>
