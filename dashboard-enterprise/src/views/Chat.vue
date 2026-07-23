<template>
  <div class="chat-page">
    <div class="chat-header">
      <h1>Chat</h1>
      <div class="agent-select">
        <label>Agent</label>
        <select v-model="selectedAgent" @change="onAgentChange">
          <option value="">Select agent...</option>
          <option v-for="a in agents" :key="a.name" :value="a.name">{{ a.name }}</option>
        </select>
      </div>
      <div class="session-info" v-if="sessionId">
        <span class="session-id">{{ sessionId.slice(0, 8) }}</span>
        <button class="btn-sm" @click="newSession">New Session</button>
      </div>
    </div>

    <div class="chat-body" ref="chatBody">
      <div class="welcome-msg" v-if="messages.length === 0">
        <div class="welcome-icon">💬</div>
        <h2>Start a conversation</h2>
        <p>Select an agent and type your message below. Supports text and image input.</p>
        <div class="quick-actions">
          <button v-for="q in quickPrompts" :key="q" class="quick-btn" @click="sendMessage(q)">{{ q }}</button>
        </div>
      </div>

      <div v-for="(msg, i) in messages" :key="i" :class="['msg-row', msg.role]">
        <div class="msg-avatar">{{ msg.role === 'user' ? '👤' : '🤖' }}</div>
        <div class="msg-bubble">
          <div class="msg-content">
            <img v-if="msg.image" :src="msg.image" class="msg-image" />
            <div class="msg-text" v-html="renderMarkdown(msg.content)"></div>
          </div>
          <div class="msg-meta">
            <span class="msg-time">{{ msg.time }}</span>
            <span v-if="msg.tokens" class="msg-tokens">{{ msg.tokens }} tokens</span>
            <span v-if="msg.model" class="msg-model">{{ msg.model }}</span>
          </div>
        </div>
      </div>

      <div v-if="streaming" class="msg-row assistant">
        <div class="msg-avatar">🤖</div>
        <div class="msg-bubble streaming">
          <div class="msg-text" v-html="renderMarkdown(streamContent)"></div>
          <span class="cursor">▊</span>
        </div>
      </div>
    </div>

    <div class="chat-input-area">
      <div class="image-preview" v-if="pendingImage">
        <img :src="pendingImage" />
        <button class="remove-img" @click="pendingImage = null">✕</button>
      </div>
      <div class="input-row">
        <label class="attach-btn" title="Attach image">
          📎
          <input type="file" accept="image/*" @change="onImageAttach" hidden />
        </label>
        <textarea
          v-model="inputText"
          @keydown.enter.exact="onEnter"
          placeholder="Type a message... (Enter to send, Shift+Enter for new line)"
          rows="1"
          ref="inputEl"
        ></textarea>
        <button class="send-btn" @click="sendMessage()" :disabled="!inputText.trim() && !pendingImage || !selectedAgent || streaming">
          {{ streaming ? '⏳' : '➤' }}
        </button>
      </div>
      <div class="input-footer">
        <span class="char-count">{{ inputText.length }} chars</span>
        <span class="agent-hint" v-if="!selectedAgent">⚠ Please select an agent first</span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, nextTick, watch } from 'vue';
import { useApiStore } from '../stores/api.js';
import { useStreamingFetch } from '../composables/useStreamingFetch.js';

const api = useApiStore();
// F-P0-3 fix: composable must be called at setup top level, not inside function
const { stream } = useStreamingFetch();
const agents = ref([]);
const selectedAgent = ref('');
const sessionId = ref('');
const messages = ref([]);
const inputText = ref('');
const pendingImage = ref(null);
const streaming = ref(false);
const streamContent = ref('');
const chatBody = ref(null);
const inputEl = ref(null);

const quickPrompts = [
  'Explain the project architecture',
  'Review the latest changes',
  'Generate unit tests',
  'Optimize performance',
];

function renderMarkdown(text) {
  if (!text) return '';
  return text
    .replace(/```(\w*)\n([\s\S]*?)```/g, '<pre><code class="$1">$2</code></pre>')
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/\n/g, '<br>');
}

