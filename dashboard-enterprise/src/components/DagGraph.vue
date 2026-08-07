<template>
  <div class="dag-graph">
    <!-- Progress bar -->
    <div class="dag-progress-bar" v-if="totalNodes > 0">
      <div class="dag-progress-track">
        <div class="dag-progress-fill" :style="{ width: progress + '%' }"></div>
      </div>
      <span class="dag-progress-text">{{ completedCount }} / {{ totalNodes }} ({{ progress }}%)</span>
      <span class="dag-conn-indicator" :class="connected ? 'on' : 'off'" :title="connected ? 'Connected' : 'Disconnected'"></span>
    </div>

    <!-- DAG visualization (SVG) -->
    <div class="dag-canvas" ref="canvasRef">
      <svg class="dag-svg" :width="svgWidth" :height="svgHeight">
        <!-- Edges -->
        <g class="dag-edges">
          <line
            v-for="edge in renderedEdges"
            :key="`${edge.source}-${edge.target}`"
            :x1="edge.x1" :y1="edge.y1"
            :x2="edge.x2" :y2="edge.y2"
            class="dag-edge"
            :class="edgeClass(edge)"
          />
        </g>
        <!-- Nodes -->
        <g class="dag-nodes">
          <g
            v-for="node in layoutNodes"
            :key="node.id"
            :transform="`translate(${node.x}, ${node.y})`"
            class="dag-node-group"
            @click="onNodeClick(node)"
          >
            <circle
              :r="nodeRadius"
              class="dag-node-circle"
              :class="`status-${nodeStates[node.id] || 'pending'}`"
            />
            <text
              :y="nodeRadius + 14"
              text-anchor="middle"
              class="dag-node-label"
            >{{ node.label || node.id }}</text>
            <!-- Status icon (simple text glyph) -->
            <text
              text-anchor="middle"
              dy="4"
              class="dag-node-icon"
              :class="`status-${nodeStates[node.id] || 'pending'}`"
            >{{ statusGlyph(nodeStates[node.id]) }}</text>
          </g>
        </g>
      </svg>
    </div>

    <!-- Empty state -->
    <div v-if="totalNodes === 0 && !executionId" class="dag-empty">
      Enter an execution ID to subscribe to DAG progress.
    </div>
    <div v-if="totalNodes === 0 && executionId && !connected" class="dag-empty">
      Connecting to execution {{ executionId }}…
    </div>

    <!-- Node detail panel (modal overlay) -->
    <div v-if="selectedNode" class="dag-detail-overlay" @click.self="selectedNode = null">
      <NodeDetailPanel :node="selectedNode" @close="selectedNode = null" />
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted, onUnmounted } from 'vue';
import { useDagProgress } from '../composables/useDagProgress.js';
import { NodeDetailPanel } from './index.js';

const props = defineProps({
  executionId: { type: String, default: '' },
  nodes: { type: Array, default: () => [] },     // [{id, label}]
  edges: { type: Array, default: () => [] },     // [{source, target}]
  transport: { type: String, default: 'sse' },   // 'sse' | 'ws'
});

const canvasRef = ref(null);
const selectedNode = ref(null);

// ── DAG progress subscription ──────────────────────────────
const {
  events,
  nodeStates,
  progress,
  connected,
  connect,
  disconnect,
  cancel,
  pause,
} = useDagProgress(props.executionId, { transport: props.transport });

// Auto-connect when executionId is provided.
onMounted(() => {
  if (props.executionId) connect();
});
onUnmounted(disconnect);

// Reconnect when executionId changes.
watch(() => props.executionId, (newId) => {
  disconnect();
  if (newId) connect();
});

// ── Node/edge layout ───────────────────────────────────────
// Build the node list from props.nodes or from observed events.
const observedNodes = computed(() => {
  const ids = new Set();
  for (const evt of events.value) {
    if (evt.node_id) ids.add(evt.node_id);
  }
  return Array.from(ids).map((id) => ({ id, label: id }));
});

const allNodes = computed(() => {
  if (props.nodes && props.nodes.length) return props.nodes;
  return observedNodes.value;
});

