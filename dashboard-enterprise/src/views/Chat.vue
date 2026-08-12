<template>
  <div class="chat-page">
    <PageHeader v-if="!embedded">
      <button class="refresh-btn" :title="t('view.chat.newSession')" @click="newSession">
        <AppIcon name="plus" :size="15" />
        <span>{{ t('view.chat.newSession') }}</span>
      </button>
    </PageHeader>

    <div class="chat-split">
      <aside class="session-sidebar">
        <div class="session-sidebar__head">
          <span class="session-sidebar__title">{{ t('view.chat.sessions') }}</span>
        </div>
      <div class="session-list">
        <div v-if="sessionsLoading" class="session-loading">{{ t('view.chat.loadingSessions') }}</div>
        <EmptyState v-else-if="!sessions.length" icon="message-square" :title="t('view.chat.noSessions')" :description="t('view.chat.noSessionsHint')" />
        <div
          v-for="s in sessions"
          v-else
          :key="s.id"
          class="session-item"
          :class="{ active: sessionId === s.id }"
          role="button"
          tabindex="0"
          @click="selectSession(s)"
          @keydown.enter="selectSession(s)"
        >
          <div class="session-item__main">
            <div class="session-item__title">{{ sessionTitle(s) }}</div>
            <div class="session-item__meta">
              <span v-if="s.agent" class="session-item__agent">{{ s.agent }}</span>
              <span>{{ s.message_count || 0 }} {{ t('view.chat.sessionMessages') }}</span>
              <span>{{ sessionTime(s) }}</span>
            </div>
          </div>
          <button v-if="isAdmin" class="session-del" :title="t('view.chat.deleteSession')" @click.stop="deleteSession(s)">
            <AppIcon name="trash" :size="14" />
          </button>
        </div>
      </div>
    </aside>

    <div class="chat-main">
    <div class="chat-header">
      <div class="chat-header__left">
        <span class="chat-header__avatar"><AppIcon :name="selectedAgent ? 'bot' : 'chat'" :size="18" /></span>
        <div class="chat-header__titles">
          <div class="chat-header__title">{{ headerTitle }}</div>
          <div v-if="headerSub" class="chat-header__sub">{{ headerSub }}</div>
        </div>
      </div>
      <div class="chat-header__actions">
        <div class="agent-select">
          <label>{{ t('view.chat.agent') }}</label>
          <select v-model="selectedAgent" @change="onAgentChange">
            <option value="">{{ t('view.chat.selectAgent') }}</option>
            <option v-for="a in agents" :key="a.name" :value="a.name">{{ a.name }}</option>
          </select>
        </div>
      </div>
    </div>

    <div ref="chatBody" class="chat-body">
      <div v-if="messages.length === 0" class="welcome-msg">
        <div class="welcome-icon"><AppIcon name="chat" :size="40" /></div>
        <h2>{{ t('view.chat.startConversation') }}</h2>
        <p>{{ t('view.chat.welcomeHint') }}</p>
      </div>

      <div v-for="(msg, i) in messages" :key="i" :class="['msg-row', msg.role]">
        <div class="msg-avatar">
          <AppIcon :name="msg.role === 'user' ? 'user' : 'bot'" :size="18" />
        </div>
        <div class="msg-bubble">
          <div class="msg-content">
            <img v-if="msg.image" :src="msg.image" :alt="t('view.chat.attachImage')" class="msg-image" />
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
        <div class="msg-avatar"><AppIcon name="bot" :size="18" /></div>
        <div class="msg-bubble streaming">
          <div class="msg-text" v-html="renderMarkdown(streamContent)"></div>
          <span class="cursor">▊</span>
          <div v-if="streamTokenCount > 0" class="stream-meta">
            <span class="stream-tokens">{{ streamTokenCount }} tokens</span>
            <span v-if="streamSpeed > 0" class="stream-speed">{{ streamSpeed }} tok/s</span>
          </div>
        </div>
      </div>
    </div>

    <div class="chat-input-area">
      <div v-if="pendingImage" class="image-preview">
        <img :src="pendingImage" :alt="t('view.chat.attachImage')" />
        <button class="remove-img" :aria-label="t('view.chat.removeImage')" @click="pendingImage = null">
          <AppIcon name="x" :size="12" />
        </button>
      </div>
      <div class="input-row">
        <label class="attach-btn" :title="t('view.chat.attachImage')">
          <AppIcon name="paperclip" :size="20" />
          <input type="file" accept="image/*" hidden @change="onImageAttach" />
        </label>
        <textarea
          ref="inputEl"
          v-model="inputText"
          :placeholder="t('view.chat.inputPlaceholder')"
          rows="1"
          @keydown.enter.exact="onEnter"
        ></textarea>
        <button class="send-btn" :disabled="(!inputText.trim() && !pendingImage) || !selectedAgent || streaming" @click="sendMessage()">
          <AppIcon :name="streaming ? 'refresh' : 'send'" :size="18" :class="{ spinning: streaming }" />
        </button>
      </div>
      <div class="input-footer">
        <span class="char-count">{{ inputText.length }} chars</span>
        <span v-if="!selectedAgent" class="agent-hint"><AppIcon name="alert-triangle" :size="13" /> {{ t('view.chat.selectAgentFirst') }}</span>
      </div>
    </div>
    </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, nextTick, watch } from 'vue';