function onAgentChange() {
  messages.value = [];
  sessionId.value = '';
}

function newSession() {
  sessionId.value = '';
  messages.value = [];
}

function onImageAttach(e) {
  const file = e.target.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = (ev) => { pendingImage.value = ev.target.result; };
  reader.readAsDataURL(file);
  e.target.value = '';
}

function onEnter(e) {
  if (e.shiftKey) return;
  e.preventDefault();
  sendMessage();
}

function now() {
  return new Date().toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' });
}

async function sendMessage(overrideText) {
  const text = overrideText || inputText.value.trim();
  if (!text && !pendingImage.value) return;
  if (!selectedAgent.value) return;

  const userMsg = {
    role: 'user',
    content: text,
    image: pendingImage.value,
    time: now(),
  };
  messages.value.push(userMsg);
  inputText.value = '';
  pendingImage.value = null;

  await nextTick();
  scrollBottom();

  streaming.value = true;
  streamContent.value = '';

  try {
    // P0-1 fix: use /api/chat/stream (SSE) instead of /api/chat (JSON)
    // F-P0-3 fix: stream() is now initialized at setup top level
    const body = {
      agent: selectedAgent.value,
      message: text,
      session_id: sessionId.value || undefined,
      images: userMsg.image ? [userMsg.image] : undefined,  // P1-11 fix: images (plural list)
    };

    let msgMeta = {};

    await stream('/api/chat/stream', body, {
      onData: async (fullContent) => {
        streamContent.value = fullContent;
        await nextTick();
        scrollBottom();
      },
      onMeta: (meta) => {
        if (meta.session_id) sessionId.value = meta.session_id;
        Object.assign(msgMeta, meta);
      },
      onDone: () => {
        streaming.value = false;
        messages.value.push({
          role: 'assistant',
          content: streamContent.value,
          time: now(),
          ...msgMeta,
        });
        streamContent.value = '';
      },
      onError: (errMsg) => {
        streaming.value = false;
        messages.value.push({ role: 'assistant', content: `Error: ${errMsg}`, time: now() });
      },
    });
  } catch (exc) {
    streaming.value = false;
    messages.value.push({ role: 'assistant', content: `Connection error: ${exc.message}`, time: now() });
  }
}

function scrollBottom() {
  if (chatBody.value) chatBody.value.scrollTop = chatBody.value.scrollHeight;
}

watch(inputText, () => {
  if (inputEl.value) {
    inputEl.value.style.height = 'auto';
    inputEl.value.style.height = Math.min(inputEl.value.scrollHeight, 120) + 'px';
  }
});

onMounted(async () => {
  try {
    const data = await api.get('/api/agents');
    agents.value = data.agents || [];
    if (agents.value.length > 0 && !selectedAgent.value) {
      selectedAgent.value = agents.value[0].name;
    }
  } catch {}
});
</script>

<style scoped>
.chat-page { display: flex; flex-direction: column; height: calc(100vh - 56px); }
.chat-header { display: flex; align-items: center; gap: 16px; margin-bottom: 16px; flex-shrink: 0; }
.chat-header h1 { font-size: 24px; font-weight: 700; }
.agent-select { display: flex; align-items: center; gap: 8px; }
.agent-select label { font-size: 12px; color: var(--text3); font-weight: 600; }
.agent-select select { background: var(--bg2); border: 1px solid var(--border); border-radius: 8px; padding: 6px 12px; font-size: 13px; color: var(--text); outline: none; }
.session-info { margin-left: auto; display: flex; align-items: center; gap: 8px; }
.session-id { font-size: 11px; color: var(--text3); font-family: monospace; background: var(--bg2); padding: 2px 8px; border-radius: 4px; }
.btn-sm { background: var(--bg2); border: 1px solid var(--border); border-radius: 8px; padding: 4px 12px; font-size: 12px; color: var(--text2); cursor: pointer; }

