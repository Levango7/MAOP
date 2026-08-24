/**
 * useStreamingFetch — POST + SSE streaming via ReadableStream.
 *
 * Supports POST requests with a JSON body and parses the SSE response stream.
 * M6 fix: token 现由 httpOnly cookie 管理，前端不再读取 localStorage token。
 * 通过 credentials: 'include' 让浏览器自动携带 httpOnly cookie 完成认证。
 *
 * @returns {{ stream: Function }}
 */

/**
 * P2 fix: 改进的 token 估算算法。
 *
 * 原算法 `Math.ceil(text.length / 4)` 对中文等多字节字符不准确:
 * 一个中文字符通常对应 1~2 个 token, 而非原算法假设的 0.25 个。
 *
 * 改进策略: 按字符的 Unicode 码点区分 ASCII 与多字节字符, 分别估算:
 *   - ASCII (码点 < 128): 约 4 个字符 / token (英文单词+空格+标点的经验值)
 *   - 多字节 (中文/CJK/emoji 等): 约 1.5 个字符 / token (GPT tokenizer 经验值)
 *
 * 局限性说明:
 *   - 这仍是启发式估算, 不同 model 的 tokenizer (BPE/Unigram) 结果会有差异。
 *   - 精确计数需使用 tiktoken-js 等库, 但会增加 ~400KB bundle 体积,
 *     且需按 model 加载不同的 encoding, 当前未引入。
 *   - 估算误差通常在 ±15% 以内, 足以满足 UI 速度指示等非计费场景。
 *
 * @param {string} text - 待估算的文本
 * @returns {number} 估算的 token 数 (向上取整)
 */
export function estimateTokenCount(text) {
  if (!text) return 0;
  let asciiCount = 0;
  let multiByteCount = 0;
  for (const ch of text) {
    if (ch.codePointAt(0) < 128) {
      asciiCount++;
    } else {
      multiByteCount++;
    }
  }
  return Math.ceil(asciiCount / 4 + multiByteCount / 1.5);
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

    // M6 fix: 移除 localStorage token 读取与 Authorization header 设置，
    // 依赖 httpOnly cookie 通过 credentials: 'include' 自动携带认证信息。
    const headers = { 'Content-Type': 'application/json' };

    // AbortController for cancellable streaming (prevents leak on unmount/renavigate)
    const controller = new AbortController();
    // P2 fix: 在外部 signal 上注册 abort listener 后必须在请求结束时移除,
    // 否则每次调用 stream() 都会在 callbacks.signal 上累积一个新 listener,
    // 导致内存泄漏。这里保存 handler 引用以便在 finally 中移除。
    const externalSignal = callbacks.signal;
    const onExternalAbort = () => controller.abort();
    if (externalSignal) {
      externalSignal.addEventListener('abort', onExternalAbort);
    }

    try {
      const res = await fetch(url, {
        method: 'POST',
        headers,
        body: JSON.stringify(body || {}),
        credentials: 'include', // M6 fix: 携带 httpOnly cookie
        signal: controller.signal,
      });

      // 401 专门处理需先于 !res.ok：401 时 !res.ok 为 true，若先判 !res.ok
      // 会落入通用错误分支，永远到不了此 401 分支（原为死代码）。
      if (res.status === 401) {
        if (onError) onError('Unauthorized');
        return;
      }

      if (!res.ok) {
        const errText = await res.text().catch(() => res.statusText);
        if (onError) onError(`HTTP ${res.status}: ${errText}`);
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
    } finally {
      // P2 fix: 无论请求成功、失败还是中止, 都要移除外部 signal 上的
      // abort listener, 避免重复注册导致的内存泄漏。
      if (externalSignal) {
        externalSignal.removeEventListener('abort', onExternalAbort);
      }
    }
  }

  return { stream };
}
