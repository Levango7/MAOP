/**
 * Chart tokens utility (2026-08-12, P0-1 fix):
 *
 * Reads CSS custom properties so chart/diagram colors follow the active
 * dark/light theme instead of hardcoding hex values. Live-read on every
 * call, so theme switches apply immediately without a remount.
 *
 * F2: 从 composables/ 移至 utils/ —— 本文件是纯工具函数，不使用 Vue 响应式 API，
 * 放在 utils/ 更符合语义。
 *
 * Typical usage:
 *   import { cssVar, cssVarAlpha } from '../utils/chartTokens.js';
 *   cssVar('--chart-1')            // "#3574f0" (current theme)
 *   cssVar('--chart-1', '#6366f1') // fallback when CSS var is empty
 *   cssVarAlpha('--chart-1', 0.12) // "#3574f01f" (hex8) or rgba()
 *
 * Keep fallbacks aligned with tokens.css — these are only a safety net when
 * someone renders a chart before stylesheets finish loading (unit tests,
 * SSR-ish edge cases).
 */
export function cssVar(name, fallback = '') {
  try {
    const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return v || fallback;
  } catch {
    return fallback;
  }
}

export function cssVarAlpha(name, alpha = 0.12) {
  const v = cssVar(name, '');
  if (!v) return `rgba(53,116,240,${alpha})`;
  if (v.startsWith('#')) {
    const hex = v.slice(1);
    if (hex.length === 6) {
      const r = parseInt(hex.slice(0, 2), 16);
      const g = parseInt(hex.slice(2, 4), 16);
      const b = parseInt(hex.slice(4, 6), 16);
      return `rgba(${r},${g},${b},${alpha})`;
    }
    if (hex.length === 3) {
      const r = parseInt(hex[0] + hex[0], 16);
      const g = parseInt(hex[1] + hex[1], 16);
      const b = parseInt(hex[2] + hex[2], 16);
      return `rgba(${r},${g},${b},${alpha})`;
    }
    if (hex.length === 8) {
      // hex8 (#RRGGBBAA) 已内含 alpha 通道，直接返回原值。
      // 设计意图: CSS 变量定义为 hex8 时，alpha 由设计师在样式表中精确设定，
      // 此时不覆盖调用者传入的 alpha 参数，保留 CSS 中的权威值。
      return v;
    }
    return v; // 其他未知格式，原样返回
  }
  // already rgb/rgba — replace trailing alpha if present
  const m = /^rgba?\(([^)]+)\)$/.exec(v);
  if (m) {
    const parts = m[1].split(',').map((s) => s.trim());
    if (parts.length >= 3) return `rgba(${parts[0]},${parts[1]},${parts[2]},${alpha})`;
  }
  return v;
}

/**
 * 读取 CSS 变量 --r-md 并转为整数（圆角半径）。
 *
 * @non-readonly
 * @param {number} fallback 当 CSS 变量不存在或 DOM 不可读时的回退值
 * @returns {number} 圆角半径（整数）
 *
 * 从 chartOptions.js 移入此文件——本函数本质上是读取 CSS 自定义属性的工具函数，
 * 与 cssVar / cssVarAlpha 同属 chartTokens 职责，集中在此便于维护。
 */
export function cssVarRounded(fallback) {
  try {
    const v = getComputedStyle(document.documentElement).getPropertyValue('--r-md').trim();
    return v ? parseInt(v, 10) : fallback;
  } catch { return fallback; }
}