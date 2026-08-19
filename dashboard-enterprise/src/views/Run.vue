<template>
  <div class="run-view">
    <PageHeader>
      <!-- AI 拆分按钮 (t194, 2026-08-14) -->
      <button
        class="ai-split-btn"
        :title="t('view.run.aiSplitHint')"
        :disabled="splitLoading"
        @click="openSplitDialog"
      >
        <AppIcon name="sparkles" :size="15" :class="{ spinning: splitLoading }" />
        <span>{{ t('view.run.aiSplit') }}</span>
      </button>
      <Segmented
        v-model="tab"
        :options="tabOptions"
        size="md"
        class="run-tabs"
        aria-label="Run mode"
      />
    </PageHeader>

    <!-- 内容区独占剩余高度; PageHeader 保持自然高度,不参与拉伸 -->
    <div class="run-body">
      <!-- keep-alive: 切 Tab 不销毁子组件,保留其内部表单/会话状态
           embedded=true 让子视图隐藏自身 PageHeader,避免双层标题 -->
      <KeepAlive>
        <ControlPanel v-if="tab === 'structured'" embedded />
        <Chat v-else embedded />
      </KeepAlive>
    </div>

    <!-- AI 任务拆分对话框 (t194) -->
    <div
      v-if="splitDialogOpen"
      class="split-overlay"
      role="dialog"
      aria-modal="true"
      :aria-label="t('view.run.splitTitle')"
      @click.self="closeSplitDialog"
      @keydown.escape.prevent="closeSplitDialog"
    >
      <div class="split-dialog">
        <header class="split-dialog__header">
          <div class="split-dialog__title">
            <AppIcon name="sparkles" :size="18" />
            <h2>{{ t('view.run.splitTitle') }}</h2>
          </div>
          <button class="split-dialog__close" :aria-label="t('view.run.close')" @click="closeSplitDialog">
            <AppIcon name="x" :size="16" />
          </button>
        </header>

        <div class="split-dialog__body">
          <p class="split-dialog__desc">{{ t('view.run.splitDescription') }}</p>

          <!-- 输入区 -->
          <div class="split-form">
            <label class="split-form__label">{{ t('view.run.taskDescription') }}</label>
            <textarea
              v-model="splitInput.description"
              class="split-form__textarea"
              :placeholder="t('view.run.taskPlaceholder')"
              rows="3"
              :disabled="splitLoading"
            ></textarea>

            <label class="split-form__label">{{ t('view.run.context') }}</label>
            <textarea
              v-model="splitInput.context"
              class="split-form__textarea"
              :placeholder="t('view.run.contextPlaceholder')"
              rows="2"
              :disabled="splitLoading"
            ></textarea>

            <label class="split-form__label">{{ t('view.run.maxSubtasks') }}</label>
            <input
              v-model.number="splitInput.maxSubtasks"
              type="number"
              min="1"
              max="50"
              class="split-form__input"
              :disabled="splitLoading"
            />
          </div>

          <!-- 错误提示 -->
          <div v-if="splitError" class="split-error">
            <AppIcon name="alert-triangle" :size="14" />
            <span>{{ splitError }}</span>
          </div>

          <!-- 拆分结果 -->
          <div v-if="splitResult" class="split-result">
            <div class="split-result__header">
              <span class="split-result__title">{{ t('view.run.splitResult') }}</span>
              <span class="split-result__stats">
                {{ t('view.run.subtaskCount', { n: splitResult.subtasks.length }) }}
                ·
                {{ t('view.run.edgeCount', { n: splitResult.edges.length }) }}
              </span>
            </div>

            <!-- DAG 可视化 (SVG) -->
            <div v-if="splitResult.subtasks.length > 0" class="split-dag">
              <svg class="split-dag__svg" :width="dagLayout.width" :height="dagLayout.height">
                <!-- 边 -->
                <g class="split-dag__edges">
                  <line
                    v-for="(edge, i) in dagLayout.edges"
                    :key="'e' + i"
                    :x1="edge.x1" :y1="edge.y1"
                    :x2="edge.x2" :y2="edge.y2"
                    class="split-dag__edge"
                  />
                  <!-- 箭头 -->
                  <polygon
                    v-for="(arrow, i) in dagLayout.arrows"
                    :key="'a' + i"
                    :points="arrow.points"
                    class="split-dag__arrow"
                  />
                </g>
                <!-- 节点 -->
                <g class="split-dag__nodes">
                  <g
                    v-for="node in dagLayout.nodes"
                    :key="node.id"
                    :transform="`translate(${node.x}, ${node.y})`"
                    class="split-dag__node"
                  >
                    <circle r="18" class="split-dag__node-circle" />
                    <text text-anchor="middle" dy="4" class="split-dag__node-id">{{ node.id }}</text>
                    <text text-anchor="middle" :y="34" class="split-dag__node-label">{{ node.label }}</text>
                  </g>
                </g>
              </svg>
            </div>

            <!-- 子任务列表 -->
            <div class="split-subtasks">
              <div
                v-for="st in splitResult.subtasks"
                :key="st.id"
                class="split-subtask"
              >
                <div class="split-subtask__head">
                  <span class="split-subtask__id">{{ st.id }}</span>
                  <span class="split-subtask__name">{{ st.name }}</span>
                  <span class="split-subtask__deps">
                    {{ t('view.run.dependsOn') }}:
                    <span v-if="st.depends_on && st.depends_on.length">{{ st.depends_on.join(', ') }}</span>
                    <span v-else class="split-subtask__no-deps">{{ t('view.run.noDeps') }}</span>
                  </span>
                </div>
                <p v-if="st.description" class="split-subtask__desc">{{ st.description }}</p>
              </div>
            </div>
          </div>
        </div>

        <footer class="split-dialog__footer">
          <button class="split-btn split-btn--ghost" :disabled="splitLoading" @click="closeSplitDialog">
            {{ t('view.run.close') }}
          </button>
          <button
            class="split-btn split-btn--primary"
            :disabled="splitLoading || !splitInput.description.trim()"
            @click="runSplit"
          >
            <AppIcon :name="splitLoading ? 'refresh' : 'sparkles'" :size="14" :class="{ spinning: splitLoading }" />
            <span>{{ splitLoading ? t('view.run.splitting') : t('view.run.runSplit') }}</span>
          </button>
        </footer>
      </div>
    </div>
  </div>
