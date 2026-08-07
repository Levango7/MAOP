<template>
  <div class="tools-view">
    <PageHeader>
      <Segmented
        :model-value="activeTab"
        :options="tabOptions"
        size="sm"
        @update:model-value="activeTab = $event"
      />
      <button class="btn-ghost" :class="{ 'is-busy': loading }" @click="load" :disabled="loading">
        <AppIcon name="refresh" :size="15" />
        <span>{{ t('common.refresh') }}</span>
      </button>
    </PageHeader>

    <!-- Skills -->
    <div v-show="activeTab === 'skills'">
      <div class="skills-head">
        <Segmented
          :model-value="skillFilter"
          :options="skillFilterOptions"
          size="sm"
          @update:model-value="skillFilter = $event"
        />
        <div class="skills-actions">
          <button class="btn-ghost" @click="openCreate">
            <AppIcon name="plus" :size="15" /><span>{{ t('view.tools.createSkill') }}</span>
          </button>
          <button class="btn-ghost" @click="triggerImport">
            <AppIcon name="download" :size="15" /><span>{{ t('view.tools.importSkill') }}</span>
            <input ref="importInput" type="file" accept=".json,application/json" hidden @change="onImport" />
          </button>
        </div>
      </div>

      <template v-for="sec in skillSections" :key="sec.key">
        <Card v-if="skillFilter === 'all' || skillFilter === sec.key" :title="sec.title" icon="sparkles" :margin-bottom="16">
          <div v-if="loading" class="blk"><Skeleton block height="48px" /><Skeleton block height="48px" /><Skeleton block height="48px" /></div>
          <EmptyState v-else-if="errors.skills" icon="alert-triangle" tone="fail" :title="t('view.tools.failedLoadSkills')" :description="errors.skills" />
          <div v-else-if="sec.items.length" class="skill-grid">
            <div v-for="s in sec.items" :key="s.name || s.id" class="skill-card">
              <div class="skill-card__top">
                <div class="skill-card__icon"><AppIcon name="sparkles" :size="16" /></div>
                <div class="skill-card__id">
                  <h4>{{ s.name || s.id || t('view.tools.unknown') }}</h4>
                  <Badge :tone="skillTone(s)">{{ skillLabel(s) }}</Badge>
                </div>
              </div>
              <p class="skill-card__desc">{{ s.description || t('view.tools.noDescription') }}</p>
              <div class="skill-card__meta" v-if="s.version || s.category">
                <Badge v-if="s.version" tone="neutral">v{{ s.version }}</Badge>
                <Badge v-if="s.category" tone="info">{{ s.category }}</Badge>
              </div>
            </div>
          </div>
          <EmptyState v-else :icon="sec.emptyIcon" :title="sec.emptyTitle" :description="sec.emptyHint" />
        </Card>
      </template>
    </div>

    <!-- MCP -->
    <div v-show="activeTab === 'mcp'">
      <div class="grid-2">
        <Card :title="t('view.tools.mcpServers')" icon="wrench" :margin-bottom="0">
          <div v-if="loading" class="blk"><Skeleton block height="40px" /><Skeleton block height="40px" /></div>
          <EmptyState v-else-if="errors.mcp" icon="alert-triangle" tone="fail" :title="t('view.tools.failedLoadMcp')" :description="errors.mcp" />
          <EmptyState v-else-if="!servers.length" icon="wrench" :title="t('view.tools.noMcp')" :description="t('view.tools.noMcpHint')" />
          <ul v-else class="mcp-list">
            <li v-for="s in servers" :key="s.name" class="mcp-row">
              <div class="mcp-row__head">
                <span class="mcp-row__name">{{ s.name }}</span>
                <Badge :tone="s.enabled === false ? 'neutral' : 'success'">{{ s.enabled === false ? t('common.disable') : t('common.enable') }}</Badge>
              </div>
              <div class="mcp-row__meta">
                <span class="mono">{{ s.transport || '—' }}</span>
                <span class="muted">{{ s.url || '' }}</span>
              </div>
            </li>
          </ul>
        </Card>

        <Card :title="t('view.tools.toolInventory')" icon="box" :margin-bottom="0">
          <div v-if="loading" class="blk"><Skeleton block height="40px" /></div>
          <div v-else class="inv">
            <div class="inv__stat">
              <span class="inv__num">{{ serverCount }}</span>
              <span class="inv__lbl">{{ t('view.tools.servers') }}</span>
            </div>
            <div class="inv__stat">
              <span class="inv__num">{{ toolCount }}</span>
              <span class="inv__lbl">{{ t('view.tools.exposedTools') }}</span>
            </div>
          </div>
        </Card>
      </div>
    </div>

    <!-- P2-11: MCP Topology -->
    <div v-show="activeTab === 'topology'">
      <Card :title="t('view.tools.topo.title')" icon="share2" :margin-bottom="0">
        <McpTopology
          :data="topology"
          :loading="topologyLoading"
          :error="errors.topology || ''"
          @refresh="loadTopology"
        />
      </Card>
    </div>

    <!-- Routing -->
    <div v-show="activeTab === 'routing'">
      <Card :title="t('view.tools.routingTable')" icon="route" :margin-bottom="0">
        <div v-if="loading" class="blk"><Skeleton block height="32px" /><Skeleton block height="32px" /></div>
        <EmptyState v-else-if="errors.routing" icon="alert-triangle" tone="fail" :title="t('view.tools.failedLoadRouting')" :description="errors.routing" />
        <DataTable
          v-else
          :columns="routeCols"
          :rows="routeRows"
          :empty-text="t('view.tools.noRoutingEntries')"
          compact
        />
      </Card>
    </div>

    <!-- Prompts -->
    <div v-show="activeTab === 'prompts'">
      <Card :title="t('view.tools.promptTemplates')" icon="scroll" :margin-bottom="0">
        <div v-if="loading" class="blk"><Skeleton block height="32px" /><Skeleton block height="32px" /></div>
        <EmptyState v-else-if="errors.prompts" icon="alert-triangle" tone="fail" :title="t('view.tools.failedLoadPrompts')" :description="errors.prompts" />
        <DataTable
          v-else
          :columns="promptCols"
          :rows="prompts"
          :empty-text="t('view.tools.noPromptTemplates')"
          compact
        />
      </Card>
    </div>

    <!-- Security -->
    <div v-show="activeTab === 'security'">
      <Card :title="t('view.tools.securityConfig')" icon="shield" :margin-bottom="0">
        <div v-if="loading" class="blk"><Skeleton block height="32px" /><Skeleton block height="32px" /><Skeleton block height="32px" /></div>
        <EmptyState v-else-if="errors.security" icon="alert-triangle" tone="fail" :title="t('view.tools.failedLoadConfig')" :description="errors.security" />
        <div v-else class="sec-grid">
          <div v-for="k in securityKeys" :key="k" class="sec-item">
            <span class="sec-item__key">{{ labelize(k) }}</span>
            <Badge :tone="security[k] ? 'success' : 'fail'">{{ security[k] ? t('common.enable') : t('common.disable') }}</Badge>
          </div>
        </div>
      </Card>
    </div>

    <!-- Create skill modal -->
    <Teleport to="body">
      <div v-if="showCreate" class="modal-mask" @click.self="closeCreate">
        <div class="modal" role="dialog" aria-modal="true">
          <div class="modal__head">
            <h3>{{ t('view.tools.createSkillTitle') }}</h3>
            <button class="modal__x" type="button" @click="closeCreate" aria-label="Close">×</button>
          </div>
          <div class="modal__body">
            <label class="field">
              <span class="field__label">{{ t('view.tools.skillName') }}</span>
              <input v-model="form.name" class="field__input" :placeholder="t('view.tools.skillName')" />
            </label>
            <label class="field">
              <span class="field__label">{{ t('view.tools.skillDesc') }}</span>
              <textarea v-model="form.description" class="field__input" rows="3" :placeholder="t('view.tools.skillDesc')"></textarea>
            </label>
            <label class="field">
              <span class="field__label">{{ t('view.tools.skillCategory') }}</span>
              <input v-model="form.category" class="field__input" :placeholder="t('view.tools.skillCategory')" />
            </label>
          </div>
          <div class="modal__foot">
            <button class="btn-ghost" type="button" @click="closeCreate">{{ t('view.tools.cancel') }}</button>
            <button class="btn-primary" type="button" :disabled="!form.name.trim() || creating" @click="submitCreate">
              {{ creating ? t('view.tools.creating') : t('view.tools.create') }}
            </button>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue';
