// Smoke tests for Blackboard.vue — shared knowledge blackboard management page.
//
// Blackboard.onMounted calls loadAll() which hits /api/blackboard/snapshot,
// /api/blackboard/domains, /api/blackboard/history, /api/blackboard/stats
// via Promise.allSettled. We mock global.fetch, stub PageHeader (via
// ListPageLayout), then assert the root renders, tabs switch, snapshot
// cards display, domain entries render, and write modal opens.

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { mount, flushPromises } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';
import Blackboard from '../views/Blackboard.vue';

const mountOptions = {
  global: {
    stubs: {
      PageHeader: { template: '<slot />' },
    },
  },
};

describe('Blackboard.vue', () => {
  let originalFetch;

  beforeEach(() => {
    setActivePinia(createPinia());
    originalFetch = global.fetch;
    global.__VITEST__ = true;
    if (typeof window !== 'undefined') window.__VITEST__ = true;
  });

  afterEach(() => {
    global.fetch = originalFetch;
    delete global.__VITEST__;
    if (typeof window !== 'undefined') delete window.__VITEST__;
  });

  function mockFetch(routes) {
    global.fetch = vi.fn((url) => {
      const u = String(url).split('?')[0];
      const body = routes[u] ?? {};
      return Promise.resolve({
        ok: true, status: 200,
        json: () => Promise.resolve(body),
        text: () => Promise.resolve(JSON.stringify(body)),
      });
    });
  }

  // Real BlackboardDomain whitelist values (py/maop/core/reliability/blackboard.py
  // R-8). The old mocks used 'tasks'/'results'/'metrics', which the backend can
  // never return — and the view's `h.op || h.action` style fallbacks let those
  // wrong-vocabulary stubs pass while the real wire shape stayed untested.
  const REAL_DOMAINS = [
    'problem', 'partial_solution', 'hypothesis', 'evidence',
    'decision', 'constraint', 'agent_contribution',
  ];

  // A typed envelope never omits a key, so entries are built whole.
  function entry(overrides = {}) {
    return {
      entry_id: 'e1',
      domain: 'problem',
      content: 'do X',
      contributor: 'agent_a',
      timestamp: '2026-10-23T00:00:00+00:00',
      confidence: 0.9,
      metadata: {},
      ...overrides,
    };
  }

  // Blackboard._record_history() — 5 keys, plus cleared_count on clear rows.
  function historyRow(overrides = {}) {
    return {
      action: 'write',
      domain: 'problem',
      entry_id: 'e1',
      contributor: 'agent_a',
      timestamp: '2026-10-23T00:00:00+00:00',
      ...overrides,
    };
  }

  function defaultRoutes(overrides = {}) {
    return {
      '/api/blackboard/snapshot': { status: 'ok', data: {} },
      '/api/blackboard/domains': { status: 'ok', data: { allowed: REAL_DOMAINS, active: [] } },
      '/api/blackboard/history': { status: 'ok', data: [] },
      '/api/blackboard/stats': {
        status: 'ok',
        data: {
          total_entries: 0,
          active_domains: [],
          event_bus_enabled: false,
          allowed_domains: REAL_DOMAINS,
        },
      },
      ...overrides,
    };
  }

  async function mountBlackboard() {
    const wrapper = mount(Blackboard, mountOptions);
    await flushPromises();
    await flushPromises();
    return wrapper;
  }

  it('renders the blackboard-view root element', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountBlackboard();
    expect(wrapper.find('.blackboard-view').exists()).toBe(true);
    wrapper.unmount();
  });

  it('renders snapshot tab content by default', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountBlackboard();
    // Snapshot tab is active by default → empty state when no domains
    expect(wrapper.find('.blackboard-view').exists()).toBe(true);
    expect(wrapper.text()).toContain('Blackboard is empty');
    wrapper.unmount();
  });

  it('renders snapshot cards when snapshot has data', async () => {
    mockFetch(defaultRoutes({
      '/api/blackboard/snapshot': {
        status: 'ok',
        data: {
          problem: [
            entry({ entry_id: 'e1', content: 'do X' }),
            entry({ entry_id: 'e2', content: 'do Y', contributor: 'agent_b', confidence: 0.8 }),
          ],
          evidence: [
            entry({ entry_id: 'e3', content: 'done', domain: 'evidence', confidence: 1.0 }),
          ],
        },
      },
      '/api/blackboard/stats': {
        status: 'ok',
        data: {
          total_entries: 3,
          active_domains: ['problem', 'evidence'],
          event_bus_enabled: false,
          allowed_domains: REAL_DOMAINS,
        },
      },
    }));
    const wrapper = await mountBlackboard();
    const cards = wrapper.findAll('.snap-card');
    expect(cards.length).toBe(2);
    expect(wrapper.text()).toContain('problem');
    expect(wrapper.text()).toContain('evidence');
    wrapper.unmount();
  });

  it('switches to domains tab and shows domain entries', async () => {
    mockFetch(defaultRoutes({
      '/api/blackboard/domains': {
        status: 'ok',
        data: { allowed: REAL_DOMAINS, active: ['problem'] },
      },
      '/api/blackboard/domains/problem': {
        status: 'ok',
        data: [entry()],
        count: 1,
      },
    }));
    const wrapper = await mountBlackboard();
    // tabOptions: snapshot, domains, history, stats → domains is index 1
    const segButtons = wrapper.findAll('.segmented__item');
    await segButtons[1].trigger('click');
    await flushPromises();
    // Domain entries should be visible
    expect(wrapper.text()).toContain('do X');
    expect(wrapper.text()).toContain('agent_a');
    wrapper.unmount();
  });

  it('switches to history tab', async () => {
    mockFetch(defaultRoutes({
      '/api/blackboard/history': {
        status: 'ok',
        data: [
          historyRow(),
          historyRow({ action: 'clear', entry_id: '', contributor: '', cleared_count: 3 }),
        ],
      },
    }));
    const wrapper = await mountBlackboard();
    const segButtons = wrapper.findAll('.segmented__item');
    await segButtons[2].trigger('click');
    await flushPromises();
    const items = wrapper.findAll('.hist-item');
    expect(items).toHaveLength(2);
    expect(items[0].text()).toContain('write');
    expect(items[0].text()).toContain('agent_a');
    expect(items[0].text()).toContain('problem');
    // 空 contributor 的 clear 行不能被当成"没有这一行"，也不该冒出别的值
    expect(items[1].text()).toContain('clear');
    wrapper.unmount();
  });

  it('switches to stats tab and shows statistics', async () => {
    mockFetch(defaultRoutes({
      '/api/blackboard/stats': {
        status: 'ok',
        data: {
          total_entries: 42,
          active_domains: ['problem', 'evidence'],
          event_bus_enabled: true,
          allowed_domains: REAL_DOMAINS,
        },
      },
    }));
    const wrapper = await mountBlackboard();
    const segButtons = wrapper.findAll('.segmented__item');
    await segButtons[3].trigger('click');
    await flushPromises();
    expect(wrapper.find('.stats-grid').exists()).toBe(true);
    expect(wrapper.text()).toContain('42');
    expect(wrapper.text()).toContain('partial_solution');
    wrapper.unmount();
  });

  it('opens write entry modal when Write Entry button clicked', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountBlackboard();
    // Switch to domains tab to access the Write Entry button
    const segButtons = wrapper.findAll('.segmented__item');
    await segButtons[1].trigger('click');
    await flushPromises();
    const buttons = wrapper.findAll('button');
    const writeBtn = buttons.find((b) => b.text().includes('Write Entry'));
    expect(writeBtn).toBeTruthy();
    await writeBtn.trigger('click');
    await flushPromises();
    // Modal should be teleported to body
    const modal = document.querySelector('.modal-overlay');
    expect(modal).toBeTruthy();
    wrapper.unmount();
  });

  it('renders only the keys the backend actually sends', async () => {
    // Negative probe: the template used to chain `h.op || h.operation || h.action`,
    // `h.actor || h.contributor`, `h.domain || h.target`,
    // `e.timestamp || e.ts || e.created_at`. Those fallbacks are why the
    // wrong-vocabulary mocks passed, so a row that carries BOTH spellings has to
    // be rendered from the producer's own keys — and a row carrying only the
    // dead spellings must contribute nothing at all.
    mockFetch(defaultRoutes({
      '/api/blackboard/history': {
        status: 'ok',
        data: [
          historyRow(),
          historyRow({
            action: 'write', contributor: '', domain: '', entry_id: '', timestamp: '',
            op: 'clear', operation: 'clear', actor: 'FAKE_ACTOR', target: 'FAKE_TARGET',
            id: 'FAKE_ID', ts: '2020-01-01T00:00:00Z', time: '2020-01-01T00:00:00Z',
          }),
        ],
      },
      '/api/blackboard/domains': {
        status: 'ok',
        data: { allowed: REAL_DOMAINS, active: ['problem'] },
      },
      '/api/blackboard/domains/problem': {
        status: 'ok',
        data: [entry({ timestamp: '', ts: '2020-01-01T00:00:00Z', created_at: '2020-01-01T00:00:00Z' })],
        count: 1,
      },
    }));
    const wrapper = await mountBlackboard();
    const segButtons = wrapper.findAll('.segmented__item');
    await segButtons[2].trigger('click');
    await flushPromises();

    const items = wrapper.findAll('.hist-item');
    expect(items).toHaveLength(2);
    // real keys only → text and tone both come from `action` / `contributor`
    expect(items[0].find('.badge').text()).toBe('write');
    expect(items[0].find('.badge').classes()).toContain('badge--success');
    expect(items[0].text()).toContain('agent_a');

    // row 1 disagrees with itself: action=write (success) vs op=clear (fail).
    // Reading the dead `op` first would badge it red and label it "clear".
    const deadBadge = items[1].find('.badge');
    expect(deadBadge.text()).toBe('write');
    expect(deadBadge.classes()).toContain('badge--success');
    for (const fake of ['FAKE_ACTOR', 'FAKE_TARGET', 'FAKE_ID', 'clear']) {
      expect(items[1].text()).not.toContain(fake);
    }
    expect(items[1].text()).not.toContain('ago');

    // entries table: a row with only ts/created_at must not render a time
    const row = wrapper.findAll('.bb-table__row')[0];
    expect(row.find('.bb-table__time').text()).toBe('—');
    wrapper.unmount();
  });

  it('does not crash when all API endpoints fail', async () => {
    global.fetch = vi.fn(() => Promise.resolve({
      ok: false, status: 500,
      json: () => Promise.resolve({}),
      text: () => Promise.resolve(''),
    }));
    const wrapper = await mountBlackboard();
    expect(wrapper.find('.blackboard-view').exists()).toBe(true);
    // Error state shown for snapshot tab
    expect(wrapper.text()).toContain('Failed to load snapshot');
    wrapper.unmount();
  });

  it('displays confidence badge with appropriate tone', async () => {
    mockFetch(defaultRoutes({
      '/api/blackboard/domains': {
        status: 'ok',
        data: { allowed: REAL_DOMAINS, active: ['problem'] },
      },
      '/api/blackboard/domains/problem': {
        status: 'ok',
        data: [
          entry({ entry_id: 'e1', content: 'high confidence', contributor: 'a', confidence: 0.95 }),
          entry({ entry_id: 'e2', content: 'low confidence', contributor: 'b', confidence: 0.2 }),
        ],
        count: 2,
      },
    }));
    const wrapper = await mountBlackboard();
    const segButtons = wrapper.findAll('.segmented__item');
    await segButtons[1].trigger('click');
    await flushPromises();
    // Confidence values should be formatted
    expect(wrapper.text()).toContain('0.95');
    expect(wrapper.text()).toContain('0.20');
    wrapper.unmount();
  });
});