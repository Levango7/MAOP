<template>
  <div class="kg-page">
    <PageHeader />

    <!-- Top toolbar: stats + refresh -->
    <div class="kg-toolbar">
      <div class="kg-stats">
        <Badge tone="brand">{{ t('view.kg.stats.nodes') }}: {{ stats.node_count }}</Badge>
        <Badge tone="info">{{ t('view.kg.stats.edges') }}: {{ stats.edge_count }}</Badge>
        <Badge tone="neutral">{{ t('view.kg.stats.visible') }}: {{ visibleCount }}</Badge>
        <Badge v-if="fps > 0" tone="success">{{ t('view.kg.stats.fps') }}: {{ fps }}</Badge>
        <Badge v-if="lodEnabled" tone="warn">{{ t('view.kg.lod.enabled') }}</Badge>
        <Badge v-if="physicsDisabled" tone="warn">{{ t('view.kg.physics.disabled') }}</Badge>
        <Badge v-if="clusteredMode" tone="warn">{{ t('view.kg.cluster.enabled') }}</Badge>
      </div>
      <div class="kg-toolbar-actions">
        <button v-if="clusteredMode" class="btn" @click="unfoldAll">
          <AppIcon name="filter" :size="14" /> {{ t('view.kg.cluster.unfold') }}
        </button>
        <button class="btn" @click="refresh" :disabled="loading">
          <AppIcon name="refresh" :size="14" /> {{ t('view.kg.refresh') }}
        </button>
      </div>
    </div>

    <!-- Cluster / threshold notice -->
    <div v-if="clusterNotice" class="kg-notice kg-notice--warn">
      <AppIcon name="info" :size="14" /> {{ clusterNotice }}
    </div>

    <!-- LOD notice -->
    <div v-if="lodEnabled" class="kg-notice kg-notice--warn">
      <AppIcon name="info" :size="14" /> {{ t('view.kg.lod.enabled') }}
    </div>

    <!-- Error banner -->
    <div v-if="error" class="kg-notice kg-notice--error">
      <AppIcon name="alert" :size="14" /> {{ error }}
      <button class="btn btn--primary" @click="refresh">{{ t('view.kg.retry') }}</button>
    </div>

    <!-- Main layout: filter panel | graph canvas | detail panel -->
    <div class="kg-layout">
      <!-- Left: filter panel -->
      <aside class="kg-filter">
        <Card :title="t('view.kg.filter.title')" icon="filter" :margin-bottom="12">
          <div class="kg-filter-section">
            <div class="kg-filter-label">{{ t('view.kg.filter.nodeTypes') }}</div>
            <label v-for="tp in NODE_TYPES" :key="tp" class="kg-checkbox">
              <input
                type="checkbox"
                :checked="kg.selectedTypes.value.has(tp)"
                @change="kg.toggleType(tp)"
              />
              <span class="kg-type-dot" :class="`kg-type-${tp}`"></span>
              {{ t(`view.kg.filter.type.${tp}`) }}
            </label>
          </div>

          <div class="kg-filter-section">
            <div class="kg-filter-label">
              {{ t('view.kg.filter.minConfidence') }}
              <span class="muted">{{ minConfidence.toFixed(2) }}</span>
            </div>
            <input
              type="range"
              min="0"
              max="1"
              step="0.05"
              :value="minConfidence"
              @input="onConfidenceInput"
              class="kg-range"
            />
          </div>

          <div class="kg-filter-section">
            <div class="kg-filter-label">{{ t('view.kg.filter.search') }}</div>
            <input
              type="text"
              :value="searchKeyword"
              @input="onSearchInput"
              :placeholder="t('view.kg.filter.searchPlaceholder')"
              class="kg-input"
            />
          </div>

          <div class="kg-filter-section">
            <div class="kg-filter-label">{{ t('view.kg.filter.limit') }}</div>
            <input
              type="number"
              min="1"
              max="2000"
              :value="limit"
              @input="onLimitInput"
              class="kg-input"
            />
          </div>

          <div class="kg-filter-actions">
            <button class="btn btn--primary" @click="applyServerFilter" :disabled="loading">
              {{ t('view.kg.filter.apply') }}
            </button>
            <button class="btn" @click="resetAll">{{ t('view.kg.filter.reset') }}</button>
          </div>
        </Card>

        <!-- Timeline (T20) -->
        <Card :title="t('view.kg.timeline.title')" icon="clock" :margin-bottom="12">
          <div class="kg-timeline">
            <div class="kg-timeline-row">
              <label>{{ t('view.kg.timeline.start') }}</label>
              <input
                type="datetime-local"
                :value="timelineStart"
                @input="onTimelineStart"
                class="kg-input"
              />
            </div>
            <div class="kg-timeline-row">
              <label>{{ t('view.kg.timeline.end') }}</label>
              <input
                type="datetime-local"
                :value="timelineEnd"
                @input="onTimelineEnd"
                class="kg-input"
              />
            </div>
            <div v-if="timelineError" class="kg-timeline-error">{{ timelineError }}</div>
            <div class="kg-timeline-range">
              <input
                type="range"
                min="0"
                max="100"
                :value="timelineProgress"
                @input="onTimelineScrub"
                class="kg-range"
              />
            </div>
          </div>
        </Card>
      </aside>

      <!-- Center: graph canvas -->
      <main class="kg-canvas-wrap">
        <div v-if="loading" class="kg-loading">
          <AppIcon name="loader" :size="24" class="kg-spin" />
          <span>{{ t('view.kg.loading') }}</span>
        </div>
        <div
          v-show="!loading"
          ref="canvasRef"
          class="kg-canvas"
          role="img"
          :aria-label="t('view.kg.title')"
        ></div>
        <EmptyState
          v-if="!loading && !error && visibleCount === 0"
          icon="share2"
          :title="t('view.kg.empty.title')"
          :description="t('view.kg.empty.desc')"
        />
      </main>

      <!-- Right: detail panel (T20) -->
      <aside class="kg-detail" v-if="selectedNode">
        <Card :title="t('view.kg.detail.title')" icon="info">
          <div class="kg-detail-body">
            <div class="kg-detail-row">
              <span class="kg-detail-label">{{ t('view.kg.detail.type') }}</span>
              <span class="kg-detail-value">
                <span class="kg-type-dot" :class="`kg-type-${selectedNode.type}`"></span>
                {{ selectedNode.type }}
              </span>
            </div>
            <div class="kg-detail-row">
              <span class="kg-detail-label">{{ t('view.kg.detail.label') }}</span>
              <span class="kg-detail-value">{{ selectedNode.label || selectedNode.id }}</span>
            </div>
            <div class="kg-detail-row">
              <span class="kg-detail-label">{{ t('view.kg.detail.timestamp') }}</span>
              <span class="kg-detail-value">{{ formatTime(selectedNode.timestamp) }}</span>
            </div>
            <div class="kg-detail-row">
              <span class="kg-detail-label">{{ t('view.kg.detail.confidence') }}</span>
              <span class="kg-detail-value">{{ ((selectedNode.confidence ?? 1) * 100).toFixed(0) }}%</span>
            </div>

            <div class="kg-detail-section" v-if="selectedNode.properties && Object.keys(selectedNode.properties).length">
              <div class="kg-detail-label">{{ t('view.kg.detail.properties') }}</div>
              <pre class="kg-detail-pre">{{ JSON.stringify(selectedNode.properties, null, 2) }}</pre>
            </div>

            <div class="kg-detail-section">
              <div class="kg-detail-label">
                {{ t('view.kg.detail.relations') }}
                <span class="muted">({{ nodeDetails?.relatedEdges.length || 0 }})</span>
              </div>
              <div v-if="nodeDetails && nodeDetails.relatedEdges.length" class="kg-rel-list">
                <div v-for="e in nodeDetails.relatedEdges" :key="e.id" class="kg-rel-item">
                  <span class="kg-rel-dir" :class="e.source === selectedNode.id ? 'out' : 'in'">
                    {{ e.source === selectedNode.id ? '→' : '←' }}
                  </span>
                  <Badge tone="neutral">{{ e.type }}</Badge>
                  <span class="kg-rel-target">{{ e.source === selectedNode.id ? e.target : e.source }}</span>
                </div>
              </div>
              <div v-else class="muted">{{ t('view.kg.detail.noRelations') }}</div>
            </div>

            <div class="kg-detail-section" v-if="nodeDetails && nodeDetails.relatedNodes.length">
              <div class="kg-detail-label">{{ t('view.kg.detail.relatedNodes') }}</div>
              <div class="kg-related-list">
                <Badge
                  v-for="n in nodeDetails.relatedNodes"
                  :key="n.id"
                  :tone="typeTone(n.type)"
                >{{ n.label || n.id }}</Badge>
              </div>
            </div>

            <div class="kg-detail-section" v-if="selectedNode.type === 'memory' && memorySummary">
              <div class="kg-detail-label">{{ t('view.kg.detail.memorySummary') }}</div>
              <div class="kg-memory-summary">{{ memorySummary }}</div>
            </div>
          </div>
          <div class="kg-detail-foot">
            <button class="btn" @click="clearSelection">{{ t('view.kg.detail.close') }}</button>
          </div>
        </Card>
      </aside>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount, watch, nextTick } from 'vue';
