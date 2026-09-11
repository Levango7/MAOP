/**
 * useDagProgress — DAG execution progress streaming composable (v4.5.0).
 *
 * Subscribes to real-time DAG node-status events via SSE or WebSocket
 * and exposes reactive state for Vue components (DagGraph.vue).
 *
 * Usage:
 *   const { events, nodeStates, progress, connected, connect, disconnect, cancel, pause }
 *     = useDagProgress(executionId, { transport: 'sse' });
 *   onMounted(connect);
 *
 * SSE mode (default):
 *   - Uses EventSource (browser-native auto-reconnect + Last-Event-ID).
 *   - URL: /api/stream/dag/{executionId}
 *   - Auth: httpOnly cookie via EventSource { withCredentials: true }.
 *   - Events: "node-status" (data: {node_id, status, timestamp, metadata, seq})
 *             "execution-complete" (closes connection)
 *
 * WebSocket mode:
 *   - Uses WebSocket; auth via same-origin httpOnly cookie (browser auto-sends).
 *   - URL: ws://host/ws/dag/{executionId}
 *   - Downstream: {type: "node-status"|"execution-complete"|"ping", data}
 *   - Upstream:   {action: "cancel"|"pause", node_id} / {type: "pong"}
 *
 * Reactive outputs:
 *   - events:     ref([]) — append-only list of node-status events
 *   - nodeStates: ref({}) — { [node_id]: status } latest state per node
 *   - progress:   ref(0)  — 0-100 completion percentage (terminal/total)
 *   - connected:  ref(false) — connection status
 *
 * Auto-cleanup: onUnmounted → disconnect().
 */
import { ref, computed, onUnmounted, getCurrentInstance } from 'vue';

const TERMINAL_STATUSES = new Set(['success', 'failed', 'skipped']);

