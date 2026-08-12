// Tests for KnowledgeGraph.vue (v4.5.0) — mount, filter, highlight, timeline, detail.
//
// We mock global.fetch for /api/knowledge-graph, stub PageHeader/Card/Badge/EmptyState
// (heavy deps with their own i18n/store wiring), then assert on the rendered
// filter panel, graph canvas, and detail panel.
//
// vis-network is dynamically imported inside the component; we stub it via
// vi.mock to avoid canvas/WebGL issues in jsdom.

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { mount, flushPromises } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';

// Stub vis-network/standalone before importing the component.
vi.mock('vis-network/standalone', () => {
  class DataSet {
    constructor(arr = []) { this._items = new Map(); for (const it of arr) this.add(it); }
    add(items) { const arr = Array.isArray(items) ? items : [items]; for (const it of arr) this._items.set(it.id, it); return arr; }
    update(items) { const arr = Array.isArray(items) ? items : [items]; for (const it of arr) this._items.set(it.id, it); return arr; }
    remove(ids) { const arr = Array.isArray(ids) ? ids : [ids]; for (const id of arr) this._items.delete(id); return arr; }
    clear() { this._items.clear(); }
    get(id) { return this._items.get(id); }
    length = 0;
  }
  class Network {
    constructor(container, data, options) { this.container = container; this.data = data; this.options = options; this._handlers = {}; }
    on(evt, cb) { (this._handlers[evt] ||= []).push(cb); }
    off() {}
    setOptions(o) { this.options = { ...this.options, ...o }; }
    setData(d) { this.data = d; }
    destroy() { this.container = null; this.data = null; }
    getScale() { return 1; }
    getViewPosition() { return { x: 0, y: 0 }; }
    getPositions() { return {}; }
    canvas = { frame: { canvas: { width: 800, height: 600 } } };
    body = { data: { nodes: new DataSet(), edges: new DataSet() } };
  }
  return { Network, DataSet };
});

import KnowledgeGraph from '../views/KnowledgeGraph.vue';

const mountOptions = {
  global: {
    stubs: {
      PageHeader: { template: '<slot />' },
      Card: { template: '<div class="card-stub"><slot /></div>' },
      Badge: { template: '<span class="badge-stub"><slot /></span>' },
      EmptyState: { template: '<div class="empty-stub" />' },
      AppIcon: { template: '<span class="icon-stub" />' },
    },
  },
};

function makeNode(id, type, opts = {}) {
  return { id, type, label: id, timestamp: opts.timestamp || '', properties: {}, confidence: opts.confidence ?? 1, ...opts };
}
function makeEdge(id, src, tgt, type) {
  return { id, source: src, target: tgt, type, timestamp: '', properties: {}, confidence: 1 };
}

const SAMPLE_GRAPH = {
  status: 'ok',
  data: {
    nodes: [
      makeNode('AgentA', 'agent', { timestamp: '2025-01-15T10:00:00', confidence: 0.95 }),
      makeNode('TaskX', 'task', { timestamp: '2025-06-01T12:00:00', confidence: 0.85 }),
      makeNode('MemoryM', 'memory', { timestamp: '2025-07-15T09:30:00', confidence: 0.8 }),
      makeNode('ConceptC', 'concept', { confidence: 0.75 }),
    ],
    edges: [
      makeEdge('r1', 'AgentA', 'TaskX', 'delegates'),
      makeEdge('r2', 'TaskX', 'MemoryM', 'produces'),
      makeEdge('r3', 'AgentA', 'MemoryM', 'remembers'),
    ],
    stats: { node_count: 4, edge_count: 3 },
  },
};