import { useApiStore } from '../stores/api.js';
import { useStreamingFetch } from '../composables/useStreamingFetch.js';
import { useToast } from '../composables/useToast.js';
import AppIcon from '../components/AppIcon.vue';
import PageHeader from '../components/PageHeader.vue';
import { EmptyState } from '../components/index.js';
import DOMPurify from 'dompurify';
import { useI18n } from '../i18n';

const { t } = useI18n();
// 嵌入式模式: 作为 Run.vue 的 Tab 子视图时, 隐藏自身 PageHeader
defineProps({
  embedded: { type: Boolean, default: false },
});
const api = useApiStore();
// F-P0-3 fix: composable must be called at setup top level, not inside function
const { stream } = useStreamingFetch();
// G1c fix: toast 用于 onImageAttach 中的图片大小校验提示
const toast = useToast();
const agents = ref([]);
const selectedAgent = ref('');
const sessionId = ref('');
const messages = ref([]);
const inputText = ref('');
const pendingImage = ref(null);
const streaming = ref(false);
let streamAbort = null;
const streamContent = ref('');
const streamTokenCount = ref(0);
const streamSpeed = ref(0);
let streamStartTime = 0;
const chatBody = ref(null);
const inputEl = ref(null);

// ── Session list (backend: GET /api/chat/sessions) ────────────────────────
const sessions = ref([]);
const sessionsLoading = ref(false);
const isAdmin = ref(false);

// ── Live chat header (reflects the active conversation, not a static title) ──
const activeSession = computed(() => sessions.value.find((s) => s.id === sessionId.value) || null);
const headerTitle = computed(() => {
  if (activeSession.value) return sessionTitle(activeSession.value);
  if (selectedAgent.value) return `${selectedAgent.value} · ${t('view.chat.newConversation')}`;
  return t('view.chat.title');
});
const headerSub = computed(() => {
  if (activeSession.value) {
    return `ID ${activeSession.value.id.slice(0, 8)} · ${activeSession.value.message_count || 0} ${t('view.chat.sessionMessages')}`;
  }
  return t('view.chat.selectAgentToStart');
});

async function detectAdmin() {
  try {
    const rolesStr = localStorage.getItem('maop_roles');
    if (rolesStr) {
      const roles = JSON.parse(rolesStr);
      if (Array.isArray(roles) && roles.some((r) => r === 'admin' || r === 'superadmin')) return true;
    }
  } catch (e) { /* ignore */ }
  // Auth disabled (e.g. MAOP_AUTH_DISABLED_ADMIN) → treat the session as superuser.
  try {
    const d = await api.get('/api/auth/status');
    if (d && d.auth_enabled === false) return true;
  } catch (e) { /* ignore */ }
  try { return localStorage.getItem('maop_user') === 'admin'; } catch (e) { return false; }
}

function fmtDate(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return '';
  const now = new Date();
  const sameDay = d.toDateString() === now.toDateString();
  const hhmm = d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' });
  return sameDay ? hhmm : `${d.toLocaleDateString()} ${hhmm}`;
}
function sessionTitle(s) {
  const id = String(s.id || '');
  if (s.agent) return `${s.agent} · ${id.slice(0, 6)}`;
  return id ? id.slice(0, 12) : t('view.chat.untitled');
}
function sessionTime(s) { return fmtDate(s.updated_at || s.created_at); }

function mapMsg(m) {
  const meta = m.metadata || {};
  return {
    role: m.role || 'user',
    content: m.content || '',
    image: m.image || meta.image || null,
    time: fmtDate(m.timestamp || m.created_at || m.time),
    model: m.model || meta.model || '',
    tokens: m.tokens || m.token_count || meta.tokens || 0,
  };
}

