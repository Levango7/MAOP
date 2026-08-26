<template>
  <div class="workflow-page">
    <ListPageLayout
      :loading="false"
      :error="error"
      :empty="false"
      :error-title="t('view.workflow.importFailed', { msg: error })"
    >
      <template #badges>
        <Badge tone="brand">{{ t('view.workflow.subtitle') }}</Badge>
      </template>
      <template #actions>
        <button class="btn" data-test="wf-import" @click="onImportClick">
          <AppIcon name="download" :size="14" /> {{ t('view.workflow.import') }}
        </button>
        <button class="btn" data-test="wf-export" @click="onExportClick">
          <AppIcon name="external-link" :size="14" /> {{ t('view.workflow.export') }}
        </button>
        <button
          class="btn btn--primary"
          data-test="wf-execute"
          :disabled="executing || !nodes.length"
          @click="onExecuteClick"
        >
          <AppIcon name="play" :size="14" />
          {{ executing ? t('view.workflow.executing') : t('view.workflow.execute') }}
        </button>
      </template>

      <template #content>
        <div class="wf-layout" data-test="wf-root">
          <!-- 左侧：节点面板 -->
          <aside class="wf-palette" data-test="wf-palette">
            <div class="wf-palette__title">{{ t('view.workflow.palette') }}</div>
            <div class="wf-palette__hint">{{ t('view.workflow.paletteHint') }}</div>
            <ul class="wf-palette__list">
              <li
                v-for="nt in nodeTypes"
                :key="nt.type"
                class="wf-palette-item"
                :class="'wf-palette-item--' + nt.type"
                :data-test="`wf-palette-${nt.type}`"
                draggable="true"
                @dragstart="onPaletteDragStart($event, nt.type)"
              >
                <span class="wf-palette-item__icon"><AppIcon :name="nt.icon" :size="16" /></span>
                <span class="wf-palette-item__label">{{ t(nt.label) }}</span>
                <span class="wf-palette-item__desc">{{ t(nt.desc) }}</span>
              </li>
            </ul>
          </aside>

          <!-- 中间：画布 -->
          <section
            class="wf-canvas"
            data-test="wf-canvas"
            @dragover.prevent="onCanvasDragOver"
            @drop.prevent="onCanvasDrop"
            @click.self="onCanvasBackgroundClick"
          >
            <svg class="wf-edges" :width="canvasSize" :height="canvasSize">
              <path
                v-for="edge in renderedEdges"
                :key="`${edge.source}->${edge.target}`"
                :d="edge.path"
                class="wf-edge"
                :data-test="`wf-edge-${edge.source}-${edge.target}`"
              />
            </svg>
            <div
              v-for="node in nodes"
              :key="node.id"
              class="wf-node"
              :class="[
                `wf-node--${node.type}`,
                { 'is-selected': node.id === selectedId },
              ]"
              :style="{ left: node.x + 'px', top: node.y + 'px' }"
              :data-test="`wf-node-${node.id}`"
              :data-node-id="node.id"
              draggable="true"
              @click.stop="onNodeClick(node)"
              @dragstart="onNodeDragStart($event, node)"

            >
              <span class="wf-node__icon"><AppIcon :name="iconForType(node.type)" :size="14" /></span>
              <span class="wf-node__label">{{ node.label || node.id }}</span>
              <span class="wf-node__type">{{ t(labelKeyForType(node.type)) }}</span>
              <button
                class="wf-node__port"
                data-test="wf-node-port"
                title="output"
                @click.stop.prevent="onPortClick(node)"
              />
            </div>
            <div v-if="!nodes.length" class="wf-canvas-empty" data-test="wf-empty">
              <AppIcon name="archive" :size="34" />
              <div class="wf-canvas-empty__title">{{ t('view.workflow.emptyTitle') }}</div>
              <div class="wf-canvas-empty__desc">{{ t('view.workflow.emptyDesc') }}</div>
            </div>
          </section>

          <!-- 右侧：属性面板 -->
          <aside class="wf-inspector" data-test="wf-inspector">
            <div class="wf-inspector__title">{{ t('view.workflow.inspector') }}</div>
            <div v-if="!selectedNode" class="wf-inspector__empty" data-test="wf-inspector-empty">
              <AppIcon name="compass" :size="22" />
              <div>{{ t('view.workflow.noSelection') }}</div>
              <div class="wf-inspector__hint">{{ t('view.workflow.noSelectionDesc') }}</div>
            </div>
            <div v-else class="wf-inspector__form" data-test="wf-inspector-form">
              <label class="wf-field">
                <span class="wf-field__label">{{ t('view.workflow.propId') }}</span>
                <input class="input wf-field__input" :value="selectedNode.id" disabled />
              </label>
              <label class="wf-field">
                <span class="wf-field__label">{{ t('view.workflow.propLabel') }}</span>
                <input
                  class="input wf-field__input"
                  data-test="wf-input-label"
                  :value="selectedNode.label"
                  @input="updateSelected('label', $event.target.value)"
                />
              </label>
              <label class="wf-field">
                <span class="wf-field__label">{{ t('view.workflow.propType') }}</span>
                <input class="input wf-field__input" :value="t(labelKeyForType(selectedNode.type))" disabled />
              </label>
              <label v-if="selectedNode.type === 'agent'" class="wf-field">
                <span class="wf-field__label">{{ t('view.workflow.propAgent') }}</span>
                <input
                  class="input wf-field__input"
                  data-test="wf-input-agent"
                  :value="selectedNode.config.agent || ''"
                  @input="updateSelectedConfig('agent', $event.target.value)"
                />
              </label>
              <label v-if="selectedNode.type === 'tool'" class="wf-field">
                <span class="wf-field__label">{{ t('view.workflow.propTool') }}</span>
                <input
                  class="input wf-field__input"
                  data-test="wf-input-tool"
                  :value="selectedNode.config.tool || ''"
                  @input="updateSelectedConfig('tool', $event.target.value)"
                />
              </label>
              <label v-if="selectedNode.type === 'condition'" class="wf-field">
                <span class="wf-field__label">{{ t('view.workflow.propPredicate') }}</span>
                <input
                  class="input wf-field__input"
                  data-test="wf-input-predicate"
                  :value="selectedNode.config.predicate || ''"
                  @input="updateSelectedConfig('predicate', $event.target.value)"
                />
              </label>
              <label v-if="selectedNode.type === 'parallel' || selectedNode.type === 'condition'" class="wf-field">
                <span class="wf-field__label">{{ t('view.workflow.propBranches') }}</span>
                <input
                  type="number"
                  min="1"
                  max="16"
                  class="input wf-field__input"
                  data-test="wf-input-branches"
                  :value="selectedNode.config.branches || 2"
                  @input="updateSelectedConfig('branches', Number($event.target.value))"
                />
              </label>
              <label class="wf-field">
                <span class="wf-field__label">{{ t('view.workflow.propConfig') }}</span>
                <textarea
                  class="input wf-field__textarea"
                  data-test="wf-input-config"
                  :value="configJson"
                  @input="updateSelectedConfigJson($event.target.value)"
                />
              </label>
              <button
                class="btn btn--danger btn--sm"
                data-test="wf-delete"
                @click="deleteSelected"
              >
                <AppIcon name="trash" :size="14" /> {{ t('view.workflow.deleteNode') }}
              </button>
            </div>
          </aside>
        </div>

        <!-- 底部工具栏 -->
        <div class="wf-toolbar" data-test="wf-toolbar">
          <span class="wf-toolbar__label">{{ t('view.workflow.toolbar') }}</span>
          <span class="wf-toolbar__stats">
            {{ nodes.length }} nodes · {{ edges.length }} edges
          </span>
          <button class="btn btn--sm" data-test="wf-clear" @click="clearAll">
            <AppIcon name="trash" :size="14" /> {{ t('view.workflow.clear') }}
          </button>
          <button class="btn btn--sm" data-test="wf-toolbar-import" @click="onImportClick">
            <AppIcon name="download" :size="14" /> {{ t('view.workflow.import') }}
          </button>
          <button class="btn btn--sm" data-test="wf-toolbar-export" @click="onExportClick">
            <AppIcon name="external-link" :size="14" /> {{ t('view.workflow.export') }}
          </button>
          <button
            class="btn btn--sm btn--primary"
            data-test="wf-toolbar-execute"
            :disabled="executing || !nodes.length"
            @click="onExecuteClick"
          >
            <AppIcon name="play" :size="14" />
            {{ executing ? t('view.workflow.executing') : t('view.workflow.execute') }}
          </button>
        </div>

        <!-- 隐藏的导入文件输入 -->
        <input
          ref="fileInputRef"
          type="file"
          accept="application/json,.json"
          class="wf-file-input"
          data-test="wf-file-input"
          @change="onImportFileChange"
        />
      </template>
    </ListPageLayout>
  </div>
