// D1 (2026-07-22, Phase D) — Tests for useWebSocket composable.
//
// jsdom does not implement WebSocket, and we never want real network calls
// in unit tests. The MockWebSocket below simulates:
//   - constructor(url) capturing the URL
//   - readyState transitions (CONNECTING → OPEN → CLOSED)
//   - onopen / onmessage / onclose / onerror callbacks
//   - send(data) capturing outbound payloads
//   - close() transitioning to CLOSED
//
// Because the composable relies on onMounted/onUnmounted, we mount a real
// test component via @vue/test-utils so those lifecycle hooks actually fire.

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { defineComponent, h } from 'vue';
import { mount } from '@vue/test-utils';
import { useWebSocket } from '../composables/useWebSocket.js';

// ── MockWebSocket ──────────────────────────────────────────

class MockWebSocket {
  static instances = [];
  static reset() { MockWebSocket.instances = []; }

  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;

  constructor(url) {
    this.url = url;
    this.readyState = MockWebSocket.CONNECTING;
    this.onopen = null;
    this.onmessage = null;
    this.onclose = null;
    this.onerror = null;
    this.sent = [];
    MockWebSocket.instances.push(this);
  }

  send(data) {
    this.sent.push(data);
  }

  close() {
    this.readyState = MockWebSocket.CLOSED;
    if (this.onclose) this.onclose({ type: 'close' });
  }

  // ── Test helpers (call from test, not from app code) ──────

  _open() {
    this.readyState = MockWebSocket.OPEN;
    if (this.onopen) this.onopen({ type: 'open' });
  }

  _emitMessage(data) {
    if (this.onmessage) this.onmessage({ type: 'message', data });
  }

  _emitError() {
    if (this.onerror) this.onerror({ type: 'error' });
  }

  _closeFromServer() {
    this.readyState = MockWebSocket.CLOSED;
    if (this.onclose) this.onclose({ type: 'close' });
  }
}

// ── withSetup helper ───────────────────────────────────────
// Mounts a real Vue component so onMounted/onUnmounted lifecycle hooks
// fire. Returns { result, unmount }.

function withSetup(composableFn) {
  let result;
  const Comp = defineComponent({
    setup() {
      result = composableFn();
      return () => h('div');
    },
  });
  const wrapper = mount(Comp);
  return { result, unmount: () => wrapper.unmount() };
}

// ── Tests ──────────────────────────────────────────────────