</template>

<script setup>
/**
 * Run.vue — 迭代 A (RFC-001): 合并原 /control 与 /chat 为单一入口 /run。
 *
 * 薄壳设计: 两个子视图保持原样,本组件只做 Tab 切换与 URL 同步:
 *   - 初始 tab 从 ?tab=structured|chat 读取(兼容旧 /control、/chat 重定向)
 *   - 切换时写回 query,刷新/分享链接后仍定位在同一模式
 *   - 用户手动把 ?tab 改成未知值时回落到 structured
 *
 * t194 (2026-08-14): 增加 "AI 拆分" 按钮 — 调用 POST /api/dag/auto-split
 * 将自然语言任务描述拆分为子任务 DAG，结果在对话框中可视化展示。
 */
import { ref, computed, watch, reactive } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import PageHeader from '../components/PageHeader.vue';
import Segmented from '../components/Segmented.vue';
import AppIcon from '../components/AppIcon.vue';
import ControlPanel from './ControlPanel.vue';
import Chat from './Chat.vue';
import { useApiStore } from '../stores/api.js';
import { useToast } from '../composables/useToast.js';
import { useI18n } from '../i18n/index.js';

const { t } = useI18n();
const route = useRoute();
const router = useRouter();
const api = useApiStore();
const toast = useToast();

const VALID = new Set(['structured', 'chat']);
const initialTab = VALID.has(route?.query?.tab) ? route.query.tab : 'structured';
const tab = ref(initialTab);

const tabOptions = computed(() => [
  { value: 'structured', label: t('view.run.tabStructured'), icon: 'play' },
  { value: 'chat', label: t('view.run.tabChat'), icon: 'chat' },
]);

