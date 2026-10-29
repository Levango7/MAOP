import { describe, it, expect } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';

/**
 * useI18n 的 t() 在 key 不存在时**返回 key 本身**：
 *
 *   if (val === null || val === undefined) return key;
 *
 * 所以拼错的 key 不会报错、不会告警，界面上直接显示 `view.foo.bar` 原文 ——
 * 第三类"静默失败"（前两类见 app-icon-registry 与 nav-coverage 两个守卫）。
 *
 * 2026-10-29 全量探测：2975 个引用 key 全部命中字典，无缺失。这个用例是
 * 防止将来新增页面忘加词条时退化。
 */

const SRC = path.resolve(process.cwd(), 'src');
const I18N = path.join(SRC, 'i18n');

/** 按花括号配平，从 anchor 起截取对象内容。 */
function braceBlock(text, anchor) {
  const re = new RegExp(anchor, 'm');
  const m = text.match(re);
  if (!m) return '';
  const i = text.indexOf('{', m.index + m[0].length - 1);
  let depth = 0;
  for (let j = i; j < text.length; j += 1) {
    if (text[j] === '{') depth += 1;
    else if (text[j] === '}') {
      depth -= 1;
      if (depth === 0) return text.slice(i + 1, j);
    }
  }
  return '';
}

/** 取对象字面量里 4 空格缩进层的键。 */
function keysOf(block) {
  const keys = new Set();
  for (const m of block.matchAll(/^ {4}'([^']+)':/gm)) keys.add(m[1]);
  for (const m of block.matchAll(/^ {4}([A-Za-z_][\w.]*):/gm)) keys.add(m[1]);
  return keys;
}

/** 字典里的全部 key = coreMessages.en ∪ 每个 view-*.js 的 en 块。 */
function dictionaryKeys() {
  const keys = new Set();

  const indexText = fs.readFileSync(path.join(I18N, 'index.js'), 'utf-8');
  const core = braceBlock(indexText, 'export const coreMessages\\s*=');
  for (const k of keysOf(braceBlock(core, '^\\s*en:\\s*\\{'))) keys.add(k);

  for (const name of fs.readdirSync(I18N)) {
    if (!/^view-.*\.js$/.test(name)) continue;
    const text = fs.readFileSync(path.join(I18N, name), 'utf-8');
    const body = braceBlock(text, 'messages\\s*=\\s*\\{');
    for (const k of keysOf(braceBlock(body, '^\\s*en:\\s*\\{'))) keys.add(k);
  }

  return keys;
}

/** 代码里 t('literal') 的引用。动态拼接（'view.foo.' + x）不是字面量，跳过。 */
function referencedKeys() {
  const used = new Map();
  const walk = (dir) => {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        if (entry.name === 'node_modules' || entry.name === '__tests__' || entry.name === 'i18n') {
          continue;
        }
        walk(full);
      } else if (/\.(vue|js)$/.test(entry.name)) {
        const text = fs.readFileSync(full, 'utf-8');
        const rel = path.relative(SRC, full).replace(/\\/g, '/');
        for (const m of text.matchAll(/\bt\(\s*['"]([A-Za-z][\w.-]*)['"]/g)) {
          const key = m[1];
          // 无点号不是 i18n key；以点结尾说明后面还拼了变量，无法静态判定
          if (!key.includes('.') || key.endsWith('.')) continue;
          if (!used.has(key)) used.set(key, new Set());
          used.get(key).add(rel);
        }
      }
    }
  };
  walk(SRC);
  return used;
}

describe('i18n coverage', () => {
  it('every literal key passed to t() exists in the dictionary', () => {
    const dict = dictionaryKeys();
    const used = referencedKeys();
    const missing = [...used.entries()]
      .filter(([k]) => !dict.has(k))
      .map(([k, files]) => `${k} (used by ${[...files].join(', ')})`);

    expect(
      missing,
      `这些 key 不在字典里，t() 会把 key 原文直接显示到界面上：\n  ${missing.join('\n  ')}`,
    ).toEqual([]);
  });

  it('en and zh dictionaries have identical key sets', () => {
    const en = dictionaryKeys();
    // 用同一套解析取 zh：coreMessages.zh ∪ view-*.js 的 zh 块
    const zh = new Set();
    const indexText = fs.readFileSync(path.join(I18N, 'index.js'), 'utf-8');
    const core = braceBlock(indexText, 'export const coreMessages\\s*=');
    for (const k of keysOf(braceBlock(core, '^\\s*zh:\\s*\\{'))) zh.add(k);
    for (const name of fs.readdirSync(I18N)) {
      if (!/^view-.*\.js$/.test(name)) continue;
      const text = fs.readFileSync(path.join(I18N, name), 'utf-8');
      const body = braceBlock(text, 'messages\\s*=\\s*\\{');
      for (const k of keysOf(braceBlock(body, '^\\s*zh:\\s*\\{'))) zh.add(k);
    }

    const onlyEn = [...en].filter((k) => !zh.has(k));
    const onlyZh = [...zh].filter((k) => !en.has(k));
    expect(onlyEn, `只有英文、缺中文翻译：\n  ${onlyEn.join('\n  ')}`).toEqual([]);
    expect(onlyZh, `只有中文、缺英文原文：\n  ${onlyZh.join('\n  ')}`).toEqual([]);
  });

  it('actually parses a non-trivial dictionary and reference set', () => {
    // 防止正则失效导致上面两个用例空转变绿
    expect(dictionaryKeys().size).toBeGreaterThan(3000);
    expect(referencedKeys().size).toBeGreaterThan(2000);
  });
});
