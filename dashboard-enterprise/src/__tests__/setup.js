// Vitest setup file - ensure localStorage is available and functional

import { config as testConfig } from '@vue/test-utils';
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

// 2026-09-01: 产品默认语言改为 zh（用户需求），但既有测试断言的文案
// 是英文。在测试环境显式 pin en，保持断言语义不变；i18n 的 zh 词典
// 由专门的词典覆盖测试（zh 键与 en 键一一对应）保证。
try { localStorage.setItem('maop_locale', 'en'); } catch { /* ignore */ }
