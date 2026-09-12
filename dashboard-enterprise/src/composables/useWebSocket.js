import { ref, onMounted, onUnmounted, getCurrentInstance } from 'vue';

const MAX_RECONNECT_ATTEMPTS = 10;

export function useWebSocket(url = '') {
  const connected = ref(false);
  const lastMessage = ref(null);
  const error = ref(null);
  let ws = null;
  let reconnectTimer = null;
  let reconnectAttempts = 0;

  // L3 fix: getWsToken() 已是死代码（M7 fix 后始终返回 ''），移除该函数
  // 并简化 connect() 中的 WebSocket 构造——不再需要 token 三元分支。

  function connect() {
    // SSR 守卫: 在非浏览器环境（SSR / Node 测试）下直接返回，避免访问全局 location 抛 ReferenceError。
    if (typeof window === 'undefined') return;
    reconnectAttempts = 0;
    try {
      const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      const baseUrl = url || `${proto}//${window.location.host}/ws`;
      // L3 fix: token 由 httpOnly cookie 自动携带（同源 WS 握手），不再通过
      // Sec-WebSocket-Protocol 子协议传递。移除 getWsToken() 死代码分支。
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
        _scheduleReconnect();
      };
      ws.onerror = (e) => {
        error.value = e;
        connected.value = false;
      };
    } catch (e) {
      error.value = e;
      _scheduleReconnect();
    }
  }

  // P2-9 fix: 统一命名为 _scheduleReconnect（下划线前缀表示内部函数），
  // 与 useDagProgress.js 保持一致。
  function _scheduleReconnect() {
    // P2-12: stop reconnecting after MAX_RECONNECT_ATTEMPTS to avoid
    // infinite retry loops when the backend is permanently down.
    if (reconnectAttempts >= MAX_RECONNECT_ATTEMPTS) {
      error.value = new Error('Max reconnection attempts reached');
      return;
    }
    if (reconnectTimer) return;
    reconnectAttempts++;
    // L4 fix: 指数退避重连——固定 3s 间隔在后端长时间不可达时会产生大量
    // 无效重连请求。改为 3000 * 2^min(attempts,5)，上限 96s，避免重连风暴。
    const delay = 3000 * Math.pow(2, Math.min(reconnectAttempts, 5));
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
      // M3 fix: ws.close() 可能抛错（如 socket 已处于 CLOSING/CLOSED 状态或
      // 浏览器内部异常），包裹 try-catch 以 best-effort 方式关闭，避免
      // disconnect() 抛错中断调用方清理流程。
      try { ws.close(); } catch { /* best-effort */ }
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