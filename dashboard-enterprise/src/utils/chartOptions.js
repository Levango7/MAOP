/**
 * chartOptions — 全局 chart.js 交互规范(迭代 C4)。
 *
 * 单仓唯一事实源: 所有时序图的 hover 行为统一, 不再逐页写死。
 *
 * F2: 从 composables/ 移至 utils/ —— 本文件是纯工具函数，不使用 Vue 响应式 API，
 * 放在 utils/ 更符合语义。
 *
 * 约定(写入前端 style-guide):
 *   - interaction.mode = 'index' + intersect=false → 鼠标扫过即显示
 *     当前索引对应的所有数据集 tooltip, 不要求精确点在线上
 *   - pointHoverRadius = 5(无 hover 时为 0, 保持线条干净)
 *   - tooltip 跟随激活索引高亮 → 用 hooks 标注
 *
 * 用法:
 *   import { baseLineOptions } from '../utils/chartOptions.js';
 *   const options = baseLineOptions({ muted, grid: chartGridColor() });
 */

import { cssVar, cssVarRounded } from './chartTokens.js';

/**
 * 构造 chart.js 折线图的全局基础 options。
 *
 * @non-readonly
 * @param {object} [opts] 配置项
 * @param {string} [opts.muted] 弱化文字颜色（默认读取 CSS 变量 --text-muted）
 * @param {string} [opts.grid] 网格线颜色（默认读取 CSS 变量 --border-subtle）
 * @param {number} [opts.maxTicks=8] X 轴最大刻度数
 * @param {boolean} [opts.legendVisible=true] 是否显示图例
 * @returns {object} chart.js options 对象
 *
 * 注意: 此函数通过 cssVar 和 cssVarRounded 读取 DOM CSS 变量，非纯函数。
 * 在 SSR 或无 DOM 环境下会使用 fallback 默认值。
 */
export function baseLineOptions({ muted, grid, maxTicks = 8, legendVisible = true } = {}) {
  const mutedColor = muted || cssVar('--text-muted', '#9aa3b2');
  const gridColor = grid || cssVar('--border-subtle', 'rgba(163,173,190,.15)');
  return {
    responsive: true,
    maintainAspectRatio: false,
    interaction: { mode: 'index', intersect: false },
    plugins: {
      legend: legendVisible
        ? { position: 'top', labels: { boxWidth: 12, font: { size: 11 }, color: mutedColor } }
        : { display: false },
      tooltip: {
        enabled: true,
        backgroundColor: cssVar('--surface-2', '#292b32'),
        borderColor: cssVar('--border', '#3c4048'),
        borderWidth: 1,
        titleColor: cssVar('--text', '#e8eaf0'),
        bodyColor: cssVar('--text-muted', '#9aa3b2'),
        padding: 10,
        cornerRadius: cssVarRounded(8),
        displayColors: true,
        boxPadding: 4,
      },
    },
    scales: {
      x: {
        grid: { display: false },
        ticks: { color: mutedColor, maxRotation: 0, autoSkip: true, maxTicksLimit: maxTicks },
      },
      y: {
        grid: { color: gridColor },
        ticks: { color: mutedColor },
        beginAtZero: true,
      },
    },
  };
}