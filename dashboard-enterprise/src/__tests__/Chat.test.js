// Tests for Chat.vue mapMsg() field extraction from metadata.
//
// mapMsg normalises a backend message into the shape the template renders:
//   image  = m.image || m.metadata.image || null
//   model  = m.model || m.metadata.model || ''
//   tokens = m.tokens || m.token_count || m.metadata.tokens || 0
//
// mapMsg is a setup-internal function (not exposed via defineExpose), so we
// drive it end-to-end: mount the component, mock /api/chat/sessions + the
// session detail endpoint, click a session to trigger loadSessionMessages
// (which calls mapMsg per message), then assert on the rendered DOM.

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { shallowMount, flushPromises } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';

// Replace useStreamingFetch.stream with a controllable mock so we can drive
// sendMessage()'s onData/onMeta/onDone/onError callbacks without a real SSE
// connection. hoisted() keeps the reference stable across vi.mock hoisting.
const { streamMock } = vi.hoisted(() => ({ streamMock: vi.fn() }));
vi.mock('../composables/useStreamingFetch.js', () => ({
  useStreamingFetch: () => ({ stream: streamMock }),
  // Chat.vue 还导入并调用 estimateTokenCount（token 计数），mock 必须提供该
  // 导出，否则 vi.mock 只返回部分模块形状会触发 vitest 的 unhandled rejection：
  //   Error: No "estimateTokenCount" export is defined on the "...useStreamingFetch.js" mock
  estimateTokenCount: () => 0,
}));

import Chat from '../views/Chat.vue';

describe('Chat.vue mapMsg field extraction', () => {
  let originalFetch;

  beforeEach(() => {
    setActivePinia(createPinia());
    originalFetch = global.fetch;
    global.__VITEST__ = true;
    if (typeof window !== 'undefined') window.__VITEST__ = true;
    streamMock.mockReset();
  });

  afterEach(() => {
    global.fetch = originalFetch;
    delete global.__VITEST__;
    if (typeof window !== 'undefined') delete window.__VITEST__;
  });

  // Mount Chat, wait for onMounted (sessions + agents + detectAdmin), click the
  // first session to load its messages (mapped via mapMsg), then settle.
  async function mountChatWithMessages(messages) {
    const routes = {
      '/api/auth/status': { auth_enabled: true },
      '/api/chat/sessions': { data: [{ id: 's1', agent: 'a1', message_count: 1 }] },
      '/api/agents': [],
      '/api/chat/s1': { data: { messages } },
    };
    global.fetch = vi.fn((url) => {
      const u = String(url);
      const body = routes[u] ?? {};
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve(body),
        text: () => Promise.resolve(JSON.stringify(body)),
      });
    });

    const wrapper = shallowMount(Chat);
    await flushPromises();
    await flushPromises();

    const sessionItem = wrapper.find('.session-item');
    expect(sessionItem.exists()).toBe(true);
    await sessionItem.trigger('click');
    await flushPromises();
    await flushPromises();
    return wrapper;
  }

  it('uses m.tokens directly when present', async () => {
    const wrapper = await mountChatWithMessages([
      { role: 'assistant', content: 'hi', tokens: 99 },
    ]);
    const tokensEl = wrapper.find('.msg-tokens');
    expect(tokensEl.exists()).toBe(true);
    expect(tokensEl.text()).toContain('99');
    expect(tokensEl.text()).toContain('tokens');
    wrapper.unmount();
  });

  it('falls back to m.token_count when m.tokens is absent', async () => {
    const wrapper = await mountChatWithMessages([
      { role: 'assistant', content: 'hi', token_count: 88 },
    ]);
    const tokensEl = wrapper.find('.msg-tokens');
    expect(tokensEl.exists()).toBe(true);
    expect(tokensEl.text()).toContain('88');
    wrapper.unmount();
  });

  it('falls back to m.metadata.tokens when tokens/token_count are absent', async () => {
    const wrapper = await mountChatWithMessages([
      { role: 'assistant', content: 'hi', metadata: { tokens: 77 } },
    ]);
    const tokensEl = wrapper.find('.msg-tokens');
    expect(tokensEl.exists()).toBe(true);
    expect(tokensEl.text()).toContain('77');
    wrapper.unmount();
  });

  it('omits the tokens span when no token source is available', async () => {
    const wrapper = await mountChatWithMessages([
      { role: 'assistant', content: 'hi' },
    ]);
    // tokens default to 0 → v-if="msg.tokens" is falsy → span not rendered.
    expect(wrapper.find('.msg-tokens').exists()).toBe(false);
    wrapper.unmount();
  });

  it('uses m.image directly when present', async () => {
    const dataUrl = 'data:image/png;base64,AAAA';
    const wrapper = await mountChatWithMessages([
      { role: 'user', content: 'c', image: dataUrl },
    ]);
    const img = wrapper.find('.msg-image');
    expect(img.exists()).toBe(true);
    expect(img.attributes('src')).toBe(dataUrl);
    wrapper.unmount();
  });

  it('falls back to m.metadata.image when m.image is absent', async () => {
    const dataUrl = 'data:image/jpeg;base64,BBBB';
    const wrapper = await mountChatWithMessages([
      { role: 'user', content: 'c', metadata: { image: dataUrl } },
    ]);
    const img = wrapper.find('.msg-image');
    expect(img.exists()).toBe(true);
    expect(img.attributes('src')).toBe(dataUrl);
    wrapper.unmount();
  });

  it('uses m.model directly when present', async () => {
    const wrapper = await mountChatWithMessages([
      { role: 'assistant', content: 'c', model: 'gpt-4' },
    ]);
    const modelEl = wrapper.find('.msg-model');
    expect(modelEl.exists()).toBe(true);
    expect(modelEl.text()).toBe('gpt-4');
    wrapper.unmount();
  });

  it('falls back to m.metadata.model when m.model is absent', async () => {
    const wrapper = await mountChatWithMessages([
      { role: 'assistant', content: 'c', metadata: { model: 'claude-3' } },
    ]);
    const modelEl = wrapper.find('.msg-model');
    expect(modelEl.exists()).toBe(true);
    expect(modelEl.text()).toBe('claude-3');
    wrapper.unmount();
  });
});

