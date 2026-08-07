/**
 * useKnowledgeGraph — composable for the /api/knowledge-graph endpoint (v4.5.0).
 *
 * Encapsulates:
 *  - fetchGraph(params)  → GET /api/knowledge-graph with type/time_range/limit
 *  - reactive state: nodes, edges, stats, loading, error
 *  - client-side filters: applyTypeFilter / applyTimeRange / applySearch
 *  - path highlight: computeReachablePath (BFS over current edges)
 *  - progressive load helper: progressiveLoad (requestIdleCallback batches)
 *
 * The composable is renderer-agnostic — it returns plain reactive data and
 * helper functions; the view component is responsible for mapping them onto
 * vis-network DataSet / Network instances.
 *
 * @example
 *   const kg = useKnowledgeGraph();
 *   onMounted(() => kg.fetchGraph({ limit: 500 }));
 */
import { ref, computed, readonly } from 'vue';
import { useApiStore } from '../stores/api.js';

// ── Node/edge style maps (design.md 2.4.5) ───────────────────────────
// Colors chosen for light theme (user pref: light/white, elegant, natural).
export const NODE_STYLE = {
  agent:   { color: { background: '#E3F2FD', border: '#1565C0', highlight: { background: '#BBDEFB', border: '#1565C0' } }, icon: { code: 'f2bd' }, shape: 'icon' },
  task:    { color: { background: '#FFF3E0', border: '#E65100', highlight: { background: '#FFE0B2', border: '#E65100' } }, icon: { code: 'f073' }, shape: 'icon' },
  memory:  { color: { background: '#F3E5F5', border: '#6A1B9A', highlight: { background: '#E1BEE7', border: '#6A1B9A' } }, icon: { code: 'f538' }, shape: 'icon' },
  concept: { color: { background: '#E8F5E9', border: '#2E7D32', highlight: { background: '#C8E6C9', border: '#2E7D32' } }, icon: { code: 'f02d' }, shape: 'icon' },
};

export const EDGE_STYLE = {
  delegates:  { color: { color: '#1565C0', highlight: '#FF5722' }, dashes: false, arrows: 'to' },
  remembers:  { color: { color: '#6A1B9A', highlight: '#FF5722' }, dashes: [5, 5], arrows: 'to' },
  produces:   { color: { color: '#2E7D32', highlight: '#FF5722' }, dashes: false, arrows: 'to' },
  depends_on: { color: { color: '#E65100', highlight: '#FF5722' }, dashes: [2, 2], arrows: 'to' },
};

// Default neutral style for unknown types (forward compatibility).
const NEUTRAL_NODE_STYLE = {
  color: { background: '#F5F5F5', border: '#616161' },
  shape: 'dot',
};
const NEUTRAL_EDGE_STYLE = {
  color: { color: '#9E9E9E', highlight: '#FF5722' },
  arrows: 'to',
};

/**
 * Map a GraphNodeV2 to a vis-network node DTO.
 */
export function toVisNode(n) {
  const style = NODE_STYLE[n.type] || NEUTRAL_NODE_STYLE;
  return {
    id: n.id,
    label: n.label || n.id,
    title: `${n.type} · ${n.label || n.id}${n.timestamp ? '\n' + n.timestamp : ''}`,
    group: n.type,
    ...style,
  };
}

/**
 * Map a GraphEdgeV2 to a vis-network edge DTO.
 */
export function toVisEdge(e) {
  const style = EDGE_STYLE[e.type] || NEUTRAL_EDGE_STYLE;
  return {
    id: e.id,
    from: e.source,
    to: e.target,
    label: e.type,
    title: `${e.source} —[${e.type}]→ ${e.target}`,
    ...style,
  };
}

/**
 * BFS reachable-path computation from a start node over a set of edges.
 *
 * Returns the set of node ids reachable from ``startId``, including
 * ``startId`` itself. Used for path highlight (spec 5.3.1 rule 7).
 *
 * @param {string} startId
 * @param {Array<{source: string, target: string}>} edges
 * @param {object} [opts] { directed: boolean = true, maxDepth: number = 10 }
 * @returns {Set<string>}
 */
