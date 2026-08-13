// Tests for SsoProviders.vue — IdP 列表渲染、协议标签、空/错误态、
// 添加/编辑对话框、协议切换、SAML Metadata 按钮可见性、删除流程。
//
// SsoProviders.onMounted 调用 load() 命中 /api/v1/sso/providers。我们 mock
// global.fetch, stub PageHeader（依赖 useRoute）, 然后断言渲染输出。

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { mount, flushPromises } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';
import SsoProviders from '../views/SsoProviders.vue';

// PageHeader 调用 useRoute(), 需要路由上下文; stub 成透传 slot 的占位组件,
// 以便 #badges / #actions slot 内的按钮能渲染并被测试触发。
const mountOptions = {
  global: { stubs: { PageHeader: { name: 'PageHeader', template: '<slot name="badges" /><slot />' } } },
};

describe('SsoProviders.vue', () => {
  let originalFetch;
  let originalConfirm;

  beforeEach(() => {
    setActivePinia(createPinia());
    originalFetch = global.fetch;
    originalConfirm = window.confirm;
    // 标记 vitest 环境, 避免 handleUnauthorized 触发 CustomEvent
    global.__VITEST__ = true;
    if (typeof window !== 'undefined') window.__VITEST__ = true;
  });

  afterEach(() => {
    global.fetch = originalFetch;
    window.confirm = originalConfirm;
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

  function defaultProviders() {
    return [
      {
        id: 1,
        name: 'Corporate Azure AD',
        protocol: 'oidc',
        tenant_id: '',
        enabled: true,
        auto_redirect: false,
        config: {
          client_id: 'azure-client',
          authorize_url: 'https://login.microsoftonline.com/authorize',
          token_url: 'https://login.microsoftonline.com/token',
          redirect_uri: 'https://maop.example.com/api/v1/sso/oidc/1/callback',
          scopes: ['openid', 'profile', 'email'],
          use_pkce: true,
        },
        attribute_mapping: { external_id: 'sub', email: 'email', display_name: 'name', roles: 'groups' },
        created_at: 1723536000,
        updated_at: 1723536000,
      },
      {
        id: 2,
        name: 'Keycloak SAML',
        protocol: 'saml',
        tenant_id: 'acme',
        enabled: false,
        auto_redirect: false,
        config: {
          sp_entity_id: 'maop-sp',
          entity_id: 'keycloak-idp',
          sso_url: 'https://keycloak.example.com/saml',
          acs_url: 'https://maop.example.com/api/v1/sso/saml/2/acs',
          want_signed: true,
        },
        attribute_mapping: { external_id: 'sub', email: 'email', display_name: 'name', roles: 'groups' },
        created_at: 1723622400,
        updated_at: 1723622400,
      },
    ];
  }

  function defaultRoutes(overrides = {}) {
    return {
      '/api/v1/sso/providers': { status: 'ok', providers: defaultProviders(), count: 2, total: 2 },
      ...overrides,
    };
  }

  async function mountView() {
    const wrapper = mount(SsoProviders, mountOptions);
    // load() 在 onMounted 中触发, 两次 flushPromises 让 fetch + finally 落定
    await flushPromises();
    await flushPromises();
    return wrapper;
  }

  // ── 列表渲染 ──────────────────────────────────────────────

  it('renders the sso-page root element', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountView();
    expect(wrapper.find('.sso-page').exists()).toBe(true);
    wrapper.unmount();
  });

  it('renders a table row per provider', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountView();
    const rows = wrapper.findAll('.sso-row');
    // 1 head + 2 data rows
    expect(rows.length).toBe(3);
    wrapper.unmount();
  });

  it('shows provider names in the table', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountView();
    expect(wrapper.text()).toContain('Corporate Azure AD');
    expect(wrapper.text()).toContain('Keycloak SAML');
    wrapper.unmount();
  });

  it('shows tenant label for scoped provider and Global for unscoped', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountView();
    expect(wrapper.text()).toContain('acme');
    // 全局租户显示 "Global" (en locale default)
    expect(wrapper.text()).toContain('Global');
    wrapper.unmount();
  });

  // ── 空态 / 错误态 ─────────────────────────────────────────

  it('renders empty state when no providers returned', async () => {
    mockFetch(defaultRoutes({ '/api/v1/sso/providers': { status: 'ok', providers: [], count: 0, total: 0 } }));
    const wrapper = await mountView();
    // 空态时不渲染数据行（只有 head 行或完全无表格行）
    const dataRows = wrapper.findAll('.sso-row:not(.sso-row--head)');
    expect(dataRows.length).toBe(0);
    wrapper.unmount();
  });

  it('sets error message when providers API fails', async () => {
    global.fetch = vi.fn(() =>
      Promise.resolve({
        ok: false,
        status: 500,
        json: () => Promise.resolve({}),
        text: () => Promise.resolve(''),
      })
    );
    const wrapper = await mountView();
    // 错误态: ListPageLayout 渲染 EmptyState with error 文本
    expect(wrapper.text()).toContain('API /api/v1/sso/providers: 500');
    wrapper.unmount();
  });

  // ── 添加对话框 ────────────────────────────────────────────

  it('opens the add-provider dialog with OIDC form by default', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountView();
    // 初始无对话框
    expect(wrapper.find('.sso-dialog').exists()).toBe(false);

    // 点击"添加"按钮（在 #actions slot 中, .btn--primary）
    const addBtn = wrapper.find('.btn--primary');
    expect(addBtn.exists()).toBe(true);
    await addBtn.trigger('click');

    // 对话框出现, 默认 OIDC fieldset
    expect(wrapper.find('.sso-dialog').exists()).toBe(true);
    expect(wrapper.find('fieldset.sso-fieldset legend').exists()).toBe(true);
    expect(wrapper.text()).toContain('OpenID Connect');
    wrapper.unmount();
  });

  it('switches to SAML form when protocol select changes to saml', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountView();
    const addBtn = wrapper.find('.btn--primary');
    await addBtn.trigger('click');

    // 协议 select 是对话框中第二个 select（第一个是协议，后续可能有 NameID Format select）
    const selects = wrapper.findAll('select.sso-input');
    // 找到协议 select（包含 oidc/saml 选项）
    const protocolSelect = selects.find((s) => {
      const opts = s.findAll('option');
      return opts.some((o) => o.attributes('value') === 'oidc') && opts.some((o) => o.attributes('value') === 'saml');
    });
    expect(protocolSelect).toBeTruthy();
    await protocolSelect.setValue('saml');

    // SAML 表单应显示 SP Entity ID 字段
    expect(wrapper.text()).toContain('SP Entity ID');
    expect(wrapper.text()).toContain('IdP SSO URL');
    wrapper.unmount();
  });

  // ── 编辑对话框 ────────────────────────────────────────────

  it('opens edit dialog populated with provider config', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountView();

    // 第一行（OIDC provider, id=1）的编辑按钮是第 3 个 btn-icon（test, toggle, edit, [metadata?], delete）
    const rows = wrapper.findAll('.sso-row:not(.sso-row--head)');
    const oidcRow = rows[0];
    const editBtn = oidcRow.findAll('.btn-icon')[2]; // edit = 第 3 个
    await editBtn.trigger('click');

    expect(wrapper.find('.sso-dialog').exists()).toBe(true);
    // 编辑 OIDC provider: 表单应含已加载的名称
    expect(wrapper.text()).toContain('Corporate Azure AD');
    wrapper.unmount();
  });

  // ── SAML Metadata 按钮可见性 ──────────────────────────────

  it('shows metadata export button only for SAML providers', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountView();
    const rows = wrapper.findAll('.sso-row:not(.sso-row--head)');

    // OIDC 行（id=1）: 操作按钮为 test, toggle, edit, delete（无 metadata）
    const oidcRowIcons = rows[0].findAll('.btn-icon');
    // SAML 行（id=2）: 操作按钮为 test, toggle, edit, metadata, delete
    const samlRowIcons = rows[1].findAll('.btn-icon');

    expect(samlRowIcons.length).toBeGreaterThan(oidcRowIcons.length);
    // SAML 行比 OIDC 行多一个按钮（metadata）
    expect(samlRowIcons.length - oidcRowIcons.length).toBe(1);
    wrapper.unmount();
  });

  // ── 删除流程 ──────────────────────────────────────────────

  it('deletes a provider after confirm and reloads the list', async () => {
    mockFetch({
      '/api/v1/sso/providers': { status: 'ok', providers: defaultProviders(), count: 2, total: 2 },
    });
    window.confirm = () => true;

    const wrapper = await mountView();
    expect(wrapper.findAll('.sso-row:not(.sso-row--head)').length).toBe(2);

    // 让 DELETE 返回成功, 之后 list 返回剩余 1 个
    mockFetch({
      '/api/v1/sso/providers': {
        status: 'ok',
        providers: [defaultProviders()[0]],
        count: 1,
        total: 1,
      },
    });

    // 点击第一行的删除按钮（最后一个 btn-icon--danger）
    const rows = wrapper.findAll('.sso-row:not(.sso-row--head)');
    const deleteBtn = rows[0].find('.btn-icon--danger');
    await deleteBtn.trigger('click');
    await flushPromises();
    await flushPromises();

    // 列表刷新后只剩 1 行
    expect(wrapper.findAll('.sso-row:not(.sso-row--head)').length).toBe(1);
    wrapper.unmount();
  });

  it('does not delete when confirm is cancelled', async () => {
    mockFetch(defaultRoutes());
    window.confirm = () => false;

    const wrapper = await mountView();
    const rows = wrapper.findAll('.sso-row:not(.sso-row--head)');
    const deleteBtn = rows[0].find('.btn-icon--danger');
    await deleteBtn.trigger('click');
    await flushPromises();

    // 未删除: 仍 2 行
    expect(wrapper.findAll('.sso-row:not(.sso-row--head)').length).toBe(2);
    wrapper.unmount();
  });

  // ── 协议标签 ──────────────────────────────────────────────

  it('renders protocol badges for oidc and saml providers', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountView();
    const text = wrapper.text();
    // 协议标签来自 i18n: view.sso.protocol.oidc = 'OIDC', view.sso.protocol.saml = 'SAML 2.0'
    expect(text).toContain('OIDC');
    expect(text).toContain('SAML 2.0');
    wrapper.unmount();
  });
});