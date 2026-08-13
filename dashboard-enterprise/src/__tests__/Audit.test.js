// Tests for Audit.vue — field mapping + enhancement coverage.
//
// The backend may return either `time`/`level`/`target` or the legacy
// `timestamp`/`severity`/`resource` field names. loadEvents() normalises them:
//   time   = e.time   || e.timestamp
//   level  = e.level  || e.severity  || 'info'
//   target = e.target || e.resource  || ''
//
// Enhancement coverage:
//   - 4 stat cards (today ops / high-risk / active users / anomalies)
//   - trend chart data buckets
//   - heatmap user×action matrix
//   - pie chart action distribution
//   - advanced filters (action / level / actor / resource / keyword / range)
//   - CSV / JSON export
//   - alert rules tab (load / create / toggle / delete)
//   - alert history tab (load)
//   - live monitor toggle
//   - tab switching

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { mount, flushPromises } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';
import Audit from '../views/Audit.vue';
import DataTable from '../components/DataTable.vue';
import StatCard from '../components/StatCard.vue';
import Segmented from '../components/Segmented.vue';

import EmptyState from '../components/EmptyState.vue';
import DetailDrawer from '../components/DetailDrawer.vue';



// PageHeader calls useRoute() which needs a router context; stub it so we can
// mount the view without providing a full vue-router instance.
// Chart.js components (Line/Bar/Pie) need canvas which jsdom lacks — stub them.
const mountOptions = {
  global: {
    stubs: {
      PageHeader: true,
      Line: true,
      Bar: true,
      Pie: true,
    },
  },
};

describe('Audit.vue field mapping', () => {
  let originalFetch;

  beforeEach(() => {
    setActivePinia(createPinia());
    originalFetch = global.fetch;
    // Mark vitest env so handleUnauthorized won't dispatch CustomEvent.
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
      const u = String(url);
      const body = routes[u] ?? {};
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve(body),
        text: () => Promise.resolve(JSON.stringify(body)),
      });
    });
  }

  async function mountAudit() {
    const wrapper = mount(Audit, mountOptions);
    // loadAll runs loadEvents + loadSummary via Promise.allSettled in onMounted.
    // Two flushes settle the fetch microtasks + the .finally() in fetchWithTimeout.
    await flushPromises();
    await flushPromises();
    return wrapper;
  }

  it('maps timestamp/severity/resource to time/level/target', async () => {
    mockFetch({
      '/api/audit/events': {
        events: [
          {
            action: 'login',
            actor: 'alice',
            timestamp: '2026-01-01T00:00:00Z',
            severity: 'warning',
            resource: 'server-1',
          },
        ],
      },
      '/api/audit/summary': { summary: { total: 1, by_action: {}, by_actor: {} } },
    });
    const wrapper = await mountAudit();
    const rows = wrapper.findComponent(DataTable).props('rows');
    expect(rows).toHaveLength(1);
    expect(rows[0].time).toBe('2026-01-01T00:00:00Z');
    expect(rows[0].level).toBe('warning');
    expect(rows[0].target).toBe('server-1');
    expect(rows[0].action).toBe('login');
    expect(rows[0].actor).toBe('alice');
    wrapper.unmount();
  });

  it('prefers time/level/target when present and defaults level to info', async () => {
    mockFetch({
      '/api/audit/events': {
        events: [
          {
            action: 'logout',
            actor: 'bob',
            time: '2026-02-02T00:00:00Z',
            level: 'critical',
            target: 'db',
          },
          { action: 'ping', actor: 'carol' },
        ],
      },
      '/api/audit/summary': { summary: { total: 2 } },
    });
    const wrapper = await mountAudit();
    const rows = wrapper.findComponent(DataTable).props('rows');
    expect(rows).toHaveLength(2);
    // explicit values preserved
    expect(rows[0].time).toBe('2026-02-02T00:00:00Z');
    expect(rows[0].level).toBe('critical');
    expect(rows[0].target).toBe('db');
    // missing fields fall back: level -> 'info', target -> ''
    expect(rows[1].level).toBe('info');
    expect(rows[1].target).toBe('');
    wrapper.unmount();
  });

  it('preserves extra fields spread from the original event', async () => {
    mockFetch({
      '/api/audit/events': {
        events: [
          {
            action: 'rotate',
            actor: 'sys',
            timestamp: '2026-03-03T00:00:00Z',
            severity: 'info',
            resource: 'keys',
            ip: '10.0.0.1',
            request_id: 'abc-123',
          },
        ],
      },
      '/api/audit/summary': { summary: { total: 1 } },
    });
    const wrapper = await mountAudit();
    const rows = wrapper.findComponent(DataTable).props('rows');
    expect(rows[0].ip).toBe('10.0.0.1');
    expect(rows[0].request_id).toBe('abc-123');
    wrapper.unmount();
  });

  it('sets events.error and renders EmptyState instead of DataTable on failure', async () => {
    global.fetch = vi.fn(() =>
      Promise.resolve({
        ok: false,
        status: 500,
        json: () => Promise.resolve({}),
        text: () => Promise.resolve(''),
      })
    );
    const wrapper = await mountAudit();
    // On error the DataTable is not rendered; EmptyState shows the error.
    expect(wrapper.findComponent(DataTable).exists()).toBe(false);
    expect(wrapper.text()).toContain('API /api/audit/events: 500');
    wrapper.unmount();
  });
});

