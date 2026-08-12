import { describe, it, expect, beforeEach } from 'vitest';
import { mount } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';
import ListPageLayout from '../components/ListPageLayout.vue';

function makeWrapper(props = {}, slots = {}) {
  return mount(ListPageLayout, {
    props: {
      loading: false,
      error: '',
      empty: false,
      ...props,
    },
    slots: {
      content: '<div class="content-slot">rows go here</div>',
      ...slots,
    },
    global: {
      stubs: { AppIcon: true },
    },
  });
}

describe('ListPageLayout', () => {
  beforeEach(() => setActivePinia(createPinia()));

  it('renders content slot in happy path', () => {
    const w = makeWrapper();
    expect(w.find('.content-slot').exists()).toBe(true);
    expect(w.text()).toContain('rows go here');
  });

  it('shows skeleton while loading (content hidden)', () => {
    const w = makeWrapper({ loading: true });
    expect(w.find('.content-slot').exists()).toBe(false);
    // Skeleton 组件 stub 掉, 验证加载分支不存在内容
  });

  it('renders error slot when error present', () => {
    const w = makeWrapper({ error: 'boom' });
    expect(w.find('.content-slot').exists()).toBe(false);
    expect(w.text()).toContain('boom');
  });

  it('renders fallback empty state when empty and no itemsEmpty slot', () => {
    const w = makeWrapper({ empty: true });
    expect(w.find('.content-slot').exists()).toBe(false);
  });

  it('prefers itemsEmpty slot over fallback', () => {
    const w = makeWrapper({ empty: true }, { itemsEmpty: '<div class="custom-empty">custom</div>' });
    expect(w.find('.custom-empty').exists()).toBe(true);
  });
});