import { useI18n } from '../i18n';
import { useKnowledgeGraph, toVisNode, toVisEdge, progressiveLoad } from '../composables/useKnowledgeGraph.js';
import PageHeader from '../components/PageHeader.vue';
import Card from '../components/Card.vue';
import Badge from '../components/Badge.vue';
import EmptyState from '../components/EmptyState.vue';
import AppIcon from '../components/AppIcon.vue';

const { t } = useI18n();
const kg = useKnowledgeGraph();

// ── Constants ──
const NODE_TYPES = ['agent', 'task', 'memory', 'concept'];
const LOD_THRESHOLD = 5000;       // spec 5.3.3 异常 4
const PROGRESSIVE_THRESHOLD = 1000; // spec 5.3.1 规则 11
const FIRST_BATCH = 500;
const BATCH_SIZE = 200;
// P2-10: 节点阈值降级 —— 节点数超过 300 时关闭物理模拟，改用预设布局
const PHYSICS_THRESHOLD = 300;
// P2-10: 聚类折叠阈值 —— 同类型节点超过该数量时折叠为单个聚合节点
const CLUSTER_THRESHOLD = 50;

// ── Local UI state ──
const canvasRef = ref(null);
const limit = ref(500);
const selectedNode = ref(null);
const fps = ref(0);
const lodEnabled = ref(false);
// P2-10: 节点阈值降级 & 聚类折叠状态
const physicsDisabled = ref(false);
const clusteredMode = ref(false);
const foldedCount = ref(0);
const displayNodeCount = ref(0);
// P2-10: 用户手动展开折叠的开关（true 表示跳过聚类折叠）
const unfoldRequested = ref(false);

