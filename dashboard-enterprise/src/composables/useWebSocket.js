import { ref, onMounted, onUnmounted, getCurrentInstance } from 'vue';

const MAX_RECONNECT_ATTEMPTS = 10;
// P2 fix: 指数退避重连参数 — 初始延迟 1s, 每次翻倍, 上限 30s。
// 避免断线后立即重连对服务器造成压力, 同时保证长时间断线后不会无限等待。
const RECONNECT_BASE_DELAY = 1000;
const RECONNECT_MAX_DELAY = 30000;

export function useWebSocket(url = '') {
  const connected = ref(false);
  const lastMessage = ref(null);
  const error = ref(null);
  let ws = null;
  let reconnectTimer = null;
  let reconnectAttempts = 0;

  function connect() {
    reconnectAttempts = 0;
    try {
      const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
      const baseUrl = url || `${proto}//${location.host}/ws`;
      // M6 fix: token 现由 httpOnly cookie 管理，前端无法读取。
      // 浏览器在 WebSocket 握手时会自动携带同源 cookie，后端从 cookie 中读取
      // token 验证。移除原 Sec-WebSocket-Protocol 子协议认证（localStorage
      // token 已失效，getWsToken() 始终返回空字符串）。
      ws = new WebSocket(baseUrl);
      ws.onopen = () => {
        connected.value = true;
        error.value = null;
        reconnectAttempts = 0; // P2-12: reset on successful connect
      };
      ws.onmessage = (event) => {
        try {
          lastMessage.value = JSON.parse(event.data);
        } catch {
          lastMessage.value = event.data;
        }
      };
      ws.onclose = (event) => {
        connected.value = false;
        // P1 fix: close code 4401 = auth failure — don't reconnect, trigger login
        if (event.code === 4401) {
          error.value = new Error('Authentication required');
          if (typeof window !== 'undefined') {
            window.dispatchEvent(new CustomEvent('maop:unauthorized'));
          }
          return; // don't schedule reconnect on auth failure
        }
        scheduleReconnect();
      };
      ws.onerror = (e) => {
        error.value = e;
        connected.value = false;
      };
    } catch (e) {
      error.value = e;
      scheduleReconnect();
    }
  }

  function scheduleReconnect() {
    // P2-12: stop reconnecting after MAX_RECONNECT_ATTEMPTS to avoid
    // infinite retry loops when the backend is permanently down.
    if (reconnectAttempts >= MAX_RECONNECT_ATTEMPTS) {
      error.value = new Error('Max reconnection attempts reached');
      return;
    }
    if (reconnectTimer) return;
    reconnectAttempts++;
    // P2 fix: 指数退避 — delay = min(BASE * 2^(attempts-1), MAX)
    // 第 1 次 1s, 第 2 次 2s, 第 3 次 4s, ... 上限 30s。
    const delay = Math.min(
      RECONNECT_BASE_DELAY * 2 ** (reconnectAttempts - 1),
      RECONNECT_MAX_DELAY,
    );
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null;
      connect();
    }, delay);
  }

  function send(data) {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(typeof data === 'string' ? data : JSON.stringify(data));
    }
  }

  function disconnect() {
    if (reconnectTimer) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
    if (ws) {
      ws.close();
      ws = null;
    }
    connected.value = false;
  }

  // Only auto-register lifecycle hooks when called inside a component setup
  // context. When used from a Pinia store (no active instance), the caller
  // drives connect()/disconnect() manually — this avoids Vue warnings and
  // gives the store full control over the socket lifecycle.
  if (getCurrentInstance()) {
    onMounted(connect);
    onUnmounted(disconnect);
  }

  return { connected, lastMessage, error, send, connect, disconnect };
}