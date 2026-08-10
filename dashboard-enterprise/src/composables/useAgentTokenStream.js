/**
 * useAgentTokenStream — SSE subscription for Agent execution token streaming.
 *
 * v5.0.0: Connects to /api/stream/agent/{execution_id} via EventSource.
 * Accumulates tokens and provides callbacks for real-time rendering.
 *
 * Unlike useStreamingFetch (POST + ReadableStream for chat), this composable
 * uses EventSource (GET) to subscribe to a running agent execution's token stream.
 *
 * @returns {{ subscribe: Function, close: Function }}
 */
import { ref, onUnmounted } from 'vue';

const TOKEN_KEY = 'maop_token';

function getToken() {
  try { return localStorage.getItem(TOKEN_KEY) || ''; } catch { return ''; }
}

export function useAgentTokenStream() {
  const streaming = ref(false);
  const content = ref('');
  const tokenCount = ref(0);
  const meta = ref(null);
  let eventSource = null;
  let abortController = null;

  /**
   * Subscribe to an agent execution's token stream.
   *
   * @param {string} executionId - The execution ID to subscribe to.
   * @param {object} [callbacks]
   * @param {function(string, string): void} [callbacks.onToken] - Called with (fullContent, delta) for each token.
   * @param {function(object): void} [callbacks.onMeta] - Called with metadata (agent, model, tokens).
   * @param {function(object): void} [callbacks.onDone] - Called with completion data.
   * @param {function(string): void} [callbacks.onError] - Called on error.
   * @param {AbortSignal} [callbacks.signal] - Optional abort signal.
   * @returns {void}
   */
  function subscribe(executionId, callbacks = {}) {
    const { onToken, onMeta, onDone, onError, signal } = callbacks;

    // Close any existing connection
    close();

    streaming.value = true;
    content.value = '';
    tokenCount.value = 0;
    meta.value = null;

    abortController = new AbortController();
    if (signal) {
      signal.addEventListener('abort', () => {
        close();
        if (onDone) onDone({ reason: 'aborted' });
      });
    }

    // Build URL with JWT token (EventSource cannot set Authorization header)
    const token = getToken();
    const url = `/api/stream/agent/${encodeURIComponent(executionId)}${token ? `?token=${encodeURIComponent(token)}` : ''}`;

    try {
      eventSource = new EventSource(url);
    } catch (exc) {
      streaming.value = false;
      if (onError) onError(`Failed to create EventSource: ${exc.message}`);
      return;
    }

    eventSource.addEventListener('token', (ev) => {
      try {
        const parsed = JSON.parse(ev.data);
        const delta = parsed.content || '';
        content.value += delta;
        tokenCount.value = content.value.length;
        if (onToken) onToken(content.value, delta);
      } catch { /* skip non-JSON */ }
    });

    eventSource.addEventListener('meta', (ev) => {
      try {
        const parsed = JSON.parse(ev.data);
        meta.value = { ...meta.value, ...parsed };
        if (onMeta) onMeta(meta.value);
      } catch { /* skip non-JSON */ }
    });

    eventSource.addEventListener('done', (ev) => {
      try {
        const parsed = JSON.parse(ev.data);
        streaming.value = false;
        close();
        if (onDone) onDone(parsed);
      } catch {
        streaming.value = false;
        close();
        if (onDone) onDone({});
      }
    });

    eventSource.addEventListener('error', (ev) => {
      // EventSource 'error' fires on connection loss AND on server-sent error events
      if (eventSource && eventSource.readyState === EventSource.CLOSED) {
        streaming.value = false;
        if (onError) onError('Connection closed');
        return;
      }
      try {
        const parsed = JSON.parse(ev.data);
        streaming.value = false;
        close();
        if (onError) onError(parsed.error || 'Stream error');
      } catch {
        // ReadyState !== CLOSED but parse failed — connection issue
        streaming.value = false;
        close();
        if (onError) onError('Stream connection error');
      }
    });
  }

  /**
   * Close the active EventSource connection.
   */
  function close() {
    if (eventSource) {
      eventSource.close();
      eventSource = null;
    }
    if (abortController) {
      abortController.abort();
      abortController = null;
    }
    streaming.value = false;
  }

  // Auto-cleanup on component unmount
  onUnmounted(() => {
    close();
  });

  return { streaming, content, tokenCount, meta, subscribe, close };
}