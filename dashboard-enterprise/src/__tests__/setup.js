// Vitest setup file - ensure localStorage is available and functional
import { vi } from 'vitest';
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
