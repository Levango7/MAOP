<template>
  <footer class="app-footer">
    <div class="app-footer__inner">
      <!-- 左：品牌 + 状态 -->
      <div class="app-footer__brand-zone">
        <span class="app-footer__brand">{{ t('topbar.systemName') }}</span>
        <span class="app-footer__ver">v{{ version }}</span>
        <span class="app-footer__status" :class="{ on: realtimeConnected }">
          <span class="app-footer__dot" :class="{ on: realtimeConnected }"></span>
          {{ realtimeConnected ? t('footer.online') : t('footer.offline') }}
        </span>
      </div>

      <!-- 中：快速链接（分组，参考云厂商 footer 的 link columns） -->
      <nav class="app-footer__links" :aria-label="t('a11y.footerNavigation')">
        <router-link to="/docs" class="app-footer__link">{{ t('nav.docs') }}</router-link>
        <span class="app-footer__sep">·</span>
        <router-link to="/monitor" class="app-footer__link">{{ t('nav.monitor') }}</router-link>
        <span class="app-footer__sep">·</span>
        <router-link to="/settings" class="app-footer__link">{{ t('nav.settings') }}</router-link>
      </nav>

      <!-- 右：版权 -->
      <span class="app-footer__copy">{{ t('footer.copyright') }}</span>
    </div>
  </footer>
</template>

<script setup>
import { computed } from 'vue';
import { useRealtimeStore } from '../stores/realtime.js';
import { useI18n } from '../i18n';

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
.app-footer__inner {
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
.app-footer__brand-zone { display: flex; align-items: center; gap: var(--sp-2); min-width: 0; }
.app-footer__brand { font-weight: 700; color: var(--brand-strong); letter-spacing: .3px; font-size: var(--fs-sm); }
.app-footer__ver { font-family: var(--font-mono); color: var(--text-muted); }
.app-footer__status { display: inline-flex; align-items: center; gap: 5px; color: var(--text-faint); }
.app-footer__status.on { color: var(--success); }
.app-footer__dot {
  width: 6px; height: 6px; border-radius: var(--r-full);
  background: var(--text-faint); opacity: .5;
  transition: background var(--motion) var(--ease), opacity var(--motion) var(--ease);
}
.app-footer__dot.on { background: var(--success); opacity: 1; }

.app-footer__links { display: flex; align-items: center; gap: var(--sp-2); flex-wrap: wrap; }
.app-footer__link { color: var(--text-muted); text-decoration: none; transition: color var(--motion) var(--ease); }
.app-footer__link:hover { color: var(--brand-strong); }
.app-footer__sep { color: var(--border-strong); opacity: .6; }
.app-footer__copy { color: var(--text-faint); white-space: nowrap; }

@media (max-width: 900px) {  /* F7: 断点统一 899px → 900px，与 pages.css / 项目其他组件一致 */
  .app-footer { padding: var(--sp-2) 16px; }
  .app-footer__inner { justify-content: center; gap: var(--sp-1); }
  .app-footer__copy { width: 100%; text-align: center; }
}
</style>