// Tab 变化 → URL query 同步
watch(tab, (v) => {
  if (!VALID.has(v)) return;
  if (route?.query && route.query.tab !== v) {
    router.replace({ query: { ...route.query, tab: v } }).catch(() => {});
  }
});

// 外部把 ?tab=chat 打在地址栏时也能响应(如旧 /chat 重定向进来)
watch(
  () => route?.query?.tab,
  (v) => {
    if (VALID.has(v) && v !== tab.value) tab.value = v;
  },
);

// ── AI 任务拆分 (t194) ───────────────────────────────────────────
const splitDialogOpen = ref(false);
const splitLoading = ref(false);
const splitError = ref('');
const splitResult = ref(null);
const splitInput = reactive({
  description: '',
  context: '',
  maxSubtasks: 10,
});

function openSplitDialog() {
  splitError.value = '';
  splitResult.value = null;
  splitDialogOpen.value = true;
}

function closeSplitDialog() {
  if (splitLoading.value) return;
  splitDialogOpen.value = false;
}

async function runSplit() {
  splitLoading.value = true;
  splitError.value = '';
  splitResult.value = null;
  try {
    const resp = await api.post('/api/dag/auto-split', {
      description: splitInput.description,
      context: splitInput.context,
      max_subtasks: splitInput.maxSubtasks,
    });
    if (resp && resp.success) {
      splitResult.value = resp.data;
      toast.success(t('view.run.splitSuccess'));
    } else {
      splitError.value = (resp && (resp.error || resp.detail)) || t('view.run.splitFailed');
      toast.error(splitError.value);
    }
  } catch (e) {
    splitError.value = e.message || t('view.run.splitFailed');
    toast.error(splitError.value);
  } finally {
    splitLoading.value = false;
  }
}

// ── DAG 可视化布局 (简单分层布局) ───────────────────────────────
const dagLayout = computed(() => {
  if (!splitResult.value || !splitResult.value.subtasks.length) {
    return { width: 0, height: 0, nodes: [], edges: [], arrows: [] };
  }
  const subtasks = splitResult.value.subtasks;
  const edges = splitResult.value.edges || [];

  // 拓扑分层
  const idToSubtask = new Map(subtasks.map((s) => [s.id, s]));
  const inDegree = new Map(subtasks.map((s) => [s.id, 0]));
  const adj = new Map(subtasks.map((s) => [s.id, []]));
  for (const e of edges) {
    if (adj.has(e[0]) && inDegree.has(e[1])) {
      adj.get(e[0]).push(e[1]);
      inDegree.set(e[1], inDegree.get(e[1]) + 1);
    }
  }
  // 也纳入 depends_on
  for (const s of subtasks) {
    for (const dep of s.depends_on || []) {
      if (adj.has(dep) && inDegree.has(s.id)) {
        adj.get(dep).push(s.id);
        inDegree.set(s.id, inDegree.get(s.id) + 1);
      }
    }
  }

  // BFS 分层
  const layers = [];
  const remaining = new Set(subtasks.map((s) => s.id));
  const layerOf = new Map();
  let currentLayer = 0;
  while (remaining.size > 0) {
    const layer = [];
    for (const id of remaining) {
      if ((inDegree.get(id) || 0) === 0) layer.push(id);
    }
    if (layer.length === 0) {
      // 有环 — 把剩余全部塞进一层避免死循环
      for (const id of remaining) layer.push(id);
    }
    for (const id of layer) {
      layerOf.set(id, currentLayer);
      remaining.delete(id);
      for (const next of adj.get(id) || []) {
        inDegree.set(next, Math.max(0, (inDegree.get(next) || 0) - 1));
      }
    }
    layers.push(layer);
    currentLayer++;
  }

  // 计算坐标
  const nodeRadius = 18;
  const xGap = 100;
  const yGap = 80;
  const xPadding = 40;
  const yPadding = 40;
  const nodes = [];
  for (let li = 0; li < layers.length; li++) {
    const layer = layers[li];

    for (let i = 0; i < layer.length; i++) {
      const id = layer[i];
      const st = idToSubtask.get(id);
      nodes.push({
        id,
        label: (st && st.name) || id,
        x: xPadding + (xGap * i) + (xGap / 2) + (li % 2 === 0 ? 0 : xGap / 4),
        y: yPadding + (yGap * li),
      });
    }
  }
  const width = Math.max(...nodes.map((n) => n.x)) + xPadding + nodeRadius;
  const height = Math.max(...nodes.map((n) => n.y)) + yPadding + nodeRadius + 20;

  const nodeMap = new Map(nodes.map((n) => [n.id, n]));
  const layoutEdges = [];
  const arrows = [];
  for (const e of edges) {
    const src = nodeMap.get(e[0]);
    const dst = nodeMap.get(e[1]);
    if (!src || !dst) continue;
    layoutEdges.push({ x1: src.x, y1: src.y, x2: dst.x, y2: dst.y });
    // 箭头三角形 (在 dst 节点边缘)
    const dx = dst.x - src.x;
    const dy = dst.y - src.y;
    const len = Math.sqrt(dx * dx + dy * dy) || 1;
    const ux = dx / len;
    const uy = dy / len;
    const tipX = dst.x - ux * nodeRadius;
    const tipY = dst.y - uy * nodeRadius;
    const arrowSize = 6;
    const perpX = -uy * arrowSize;
    const perpY = ux * arrowSize;
    const baseX = tipX - ux * arrowSize;
    const baseY = tipY - uy * arrowSize;
    arrows.push({
      points: `${tipX},${tipY} ${baseX + perpX},${baseY + perpY} ${baseX - perpX},${baseY - perpY}`,
    });
  }

  return { width, height, nodes, edges: layoutEdges, arrows };
});
</script>

