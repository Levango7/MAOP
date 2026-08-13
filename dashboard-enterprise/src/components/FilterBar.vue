<template>
  <div class="filterbar" :class="{ 'has-results': resultsLabel }">
    <!-- 搜索框: 有 searchKey 时显示 -->
    <div v-if="searchKey" class="filterbar__search">
      <AppIcon name="search" :size="14" class="filterbar__icon" aria-hidden="true" />
      <input
        class="filterbar__input"
        :value="modelValue[searchKey]"
        type="search"
        :placeholder="searchPlaceholder"
        :aria-label="searchPlaceholder || t('a11y.search')"
        @input="set(searchKey, $event.target.value)"
      />
    </div>

    <!-- 下拉选择: 由 schema 定义 -->
    <select
      v-for="f in selectFilters"
      :key="f.key"
      class="filterbar__select"
      :value="modelValue[f.key]"
      :aria-label="f.label"
      @change="set(f.key, $event.target.value)"
    >
      <option value="">{{ f.label }}</option>
      <option v-for="opt in f.options" :key="opt.value" :value="opt.value">{{ opt.label ?? opt.value }}</option>
    </select>

    <!-- 结果计数 -->
    <span v-if="resultsLabel" class="filterbar__meta">{{ resultsLabel }}</span>

    <slot name="extra" />
  </div>
</template>

<script setup>
/**
 * FilterBar — 声明式过滤器行(迭代 C1)。
 *
 * 把每个列表页手写的 filter 沙盒统一成一个声明式组件:
 *   <FilterBar
 *     :model-value="filters"
 *     :schema="[{ key:'level', label:'Level', options:[{value:'info'},{value:'warning'},{value:'critical'}] }]"
 *     search-key="actor"
 *     search-placeholder="Filter by actor…"
 *     :results-label="`${n} rows`"
 *   />
 *
 * - model-value 是可写对象(v-model 语义: 组件内部直接 mutate 该对象,
 *   父组件无需再写 @update:model-value 处理)。
 * - searchKey 指定文本搜索字段; schema 里的选项列表渲染为 <select>。
 * - 纯表现层: 不请求数据, 不持有状态。
 */
import AppIcon from './AppIcon.vue';
import { useI18n } from '../i18n';

const { t } = useI18n();

const props = defineProps({
  modelValue: { type: Object, default: () => ({}) },
  schema: { type: Array, default: () => [] }, // [{ key, label, options: [{value,label?}] }]
  searchKey: { type: String, default: '' },
  searchPlaceholder: { type: String, default: '' },
  resultsLabel: { type: String, default: '' },
});

const selectFilters = schemaWhere((f) => f.options && f.options.length);

function schemaWhere(pred) { return props.schema.filter(pred); }

// 契约: modelValue 是"可写对象", 由父组件持有同一引用并预置空字段。
// 组件只做字段级写入(非替换对象), 因此父组件的响应式状态同步更新,
// 无需 emit 一条 update → 更少样板。ListPageLayout 已遵守此契约
// (通过 :model-value="filters" 传递同一 reactive 对象)。
// eslint-disable-next-line vue/no-mutating-props -- 见上契约说明
function set(key, val) { props.modelValue[key] = val; }
</script>

<style scoped>
.filterbar {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  flex-wrap: wrap;
  padding: var(--sp-2) var(--sp-3);
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r-lg);
}
.filterbar__search { position: relative; display: flex; align-items: center; min-width: 180px; flex: 1; }
.filterbar__icon { position: absolute; left: var(--sp-2); color: var(--text-faint); pointer-events: none; }
.filterbar__input {
  width: 100%;
  padding: var(--sp-1) var(--sp-3) var(--sp-1) 30px;
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: var(--r-md);
  color: var(--text);
  font-size: var(--fs-sm);
  font-family: inherit;
  transition: border-color var(--motion) var(--ease);
}
.filterbar__input:focus { outline: none; border-color: var(--brand); box-shadow: 0 0 0 2px var(--brand-soft); }
.filterbar__input::placeholder { color: var(--text-faint); }
.filterbar__select {
  padding: var(--sp-1) var(--sp-3);
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: var(--r-md);
  color: var(--text);
  font-size: var(--fs-sm);
  font-family: inherit;
  cursor: pointer;
}
.filterbar__select:focus { outline: none; border-color: var(--brand); }
.filterbar__meta { margin-left: auto; font-size: var(--fs-xs); color: var(--text-faint); white-space: nowrap; }
</style>