export function computeReachablePath(startId, edges, opts = {}) {
  const { directed = true, maxDepth = 10 } = opts;
  const visited = new Set([startId]);
  let frontier = [startId];
  for (let depth = 0; depth < maxDepth && frontier.length; depth++) {
    const next = [];
    for (const nodeId of frontier) {
      for (const e of edges) {
        if (e.source === nodeId && !visited.has(e.target)) {
          visited.add(e.target);
          next.push(e.target);
        }
        if (!directed && e.target === nodeId && !visited.has(e.source)) {
          visited.add(e.source);
          next.push(e.source);
        }
      }
    }
    frontier = next;
  }
  return visited;
}

/**
 * BFS shortest path between two nodes (used when user selects two nodes).
 *
 * @returns {Array<string>|null} node id sequence from start to end, or null
 */
export function computeShortestPath(startId, endId, edges) {
  if (startId === endId) return [startId];
  const visited = new Set([startId]);
  const queue = [[startId, [startId]]];
  while (queue.length) {
    const [current, path] = queue.shift();
    for (const e of edges) {
      let neighbor = null;
      if (e.source === current) neighbor = e.target;
      else if (e.target === current) neighbor = e.source;
      if (neighbor === null) continue;
      if (neighbor === endId) return [...path, neighbor];
      if (!visited.has(neighbor)) {
        visited.add(neighbor);
        queue.push([neighbor, [...path, neighbor]]);
      }
    }
  }
  return null;
}

/**
 * Progressive loader — yields batches via requestIdleCallback to avoid
 * blocking the main thread (spec 5.3.1 rule 11/16).
 *
 * @param {Array} items
 * @param {function(Array, number): void} onBatch  called with (batch, batchIndex)
 * @param {object} [opts] { firstBatchSize: 500, batchSize: 200 }
 * @returns {Promise<void>}
 */
export async function progressiveLoad(items, onBatch, opts = {}) {
  const { firstBatchSize = 500, batchSize = 200 } = opts;
  if (!items.length) return;
  const first = items.slice(0, firstBatchSize);
  onBatch(first, 0);
  for (let i = firstBatchSize, bi = 1; i < items.length; i += batchSize, bi++) {
    await new Promise((r) => {
      if (typeof requestIdleCallback === 'function') requestIdleCallback(r);
      else setTimeout(r, 16);  // fallback ~60fps
    });
    onBatch(items.slice(i, i + batchSize), bi);
  }
}

/**
 * ric-aware scheduler shim for environments without requestIdleCallback.
 */
function scheduleIdle(task) {
  if (typeof requestIdleCallback === 'function') requestIdleCallback(task);
  else setTimeout(task, 16);
}