.chat-body { flex: 1; overflow-y: auto; padding: 8px 0; }
.welcome-msg { text-align: center; padding: 60px 20px; color: var(--text3); }
.welcome-icon { font-size: 48px; margin-bottom: 16px; }
.welcome-msg h2 { font-size: 20px; color: var(--text); margin-bottom: 8px; }
.welcome-msg p { font-size: 14px; margin-bottom: 24px; }
.quick-actions { display: flex; flex-wrap: wrap; gap: 8px; justify-content: center; }
.quick-btn { background: var(--bg2); border: 1px solid var(--border); border-radius: 20px; padding: 8px 16px; font-size: 13px; color: var(--text2); cursor: pointer; transition: all .15s; }
.quick-btn:hover { border-color: var(--accent); color: var(--accent); }

.msg-row { display: flex; gap: 10px; margin-bottom: 16px; padding: 0 4px; }
.msg-row.user { flex-direction: row-reverse; }
.msg-avatar { width: 32px; height: 32px; border-radius: 10px; background: var(--bg2); display: flex; align-items: center; justify-content: center; font-size: 16px; flex-shrink: 0; }
.msg-bubble { max-width: 70%; background: var(--bg2); border: 1px solid var(--border); border-radius: 16px; padding: 12px 16px; }
.msg-row.user .msg-bubble { background: color-mix(in srgb, var(--accent) 12%, var(--bg2)); border-color: color-mix(in srgb, var(--accent) 25%, var(--border)); }
.msg-content { font-size: 14px; line-height: 1.6; }
.msg-image { max-width: 300px; border-radius: 8px; margin-bottom: 8px; }
.msg-text :deep(pre) { background: var(--bg); border-radius: 8px; padding: 12px; overflow-x: auto; margin: 8px 0; font-size: 13px; }
.msg-text :deep(code) { font-family: 'SF Mono', Menlo, monospace; font-size: 13px; background: var(--bg); padding: 2px 6px; border-radius: 4px; }
.msg-text :deep(pre code) { background: none; padding: 0; }
.msg-meta { display: flex; gap: 10px; margin-top: 6px; font-size: 11px; color: var(--text3); }
.msg-tokens { background: var(--bg); padding: 1px 6px; border-radius: 4px; }
.msg-model { background: var(--bg); padding: 1px 6px; border-radius: 4px; font-family: monospace; }

.streaming { border-color: var(--accent); }
.cursor { animation: blink 1s step-end infinite; color: var(--accent); }
@keyframes blink { 50% { opacity: 0; } }

.chat-input-area { flex-shrink: 0; border-top: 1px solid var(--border); padding: 12px 0; }
.image-preview { position: relative; display: inline-block; margin-bottom: 8px; }
.image-preview img { max-height: 80px; border-radius: 8px; border: 1px solid var(--border); }
.remove-img { position: absolute; top: -6px; right: -6px; background: var(--fail); color: #fff; border: none; border-radius: 50%; width: 20px; height: 20px; cursor: pointer; font-size: 11px; display: flex; align-items: center; justify-content: center; }
.input-row { display: flex; align-items: flex-end; gap: 8px; }
.attach-btn { cursor: pointer; font-size: 20px; padding: 6px; border-radius: 8px; background: var(--bg2); border: 1px solid var(--border); }
.attach-btn:hover { border-color: var(--accent); }
textarea { flex: 1; background: var(--bg2); border: 1px solid var(--border); border-radius: 12px; padding: 10px 14px; font-size: 14px; color: var(--text); resize: none; outline: none; font-family: inherit; line-height: 1.5; min-height: 42px; max-height: 120px; }
textarea:focus { border-color: var(--accent); }
.send-btn { width: 42px; height: 42px; border-radius: 12px; background: var(--accent); color: #fff; border: none; font-size: 18px; cursor: pointer; display: flex; align-items: center; justify-content: center; transition: all .15s; flex-shrink: 0; }
.send-btn:disabled { opacity: .4; cursor: not-allowed; }
.send-btn:not(:disabled):hover { transform: scale(1.05); }
.input-footer { display: flex; justify-content: space-between; margin-top: 4px; font-size: 11px; color: var(--text3); }
.agent-hint { color: var(--warn); }
</style>