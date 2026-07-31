<template>
  <footer class="app-footer">
    <div class="footer-inner">
      <div class="footer-left">
        <span class="footer-brand">MAOP</span>
        <span class="footer-ver">v{{ version }}</span>
        <span class="footer-tag">{{ t('footer.tagline') }}</span>
      </div>

      <nav class="footer-right" aria-label="footer">
        <span class="footer-status" :class="{ on: realtimeConnected }">
          <span class="footer-dot" :class="{ on: realtimeConnected }"></span>
          {{ realtimeConnected ? t('footer.online') : t('footer.offline') }}
        </span>
        <router-link to="/settings" class="footer-link">{{ t('nav.settings') }}</router-link>
        <router-link to="/audit" class="footer-link">{{ t('nav.audit') }}</router-link>
        <router-link to="/monitor" class="footer-link">{{ t('nav.monitor') }}</router-link>
        <span class="footer-sep">·</span>
        <span class="footer-copy">{{ t('footer.copyright') }}</span>
      </nav>
    </div>
  </footer>
</template>

<script setup>
import { computed } from 'vue';
import { useRealtimeStore } from '../stores/realtime.js';
import { useI18n } from '../i18n/index.js';

defineProps({
  version: { type: String, default: 'unknown' },
});

const realtime = useRealtimeStore();
const { t } = useI18n();
const realtimeConnected = computed(() => realtime.connected);
</script>

<style scoped>
.app-footer {
  margin-top: auto; /* pin to the bottom of the content area */
  border-top: 1px solid var(--border);
  background: var(--surface);
  /* Full-width band: spans the entire content area (from the sidebar edge
     to the scrollbar), not just the centered content-shell. Horizontal
     padding matches the content shell so the footer text lines up with the
     cards' outer edge. */
  padding: var(--sp-6) var(--content-pad);
  flex-shrink: 0;
}
.footer-inner {
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: space-between; /* two balanced zones: brand ← → meta */
  gap: var(--sp-4);
  flex-wrap: wrap;
  font-size: var(--fs-sm);
  color: var(--text-faint);
}
.footer-left { display: flex; align-items: center; gap: var(--sp-2); min-width: 0; }
.footer-brand { font-weight: 700; color: var(--brand-strong); letter-spacing: .3px; }
.footer-ver { font-family: var(--font-mono); color: var(--text-muted); }
.footer-tag { color: var(--text-faint); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

.footer-right { display: flex; align-items: center; gap: var(--sp-3); flex-wrap: wrap; }
.footer-status { display: inline-flex; align-items: center; gap: 6px; color: var(--text-faint); }
.footer-status.on { color: var(--success); }
.footer-dot {
  width: 7px; height: 7px; border-radius: 50%;
  background: var(--text-faint); opacity: .5;
  transition: background var(--motion) var(--ease), opacity var(--motion) var(--ease);
}
.footer-dot.on { background: var(--success); opacity: 1; }
.footer-link { color: var(--text-muted); text-decoration: none; transition: color var(--motion) var(--ease); }
.footer-link:hover { color: var(--brand-strong); }
.footer-sep { color: var(--border-strong); }
.footer-copy { color: var(--text-faint); }

@media (max-width: 899px) {
  .app-footer { padding: var(--sp-5) 16px; }
  .footer-tag { display: none; }
  .footer-right { width: 100%; justify-content: flex-start; }
}
</style>