describe('useWebSocket composable', () => {
  let originalWebSocket;
  let originalSetTimeout;

  beforeEach(() => {
    MockWebSocket.reset();
    originalWebSocket = global.WebSocket;
    global.WebSocket = MockWebSocket;
    // Use fake timers so we can control reconnect scheduling.
    originalSetTimeout = global.setTimeout;
  });

  afterEach(() => {
    global.WebSocket = originalWebSocket;
    global.setTimeout = originalSetTimeout;
    vi.restoreAllMocks();
  });

  it('exposes connected/lastMessage/error refs and send/disconnect functions', () => {
    const { result, unmount } = withSetup(() => {
      // Pass a url so connect() doesn't depend on location.
      return useWebSocket('ws://localhost:9999/ws');
    });
    try {
      expect(result).toHaveProperty('connected');
      expect(result).toHaveProperty('lastMessage');
      expect(result).toHaveProperty('error');
      expect(typeof result.send).toBe('function');
      expect(typeof result.disconnect).toBe('function');
      // onMounted fires → connect() ran → WebSocket was constructed.
      expect(MockWebSocket.instances).toHaveLength(1);
      expect(MockWebSocket.instances[0].url).toBe('ws://localhost:9999/ws');
    } finally {
      unmount();
    }
  });

  it('defaults: connected=false, lastMessage=null, error=null', () => {
    const { result, unmount } = withSetup(() => {
      return useWebSocket('ws://localhost:1/ws');
    });
    try {
      expect(result.connected.value).toBe(false);
      expect(result.lastMessage.value).toBeNull();
      expect(result.error.value).toBeNull();
    } finally {
      unmount();
    }
  });

  it('onopen sets connected=true and clears error', () => {
    const { result, unmount } = withSetup(() => {
      return useWebSocket('ws://localhost:2/ws');
    });
    try {
      const ws = MockWebSocket.instances[0];
      result.error.value = new Error('previous');
      ws._open();
      expect(result.connected.value).toBe(true);
      expect(result.error.value).toBeNull();
    } finally {
      unmount();
    }
  });

  it('onmessage parses JSON and sets lastMessage', () => {
    const { result, unmount } = withSetup(() => {
      return useWebSocket('ws://localhost:3/ws');
    });
    try {
      const ws = MockWebSocket.instances[0];
      ws._emitMessage(JSON.stringify({ type: 'update', data: { count: 5 } }));
      expect(result.lastMessage.value).toEqual({ type: 'update', data: { count: 5 } });
    } finally {
      unmount();
    }
  });

  it('onmessage falls back to raw string when payload is not JSON', () => {
    const { result, unmount } = withSetup(() => {
      return useWebSocket('ws://localhost:4/ws');
    });
    try {
      const ws = MockWebSocket.instances[0];
      ws._emitMessage('plain-text-payload');
      expect(result.lastMessage.value).toBe('plain-text-payload');
    } finally {
      unmount();
    }
  });

  it('onclose sets connected=false and schedules reconnect', () => {
    // Use fake timers to detect reconnect scheduling.
    const timers = [];
    global.setTimeout = (fn, delay) => {
      const id = { _fn: fn, _delay: delay };
      timers.push(id);
      return id;
    };
    const { result, unmount } = withSetup(() => {
      return useWebSocket('ws://localhost:5/ws');
    });
    try {
      const ws = MockWebSocket.instances[0];
      ws._open();
      expect(result.connected.value).toBe(true);
      ws._closeFromServer();
      expect(result.connected.value).toBe(false);
      // A reconnect timer should have been scheduled.
      expect(timers.length).toBeGreaterThanOrEqual(1);
    } finally {
      unmount();
    }
  });

  it('onerror sets error and connected=false', () => {
    const { result, unmount } = withSetup(() => {
      return useWebSocket('ws://localhost:6/ws');
    });
    try {
      const ws = MockWebSocket.instances[0];
      ws._open();
      const errEvent = { type: 'error', message: 'connection refused' };
      ws.onerror(errEvent);
      // Vue ref wraps object values in a reactive proxy, so use deep equality.
      expect(result.error.value).toStrictEqual(errEvent);
      expect(result.connected.value).toBe(false);
    } finally {
      unmount();
    }
  });

  it('send() sends string data as-is when ws is OPEN', () => {
    const { result, unmount } = withSetup(() => {
      return useWebSocket('ws://localhost:7/ws');
    });
    try {
      const ws = MockWebSocket.instances[0];
      ws._open();
      result.send('hello');
      expect(ws.sent).toEqual(['hello']);
    } finally {
      unmount();
    }
  });

  it('send() stringifies objects', () => {
    const { result, unmount } = withSetup(() => {
      return useWebSocket('ws://localhost:8/ws');
    });
    try {
      const ws = MockWebSocket.instances[0];
      ws._open();
      result.send({ action: 'ping', ts: 123 });
      expect(ws.sent).toEqual([JSON.stringify({ action: 'ping', ts: 123 })]);
    } finally {
      unmount();
    }
  });

  it('send() is a no-op when ws is not OPEN', () => {
    const { result, unmount } = withSetup(() => {
      return useWebSocket('ws://localhost:9/ws');
    });
    try {
      const ws = MockWebSocket.instances[0];
      // ws.readyState is still CONNECTING (not OPEN) → send should be a no-op.
      result.send('should-not-send');
      expect(ws.sent).toEqual([]);
    } finally {
      unmount();
    }
  });

  it('disconnect() clears reconnect timer and closes ws', () => {
    const timers = [];
    global.setTimeout = (fn, delay) => {
      const id = { _fn: fn, _delay: delay, _cleared: false };
      timers.push(id);
      return id;
    };
    global.clearTimeout = (id) => {
      if (id) id._cleared = true;
    };
    const { result, unmount } = withSetup(() => {
      return useWebSocket('ws://localhost:10/ws');
    });
    try {
      const ws = MockWebSocket.instances[0];
      ws._open();
      // Trigger a reconnect schedule by closing from server.
      ws._closeFromServer();
      const scheduledTimer = timers[timers.length - 1];
      expect(scheduledTimer).toBeDefined();
      // Now call disconnect() — should clear the timer and close ws.
      result.disconnect();
      expect(result.connected.value).toBe(false);
      expect(scheduledTimer._cleared).toBe(true);
    } finally {
      unmount();
    }
  });

  it('disconnect() is safe to call when ws is null', () => {
    const { result, unmount } = withSetup(() => {
      return useWebSocket('ws://localhost:11/ws');
    });
    try {
      // disconnect() should not throw even if called multiple times.
      result.disconnect();
      result.disconnect();
      expect(result.connected.value).toBe(false);
    } finally {
      unmount();
    }
  });

  it('onUnmounted auto-disconnects', () => {
    const { unmount } = withSetup(() => {
      return useWebSocket('ws://localhost:12/ws');
    });
    const ws = MockWebSocket.instances[0];
    ws._open();
    unmount();
    // After unmount, ws should have been closed (readyState = CLOSED).
    expect(ws.readyState).toBe(MockWebSocket.CLOSED);
  });
});