</template>

<script setup>
/**
 * WorkflowEditor — 可视化工作流编辑器。
 *
 * 设计要点:
 *  - 纯 CSS + SVG 实现, 不引入 VueFlow / reactflow 等任何新依赖。
 *  - 节点用绝对定位 div, 连线用 SVG path (三次贝塞尔), 由源节点右侧端口
 *    到目标节点左侧中点。
 *  - 拖拽: 左侧面板项 draggable, drop 到画布生成节点; 画布上节点 draggable,
 *    用于重新定位 (拖拽时不连线)。
 *  - 连线: 点击节点右侧 output 端口进入"连线模式", 再点击另一节点完成连线。
 *  - DAG JSON: { nodes: [{id,type,label,x,y,config}], edges: [{source,target}] }。
 *  - 执行: POST /api/dag/execute, body 即导出的 DAG JSON。
 *
 * 复用 ListPageLayout 作为页面骨架 (页头/三态/插槽)。
 */
import { ref, computed } from 'vue';
import { useI18n } from '../i18n/index.js';
import { useToast } from '../composables/useToast.js';
import ListPageLayout from '../components/ListPageLayout.vue';
import Badge from '../components/Badge.vue';
import AppIcon from '../components/AppIcon.vue';

const { t } = useI18n();
const toast = useToast();

