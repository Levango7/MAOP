import { describe, it, expect } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';

/**
 * AppIcon 的 glyph 计算是 `ICONS[name] || ''` —— 未知名字**静默渲染为空**，
 * 既不报错也不告警。2026-10-29 实测：13 个名字被 9 个视图引用却没有定义
 * （key / layers / list / timer / lock / grid / flag / history / check-square /
 * git-commit / chart / heart-pulse / share-nodes），页面标题旁一直是空白，
 * 而所有测试与 lint 都是绿的。
 *
 * 这个用例把「引用集合 ⊆ 定义集合」钉成断言：少定义一个图标 CI 就红。
 */

const SRC = path.resolve(process.cwd(), 'src');
const ICON_FILE = path.join(SRC, 'components', 'AppIcon.vue');

function definedIcons() {
  const text = fs.readFileSync(ICON_FILE, 'utf-8');
  const start = text.indexOf('const ICONS = {');
  const end = text.indexOf('const props =');
  expect(start).toBeGreaterThan(-1);
  expect(end).toBeGreaterThan(start);
  const block = text.slice(start, end);
  // 只认「两个空格缩进 + 名字 + 冒号」这种条目写法，避免误抓 path 里的内容
  return new Set(
    [...block.matchAll(/^ {2}'?([a-z][a-z0-9-]*)'?:\s*'/gm)].map((m) => m[1]),
  );
}

function walk(dir, out = []) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      if (entry.name === 'node_modules' || entry.name === '__tests__') continue;
      walk(full, out);
    } else if (/\.(vue|js)$/.test(entry.name)) {
      out.push(full);
    }
  }
  return out;
}

function usedIcons() {
  const used = new Map(); // name -> Set(files)
  for (const file of walk(SRC)) {
    const text = fs.readFileSync(file, 'utf-8');
    const rel = path.relative(SRC, file).replace(/\\/g, '/');
    const add = (name) => {
      if (!used.has(name)) used.set(name, new Set());
      used.get(name).add(rel);
    };
    // 模板里：icon="key"；脚本里：icon: 'key'
    for (const m of text.matchAll(/icon="([a-z][a-z0-9-]*)"/g)) add(m[1]);
    for (const m of text.matchAll(/icon:\s*'([a-z][a-z0-9-]*)'/g)) add(m[1]);
  }
  return used;
}

describe('AppIcon registry', () => {
  it('defines every icon name referenced by views and nav', () => {
    const defined = definedIcons();
    const used = usedIcons();
    const missing = [...used.entries()]
      .filter(([name]) => !defined.has(name))
      .map(([name, files]) => `${name} (used by ${[...files].join(', ')})`);

    expect(missing, `未定义的图标名会让 glyph 静默为空：\n  ${missing.join('\n  ')}`).toEqual([]);
  });

  it('actually parses a non-trivial number of definitions', () => {
    // 防止正则失效导致上面的用例空转变绿
    expect(definedIcons().size).toBeGreaterThan(60);
  });
});