import { useApiStore } from '../stores/api.js';
import { useI18n } from '../i18n';
import { AppIcon, Card, Badge, DataTable, Segmented, Skeleton, EmptyState, PageHeader } from '../components/index.js';
import McpTopology from '../components/McpTopology.vue';

const api = useApiStore();
const { t } = useI18n();

const activeTab = ref('skills');
const loading = ref(false);
const errors = ref({ skills: null, mcp: null, routing: null, prompts: null, security: null, topology: null });

const skills = ref([]);
const servers = ref([]);
const serverCount = ref(0);
const toolCount = ref(0);
const routes = ref([]);
const prompts = ref([]);
const security = ref({});

// P2-11: MCP topology state
const topology = ref({ servers: [], tools: [], agents: [], edges: [] });
const topologyLoading = ref(false);

// ── Skills: three functional areas (built-in / imported / custom) ──────
// Backend /api/skills does not tag a source today, so we derive one from
// the skill path (falling back to 'builtin'), and keep user-created /
// imported skills in localStorage so the three areas are always visible.
//
// Built-in skills fallback: when /api/skills returns empty (no category="skill"
// tools registered, no skills/ directory), we surface MAOP's actual capabilities
// as first-class built-in skills so the section is never empty.
const BUILTIN_SKILLS_FALLBACK = [
  { name: 'model-manager',   description: t('view.tools.builtin.modelManager'),   category: 'model',     version: '1.0', source: 'builtin' },
  { name: 'log-query',       description: t('view.tools.builtin.logQuery'),       category: 'logging',   version: '1.0', source: 'builtin' },
  { name: 'monitor',         description: t('view.tools.builtin.monitor'),         category: 'ops',        version: '1.0', source: 'builtin' },
  { name: 'vector-search',    description: t('view.tools.builtin.vectorSearch'),    category: 'search',     version: '1.0', source: 'builtin' },
  { name: 'agent-orchestrator', description: t('view.tools.builtin.agentOrch'),  category: 'agent',      version: '1.0', source: 'builtin' },
  { name: 'memory-manager',   description: t('view.tools.builtin.memoryManager'),  category: 'memory',     version: '1.0', source: 'builtin' },
  { name: 'skill-router',     description: t('view.tools.builtin.skillRouter'),    category: 'routing',    version: '1.0', source: 'builtin' },
  { name: 'security-audit',   description: t('view.tools.builtin.securityAudit'),  category: 'security',   version: '1.0', source: 'builtin' },
];

