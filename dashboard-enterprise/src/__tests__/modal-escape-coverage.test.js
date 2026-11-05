import { describe, it, expect } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';

/**
 * 「v-modal-a11y 弹窗没有 Esc 关闭路径」的守卫（前端静默失败第六类）。
 *
 * 架构事实（2026-11-05 实测，见 directives/modalA11y.js 修订后的说明）：
 *   - 指令只打 role/aria-modal/data-modal-root/tabindex 四个标记，
 *     不处理键盘、不管理焦点（旧注释声称做的焦点定向/还原从未实现）。
 *   - App.vue 的 onGlobalEsc 选中最后一个 [data-modal-root="true"]，
 *     在其上派发冒泡的 CustomEvent('modal:escape')。
 *   - 因此每个 overlay 必须自己用 @modal:escape 接住，否则按 Esc 毫无反应：
 *     没有报错、没有日志、没有网络请求，纯静默失败。
 *
 * 修复前实测：50 个 v-modal-a11y 标签中 8 个没有 @modal:escape；
 * 其中 Run.vue 用局部 @keydown.escape 处理（焦点依赖，另案），真缺口 7 个。
 * 修复方式照抄仓内既有写法：@click.self 与 @modal:escape 用同一个关闭表达式。
 */

const SRC = path.resolve(process.cwd(), 'src');
const SCAN_DIRS = ['views', 'components'];
// 只取开标签；[^>] 允许跨行属性。与 audit_modal_escape.py 的判据一致。
const TAG = /<([a-zA-Z][\w-]*)\b[^>]*?\bv-modal-a11y\b[^>]*?>/g;
// 局部 Esc 另案白名单：必须同时满足「文件名在白名单」且「标签里真有
// @keydown.escape」，否则不能豁免；数量在主用例里精确断言，防止白名单膨胀。
const LOCAL_ESC_EXEMPT = new Set(['Run.vue']);

function collectVueFiles() {
  const files = [];
  for (const dir of SCAN_DIRS) {
    const abs = path.join(SRC, dir);
    if (!fs.existsSync(abs)) continue;
    for (const name of fs.readdirSync(abs)) {
      if (name.endsWith('.vue')) files.push({ name, abs: path.join(abs, name) });
    }
  }
  return files;
}

function findTags(text, file) {
  const tags = [];
  for (const m of text.matchAll(TAG)) {
    tags.push({ file, line: text.slice(0, m.index).split('\n').length, tag: m[0] });
  }
  return tags;
}

function classify(tags) {
  const missing = [];
  const exempted = [];
  let withGlobal = 0;
  for (const t of tags) {
    if (t.tag.includes('@modal:escape')) {
      withGlobal += 1;
      continue;
    }
    if (LOCAL_ESC_EXEMPT.has(t.file) && t.tag.includes('@keydown.escape')) {
      exempted.push(t);
      continue;
    }
    missing.push(t);
  }
  return { missing, exempted, withGlobal };
}

function scanCorpus() {
  return collectVueFiles().flatMap((f) =>
    findTags(fs.readFileSync(f.abs, 'utf-8'), f.name),
  );
}

describe('v-modal-a11y overlays must have an Escape close path', () => {
  it('detector self-check: bad / good / exempted / non-exempted local Esc', () => {
    const bad = findTags('<div v-modal-a11y @click.self="close">', 'Bad.vue');
    expect(bad).toHaveLength(1);
    expect(classify(bad).missing).toHaveLength(1);

    const good = findTags(
      '<div v-modal-a11y @click.self="close" @modal:escape="close">',
      'Good.vue',
    );
    expect(classify(good).missing).toHaveLength(0);

    const exempt = findTags('<div v-modal-a11y @keydown.escape="close">', 'Run.vue');
    expect(classify(exempt).exempted).toHaveLength(1);
    expect(classify(exempt).missing).toHaveLength(0);

    // 局部 @keydown.escape 不能成为任意文件的通行证。
    const other = findTags('<div v-modal-a11y @keydown.escape="close">', 'Other.vue');
    expect(classify(other).missing).toHaveLength(1);
  });

  it('scans a real corpus (guards against a vacuously green test)', () => {
    const tags = scanCorpus();
    expect(tags.length).toBeGreaterThanOrEqual(45);
    const { withGlobal, exempted } = classify(tags);
    expect(withGlobal).toBeGreaterThanOrEqual(45);
    expect(exempted.map((t) => t.file)).toEqual(['Run.vue']);
  });

  it('every v-modal-a11y overlay listens for modal:escape (except the declared local-Esc case)', () => {
    const { missing } = classify(scanCorpus());
    expect(missing.map((t) => `${t.file}:${t.line}`)).toEqual([]);
  });
});
