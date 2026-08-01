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
/* 页面级标题栏 — 只保留页面标题+操作槽，全局元素已迁移到 TopBar
 * 视觉层次：card-sheen + 顶部 hairline + 左侧装饰条 + brand 图标徽章 */
.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--sp-4);
  margin-bottom: var(--sp-3);
  flex-wrap: wrap;
  padding: var(--sp-3) var(--sp-5);
  background: var(--card-sheen), var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r-lg);
  box-shadow: var(--shadow-sm);
  position: relative;
  overflow: hidden;
}
/* 顶部 hairline 渐变线 */
.page-header::before {
  content: '';
  position: absolute;
  top: 0; left: 0; right: 0; height: 1px;
  background: linear-gradient(90deg,
    transparent 0%,
    var(--card-hairline) 15%,
    var(--brand-faint) 50%,
    var(--card-hairline) 85%,
    transparent 100%);
  pointer-events: none;
  border-radius: var(--r-lg) var(--r-lg) 0 0;
}
/* 左侧装饰条，强化页面身份 */
.page-header::after {
  content: '';
  position: absolute;
  left: 0; top: 20%; bottom: 20%;
  width: 3px;
  background: linear-gradient(180deg, var(--brand-strong), var(--brand));
  border-radius: 0 3px 3px 0;
  opacity: .7;
  pointer-events: none;
}
.page-header__main {
  display: flex;
  align-items: center;
  gap: var(--sp-4);
  min-width: 0;
  padding-left: 4px;
}
.page-header__icon {
  display: grid;
  place-items: center;
  width: 42px;
  height: 42px;
  flex-shrink: 0;
  border-radius: var(--r-md);
  background: linear-gradient(135deg, var(--brand-soft), var(--brand-faint));
  color: var(--brand-strong);
  border: 1px solid var(--brand-faint);
  box-shadow: inset 0 1px 0 var(--card-hairline);
  position: relative;
}
.page-header__icon::after {
  content: '';
  position: absolute;
  inset: 0;
  border-radius: inherit;
  background: linear-gradient(180deg, rgba(255, 255, 255, .05), transparent 50%);
  pointer-events: none;
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
  letter-spacing: -0.018em;
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
  margin: 5px 0 0;
  line-height: 1.45;
  letter-spacing: .005em;
}

.page-header__actions {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  flex-wrap: wrap;
  flex-shrink: 0;
}

@media (max-width: 900px) {
  .page-header { gap: var(--sp-3); padding: var(--sp-4) var(--sp-5); }
  .page-header__icon { width: 38px; height: 38px; }
}

@media (max-width: 700px) {
  .page-header__actions { width: 100%; justify-content: flex-start; }
  .page-header::after { display: none; }
}
</style>