// Timeline state (T20)
const timelineStart = ref('');
const timelineEnd = ref('');
const timelineError = ref('');
const timelineProgress = ref(100);

// ── vis-network instance (lazy-loaded to keep bundle split) ──
let network = null;
let visNodesDS = null;
let visEdgesDS = null;
let visLib = null;  // { Network, DataSet }

// ── Frame rate measurement (T21) ──
const frameTimes = [];
let fpsRafId = null;

function startFpsMeasure() {
  if (fpsRafId) return;
  let last = performance.now();
  const tick = (now) => {
    frameTimes.push(now - last);
    last = now;
    if (frameTimes.length > 30) frameTimes.shift();
    if (frameTimes.length >= 10) {
      const avg = frameTimes.reduce((a, b) => a + b, 0) / frameTimes.length;
      fps.value = Math.round(1000 / avg);
    }
    fpsRafId = requestAnimationFrame(tick);
  };
  fpsRafId = requestAnimationFrame(tick);
}

function stopFpsMeasure() {
  if (fpsRafId) cancelAnimationFrame(fpsRafId);
  fpsRafId = null;
}

// ── Computed ──
const stats = computed(() => kg.stats.value);
const loading = computed(() => kg.loading.value);
const error = computed(() => kg.error.value);
const minConfidence = computed(() => kg.minConfidence.value);
const searchKeyword = computed(() => kg.searchKeyword.value);
const visibleCount = computed(() => kg.filteredNodes.value.length);

const nodeDetails = computed(() => {
  if (!selectedNode.value) return null;
  return kg.getNodeDetails(selectedNode.value.id);
});

const memorySummary = computed(() => {
  if (!selectedNode.value || selectedNode.value.type !== 'memory') return '';
  const props = selectedNode.value.properties || {};
  return props.description || props.summary || props.content || props.text || '';
});

