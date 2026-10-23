// Tests for Notifications.vue — notification list, mark-read, empty/error states, detail drawer.
//
// Notifications.onMounted calls loadAll() which hits
// /api/notifications/list. We mock global.fetch, stub PageHeader so its
// badges/default slots pass through, then assert on the rendered list,
// stat cards, EmptyState, DetailDrawer and mark-read/mark-all actions.

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { mount, flushPromises } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';

// Mock useConfirm — showConfirm returns Promise<boolean>
const mockShowConfirm = vi.fn(() => Promise.resolve(true));
vi.mock('../composables/useConfirm.js', () => ({
  useConfirm: () => ({ showConfirm: mockShowConfirm }),
  confirmState: { visible: false, message: '', title: '', confirmText: '', cancelText: '', tone: 'danger', _resolve: null },
}));

import Notifications from '../views/Notifications.vue';
import ListPageLayout from '../components/ListPageLayout.vue';
import StatCard from '../components/StatCard.vue';
import EmptyState from '../components/EmptyState.vue';
import DetailDrawer from '../components/DetailDrawer.vue';
import Badge from '../components/Badge.vue';

// PageHeader calls useRoute() which needs a router context; stub it so the
// badges slot and the default actions slot both pass through to ListPageLayout.
const mountOptions = {
  global: {
    stubs: {
      PageHeader: { template: '<slot name="badges" /><slot />' },
      // Chart.js is not used here, but keep the stubs listed in the task spec
      // so the test file documents the expected stubbing pattern.
      Line: true,
      Doughnut: true,
    },
  },
};