<style scoped>
/* 壳必须是 flex 列 + 撑满高度, 否则内嵌的 .chat-page(flex:1 + height:0)
 * 高度链断掉, 聊天区会收缩成左上角一小块(2026-08-12 修复) */
.run-view {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
}
/* 内容区填充剩余高度; 注意: flex:1 只给 .run-body,
 * 绝不能给 .run-view > * —— 否则 PageHeader 也会被拉伸(上轮误伤) */
.run-body {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}
.run-body > * { flex: 1; min-height: 0; }
/* Segmented 内嵌进 PageHeader 操作区,不额外占行 */
.run-tabs { margin-left: auto; }

/* ── AI 拆分按钮 ─────────────────────────────────────────── */
.ai-split-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px;
  border: 1px solid var(--brand);
  border-radius: var(--r-md, 8px);
  background: var(--brand);
  color: var(--brand-contrast, #fff);
  font-size: var(--fs-sm, 13px);
  font-weight: 500;
  cursor: pointer;
  transition: opacity 0.15s, transform 0.1s;
}
.ai-split-btn:hover:not(:disabled) { opacity: 0.9; }
.ai-split-btn:active:not(:disabled) { transform: scale(0.98); }
.ai-split-btn:disabled { opacity: 0.5; cursor: not-allowed; }

/* ── 拆分对话框 ─────────────────────────────────────────── */
.split-overlay {
  position: fixed;
  inset: 0;
  background: var(--overlay-scrim);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
  padding: 16px;
}
.split-dialog {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r-lg, 12px);
  width: min(720px, 100%);
  max-height: 90vh;
  display: flex;
  flex-direction: column;
  box-shadow: 0 12px 40px rgba(0, 0, 0, 0.15);
}
.split-dialog__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 20px;
  border-bottom: 1px solid var(--border);
}
.split-dialog__title {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--text);
}
.split-dialog__title h2 {
  margin: 0;
  font-size: var(--fs-lg, 16px);
  font-weight: 600;
}
.split-dialog__close {
  background: transparent;
  border: none;
  cursor: pointer;
  color: var(--text-muted);
  padding: 4px;
  border-radius: var(--r-sm, 6px);
  display: inline-flex;
}
.split-dialog__close:hover { background: var(--surface-hover, rgba(0,0,0,0.04)); }
.split-dialog__body {
  padding: 20px;
  overflow-y: auto;
  flex: 1;
}
.split-dialog__desc {
  margin: 0 0 16px;
  color: var(--text-muted);
  font-size: var(--fs-sm, 13px);
  line-height: 1.5;
}
.split-form { display: flex; flex-direction: column; gap: 6px; margin-bottom: 16px; }
.split-form__label {
  font-size: var(--fs-sm, 13px);
  font-weight: 500;
  color: var(--text);
}
.split-form__textarea,
.split-form__input {
  border: 1px solid var(--border);
  border-radius: var(--r-md, 8px);
  padding: 8px 10px;
  font-size: var(--fs-sm, 13px);
  font-family: inherit;
  background: var(--surface);
  color: var(--text);
  resize: vertical;
}
.split-form__textarea:focus,
.split-form__input:focus {
  outline: none;
  border-color: var(--brand);
  box-shadow: 0 0 0 2px var(--brand-soft);
}
.split-form__input { width: 80px; }