// ── 节点类型元数据 ──────────────────────────────────────────
const nodeTypes = [
  { type: 'agent', icon: 'bot', label: 'view.workflow.typeAgent', desc: 'view.workflow.typeAgentDesc' },
  { type: 'tool', icon: 'wrench', label: 'view.workflow.typeTool', desc: 'view.workflow.typeToolDesc' },
  { type: 'condition', icon: 'route', label: 'view.workflow.typeCondition', desc: 'view.workflow.typeConditionDesc' },
  { type: 'parallel', icon: 'network', label: 'view.workflow.typeParallel', desc: 'view.workflow.typeParallelDesc' },
];

function iconForType(type) {
  const m = nodeTypes.find((n) => n.type === type);
  return m ? m.icon : 'box';
}
function labelKeyForType(type) {
  const m = nodeTypes.find((n) => n.type === type);
  return m ? m.label : 'view.workflow.typeTool';
}

// ── 状态 ────────────────────────────────────────────────────
const nodes = ref([]);
const edges = ref([]);
const selectedId = ref('');
const error = ref('');
const executing = ref(false);
const fileInputRef = ref(null);

// 连线模式: 点击 output 端口后, pendingEdgeSource 记录源节点 id
const pendingEdgeSource = ref('');

// 画布尺寸 (SVG 视口), 给一个足够大的固定值, 节点超出则裁切
const canvasSize = 4000;

let _idSeq = 0;
function genId(type) {
  _idSeq += 1;
  return `${type}_${Date.now().toString(36)}_${_idSeq}`;
}

const selectedNode = computed(() => nodes.value.find((n) => n.id === selectedId.value) || null);

const configJson = computed(() => {
  if (!selectedNode.value) return '';
  try {
    return JSON.stringify(selectedNode.value.config || {}, null, 2);
  } catch {
    return '{}';
  }
});

// ── 拖拽: 从面板拖入画布 ────────────────────────────────────
function onPaletteDragStart(event, type) {
  // dataTransfer 携带节点类型, drop 时读取
  event.dataTransfer.effectAllowed = 'copy';
  event.dataTransfer.setData('application/x-wf-palette', type);
}

function onCanvasDragOver(event) {
  // 允许 copy 操作 (否则浏览器默认禁止 drop)
  if (event.dataTransfer) event.dataTransfer.dropEffect = 'copy';
}

function onCanvasDrop(event) {
  const type = event.dataTransfer && event.dataTransfer.getData('application/x-wf-palette');
  if (!type) return;
  // drop 坐标相对于画布; 减去节点半宽使光标落在节点中心
  const rect = event.currentTarget.getBoundingClientRect();
  const x = Math.max(0, Math.round(event.clientX - rect.left - 70));
  const y = Math.max(0, Math.round(event.clientY - rect.top - 18));
  addNode(type, x, y);
}

