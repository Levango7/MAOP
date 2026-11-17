import { defineStore } from 'pinia';
import { ref, computed, watch } from 'vue';
import { useWebSocket } from '../composables/useWebSocket.js';
import { useToast } from '../composables/useToast.js';

/**
 * Global realtime store.
 *
 * Maintains a single WebSocket connection (shared across the whole app via
 * App.vue lifecycle) and exposes the latest pushed snapshot to any page.
 *
 * Connection URL is derived from the current page hostname so the same
 * build works in dev and behind a proxy: ws://<hostname>:9079/ws
 */
/**
 * 后端审计告警引擎命中即 broadcaster(alert.model_dump()) → /ws 原样 send_json,
 * 无 type 信封;快照消息带 type:'snapshot'。以 alert_id+rule_id 双键识别告警,
 * 避免把快照误当告警。返回 toast 文案与 tone,非告警消息返回 null。
 */
export function alertToastFromMessage(msg) {
  if (!msg || typeof msg !== 'object' || !msg.alert_id || !msg.rule_id) return null;
  const severity = String(msg.severity || '').toLowerCase();
  const tone = severity === 'critical' || severity === 'error' ? 'error'
    : severity === 'warning' || severity === 'warn' ? 'warn'
      : 'info';
  const rule = (msg.metadata && msg.metadata.rule_name) || msg.rule_id;
  const text = msg.message ? `${rule}: ${msg.message}` : rule;
  return { text, tone };
}

export const useRealtimeStore = defineStore('realtime', () => {
  const connected = ref(false);
  const snapshot = ref(null);
  const lastUpdate = ref(null);

  // Internal handle to the composable driving the socket.
  let ws = null;
  let stopConnected = null;
  let stopMessage = null;

  function connect() {
    if (ws) return; // already initialised — idempotent

    const hostname =
      typeof window !== 'undefined' && window.location
        ? window.location.hostname
        : 'localhost';
    // P1 fix: use protocol-relative URL so wss:// is used on HTTPS pages
    // P2-9 fix: when location.port is empty (reverse proxy on 80/443),
    // omit the port to avoid an invalid `ws://host:/ws` URL.
    // R10 fix: 统一 location 访问风格，使用 `typeof window !== 'undefined' && window.location`
    // 与上方 hostname 守卫一致，避免裸 `location` 在非浏览器环境（SSR/Node 测试）抛 ReferenceError。
    const wsProto = typeof window !== 'undefined' && window.location && window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsPort = typeof window !== 'undefined' && window.location && window.location.port ? `:${window.location.port}` : '';
    const url = `${wsProto}//${hostname}${wsPort}/ws`;

    // useWebSocket auto-registers onMounted/onUnmounted only when called
    // inside a component setup; here we drive connect()/disconnect() manually.
    ws = useWebSocket(url);

    // Mirror the composable's connection flag into store state.
    stopConnected = watch(
      () => ws.connected.value,
      (v) => {
        connected.value = v;
      },
      { immediate: true }
    );

    // On every incoming message, refresh snapshot + lastUpdate and ensure
    // connected reflects the socket's actual state.
    stopMessage = watch(
      () => ws.lastMessage.value,
      (msg) => {
        if (msg === null || msg === undefined) return;
        snapshot.value = msg;
        lastUpdate.value = Date.now();
        connected.value = ws.connected.value;
        // 告警引擎命中广播(notify 通道)→ store 型 toast;引擎已在窗口内去重,
        // 这里不再二次节流。
        const alertToast = alertToastFromMessage(msg);
        if (alertToast) {
          useToast().show(alertToast.text, { tone: alertToast.tone, timeout: 5200 });
        }
      }
    );

    // Kick off the connection (composable won't auto-connect outside setup).
    try {
      ws.connect();
    } catch (e) {
      // Never let a connection failure crash the app — useWebSocket
      // already schedules a silent reconnect on error/close.
      console.warn('[realtime] connect failed, will retry:', e);
    }
  }

  function disconnect() {
    if (ws) {
      try {
        ws.disconnect();
      } catch {
        // ignore — best-effort teardown
      }
      ws = null;
    }
    if (stopConnected) {
      stopConnected();
      stopConnected = null;
    }
    if (stopMessage) {
      stopMessage();
      stopMessage = null;
    }
    connected.value = false;
    // P2-7: 刻意不重置 snapshot 和 lastUpdate —— 保留最后一次推送的数据，
    // 让组件在断连期间仍能展示最后一次的快照而非空白。重连后新数据会
    // 自然覆盖。这是有意的设计选择，不是遗漏。
  }

  // Expose snapshot as a read-only computed so consumers get a stable
  // reactive view without being able to mutate the source.
  const snapshotComputed = computed(() => snapshot.value);

  return {
    // state
    connected,
    lastUpdate,
    // computed (read-only)
    snapshot: snapshotComputed,
    // actions
    connect,
    disconnect,
  };
});