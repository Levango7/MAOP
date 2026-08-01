<template>
  <header class="page-header">
    <!-- 左：页面图标 + 标题 -->
    <div class="page-header__main">
      <span class="page-header__icon" v-if="iconName">
        <AppIcon :name="iconName" :size="22" />
      </span>
      <div class="page-header__text">
        <div class="page-header__titlerow">
          <h1 class="page-header__title">{{ titleText }}</h1>
          <span class="page-header__badges"><slot name="badges" /></span>
        </div>
        <p class="page-header__sub" v-if="subtitleText">{{ subtitleText }}</p>
      </div>
    </div>

    <!-- 右：页面专属操作（刷新/导出等） -->
    <div class="page-header__actions">
      <slot />
    </div>
  </header>
</template>

<script setup>
import { computed } from 'vue';
import { useRoute } from 'vue-router';
import { useI18n } from '../i18n/index.js';
import { getPageMeta } from '../nav.js';
import AppIcon from './AppIcon.vue';

const props = defineProps({
  icon: { type: String, default: '' },
  title: { type: String, default: '' },
  subtitle: { type: String, default: '' },
});

const { t } = useI18n();
const route = useRoute();
const meta = getPageMeta(route.path) || {};

const iconName = computed(() => props.icon || meta.icon || '');
const titleText = computed(() => props.title || (meta.label ? t(meta.label) : ''));
const subtitleText = computed(() => props.subtitle || (meta.subtitle ? t(meta.subtitle) : ''));
</script>

<style>
/* 页面级标题栏 — 只保留页面标题+操作槽，全局元素已迁移到 TopBar */
.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--sp-4);
  margin-bottom: var(--sp-6);
  flex-wrap: wrap;
  padding: var(--sp-4) var(--sp-5);
  background: var(--card-sheen), var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r-lg);
  box-shadow: var(--shadow-sm);
  position: relative;
}
.page-header::before {
  content: '';
  position: absolute;
  top: 0; left: 0; right: 0; height: 1px;
  background: var(--card-hairline);
  pointer-events: none;
  border-radius: var(--r-lg) var(--r-lg) 0 0;
}
.page-header__main {
  display: flex;
  align-items: center;
  gap: var(--sp-3);
  min-width: 0;
}
.page-header__icon {
  display: grid;
  place-items: center;
  width: 38px;
  height: 38px;
  flex-shrink: 0;
  border-radius: var(--r-md);
  background: var(--brand-soft);
  color: var(--brand-strong);
  border: 1px solid var(--border-subtle);
}
.page-header__text { min-width: 0; }
.page-header__titlerow {
  display: flex;
  align-items: center;
  gap: var(--sp-3);
  flex-wrap: wrap;
}
.page-header__title {
  font-size: var(--fs-2xl, 22px);
  font-weight: 700;
  line-height: 1.2;
  letter-spacing: -0.015em;
  color: var(--text);
  margin: 0;
}
.page-header__badges {
  display: inline-flex;
  align-items: center;
  gap: var(--sp-2);
}
.page-header__sub {
  font-size: var(--fs-sm, 13px);
  color: var(--text-muted);
  margin: 4px 0 0;
  line-height: 1.4;
}

.page-header__actions {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  flex-wrap: wrap;
}

@media (max-width: 900px) {
  .page-header { gap: var(--sp-3); padding: var(--sp-3) var(--sp-4); }
}

@media (max-width: 700px) {
  .page-header__actions { width: 100%; justify-content: flex-start; }
}
</style>
