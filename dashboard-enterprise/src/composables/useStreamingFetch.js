/**
 * useStreamingFetch — POST + SSE streaming via ReadableStream.
 *
 * Supports POST requests with a JSON body and parses the SSE response stream.
 * Auth: httpOnly cookie only (M7 fix — no localStorage token, no
 * Authorization header; the cookie is sent automatically for same-origin).
 *
 * @returns {{ stream: Function }}
 */


export function useStreamingFetch() {
  /**
   * Send a POST request and stream the SSE response.
   *
   * @param {string} url - Endpoint URL (e.g. '/api/chat/stream')
   * @param {object} body - JSON body
   * @param {object} [callbacks]
   * @param {function(string, object): void} [callbacks.onData] - Called with (content, meta) for each chunk
   * @param {function(object): void} [callbacks.onMeta] - Called with metadata (session_id, tokens, model)
   * @param {function(): void} [callbacks.onDone] - Called when stream completes
   * @param {function(string): void} [callbacks.onError] - Called on error
   * @returns {Promise<void>}
   */
  async function stream(url, body, callbacks = {}) {
    const { onData, onMeta, onDone, onError } = callbacks;

    const headers = { 'Content-Type': 'application/json' };

    // AbortController for cancellable streaming (prevents leak on unmount/renavigate)
    const controller = new AbortController();
    const onAbort = () => controller.abort();
    // M4 fix: fetch 开始前检查外部 signal 是否已 aborted。
    // 若外部 signal 在调用 stream() 之前已 aborted，addEventListener('abort')
    // 不会触发（事件已过），fetch 仍会发起无用请求。提前检查并直接返回。
    if (callbacks.signal && callbacks.signal.aborted) {
      if (onError) onError('Aborted');
      return;
    }
    if (callbacks.signal) {
      callbacks.signal.addEventListener('abort', onAbort);
    }

    try {
      const res = await fetch(url, {
        method: 'POST',
        headers,
        body: JSON.stringify(body || {}),
        signal: controller.signal,
        credentials: 'include',
      });

      // F23 修复: 401 检查必须在通用 !res.ok 之前，否则 401 分支为死代码
      if (res.status === 401) {
        if (onError) onError('Unauthorized');
        return;
      }

      if (!res.ok) {
        const errText = await res.text().catch(() => res.statusText);
        if (onError) onError(`HTTP ${res.status}: ${errText}`);
        return;
      }

      // M5 fix: res.body 可能为 null（如 204 No Content、某些浏览器/代理剥离 body，
      // 或后端未正确设置 SSE Content-Type）。getReader() 在 null 上调用会抛
      // TypeError，提前检查给出明确错误信息便于调用方排查。
      if (!res.body) throw new Error('Response body is null');
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let fullContent = '';
      let buffer = '';
      let currentEvent = '';

      // M6 fix: 流式读取超时机制。某些后端在连接建立后不再发送任何数据
      // （如 agent 卡死、队列阻塞），reader.read() 会永久挂起导致前端
      // Promise 永不 resolve。设置 30s 无数据超时，超时后 abort 连接。
      const STREAM_TIMEOUT_MS = 30000;
      let streamTimer = setTimeout(() => {
        try { controller.abort(); } catch { /* already aborted */ }
        if (onError) onError('Stream timeout: no data received in 30s');
      }, STREAM_TIMEOUT_MS);

      try {
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          // 收到数据，重置超时计时器
          clearTimeout(streamTimer);
          streamTimer = setTimeout(() => {
            try { controller.abort(); } catch { /* already aborted */ }
            if (onError) onError('Stream timeout: no data received in 30s');
          }, STREAM_TIMEOUT_MS);
          buffer += decoder.decode(value, { stream: true });

          const lines = buffer.split('\n');
          buffer = lines.pop() || '';

          for (const line of lines) {
            if (line.startsWith('event: ')) {
              currentEvent = line.slice(7).trim();
              continue;
            }
            if (!line.startsWith('data: ')) continue;
            const data = line.slice(6).trim();
            if (data === '[DONE]') {
              if (onDone) onDone();
              return;
            }
            try {
              const parsed = JSON.parse(data);
              if (currentEvent === 'error' || parsed.error) {
                const errMsg = typeof parsed.error === 'string'
                  ? parsed.error
                  : (parsed.error && parsed.error.message) || 'Stream error';
                if (onError) onError(errMsg);
                currentEvent = '';
                return;
              }
              if (parsed.content) {
                fullContent += parsed.content;
                if (onData) onData(fullContent, parsed);
              }
              if (parsed.session_id || parsed.tokens || parsed.model) {
                const meta = {};
                if (parsed.session_id) meta.session_id = parsed.session_id;
                if (parsed.tokens) meta.tokens = parsed.tokens;
                if (parsed.model) meta.model = parsed.model;
                if (onMeta) onMeta(meta);
              }
            } catch {
              // Non-JSON data line, skip
            }
            currentEvent = '';
          }
        }
      } finally {
        // M6 fix: 无论流式读取正常结束、中途 return 还是抛错，都清理超时计时器，
        // 避免定时器泄漏导致 abort 在连接已关闭后仍触发。
        clearTimeout(streamTimer);
      }

      if (onDone) onDone();
    } catch (exc) {
      if (onError) onError(exc.message || String(exc));
    } finally {
      if (callbacks.signal) {
        callbacks.signal.removeEventListener('abort', onAbort);
      }
    }
  }

  return { stream };
}
