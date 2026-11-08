import { describe, it, expect } from 'vitest';
import { createRouter, createMemoryHistory } from 'vue-router';
import { nextTick } from 'vue';
import { useRouteLoadingBar } from '../composables/useMaopEffects.js';

// ── 路由懒加载进度条（2026-11-08 P2-3 修复的钉子）──────────────────────
//
// 背景：router 有 55 处动态 import()（路由级分包）。首次进入某路由时 chunk
// 未到位，`<router-view>` 整块空白 4–10 秒——用户看到的是"页面坏了"而不是
// "在加载"（真机截图证据见 docs/audits/frontend-runtime-spotcheck-2026-11-08.md）。
//
// 值得记的是缺陷形态：MaopLoadingBar.vue、全局事件 maop-loading-start/stop、
// useLoadingBar() 三样**都已存在**，组件也确实渲染在 App.vue 里，缺的只是
// "谁在导航时触发它"——全仓零调用点，属零件齐全的死接线。所以这组测试不只
// 钉"进度条会亮"，更钉"**导航真的会触发它**"与"**看完真的会灭**"。

// 两条静态路由即可：不需要真实懒加载，因为被测对象是"导航→事件"这段接线，
// 而不是 chunk 本身加载要多久（那是 vite 的事）。
function makeRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'home', component: { template: '<div>home</div>' } },
      { path: '/run', name: 'run', component: { template: '<div>run</div>' } },
      { path: '/memory', name: 'memory', component: { template: '<div>memory</div>' } },
    ],
  });
}

// 精确移除：必须用与注册时同源的 handler 引用（匿名箭头函数Remove不掉）。
function watchLoadingEvents(sink) {
  const onStart = () => sink.push('start');
  const onStop = () => sink.push('stop');
  window.addEventListener('maop-loading-start', onStart);
  window.addEventListener('maop-loading-stop', onStop);
  return () => {
    window.removeEventListener('maop-loading-start', onStart);
    window.removeEventListener('maop-loading-stop', onStop);
  };
}

describe('useRouteLoadingBar — 导航触发顶部加载条', () => {
  it('跨路径导航：先进度条亮，后灭', async () => {
    const router = makeRouter();
    const seen = [];
    const unwatch = watchLoadingEvents(seen);
    const remove = useRouteLoadingBar(router);

    await router.push('/');
    await nextTick();
    seen.length = 0; // 忽略初始导航

    await router.push('/run');
    await nextTick();
    expect(seen).toContain('start');
    expect(seen.indexOf('start')).toBeLessThan(seen.indexOf('stop'));
    // 关键回归：guard 必须**不拦截**导航。返回任何非 undefined 的值都会被
    // vue-router 当成"重定向/中止"，那会把整个应用导航搞死——比懒加载白屏
    // 严重得多。这里用路由结果本身证明导航真的走完了。
    expect(router.currentRoute.value.path).toBe('/run');

    unwatch();
    remove();
  });

  it('同一路径的重复导航不闪进度条', async () => {
    const router = makeRouter();
    const seen = [];
    const unwatch = watchLoadingEvents(seen);
    const remove = useRouteLoadingBar(router);

    await router.push('/run');
    await nextTick();
    seen.length = 0;

    // 只改 query：fullPath 不同 ⇒ 仍算导航（用户感知上确实换了内容）
    await router.push('/run?tab=2');
    await nextTick();
    expect(seen).toContain('start');

    unwatch();
    remove();
  });

  it('完全相同的 fullPath 不触发（点当前页链接不该闪）', async () => {
    const router = makeRouter();
    const seen = [];
    const unwatch = watchLoadingEvents(seen);
    const remove = useRouteLoadingBar(router);

    await router.push('/run');
    await nextTick();
    seen.length = 0;

    await router.push('/run');
    await nextTick();
    expect(seen).not.toContain('start');

    unwatch();
    remove();
  });

  it('onUnmounted 后导航不再触发（guard 已摘除，无泄漏）', async () => {
    const router = makeRouter();
    const seen = [];
    const unwatch = watchLoadingEvents(seen);
    const remove = useRouteLoadingBar(router);

    await router.push('/');
    await nextTick();
    seen.length = 0;

    remove();
    await router.push('/memory');
    await nextTick();
    expect(seen).not.toContain('start');

    unwatch();
  });

  it('chunk 加载失败（router.onError）也会灭条，不会永远转', async () => {
    const router = makeRouter();
    const seen = [];
    const unwatch = watchLoadingEvents(seen);
    const remove = useRouteLoadingBar(router);

    await router.push('/');
    await nextTick();
    seen.length = 0;

    // 模拟异步组件加载失败：vue-router 在懒加载 chunk 404 时走 onError。
    router.options.routes[1].component = () => Promise.reject(new Error('chunk 404'));
    await router.push('/run').catch(() => {});
    await nextTick();

    expect(seen).toContain('start');
    expect(seen[seen.length - 1]).toBe('stop');

    unwatch();
    remove();
  });
});