describe('Notifications.vue', () => {
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

  async function mountNotifications() {
    const wrapper = mount(Notifications, mountOptions);
    // loadAll awaits api.get then sets loading=false in finally.
    // Two flushes settle the fetch microtasks + the .finally() in fetchWithTimeout.
    await flushPromises();
    await flushPromises();
    return wrapper;
  }

  function sampleNotifications() {
    const now = Date.now();
    // Real backend shape (NotificationResponse): notification_id / body /
    // read_at / event_type. The old id/message/read/category sample hid the
    // mismatch that made the live page render blank messages and call
    // /api/notifications/undefined/read.
    return [
      {
        notification_id: 'notif_1',
        level: 'info',
        event_type: '',
        title: 'Agent deployed',
        body: 'Agent "data-analyst" was deployed successfully',
        read_at: 0,
        created_at: now - 60_000,
        event_payload: { agent_id: 'agent_1', action: 'deploy' },
      },
      {
        notification_id: 'notif_2',
        level: 'warning',
        event_type: 'cost_daily_exceeded',
        title: 'Cost threshold reached',
        body: 'Daily spend exceeded 80% of budget',
        read_at: 0,
        created_at: now - 3_600_000,
        event_payload: { budget: 100, current: 82 },
      },
      {
        notification_id: 'notif_3',
        level: 'error',
        event_type: 'audit_alert',
        title: 'Auth failure spike',
        body: '5 consecutive login failures from 10.0.0.1',
        read_at: now - 3_000_000,
        created_at: now - 7_200_000, // 2h ago — within the 24h "today" window
        event_payload: { ip: '10.0.0.1' },
      },
    ];
  }

  function defaultRoutes(overrides = {}) {
    return {
      '/api/notifications/list': { notifications: sampleNotifications(), total: 3, has_more: false },
      ...overrides,
    };
  }

  // ── 1. renders the notifications root element ──
  it('renders the notifications root element', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountNotifications();
    expect(wrapper.find('.notifications-view').exists()).toBe(true);
    // ListPageLayout skeleton is rendered
    expect(wrapper.findComponent(ListPageLayout).exists()).toBe(true);
    wrapper.unmount();
  });

  // ── 2. renders notification items from loaded data ──
  it('renders notification items from loaded data', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountNotifications();
    const rows = wrapper.findAll('.notif-row');
    expect(rows).toHaveLength(3);
    // Title text is rendered
    expect(wrapper.text()).toContain('Agent deployed');
    expect(wrapper.text()).toContain('Cost threshold reached');
    expect(wrapper.text()).toContain('Auth failure spike');
    wrapper.unmount();
  });

  // ── Extra: real NotificationResponse field names ──
  it('maps the backend notification_id/body/read_at/event_type contract', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountNotifications();
    const rows = wrapper.findAll('.notif-row');
    expect(rows).toHaveLength(3);
    // body (not message) is rendered
    expect(rows[1].text()).toContain('Daily spend exceeded 80% of budget');
    // read_at != 0 means read
    expect(rows[2].classes()).not.toContain('is-unread');
    // event_type is mapped to the existing category badges
    expect(rows[1].text()).toContain('Cost');
    expect(rows[2].text()).toContain('Security');
    wrapper.unmount();
  });

  it('renders the critical level and quota category', async () => {
    mockFetch({
      '/api/notifications/list': {
        notifications: [{
          notification_id: 'notif_c',
          level: 'critical',
          event_type: 'quota_exceeded',
          title: 'Quota exhausted',
          body: 'Tenant quota is exhausted',
          read_at: 0,
          created_at: Date.now(),
          event_payload: {},
        }],
        total: 1,
        has_more: false,
      },
    });
    const wrapper = await mountNotifications();
    const row = wrapper.find('.notif-row');
    expect(row.text()).toContain('Critical');
    expect(row.text()).toContain('Quota');
    wrapper.unmount();
  });

  // ── 3. shows unread badge count ──
  it('shows unread count as the first StatCard value', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountNotifications();
    const cards = wrapper.findAllComponents(StatCard);
    expect(cards).toHaveLength(4);
    // First StatCard is "Unread" — 2 unread out of 3
    expect(cards[0].props('value')).toBe(2);
    // Second is "Today" — all 3 within 24h
    expect(cards[1].props('value')).toBe(3);
    // Third is "Warnings" — 1 warning
    expect(cards[2].props('value')).toBe(1);
    // Fourth is "Errors" — 1 error
    expect(cards[3].props('value')).toBe(1);
    wrapper.unmount();
  });

  // ── 4. marks notification as read on click ──
  it('marks a notification as read when the mark-read button is clicked', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountNotifications();
    // First notification is unread → has a mark-read button
    const rows = wrapper.findAll('.notif-row');
    expect(rows[0].classes()).toContain('is-unread');
    const markBtn = rows[0].find('.act-btn.small:not(.danger)');
    expect(markBtn.exists()).toBe(true);
    await markBtn.trigger('click');
    await flushPromises();
    // POST /api/notifications/{id}/read was issued
    expect(global.fetch).toHaveBeenCalledWith(
      '/api/notifications/notif_1/read',
      expect.objectContaining({ method: 'POST' }),
    );
    // The row is no longer unread
    const updatedRows = wrapper.findAll('.notif-row');
    expect(updatedRows[0].classes()).not.toContain('is-unread');
    wrapper.unmount();
  });

  // ── 5. shows empty state when no notifications ──
  it('shows empty state when no notifications are returned', async () => {
    mockFetch({
      '/api/notifications/list': { notifications: [], total: 0, has_more: false },
    });
    const wrapper = await mountNotifications();
    expect(wrapper.findComponent(EmptyState).exists()).toBe(true);
    expect(wrapper.text()).toContain('No notifications');
    expect(wrapper.findAll('.notif-row')).toHaveLength(0);
    wrapper.unmount();
  });

  // ── 6. shows error state when API fails ──
  it('shows error state when the list API fails', async () => {
    global.fetch = vi.fn(() =>
      Promise.resolve({
        ok: false,
        status: 500,
        json: () => Promise.resolve({}),
        text: () => Promise.resolve(''),
      }),
    );
    const wrapper = await mountNotifications();
    expect(wrapper.findComponent(EmptyState).exists()).toBe(true);
    expect(wrapper.text()).toContain('API /api/notifications/list: 500');
    expect(wrapper.findAll('.notif-row')).toHaveLength(0);
    wrapper.unmount();
  });

  // ── 7. opens detail drawer on notification click ──
  it('opens the detail drawer when a notification row is clicked', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountNotifications();
    // DetailDrawer starts closed
    const drawer = wrapper.findComponent(DetailDrawer);
    expect(drawer.exists()).toBe(true);
    expect(drawer.props('open')).toBe(false);
    // Click the first row
    const rows = wrapper.findAll('.notif-row');
    await rows[0].trigger('click');
    expect(drawer.props('open')).toBe(true);
    // Detail body shows the message
    expect(wrapper.text()).toContain('Agent "data-analyst" was deployed successfully');
    wrapper.unmount();
  });

  // ── 8. read-all button works ──
  it('marks all notifications as read when the mark-all-read button is clicked', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountNotifications();
    // Initially 2 unread rows
    expect(wrapper.findAll('.notif-row.is-unread')).toHaveLength(2);
    // The mark-all-read button is the first action in the actions slot
    const buttons = wrapper.findAll('button.act-btn');
    const markAllBtn = buttons.find((b) => b.text().includes('Mark all as read'));
    expect(markAllBtn).toBeTruthy();
    await markAllBtn.trigger('click');
    await flushPromises();
    expect(global.fetch).toHaveBeenCalledWith(
      '/api/notifications/read-all',
      expect.objectContaining({ method: 'POST' }),
    );
    // No row is unread anymore
    expect(wrapper.findAll('.notif-row.is-unread')).toHaveLength(0);
    wrapper.unmount();
  });

  // ── Extra: preferences button opens the modal ──
  it('opens the preferences modal and loads channels', async () => {
    mockFetch({
      ...defaultRoutes(),
      '/api/notifications/preferences': {
        status: 'ok',
        preference: {
          channel_enabled: { email: false, in_app: true },
          event_level_min: {},
        },
      },
    });
    const wrapper = await mountNotifications();
    expect(wrapper.find('.modal-overlay').exists()).toBe(false);
    const buttons = wrapper.findAll('button.act-btn');
    const prefBtn = buttons.find((b) => b.text().includes('Preferences'));
    expect(prefBtn).toBeTruthy();
    await prefBtn.trigger('click');
    await flushPromises();
    expect(wrapper.find('.modal-overlay').exists()).toBe(true);
    const rows = wrapper.find('.pref-table').findAll('tbody tr');
    expect(rows.length).toBe(4);
    const checkboxes = rows.map((r) => r.find('input[type="checkbox"]').element.checked);
    expect(checkboxes[0]).toBe(false); // email disabled on the server
    expect(checkboxes[2]).toBe(true); // in_app enabled

    // Saving must speak the backend contract: {channel_enabled: {...}}.
    const saveBtn = wrapper.findAll('.modal-actions button')
      .find((b) => b.text().includes('Save'));
    expect(saveBtn).toBeTruthy();
    await saveBtn.trigger('click');
    await flushPromises();
    const put = global.fetch.mock.calls.find(
      ([u, init]) => String(u).startsWith('/api/notifications/preferences')
        && (init?.method || 'GET') === 'PUT',
    );
    expect(put).toBeTruthy();
    const body = JSON.parse(put[1].body);
    expect(body.channel_enabled.email).toBe(false);
    expect(body.channel_enabled.in_app).toBe(true);
    expect(body.preferences).toBeUndefined();
    wrapper.unmount();
  });

  // ── Extra: filters by level select ──
  it('filters notifications by level select', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountNotifications();
    expect(wrapper.findAll('.notif-row')).toHaveLength(3);
    // First select in FilterBar is the level filter
    const selects = wrapper.findAll('select');
    expect(selects.length).toBeGreaterThanOrEqual(1);
    await selects[0].setValue('warning');
    expect(wrapper.findAll('.notif-row')).toHaveLength(1);
    expect(wrapper.text()).toContain('Cost threshold reached');
    wrapper.unmount();
  });

  // ── Extra: delete button removes the notification ──
  it('deletes a notification when the delete button is clicked', async () => {
    mockShowConfirm.mockReturnValue(Promise.resolve(true));
    mockFetch(defaultRoutes());
    const wrapper = await mountNotifications();
    expect(wrapper.findAll('.notif-row')).toHaveLength(3);
    const rows = wrapper.findAll('.notif-row');
    const deleteBtn = rows[0].find('button.act-btn.small.danger');
    expect(deleteBtn.exists()).toBe(true);
    await deleteBtn.trigger('click');
    await flushPromises();
    await flushPromises();
    expect(global.fetch).toHaveBeenCalledWith(
      '/api/notifications/notif_1',
      expect.objectContaining({ method: 'DELETE' }),
    );
    expect(wrapper.findAll('.notif-row')).toHaveLength(2);
    wrapper.unmount();
  });

  // ── Extra: Badge components render for level + category ──
  it('renders level and category badges inside each row', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountNotifications();
    const rows = wrapper.findAll('.notif-row');
    expect(rows).toHaveLength(3);
    // Each row has 2 badges (level + category)
    const badgesInRow = rows[0].findAllComponents(Badge);
    expect(badgesInRow.length).toBeGreaterThanOrEqual(2);
    wrapper.unmount();
  });

  // ── 分页参数必须走查询串 ──
  // api.get 的第二个参数是 fetch init（只有 .headers 会被读），以前写成
  // api.get(url, { limit, offset }) ⇒ 参数从没发出，后端按 limit=100 返回，
  // "加载更多"再取一次同一页并 append，整批条目重复。
  function mockPagedFetch() {
    const urls = [];
    const page = (ids, total, hasMore) => ({
      notifications: ids.map((id) => ({
        id, level: 'info', category: 'system', title: id, message: id,
        read: false, created_at: Date.now(), metadata: {},
      })),
      total,
      has_more: hasMore,
    });
    global.fetch = vi.fn((url) => {
      const full = String(url);
      urls.push(full);
      const u = full.split('?')[0];
      const qs = new URLSearchParams(full.split('?')[1] || '');
      const offset = Number(qs.get('offset') || 0);
      let body = {};
      if (u === '/api/notifications/list') {
        body = offset === 0 ? page(['n1', 'n2'], 3, true) : page(['n3'], 3, false);
      }
      return Promise.resolve({
        ok: true, status: 200,
        json: () => Promise.resolve(body),
        text: () => Promise.resolve(JSON.stringify(body)),
      });
    });
    urls.self = urls;
    return urls;
  }

  it('sends limit/offset as query parameters on the first page', async () => {
    const urls = mockPagedFetch();
    const wrapper = await mountNotifications();
    expect(urls).toContain('/api/notifications/list?limit=20&offset=0');
    wrapper.unmount();
  });

  it('advances the offset on load more and never duplicates rows', async () => {
    const urls = mockPagedFetch();
    const wrapper = await mountNotifications();
    expect(wrapper.findAll('.notif-row')).toHaveLength(2);

    const btn = wrapper.findAll('button').find((b) => b.text().includes('Load more'));
    expect(btn).toBeTruthy();
    await btn.trigger('click');
    await flushPromises();
    await flushPromises();

    expect(urls).toContain('/api/notifications/list?limit=20&offset=20');
    const rows = wrapper.findAll('.notif-row');
    expect(rows).toHaveLength(3);
    // 每行同时渲染 title 与 message（都是同一个串），所以按"含 n1 的行数"判定，
    // 而不是按出现次数 —— 后者一行就命中两次。offset 若没发出去，这里会是 2 行 n1。
    expect(rows.filter((r) => r.text().includes('n1'))).toHaveLength(1);
    expect(rows.filter((r) => r.text().includes('n3'))).toHaveLength(1);
    wrapper.unmount();
  });
});