const LS_IMPORTED = 'maop_imported_skills';
const LS_CUSTOM = 'maop_custom_skills';
function loadLocal(key, fallback) {
  try { const v = localStorage.getItem(key); return v ? JSON.parse(v) : fallback; } catch { return fallback; }
}
function saveLocal(key, val) {
  try { localStorage.setItem(key, JSON.stringify(val)); } catch { /* storage may be unavailable */ }
}
const importedSkills = ref(loadLocal(LS_IMPORTED, []));
const customSkills = ref(loadLocal(LS_CUSTOM, []));

const skillFilter = ref('all');
const skillFilterOptions = [
  { value: 'all', label: t('view.tools.filterAll') },
  { value: 'builtin', label: t('view.tools.filterBuiltin') },
  { value: 'imported', label: t('view.tools.filterImported') },
  { value: 'custom', label: t('view.tools.filterCustom') },
];

const skillSections = computed(() => {
  const builtinItems = skills.value.length > 0
    ? skills.value.filter((s) => skillSource(s) === 'builtin')
    : BUILTIN_SKILLS_FALLBACK;
  return [
  {
    key: 'builtin',
    title: t('view.tools.builtinSkills'),
    items: builtinItems,
    emptyTitle: t('view.tools.noBuiltin'),
    emptyHint: t('view.tools.noBuiltinHint'),
    emptyIcon: 'sparkles',
  },
  {
    key: 'imported',
    title: t('view.tools.importedSkills'),
    items: importedSkills.value,
    emptyTitle: t('view.tools.noImported'),
    emptyHint: t('view.tools.noImportedHint'),
    emptyIcon: 'download',
  },
  {
    key: 'custom',
    title: t('view.tools.customSkills'),
    items: customSkills.value,
    emptyTitle: t('view.tools.noCustom'),
    emptyHint: t('view.tools.noCustomHint'),
    emptyIcon: 'plus',
  },
  ];
});
const importInput = ref(null);
function triggerImport() { if (importInput.value) importInput.value.click(); }
async function onImport(e) {
  const input = e.target;
  const file = input.files && input.files[0];
  input.value = '';
  if (!file) return;
  try {
    const data = JSON.parse(await file.text());
    const list = Array.isArray(data) ? data : [data];
    const next = list.map((d) => ({
      name: d.name || d.id || file.name.replace(/\.json$/i, ''),
      description: d.description || '',
      category: d.category || 'imported',
      version: d.version || '',
      source: 'imported',
    }));
    importedSkills.value = [...importedSkills.value, ...next];
    saveLocal(LS_IMPORTED, importedSkills.value);
  } catch (err) {
    errors.value = { ...errors.value, skills: 'Import failed: ' + (err && err.message ? err.message : err) };
  }
}

