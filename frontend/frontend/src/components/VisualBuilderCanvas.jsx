import React, { useState, useCallback } from 'react';
import ReactFlow, {
  ReactFlowProvider,
  addEdge,
  useNodesState,
  useEdgesState,
  Controls,
  MiniMap,
  Background,
} from 'reactflow';
import 'reactflow/dist/style.css';

// 自定义节点类型
const nodeTypes = {
  trigger: ({ data }) => (
    <div className="node-trigger">
      <div className="node-title">{data.label}</div>
      <div className="node-desc">{data.config?.event || '触发节点'}</div>
    </div>
  ),
  filter: ({ data }) => (
    <div className="node-filter">
      <div className="node-title">{data.label}</div>
      <div className="node-desc">{data.config?.condition || '过滤节点'}</div>
    </div>
  ),
  llm_node: ({ data }) => (
    <div className="node-llm">
      <div className="node-title">{data.label}</div>
      <div className="node-desc">{data.config?.task || 'LLM推理节点'}</div>
    </div>
  ),
  response: ({ data }) => (
    <div className="node-response">
      <div className="node-title">{data.label}</div>
      <div className="node-desc">{data.config?.action || '响应节点'}</div>
    </div>
  ),
};

const VisualBuilderCanvas = () => {
  const [nodes, setNodes, onNodesChange] = useNodesState([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);
  const [workflowName, setWorkflowName] = useState('新建工作流');

  // 连接节点
  const onConnect = useCallback(
    (params) => setEdges((eds) => addEdge(params, eds)),
    [setEdges]
  );

  // 添加新节点
  const addNode = useCallback((type) => {
    const newNode = {
      id: `node_${Date.now()}`,
      type,
      position: { x: Math.random() * 400, y: Math.random() * 400 },
      data: {
        label: `新${type}节点`,
        config: {}
      },
    };
    setNodes((nds) => nds.concat(newNode));
  }, [setNodes]);

  // 导出为YAML
  const exportYaml = async () => {
    const dag = {
      name: workflowName,
      nodes: nodes.map(n => ({
        id: n.id,
        type: n.type,
        config: n.data.config || {}
      })),
      edges: edges.map(e => ({
        from: e.source,
        to: e.target
      }))
    };
    try {
      const response = await fetch('/api/v1/workflow/compile', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(dag)
      });
      const yaml = await response.text();
      // 下载YAML文件
      const blob = new Blob([yaml], { type: 'text/yaml' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `${workflowName}.yaml`;
      a.click();
    } catch (error) {
      alert('导出失败: ' + error.message);
    }
  };

  return (
    <div className="visual-builder">
      <div className="toolbar">
        <input
          value={workflowName}
          onChange={(e) => setWorkflowName(e.target.value)}
          placeholder="工作流名称"
        />
        <button onClick={() => addNode('trigger')}>添加触发节点</button>
        <button onClick={() => addNode('filter')}>添加过滤节点</button>
        <button onClick={() => addNode('llm_node')}>添加LLM节点</button>
        <button onClick={() => addNode('response')}>添加响应节点</button>
        <button onClick={exportYaml} className="export-btn">导出YAML</button>
      </div>
      <div style={{ width: '100%', height: '80vh' }}>
        <ReactFlow
          nodes={nodes}
          edges={edges}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onConnect={onConnect}
          nodeTypes={nodeTypes}
          fitView
        >
          <Controls />
          <MiniMap />
          <Background variant="dots" gap={12} size={1} />
        </ReactFlow>
      </div>
    </div>
  );
};

export default () => (
  <ReactFlowProvider>
    <VisualBuilderCanvas />
  </ReactFlowProvider>
);