// P2-10: 节点数量提示 —— "当前显示 N 个节点，已折叠 M 个"
const clusterNotice = computed(() => {
  if (!clusteredMode.value && !physicsDisabled.value) return '';
  const parts = [];
  if (physicsDisabled.value) {
    parts.push(t('view.kg.physics.disabledTip'));
  }
  if (clusteredMode.value && foldedCount.value > 0) {
    parts.push(
      t('view.kg.cluster.notice', {
        display: displayNodeCount.value,
        folded: foldedCount.value,
      }),
    );
  }
  return parts.join(' · ');
});

// ── Helpers ──
function formatTime(ts) {
  if (!ts) return '—';
  try {
    const d = new Date(ts);
    return isNaN(d.getTime()) ? String(ts) : d.toLocaleString();
  } catch {
    return String(ts);
  }
}

function typeTone(type) {
  return { agent: 'brand', task: 'warn', memory: 'info', concept: 'success' }[type] || 'neutral';
}

function toIsoLocal(dt) {
  // datetime-local input gives "YYYY-MM-DDTHH:mm"; convert to ISO 8601 with seconds.
  if (!dt) return '';
  return dt.length === 16 ? dt + ':00' : dt;
}

// ── Filter input handlers ──
function onConfidenceInput(e) {
  kg.minConfidence.value = parseFloat(e.target.value) || 0;
  scheduleRender();
}

function onSearchInput(e) {
  kg.searchKeyword.value = e.target.value;
  scheduleRender();
}

function onLimitInput(e) {
  limit.value = Math.max(1, Math.min(2000, parseInt(e.target.value) || 500));
}

function onTimelineStart(e) {
  timelineStart.value = e.target.value;
  validateAndApplyTimeline();
}

function onTimelineEnd(e) {
  timelineEnd.value = e.target.value;
  validateAndApplyTimeline();
}

function validateAndApplyTimeline() {
  const start = toIsoLocal(timelineStart.value);
  const end = toIsoLocal(timelineEnd.value);
  if (start && end && start > end) {
    timelineError.value = t('view.kg.timeline.invalidRange');
    return;
  }
  timelineError.value = '';
  kg.timeRange.value = { start, end };
  scheduleRender();
}

function onTimelineScrub(e) {
  // Scrub the timeline: set end to a fraction of the [start, maxTimestamp] range.
  const progress = parseInt(e.target.value) || 0;
  timelineProgress.value = progress;
  const allTs = kg.rawNodes.value.map((n) => n.timestamp).filter(Boolean).sort();
  if (!allTs.length) return;
  const min = allTs[0];
  const max = allTs[allTs.length - 1];
  if (min === max) return;
  // Interpolate end time
  const minMs = new Date(min).getTime();
  const maxMs = new Date(max).getTime();
  const endMs = minMs + (maxMs - minMs) * (progress / 100);
  const endIso = new Date(endMs).toISOString().slice(0, 19);
  kg.timeRange.value = { start: min, end: endIso };
  scheduleRender();
}

function applyServerFilter() {
  // Re-fetch from server with current type/limit (time_range handled client-side first).
  const types = Array.from(kg.selectedTypes.value);
  const params = {
    type: types.join(','),
    limit: limit.value,
  };
  // If timeline range is set and validated, push to server.
  const { start, end } = kg.timeRange.value;
  if (start && end && start <= end) {
    params.time_range = `${start},${end}`;
  }
  kg.fetchGraph(params).then(() => {
    nextTick(() => renderGraph());
  });
}

function resetAll() {
  kg.resetFilters();
  timelineStart.value = '';
  timelineEnd.value = '';
  timelineError.value = '';
  timelineProgress.value = 100;
  limit.value = 500;
  clearSelection();
  kg.fetchGraph({ limit: 500 }).then(() => {
    nextTick(() => renderGraph());
  });
}

function refresh() {
  kg.fetchGraph(kg.lastParams.value || { limit: limit.value }).then(() => {
    nextTick(() => renderGraph());
  });
}

// ── Selection / highlight ──
function selectNode(nodeId) {
  const node = kg.rawNodes.value.find((n) => n.id === nodeId);
  selectedNode.value = node || null;
}

function clearSelection() {
  selectedNode.value = null;
  kg.clearHighlight();
  clearHighlightVisual();
}

// ── vis-network rendering ──
async function loadVisLib() {
  if (visLib) return visLib;
  // Dynamic import keeps vis-network out of the main bundle.
  const { Network, DataSet } = await import('vis-network/standalone');
  visLib = { Network, DataSet };
  return visLib;
}