export function useKnowledgeGraph() {
  const api = useApiStore();

  // ── Server-fetched state (raw, unfiltered) ──
  const rawNodes = ref([]);
  const rawEdges = ref([]);
  const stats = ref({ node_count: 0, edge_count: 0 });
  const loading = ref(false);
  const error = ref('');
  const lastParams = ref(null);

  // ── Client-side filter state ──
  const selectedTypes = ref(new Set(['agent', 'task', 'memory', 'concept']));
  const minConfidence = ref(0);
  const searchKeyword = ref('');
  const timeRange = ref({ start: '', end: '' });  // ISO strings, '' = unbounded

  // ── Highlight state ──
  const highlightedNodeId = ref('');
  const highlightedPathIds = ref(new Set());

  // ── Derived: filtered nodes/edges (client-side) ──
  const filteredNodes = computed(() => {
    let nodes = rawNodes.value;
    // Type filter
    const types = selectedTypes.value;
    if (types && types.size < 4) {
      nodes = nodes.filter((n) => types.has(n.type));
    }
    // Confidence filter
    if (minConfidence.value > 0) {
      const mc = minConfidence.value;
      nodes = nodes.filter((n) => (n.confidence ?? 1) >= mc);
    }
    // Search filter (label/id substring, case-insensitive)
    const kw = searchKeyword.value.trim().toLowerCase();
    if (kw) {
      nodes = nodes.filter((n) =>
        (n.label || '').toLowerCase().includes(kw) ||
        (n.id || '').toLowerCase().includes(kw),
      );
    }
    // Time range filter (lexicographic compare on ISO strings)
    const { start, end } = timeRange.value;
    if (start || end) {
      nodes = nodes.filter((n) => {
        if (!n.timestamp) return false;  // no timestamp → exclude when filtering by time
        if (start && n.timestamp < start) return false;
        if (end && n.timestamp > end) return false;
        return true;
      });
    }
    return nodes;
  });

  const filteredEdges = computed(() => {
    const nodes = filteredNodes.value;
    const idSet = new Set(nodes.map((n) => n.id));
    return rawEdges.value.filter((e) => idSet.has(e.source) && idSet.has(e.target));
  });

  // ── Actions ──

  /**
   * Fetch graph data from /api/knowledge-graph.
   * @param {object} [params] { type, time_range, limit }
   */
  async function fetchGraph(params = {}) {
    loading.value = true;
    error.value = '';
    lastParams.value = params;
    try {
      const qs = new URLSearchParams();
      if (params.type) qs.set('type', params.type);
      if (params.time_range) qs.set('time_range', params.time_range);
      qs.set('limit', String(params.limit ?? 500));
      const url = `/api/knowledge-graph?${qs.toString()}`;
      const resp = await api.get(url);
      const data = resp.data || resp;
      rawNodes.value = Array.isArray(data.nodes) ? data.nodes : [];
      rawEdges.value = Array.isArray(data.edges) ? data.edges : [];
      stats.value = data.stats || {
        node_count: rawNodes.value.length,
        edge_count: rawEdges.value.length,
      };
    } catch (e) {
      error.value = e.message || String(e);
      rawNodes.value = [];
      rawEdges.value = [];
      stats.value = { node_count: 0, edge_count: 0 };
    } finally {
      loading.value = false;
    }
  }

  /** Toggle a node type in the filter set. */
  function toggleType(type) {
    const next = new Set(selectedTypes.value);
    if (next.has(type)) next.delete(type);
    else next.add(type);
    selectedTypes.value = next;
  }

  /** Reset all client-side filters to defaults. */
  function resetFilters() {
    selectedTypes.value = new Set(['agent', 'task', 'memory', 'concept']);
    minConfidence.value = 0;
    searchKeyword.value = '';
    timeRange.value = { start: '', end: '' };
    highlightedNodeId.value = '';
    highlightedPathIds.value = new Set();
  }

  /**
   * Highlight the reachable path from a node (spec 5.3.1 rule 7).
   * @param {string} nodeId
   * @param {object} [opts] { directed: boolean }
   */
  function highlightPath(nodeId, opts = {}) {
    highlightedNodeId.value = nodeId;
    const pathIds = computeReachablePath(nodeId, filteredEdges.value, opts);
    highlightedPathIds.value = pathIds;
    return pathIds;
  }

  /** Clear path highlight. */
  function clearHighlight() {
    highlightedNodeId.value = '';
    highlightedPathIds.value = new Set();
  }

  /**
   * Get details for a node: properties, related edges, related nodes.
   * Used by NodeDetailPanel (spec 5.3.1 rule 9).
   */
  function getNodeDetails(nodeId) {
    const node = rawNodes.value.find((n) => n.id === nodeId);
    if (!node) return null;
    const relatedEdges = rawEdges.value.filter(
      (e) => e.source === nodeId || e.target === nodeId,
    );
    const relatedNodeIds = new Set();
    for (const e of relatedEdges) {
      if (e.source !== nodeId) relatedNodeIds.add(e.source);
      if (e.target !== nodeId) relatedNodeIds.add(e.target);
    }
    const relatedNodes = rawNodes.value.filter((n) => relatedNodeIds.has(n.id));
    return { node, relatedEdges, relatedNodes };
  }

  return {
    // state (readonly for external consumers)
    rawNodes: readonly(rawNodes),
    rawEdges: readonly(rawEdges),
    stats: readonly(stats),
    loading: readonly(loading),
    error: readonly(error),
    lastParams: readonly(lastParams),
    selectedTypes,
    minConfidence,
    searchKeyword,
    timeRange,
    highlightedNodeId: readonly(highlightedNodeId),
    highlightedPathIds: readonly(highlightedPathIds),
    // derived
    filteredNodes,
    filteredEdges,
    // actions
    fetchGraph,
    toggleType,
    resetFilters,
    highlightPath,
    clearHighlight,
    getNodeDetails,
  };
}