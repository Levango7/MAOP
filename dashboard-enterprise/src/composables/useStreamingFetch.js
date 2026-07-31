/**
 * useStreamingFetch — POST + SSE streaming via ReadableStream.
 *
 * Supports POST requests with a JSON body and parses the SSE response stream.
 * Automatically injects JWT token for auth.
 *
 * @returns {{ stream: Function }}
 */
import { getCurrentInstance } from 'vue';

const TOKEN_KEY = 'maop_token';

function getToken() {
  try { return localStorage.getItem(TOKEN_KEY) || ''; } catch { return ''; }
}

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
    const token = getToken();

    const headers = { 'Content-Type': 'application/json' };
    if (token) headers['Authorization'] = `Bearer ${token}`;

    try {
      const res = await fetch(url, {
        method: 'POST',
        headers,
        body: JSON.stringify(body || {}),
      });

      if (!res.ok) {
        const errText = await res.text().catch(() => res.statusText);
        if (onError) onError(`HTTP ${res.status}: ${errText}`);
        return;
      }

      if (res.status === 401) {
        if (onError) onError('Unauthorized');
        return;
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let fullContent = '';
      let buffer = '';
      let currentEvent = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
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

      if (onDone) onDone();
    } catch (exc) {
      if (onError) onError(exc.message || String(exc));
    }
  }

  return { stream };
}
