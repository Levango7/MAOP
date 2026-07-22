import { describe, it, expect } from 'vitest';
import router from '../router/index.js';

describe('Router', () => {
  it('has all expected routes', () => {
    const names = router.getRoutes().map((r) => r.name);
    const expected = [
      'overview', 'control', 'chat', 'agents', 'memory', 'evolve',
      'search', 'vector', 'tools', 'models',
      'logs', 'monitor', 'cost', 'audit', 'rbac', 'tenants', 'settings',
    ];
    for (const name of expected) {
      expect(names).toContain(name);
    }
  });

  it('overview route is the root path', () => {
    const route = router.getRoutes().find((r) => r.name === 'overview');
    expect(route?.path).toBe('/');
  });

  it('has 17 routes total', () => {
    expect(router.getRoutes().length).toBe(17);
  });
});