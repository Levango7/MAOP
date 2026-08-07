<template>
  <div class="mcp-topo">
    <!-- Toolbar -->
    <div class="mcp-topo__toolbar">
      <div class="mcp-topo__stats">
        <Badge tone="brand">{{ t('view.tools.topo.servers') }}: {{ stats.servers }}</Badge>
        <Badge tone="info">{{ t('view.tools.topo.tools') }}: {{ stats.tools }}</Badge>
        <Badge tone="success">{{ t('view.tools.topo.agents') }}: {{ stats.agents }}</Badge>
        <Badge tone="neutral">{{ t('view.tools.topo.edges') }}: {{ stats.edges }}</Badge>
      </div>
      <div class="mcp-topo__actions">
        <button class="btn-ghost" @click="fitView" :disabled="!ready">
          <AppIcon name="filter" :size="14" /> {{ t('view.tools.topo.fit') }}
        </button>
        <button class="btn-ghost" @click="emit('refresh')" :disabled="loading">
          <AppIcon name="refresh" :size="14" /> {{ t('common.refresh') }}
        </button>
      </div>
    </div>

    <!-- Error banner -->
    <div v-if="error" class="mcp-topo__notice mcp-topo__notice--error">
      <AppIcon name="alert" :size="14" /> {{ error }}
    </div>

    <!-- Canvas -->
    <div class="mcp-topo__canvas-wrap">
      <div v-if="loading" class="mcp-topo__loading">
        <AppIcon name="loader" :size="24" class="mcp-topo__spin" />
        <span>{{ t('view.tools.topo.loading') }}</span>
      </div>
      <div
        v-show="!loading"
        ref="canvasRef"
        class="mcp-topo__canvas"
        role="img"
        :aria-label="t('view.tools.topo.title')"
      ></div>
      <EmptyState
        v-if="!loading && !error && stats.total === 0"
        icon="share2"
        :title="t('view.tools.topo.empty.title')"
        :description="t('view.tools.topo.empty.desc')"
      />
    </div>

    <!-- Legend -->
    <div class="mcp-topo__legend">
      <span class="mcp-topo__legend-item">
        <span class="mcp-topo__dot mcp-topo__dot--server"></span>
        {{ t('view.tools.topo.legend.server') }}
      </span>
      <span class="mcp-topo__legend-item">
        <span class="mcp-topo__dot mcp-topo__dot--tool"></span>
        {{ t('view.tools.topo.legend.tool') }}
      </span>
      <span class="mcp-topo__legend-item">
        <span class="mcp-topo__dot mcp-topo__dot--agent"></span>
        {{ t('view.tools.topo.legend.agent') }}
      </span>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount, watch, nextTick } from 'vue';
import { useI18n } from '../i18n';
import { AppIcon, Badge, EmptyState } from './index.js';

const props = defineProps({
  data: {
    type: Object,
    default: () => ({ servers: [], tools: [], agents: [], edges: [] }),
  },
  loading: { type: Boolean, default: false },
  error: { type: String, default: '' },
});

const emit = defineEmits(['refresh']);

const { t } = useI18n();

const canvasRef = ref(null);
const ready = ref(false);

// vis-network instance (lazy-loaded to keep bundle split)
let network = null;
let visNodesDS = null;
let visEdgesDS = null;
let visLib = null;

const stats = computed(() => {
  const servers = props.data.servers?.length || 0;
  const tools = props.data.tools?.length || 0;
  const agents = props.data.agents?.length || 0;
  const edges = props.data.edges?.length || 0;
  return { servers, tools, agents, edges, total: servers + tools + agents };
});

async function loadVisLib() {
  if (visLib) return visLib;
  const { Network, DataSet } = await import('vis-network/standalone');
  visLib = { Network, DataSet };
  return visLib;
}

function buildVisNodes() {
  const nodes = [];
  // Server nodes (square, brand color)
  for (const s of props.data.servers || []) {
    nodes.push({
      id: s.name,
      label: s.name,
      group: 'server',
      shape: 'box',
      color: { background: '#1565C0', border: '#0D47A1', highlight: { background: '#1976D2', border: '#0D47A1' } },
      font: { color: '#fff', size: 13, face: 'Inter, system-ui, sans-serif' },
      title: `${t('view.tools.topo.legend.server')}: ${s.name}\ntransport: ${s.transport}\nstatus: ${s.status}\ntools: ${s.tools_count}`,
    });
  }
  // Tool nodes (dot, info color)
  for (const tl of props.data.tools || []) {
    nodes.push({
      id: tl.id,
      label: tl.name,
      group: 'tool',
      shape: 'dot',
      size: 10,
      color: { background: '#039BE5', border: '#0277BD' },
      font: { size: 11, face: 'Inter, system-ui, sans-serif' },
      title: `${t('view.tools.topo.legend.tool')}: ${tl.name}\nserver: ${tl.server_name}\n${tl.description || ''}`,
    });
  }
  // Agent nodes (diamond, success color)
  for (const a of props.data.agents || []) {
    nodes.push({
      id: a.name,
      label: a.name,
      group: 'agent',
      shape: 'diamond',
      size: 14,
      color: { background: '#43A047', border: '#2E7D32', highlight: { background: '#66BB6A', border: '#2E7D32' } },
      font: { color: '#fff', size: 12, face: 'Inter, system-ui, sans-serif' },
      title: `${t('view.tools.topo.legend.agent')}: ${a.name}\nprovider: ${a.provider || '—'}\nenabled: ${a.enabled}`,
    });
  }
  return nodes;
}