// ── 拖拽: 画布上移动节点 ────────────────────────────────────
function onNodeDragStart(event, node) {
  // 节点本身的拖拽 (而非面板): 标记 dataTransfer 以区分, drop 时可在画布
  // onCanvasDrop 中判断是否为节点移动 (当前版本仅支持面板拖入新建节点)。
  if (event.dataTransfer) {
    event.dataTransfer.effectAllowed = 'move';
    event.dataTransfer.setData('application/x-wf-node', node.id);
  }
}

// ── 节点 CRUD ──────────────────────────────────────────────
function defaultConfig(type) {
  if (type === 'agent') return { agent: '' };
  if (type === 'tool') return { tool: '' };
  if (type === 'condition') return { predicate: '', branches: 2 };
  if (type === 'parallel') return { branches: 2 };
  return {};
}

function addNode(type, x = 80, y = 80) {
  const id = genId(type);
  const node = {
    id,
    type,
    label: t(labelKeyForType(type)),
    x,
    y,
    config: defaultConfig(type),
  };
  nodes.value.push(node);
  selectedId.value = id;
  return node;
}

function deleteSelected() {
  if (!selectedId.value) return;
  const id = selectedId.value;
  nodes.value = nodes.value.filter((n) => n.id !== id);
  edges.value = edges.value.filter((e) => e.source !== id && e.target !== id);
  selectedId.value = '';
}

function clearAll() {
  nodes.value = [];
  edges.value = [];
  selectedId.value = '';
  pendingEdgeSource.value = '';
}

function onNodeClick(node) {
  // 如果在连线模式中, 完成连线
  if (pendingEdgeSource.value && pendingEdgeSource.value !== node.id) {
    addEdge(pendingEdgeSource.value, node.id);
    pendingEdgeSource.value = '';
    return;
  }
  selectedId.value = node.id;
}

function onCanvasBackgroundClick() {
  selectedId.value = '';
  pendingEdgeSource.value = '';
}

function onPortClick(node) {
  // 进入连线模式: 等待点击目标节点
  pendingEdgeSource.value = node.id;
  selectedId.value = node.id;
}

function addEdge(source, target) {
  if (source === target) {
    toast.warn(t('view.workflow.cannotSelfLoop'));
    return false;
  }
  const exists = edges.value.some((e) => e.source === source && e.target === target);
  if (exists) {
    toast.warn(t('view.workflow.edgeExists'));
    return false;
  }
  edges.value.push({ source, target });
  return true;
}

// ── 属性面板更新 ────────────────────────────────────────────
function updateSelected(key, value) {
  if (!selectedNode.value) return;
  selectedNode.value[key] = value;
}
function updateSelectedConfig(key, value) {
  if (!selectedNode.value) return;
  selectedNode.value.config = { ...selectedNode.value.config, [key]: value };
}
function updateSelectedConfigJson(text) {
  if (!selectedNode.value) return;
  try {
    const parsed = JSON.parse(text);
    selectedNode.value.config = parsed;
  } catch {
    // 解析失败时不覆盖, 保留当前 config; 用户继续编辑
  }
}

// ── 连线路径 (三次贝塞尔) ──────────────────────────────────
const renderedEdges = computed(() => {
  return edges.value.map((e) => {
    const s = nodes.value.find((n) => n.id === e.source);
    const tg = nodes.value.find((n) => n.id === e.target);
    if (!s || !tg) return { ...e, path: '' };
    // 源点: 节点右侧中点; 终点: 节点左侧中点
    const x1 = s.x + 140;
    const y1 = s.y + 18;
    const x2 = tg.x;
    const y2 = tg.y + 18;
    const dx = Math.max(40, Math.abs(x2 - x1) / 2);
    const path = `M ${x1} ${y1} C ${x1 + dx} ${y1}, ${x2 - dx} ${y2}, ${x2} ${y2}`;
    return { ...e, path };
  });
});

// ── 导入 / 导出 / 执行 ─────────────────────────────────────
function onImportClick() {
  if (fileInputRef.value) fileInputRef.value.click();
}

function onImportFileChange(event) {
  const file = event.target.files && event.target.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = () => {
    try {
      const text = String(reader.result || '');
      const dag = JSON.parse(text);
      importDag(dag);
    } catch (e) {
      error.value = t('view.workflow.invalidJson');
      toast.error(t('view.workflow.importFailed', { msg: e.message }));
    }
  };
  reader.onerror = () => {
    toast.error(t('view.workflow.importFailed', { msg: 'read error' }));
  };
  reader.readAsText(file);
  // 重置 value 使同一文件可再次触发 change
  event.target.value = '';
}