// ══════════════════════════════════════════════════════════════════════════
// Enhancement coverage: stat cards, filters, charts, export, tabs
// ══════════════════════════════════════════════════════════════════════════
describe('Audit.vue enhancement', () => {
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
    global.fetch = vi.fn((url, init) => {
      const u = String(url).split('?')[0];
      const body = routes[u] ?? {};
      // For POST/PUT/DELETE, echo back the body so we can inspect
      if (init && init.method && init.body) {
        try {
          const parsed = JSON.parse(init.body);
          return Promise.resolve({
            ok: true,
            status: 200,
            json: () => Promise.resolve({ status: 'ok', ...parsed, ...body }),
            text: () => Promise.resolve(JSON.stringify(body)),
          });
        } catch { /* fall through */ }
      }
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve(body),
        text: () => Promise.resolve(JSON.stringify(body)),
      });
    });
  }

  async function mountAudit() {
    const wrapper = mount(Audit, mountOptions);
    await flushPromises();
    await flushPromises();
    return wrapper;
  }

  // Build a set of events spread across the last 24h so stat cards + charts
  // have non-trivial data to render.
  function sampleEvents() {
    const now = Date.now();
    const iso = (msAgo) => new Date(now - msAgo).toISOString();
    return [
      { action: 'login', actor: 'alice', time: iso(1000), level: 'info', target: 'auth', result: 'success' },
      { action: 'login', actor: 'bob', time: iso(2000), level: 'info', target: 'auth', result: 'success' },
      { action: 'delete', actor: 'alice', time: iso(3000), level: 'critical', target: 'db', result: 'success' },
      { action: 'update', actor: 'carol', time: iso(4000), level: 'warning', target: 'config', result: 'success' },
      { action: 'logout', actor: 'alice', time: iso(5000), level: 'info', target: 'auth', result: 'success' },
      { action: 'export', actor: 'bob', time: iso(6000), level: 'warning', target: 'report', result: 'failure' },
    ];
  }

  it('renders 4 stat cards (today ops / high-risk / active users / anomalies)', async () => {
    mockFetch({
      '/api/audit/events': { events: sampleEvents() },
      '/api/audit/summary': { summary: { total: 6 } },
    });
    const wrapper = await mountAudit();
    const cards = wrapper.findAllComponents(StatCard);
    expect(cards).toHaveLength(4);
    // Today ops = 6 (all within 24h)
    expect(cards[0].props('value')).toBe(6);
    // High-risk (critical) = 1
    expect(cards[1].props('value')).toBe(1);
    // Active users = 3 (alice, bob, carol)
    expect(cards[2].props('value')).toBe(3);
    // Anomalies (warning + critical) = 3
    expect(cards[3].props('value')).toBe(3);
    wrapper.unmount();
  });

  it('renders 3 tab options in Segmented (events / rules / history)', async () => {
    mockFetch({
      '/api/audit/events': { events: [] },
      '/api/audit/summary': { summary: { total: 0 } },
    });
    const wrapper = await mountAudit();
    const seg = wrapper.findComponent(Segmented);
    expect(seg.exists()).toBe(true);
    const opts = seg.props('options');
    expect(opts).toHaveLength(3);
    expect(opts.map((o) => o.value)).toEqual(['events', 'rules', 'history']);
    wrapper.unmount();
  });

  it('filters rows by keyword search across action/actor/target/detail', async () => {
    mockFetch({
      '/api/audit/events': { events: sampleEvents() },
      '/api/audit/summary': { summary: { total: 6 } },
    });
    const wrapper = await mountAudit();
    // Initial: 6 rows
    expect(wrapper.findComponent(DataTable).props('rows')).toHaveLength(6);

    // Simulate keyword filter by mutating the filters reactive object.
    // FilterBar writes into modelValue directly; we find the search input.
    const searchInput = wrapper.find('input[type="search"]');
    expect(searchInput.exists()).toBe(true);
    await searchInput.setValue('alice');

    // After filter: alice appears in 3 events (login, delete, logout)
    expect(wrapper.findComponent(DataTable).props('rows')).toHaveLength(3);
    wrapper.unmount();
  });

  it('filters rows by action select', async () => {
    mockFetch({
      '/api/audit/events': { events: sampleEvents() },
      '/api/audit/summary': { summary: { total: 6 } },
    });
    const wrapper = await mountAudit();
    // FilterBar renders schema selects first (action, level, actor, resource),
    // then the extra slot's range select. The schema selects use @change.
    const selects = wrapper.findAll('select');
    // action select is the first one in filterSchema
    selects[0].element.value = 'login';
    await selects[0].trigger('change');
    expect(wrapper.findComponent(DataTable).props('rows')).toHaveLength(2);
    wrapper.unmount();
  });

  it('exports CSV via download Blob', async () => {
    mockFetch({
      '/api/audit/events': { events: sampleEvents() },
      '/api/audit/summary': { summary: { total: 6 } },
    });
    const wrapper = await mountAudit();

    // Stub URL.createObjectURL + a.click to capture the CSV content
    const captured = { url: null, filename: null, clicks: 0 };
    const origCreate = URL.createObjectURL;
    const origRevoke = URL.revokeObjectURL;
    URL.createObjectURL = vi.fn((blob) => {
      captured.url = blob;
      return 'blob:fake';
    });
    URL.revokeObjectURL = vi.fn();
    // Mock createElement to capture <a download>
    const origCreateElement = document.createElement.bind(document);
    vi.spyOn(document, 'createElement').mockImplementation((tag) => {
      const el = origCreateElement(tag);
      if (tag === 'a') {
        Object.defineProperty(el, 'click', { value: () => { captured.clicks += 1; captured.filename = el.download; } });
      }
      return el;
    });

    // Click the CSV export button (first button in export-group)
    const exportButtons = wrapper.findAll('.export-group button');
    expect(exportButtons.length).toBeGreaterThanOrEqual(2);
    await exportButtons[0].trigger('click');

    expect(captured.clicks).toBe(1);
    expect(captured.filename).toBe('audit-events.csv');
    expect(captured.url).toBeInstanceOf(Blob);
    expect(captured.url.type).toBe('text/csv');

    URL.createObjectURL = origCreate;
    URL.revokeObjectURL = origRevoke;
    document.createElement.mockRestore();
    wrapper.unmount();
  });

  it('exports JSON via download Blob', async () => {
    mockFetch({
      '/api/audit/events': { events: sampleEvents() },
      '/api/audit/summary': { summary: { total: 6 } },
    });
    const wrapper = await mountAudit();

    const captured = { url: null, filename: null, clicks: 0 };
    const origCreate = URL.createObjectURL;
    const origRevoke = URL.revokeObjectURL;
    URL.createObjectURL = vi.fn((blob) => { captured.url = blob; return 'blob:fake'; });
    URL.revokeObjectURL = vi.fn();
    const origCreateElement = document.createElement.bind(document);
    vi.spyOn(document, 'createElement').mockImplementation((tag) => {
      const el = origCreateElement(tag);
      if (tag === 'a') {
        Object.defineProperty(el, 'click', { value: () => { captured.clicks += 1; captured.filename = el.download; } });
      }
      return el;
    });

    const exportButtons = wrapper.findAll('.export-group button');
    await exportButtons[1].trigger('click');

    expect(captured.clicks).toBe(1);
    expect(captured.filename).toBe('audit-events.json');
    expect(captured.url.type).toBe('application/json');

    URL.createObjectURL = origCreate;
    URL.revokeObjectURL = origRevoke;
    document.createElement.mockRestore();
    wrapper.unmount();
  });

  it('switches to rules tab and loads alert rules', async () => {
    mockFetch({
      '/api/audit/events': { events: [] },
      '/api/audit/summary': { summary: { total: 0 } },
      '/api/audit/rules': {
        rules: [
          { id: 'r1', name: 'Critical delete', condition: 'action=delete AND severity=critical', severity: 'critical', enabled: true },
          { id: 'r2', name: 'Failed login', condition: 'action=login AND result=failure', severity: 'warning', enabled: false },
        ],
      },
    });
    const wrapper = await mountAudit();

    // Click the "rules" tab via Segmented
    const seg = wrapper.findComponent(Segmented);
    seg.vm.$emit('update:modelValue', 'rules');
    await flushPromises();
    await flushPromises();

    // Rule list should render 2 rows
    const ruleRows = wrapper.findAll('.rule-row');
    expect(ruleRows).toHaveLength(2);
    expect(wrapper.text()).toContain('Critical delete');
    expect(wrapper.text()).toContain('Failed login');
    wrapper.unmount();
  });

  it('shows rules error EmptyState when /api/audit/rules fails', async () => {
    global.fetch = vi.fn((url) => {
      const u = String(url).split('?')[0];
      if (u === '/api/audit/rules') {
        return Promise.resolve({ ok: false, status: 500, json: () => Promise.resolve({}), text: () => Promise.resolve('') });
      }
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ events: [], summary: { total: 0 } }), text: () => Promise.resolve('{}') });
    });
    const wrapper = await mountAudit();

    const seg = wrapper.findComponent(Segmented);
    seg.vm.$emit('update:modelValue', 'rules');
    await flushPromises();
    await flushPromises();

    // Should show error EmptyState with the rules error message
    const empties = wrapper.findAllComponents(EmptyState);
    expect(empties.length).toBeGreaterThanOrEqual(1);
    expect(wrapper.text()).toContain('API /api/audit/rules: 500');
    wrapper.unmount();
  });

  it('opens rule editor drawer when create button clicked', async () => {
    mockFetch({
      '/api/audit/events': { events: [] },
      '/api/audit/summary': { summary: { total: 0 } },
      '/api/audit/rules': { rules: [] },
    });
    const wrapper = await mountAudit();

    // Switch to rules tab
    const seg = wrapper.findComponent(Segmented);
    seg.vm.$emit('update:modelValue', 'rules');
    await flushPromises();
    await flushPromises();

    // Drawer initially closed
    const drawerBefore = wrapper.findComponent(DetailDrawer);
    expect(drawerBefore.props('open')).toBe(false);

    // Click create button
    const createBtn = wrapper.find('button.act-btn');
    expect(createBtn.exists()).toBe(true);
    await createBtn.trigger('click');

    const drawerAfter = wrapper.findComponent(DetailDrawer);
    expect(drawerAfter.props('open')).toBe(true);
    wrapper.unmount();
  });

  it('switches to history tab and loads alert history', async () => {
    mockFetch({
      '/api/audit/events': { events: [] },
      '/api/audit/summary': { summary: { total: 0 } },
      '/api/audit/alerts': {
        alerts: [
          { id: 'a1', time: '2026-01-01T00:00:00Z', rule_name: 'Critical delete', event: 'delete db', actor: 'alice', severity: 'critical' },
        ],
      },
    });
    const wrapper = await mountAudit();

    const seg = wrapper.findComponent(Segmented);
    seg.vm.$emit('update:modelValue', 'history');
    await flushPromises();
    await flushPromises();

    // History DataTable should have 1 row
    const dt = wrapper.findComponent(DataTable);
    expect(dt.exists()).toBe(true);
    expect(dt.props('rows')).toHaveLength(1);
    expect(dt.props('rows')[0].rule_name).toBe('Critical delete');
    wrapper.unmount();
  });

  it('renders heatmap table when events exist', async () => {
    mockFetch({
      '/api/audit/events': { events: sampleEvents() },
      '/api/audit/summary': { summary: { total: 6 } },
    });
    const wrapper = await mountAudit();
    const heatmapTable = wrapper.find('.heatmap__table');
    expect(heatmapTable.exists()).toBe(true);
    // Should have header row + 3 user rows (alice, bob, carol)
    const rows = heatmapTable.findAll('tr');
    expect(rows.length).toBe(4); // 1 header + 3 users
    wrapper.unmount();
  });

  it('renders trend chart Card with Segmented for line/bar switch', async () => {
    mockFetch({
      '/api/audit/events': { events: sampleEvents() },
      '/api/audit/summary': { summary: { total: 6 } },
    });
    const wrapper = await mountAudit();
    // The trend Card contains a Segmented for chart kind switching
    const segs = wrapper.findAllComponents(Segmented);
    // First Segmented is tab switcher, second is trend kind
    expect(segs.length).toBeGreaterThanOrEqual(2);
    const trendSeg = segs[1];
    const opts = trendSeg.props('options');
    expect(opts.map((o) => o.value)).toEqual(['line', 'bar']);
    wrapper.unmount();
  });

  it('disables export buttons when no visible rows', async () => {
    mockFetch({
      '/api/audit/events': { events: [] },
      '/api/audit/summary': { summary: { total: 0 } },
    });
    const wrapper = await mountAudit();
    const exportButtons = wrapper.findAll('.export-group button');
    expect(exportButtons.length).toBeGreaterThanOrEqual(2);
    expect(exportButtons[0].attributes('disabled')).toBeDefined();
    expect(exportButtons[1].attributes('disabled')).toBeDefined();
    wrapper.unmount();
  });
});