const totalNodes = computed(() => allNodes.value.length);

const completedCount = computed(() => {
  const terminal = new Set(['success', 'failed', 'skipped']);
  return allNodes.value.filter((n) => terminal.has(nodeStates.value[n.id])).length;
});

// Layout: arrange nodes in a grid (columns based on count).
const NODE_SPACING_X = 100;
const NODE_SPACING_Y = 80;
const nodeRadius = 18;

const layoutNodes = computed(() => {
  const nodes = allNodes.value;
  if (!nodes.length) return [];
  // If edges provided, do a simple layered layout; otherwise grid.
  if (props.edges && props.edges.length) {
    return layeredLayout(nodes, props.edges);
  }
  // Grid layout: sqrt(n) columns.
  const cols = Math.ceil(Math.sqrt(nodes.length));
  return nodes.map((n, i) => ({
    ...n,
    x: 50 + (i % cols) * NODE_SPACING_X,
    y: 40 + Math.floor(i / cols) * NODE_SPACING_Y,
  }));
});

const renderedEdges = computed(() => {
  if (!props.edges || !props.edges.length) return [];
  const pos = {};
  for (const n of layoutNodes.value) pos[n.id] = n;
  return props.edges
    .filter((e) => pos[e.source] && pos[e.target])
    .map((e) => ({
      ...e,
      x1: pos[e.source].x,
      y1: pos[e.source].y,
      x2: pos[e.target].x,
      y2: pos[e.target].y,
    }));
});

const svgWidth = computed(() => {
  if (!layoutNodes.value.length) return 400;
  return Math.max(...layoutNodes.value.map((n) => n.x)) + 60;
});
const svgHeight = computed(() => {
  if (!layoutNodes.value.length) return 200;
  return Math.max(...layoutNodes.value.map((n) => n.y)) + 50;
});

// ── Simple layered layout (BFS levels) ─────────────────────
function layeredLayout(nodes, edges) {
  const adj = {};
  const inDeg = {};
  for (const n of nodes) {
    adj[n.id] = [];
    inDeg[n.id] = 0;
  }
  for (const e of edges) {
    if (adj[e.source]) adj[e.source].push(e.target);
    if (e.target in inDeg) inDeg[e.target] = (inDeg[e.target] || 0) + 1;
  }
  // BFS from roots (in-degree 0).
  const levels = {};
  let queue = nodes.filter((n) => (inDeg[n.id] || 0) === 0).map((n) => n.id);
  let level = 0;
  const visited = new Set();
  while (queue.length) {
    for (const id of queue) {
      levels[id] = level;
      visited.add(id);
    }
    const next = [];
    for (const id of queue) {
      for (const child of adj[id] || []) {
        if (!visited.has(child)) next.push(child);
      }
    }
    queue = Array.from(new Set(next));
    level++;
  }
  // Assign unvisited nodes to the last level.
  for (const n of nodes) {
    if (!(n.id in levels)) levels[n.id] = level;
  }
  // Group by level → x position; index within level → y position.
  const byLevel = {};
  for (const n of nodes) {
    const lv = levels[n.id];
    if (!byLevel[lv]) byLevel[lv] = [];
    byLevel[lv].push(n);
  }
  const result = [];
  for (const lv of Object.keys(byLevel).sort((a, b) => a - b)) {
    const group = byLevel[lv];
    group.forEach((n, i) => {
      result.push({
        ...n,
        x: 50 + Number(lv) * NODE_SPACING_X,
        y: 40 + i * NODE_SPACING_Y,
      });
    });
  }
  return result;
}

// ── Status → visual mapping (spec 5.2.1 rule 7) ───────────
const STATUS_GLYPH = {
  pending: '○',
  running: '◐',
  success: '✓',
  failed: '✕',
  skipped: '–',
};
function statusGlyph(status) {
  return STATUS_GLYPH[status] || STATUS_GLYPH.pending;
}