function importDag(dag) {
  if (!dag || typeof dag !== 'object') {
    throw new Error('not an object');
  }
  const incomingNodes = Array.isArray(dag.nodes) ? dag.nodes : [];
  const incomingEdges = Array.isArray(dag.edges) ? dag.edges : [];
  // 校验 id 唯一
  const ids = new Set();
  for (const n of incomingNodes) {
    if (!n.id) throw new Error('node missing id');
    if (ids.has(n.id)) {
      toast.warn(t('view.workflow.duplicateId', { id: n.id }));
      return;
    }
    ids.add(n.id);
  }
  nodes.value = incomingNodes.map((n) => ({
    id: String(n.id),
    type: String(n.type || 'tool'),
    label: String(n.label || n.id),
    x: Number(n.x) || 80,
    y: Number(n.y) || 80,
    config: n.config && typeof n.config === 'object' ? n.config : defaultConfig(n.type),
  }));
  edges.value = incomingEdges
    .filter((e) => e && ids.has(e.source) && ids.has(e.target))
    .map((e) => ({ source: String(e.source), target: String(e.target) }));
  selectedId.value = '';
  error.value = '';
  toast.success(t('view.workflow.imported', { nodes: nodes.value.length, edges: edges.value.length }));
}

function exportDag() {
  return {
    nodes: nodes.value.map((n) => ({
      id: n.id,
      type: n.type,
      label: n.label,
      x: n.x,
      y: n.y,
      config: n.config || {},
    })),
    edges: edges.value.map((e) => ({ source: e.source, target: e.target })),
  };
}

