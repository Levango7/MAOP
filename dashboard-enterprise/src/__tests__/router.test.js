import { describe, it, expect } from 'vitest';
import router from '../router/index.js';

describe('Router', () => {
  it('has all expected routes', () => {
    const names = router.getRoutes().map((r) => r.name);
    const expected = [
      'overview', 'run', 'agents', 'memory', 'evolve',
      'search', 'vector', 'tools', 'models',
      'logs', 'monitor',
      'observability',
      'cost', 'audit', 'rbac', 'tenants', 'settings',
      'users', 'docs',
      'knowledge-graph',  // v4.5.0: knowledge graph visualization
    ];
    for (const name of expected) {
      expect(names).toContain(name);
    }
  });

  it('overview route is the root path', () => {
    const route = router.getRoutes().find((r) => r.name === 'overview');
    expect(route?.path).toBe('/');
  });

  it('has 24 route records total', () => {
    // 拓扑: 19 个命名业务路由 + 3 个匿名重定向(/control /chat /evolution-history)
    // + 1 个 /run 命名路由 + 1 个 /:pathMatch(.*)* 兜底
    expect(router.getRoutes().length).toBe(24);
  });

  it('legacy routes declare redirects to merged pages', () => {
    // vue-router resolve() 只返回输入 path;重定向配置在 router.options.routes 的
    // redirect 字段上。直接断言配置对象,避免对 resolve 的行为产生误导性期待。
    const routes = router.options.routes;
    const byPath = Object.fromEntries(routes.filter((r) => r.path).map((r) => [r.path, r]));
    const target = (p) => {
      const r = byPath[p]?.redirect;
      // redirect 可能是对象 { path, query } 或字符串;统一取 path 部分
      return typeof r === 'string' ? r : (r?.path || '');
    };
    expect(target('/control')).toBe('/run');
    expect(target('/chat')).toBe('/run');
    expect(target('/evolution-history')).toBe('/evolve');
  });

  it('knowledge-graph route has no enterprise guard', () => {
    // v4.5.0: /knowledge-graph is general-availability (spec 5.3.1 rule 1)
    const route = router.getRoutes().find((r) => r.name === 'knowledge-graph');
    expect(route?.path).toBe('/knowledge-graph');
    expect(route?.meta?.requiresEnterprise).toBeFalsy();
  });
});