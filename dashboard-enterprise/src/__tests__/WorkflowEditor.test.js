// Tests for WorkflowEditor.vue — 可视化工作流编辑器。
//
// WorkflowEditor 复用 ListPageLayout 作为骨架, 内部 PageHeader 依赖
// useRoute, 故 stub PageHeader。其余组件 (Badge/AppIcon/EmptyState 等)
// 不依赖 router, 可正常渲染。mock global.fetch 用于执行接口测试。

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { mount, flushPromises } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';
import WorkflowEditor from '../views/WorkflowEditor.vue';

const mountOptions = {
  global: {
    stubs: {
      // PageHeader 依赖 useRoute, 测试环境无 router, stub 掉
      PageHeader: { template: '<slot />' },
    },
  },
};

describe('WorkflowEditor.vue', () => {
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
  });

  async function mountEditor() {
    const wrapper = mount(WorkflowEditor, mountOptions);
    await flushPromises();
    return wrapper;
  }

  // ── 1. 渲染根元素 ────────────────────────────────────────
  it('renders the workflow root element', async () => {
    const wrapper = await mountEditor();
    expect(wrapper.find('.workflow-page').exists()).toBe(true);
    expect(wrapper.find('[data-test="wf-root"]').exists()).toBe(true);
    wrapper.unmount();
  });

  // ── 2. 渲染节点类型列表 (4 种) ───────────────────────────
  it('renders all four node types in the palette', async () => {
    const wrapper = await mountEditor();
    expect(wrapper.find('[data-test="wf-palette-agent"]').exists()).toBe(true);
    expect(wrapper.find('[data-test="wf-palette-tool"]').exists()).toBe(true);
    expect(wrapper.find('[data-test="wf-palette-condition"]').exists()).toBe(true);
    expect(wrapper.find('[data-test="wf-palette-parallel"]').exists()).toBe(true);
    // 面板标题存在
    expect(wrapper.find('.wf-palette__title').exists()).toBe(true);
    wrapper.unmount();
  });

  // ── 3. 空状态显示 ────────────────────────────────────────
  it('shows empty state when no nodes exist', async () => {
    const wrapper = await mountEditor();
    expect(wrapper.find('[data-test="wf-empty"]').exists()).toBe(true);
    expect(wrapper.text()).toContain('Empty workflow');
    // 画布上不应有节点
    expect(wrapper.findAll('.wf-node')).toHaveLength(0);
    wrapper.unmount();
  });

  // ── 4. 添加节点 (通过 expose 的 addNode) ─────────────────
  it('adds a node to the canvas via addNode()', async () => {
    const wrapper = await mountEditor();
    const vm = wrapper.vm;
    const node = vm.addNode('agent', 100, 100);
    await flushPromises();
    expect(node.id).toBeTruthy();
    expect(node.type).toBe('agent');
    expect(vm.nodes).toHaveLength(1);
    // 节点 DOM 渲染
    expect(wrapper.findAll('.wf-node')).toHaveLength(1);
    expect(wrapper.find(`[data-test="wf-node-${node.id}"]`).exists()).toBe(true);
    // 空态消失
    expect(wrapper.find('[data-test="wf-empty"]').exists()).toBe(false);
    wrapper.unmount();
  });

  // ── 5. 选中节点显示属性面板 ──────────────────────────────
  it('shows inspector form when a node is selected', async () => {
    const wrapper = await mountEditor();
    const vm = wrapper.vm;
    const node = vm.addNode('tool', 50, 50);
    await flushPromises();
    // addNode 自动选中
    expect(vm.selectedId).toBe(node.id);
    expect(wrapper.find('[data-test="wf-inspector-form"]').exists()).toBe(true);
    expect(wrapper.find('[data-test="wf-input-label"]').exists()).toBe(true);
    // tool 类型应有 tool 名称输入
    expect(wrapper.find('[data-test="wf-input-tool"]').exists()).toBe(true);
    wrapper.unmount();
  });

  // ── 6. 删除节点 ──────────────────────────────────────────
  it('deletes the selected node', async () => {
    const wrapper = await mountEditor();
    const vm = wrapper.vm;
    vm.addNode('agent', 10, 10);
    await flushPromises();
    expect(vm.nodes).toHaveLength(1);
    vm.deleteSelected();
    await flushPromises();
    expect(vm.nodes).toHaveLength(0);
    expect(vm.selectedId).toBe('');
    wrapper.unmount();
  });

  // ── 7. 导出 DAG JSON ─────────────────────────────────────
  it('exports DAG JSON with nodes and edges', async () => {
    const wrapper = await mountEditor();
    const vm = wrapper.vm;
    const a = vm.addNode('agent', 0, 0);
    const b = vm.addNode('tool', 200, 100);
    vm.addEdge(a.id, b.id);
    await flushPromises();
    const dag = vm.exportDag();
    expect(dag.nodes).toHaveLength(2);
    expect(dag.edges).toHaveLength(1);
    expect(dag.edges[0]).toEqual({ source: a.id, target: b.id });
    // 节点字段完整
    expect(dag.nodes[0]).toHaveProperty('id');
    expect(dag.nodes[0]).toHaveProperty('type');
    expect(dag.nodes[0]).toHaveProperty('label');
    expect(dag.nodes[0]).toHaveProperty('x');
    expect(dag.nodes[0]).toHaveProperty('y');
    expect(dag.nodes[0]).toHaveProperty('config');
    wrapper.unmount();
  });

  // ── 8. 导入 DAG JSON ─────────────────────────────────────
  it('imports DAG JSON via importDag()', async () => {
    const wrapper = await mountEditor();
    const vm = wrapper.vm;
    const dag = {
      nodes: [
        { id: 'n1', type: 'agent', label: 'A1', x: 10, y: 20, config: { agent: 'claude' } },
        { id: 'n2', type: 'tool', label: 'T1', x: 100, y: 50, config: { tool: 'search' } },
      ],
      edges: [{ source: 'n1', target: 'n2' }],
    };
    vm.importDag(dag);
    await flushPromises();
    expect(vm.nodes).toHaveLength(2);
    expect(vm.edges).toHaveLength(1);
    expect(vm.nodes[0].id).toBe('n1');
    expect(vm.nodes[0].config.agent).toBe('claude');
    // DOM 渲染导入的节点
    expect(wrapper.findAll('.wf-node')).toHaveLength(2);
    wrapper.unmount();
  });

  // ── 9. 执行调用 POST /api/dag/execute ───────────────────
  it('calls POST /api/dag/execute on execute', async () => {
    const fetchMock = vi.fn(() => Promise.resolve({
      ok: true,
      status: 200,
      json: () => Promise.resolve({ run_id: 'run-123' }),
    }));
    global.fetch = fetchMock;
    const wrapper = await mountEditor();
    const vm = wrapper.vm;
    vm.addNode('agent', 0, 0);
    await flushPromises();
    await vm.onExecuteClick();
    await flushPromises();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const callArgs = fetchMock.mock.calls[0];
    expect(callArgs[0]).toBe('/api/dag/execute');
    const init = callArgs[1];
    expect(init.method).toBe('POST');
    expect(init.headers['Content-Type']).toBe('application/json');
    const body = JSON.parse(init.body);
    expect(body.nodes).toHaveLength(1);
    wrapper.unmount();
  });

  // ── 10. 连线校验: 不能自连 ───────────────────────────────
  it('rejects self-loop edges', async () => {
    const wrapper = await mountEditor();
    const vm = wrapper.vm;
    const a = vm.addNode('agent', 0, 0);
    await flushPromises();
    const added = vm.addEdge(a.id, a.id);
    expect(added).toBe(false);
    expect(vm.edges).toHaveLength(0);
    wrapper.unmount();
  });

  // ── 11. 连线校验: 重复边拒绝 ─────────────────────────────
  it('rejects duplicate edges', async () => {
    const wrapper = await mountEditor();
    const vm = wrapper.vm;
    const a = vm.addNode('agent', 0, 0);
    const b = vm.addNode('tool', 100, 100);
    await flushPromises();
    expect(vm.addEdge(a.id, b.id)).toBe(true);
    expect(vm.addEdge(a.id, b.id)).toBe(false);
    expect(vm.edges).toHaveLength(1);
    wrapper.unmount();
  });

  // ── 12. 清空所有 ─────────────────────────────────────────
  it('clears all nodes and edges via clearAll()', async () => {
    const wrapper = await mountEditor();
    const vm = wrapper.vm;
    const a = vm.addNode('agent', 0, 0);
    const b = vm.addNode('tool', 100, 100);
    vm.addEdge(a.id, b.id);
    await flushPromises();
    expect(vm.nodes).toHaveLength(2);
    expect(vm.edges).toHaveLength(1);
    vm.clearAll();
    await flushPromises();
    expect(vm.nodes).toHaveLength(0);
    expect(vm.edges).toHaveLength(0);
    expect(vm.selectedId).toBe('');
    // 空态恢复
    expect(wrapper.find('[data-test="wf-empty"]').exists()).toBe(true);
    wrapper.unmount();
  });

  // ── 13. 属性面板更新节点标签 ─────────────────────────────
  it('updates node label via inspector input', async () => {
    const wrapper = await mountEditor();
    const vm = wrapper.vm;
    vm.addNode('agent', 0, 0);
    await flushPromises();
    const input = wrapper.find('[data-test="wf-input-label"]');
    expect(input.exists()).toBe(true);
    await input.setValue('My Agent Node');
    await flushPromises();
    expect(vm.nodes[0].label).toBe('My Agent Node');
    wrapper.unmount();
  });

  // ── 14. 工具栏统计显示 ───────────────────────────────────
  it('displays node and edge counts in the toolbar', async () => {
    const wrapper = await mountEditor();
    const vm = wrapper.vm;
    const a = vm.addNode('agent', 0, 0);
    const b = vm.addNode('tool', 100, 100);
    vm.addEdge(a.id, b.id);
    await flushPromises();
    const toolbar = wrapper.find('[data-test="wf-toolbar"]');
    expect(toolbar.exists()).toBe(true);
    expect(toolbar.text()).toContain('2 nodes');
    expect(toolbar.text()).toContain('1 edges');
    wrapper.unmount();
  });

  // ── 15. 执行按钮在无节点时禁用 ───────────────────────────
  it('disables execute button when there are no nodes', async () => {
    const wrapper = await mountEditor();
    const btn = wrapper.find('[data-test="wf-execute"]');
    expect(btn.exists()).toBe(true);
    expect(btn.attributes('disabled')).toBeDefined();
    wrapper.unmount();
  });
});