function onExportClick() {
  const dag = exportDag();
  const text = JSON.stringify(dag, null, 2);
  // 触发下载
  try {
    const blob = new Blob([text], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `workflow-${Date.now()}.json`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  } catch {
    // 测试环境 (jsdom) 无 URL.createObjectURL, 降级到 toast 显示
  }
  toast.success(t('view.workflow.exported'));
  return text;
}

async function onExecuteClick() {
  if (executing.value || !nodes.value.length) return;
  executing.value = true;
  try {
    const body = JSON.stringify(exportDag());
    const res = await fetch('/api/dag/execute', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(data.error || data.message || `HTTP ${res.status}`);
    }
    toast.success(t('view.workflow.executeOk', { id: data.run_id || data.id || 'n/a' }));
  } catch (e) {
    toast.error(t('view.workflow.executeFailed', { msg: e.message }));
  } finally {
    executing.value = false;
  }
}

// 暴露给测试 (defineExpose 不影响生产)
defineExpose({
  nodes,
  edges,
  selectedId,
  addNode,
  addEdge,
  deleteSelected,
  clearAll,
  exportDag,
  importDag,
  onExecuteClick,
});
</script>

<style scoped>
.workflow-page { display: block; }

.wf-layout {
  display: grid;
  grid-template-columns: 220px 1fr 280px;
  gap: var(--sp-3);
  min-height: 520px;
}

/* ── 左侧节点面板 ─────────────────────────────────────── */
.wf-palette {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r-lg);
  padding: var(--sp-3);
  display: flex;
  flex-direction: column;
  gap: var(--sp-2);
}
.wf-palette__title {
  font-weight: 600;
  font-size: var(--fs-sm);
  color: var(--text);
}
.wf-palette__hint {
  font-size: var(--fs-xs);
  color: var(--text-muted);
  margin-bottom: var(--sp-2);
}
.wf-palette__list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--sp-2);
}
.wf-palette-item {
  display: grid;
  grid-template-columns: 22px 1fr;
  grid-template-rows: auto auto;
  grid-template-areas:
    "icon label"
    "icon desc";
  align-items: center;
  gap: 0 var(--sp-2);
  padding: var(--sp-2) var(--sp-3);
  border: 1px solid var(--border);
  border-radius: var(--r-md);
  background: var(--surface-2);
  cursor: grab;
  user-select: none;
  transition: border-color .15s, background .15s;
}
.wf-palette-item:hover {
  border-color: var(--brand);
  background: var(--brand-soft);
}
.wf-palette-item:active { cursor: grabbing; }
.wf-palette-item__icon { grid-area: icon; color: var(--text-muted); }
.wf-palette-item__label { grid-area: label; font-weight: 600; font-size: var(--fs-sm); }
.wf-palette-item__desc { grid-area: desc; font-size: var(--fs-xs); color: var(--text-muted); }
.wf-palette-item--agent .wf-palette-item__icon { color: var(--brand); }
.wf-palette-item--tool .wf-palette-item__icon { color: var(--success); }
.wf-palette-item--condition .wf-palette-item__icon { color: var(--warn); }
.wf-palette-item--parallel .wf-palette-item__icon { color: var(--info, #38bdf8); }

/* ── 中间画布 ─────────────────────────────────────────── */
.wf-canvas {
  position: relative;
  background:
    linear-gradient(var(--border) 1px, transparent 1px) 0 0 / 24px 24px,
    linear-gradient(90deg, var(--border) 1px, transparent 1px) 0 0 / 24px 24px,
    var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r-lg);
  overflow: auto;
  min-height: 520px;
}
.wf-edges {
  position: absolute;
  top: 0;
  left: 0;
  pointer-events: none;
}
.wf-edge {
  fill: none;
  stroke: var(--brand);
  stroke-width: 2;
  opacity: .7;
}
.wf-node {
  position: absolute;
  width: 140px;
  height: 36px;
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 0 10px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r-md);
  box-shadow: var(--shadow-sm);
  cursor: grab;
  user-select: none;
  z-index: 2;
}
.wf-node:hover { border-color: var(--brand); }
.wf-node.is-selected {
  border-color: var(--brand);
  box-shadow: 0 0 0 2px var(--brand-soft);
}
.wf-node--agent { border-left: 3px solid var(--brand); }
.wf-node--tool { border-left: 3px solid var(--success); }
.wf-node--condition { border-left: 3px solid var(--warn); }
.wf-node--parallel { border-left: 3px solid var(--info, #38bdf8); }
.wf-node__icon { color: var(--text-muted); flex-shrink: 0; }
.wf-node__label {
  font-size: var(--fs-sm);
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  flex: 1;
}
.wf-node__type {
  font-size: var(--fs-xs);
  color: var(--text-muted);
  flex-shrink: 0;
}
.wf-node__port {
  position: absolute;
  right: -5px;
  top: 50%;
  transform: translateY(-50%);
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: var(--brand);
  border: 2px solid var(--surface);
  cursor: crosshair;
  padding: 0;
}
.wf-canvas-empty {
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--sp-2);
  color: var(--text-faint);
  text-align: center;
  pointer-events: none;
}
.wf-canvas-empty__title { font-weight: 600; color: var(--text-muted); }
.wf-canvas-empty__desc { font-size: var(--fs-sm); max-width: 280px; }

/* ── 右侧属性面板 ─────────────────────────────────────── */
.wf-inspector {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r-lg);
  padding: var(--sp-3);
  display: flex;
  flex-direction: column;
  gap: var(--sp-2);
}
.wf-inspector__title {
  font-weight: 600;
  font-size: var(--fs-sm);
  color: var(--text);
  margin-bottom: var(--sp-2);
}
.wf-inspector__empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--sp-2);
  text-align: center;
  color: var(--text-faint);
  padding: var(--sp-5) 0;
}
.wf-inspector__hint { font-size: var(--fs-xs); color: var(--text-muted); max-width: 220px; }
.wf-inspector__form { display: flex; flex-direction: column; gap: var(--sp-2); }
.wf-field { display: flex; flex-direction: column; gap: 4px; }
.wf-field__label { font-size: var(--fs-xs); color: var(--text-muted); font-weight: 600; }
.wf-field__input, .wf-field__textarea {
  font-size: var(--fs-sm);
  padding: 6px 8px;
}
.wf-field__textarea { min-height: 80px; resize: vertical; font-family: ui-monospace, monospace; }

/* ── 底部工具栏 ───────────────────────────────────────── */
.wf-toolbar {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  padding: var(--sp-2) var(--sp-3);
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r-lg);
  margin-top: var(--sp-3);
  flex-wrap: wrap;
}
.wf-toolbar__label { font-weight: 600; font-size: var(--fs-sm); margin-right: var(--sp-2); }
.wf-toolbar__stats {
  font-size: var(--fs-xs);
  color: var(--text-muted);
  margin-right: auto;
}

.wf-file-input { display: none; }

@media (max-width: 1100px) {
  .wf-layout { grid-template-columns: 180px 1fr 240px; }
}
@media (max-width: 860px) {
  .wf-layout { grid-template-columns: 1fr; }
  .wf-canvas { min-height: 360px; }
}
</style>