function edgeClass(edge) {
  const srcStatus = nodeStates.value[edge.source];
  const tgtStatus = nodeStates.value[edge.target];
  if (srcStatus === 'failed') return 'edge-failed';
  if (tgtStatus === 'skipped') return 'edge-skipped';
  if (srcStatus === 'success' && tgtStatus === 'success') return 'edge-success';
  return '';
}

// ── Node click → detail panel ──────────────────────────────
function onNodeClick(node) {
  // Find the latest event for this node to get metadata.
  const latest = events.value
    .filter((e) => e.node_id === node.id)
    .pop();
  selectedNode.value = latest || { node_id: node.id, status: nodeStates.value[node.id] || 'pending', timestamp: '', metadata: {} };
}

// Expose cancel/pause for parent components.
defineExpose({ cancel, pause, connect, disconnect, events, nodeStates, progress, connected });
</script>

<style scoped>
.dag-graph {
  position: relative;
  border: 1px solid var(--border, #e2e8f0);
  border-radius: 8px;
  background: var(--bg-card, #fff);
  padding: 12px;
}

/* Progress bar */
.dag-progress-bar {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 10px;
}
.dag-progress-track {
  flex: 1;
  height: 6px;
  background: var(--bg-muted, #f1f5f9);
  border-radius: 3px;
  overflow: hidden;
}
.dag-progress-fill {
  height: 100%;
  background: var(--brand, #3b82f6);
  border-radius: 3px;
  transition: width 0.3s ease;
}
.dag-progress-text {
  font-size: 12px;
  color: var(--text-muted, #64748b);
  white-space: nowrap;
}
.dag-conn-indicator {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}
.dag-conn-indicator.on { background: var(--success, #22c55e); }
.dag-conn-indicator.off { background: var(--text-muted, #94a3b8); }

/* DAG canvas */
.dag-canvas {
  overflow: auto;
  max-height: 400px;
}
.dag-svg {
  display: block;
}

/* Edges */
.dag-edge {
  stroke: var(--border, #cbd5e1);
  stroke-width: 1.5;
  fill: none;
}
.dag-edge.edge-success { stroke: var(--success, #22c55e); stroke-width: 2; }
.dag-edge.edge-failed { stroke: var(--fail, #ef4444); stroke-width: 2; stroke-dasharray: 4 3; }
.dag-edge.edge-skipped { stroke: var(--warn, #f59e0b); stroke-width: 1.5; stroke-dasharray: 3 3; }

/* Nodes */
.dag-node-group { cursor: pointer; }
.dag-node-circle {
  stroke-width: 2;
  stroke: #fff;
  fill: #9e9e9e; /* pending (default) */
  transition: fill 0.2s ease;
}
.dag-node-circle.status-pending { fill: #9e9e9e; }
.dag-node-circle.status-running { fill: #1976d2; }
.dag-node-circle.status-success { fill: #388e3c; }
.dag-node-circle.status-failed { fill: #d32f2f; }
.dag-node-circle.status-skipped { fill: #f57c00; }
.dag-node-group:hover .dag-node-circle { stroke: var(--brand, #3b82f6); stroke-width: 3; }

.dag-node-label {
  font-size: 11px;
  fill: var(--text, #334155);
  font-family: inherit;
  user-select: none;
}
.dag-node-icon {
  font-size: 14px;
  fill: #fff;
  font-weight: bold;
  user-select: none;
  pointer-events: none;
}
.dag-node-icon.status-pending { fill: #fff; }
.dag-node-icon.status-running { fill: #fff; }
.dag-node-icon.status-success { fill: #fff; }
.dag-node-icon.status-failed { fill: #fff; }
.dag-node-icon.status-skipped { fill: #fff; }

/* Empty state */
.dag-empty {
  text-align: center;
  padding: 30px;
  color: var(--text-muted, #94a3b8);
  font-size: 13px;
}

/* Detail overlay */
.dag-detail-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(0, 0, 0, 0.3);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
}
.dag-detail-overlay > :deep(.node-detail-panel) {
  width: 420px;
  max-width: 90vw;
  max-height: 80vh;
  overflow-y: auto;
}
</style>