<template>
  <div class="tools-page">
    <div class="topbar">
      <h1>Tools & Skills</h1>
      <div class="tab-bar">
        <button v-for="t in tabs" :key="t.key" :class="['tab-btn', { active: activeTab === t.key }]" @click="activeTab = t.key">{{ t.icon }} {{ t.label }}</button>
      </div>
      <button class="btn-action" @click="loadAll">↻ Refresh</button>
    </div>

    <div v-if="activeTab === 'skills'">
      <div class="skill-grid" v-if="skills.length">
        <div class="skill-card" v-for="s in skills" :key="s.name || s.id">
          <div class="skill-top">
            <div class="skill-icon" :style="{ background: skillColor(s.name) }">{{ (s.name || '?').charAt(0).toUpperCase() }}</div>
            <div class="skill-identity">
              <h4>{{ s.name }}</h4>
              <span class="status-badge" :class="skillStatusClass(s)">{{ s.status || 'active' }}</span>
            </div>
          </div>
          <p class="skill-desc">{{ s.description || 'No description' }}</p>
          <div class="skill-meta" v-if="s.version || s.category">
            <span v-if="s.version" class="meta-tag">v{{ s.version }}</span>
            <span v-if="s.category" class="meta-tag">{{ s.category }}</span>
          </div>
        </div>
      </div>
      <div class="empty" v-else>No skills loaded</div>
    </div>

    <div v-if="activeTab === 'mcp'">
      <div class="two-col">
        <div class="panel">
          <h3>MCP Servers</h3>
          <div class="mcp-list" v-if="mcpServers.length">
            <div class="mcp-row" v-for="s in mcpServers" :key="s.name || s.id">
              <div class="mcp-id">
                <span class="mcp-name">{{ s.name }}</span>
                <span class="status-badge" :class="s.status === 'connected' || s.healthy !== false ? 'healthy' : 'unhealthy'">{{ s.status || (s.healthy !== false ? 'connected' : 'disconnected') }}</span>
              </div>
              <div class="mcp-tools" v-if="s.tools && s.tools.length">
                <span class="tool-tag" v-for="t in s.tools.slice(0, 5)" :key="t.name || t">{{ t.name || t }}</span>
                <span class="tool-more" v-if="s.tools.length > 5">+{{ s.tools.length - 5 }}</span>
              </div>
              <div class="mcp-tools" v-else-if="s.tool_count">
                <span class="tool-count">{{ s.tool_count }} tools</span>
              </div>
            </div>
          </div>
          <div class="empty" v-else>No MCP servers</div>
        </div>

        <div class="panel">
          <h3>Routing Table</h3>
          <div class="routing-table" v-if="routes.length">
            <div class="routing-header">
              <span>Pattern</span><span>Target</span><span>Priority</span>
            </div>
            <div class="routing-row" v-for="r in routes" :key="r.pattern || r.name">
              <span class="mono">{{ r.pattern || r.match || '—' }}</span>
              <span class="route-target">{{ r.target || r.server || r.agent || '—' }}</span>
              <span>{{ r.priority ?? '—' }}</span>
            </div>
          </div>
          <div class="empty" v-else>No routing entries</div>
        </div>
      </div>
    </div>

    <div v-if="activeTab === 'prompts'">
      <div class="prompt-list" v-if="prompts.length">
        <div class="prompt-card" v-for="p in prompts" :key="p.name || p.id">
          <div class="prompt-header">
            <h4>{{ p.name }}</h4>
            <button class="btn-copy" @click="copyPrompt(p)">Copy</button>
          </div>
          <pre class="prompt-content">{{ p.content || p.template || p.body || '' }}</pre>
          <div class="prompt-meta" v-if="p.category || p.version">
            <span v-if="p.category" class="meta-tag">{{ p.category }}</span>
            <span v-if="p.version" class="meta-tag">v{{ p.version }}</span>
          </div>
        </div>
      </div>
      <div class="empty" v-else>No prompt templates</div>
    </div>

    <div v-if="activeTab === 'security'">
      <div class="panel" v-if="securityConfig">
        <h3>Security Configuration</h3>
        <div class="sec-grid">
          <div class="sec-item" v-for="(v, k) in securityDisplay" :key="k">
            <span class="sec-key">{{ k }}</span>
            <span class="sec-val" :class="typeof v === 'boolean' ? (v ? 'on' : 'off') : ''">{{ typeof v === 'boolean' ? (v ? 'Enabled' : 'Disabled') : v }}</span>
          </div>
        </div>
      </div>
      <div class="empty" v-else>No security configuration loaded</div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue';