async function renderGraph() {
  if (!canvasRef.value) return;
  const rawNodes = kg.filteredNodes.value;
  const rawEdges = kg.filteredEdges.value;

  // Empty state — tear down existing network.
  if (!rawNodes.length) {
    destroyNetwork();
    return;
  }

  try {
    await loadVisLib();
  } catch (e) {
    kg.error.value = t('view.kg.error.render') + ': ' + (e.message || e);
    return;
  }

  const { Network, DataSet } = visLib;

  // LOD decision (T21)
  lodEnabled.value = rawNodes.length > LOD_THRESHOLD;

  // P2-10: 节点阈值降级 —— 节点 > 300 时关闭物理模拟
  physicsDisabled.value = rawNodes.length > PHYSICS_THRESHOLD;

  // P2-10: 聚类折叠 —— 按 entity_type 分组，超过阈值时折叠同类型节点
  const { nodes: renderNodes, edges: renderEdges, folded } = applyClusterFolding(rawNodes, rawEdges);
  clusteredMode.value = folded > 0;
  foldedCount.value = folded;
  displayNodeCount.value = renderNodes.length;

  // Rebuild DataSets (simpler than incremental diffing for filter changes).
  if (!visNodesDS) visNodesDS = new DataSet();
  else visNodesDS.clear();
  if (!visEdgesDS) visEdgesDS = new DataSet();
  else visEdgesDS.clear();

  // Progressive load when node count exceeds threshold (T21).
  if (renderNodes.length > PROGRESSIVE_THRESHOLD) {
    await progressiveLoad(renderNodes, (batch) => {
      visNodesDS.add(batch.map(toVisNode));
      const idSet = new Set(batch.map((n) => n.id));
      const batchEdges = renderEdges.filter((e) => idSet.has(e.source) && idSet.has(e.target));
      visEdgesDS.add(batchEdges.map(toVisEdge));
    }, { firstBatchSize: FIRST_BATCH, batchSize: BATCH_SIZE });
  } else {
    visNodesDS.add(renderNodes.map(toVisNode));
    visEdgesDS.add(renderEdges.map(toVisEdge));
  }

  // (Re)create the network with current options.
  const options = buildOptions(renderNodes.length);
  if (!network) {
    network = new Network(canvasRef.value, { nodes: visNodesDS, edges: visEdgesDS }, options);
    attachNetworkEvents();
  } else {
    network.setOptions(options);
    network.setData({ nodes: visNodesDS, edges: visEdgesDS });
  }

  startFpsMeasure();
}

// P2-10: 聚类折叠 —— 按 entity_type 分组，同类型节点数超过 CLUSTER_THRESHOLD 时
// 折叠为单个聚合节点（保留前 CLUSTER_THRESHOLD 个代表节点 + 1 个聚合节点）。
// 返回 { nodes, edges, folded } 供 renderGraph 使用。
function applyClusterFolding(rawNodes, rawEdges) {
  // 仅在节点总数超过 PHYSICS_THRESHOLD 且用户未请求展开时启用聚类折叠
  if (rawNodes.length <= PHYSICS_THRESHOLD || unfoldRequested.value) {
    return { nodes: rawNodes, edges: rawEdges, folded: 0 };
  }
  // 按 type 分组
  const groups = new Map();
  for (const n of rawNodes) {
    const tp = n.type || 'unknown';
    if (!groups.has(tp)) groups.set(tp, []);
    groups.get(tp).push(n);
  }
  let folded = 0;
  const keptNodes = [];
  const clusterMeta = []; // { type, count, keptIds }
  for (const [tp, list] of groups) {
    if (list.length > CLUSTER_THRESHOLD) {
      // 保留前 CLUSTER_THRESHOLD 个代表节点，其余折叠
      const kept = list.slice(0, CLUSTER_THRESHOLD);
      const foldedCountForType = list.length - kept.length;
      keptNodes.push(...kept);
      // 增加一个聚合占位节点
      const clusterNodeId = `__cluster_${tp}`;
      keptNodes.push({
        id: clusterNodeId,
        type: tp,
        label: `+${foldedCountForType} ${tp}`,
        isCluster: true,
        clusterType: tp,
        clusterCount: foldedCountForType,
        confidence: 1,
      });
      folded += foldedCountForType;
      clusterMeta.push({ type: tp, clusterNodeId, keptIds: new Set(kept.map((n) => n.id)) });
    } else {
      keptNodes.push(...list);
    }
  }
  // 重写边：被折叠的节点 → 对应的聚合节点
  if (folded === 0) {
    return { nodes: rawNodes, edges: rawEdges, folded: 0 };
  }
  const foldMap = new Map(); // originalId -> clusterNodeId
  for (const meta of clusterMeta) {
    const tp = meta.type;
    const list = groups.get(tp) || [];
    for (const n of list) {
      if (!meta.keptIds.has(n.id)) foldMap.set(n.id, meta.clusterNodeId);
    }
  }
  const renderEdges = [];
  for (const e of rawEdges) {
    const s = foldMap.get(e.source) || e.source;
    const tg = foldMap.get(e.target) || e.target;
    // 跳过自环（折叠后可能产生）
    if (s === tg) continue;
    renderEdges.push({ ...e, source: s, target: tg });
  }
  return { nodes: keptNodes, edges: renderEdges, folded };
}

