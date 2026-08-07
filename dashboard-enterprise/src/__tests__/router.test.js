import { describe, it, expect } from 'vitest';
import router from '../router/index.js';

describe('Router', () => {
  it('has all expected routes', () => {
    const names = router.getRoutes().map((r) => r.name);
    const expected = [
      'overview', 'control', 'chat', 'agents', 'memory', 'evolve',
      'search', 'vector', 'tools', 'models',
      'logs', 'monitor', 'cost', 'audit', 'rbac', 'tenants', 'settings',
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

  it('has 21 routes total', () => {
    // 20 业务路由 (incl. v4.5.0 /knowledge-graph) + 1 个 catch-all 重定向 (:pathMatch(.*)*)
    expect(router.getRoutes().length).toBe(21);
  });

  it('knowledge-graph route has no enterprise guard', () => {
    // v4.5.0: /knowledge-graph is general-availability (spec 5.3.1 rule 1)
    const route = router.getRoutes().find((r) => r.name === 'knowledge-graph');
    expect(route?.path).toBe('/knowledge-graph');
    expect(route?.meta?.requiresEnterprise).toBeFalsy();
  });
});