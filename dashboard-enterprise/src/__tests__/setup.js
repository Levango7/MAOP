// Vitest setup file - ensure localStorage is available and functional

import { config as testConfig } from '@vue/test-utils';
import { vi } from 'vitest';
import { vModalA11y } from '../directives/modalA11y.js';

// Create a functional localStorage mock
const createLocalStorageMock = () => {
  let store = {};
  return {
    getItem: (key) => store[key] ?? null,
    setItem: (key, value) => { store[key] = String(value); },
    removeItem: (key) => { delete store[key]; },
    clear: () => { store = {}; },
    get length() { return Object.keys(store).length; },
    key: (i) => Object.keys(store)[i] ?? null,
  };
};

// Always override localStorage with our mock to ensure it works
Object.defineProperty(globalThis, 'localStorage', {
  value: createLocalStorageMock(),
  writable: true,
  configurable: true,
});

// Also set on window if it exists
if (typeof window !== 'undefined') {
  Object.defineProperty(window, 'localStorage', {
    value: globalThis.localStorage,
    writable: true,
    configurable: true,
  });
}

// Register global directives (mirrors main.js so tests don't emit
// "Failed to resolve directive" warnings for v-modal-a11y)
testConfig.global.directives = {
  ...testConfig.global.directives,
  'modal-a11y': vModalA11y,
};

// ── Canvas mock ───────────────────────────────────────────────────
// jsdom does not implement HTMLCanvasElement.getContext, which breaks
// vue-chartjs / Chart.js (Overview.vue, Audit.vue charts) at runtime:
//   Error: Not implemented: HTMLCanvasElement.prototype.getContext
// Provide a 2D context stub so chart components can mount in tests.
const makeCanvas2DContext = () => {
  const ctx = {
    canvas: { width: 300, height: 150 },
    measureText: () => ({ width: 0 }),
    // Chart.js calls many 2D context methods; route unknown ones to no-ops
    // instead of throwing "not implemented" from jsdom.
  };
  return new Proxy(ctx, {
    get(target, prop) {
      if (prop in target) return target[prop];
      if (typeof prop === 'string') {
        target[prop] = () => {};
        return target[prop];
      }
      return undefined;
    },
  });
};

if (typeof HTMLCanvasElement !== 'undefined') {
  // jsdom 已实现 getContext 但会抛 "Not implemented"（HTMLCanvasElement-impl.js），
  // 因此不能以"是否已存在"为守卫，必须强制覆盖。
  Object.defineProperty(HTMLCanvasElement.prototype, 'getContext', {
    configurable: true,
    writable: true,
    value: function getContext() {
      return makeCanvas2DContext();
    },
  });
}

// ── vue-chartjs mock ──────────────────────────────────────────────
// vue-chartjs 的 Line/Bar/Pie 等组件由 createTypedChart() 生成，组件对象没有
// name/__name 字段，Vue Test Utils 的字符串 stub 键（{ Line: true }）匹配不到，
// 导致真实 Chart 实例仍被创建。chart.js 在 jsdom 下创建真实图表时会：
//   1. new Chart() 时抛 "can't acquire context from the given item"（stderr）
//   2. 组件卸载后异步 resize 触发 getComputedStyle(null) → unhandled rejection
// 因此这里全局 mock vue-chartjs，把图表组件替换为无副作用 stub。
vi.mock('vue-chartjs', () => {
  const ChartStub = {
    name: 'ChartStub',
    inheritAttrs: false,
    template: '<div class="chart-stub" />',
  };
  return {
    Line: ChartStub,
    Bar: ChartStub,
    Pie: ChartStub,
    Doughnut: ChartStub,
    Radar: ChartStub,
    Scatter: ChartStub,
    Bubble: ChartStub,
    PolarArea: ChartStub,
    default: ChartStub,
  };
});