// ── sendMessage streaming via mocked useStreamingFetch.stream ──────────────
// These exercise the sendMessage() control flow (user msg append, stream
// callbacks, error handling) which is the bulk of Chat.vue's untested code.

describe('Chat.vue sendMessage streaming', () => {
  let originalFetch;

  beforeEach(() => {
    setActivePinia(createPinia());
    originalFetch = global.fetch;
    global.__VITEST__ = true;
    if (typeof window !== 'undefined') window.__VITEST__ = true;
    streamMock.mockReset();
  });

  afterEach(() => {
    global.fetch = originalFetch;
    delete global.__VITEST__;
    if (typeof window !== 'undefined') delete window.__VITEST__;
  });

  function mockFetch(routes) {
    global.fetch = vi.fn((url) => {
      const body = routes[String(url)] ?? {};
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve(body),
        text: () => Promise.resolve(JSON.stringify(body)),
      });
    });
  }

  // Mount Chat with one agent available so selectedAgent auto-selects and the
  // send button becomes enabled once text is entered.
  async function mountChatForSend() {
    mockFetch({
      '/api/auth/status': { auth_enabled: true },
      '/api/chat/sessions': { data: [] },
      '/api/agents': [{ name: 'agent1' }],
    });
    const wrapper = shallowMount(Chat);
    await flushPromises();
    await flushPromises();
    return wrapper;
  }

  it('appends the user message and streams the assistant reply via onDone', async () => {
    streamMock.mockImplementation(async (url, body, cb) => {
      cb.onData?.('Hello back');
      cb.onMeta?.({ session_id: 'sess-123', model: 'gpt-4', tokens: 10 });
      cb.onDone?.();
    });
    const wrapper = await mountChatForSend();
    await wrapper.find('textarea').setValue('ping');
    await wrapper.find('.send-btn').trigger('click');
    await flushPromises();
    await flushPromises();

    expect(wrapper.find('.msg-row.user').exists()).toBe(true);
    const assistant = wrapper.find('.msg-row.assistant');
    expect(assistant.exists()).toBe(true);
    expect(assistant.find('.msg-text').text()).toContain('Hello back');
    // onMeta model/token flow through to the assistant message meta.
    expect(assistant.find('.msg-model').text()).toContain('gpt-4');
    expect(assistant.find('.msg-tokens').text()).toContain('10');
    wrapper.unmount();
  });

  it('appends an error message when the stream reports onError', async () => {
    streamMock.mockImplementation(async (url, body, cb) => {
      cb.onError?.('boom');
    });
    const wrapper = await mountChatForSend();
    await wrapper.find('textarea').setValue('hi');
    await wrapper.find('.send-btn').trigger('click');
    await flushPromises();
    await flushPromises();

    const assistant = wrapper.find('.msg-row.assistant');
    expect(assistant.exists()).toBe(true);
    expect(assistant.find('.msg-text').text()).toContain('Error: boom');
    wrapper.unmount();
  });

  it('appends a connection error when stream() throws', async () => {
    streamMock.mockRejectedValue(new Error('network down'));
    const wrapper = await mountChatForSend();
    await wrapper.find('textarea').setValue('hi');
    await wrapper.find('.send-btn').trigger('click');
    await flushPromises();
    await flushPromises();

    const assistant = wrapper.find('.msg-row.assistant');
    expect(assistant.exists()).toBe(true);
    expect(assistant.find('.msg-text').text()).toContain('Connection error: network down');
    wrapper.unmount();
  });

  it('does nothing when no agent is selected', async () => {
    streamMock.mockImplementation(async () => {});
    mockFetch({
      '/api/auth/status': { auth_enabled: true },
      '/api/chat/sessions': { data: [] },
      '/api/agents': [], // no agents → selectedAgent stays ''
    });
    const wrapper = shallowMount(Chat);
    await flushPromises();
    await flushPromises();
    await wrapper.find('textarea').setValue('hi');
    await wrapper.find('.send-btn').trigger('click');
    await flushPromises();
    // No messages rendered, stream never called.
    expect(wrapper.findAll('.msg-row')).toHaveLength(0);
    expect(streamMock).not.toHaveBeenCalled();
    wrapper.unmount();
  });
});