import { useApiStore } from '../stores/api.js';

const api = useApiStore();

const activeTab = ref('skills');
const skills = ref([]);
const mcpServers = ref([]);
const routes = ref([]);
const prompts = ref([]);
const securityConfig = ref(null);

const tabs = [
  { key: 'skills', label: 'Skills', icon: '⚡' },
  { key: 'mcp', label: 'MCP', icon: '🔌' },
  { key: 'prompts', label: 'Prompts', icon: '📝' },
  { key: 'security', label: 'Security', icon: '🔒' },
];

function skillColor(name) {
  const colors = ['#3b82f6', '#a78bfa', '#22c55e', '#f59e0b', '#ef4444', '#06b6d4'];
  let hash = 0;
  for (let i = 0; i < (name || '').length; i++) hash = name.charCodeAt(i) + ((hash << 5) - hash);
  return colors[Math.abs(hash) % colors.length];
}

function skillStatusClass(s) {
  if (s.status === 'error' || s.status === 'disabled') return 'unhealthy';
  if (s.status === 'warning' || s.status === 'deprecated') return 'warn';
  return 'healthy';
}

const securityDisplay = computed(() => {
  if (!securityConfig.value) return {};
  const c = securityConfig.value;
  const display = {};
  const keys = Object.keys(c);
  for (const k of keys) {
    if (typeof c[k] === 'object' && c[k] !== null) continue;
    display[k.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase())] = c[k];
  }
  return display;
});

function copyPrompt(p) {
  const text = p.content || p.template || p.body || '';
  if (navigator.clipboard) {
    navigator.clipboard.writeText(text);
  }
}

async function loadSkills() {
  try { const d = await api.get('/api/skills'); skills.value = d.skills || d || []; } catch {}
}

async function loadMcp() {
  try { const d = await api.get('/api/mcp'); mcpServers.value = d.servers || d || []; } catch {}
}

async function loadRouting() {
  try { const d = await api.get('/api/routing'); routes.value = d.routes || d || []; } catch {}
}

async function loadPrompts() {
  try { const d = await api.get('/api/prompts'); prompts.value = d.prompts || d.templates || d || []; } catch {}
}

async function loadSecurity() {
  try { const d = await api.get('/api/security/config'); securityConfig.value = d.config || d; } catch {}
}

function loadAll() {
  loadSkills();
  loadMcp();
  loadRouting();
  loadPrompts();
  loadSecurity();
}

onMounted(loadAll);
</script>