const showCreate = ref(false);
const creating = ref(false);
const form = ref({ name: '', description: '', category: '' });
function openCreate() { form.value = { name: '', description: '', category: '' }; showCreate.value = true; }
function closeCreate() { showCreate.value = false; }
async function submitCreate() {
  const name = form.value.name.trim();
  if (!name) return;
  creating.value = true;
  const item = {
    name,
    description: form.value.description.trim(),
    category: form.value.category.trim() || 'custom',
    version: '',
    source: 'custom',
  };
  customSkills.value = [...customSkills.value, item];
  saveLocal(LS_CUSTOM, customSkills.value);
  creating.value = false;
  closeCreate();
}

const tabOptions = [
  { value: 'skills', label: t('view.tools.tab.skills'), icon: 'sparkles' },
  { value: 'mcp', label: t('view.tools.tab.mcp'), icon: 'wrench' },
  { value: 'topology', label: t('view.tools.tab.topology'), icon: 'share2' },
  { value: 'routing', label: t('view.tools.tab.routing'), icon: 'route' },
  { value: 'prompts', label: t('view.tools.tab.prompts'), icon: 'scroll' },
  { value: 'security', label: t('view.tools.tab.security'), icon: 'shield' },
];

const securityKeys = computed(() => Object.keys(security.value || {}));

const routeCols = [
  { key: 'pattern', label: t('view.tools.col.pattern'), width: '40%' },
  { key: 'target', label: t('view.tools.col.target'), width: '40%' },
  { key: 'priority', label: t('view.tools.col.priority'), align: 'right', width: '20%' },
];
const routeRows = computed(() =>
  routes.value.map((r) => ({
    pattern: r.pattern || r.match || '—',
    target: r.target || r.server || r.agent || '—',
    priority: r.priority ?? '—',
  }))
);
const promptCols = [
  { key: 'name', label: t('common.name'), width: '50%' },
  { key: 'category', label: t('view.tools.col.category'), type: 'badge', width: '50%' },
];

function skillSource(s) {
  if (s.source) return s.source;
  const p = (s.path || '').toLowerCase();
  if (p.includes('imported')) return 'imported';
  if (p.includes('user') || p.includes('custom') || p.includes('.workbuddy')) return 'custom';
  return 'builtin';
}
function skillTone(s) {
  if (s.enabled === false || (s.status || '').toLowerCase() === 'disabled') return 'neutral';
  return 'success';
}
function skillLabel(s) {
  const src = skillSource(s);
  if (src === 'imported') return t('view.tools.filterImported');
  if (src === 'custom') return t('view.tools.filterCustom');
  return t('view.tools.filterBuiltin');
}
function labelize(k) {
  return k.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

async function load() {
  loading.value = true;
  const [sk, mc, rt, pr, sec] = await Promise.allSettled([
    api.get('/api/skills'),
    api.get('/api/mcp'),
    api.get('/api/routing'),
    api.get('/api/prompts'),
    api.get('/api/security/config'),
  ]);
  errors.value = { skills: null, mcp: null, routing: null, prompts: null, security: null };

  if (sk.status === 'fulfilled') {
    skills.value = (sk.value.skills || []).map((s) => ({ ...s, source: skillSource(s) }));
  } else errors.value.skills = (sk.reason && sk.reason.message) || 'Request failed';

  if (mc.status === 'fulfilled') {
    servers.value = mc.value.servers || [];
    serverCount.value = mc.value.server_count ?? servers.value.length;
    toolCount.value = mc.value.tool_count ?? (mc.value.tools || []).length;
  } else errors.value.mcp = (mc.reason && mc.reason.message) || 'Request failed';

  if (rt.status === 'fulfilled') routes.value = rt.value.routes || [];
  else errors.value.routing = (rt.reason && rt.reason.message) || 'Request failed';

  if (pr.status === 'fulfilled') prompts.value = pr.value.prompts || [];
  else errors.value.prompts = (pr.reason && pr.reason.message) || 'Request failed';

  if (sec.status === 'fulfilled') security.value = sec.value || {};
  else errors.value.security = (sec.reason && sec.reason.message) || 'Request failed';

  loading.value = false;
}

// P2-11: 加载 MCP 拓扑（servers ↔ tools ↔ agents）
async function loadTopology() {
  topologyLoading.value = true;
  try {
    const data = await api.get('/api/mcp/topology');
    topology.value = {
      servers: data.servers || [],
      tools: data.tools || [],
      agents: data.agents || [],
      edges: data.edges || [],
    };
    errors.value = { ...errors.value, topology: null };
  } catch (err) {
    errors.value = { ...errors.value, topology: (err && err.message) || t('view.tools.failedLoadTopology') };
  } finally {
    topologyLoading.value = false;
  }
}

onMounted(() => {
  load();
  loadTopology();
});
</script>

<style scoped>
</style>