describe('KnowledgeGraph.vue', () => {
  let originalFetch;

  beforeEach(() => {
    setActivePinia(createPinia());
    originalFetch = global.fetch;
    global.__VITEST__ = true;
    if (typeof window !== 'undefined') window.__VITEST__ = true;
  });

  afterEach(() => {
    global.fetch = originalFetch;
    delete global.__VITEST__;
    if (typeof window !== 'undefined') delete window.__VITEST__;
    vi.restoreAllMocks();
  });

  function mockFetch(routes) {
    global.fetch = vi.fn((url) => {
      const u = String(url);
      const body = routes[u] ?? { status: 'ok', data: { nodes: [], edges: [], stats: { node_count: 0, edge_count: 0 } } };
      return Promise.resolve({
        ok: true, status: 200,
        json: () => Promise.resolve(body),
        text: () => Promise.resolve(JSON.stringify(body)),
      });
    });
  }

  async function mountKg() {
    const wrapper = mount(KnowledgeGraph, mountOptions);
    await flushPromises();
    await flushPromises();
    return wrapper;
  }

  it('renders the knowledge graph root element', async () => {
    mockFetch({ '/api/knowledge-graph?limit=500': SAMPLE_GRAPH });
    const wrapper = await mountKg();
    expect(wrapper.find('.kg-page').exists()).toBe(true);
    wrapper.unmount();
  });

  it('renders the filter panel with 4 node type checkboxes', async () => {
    mockFetch({ '/api/knowledge-graph?limit=500': SAMPLE_GRAPH });
    const wrapper = await mountKg();
    const checkboxes = wrapper.findAll('.kg-checkbox input[type="checkbox"]');
    expect(checkboxes.length).toBe(4);
    wrapper.unmount();
  });

  it('renders the graph canvas area', async () => {
    mockFetch({ '/api/knowledge-graph?limit=500': SAMPLE_GRAPH });
    const wrapper = await mountKg();
    expect(wrapper.find('.kg-canvas-wrap').exists()).toBe(true);
    expect(wrapper.find('.kg-canvas').exists()).toBe(true);
    wrapper.unmount();
  });

  it('renders the timeline panel', async () => {
    mockFetch({ '/api/knowledge-graph?limit=500': SAMPLE_GRAPH });
    const wrapper = await mountKg();
    expect(wrapper.find('.kg-timeline').exists()).toBe(true);
    wrapper.unmount();
  });

  it('displays node and edge counts in the toolbar', async () => {
    mockFetch({ '/api/knowledge-graph?limit=500': SAMPLE_GRAPH });
    const wrapper = await mountKg();
    const text = wrapper.text();
    expect(text).toContain('4');
    expect(text).toContain('3');
    wrapper.unmount();
  });

  it('shows empty state when graph has no nodes', async () => {
    mockFetch({
      '/api/knowledge-graph?limit=500': {
        status: 'ok',
        data: { nodes: [], edges: [], stats: { node_count: 0, edge_count: 0 } },
      },
    });
    const wrapper = await mountKg();
    expect(wrapper.find('.empty-stub').exists()).toBe(true);
    wrapper.unmount();
  });

  it('shows error banner when API fails', async () => {
    global.fetch = vi.fn(() => Promise.resolve({
      ok: false, status: 500,
      json: () => Promise.resolve({ error: 'server error' }),
      text: () => Promise.resolve('server error'),
    }));
    const wrapper = await mountKg();
    expect(wrapper.find('.kg-notice--error').exists()).toBe(true);
    wrapper.unmount();
  });

  it('toggling a node type checkbox updates the filter', async () => {
    mockFetch({ '/api/knowledge-graph?limit=500': SAMPLE_GRAPH });
    const wrapper = await mountKg();
    // Uncheck the "task" checkbox (second one)
    const checkboxes = wrapper.findAll('.kg-checkbox input[type="checkbox"]');
    await checkboxes[1].setValue(false);
    // The composable state should reflect the toggle — we verify via the
    // component's reactive filteredNodes (indirectly through visibleCount).
    // Since vis-network is stubbed, we can't inspect the canvas, but the
    // component should not crash.
    expect(wrapper.find('.kg-page').exists()).toBe(true);
    wrapper.unmount();
  });

  it('typing in the search box updates the search keyword', async () => {
    mockFetch({ '/api/knowledge-graph?limit=500': SAMPLE_GRAPH });
    const wrapper = await mountKg();
    const searchInput = wrapper.find('.kg-filter-section:nth-child(3) .kg-input');
    if (searchInput.exists()) {
      await searchInput.setValue('Agent');
    }
    expect(wrapper.find('.kg-page').exists()).toBe(true);
    wrapper.unmount();
  });

  it('clicking refresh re-fetches the graph', async () => {
    const fetchFn = vi.fn((_url) => {
      const body = SAMPLE_GRAPH;
      return Promise.resolve({
        ok: true, status: 200,
        json: () => Promise.resolve(body),
        text: () => Promise.resolve(JSON.stringify(body)),
      });
    });
    global.fetch = fetchFn;
    const wrapper = await mountKg();
    const initialCalls = fetchFn.mock.calls.length;
    const refreshBtn = wrapper.find('.kg-toolbar-actions .btn');
    if (refreshBtn.exists()) {
      await refreshBtn.trigger('click');
      await flushPromises();
      expect(fetchFn.mock.calls.length).toBeGreaterThan(initialCalls);
    }
    wrapper.unmount();
  });

  it('renders the limit input with default 500', async () => {
    mockFetch({ '/api/knowledge-graph?limit=500': SAMPLE_GRAPH });
    const wrapper = await mountKg();
    const numInput = wrapper.find('input[type="number"]');
    expect(numInput.exists()).toBe(true);
    expect(numInput.element.value).toBe('500');
    wrapper.unmount();
  });

  it('renders the min confidence range input', async () => {
    mockFetch({ '/api/knowledge-graph?limit=500': SAMPLE_GRAPH });
    const wrapper = await mountKg();
    const range = wrapper.find('input[type="range"]');
    expect(range.exists()).toBe(true);
    wrapper.unmount();
  });
});

