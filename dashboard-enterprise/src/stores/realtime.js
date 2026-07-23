import { defineStore } from 'pinia';
import { ref, computed, watch } from 'vue';
import { useWebSocket } from '../composables/useWebSocket.js';

/**
 * Global realtime store.
 *
 * Maintains a single WebSocket connection (shared across the whole app via
 * App.vue lifecycle) and exposes the latest pushed snapshot to any page.
 *
 * Connection URL is derived from the current page hostname so the same
 * build works in dev and behind a proxy: ws://<hostname>:9079/ws
 */
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
    const wsProto = typeof location !== 'undefined' && location.protocol === 'https:' ? 'wss:' : 'ws:';
    const url = `${wsProto}//${hostname}:${location.port}/ws`;

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
      } catch (e) {
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