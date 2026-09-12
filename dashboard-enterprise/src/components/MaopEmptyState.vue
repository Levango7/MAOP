<template>
  <!--
    MAOP 独有空状态组件 (去AI同质化)
    区别于 Element Plus 默认空状态与 AI 模板常见的大图/emoji 风格:
    - 用 CSS 绘制简洁几何插图 (带描边的圆 + 斜线), 不依赖图片资源
    - 品牌青绿色调, 1px 描边卡片包裹, 无阴影
    - 支持自定义标题、描述、操作按钮 (通过 slot)
  -->
  <div class="maop-empty">
    <div class="maop-empty__illustration">
      <!-- CSS 绘制的简洁插图: 一个带描边的圆 + 一条斜线 -->
      <div class="maop-empty__shape"></div>
    </div>
    <h3 class="maop-empty__title">{{ title }}</h3>
    <p class="maop-empty__desc">{{ description }}</p>
    <slot name="action"></slot>
  </div>
</template>

<script setup>
/**
 * MaopEmptyState — MAOP 独有空状态组件
 * @prop {String} title       - 标题文本
 * @prop {String} description - 描述文本
 * @slot action               - 操作按钮插槽 (可选)
 */
defineProps({
  title: { type: String, default: '暂无数据' },
  description: { type: String, default: '当前还没有可展示的内容' },
});
</script>

<style scoped>
/* MAOP 独有空状态: 1px 描边卡片包裹, 无阴影, 居中布局 */
.maop-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
  padding: var(--sp-8) var(--sp-6);
  gap: var(--sp-3);
  /* 1px 描边卡片, 不用阴影 —— JetBrains 式层级语言 */
  border: 1px solid var(--border);
  border-radius: var(--r-lg);
  background: var(--surface);
}

/* 插图容器: 固定尺寸, 居中放置 CSS 绘制的几何图形 */
.maop-empty__illustration {
  display: grid;
  place-items: center;
  width: 72px;
  height: 72px;
  margin-bottom: var(--sp-2);
}

/* CSS 绘制简洁插图: 一个带描边的圆 + 一条斜线
 * 用伪元素组合: 主体为圆形 (::before), 斜线为 ::after
 * 品牌青绿色描边, 不填充 —— 线条插画风, 区别于 AI 模板的实心图标 */
.maop-empty__shape {
  position: relative;
  width: 48px;
  height: 48px;
}
/* 圆形: 品牌色描边, 透明填充 */
.maop-empty__shape::before {
  content: '';
  position: absolute;
  inset: 0;
  border: 2px solid var(--brand);
  border-radius: 50%;
  opacity: 0.7;
}
/* 斜线: 从左下到右上的对角线, 品牌色, 与圆形成"搜索无果"的视觉隐喻 */
.maop-empty__shape::after {
  content: '';
  position: absolute;
  left: -6px;
  bottom: -6px;
  width: 60px;
  height: 2px;
  background: var(--brand-strong);
  transform: rotate(-45deg);
  transform-origin: left center;
  border-radius: 1px;
  opacity: 0.5;
}

/* 标题: 中等字重, 主文字色 */
.maop-empty__title {
  font-size: var(--fs-md);
  font-weight: 600;
  color: var(--text);
  letter-spacing: -0.01em;
}

/* 描述: 次要文字色, 限制最大宽度保持可读性 */
.maop-empty__desc {
  font-size: var(--fs-sm);
  color: var(--text-muted);
  max-width: 320px;
  line-height: 1.55;
}
</style>