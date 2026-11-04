import { describe, it, expect } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';

/**
 * 卡片与页面底色的对比度守卫。
 *
 * 2026-10-29 用户反馈："card 的背景色 和 底布的背景色、界限之间区别最好再明显一点"。
 * 实测当时（暗色主题）：
 *   --border  #3c4048 on --surface #22242a = 1.49:1
 *   --surface #22242a on --bg      #1b1d21 = 1.09:1
 * 亮色主题：
 *   --border  #d4d8e0 on --surface #ffffff = 1.43:1
 *   --surface #ffffff on --bg      #f2f3f5 = 1.11:1
 *
 * 两个数都低于可辨识阈值，而设计注释当时写的是"层级由 1px 描边 + 明度差承担" ——
 * 依据不成立，所以卡片和底布糊在一起。
 *
 * 这个用例把下限钉住，防止有人把 --border-card 改回接近 --border 的值。
 */

const STYLES = path.resolve(process.cwd(), 'src', 'styles');

/** 从 css 文本里取变量值（只认 #rrggbb）。 */
function readVar(cssText, name) {
  const re = new RegExp(`^\\s*--${name}:\\s*(#[0-9a-fA-F]{6})\\s*;`, 'm');
  const m = cssText.match(re);
  if (!m) throw new Error(`变量 --${name} 未定义或不是 #rrggbb`);
  return m[1];
}

function hexToRgb(hex) {
  return [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255);
}

/** WCAG 2.x 相对亮度。 */
function luminance(hex) {
  const [r, g, b] = hexToRgb(hex).map((c) =>
    c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4,
  );
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/** WCAG 对比度，返回 1..21。 */
function contrast(a, b) {
  const la = luminance(a);
  const lb = luminance(b);
  const [hi, lo] = la > lb ? [la, lb] : [lb, la];
  return (hi + 0.05) / (lo + 0.05);
}

const THEMES = [
  { name: 'dark', file: 'tokens.css', minBorder: 1.8, minSurface: 1.05 },
  { name: 'light', file: 'themes.css', minBorder: 1.6, minSurface: 1.05 },
];

describe('card contrast', () => {
  for (const t of THEMES) {
    it(`${t.name}: --border-card is distinguishable from --surface`, () => {
      const css = fs.readFileSync(path.join(STYLES, t.file), 'utf-8');
      const border = readVar(css, 'border-card');
      const surface = readVar(css, 'surface');
      const ratio = contrast(border, surface);

      expect(
        ratio,
        `${t.name} 主题：--border-card ${border} on --surface ${surface} 只有 ${ratio.toFixed(2)}:1，` +
          `低于 ${t.minBorder}:1 —— 卡片边界会看不出来（修复前 --border 是 1.49/1.43:1）`,
      ).toBeGreaterThanOrEqual(t.minBorder);
    });

    it(`${t.name}: --surface is distinguishable from --bg`, () => {
      const css = fs.readFileSync(path.join(STYLES, t.file), 'utf-8');
      const surface = readVar(css, 'surface');
      const bg = readVar(css, 'bg');
      const ratio = contrast(surface, bg);

      expect(
        ratio,
        `${t.name} 主题：--surface ${surface} 与 --bg ${bg} 只有 ${ratio.toFixed(2)}:1，` +
          `低于 ${t.minSurface}:1`,
      ).toBeGreaterThanOrEqual(t.minSurface);
    });

    it(`${t.name}: hover border is perceptibly different from resting border`, () => {
      // 2026-10-29 实测踩到：--border-card(#4a4f59) 与 --border-strong(#4e545f)
      // 亮度几乎相同，而卡片 hover 原来指向 --border-strong —— 结果是 hover
      // 完全没有视觉反馈。这条断言要求两者至少差 1.2:1。
      const css = fs.readFileSync(path.join(STYLES, t.file), 'utf-8');
      const rest = readVar(css, 'border-card');
      const hover = readVar(css, 'border-card-hover');
      const ratio = contrast(rest, hover);

      expect(
        ratio,
        `${t.name} 主题：卡片常态描边 ${rest} 与 hover 描边 ${hover} 只差 ${ratio.toFixed(2)}:1，` +
          `hover 会看起来没有反应`,
      ).toBeGreaterThanOrEqual(1.2);
    });
  }

  it('computes the known baseline ratios (guards the formula itself)', () => {
    // 黑/白 = 21:1；同色 = 1:1。若公式写错，上面几条会恒真/恒假而无人察觉。
    expect(contrast('#000000', '#ffffff')).toBeCloseTo(21, 1);
    expect(contrast('#22242a', '#22242a')).toBeCloseTo(1, 5);
  });

  it('--shadow-surface is defined in both themes', () => {
    for (const t of THEMES) {
      const css = fs.readFileSync(path.join(STYLES, t.file), 'utf-8');
      expect(
        /^\s*--shadow-surface:/m.test(css),
        `${t.name} 主题缺 --shadow-surface，卡片会回落到"只有描边"的状态`,
      ).toBe(true);
    }
  });
});