export function useDagProgress(executionId, options = {}) {
  const {
    transport = 'sse',
    maxEvents = 500,
    reconnectDelay = 1000,
    maxReconnectAttempts = 10,
  } = options;

  // ── Reactive state ────────────────────────────────────────
  const events = ref([]);
  const nodeStates = ref({});
  const connected = ref(false);
  const executionComplete = ref(false);

  // Progress: percentage of nodes in terminal state (0-100).
  // Computed from nodeStates — when all nodes reach a terminal state,
  // progress is 100.
  const progress = computed(() => {
    const states = nodeStates.value;
    const ids = Object.keys(states);
    if (ids.length === 0) return 0;
    const terminal = ids.filter((id) => TERMINAL_STATUSES.has(states[id]));
    return Math.round((terminal.length / ids.length) * 100);
  });

  // ── Internal handles ──────────────────────────────────────
  let eventSource = null;
  let ws = null;
  let reconnectTimer = null;
  let reconnectAttempts = 0;
  let manuallyDisconnected = false;

  // ── Helpers ───────────────────────────────────────────────

  // L8 fix: 移除未使用的 _getToken 死代码（M6 fix 后 token 由 httpOnly cookie 管理，
  // SSE 通过 withCredentials: true 自动携带，不再需要读取 token）。

  function _applyEvent(data) {
    if (!data || !data.node_id || !data.status) return;
    // P2-4 fix: 一次性构造新数组，避免 push + slice 两次响应式触发。
    // 追加事件并裁剪到 maxEvents 上限（防止无限增长）。
    const next = events.value.length >= maxEvents
      ? events.value.slice(-(maxEvents - 1)).concat(data)
      : events.value.concat(data);
    events.value = next;
    // Update nodeStates reactively (new object to trigger reactivity).
    nodeStates.value = { ...nodeStates.value, [data.node_id]: data.status };
  }

  function _handleComplete(_data) {
    executionComplete.value = true;
    // Auto-disconnect after execution completes (spec 5.2.1 rule 11).
    disconnect();
  }

  function _scheduleReconnect() {
    if (manuallyDisconnected) return;
    if (reconnectAttempts >= maxReconnectAttempts) return;
    reconnectAttempts++;
    // R10 fix: 重连退避添加 60s 上限，避免指数退避在长时间断连后
    // 产生过大延迟（如第 10 次重连 = 2^9 * 1000 = 512s）导致连接永远无法恢复。
    const delay = Math.min(reconnectDelay * Math.pow(2, reconnectAttempts - 1), 60000); // exponential backoff w/ 60s cap
    clearTimeout(reconnectTimer);
    reconnectTimer = setTimeout(() => {
      if (!manuallyDisconnected) connect();
    }, delay);
  }

  // ── SSE connection ────────────────────────────────────────

  function connectSSE() {
    // R10 fix: 先关闭旧 EventSource 连接，避免重复 connect 时旧连接泄漏。
    if (eventSource) {
      try { eventSource.close(); } catch { /* ignore */ }
      eventSource = null;
    }
    // L8 fix: SSR 守卫——EventSource 在非浏览器环境（SSR / Node 测试）下未定义，
    // 直接 new EventSource() 会抛 ReferenceError。提前返回避免崩溃。
    if (typeof EventSource === 'undefined') return;
    // M6 fix: 移除 URL query 中的 token，避免日志/浏览器历史/Referer 泄漏。
    // 原：url = `/api/stream/dag/${executionId}?token=${token}`
    // 现：通过 EventSource withCredentials: true 依赖 httpOnly cookie 认证。
    const url = `/api/stream/dag/${executionId}`;
    try {
      // M6 fix: withCredentials: true 携带 httpOnly cookie（SSE 标准）。
      eventSource = new EventSource(url, { withCredentials: true });
    } catch {
      _scheduleReconnect();
      return;
    }

    eventSource.addEventListener('node-status', (e) => {
      try {
        const data = JSON.parse(e.data);
        _applyEvent(data);
      } catch { /* ignore malformed */ }
    });

    eventSource.addEventListener('execution-complete', (e) => {
      try {
        const data = JSON.parse(e.data);
        _handleComplete(data);
      } catch {
        _handleComplete(null);
      }
    });

    eventSource.onopen = () => {
      connected.value = true;
      reconnectAttempts = 0; // reset on successful connect
    };

    eventSource.onerror = () => {
      connected.value = false;
      // EventSource auto-reconnects, but if it fails repeatedly we
      // supplement with our own exponential backoff for robustness.
      // Only schedule if EventSource is in CLOSED state.
      // L4 fix: 使用 EventSource.CLOSED 常量替代硬编码数字 2，提高可读性。
      if (eventSource && eventSource.readyState === EventSource.CLOSED) {
        _scheduleReconnect();
      }
    };
  }

  // ── WebSocket connection ──────────────────────────────────

  function connectWS() {
    // R10 fix: 先关闭旧 WebSocket 连接，避免重复 connect 时旧连接泄漏。
    if (ws) {
      try { ws.close(); } catch { /* ignore */ }
      ws = null;
    }
    // SSR 守卫: 在非浏览器环境（SSR / Node 测试）下直接返回，避免 ReferenceError。
    // 参考 realtime.js:27-30 的守卫写法。
    if (typeof window === 'undefined') return;
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    // M6 fix: token 现由 httpOnly cookie 管理，不再通过 Sec-WebSocket-Protocol 子协议传递。
    // 浏览器在 WebSocket 握手时会自动携带同源 cookie，后端从 cookie 中读取 token 验证。
    const url = `${proto}//${window.location.host}/ws/dag/${executionId}`;
    try {
      ws = new WebSocket(url);
    } catch {
      _scheduleReconnect();
      return;
    }

    ws.onmessage = (e) => {
      let msg;
      try {
        msg = JSON.parse(e.data);
      } catch {
        return;
      }
      if (msg.type === 'node-status') {
        _applyEvent(msg.data);
      } else if (msg.type === 'execution-complete') {
        _handleComplete(msg.data);
      } else if (msg.type === 'ping') {
        // Respond to server heartbeat with pong.
        _sendWS({ type: 'pong' });
      }
      // 'pong' and 'action-result' messages are handled by callers
      // via the events stream or can be extended with callbacks.
    };

    ws.onopen = () => {
      connected.value = true;
      reconnectAttempts = 0;
    };

    ws.onclose = () => {
      connected.value = false;
      _scheduleReconnect();
    };

    ws.onerror = () => {
      connected.value = false;
    };
  }

  function _sendWS(obj) {
    if (ws && ws.readyState === WebSocket.OPEN) {
      try {
        ws.send(JSON.stringify(obj));
      } catch { /* ignore */ }
    }
  }

  // ── Public API ────────────────────────────────────────────

  function connect() {
    // R10 fix: 先关闭旧连接（含 eventSource/ws/reconnectTimer），避免重复
    // connect 时旧连接泄漏。connectSSE/connectWS 内部也会各自关闭对应句柄，
    // 此处统一 disconnect() 确保无论 transport 类型都清理干净。
    disconnect();
    manuallyDisconnected = false;
    // P1-1 fix: 重置重连计数器，避免 disconnect 后 reconnectAttempts 已达上限
    // 导致再次 connect 时 _scheduleReconnect 直接返回无法重连。
    reconnectAttempts = 0;
    if (transport === 'sse') {
      connectSSE();
    } else {
      connectWS();
    }
  }

  function disconnect() {
    manuallyDisconnected = true;
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
    if (eventSource) {
      try { eventSource.close(); } catch { /* ignore */ }
      eventSource = null;
    }
    if (ws) {
      try { ws.close(); } catch { /* ignore */ }
      ws = null;
    }
    connected.value = false;
  }

  function cancel(nodeId) {
    if (transport === 'ws') {
      _sendWS({ action: 'cancel', node_id: nodeId });
    }
    // SSE is read-only — cancel requires WebSocket.
  }

  function pause(nodeId) {
    if (transport === 'ws') {
      _sendWS({ action: 'pause', node_id: nodeId });
    }
  }

  // ── Auto-cleanup on unmount ───────────────────────────────
  // R10 fix: 加 getCurrentInstance() 守卫，避免在非组件 setup 上下文
  // （如 Pinia store、纯单元测试）中调用 onUnmounted 抛 Vue 警告。
  if (getCurrentInstance()) {
    onUnmounted(disconnect);
  }

  return {
    // Reactive state
    events,
    nodeStates,
    progress,
    connected,
    executionComplete,
    // Actions
    connect,
    disconnect,
    cancel,
    pause,
  };
}