/**
 * chartOptions — 全局 chart.js 交互规范(迭代 C4)。
 *
 * 单仓唯一事实源: 所有时序图的 hover 行为统一, 不再逐页写死。
 *
 * 约定(写入前端 style-guide):
 *   - interaction.mode = 'index' + intersect=false → 鼠标扫过即显示
 *     当前索引对应的所有数据集 tooltip, 不要求精确点在线上
 *   - pointHoverRadius = 5(无 hover 时为 0, 保持线条干净)
 *   - tooltip 跟随激活索引高亮 → 用 hooks 标注
 *
 * 用法:
 *   import { baseLineOptions } from '../composables/chartOptions.js';
 *   const options = baseLineOptions({ muted, grid: chartGridColor() });
 */

import { cssVar } from './chartTokens.js';

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

function cssVarRounded(fallback) {
  try {
    const v = getComputedStyle(document.documentElement).getPropertyValue('--r-md').trim();
    return v ? parseInt(v, 10) : fallback;
  } catch { return fallback; }
}