<style scoped>
.tools-page { }
.topbar { display: flex; align-items: center; gap: 12px; margin-bottom: 20px; }
.topbar h1 { font-size: 24px; font-weight: 700; }
.tab-bar { display: flex; gap: 4px; background: var(--bg2); border-radius: 10px; padding: 3px; margin-left: 16px; }
.tab-btn { background: none; border: none; padding: 6px 14px; border-radius: 8px; font-size: 13px; color: var(--text2); cursor: pointer; }
.tab-btn.active { background: var(--accent); color: #fff; }
.btn-action { margin-left: auto; background: var(--bg2); border: 1px solid var(--border); border-radius: 10px; padding: 6px 14px; font-size: 13px; color: var(--text2); cursor: pointer; }

.skill-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 16px; }
.skill-card { background: var(--bg2); border: 1px solid var(--border); border-radius: var(--radius); padding: 16px; transition: all .15s; }
.skill-card:hover { border-color: var(--accent); }
.skill-top { display: flex; align-items: center; gap: 12px; margin-bottom: 10px; }
.skill-icon { width: 36px; height: 36px; border-radius: 10px; display: flex; align-items: center; justify-content: center; color: #fff; font-size: 16px; font-weight: 700; }
.skill-identity { flex: 1; }
.skill-identity h4 { font-size: 14px; font-weight: 600; margin-bottom: 2px; }
.skill-desc { font-size: 12px; color: var(--text2); line-height: 1.5; margin-bottom: 8px; }
.skill-meta { display: flex; gap: 6px; }
.meta-tag { font-size: 10px; padding: 2px 8px; border-radius: 4px; background: var(--bg3); color: var(--text2); }

.status-badge { padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; }
.status-badge.healthy { background: rgba(34,197,94,.15); color: var(--success); }
.status-badge.unhealthy { background: rgba(239,68,68,.15); color: var(--fail); }
.status-badge.warn { background: rgba(245,158,11,.15); color: var(--warn); }

.two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.panel { background: var(--bg2); border: 1px solid var(--border); border-radius: var(--radius); padding: 20px; }
.panel h3 { font-size: 14px; font-weight: 600; color: var(--text2); margin-bottom: 16px; }

.mcp-row { padding: 10px 12px; border-bottom: 1px solid var(--border); }
.mcp-row:last-child { border-bottom: none; }
.mcp-id { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
.mcp-name { font-weight: 600; font-size: 13px; }
.mcp-tools { display: flex; flex-wrap: wrap; gap: 4px; }
.tool-tag { font-size: 10px; padding: 1px 6px; border-radius: 3px; background: var(--bg3); color: var(--text2); font-family: monospace; }
.tool-more { font-size: 10px; color: var(--text2); padding: 1px 4px; }
.tool-count { font-size: 11px; color: var(--text2); }

.routing-table { }
.routing-header { display: grid; grid-template-columns: 1.5fr 1fr 0.6fr; gap: 8px; padding: 8px 12px; font-size: 11px; font-weight: 600; color: var(--text2); text-transform: uppercase; letter-spacing: .5px; border-bottom: 1px solid var(--border); }
.routing-row { display: grid; grid-template-columns: 1.5fr 1fr 0.6fr; gap: 8px; padding: 8px 12px; font-size: 13px; align-items: center; border-bottom: 1px solid var(--border); }
.routing-row:last-child { border-bottom: none; }
.route-target { font-weight: 600; color: var(--accent); }
.mono { font-family: monospace; font-size: 12px; }

.prompt-list { display: grid; grid-template-columns: repeat(auto-fill, minmax(400px, 1fr)); gap: 16px; }
.prompt-card { background: var(--bg2); border: 1px solid var(--border); border-radius: var(--radius); padding: 16px; }
.prompt-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 10px; }
.prompt-header h4 { font-size: 14px; font-weight: 600; }
.btn-copy { background: var(--bg3); border: 1px solid var(--border); border-radius: 6px; padding: 3px 10px; font-size: 11px; color: var(--text2); cursor: pointer; }
.btn-copy:hover { border-color: var(--accent); color: var(--accent); }
.prompt-content { background: var(--bg); border: 1px solid var(--border); border-radius: 8px; padding: 12px; font-size: 12px; line-height: 1.6; max-height: 200px; overflow-y: auto; white-space: pre-wrap; word-break: break-word; margin-bottom: 8px; font-family: monospace; color: var(--text1); }
.prompt-meta { display: flex; gap: 6px; }

.sec-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
.sec-item { display: flex; justify-content: space-between; padding: 8px 12px; background: var(--bg); border-radius: 6px; font-size: 13px; }
.sec-key { color: var(--text2); }
.sec-val { font-weight: 600; font-size: 12px; }
.sec-val.on { color: var(--success); }
.sec-val.off { color: var(--fail); }

.empty { font-size: 13px; color: var(--text2); padding: 32px; text-align: center; }
</style>