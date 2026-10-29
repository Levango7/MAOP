import { describe, it, expect } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';

/**
 * 「有路由、但用户永远到不了」的守卫。
 *
 * 2026-10-29 实测：`git grep "capability/examples"` 只命中 router/index.js 本身 ——
 * 5.5.0 新增的 4 个页面（agent-versions / credential-vault / long-running-tasks /
 * examples）除了手输 URL 之外没有任何入口，等于没交付。同期还有 3 个成型的 Agent 页
 * （agent-registry / agent-billing / agent-gateway）同样不可达。
 *
 * 这类缺陷不会让任何测试变红：路由能 import、组件能渲染、build 能过，
 * 只是没人能点进去。所以只能把「可达性」本身钉成断言。
 */

const SRC = path.resolve(process.cwd(), 'src');
const ROUTER = path.join(SRC, 'router', 'index.js');
const NAV = path.join(SRC, 'nav.js');

/**
 * 豁免：确实不需要导航入口的路由。
 * 每一条都必须写清理由 —— 无理由的豁免等于把守卫关掉。
 */
const EXEMPT = {
  '/capability/agent-recommend':
    'AgentRecommend.vue 已作为 AgentRegistry 的 recommend tab 嵌入，独立路由是冗余别名，保留只为深链不断',
};

/** 从 router/index.js 取出 { path, redirect } 列表。 */
function parseRoutes() {
  const text = fs.readFileSync(ROUTER, 'utf-8');
  const routes = [];
  // 路由在本文件里都是单行定义，按行取即可（多行会被下面 count 断言暴露）
  for (const line of text.split('\n')) {
    const m = line.match(/path:\s*'([^']+)'/);
    if (!m) continue;
    routes.push({ path: m[1], isRedirect: /redirect:/.test(line) });
  }
  return routes;
}

/** 从 nav.js 取出所有入口路径：to 与 matchPaths（旧路由别名）。 */
function parseNavTargets() {
  const text = fs.readFileSync(NAV, 'utf-8');
  const targets = new Set();
  for (const m of text.matchAll(/to:\s*'([^']+)'/g)) targets.add(m[1]);
  for (const grp of text.matchAll(/matchPaths:\s*\[([^\]]*)\]/g)) {
    for (const m of grp[1].matchAll(/'([^']+)'/g)) targets.add(m[1]);
  }
  return targets;
}

/** 全仓（排除 router 自身与测试）里，是否存在指向 path 的跳转。 */
function linkRefs(pathValue) {
  const patterns = [
    `to="${pathValue}"`,
    `to='${pathValue}'`,
    `push('${pathValue}')`,
    `push("${pathValue}")`,
    `href="${pathValue}"`,
  ];
  const found = [];
  const walk = (dir) => {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        if (entry.name === 'node_modules' || entry.name === '__tests__') continue;
        walk(full);
      } else if (/\.(vue|js)$/.test(entry.name) && full !== ROUTER) {
        const text = fs.readFileSync(full, 'utf-8');
        if (patterns.some((p) => text.includes(p))) {
          found.push(path.relative(SRC, full).replace(/\\/g, '/'));
        }
      }
    }
  };
  walk(SRC);
  return found;
}

/** 结构性路由：不需要入口，也不算缺陷。 */
function isStructural(route) {
  if (route.isRedirect) return true; // 旧路由 301 兼容，如 /tasks → /home/tasks
  if (route.path === '/') return true; // 重定向到 /home
  if (route.path.includes(':')) return true; // 动态段（/share/:token）与 404 兜底
  return false;
}

describe('nav coverage', () => {
  it('every component route is reachable from nav or a link', () => {
    const routes = parseRoutes();
    const targets = parseNavTargets();

    const unreachable = routes
      .filter((r) => !isStructural(r))
      .filter((r) => !targets.has(r.path) && !EXEMPT[r.path])
      .filter((r) => linkRefs(r.path).length === 0)
      .map((r) => r.path);

    expect(
      unreachable,
      `以下路由既不在 nav 的 to/matchPaths 里，也没有任何 to=/push()/href= 指向：\n  ${unreachable.join('\n  ')}`,
    ).toEqual([]);
  });

  it('has no dangling nav entry pointing at a nonexistent route', () => {
    const paths = new Set(parseRoutes().map((r) => r.path));
    const dangling = [...parseNavTargets()].filter((t) => !paths.has(t));
    expect(dangling, `导航项指向了不存在的路由：\n  ${dangling.join('\n  ')}`).toEqual([]);
  });

  it('actually parses a non-trivial number of routes and nav targets', () => {
    // 防止正则失效导致上面两个用例空转变绿 —— 这类守卫最容易死在"永远为真"上
    expect(parseRoutes().length).toBeGreaterThan(60);
    expect(parseNavTargets().size).toBeGreaterThan(50);
  });

  it('every exempt path still exists as a route', () => {
    // 豁免项不能变成"僵尸白名单"：路由被删了就该把豁免也删掉
    const paths = new Set(parseRoutes().map((r) => r.path));
    const stale = Object.keys(EXEMPT).filter((p) => !paths.has(p));
    expect(stale, `豁免清单里有已不存在的路由，请删除对应条目：\n  ${stale.join('\n  ')}`).toEqual([]);
  });
});