// P2-10: 展开全部折叠节点 —— 切换 unfoldRequested 并重渲染
function unfoldAll() {
  unfoldRequested.value = true;
  renderGraph();
}

function buildOptions(nodeCount) {
  // P2-10: 节点阈值降级 —— 节点 > 300 时关闭物理模拟，改用预设布局（grid），
  // 避免力导向在大图上的 O(n^2) 计算开销导致浏览器卡死。
  const usePresetLayout = nodeCount > PHYSICS_THRESHOLD;
  return {
    nodes: {
      shape: 'dot',
      size: 16,
      font: { size: 14, face: 'Inter, system-ui, sans-serif' },
      borderWidth: 1,
      scaling: { min: 8, max: 24 },
    },
    edges: {
      width: 1,
      smooth: usePresetLayout ? false : { type: 'continuous', roundness: 0.5 },
      font: { size: 10, align: 'middle' },
      arrows: { to: { enabled: true, scaleFactor: 0.6 } },
    },
    layout: {
      improvedLayout: nodeCount <= 500,
      hierarchical: false,
      // 预设布局：vis-network 内置 randomLayout 在 physics 关闭时按节点插入顺序排布
      randomSeed: usePresetLayout ? 42 : undefined,
    },
    physics: {
      enabled: !usePresetLayout,
      solver: 'forceAtlas2Based',
      forceAtlas2Based: { gravitationalConstant: -26, centralGravity: 0.005, springLength: 100, springConstant: 0.04, damping: 0.4 },
      stabilization: { enabled: true, iterations: 50, updateInterval: 10, onlyDynamicEdges: false, fit: true },
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
      multiselect: false,
    },
  };
}

function attachNetworkEvents() {
  if (!network) return;
  // Click → select + highlight path (T19)
  network.on('click', (params) => {
    if (params.nodes && params.nodes.length) {
      const nodeId = params.nodes[0];
      selectNode(nodeId);
      kg.highlightPath(nodeId);
      applyHighlightVisual();
    } else {
      clearSelection();
    }
  });
  // Double-click → open detail panel (T20)
  network.on('doubleClick', (params) => {
    if (params.nodes && params.nodes.length) {
      selectNode(params.nodes[0]);
    }
  });
  // Zoom/drag → viewport culling + LOD (T21)
  network.on('zoom', () => {
    applyLOD();
    scheduleViewportCull();
  });
  network.on('dragEnd', () => {
    scheduleViewportCull();
  });
}

// ── Path highlight visuals (T19) ──
function applyHighlightVisual() {
  if (!visNodesDS || !visEdgesDS) return;
  const pathIds = kg.highlightedPathIds.value;
  if (!pathIds.size) return;
  // Dim non-path nodes, emphasize path nodes.
  const updates = kg.filteredNodes.value.map((n) => ({
    id: n.id,
    opacity: pathIds.has(n.id) ? 1.0 : 0.2,
    borderWidth: pathIds.has(n.id) ? 3 : 1,
  }));
  visNodesDS.update(updates);
  // Emphasize path edges.
  const edgeUpdates = kg.filteredEdges.value.map((e) => {
    const isPath = pathIds.has(e.source) && pathIds.has(e.target);
    return {
      id: e.id,
      width: isPath ? 3 : 1,
      color: isPath ? { color: '#ff7b72', highlight: '#ff7b72' } : { color: '#CCCCCC', opacity: 0.4 },
    };
  });
  visEdgesDS.update(edgeUpdates);
}

