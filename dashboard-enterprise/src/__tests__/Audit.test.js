// Tests for Audit.vue field mapping in loadEvents().
//
// The backend may return either `time`/`level`/`target` or the legacy
// `timestamp`/`severity`/`resource` field names. loadEvents() normalises them:
//   time   = e.time   || e.timestamp
//   level  = e.level  || e.severity  || 'info'
//   target = e.target || e.resource  || ''
//
// We mount the real component, mock fetch for the two audit endpoints, then
// inspect the DataTable `rows` prop to verify the normalised output.

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { mount, flushPromises } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';
import Audit from '../views/Audit.vue';
import DataTable from '../components/DataTable.vue';
import { PageHeader } from '../components/index.js';

// PageHeader calls useRoute() which needs a router context; stub it so we can
// mount the view without providing a full vue-router instance.
const mountOptions = { global: { stubs: { PageHeader: true } } };

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