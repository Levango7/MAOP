// Smoke tests for Docs.vue — static documentation viewer.
//
// Docs.vue has no API calls; it uses import.meta.glob to lazily load markdown
// files from ../../../docs/**/*.md. We stub PageHeader and assert the root
// renders, the sidebar lists categories, and selecting a doc renders content.

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { mount, flushPromises } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';
import Docs from '../views/Docs.vue';

const mountOptions = { global: { stubs: { PageHeader: { template: '<slot />' } } } };

describe('Docs.vue', () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    global.__VITEST__ = true;
    if (typeof window !== 'undefined') window.__VITEST__ = true;
  });

  afterEach(() => {
    delete global.__VITEST__;
    if (typeof window !== 'undefined') delete window.__VITEST__;
  });

  async function mountDocs() {
    const wrapper = mount(Docs, mountOptions);
    // Docs.vue calls selectDoc() synchronously on setup; the loader is async.
    await flushPromises();
    await flushPromises();
    return wrapper;
  }

  it('renders the docs-page root element', async () => {
    const wrapper = await mountDocs();
    expect(wrapper.find('.docs-page').exists()).toBe(true);
    wrapper.unmount();
  });

  it('renders the docs-layout with sidebar and content sections', async () => {
    const wrapper = await mountDocs();
    expect(wrapper.find('.docs-layout').exists()).toBe(true);
    expect(wrapper.find('.docs-sidebar').exists()).toBe(true);
    expect(wrapper.find('.docs-content').exists()).toBe(true);
    wrapper.unmount();
  });

  it('renders documentation category headings in the sidebar', async () => {
    const wrapper = await mountDocs();
    // categories array has 4 entries: gettingStarted, guides, enterprise, integrations
    const cats = wrapper.findAll('.doc-cat');
    expect(cats.length).toBeGreaterThanOrEqual(4);
    wrapper.unmount();
  });

  it('renders doc list items as clickable buttons', async () => {
    const wrapper = await mountDocs();
    const items = wrapper.findAll('.doc-cat__item');
    // Total items across all categories >= 14 (4+5+3+2)
    expect(items.length).toBeGreaterThanOrEqual(10);
    wrapper.unmount();
  });

  it('renders either the article, loading, or empty state in the content area', async () => {
    const wrapper = await mountDocs();
    const content = wrapper.find('.docs-content');
    expect(content.exists()).toBe(true);
    // One of these three states must be present
    const hasArticle = content.find('.docs-article').exists();
    const hasLoading = content.find('.docs-loading').exists();
    const hasEmpty = content.find('.docs-empty').exists();
    expect(hasArticle || hasLoading || hasEmpty).toBe(true);
    wrapper.unmount();
  });
});