function clearHighlightVisual() {
  if (!visNodesDS || !visEdgesDS) return;
  visNodesDS.update(kg.filteredNodes.value.map((n) => ({ id: n.id, opacity: 1.0, borderWidth: 1 })));
  visEdgesDS.update(kg.filteredEdges.value.map((e) => ({ id: e.id, width: 1 })));
}

// ── LOD (Level of Detail) — T21 ──
function applyLOD() {
  if (!network) return;
  const scale = network.getScale();
  // Hide labels at low zoom; cluster at very low zoom.
  if (scale <= 0.2) {
    network.setOptions({ nodes: { font: { size: 0 } } });
  } else if (scale <= 0.5) {
    network.setOptions({ nodes: { font: { size: 8 } } });
  } else {
    network.setOptions({ nodes: { font: { size: 14 } } });
  }
}

// ── Viewport culling (T21) — hide nodes outside the visible canvas ──
let cullRafId = null;
function scheduleViewportCull() {
  if (cullRafId) cancelAnimationFrame(cullRafId);
  cullRafId = requestAnimationFrame(updateViewport);
}

function updateViewport() {
  cullRafId = null;
  if (!network || !visNodesDS) return;
  // Only cull when node count is large enough to benefit.
  if (kg.filteredNodes.value.length <= 500) return;
  try {
    const pos = network.getViewPosition();
    const scale = network.getScale();
    const canvas = network.canvas.frame.canvas;
    const halfW = canvas.width / 2 / scale + 100;
    const halfH = canvas.height / 2 / scale + 100;
    const positions = network.getPositions();
    const updates = [];
    for (const [id, p] of Object.entries(positions)) {
      const inView = Math.abs(p.x - pos.x) < halfW && Math.abs(p.y - pos.y) < halfH;
      updates.push({ id, hidden: !inView });
    }
    if (updates.length) visNodesDS.update(updates);
  } catch {
    // network not ready — skip
  }
}

// ── Render scheduling (debounced for filter input) ──
let renderTimer = null;
function scheduleRender() {
  if (renderTimer) clearTimeout(renderTimer);
  renderTimer = setTimeout(() => renderGraph(), 100);
}

// ── Lifecycle ──
onMounted(() => {
  kg.fetchGraph({ limit: limit.value }).then(() => {
    nextTick(() => renderGraph());
  });
});

onBeforeUnmount(() => {
  stopFpsMeasure();
  if (cullRafId) cancelAnimationFrame(cullRafId);
  if (renderTimer) clearTimeout(renderTimer);
  destroyNetwork();
});

function destroyNetwork() {
  if (network) {
    network.destroy();
    network = null;
  }
  visNodesDS = null;
  visEdgesDS = null;
}

// Re-render when filtered data changes (e.g. type filter toggle).
watch([() => kg.filteredNodes.value, () => kg.filteredEdges.value], () => {
  scheduleRender();
}, { deep: false });
</script>

<style scoped>
.kg-page {
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-height: calc(100vh - 120px);
}

