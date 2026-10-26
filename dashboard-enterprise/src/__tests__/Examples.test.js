import { describe, it, expect, beforeEach, vi } from 'vitest';
import { mount, flushPromises } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';

const mockToast = { success: vi.fn(), error: vi.fn(), warn: vi.fn(), info: vi.fn(), show: vi.fn(), dismiss: vi.fn() };
vi.mock('../composables/useToast.js', () => ({
  useToast: () => mockToast,
  toastState: { items: [] },
}));

import Examples from '../views/Examples.vue';
import { EXAMPLES } from '../data/examples.js';

/**
 * 示例库是纯前端内容页。三个断言点：
 *  1. 每个示例都必须有对应的 i18n 文案 —— 数据文件与 i18n 是两处独立维护的，
 *     一旦 id 写错，页面上会直接显示 `view.examples.item.xxx.title` 这种原始 key。
 *  2. 搜索要同时命中 task 原文和本地化标题（用户记不住是哪种写法）。
 *  3. 复制的是 `maop run --task "..."`，与 README 的用法一致。
 */
describe('Examples.vue', () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    localStorage.setItem('maop_locale', 'en');
  });

  function mountView() {
    return mount(Examples);
  }

  it('renders one card per example', async () => {
    const wrapper = mountView();
    await flushPromises();
    const cards = wrapper.findAll('.ex-cmd');
    expect(cards).toHaveLength(EXAMPLES.length);
  });

  it('has i18n copy for every example id', async () => {
    const wrapper = mountView();
    await flushPromises();
    const text = wrapper.text();
    // 缺 key 时 t() 原样返回 key，这里就会命中
    expect(text).not.toContain('view.examples.item.');
  });

  it('filters by category', async () => {
    const wrapper = mountView();
    await flushPromises();
    await wrapper.find('select').setValue('docs');
    await flushPromises();

    const cmds = wrapper.findAll('.ex-cmd').map((n) => n.text());
    expect(cmds).toHaveLength(1);
    expect(cmds[0]).toContain('generate API docs');
  });

  it('searches by task text', async () => {
    const wrapper = mountView();
    await flushPromises();
    await wrapper.find('input[type="text"]').setValue('JWT');
    await flushPromises();

    const cmds = wrapper.findAll('.ex-cmd').map((n) => n.text());
    expect(cmds).toHaveLength(1);
    expect(cmds[0]).toContain('JWT');
  });

  it('searches by localized title', async () => {
    const wrapper = mountView();
    await flushPromises();
    // 标题是 "Switch auth to JWT"，task 里没有 "switch"
    await wrapper.find('input[type="text"]').setValue('switch');
    await flushPromises();

    expect(wrapper.findAll('.ex-cmd')).toHaveLength(1);
  });

  it('shows an empty state when nothing matches', async () => {
    const wrapper = mountView();
    await flushPromises();
    await wrapper.find('input[type="text"]').setValue('zzz-no-match');
    await flushPromises();
    expect(wrapper.text()).toContain('No example matches this filter');
  });

  it('copies the runnable command, not just the prompt', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });

    const wrapper = mountView();
    await flushPromises();
    await wrapper.findAll('button').find((b) => b.text().includes('Copy command')).trigger('click');
    await flushPromises();

    expect(writeText).toHaveBeenCalledTimes(1);
    const copied = writeText.mock.calls[0][0];
    expect(copied.startsWith('maop run --task "')).toBe(true);
    expect(copied).toContain('refactor auth module to use JWT');
  });

  it('reports a copy failure instead of throwing', async () => {
    Object.assign(navigator, {
      clipboard: { writeText: vi.fn().mockRejectedValue(new Error('denied')) },
    });

    const wrapper = mountView();
    await flushPromises();
    await wrapper.findAll('button').find((b) => b.text().includes('Copy command')).trigger('click');
    await flushPromises();

    expect(wrapper.text()).toContain('Copy failed');
  });
});
