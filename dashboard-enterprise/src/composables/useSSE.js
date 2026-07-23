// t21 (2026-07-21) — SSE composable for real-time server push.
//
// Wraps the native EventSource API with:
//   - Automatic reconnection with exponential backoff (1s → 2s → 4s → ... → 30s cap)
//   - Event-type filtering (subscribe to specific named events, not just "message")
//   - Automatic JSON parsing of data payloads (falls back to raw string)
//   - Vue lifecycle integration (onMounted connects, onUnmounted closes)
//   - `lastEvent` ref holding the most recent decoded payload
//   - `connected` ref reflecting readyState
//   - `error` ref capturing the last EventSource error event
//
// Usage:
//   import { useSSE } from '../composables/useSSE.js';
//   const { connected, lastEvent, error, close } = useSSE({
//     url: '/api/stream',              // default '/api/stream'
//     events: ['state'],               // default: [] (all events / 'message')
//     autoReconnect: true,             // default true
//     maxBackoffMs: 30_000,            // default 30000
//   });
//
// Two backend SSE endpoints are supported:
//   - GET /api/stream            (provider.py:306) — global state push, event="state"
//   - GET /api/stream/{trace_id} (routers/stream.py:22) — per-execution output,
//                                                          requires admin token
//
// The composable automatically injects the auth token (if present in
// localStorage['maop_token']) as a query parameter because EventSource does
// not support custom headers. The backend reads the token from the query
// string for SSE endpoints.

import { ref, onMounted, onUnmounted, getCurrentInstance } from 'vue';

const AUTH_QUERY_KEY = 'token';
const DEFAULT_URL = '/api/stream';
const DEFAULT_MAX_BACKOFF_MS = 30_000;
const INITIAL_BACKOFF_MS = 1_000;
const DEFAULT_MAX_RETRIES = 10; // P1 fix: stop reconnecting after this many consecutive failures

function _getAuthToken() {
  try {
    return localStorage.getItem('maop_token') || '';
  } catch {
    return '';
  }
}

function _buildUrl(url, withAuth) {
  if (!withAuth) return url;
  const token = _getAuthToken();
  if (!token) return url;
  const sep = url.includes('?') ? '&' : '?';
  return `${url}${sep}${AUTH_QUERY_KEY}=${encodeURIComponent(token)}`;
}

/**
 * Subscribe to a Server-Sent Events endpoint.
 *
 * @param {object} opts
 * @param {string} [opts.url='/api/stream']      SSE endpoint URL.
 * @param {string[]} [opts.events=[]]           Named events to subscribe to
 *                                              (empty = 'message' only).
 * @param {boolean} [opts.autoReconnect=true]   Reconnect on close/error.
 * @param {number} [opts.maxBackoffMs=30000]    Max backoff between reconnects.
 * @param {boolean} [opts.withAuth=true]        Inject maop_token as query param.
 * @returns {{connected: import('vue').Ref<boolean>,
 *            lastEvent: import('vue').Ref<any>,
 *            error: import('vue').Ref<any>,
 *            close: () => void}}
 */
export function useSSE(opts = {}) {
  const url = opts.url || DEFAULT_URL;
  const events = Array.isArray(opts.events) ? opts.events : [];
  const autoReconnect = opts.autoReconnect !== false;
  const maxBackoffMs = opts.maxBackoffMs || DEFAULT_MAX_BACKOFF_MS;
  const maxRetries = opts.maxRetries || DEFAULT_MAX_RETRIES;
  const withAuth = opts.withAuth !== false;

  const connected = ref(false);
  const lastEvent = ref(null);
  const error = ref(null);

  let es = null;
  let closed = false;
  let backoffMs = INITIAL_BACKOFF_MS;
  let retryCount = 0;
  let reconnectTimer = null;
  let handlers = [];

  function _clearReconnect() {
    if (reconnectTimer) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
  }

  function _scheduleReconnect() {
    if (!autoReconnect || closed) return;
    // P1 fix: stop reconnecting after maxRetries consecutive failures
    if (retryCount >= maxRetries) {
      error.value = new Error(`SSE: max retries (${maxRetries}) exceeded`);
      connected.value = false;
      // Likely auth failure — trigger login flow
      if (typeof window !== 'undefined') {
        window.dispatchEvent(new CustomEvent('maop:unauthorized'));
      }
      return;
    }
    _clearReconnect();
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null;
      retryCount++;
      _connect();
    }, backoffMs);
    // Exponential backoff with cap.
    backoffMs = Math.min(backoffMs * 2, maxBackoffMs);
  }

  function _decode(data) {
    if (data == null || data === '') return null;
    try {
      return JSON.parse(data);
    } catch {
      return data;
    }
  }

  function _attachHandlers() {
    if (!es) return;
    es.onopen = () => {
      connected.value = true;
      error.value = null;
      // Reset backoff and retry count after a successful connection.
      backoffMs = INITIAL_BACKOFF_MS;
      retryCount = 0;
    };
    es.onerror = (e) => {
      error.value = e;
      connected.value = false;
      // EventSource auto-reconnects on transient errors, but if readyState
      // is CLOSED we need to reconnect manually.
      if (es && es.readyState === EventSource.CLOSED) {
        _scheduleReconnect();
      }
    };
    // Always subscribe to the default 'message' event.
    const messageHandler = (e) => {
      lastEvent.value = _decode(e.data);
    };
    es.addEventListener('message', messageHandler);
    handlers.push(['message', messageHandler]);
    // Subscribe to named events.
    for (const ev of events) {
      if (ev === 'message') continue;
      const h = (e) => {
        lastEvent.value = _decode(e.data);
      };
      es.addEventListener(ev, h);
      handlers.push([ev, h]);
    }
  }

  function _detachHandlers() {
    if (!es) return;
    for (const [ev, h] of handlers) {
      try { es.removeEventListener(ev, h); } catch { /* noop */ }
    }
    handlers = [];
  }

  function _connect() {
    if (closed) return;
    _detachHandlers();
    try {
      es = new EventSource(_buildUrl(url, withAuth));
      _attachHandlers();
    } catch (e) {
      error.value = e;
      connected.value = false;
      _scheduleReconnect();
    }
  }

  function close() {
    closed = true;
    _clearReconnect();
    _detachHandlers();
    if (es) {
      try { es.close(); } catch { /* noop */ }
      es = null;
    }
    connected.value = false;
  }

  const _instance = getCurrentInstance();
  if (_instance) {
    onMounted(_connect);
    onUnmounted(close);
  }

  return { connected, lastEvent, error, close };
}