/* ── Toolbar ── */
.kg-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 8px 12px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r-md, 8px);
}
.kg-stats { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
.kg-toolbar-actions { display: flex; gap: 8px; }

/* ── Notices ── */
.kg-notice {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border-radius: var(--r-md, 8px);
  font-size: 13px;
}
.kg-notice--warn { background: #FFF3E0; border: 1px solid #FFB74D; color: #d29922; }
.kg-notice--error { background: #FFEBEE; border: 1px solid #FFCDD2; color: #a40e26; }
.kg-notice--error .btn { margin-left: auto; }

/* ── Layout ── */
.kg-layout {
  display: grid;
  grid-template-columns: 260px 1fr 320px;
  gap: 12px;
  flex: 1;
  min-height: 600px;
}
.kg-layout:has(.kg-detail:not([hidden])) { grid-template-columns: 260px 1fr 320px; }
.kg-layout:not(:has(.kg-detail)) { grid-template-columns: 260px 1fr; }

@media (max-width: 1100px) {
  .kg-layout { grid-template-columns: 240px 1fr; }
  .kg-detail { display: none; }
}

/* ── Filter panel ── */
.kg-filter { display: flex; flex-direction: column; gap: 12px; }
.kg-filter-section { display: flex; flex-direction: column; gap: 6px; padding: 6px 0; }
.kg-filter-section + .kg-filter-section { border-top: 1px solid var(--border-light, rgba(148,163,184,.16)); }
.kg-filter-label { font-size: 12px; font-weight: 600; color: var(--text-muted); }
.kg-checkbox { display: flex; align-items: center; gap: 6px; font-size: 13px; cursor: pointer; }
.kg-checkbox input { margin: 0; }
.kg-type-dot {
  display: inline-block;
  width: 10px; height: 10px;
  border-radius: 50%;
  flex-shrink: 0;
}
.kg-type-agent { background: #3574f0; }
.kg-type-task { background: #d29922; }
.kg-type-memory { background: #6A1B9A; }
.kg-type-concept { background: #2E7D32; }
.kg-range { width: 100%; }
.kg-input {
  width: 100%;
  padding: 6px 8px;
  border: 1px solid var(--border);
  border-radius: 4px;
  font-size: 13px;
  background: var(--surface);
  color: var(--text);
}
.kg-filter-actions { display: flex; gap: 8px; margin-top: 8px; }
.kg-filter-actions .btn { flex: 1; }

/* ── Timeline ── */
.kg-timeline { display: flex; flex-direction: column; gap: 8px; }
.kg-timeline-row { display: flex; flex-direction: column; gap: 4px; }
.kg-timeline-row label { font-size: 12px; color: var(--text-muted); }
.kg-timeline-error { color: #a40e26; font-size: 12px; }
.kg-timeline-range { padding-top: 4px; }

/* ── Canvas ── */
.kg-canvas-wrap {
  position: relative;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r-md, 8px);
  overflow: hidden;
  min-height: 600px;
}
.kg-canvas { width: 100%; height: 100%; min-height: 600px; }
.kg-loading {
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
.kg-spin { animation: kg-spin 1s linear infinite; }
@keyframes kg-spin { to { transform: rotate(360deg); } }

/* ── Detail panel ── */
.kg-detail { display: flex; flex-direction: column; }
.kg-detail-body { display: flex; flex-direction: column; gap: 8px; }
.kg-detail-row { display: flex; gap: 12px; padding: 4px 0; align-items: center; }
.kg-detail-row + .kg-detail-row { border-top: 1px solid var(--border-light, rgba(148,163,184,.16)); }
.kg-detail-label { width: 90px; flex-shrink: 0; font-size: 12px; color: var(--text-muted); font-weight: 500; }
.kg-detail-value { flex: 1; font-size: 13px; word-break: break-word; display: flex; align-items: center; gap: 6px; }
.kg-detail-section { padding-top: 8px; border-top: 1px solid var(--border-light, rgba(148,163,184,.16)); display: flex; flex-direction: column; gap: 6px; }
.kg-detail-pre {
  margin: 0;
  padding: 8px;
  background: var(--bg-code, #f5f5f5);
  border-radius: 4px;
  font-size: 11px;
  font-family: 'SF Mono', 'Fira Code', monospace;
  overflow-x: auto;
  max-height: 160px;
  overflow-y: auto;
  white-space: pre-wrap;
}
.kg-rel-list { display: flex; flex-direction: column; gap: 4px; }
.kg-rel-item { display: flex; align-items: center; gap: 6px; font-size: 12px; }
.kg-rel-dir { font-weight: bold; color: var(--text-muted); }
.kg-rel-dir.out { color: #3574f0; }
.kg-rel-dir.in { color: #d29922; }
.kg-rel-target { font-family: 'SF Mono', 'Fira Code', monospace; }
.kg-related-list { display: flex; flex-wrap: wrap; gap: 4px; }
.kg-memory-summary {
  padding: 8px;
  background: var(--bg-muted, rgba(148,163,184,.10));
  border-radius: 4px;
  font-size: 12px;
  line-height: 1.5;
  max-height: 120px;
  overflow-y: auto;
}
.kg-detail-foot { display: flex; justify-content: flex-end; padding-top: 8px; }

/* ── Buttons (local, matching project style) ── */
.btn {
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
.btn:hover { background: var(--bg-hover, rgba(148,163,184,.16)); border-color: var(--border-strong, rgba(148,163,184,.45)); }
.btn:disabled { opacity: 0.5; cursor: not-allowed; }
.btn--primary { background: var(--brand, #3574f0); color: #fff; border-color: var(--brand, #3574f0); }
.btn--primary:hover { background: var(--brand-strong, #0D47A1); }

.muted { color: var(--text-muted); font-weight: 400; }
</style>