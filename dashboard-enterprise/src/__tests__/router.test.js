import { describe, it, expect } from 'vitest';
import router from '../router/index.js';

// ── 2026-09-01 信息架构重设计（5 组任务流导航）─────────────────────
// 断言更新为新结构：5 组顶层路径（/home /run /memory /capability /operate）
// + 设置组 + 企业管理组 + 全部旧路径 301 重定向（深链兼容不断）。

describe('Router', () => {
  it('has all expected named routes (IA-2026-09)', () => {
    const names = router.getRoutes().map((r) => r.name);
    const expected = [
      // 首页组
      'overview', 'tasks',
      // 执行组
      'run', 'dispatch',
      // 记忆组
      'memory', 'search', 'knowledge-graph',
      // 能力组
      'agents', 'skills', 'skill-market', 'models',
      // 运维组
      'monitor', 'logs', 'observability', 'cost',
      // 企业管理组
      'audit', 'rbac', 'tenants', 'users',
      'licenses', 'sso', 'quotas', 'apikeys',
      // 设置组
      'settings', 'notifications', 'docs',
      // 保留直达（导航不罗列但深链可达）
      'workflow-editor', 'evolve',
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
    expect(target('/evolution-history')).toBe('/evolve');
  });

  it('knowledge-graph route has no enterprise guard', () => {
    const route = router.getRoutes().find((r) => r.name === 'knowledge-graph');
    expect(route?.path).toBe('/memory/graph');
    expect(route?.meta?.requiresEnterprise).toBeFalsy();
  });
});
