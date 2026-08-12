import { describe, it, expect, beforeEach } from 'vitest';
import { mount } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';
import FilterBar from '../components/FilterBar.vue';

function makeWrapper(modelValue = null, props = {}) {
  return mount(FilterBar, {
    props: {
      modelValue: modelValue || {},
      schema: [
        { key: 'level', label: 'Level', options: [{ value: 'info' }, { value: 'warning' }, { value: 'critical' }] },
        { key: 'status', label: 'Status', options: [] },
      ],
      searchKey: 'query',
      searchPlaceholder: 'Search…',
      resultsLabel: '3 / 5',
      ...props,
    },
    global: {
      stubs: { AppIcon: true },
    },
  });
}

describe('FilterBar', () => {
  beforeEach(() => setActivePinia(createPinia()));

  it('renders search input and selects from schema', () => {
    const w = makeWrapper();
    expect(w.find('.filterbar__input').exists()).toBe(true);
    expect(w.findAll('.filterbar__select').length).toBe(1); // 只有带 options 的
    expect(w.text()).toContain('3 / 5');
  });

  it('renders an option set only when options exist (empty options → no select)', () => {
    const w = makeWrapper();
    // status.options 为空数组 → 不渲染 select
    const selects = w.findAll('.filterbar__select');
    expect(selects.length).toBe(1);
  });

  it('mutates modelValue object on input (no emit needed)', async () => {
    const mv = { query: '', level: '' };
    const w = makeWrapper(mv);
    const input = w.find('.filterbar__input');
    await input.setValue('agent');
    expect(mv.query).toBe('agent');
  });

  it('mutates modelValue on select change', async () => {
    const mv = { query: '', level: '' };
    const w = makeWrapper(mv);
    const select = w.find('.filterbar__select');
    await select.setValue('warning');
    expect(mv.level).toBe('warning');
  });
});