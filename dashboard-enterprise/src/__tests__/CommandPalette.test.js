import { describe, it, expect, beforeEach } from 'vitest';
import { mount } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';
import CommandPalette from '../components/CommandPalette.vue';

function makeWrapper() {
  return mount(CommandPalette, {
    global: {
      stubs: { Teleport: true, AppIcon: true, transition: false },
    },
  });
}

describe('CommandPalette', () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    // 注：原 `window.location.href = ...` 赋值会触发 jsdom 的
    // "Not implemented: navigation (except hash changes)" 未处理错误，
    // 使 vitest 全量跑出现 unhandled errors 且影响 CI 退出码。
    // CommandPalette 组件不读取 location（grep 确认无引用），该赋值冗余，已移除。
  });

  it('exposes command sources from nav (≥ 15 routes)', () => {
    const w = makeWrapper();
    const cmds = w.vm.commands;
    expect(cmds.length).toBeGreaterThanOrEqual(15);
    expect(cmds.every((c) => c.to && c.label && c.icon)).toBe(true);
  });

  it('filters commands by query', () => {
    const w = makeWrapper();
    w.vm.query = 'agent';
    const hits = w.vm.results;
    expect(hits.length).toBeGreaterThan(0);
    for (const h of hits) {
      expect(`${h.label} ${h.subtitle}`.toLowerCase()).toContain('agent');
    }
  });

  it('returns empty results for garbage query', () => {
    const w = makeWrapper();
    w.vm.query = 'zzzznotfound';
    expect(w.vm.results.length).toBe(0);
  });

  it('keys: Cmd+K toggles open', () => {
    const w = makeWrapper();
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'k', metaKey: true }));
    expect(w.vm.open).toBe(true);
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'k', metaKey: true }));
    expect(w.vm.open).toBe(false);
  });
});