function buildVisEdges() {
  return (props.data.edges || []).map((e) => ({
    id: e.id,
    from: e.source,
    to: e.target,
    color: e.type === 'server-agent' ? { color: '#66BB6A', opacity: 0.7 } : { color: '#90A4AE', opacity: 0.7 },
    width: 1,
    arrows: { to: { enabled: true, scaleFactor: 0.5 } },
    smooth: { type: 'continuous', roundness: 0.4 },
  }));
}

function buildOptions() {
  return {
    layout: { improvedLayout: true, hierarchical: false },
    physics: {
      enabled: true,
      solver: 'forceAtlas2Based',
      forceAtlas2Based: { gravitationalConstant: -30, centralGravity: 0.01, springLength: 110, springConstant: 0.04, damping: 0.4 },
      stabilization: { enabled: true, iterations: 80, updateInterval: 10, fit: true },
      timestep: 0.35,
    },
    interaction: {
      hover: true,
      tooltipDelay: 200,
      zoomView: true,
      dragView: true,
      dragNodes: true,
      navigationButtons: false,
      keyboard: false,
    },
  };
}

async function render() {
  if (!canvasRef.value) return;
  const nodes = buildVisNodes();
  const edges = buildVisEdges();
  if (!nodes.length) {
    destroyNetwork();
    ready.value = false;
    return;
  }
  try {
    await loadVisLib();
  } catch (e) {
    // 渲染失败时静默处理，错误展示交给外层
    return;
  }
  const { Network, DataSet } = visLib;
  if (!visNodesDS) visNodesDS = new DataSet();
  else visNodesDS.clear();
  if (!visEdgesDS) visEdgesDS = new DataSet();
  else visEdgesDS.clear();
  visNodesDS.add(nodes);
  visEdgesDS.add(edges);
  const options = buildOptions();
  if (!network) {
    network = new Network(canvasRef.value, { nodes: visNodesDS, edges: visEdgesDS }, options);
  } else {
    network.setOptions(options);
    network.setData({ nodes: visNodesDS, edges: visEdgesDS });
  }
  ready.value = true;
}

function fitView() {
  if (network) network.fit({ animation: { duration: 300, easingFunction: 'easeInOutQuad' } });
}

function destroyNetwork() {
  if (network) {
    network.destroy();
    network = null;
  }
  visNodesDS = null;
  visEdgesDS = null;
}

onMounted(() => {
  nextTick(() => render());
});

onBeforeUnmount(() => {
  destroyNetwork();
});

// 数据变化时重渲染
watch(
  () => props.data,
  () => nextTick(() => render()),
  { deep: false },
);
</script>

<style scoped>
.mcp-topo {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.mcp-topo__toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 8px 12px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r-md, 8px);
}
.mcp-topo__stats { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
.mcp-topo__actions { display: flex; gap: 8px; }

.mcp-topo__notice {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border-radius: var(--r-md, 8px);
  font-size: 13px;
}
.mcp-topo__notice--error { background: #FFEBEE; border: 1px solid #FFCDD2; color: #C62828; }

.mcp-topo__canvas-wrap {
  position: relative;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r-md, 8px);
  overflow: hidden;
  min-height: 480px;
  height: 480px;
}
.mcp-topo__canvas { width: 100%; height: 100%; min-height: 480px; }
.mcp-topo__loading {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  color: var(--text-muted);
  background: var(--surface);
  z-index: 2;
}
.mcp-topo__spin { animation: mcp-topo-spin 1s linear infinite; }
@keyframes mcp-topo-spin { to { transform: rotate(360deg); } }

.mcp-topo__legend {
  display: flex;
  gap: 16px;
  padding: 6px 12px;
  font-size: 12px;
  color: var(--text-muted);
}
.mcp-topo__legend-item { display: inline-flex; align-items: center; gap: 6px; }
.mcp-topo__dot {
  display: inline-block;
  width: 10px; height: 10px;
  border-radius: 50%;
  flex-shrink: 0;
}
.mcp-topo__dot--server { background: #1565C0; border-radius: 2px; }
.mcp-topo__dot--tool { background: #039BE5; }
.mcp-topo__dot--agent { background: #43A047; transform: rotate(45deg); }

.btn-ghost {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px;
  border: 1px solid var(--border);
  border-radius: 4px;
  background: var(--surface);
  color: var(--text);
  font-size: 13px;
  cursor: pointer;
  transition: background 0.15s, border-color 0.15s;
}
.btn-ghost:hover { background: var(--bg-hover, #f1f5f9); border-color: var(--border-strong, #cbd5e1); }
.btn-ghost:disabled { opacity: 0.5; cursor: not-allowed; }
</style>