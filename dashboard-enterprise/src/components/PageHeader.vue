<template>
  <header class="page-header">
    <!-- 左：页面图标 + 标题 -->
    <div class="page-header__main">
      <span v-if="iconName" class="page-header__icon">
        <AppIcon :name="iconName" :size="22" />
      </span>
      <div class="page-header__text">
        <div class="page-header__titlerow">
          <h1 class="page-header__title">{{ titleText }}</h1>
          <span class="page-header__badges"><slot name="badges" /></span>
        </div>
        <p v-if="subtitleText" class="page-header__sub">{{ subtitleText }}</p>
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
// 路由容错: 单元测试或未挂 router 的环境下, route 可能为 undefined
const meta = (route && route.path ? getPageMeta(route.path) : null) || {};

const iconName = computed(() => props.icon || meta.icon || '');
const titleText = computed(() => props.title || (meta.label ? t(meta.label) : ''));
const subtitleText = computed(() => props.subtitle || (meta.subtitle ? t(meta.subtitle) : ''));
</script>

<style>
/* 页面级标题栏 — 2026-08-12 精修,对齐"workbench"设计语言
 * - margin-bottom: 16px(--sp-3 → --sp-4),与 .section/.card 节奏一致
 * - 去掉渐变 hairline / 左侧品牌装饰条 / 图标渐变徽章+内发光
 * - hover 无位移/无阴影 — 静止感
 * - 图标回归 34px 单色块,与 TopBar logo 同一规格 */
.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--sp-4);
  margin-bottom: var(--sp-4);
  flex-wrap: wrap;
  padding: var(--sp-3) var(--sp-4);
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r-lg);
  position: relative;
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
  width: 34px;
  height: 34px;
  flex-shrink: 0;
  border-radius: var(--r-md);
  background: var(--brand);
  color: var(--brand-contrast);
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
  font-weight: 600;
  line-height: 1.25;
  letter-spacing: -0.012em;
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
  .page-header { gap: var(--sp-3); padding: var(--sp-3) var(--sp-4); }
}

@media (max-width: 700px) {
  .page-header__actions { width: 100%; justify-content: flex-start; }
}
</style>
