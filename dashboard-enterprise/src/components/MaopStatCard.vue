<template>
  <!--
    MAOP 独有统计卡片 (去AI同质化)
    区别于 Element Plus 的 el-card 与 AI 模板常见的阴影卡片:
    - 顶部 1px 品牌色线条 (MAOP 独有标识, 用伪元素绘制)
    - 标题 + 大数字 + 趋势箭头
    - 悬浮时微抬效果 (maop-card-hover, translateY -2px)
    - 1px 描边, 不用阴影 —— JetBrains 式层级语言
    - 暗色/亮色主题自动适配 (通过 CSS 变量)
  -->
  <div class="maop-stat" :class="{ 'maop-stat--up': isUp, 'maop-stat--down': isDown }">
    <!-- 标题行: 标题 + 可选图标 -->
    <div class="maop-stat__header">
      <span class="maop-stat__label">{{ label }}</span>
      <span v-if="unit" class="maop-stat__unit">{{ unit }}</span>
    </div>
    <!-- 数值行: 大数字 + 趋势箭头 -->
    <div class="maop-stat__body">
      <div class="maop-stat__value">{{ value }}</div>
      <div v-if="trend !== null && trend !== undefined" class="maop-stat__trend">
        <span class="maop-stat__arrow" aria-hidden="true">{{ isUp ? '↑' : isDown ? '↓' : '→' }}</span>
        <span class="maop-stat__trend-value">{{ Math.abs(trend) }}{{ trendSuffix }}</span>
      </div>
    </div>
    <!-- 可选副标题/描述 -->
    <div v-if="subtitle" class="maop-stat__subtitle">{{ subtitle }}</div>
  </div>
</template>

<script setup>
/**
 * MaopStatCard — MAOP 独有统计卡片组件
 * @prop {String|Number} label       - 卡片标题 (指标名称)
 * @prop {String|Number} value       - 主数值
 * @prop {String}        unit        - 单位 (可选, 如 "%" / "ms")
 * @prop {Number}        trend       - 趋势百分比 (正=上升, 负=下降, 0=持平)
 * @prop {String}        trendSuffix - 趋势值后缀 (默认 "%")
 * @prop {String}        subtitle    - 副标题/描述 (可选)
 */
import { computed } from 'vue';

const props = defineProps({
  label: { type: [String, Number], required: true },
  value: { type: [String, Number], default: '—' },
  unit: { type: String, default: '' },
  trend: { type: Number, default: null },
  trendSuffix: { type: String, default: '%' },
  subtitle: { type: String, default: '' },
});

// 趋势方向计算 (用于颜色与箭头)
const isUp = computed(() => props.trend !== null && props.trend !== undefined && props.trend > 0);
const isDown = computed(() => props.trend !== null && props.trend !== undefined && props.trend < 0);
</script>

<style scoped>
/* MAOP 独有统计卡片: 1px 描边, 无阴影, 顶部品牌色线条
 * 悬浮时微抬 (maop-card-hover 效果内联实现, 避免依赖外部 CSS) */
.maop-stat {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: var(--sp-2);
  padding: var(--sp-4);
  /* 1px 描边, 不用阴影 —— JetBrains 式层级语言 */
  border: 1px solid var(--border);
  border-radius: var(--r-lg);
  background: var(--surface);
  /* 悬浮微抬效果 (MAOP 独有, 非 scale 缩放) */
  transition: transform 0.2s cubic-bezier(0.4, 0, 0.2, 1),
              border-color 0.2s cubic-bezier(0.4, 0, 0.2, 1),
              box-shadow 0.2s cubic-bezier(0.4, 0, 0.2, 1);
}

/* MAOP 独有标识: 顶部 1px 品牌色线条
 * 用伪元素绘制, 区别于 AI 模板的左边框色条 */
.maop-stat::before {
  content: '';
  position: absolute;
  top: 0;
  left: var(--sp-4);
  right: var(--sp-4);
  height: 1px;
  background: linear-gradient(90deg, var(--brand), var(--brand-strong));
  opacity: 0.6;
}

/* 悬浮微抬: translateY -2px + 柔和阴影 + 描边加深 */
.maop-stat:hover {
  transform: translateY(-2px);
  border-color: var(--border-strong);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.12);
}

/* 标题行: 标题 + 单位 */
.maop-stat__header {
  display: flex;
  align-items: baseline;
  gap: var(--sp-1);
}
.maop-stat__label {
  font-size: var(--fs-xs);
  font-weight: 600;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}
.maop-stat__unit {
  font-size: var(--fs-2xs);
  color: var(--text-faint);
  font-weight: 500;
}

/* 数值行: 大数字 + 趋势 */
.maop-stat__body {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--sp-2);
}
.maop-stat__value {
  font-size: var(--fs-2xl);
  font-weight: 700;
  color: var(--text);
  line-height: 1.1;
  letter-spacing: -0.02em;
  font-variant-numeric: tabular-nums;
}

/* 趋势指示: 箭头 + 百分比, 语义色 (上升=成功绿, 下降=失败红) */
.maop-stat__trend {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  font-size: var(--fs-xs);
  font-weight: 700;
  padding: 2px 6px;
  border-radius: var(--r-full);
  background: var(--surface-2);
  color: var(--text-faint);
}
.maop-stat--up .maop-stat__trend {
  color: var(--success);
  background: var(--success-soft);
}
.maop-stat--down .maop-stat__trend {
  color: var(--fail);
  background: var(--fail-soft);
}
.maop-stat__arrow {
  font-size: var(--fs-sm);
  line-height: 1;
}

/* 副标题: 次要信息 */
.maop-stat__subtitle {
  font-size: var(--fs-xs);
  color: var(--text-faint);
  line-height: 1.4;
}
</style>