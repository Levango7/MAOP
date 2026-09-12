<template>
  <!--
    MAOP 独有品牌标识 (去AI同质化)
    区别于 AI 模板常见的立方体/圆形 Logo:
    - "M" 字母 + 青绿色渐变 + 1px 描边
    - 独特的折叠纸效果 (用 CSS clip-path 切出折角)
    - 纯 CSS 绘制, 不依赖图片资源
    - 自动适配暗色/亮色主题 (通过 CSS 变量)
  -->
  <div class="maop-logo" :class="`maop-logo--${size}`" role="img" :aria-label="ariaLabel">
    <!-- 折叠纸背景层: 用 clip-path 切出右上折角, 品牌色渐变填充 -->
    <div class="maop-logo__paper"></div>
    <!-- "M" 字母层: 居中显示, 品牌对比色 -->
    <span class="maop-logo__letter">M</span>
  </div>
</template>

<script setup>
/**
 * MaopLogo — MAOP 独有品牌标识组件
 * @prop {String} size      - 尺寸: small(24px) / medium(32px) / large(48px)
 * @prop {String} ariaLabel - 无障碍标签 (默认 "MAOP")
 */
defineProps({
  size: {
    type: String,
    default: 'medium',
    validator: (v) => ['small', 'medium', 'large'].includes(v),
  },
  ariaLabel: { type: String, default: 'MAOP' },
});
</script>

<style scoped>
/* MAOP 独有 Logo: 折叠纸效果 + "M" 字母
 * 用 CSS clip-path 切出右上折角, 模拟"折纸"质感
 * 区别于 AI 模板的立方体/圆形/渐变球 */
.maop-logo {
  position: relative;
  display: grid;
  place-items: center;
  border: 1px solid var(--brand);
  border-radius: var(--r-sm);
  overflow: hidden;
  flex-shrink: 0;
}

/* 折叠纸背景层: 品牌色渐变 + clip-path 切折角 */
.maop-logo__paper {
  position: absolute;
  inset: 0;
  background: linear-gradient(135deg, var(--brand) 0%, var(--brand-strong) 100%);
  /* clip-path 切出右上角折痕 —— 折纸效果的核心 */
  clip-path: polygon(0 0, 75% 0, 100% 25%, 100% 100%, 0 100%);
}

/* "M" 字母: 居中, 品牌对比色 (白色), 加粗 */
.maop-logo__letter {
  position: relative;
  z-index: 1;
  font-family: var(--font-sans);
  font-weight: 800;
  color: var(--brand-contrast);
  line-height: 1;
  letter-spacing: -0.05em;
}

/* ── 尺寸变体 ──────────────────────────────────────────────────── */
.maop-logo--small {
  width: 24px;
  height: 24px;
}
.maop-logo--small .maop-logo__letter {
  font-size: 14px;
}

.maop-logo--medium {
  width: 32px;
  height: 32px;
}
.maop-logo--medium .maop-logo__letter {
  font-size: 18px;
}

.maop-logo--large {
  width: 48px;
  height: 48px;
}
.maop-logo--large .maop-logo__letter {
  font-size: 26px;
}
</style>