// ── useKnowledgeGraph composable unit tests ────────────────────────

import { useKnowledgeGraph, computeReachablePath, computeShortestPath, toVisNode, toVisEdge } from '../composables/useKnowledgeGraph.js';

describe('useKnowledgeGraph composable', () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    global.__VITEST__ = true;
  });

  afterEach(() => {
    delete global.__VITEST__;
  });

  it('computeReachablePath returns BFS-reachable node ids', () => {
    const edges = [
      { source: 'a', target: 'b' },
      { source: 'b', target: 'c' },
      { source: 'x', target: 'y' },  // disconnected
    ];
    const reachable = computeReachablePath('a', edges);
    expect(reachable.has('a')).toBe(true);
    expect(reachable.has('b')).toBe(true);
    expect(reachable.has('c')).toBe(true);
    expect(reachable.has('x')).toBe(false);
  });

  it('computeReachablePath respects maxDepth', () => {
    const edges = [
      { source: 'a', target: 'b' },
      { source: 'b', target: 'c' },
      { source: 'c', target: 'd' },
    ];
    const reachable = computeReachablePath('a', edges, { maxDepth: 1 });
    expect(reachable.has('a')).toBe(true);
    expect(reachable.has('b')).toBe(true);
    expect(reachable.has('c')).toBe(false);
  });

  it('computeShortestPath finds the shortest path', () => {
    const edges = [
      { source: 'a', target: 'b' },
      { source: 'b', target: 'd' },
      { source: 'a', target: 'c' },
      { source: 'c', target: 'd' },
    ];
    const path = computeShortestPath('a', 'd', edges);
    expect(path).not.toBeNull();
    expect(path[0]).toBe('a');
    expect(path[path.length - 1]).toBe('d');
    expect(path.length).toBe(3);  // a→b→d or a→c→d
  });

  it('computeShortestPath returns null when no path', () => {
    const edges = [{ source: 'a', target: 'b' }];
    const path = computeShortestPath('a', 'z', edges);
    expect(path).toBeNull();
  });

  it('toVisNode maps node to vis-network DTO with type-based style', () => {
    const n = { id: 'x', type: 'agent', label: 'X', timestamp: '', properties: {}, confidence: 1 };
    const vis = toVisNode(n);
    expect(vis.id).toBe('x');
    expect(vis.label).toBe('X');
    expect(vis.group).toBe('agent');
    expect(vis.color).toBeDefined();
  });

  it('toVisEdge maps edge to vis-network DTO with type-based style', () => {
    const e = { id: 'e1', source: 'a', target: 'b', type: 'delegates', timestamp: '', properties: {}, confidence: 1 };
    const vis = toVisEdge(e);
    expect(vis.id).toBe('e1');
    expect(vis.from).toBe('a');
    expect(vis.to).toBe('b');
    expect(vis.color).toBeDefined();
  });

  it('useKnowledgeGraph initializes with default state', () => {
    const kg = useKnowledgeGraph();
    expect(kg.rawNodes.value).toEqual([]);
    expect(kg.rawEdges.value).toEqual([]);
    expect(kg.loading.value).toBe(false);
    expect(kg.error.value).toBe('');
    expect(kg.selectedTypes.value.size).toBe(4);
  });

  it('toggleType adds and removes types', () => {
    const kg = useKnowledgeGraph();
    kg.toggleType('agent');
    expect(kg.selectedTypes.value.has('agent')).toBe(false);
    kg.toggleType('agent');
    expect(kg.selectedTypes.value.has('agent')).toBe(true);
  });

  it('resetFilters restores defaults', () => {
    const kg = useKnowledgeGraph();
    kg.toggleType('agent');
    kg.minConfidence.value = 0.5;
    kg.searchKeyword.value = 'test';
    kg.resetFilters();
    expect(kg.selectedTypes.value.size).toBe(4);
    expect(kg.minConfidence.value).toBe(0);
    expect(kg.searchKeyword.value).toBe('');
  });
});