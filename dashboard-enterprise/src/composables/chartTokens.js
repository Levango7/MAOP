/**
 * Chart tokens composable (2026-08-12, P0-1 fix):
 *
 * Reads CSS custom properties so chart/diagram colors follow the active
 * dark/light theme instead of hardcoding hex values. Live-read on every
 * call, so theme switches apply immediately without a remount.
 *
 * Typical usage:
 *   import { cssVar, cssVarAlpha } from '../composables/chartTokens.js';
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
    return v; // hex8 already contains alpha
  }
  // already rgb/rgba — replace trailing alpha if present
  const m = /^rgba?\(([^)]+)\)$/.exec(v);
  if (m) {
    const parts = m[1].split(',').map((s) => s.trim());
    if (parts.length >= 3) return `rgba(${parts[0]},${parts[1]},${parts[2]},${alpha})`;
  }
  return v;
}
