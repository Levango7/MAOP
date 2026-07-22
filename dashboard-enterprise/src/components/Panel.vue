<template>
  <!--
    D4 (2026-07-22, Phase D): Reusable Panel component.
    Replaces the 10+ duplicated `.panel { … }` CSS blocks across views.
    The root class "panel" is preserved so App.vue's light-theme override
    (.light-theme .panel) continues to apply without any global CSS changes.

    Standardizes the emerging panel-header pattern (already hand-written
    in Logs.vue and Agents.vue) into a proper `actions` slot:

        <Panel title="Log Output">
          <template #actions>
            <span class="line-count">{{ count }}</span>
          </template>
          <LogContent />
        </Panel>

    When no title/actions are provided, the header is omitted and the
    panel renders as a plain padded container (VectorSearch pattern).
  -->
  <div
    class="panel"
    :class="{ 'no-shadow': !shadow }"
    :style="rootStyle"
  >
    <div
      v-if="title || $slots.title || $slots.actions"
      class="panel-header"
    >
      <h3 class="panel-title">
        <slot name="title">{{ title }}</slot>
      </h3>
      <div v-if="$slots.actions" class="panel-actions">
        <slot name="actions" />
      </div>
    </div>
    <div class="panel-body"><slot /></div>
    <div v-if="$slots.footer" class="panel-footer">
      <slot name="footer" />
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue';

const props = defineProps({
  /** Panel title. When empty and no title/actions slots, header is omitted. */
  title: { type: String, default: '' },
  /** Toggle box-shadow. Some pages (Evolve, Models, Tools) omit shadow. */
  shadow: { type: Boolean, default: true },
  /** Bottom margin (e.g., '16px' or 16). Empty string = no explicit margin. */
  marginBottom: { type: [String, Number], default: '' },
  /** Overflow-x for wide content (e.g., 'auto' for Audit tables). */
  overflow: {
    type: String,
    default: 'visible',
    validator: (v) => ['visible', 'auto', 'hidden'].includes(v),
  },
  /** Inner padding (e.g., 20, '16px', 0). Applied to the root .panel. */
  bodyPadding: { type: [String, Number], default: 20 },
});

const rootStyle = computed(() => {
  const s = {};
  if (props.marginBottom !== '') {
    s.marginBottom =
      typeof props.marginBottom === 'number'
        ? props.marginBottom + 'px'
        : props.marginBottom;
  }
  if (props.overflow !== 'visible') {
    s.overflowX = props.overflow;
  }
  s.padding =
    typeof props.bodyPadding === 'number'
      ? props.bodyPadding + 'px'
      : props.bodyPadding;
  return s;
});
</script>

<style scoped>
.panel {
  background: var(--bg2);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
}
.panel.no-shadow { box-shadow: none; }

/* Header: title + optional actions row. */
.panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 16px;
}
.panel-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--text2);
  margin: 0;
}
.panel-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: var(--text3);
}

/* Body: default slot content (no extra padding — root .panel has padding). */
.panel-body { min-width: 0; }

/* Optional footer with top border. */
.panel-footer {
  margin-top: 16px;
  padding-top: 12px;
  border-top: 1px solid var(--border);
  font-size: 12px;
  color: var(--text3);
}
</style>