.split-error {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 12px;
  background: var(--fail-soft);
  border: 1px solid var(--fail);
  border-radius: var(--r-md, 8px);
  color: var(--fail);
  font-size: var(--fs-sm, 13px);
  margin-bottom: 12px;
}

/* ── 拆分结果 ─────────────────────────────────────────── */
.split-result { margin-top: 16px; }
.split-result__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
  padding-bottom: 8px;
  border-bottom: 1px solid var(--border);
}
.split-result__title { font-weight: 600; color: var(--text); }
.split-result__stats { font-size: var(--fs-sm, 13px); color: var(--text-muted); }

/* DAG SVG */
.split-dag {
  background: var(--surface-alt, rgba(0,0,0,0.02));
  border: 1px solid var(--border);
  border-radius: var(--r-md, 8px);
  padding: 12px;
  overflow: auto;
  margin-bottom: 16px;
}
.split-dag__svg { display: block; }
.split-dag__edge {
  stroke: var(--text-muted);
  stroke-width: 1.5;
  fill: none;
}
.split-dag__arrow { fill: var(--text-muted); }
.split-dag__node-circle {
  fill: var(--surface);
  stroke: var(--brand);
  stroke-width: 2;
}
.split-dag__node-id {
  font-size: 10px;
  font-weight: 600;
  fill: var(--text);
}
.split-dag__node-label {
  font-size: 10px;
  fill: var(--text-muted);
}

/* 子任务列表 */
.split-subtasks { display: flex; flex-direction: column; gap: 8px; }
.split-subtask {
  padding: 10px 12px;
  background: var(--surface-alt, rgba(0,0,0,0.02));
  border: 1px solid var(--border);
  border-radius: var(--r-md, 8px);
}
.split-subtask__head {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.split-subtask__id {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 28px;
  height: 22px;
  padding: 0 6px;
  background: var(--brand);
  color: var(--brand-contrast, #fff);
  border-radius: var(--r-sm, 6px);
  font-size: 11px;
  font-weight: 600;
}
.split-subtask__name { font-weight: 500; color: var(--text); }
.split-subtask__deps {
  margin-left: auto;
  font-size: var(--fs-sm, 13px);
  color: var(--text-muted);
}
.split-subtask__no-deps { font-style: italic; opacity: 0.7; }
.split-subtask__desc {
  margin: 6px 0 0;
  font-size: var(--fs-sm, 13px);
  color: var(--text-muted);
  line-height: 1.5;
}

/* ── 对话框底栏 ─────────────────────────────────────────── */
.split-dialog__footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding: 12px 20px;
  border-top: 1px solid var(--border);
}
.split-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 8px 16px;
  border-radius: var(--r-md, 8px);
  font-size: var(--fs-sm, 13px);
  font-weight: 500;
  cursor: pointer;
  border: 1px solid transparent;
  transition: opacity 0.15s;
}
.split-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.split-btn--ghost {
  background: transparent;
  border-color: var(--border);
  color: var(--text);
}
.split-btn--primary {
  background: var(--brand);
  color: var(--brand-contrast, #fff);
}
.split-btn--primary:hover:not(:disabled) { opacity: 0.9; }

/* 旋转动画 (loading 状态) */
.spinning {
  animation: spin 1s linear infinite;
}
@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}
</style>
