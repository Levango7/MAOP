import { ref, onMounted, onUnmounted } from 'vue';

export function useWebSocket(url = '') {
  const connected = ref(false);
  const lastMessage = ref(null);
  const error = ref(null);
  let ws = null;
  let reconnectTimer = null;

  function getWsUrl() {
    if (url) return url;
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    return `${proto}//${location.host}/ws`;
  }

  function connect() {
    try {
      ws = new WebSocket(getWsUrl());
      ws.onopen = () => {
        connected.value = true;
        error.value = null;
      };
      ws.onmessage = (event) => {
        try {
          lastMessage.value = JSON.parse(event.data);
        } catch {
          lastMessage.value = event.data;
        }
      };
      ws.onclose = () => {
        connected.value = false;
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
    if (reconnectTimer) return;
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

  onMounted(connect);
  onUnmounted(disconnect);

  return { connected, lastMessage, error, send, disconnect };
}