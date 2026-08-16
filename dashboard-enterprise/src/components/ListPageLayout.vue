<template>
  <div class="list-page">
    <PageHeader>
      <template v-if="$slots.badges" #badges><slot name="badges" /></template>
      <slot name="actions" />
    </PageHeader>

    <!-- 统计条(可选) -->
    <div v-if="$slots.stats" class="stat-row">
      <slot name="stats" />
    </div>

    <!-- 过滤器(可选) -->
    <FilterBar
      v-if="filterSchema?.length || searchKey"
      :model-value="filters"
      :schema="filterSchema"
      :search-key="searchKey"
      :search-placeholder="searchPlaceholder"
      :results-label="resultsLabel"
      class="filterbar-embed"
    >
      <template v-if="$slots.filterExtra" #extra><slot name="filterExtra" /></template>
    </FilterBar>

    <!-- 三态主体: 错误 → 加载 → 空态(自定义/兜底) → 表格 -->
    <div v-if="error">
      <slot name="error" :error="error">
        <EmptyState icon="alert-triangle" tone="fail" :title="errorTitle" :description="error" />
      </slot>
    </div>
    <div v-else-if="loading">
      <slot name="loading">
        <Skeleton :lines="loadingLines" block />
      </slot>
    </div>
    <div v-else-if="empty && $slots.itemsEmpty">
      <slot name="itemsEmpty" />
    </div>
    <EmptyState
      v-else-if="empty"
      icon="inbox"
      :title="emptyTitle"
      :description="emptyDesc"
    />
    <div v-else>
      <!-- 作用域插槽: 把内部 filters 暴露给内容, 供视图做过滤 -->
      <slot name="content" :filters="filters" />
    </div>
  </div>
</template>

<script setup>
/**
 * ListPageLayout — 统一列表页骨架(迭代 C1)。
 *
 * 收敛 22 个视图各自手写的 页头/统计/过滤/三态 结构, 提供唯一模板:
 *   <ListPageLayout
 *     :loading="loading" :error="error" :empty="!rows.length"
 *     :filter-schema="[...]" search-key="query"
 *     error-title="Failed" empty-title="No data">
 *     <template #content><DataTable :rows="rows" ... /></template>
 *   </ListPageLayout>
 *
 * 规则(写入 style-guide):
 *   - 加载/错误/空态 → 交给本组件的三态, 禁止在视图里再手写
 *   - 过滤器 → 用 filterSchema 声明, 不再手写 select/input
 *   - 内容 → 放进 #content; 特殊空态可用 #itemsEmpty 覆盖
 */
import { ref, computed } from 'vue';
import PageHeader from './PageHeader.vue';
import FilterBar from './FilterBar.vue';
import Skeleton from './Skeleton.vue';
import EmptyState from './EmptyState.vue';

const props = defineProps({
  loading: { type: Boolean, default: false },
  error: { type: String, default: '' },
  empty: { type: Boolean, default: false },
  filterSchema: { type: Array, default: () => [] },
  searchKey: { type: String, default: '' },
  searchPlaceholder: { type: String, default: '' },
  resultsLabel: { type: String, default: '' },
  errorTitle: { type: String, default: '' },
  emptyTitle: { type: String, default: '' },
  emptyDesc: { type: String, default: '' },
  loadingLines: { type: Number, default: 6 },
  // 可选: 父视图用 v-model:filters 持有同一个对象, 以便在 props 层算
  // empty/results-label (过滤发生在视图, 组件只负责收集)
  filters: { type: Object, default: null },
});

// 若无父级 v-model, 内部自持一份(粗粒度过滤场景)
const internalFilters = ref({});
const filters = computed(() => props.filters || internalFilters.value);
const empty = computed(() => props.empty);
</script>

<style scoped>
.list-page { display: flex; flex-direction: column; gap: var(--sp-4); }
.filterbar-embed { margin-bottom: 0; }
</style>