<template>
  <footer class="app-footer">
    <div class="footer-inner">
      <!-- 左：品牌 + 状态 -->
      <div class="footer-brand-zone">
        <span class="footer-brand">MAOP</span>
        <span class="footer-ver">v{{ version }}</span>
        <span class="footer-status" :class="{ on: realtimeConnected }">
          <span class="footer-dot" :class="{ on: realtimeConnected }"></span>
          {{ realtimeConnected ? t('footer.online') : t('footer.offline') }}
        </span>
      </div>

      <!-- 中：快速链接（分组，参考云厂商 footer 的 link columns） -->
      <nav class="footer-links" :aria-label="t('a11y.footerNavigation')">
        <router-link to="/docs" class="footer-link">{{ t('nav.docs') }}</router-link>
        <span class="footer-sep">·</span>
        <router-link to="/monitor" class="footer-link">{{ t('nav.monitor') }}</router-link>
        <span class="footer-sep">·</span>
        <router-link to="/settings" class="footer-link">{{ t('nav.settings') }}</router-link>
      </nav>

      <!-- 右：版权 -->
      <span class="footer-copy">{{ t('footer.copyright') }}</span>
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
  margin-top: auto;
  border-top: 1px solid var(--border);
  background: var(--surface);
  padding: var(--sp-3) var(--content-pad);
  flex-shrink: 0;
}
.footer-inner {
  width: 100%;
  max-width: var(--maxw);
  margin: 0 auto;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--sp-4);
  flex-wrap: wrap;
  font-size: var(--fs-xs);
  color: var(--text-faint);
}
.footer-brand-zone { display: flex; align-items: center; gap: var(--sp-2); min-width: 0; }
.footer-brand { font-weight: 700; color: var(--brand-strong); letter-spacing: .3px; font-size: var(--fs-sm); }
.footer-ver { font-family: var(--font-mono); color: var(--text-muted); }
.footer-status { display: inline-flex; align-items: center; gap: 5px; color: var(--text-faint); }
.footer-status.on { color: var(--success); }
.footer-dot {
  width: 6px; height: 6px; border-radius: 50%;
  background: var(--text-faint); opacity: .5;
  transition: background var(--motion) var(--ease), opacity var(--motion) var(--ease);
}
.footer-dot.on { background: var(--success); opacity: 1; }

.footer-links { display: flex; align-items: center; gap: var(--sp-2); flex-wrap: wrap; }
.footer-link { color: var(--text-muted); text-decoration: none; transition: color var(--motion) var(--ease); }
.footer-link:hover { color: var(--brand-strong); }
.footer-sep { color: var(--border-strong); opacity: .6; }
.footer-copy { color: var(--text-faint); white-space: nowrap; }

@media (max-width: 899px) {
  .app-footer { padding: var(--sp-2) 16px; }
  .footer-inner { justify-content: center; gap: var(--sp-1); }
  .footer-copy { width: 100%; text-align: center; }
}
</style>
