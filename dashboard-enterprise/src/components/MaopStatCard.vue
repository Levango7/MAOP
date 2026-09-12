<template>
  <!--
    MAOP 独有统计卡片 (去AI同质化)
    区别于 Element Plus 的 el-card 与 AI 模板常见的阴影卡片:
    - 标题 + 大数字 + 趋势箭头
    - 1px 描边, 不用阴影 —— JetBrains New UI 无飘浮层级语言
    - 悬浮仅描边加深, 不抬升不阴影 (与 StatCard 风格一致)
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
/* MAOP 独有统计卡片: 1px 描边, 无阴影, 无飘浮
 * 悬浮仅描边加深 —— 与 StatCard 风格一致 (JetBrains New UI) */
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
  /* 描边过渡 —— JetBrains New UI 无飘浮设计语言 */
  transition: border-color var(--motion-normal) var(--ease);
}

/* 悬浮: 仅描边加深, 不抬升不阴影 —— 与 StatCard 一致 */
.maop-stat:hover {
  border-color: var(--border-strong);
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
  font-size: var(--fs-xl);
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
  gap: var(--sp-1);
  font-size: var(--fs-xs);
  font-weight: 700;
  padding: var(--sp-1) var(--sp-2);
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