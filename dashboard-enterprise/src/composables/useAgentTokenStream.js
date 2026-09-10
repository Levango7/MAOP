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

export function useAgentTokenStream() {
  const streaming = ref(false);
  const content = ref('');
  // L7 fix: tokenCount 实际存储的是 content.value.length（字符数），
  // 而非 LLM token 数。命名保留以兼容现有调用方，但此处明确注释：
  /* charCount: actually character count, not token count */
  const tokenCount = ref(0);
  const meta = ref(null);
  let eventSource = null;
  let abortController = null;
  // F26 fix: 保存外部 signal 的 abort handler 引用, 以便在 close() 中移除监听器,
  // 避免每次 subscribe 都累积一个监听器 (memory leak + 重复 close 调用)。
  let externalSignal = null;
  let externalAbortHandler = null;

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
      // F26 fix: 保存 handler 引用以便 close() 能 removeEventListener。
      // 此前直接传匿名箭头函数, 引用丢失导致监听器永远无法移除。
      externalSignal = signal;
      externalAbortHandler = () => {
        close();
        if (onDone) onDone({ reason: 'aborted' });
      };
      signal.addEventListener('abort', externalAbortHandler);
    }

    // Auth: rely on the httpOnly maop_token cookie.
    // H2 fix: EventSource 默认不携带跨域 cookie，必须显式设置 withCredentials: true，
    // 否则 httpOnly maop_token cookie 在跨域场景下不会随请求发送，导致鉴权失败。
    // M7 fix: previously the JWT was read from localStorage and appended to
    // the URL — tokens must never appear in URLs/access logs (parity with
    // the WebSocket subprotocol fix P1-10).
    const url = `/api/stream/agent/${encodeURIComponent(executionId)}`;

    try {
      eventSource = new EventSource(url, { withCredentials: true });
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
    // F26 fix: 移除外部 signal 的 abort 监听器, 释放引用避免泄漏。
    if (externalSignal && externalAbortHandler) {
      externalSignal.removeEventListener('abort', externalAbortHandler);
      externalSignal = null;
      externalAbortHandler = null;
    }
    streaming.value = false;
  }

  // Auto-cleanup on component unmount
  onUnmounted(() => {
    close();
  });

  return { streaming, content, tokenCount, meta, subscribe, close };
}