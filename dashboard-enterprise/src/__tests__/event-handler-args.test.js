import { describe, it, expect } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';

/**
 * 「DOM 事件对象被当成业务参数」的守卫（第五类静默失败）。
 *
 * 2026-11-05 实测：AgentVersions.vue 把 `@keyup.enter="load"` /
 * `@change="load"` / `@click="load"` 绑到 `async function load(page = 1)`。
 * Vue 对裸函数引用会传入 Event 作为首个实参，默认值不生效，于是
 * `params.set('page', String(page))` 拼出 `page=[object PointerEvent]`，
 * 后端 422、列表空白，且控制台不报错。既有 AgentVersions.test.js 的
 * 「passes filters」用例只断言 agent_id/status，因此长期漏网。
 *
 * 判据说明：不禁止所有「带形参的裸绑定」——`@change="onInput(e)"` 这类
 * 形参本就是为了接收事件/组件载荷，是正确写法。真正的信号是**形参带
 * 默认值**：作者明确期望调用方不传参，却被 Event 顶替了缺省。
 * 全仓扫描 86 个 .vue / 102 处裸绑定中，只有这一处命中。
 */

const SRC = path.resolve(process.cwd(), 'src');
const SCAN_DIRS = ['views', 'components'];

const BARE_HANDLER = /@([a-zA-Z][\w.-]*)\s*=\s*["']([A-Za-z_$][\w$]*)["']/g;
const FN_DECL = /(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(([^)]*)\)/g;
const FN_ARROW = /(?:const|let)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\(([^)]*)\)\s*=>/g;
const TEMPLATE_END = /<\/template>/;

/** 返回 { file, fn, params, events } 列表：裸绑定且形参含默认值。 */
function detect(text, file = '<sample>') {
  const end = TEMPLATE_END.exec(text);
  const template = end ? text.slice(0, end.index) : text;
  const script = end ? text.slice(end.index + end[0].length) : '';

  const bounds = new Map();
  for (const m of template.matchAll(BARE_HANDLER)) {
    if (!bounds.has(m[2])) bounds.set(m[2], []);
    bounds.get(m[2]).push(m[1]);
  }
  if (bounds.size === 0) return { findings: [], boundCount: 0 };

  const signatures = new Map();
  for (const m of script.matchAll(FN_DECL)) signatures.set(m[1], m[2].trim());
  for (const m of script.matchAll(FN_ARROW)) signatures.set(m[1], m[2].trim());

  const findings = [];
  for (const [fn, params] of signatures) {
    if (!bounds.has(fn)) continue;
    // A default value means the caller is expected to omit the argument.
    // Vue instead supplies the DOM Event, silently replacing the default.
    if (params.includes('=')) {
      findings.push({ file, fn, params, events: bounds.get(fn) });
    }
  }
  return { findings, boundCount: [...bounds.values()].reduce((n, v) => n + v.length, 0) };
}

function collectVueFiles() {
  const files = [];
  for (const dir of SCAN_DIRS) {
    const abs = path.join(SRC, dir);
    if (!fs.existsSync(abs)) continue;
    for (const name of fs.readdirSync(abs)) {
      if (name.endsWith('.vue')) files.push(path.join(abs, name));
    }
  }
  return files;
}

describe('bare event handlers must not shadow defaulted parameters', () => {
  it('detector self-check: a known-bad sample is caught, a clean one is not', () => {
    const bad = `<template><button @click="load" /></template>
      <script setup>async function load(page = 1) { return page; }</script>`;
    expect(detect(bad).findings.map((f) => f.fn)).toEqual(['load']);

    const clean = `<template><button @click="load()" /></template>
      <script setup>async function load(page = 1) { return page; }</script>`;
    expect(detect(clean).findings).toEqual([]);

    const eventArg = `<template><input @change="onInput" /></template>
      <script setup>function onInput(e) { return e.target.value; }</script>`;
    expect(detect(eventArg).findings).toEqual([]);
  });

  it('scans a real corpus (guards against a vacuously green test)', () => {
    const files = collectVueFiles();
    expect(files.length).toBeGreaterThan(60);
    let bound = 0;
    for (const file of files) bound += detect(fs.readFileSync(file, 'utf-8'), file).boundCount;
    expect(bound).toBeGreaterThan(50);
  });

  it('no bare handler binds to a function with a defaulted parameter', () => {
    const findings = [];
    for (const file of collectVueFiles()) {
      const text = fs.readFileSync(file, 'utf-8');
      findings.push(...detect(text, path.basename(file)).findings);
    }
    const details = findings.map(
      (f) => `${f.file}: @${f.events.join(',@')} -> ${f.fn}(${f.params})`,
    );
    expect(details).toEqual([]);
  });
});
