import { ref, onMounted, onUnmounted, getCurrentInstance } from 'vue';

const MAX_RECONNECT_ATTEMPTS = 10;

export function useWebSocket(url = '') {
  const connected = ref(false);
  const lastMessage = ref(null);
  const error = ref(null);
  let ws = null;
  let reconnectTimer = null;
  let reconnectAttempts = 0;

  function getWsToken() {
    try { return localStorage.getItem('maop_token') || ''; } catch { return ''; }
  }

  function connect() {
    reconnectAttempts = 0;
    try {
      const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
      const baseUrl = url || `${proto}//${location.host}/ws`;
      const token = getWsToken();
      // P1-10 fix: send JWT via Sec-WebSocket-Protocol subprotocol (not URL
      // query) so the token never appears in access logs / browser history.
      // The backend (server.py:612-623) accepts it from the subprotocol.
      ws = token
        ? new WebSocket(baseUrl, ['token', token])
        : new WebSocket(baseUrl);
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
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null;
      connect();
    }, 3000);
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