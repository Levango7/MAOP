import { describe, it, expect, vi } from 'vitest';
import { createPinia, setActivePinia } from 'pinia';
import router from '../router/index.js';
import { useEditionStore } from '../stores/edition.js';

// ── 2026-09-01 信息架构重设计（5 组任务流导航）─────────────────────
// 断言更新为新结构：5 组顶层路径（/home /run /memory /capability /operate）
// + 设置组 + 企业管理组 + 全部旧路径 301 重定向（深链兼容不断）。
// ── IA 扩展：新增 13 管理页面 + workflow/evolve 归组 + analysis/feedback ──

describe('Router', () => {
  it('has all expected named routes (IA-2026-09)', () => {
    const names = router.getRoutes().map((r) => r.name);
    const expected = [
      // 首页组
      'overview', 'tasks',
      // 执行组（含 workflow 归组）
      'run', 'dispatch', 'workflow',
      // 记忆组
      'memory', 'search', 'knowledge-graph',
      // 能力组（含 evolve 归组 + 8 扩展页）
      'agents', 'skills', 'skill-market', 'models', 'evolve',
      'mcp', 'plugins', 'subagents', 'routing-rules', 'scheduling', 'protocols', 'worktrees', 'agent-proxy',
      // 运维组（含 5 扩展页）
      'monitor', 'logs', 'observability', 'cost',
      'debate', 'blackboard', 'alert-rules', 'webhooks', 'integrations',
      // 分析与反馈组（IA 新增）
      'analysis', 'feedback',
      // 企业管理组
      'audit', 'rbac', 'tenants', 'users',
      'licenses', 'sso', 'quotas', 'apikeys',
      // 设置组
      'settings', 'notifications', 'docs',
    ];
    for (const name of expected) {
      expect(names).toContain(name);
    }
  });

  it('overview lives under /home (IA root)', () => {
    const route = router.getRoutes().find((r) => r.name === 'overview');
    expect(route?.path).toBe('/home');
  });

  it('legacy paths redirect into the 5 groups (deep-link compat)', () => {
    const routes = router.options.routes;
    const byPath = Object.fromEntries(routes.filter((r) => r.path).map((r) => [r.path, r]));
    const target = (p) => {
      const r = byPath[p]?.redirect;
      return typeof r === 'string' ? r : (r?.path || '');
    };
    // 旧根 → 首页
    expect(target('/')).toBe('/home');
    // 旧扁平路径 → 新组内路径
    expect(target('/agents')).toBe('/capability/agents');
    expect(target('/tasks')).toBe('/home/tasks');
    expect(target('/search')).toBe('/memory/search');
    expect(target('/vector')).toBe('/memory/search');
    expect(target('/knowledge-graph')).toBe('/memory/graph');
    expect(target('/tools')).toBe('/capability/skills');
    expect(target('/models')).toBe('/capability/models');
    expect(target('/monitor')).toBe('/operate');
    expect(target('/observability')).toBe('/operate/tracing');
    expect(target('/cost')).toBe('/operate/cost');
    expect(target('/skill-market')).toBe('/capability/market');
    expect(target('/skill-editor')).toBe('/capability/skills');
    // 合并类重定向（带 query 的）
    expect(target('/control')).toBe('/run');
    expect(target('/chat')).toBe('/run');
    // IA 扩展: evolve 归入能力组 → /capability/evolve
    expect(target('/evolution-history')).toBe('/capability/evolve');
    expect(target('/evolve')).toBe('/capability/evolve');
    // IA 扩展: workflow-editor 归入执行组 → /run/workflow
    expect(target('/workflow-editor')).toBe('/run/workflow');
  });

  it('knowledge-graph route has no enterprise guard', () => {
    const route = router.getRoutes().find((r) => r.name === 'knowledge-graph');
    expect(route?.path).toBe('/memory/graph');
    expect(route?.meta?.requiresEnterprise).toBeFalsy();
  });

  it('gates the notification inbox by capability and its config page by edition', () => {
    // 收件箱的读端点任何已登录用户都能用（notifications router docstring），
    // 所以它只能按"端点在不在"挡，不能按 edition 挡 —— 企业版的普通用户
    // 拿不到 /api/info/edition，按 edition 判断会把他该用的入口也藏掉。
    // 配置页的端点全部 require_admin，继续按企业版守卫。
    const routes = router.getRoutes();
    const inbox = routes.find((r) => r.name === 'notifications');
    const config = routes.find((r) => r.name === 'notification-config');
    expect(inbox?.meta?.requiresCapability).toBe('notifications');
    expect(inbox?.meta?.requiresEnterprise).toBeFalsy();
    expect(config?.meta?.requiresEnterprise).toBe(true);
  });

  it('redirects the inbox home only when the probe says the engine is missing', async () => {
    // 未知（null）放行 —— 探针还没回就凭猜测挡深链，等于把用户能走的路改掉。
    setActivePinia(createPinia());
    const store = useEditionStore();
    // 守卫在"未知"时会补一次探针；这里把 fetch 钉成"端点在"，
    // 既不打真网络，也不让那次补测改变结论。
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: true, status: 200, json: () => Promise.resolve({}),
    });
    try {
      store.notificationsAvailable = null;
      await router.push('/notifications');
      expect(router.currentRoute.value.path).toBe('/notifications');

      store.notificationsAvailable = false;
      await router.push('/home');
      await router.push('/notifications');
      expect(router.currentRoute.value.path).toBe('/home');

      store.notificationsAvailable = true;
      await router.push('/home');
      await router.push('/notifications');
      expect(router.currentRoute.value.path).toBe('/notifications');
    } finally {
      fetchSpy.mockRestore();
      await router.push('/home');
    }
  });

  it('redirects integrations home only when the n8n probe says the router is missing', async () => {
    // 同一套能力语义：404 才挡，未知（含非 admin 的 403）放行。
    setActivePinia(createPinia());
    const store = useEditionStore();
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: true, status: 200, json: () => Promise.resolve({}),
    });
    try {
      store.n8nAvailable = null;
      await router.push('/operate/integrations');
      expect(router.currentRoute.value.path).toBe('/operate/integrations');

      store.n8nAvailable = false;
      await router.push('/home');
      await router.push('/operate/integrations');
      expect(router.currentRoute.value.path).toBe('/home');

      store.n8nAvailable = true;
      await router.push('/home');
      await router.push('/operate/integrations');
      expect(router.currentRoute.value.path).toBe('/operate/integrations');
    } finally {
      fetchSpy.mockRestore();
      await router.push('/home');
    }
  });
});