async function loadSessions() {
  sessionsLoading.value = true;
  try {
    const r = await api.get('/api/chat/sessions');
    sessions.value = (r && r.data) ? r.data : [];
  } catch (e) {
    sessions.value = [];
  } finally {
    sessionsLoading.value = false;
  }
}
async function loadSessionMessages(id) {
  try {
    const r = await api.get(`/api/chat/${id}`);
    const msgs = (r && r.data && r.data.messages) || [];
    messages.value = msgs.map(mapMsg);
  } catch (e) {
    messages.value = [];
  }
  await nextTick();
  scrollBottom();
}
async function selectSession(s) {
  sessionId.value = s.id;
  messages.value = [];
  if (s.agent && agents.value.some((a) => a.name === s.agent)) selectedAgent.value = s.agent;
  await loadSessionMessages(s.id);
}
async function deleteSession(s) {
  if (!confirm(t('view.chat.deleteSessionConfirm', { title: sessionTitle(s) }))) return;
  try {
    await api.delete(`/api/chat/${s.id}`);
    sessions.value = sessions.value.filter((x) => x.id !== s.id);
    if (sessionId.value === s.id) { sessionId.value = ''; messages.value = []; }
  } catch (e) {
    alert(t('view.chat.deleteFailed') + ': ' + (e && e.message ? e.message : 'error'));
  }
}

// Normalize /api/agents (returns a dict of registry→agent-list, not an array)
function toList(d) {
  if (Array.isArray(d)) return d;
  if (d && typeof d === 'object') {
    const arr = Object.values(d).find((v) => Array.isArray(v));
    return arr || [];
  }
  return [];
}

function renderMarkdown(text) {
  if (!text) return '';
  const html = text
    .replace(/```(\w*)\n([\s\S]*?)```/g, '<pre><code class="$1">$2</code></pre>')
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/\n/g, '<br>');
  // P0 fix: sanitize HTML to prevent XSS from LLM output
  return DOMPurify.sanitize(html, {
    ALLOWED_TAGS: ['pre', 'code', 'strong', 'em', 'br', 'p', 'ul', 'ol', 'li', 'a', 'span'],
    ALLOWED_ATTR: ['class', 'href', 'target', 'rel'],
  });
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
  if (file.size > 5 * 1024 * 1024) { toast.error(t('view.chat.imageTooLarge') || 'Image must be under 5MB'); return; }
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
  streamTokenCount.value = 0;
  streamSpeed.value = 0;
  streamStartTime = Date.now();

  // Cancel any in-flight stream before starting a new one
  if (streamAbort) { streamAbort.abort(); streamAbort = null; }
  streamAbort = new AbortController();

  try {
    // P0-1 fix: use /api/chat/stream (SSE) instead of /api/chat (JSON)
    // F-P0-3 fix: stream() is now initialized at setup top level
    const body = {
      agent: selectedAgent.value,
      message: text,
      session_id: sessionId.value || undefined,
      images: userMsg.image ? [userMsg.image] : undefined,  // P1-11 fix: images (plural list)
    };

    const msgMeta = {};

    await stream('/api/chat/stream', body, {
      signal: streamAbort.signal,
      onData: async (fullContent) => {
        streamContent.value = fullContent;
        // v5.0.0: token count + speed tracking
        streamTokenCount.value = Math.ceil(fullContent.length / 4);
        const elapsed = (Date.now() - streamStartTime) / 1000;
        if (elapsed > 0.1) {
          streamSpeed.value = Math.round(streamTokenCount.value / elapsed);
        }
        await nextTick();
        scrollBottom();
      },
      onMeta: (meta) => {
        if (meta.session_id && meta.session_id !== sessionId.value) {
          sessionId.value = meta.session_id;
          loadSessions();
        }
        Object.assign(msgMeta, meta);
      },
      onDone: () => {
        streaming.value = false;
        streamAbort = null;
        messages.value.push({
          role: 'assistant',
          content: streamContent.value,
          time: now(),
          tokens: streamTokenCount.value,
          ...msgMeta,
        });
        streamContent.value = '';
        streamTokenCount.value = 0;
        streamSpeed.value = 0;
      },
      onError: (errMsg) => {
        streaming.value = false;
        streamAbort = null;
        streamTokenCount.value = 0;
        streamSpeed.value = 0;
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
  isAdmin.value = await detectAdmin();
  loadSessions();
  try {
    const data = await api.get('/api/agents');
    agents.value = toList(data);
    if (agents.value.length > 0 && !selectedAgent.value) {
      selectedAgent.value = agents.value[0].name;
    }
  } catch (e) {
    console.warn('[chat] agent list load failed:', e && e.message);
  }
});
</script>

<style scoped>
/* 2026-08-12 重构说明:
 * 高度/flex 等高链由全局 pages.css 统一管理(.chat-page/.chat-split/.chat-main/
 * .chat-body 已在那儿定义), 本 scoped 块只保留组件私有样式, 不再重复声明布局,
 * 避免两套规则互相覆盖导致分辨率抖动。JS ResizeObserver 强制同步已删除。 */
.stream-meta {
  display: flex;
  gap: 8px;
  margin-top: 4px;
  font-size: 11px;
  color: var(--text-faint, #999);
}
.stream-tokens, .stream-speed {
  font-variant-numeric: tabular-nums;
}
</style>
