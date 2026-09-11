<template>
  <div class="dt-wrap" :class="{ 'dt--loading': loading }">
    <table class="dt" :class="{ 'dt--compact': compact }">
      <thead>
        <tr>
          <th
            v-for="col in cols"
            :key="col.key"
            :style="{ textAlign: col.align || 'left', width: col.width || null }"
            :class="{ sortable: sortable && col.sortable !== false }"
            :tabindex="sortable && col.sortable !== false ? 0 : undefined"
            :role="sortable && col.sortable !== false ? 'button' : undefined"
            :aria-sort="sortKey === col.key ? (sortDir === 'asc' ? 'ascending' : 'descending') : (sortable && col.sortable !== false ? 'none' : undefined)"
            :aria-label="sortable && col.sortable !== false ? sortLabel(col.label) : undefined"
            @click="sortable && col.sortable !== false ? toggleSort(col.key) : null"
            @keydown.enter.prevent="sortable && col.sortable !== false ? toggleSort(col.key) : null"
            @keydown.space.prevent="sortable && col.sortable !== false ? toggleSort(col.key) : null"
          >
            <span class="dt__th">
              {{ col.label }}
              <AppIcon v-if="sortable && col.sortable !== false" name="chevrondown" :size="12" class="dt__sort" :class="{ 'is-active': sortKey === col.key, 'is-desc': sortKey === col.key && sortDir === 'desc' }" aria-hidden="true" />
            </span>
          </th>
        </tr>
      </thead>
        <tbody>
        <tr v-for="(row, i) in pagedData" :key="rowKey ? row[rowKey] : i" :class="{ 'is-clickable': clickable }" @click="onRowClick(row)">
          <td v-for="col in cols" :key="col.key" :style="{ textAlign: col.align || 'left' }">
            <Badge v-if="col.type === 'badge'" :tone="badgeTone(col, row)">{{ row[col.key] }}</Badge>
            <span v-else-if="col.type === 'bool-icon'" class="dt__bool" :class="row[col.key] ? 'is-true' : 'is-false'" :aria-label="row[col.key] ? t('a11y.yes') : t('a11y.no')">
              <AppIcon :name="row[col.key] ? 'check' : 'x'" :size="13" aria-hidden="true" />
            </span>
            <span v-else-if="col.type === 'num'" class="dt__num">{{ row[col.key] }}</span>
            <span v-else-if="col.type === 'time'" class="dt__time">{{ formatRel(row[col.key]) }}</span>
            <span v-else class="dt__text">{{ row[col.key] }}</span>
          </td>
        </tr>
        <tr v-if="!loading && !sortedRows.length">
          <td :colspan="cols.length" class="dt__empty">{{ emptyText || t('common.noData') }}</td>
        </tr>
      </tbody>
    </table>
    <div v-if="loading" class="dt__skeleton">
      <div v-for="n in 4" :key="n" class="dt__sk-row" :style="{ gridTemplateColumns: 'repeat(' + cols.length + ', 1fr)' }">
        <Skeleton v-for="c in cols.length" :key="c" height="12px" />
      </div>
    </div>
    <!-- 分页控件: pageSize > 0 且多页时显示。
         ‹/› 为通用排版符号(非英文), 页码格式 "当前页 / 总页数"。
         上一页/下一页 aria-label 复用已有 i18n key, 避免新增 key。 -->
    <div v-if="showPager" class="dt__pager" role="navigation">
      <button
        class="dt__pager-btn"
        type="button"
        :disabled="currentPage === 1"
        :aria-label="t('view.tasks.prevPage')"
        @click="goPrev"
      >‹</button>
      <span class="dt__pager-info">{{ currentPage }} / {{ totalPages }}</span>
      <button
        class="dt__pager-btn"
        type="button"
        :disabled="currentPage === totalPages"
        :aria-label="t('action.next')"
        @click="goNext"
      >›</button>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue';
import AppIcon from './AppIcon.vue';
import Badge from './Badge.vue';
import Skeleton from './Skeleton.vue';
import { useI18n } from '../i18n';

const props = defineProps({
  columns: { type: Array, default: null }, // [{ key, label, align?, type?, width?, tone? }]
  rows: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
  rowKey: { type: String, default: 'id' },
  emptyText: { type: String, default: '' },
  sortable: { type: Boolean, default: false },
  compact: { type: Boolean, default: false },
  clickable: { type: Boolean, default: false },
  // 分页: pageSize <= 0 时不分页(向后兼容, 显示全部数据)
  pageSize: { type: Number, default: 0 },
});

const emit = defineEmits(['row-click']);

const { t } = useI18n();

function sortLabel(colLabel) {
  return t('a11y.sortBy', { column: colLabel });
}

// Derive columns from the first row when not provided
const cols = computed(() => {
  if (props.columns && props.columns.length) return props.columns;
  const first = props.rows && props.rows[0];
  if (!first || typeof first !== 'object') return [];
  return Object.keys(first)
    .filter((k) => {
      const v = first[k];
      return v === null || typeof v !== 'object';
    })
    .slice(0, 6)
    .map((k) => ({ key: k, label: labelize(k) }));
});

const sortKey = ref('');
const sortDir = ref('asc');
function toggleSort(key) {
  if (sortKey.value === key) sortDir.value = sortDir.value === 'asc' ? 'desc' : 'asc';
  else { sortKey.value = key; sortDir.value = 'asc'; }
}
const sortedRows = computed(() => {
  if (!sortKey.value || !props.sortable) return props.rows;
  const arr = [...props.rows];
  arr.sort((a, b) => {
    const x = a[sortKey.value], y = b[sortKey.value];
    if (x === null || x === undefined) return 1; if (y === null || y === undefined) return -1;
    if (typeof x === 'number' && typeof y === 'number') return sortDir.value === 'asc' ? x - y : y - x;
    return sortDir.value === 'asc'
      ? String(x).localeCompare(String(y))
      : String(y).localeCompare(String(x));
  });
  return arr;
});

// ── 分页 (迭代 C: 大数据集客户端分页) ───────────────────────────────
// pageSize <= 0 时不分页, 显示全部数据(向后兼容)。
// currentPage 越界(数据减少/筛选后)自动回正, 避免空页。
const currentPage = ref(1);
const total = computed(() => sortedRows.value.length);
const totalPages = computed(() => props.pageSize > 0 ? Math.max(1, Math.ceil(total.value / props.pageSize)) : 1);
const showPager = computed(() => props.pageSize > 0 && totalPages.value > 1);

// 数据减少时当前页可能越界 → 自动回正到末页
watch(totalPages, (tp) => {
  if (currentPage.value > tp) currentPage.value = tp;
});

const pagedData = computed(() => {
  if (props.pageSize <= 0) return sortedRows.value;
  const start = (currentPage.value - 1) * props.pageSize;
  return sortedRows.value.slice(start, start + props.pageSize);
});

function goPrev() { if (currentPage.value > 1) currentPage.value -= 1; }
function goNext() { if (currentPage.value < totalPages.value) currentPage.value += 1; }

function labelize(k) {
  return k.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}
function toneFor(v) {
  const s = String(v === null || v === undefined ? '' : v).toLowerCase();
  if (/(success|ok|done|completed|healthy|pass|green|up|online)/.test(s)) return 'success';
  if (/(fail|error|err|down|dead|red|critical|exception|offline)/.test(s)) return 'fail';
  if (/(warn|warning|pending|yellow|degraded|slow|throttl)/.test(s)) return 'warn';
  if (/(info|running|active|blue|idle)/.test(s)) return 'info';
  return 'neutral';
}
function badgeTone(col, row) {
  if (typeof col.tone === 'function') return col.tone(row[col.key], row);
  if (col.tone) return col.tone;
  return toneFor(row[col.key]);
}
function onRowClick(row) { emit('row-click', row); }
function formatRel(ts) {
  if (ts === null || ts === undefined) return '—';
  const d = new Date(typeof ts === 'number' ? ts : String(ts));
  if (isNaN(d.getTime())) return String(ts);
  const diff = Math.max(0, Math.floor((Date.now() - d.getTime()) / 1000));
  if (diff < 60) return t('common.secondsAgo', { n: diff });
  if (diff < 3600) return t('common.minutesAgo', { n: Math.floor(diff / 60) });
  if (diff < 86400) return t('common.hoursAgo', { n: Math.floor(diff / 3600) });
  return t('common.daysAgo', { n: Math.floor(diff / 86400) });
}
</script>

<style scoped>
.dt-wrap { position: relative; width: 100%; overflow-x: auto; }
.dt { width: 100%; border-collapse: collapse; font-size: var(--fs-sm); }
.dt thead th {
  position: sticky; top: 0; z-index: var(--z-raised);
  background: var(--surface-2);
  color: var(--text-muted);
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: .03em;
  font-size: var(--fs-xs);
  padding: var(--sp-3) var(--sp-3);
  border-bottom: 1px solid var(--border-strong);
  white-space: nowrap;
  user-select: none;
  transition: color var(--motion-fast) var(--ease);
}
.dt th.sortable { cursor: pointer; }
.dt th.sortable:hover { color: var(--brand-strong); }
.dt__th { display: inline-flex; align-items: center; gap: var(--sp-1); }
.dt__sort { opacity: .3; transition: opacity var(--motion) var(--ease), transform var(--motion) var(--ease); }
.dt__sort.is-active { opacity: 1; color: var(--brand-strong); transform: rotate(180deg); }
.dt__sort.is-active.is-desc { transform: rotate(0deg); }
.dt tbody td { padding: var(--sp-3); border-bottom: 1px solid var(--border-subtle); color: var(--text); vertical-align: middle; }
.dt--compact tbody td { padding: var(--sp-2) var(--sp-3); }
.dt tbody tr { transition: background var(--motion) var(--ease); }
.dt tbody tr:hover { background: var(--surface-2); }
.dt tbody tr.is-clickable { cursor: pointer; }
.dt tbody tr:last-child td { border-bottom: none; }
.dt__bool { display: inline-flex; align-items: center; }
.dt__bool.is-true .app-icon { color: var(--success); }
.dt__bool.is-false .app-icon { color: var(--fail); }
.dt__num { font-variant-numeric: tabular-nums; font-family: var(--font-mono); }
.dt__time { color: var(--text-muted); white-space: nowrap; }
.dt__text { overflow: hidden; text-overflow: ellipsis; }
.dt__empty { text-align: center; color: var(--text-faint); padding: var(--sp-6); }
.dt__skeleton { padding: var(--sp-3); display: flex; flex-direction: column; gap: var(--sp-3); }
.dt__sk-row { display: grid; gap: var(--sp-3); }
/* ── 分页控件 ── */
.dt__pager {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--sp-2);
  padding: var(--sp-3);
  border-top: 1px solid var(--border-subtle);
}
.dt__pager-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  /* 28px 为分页按钮视觉规格固定值 */
  width: 28px;
  height: 28px;
  border: 1px solid var(--border);
  border-radius: var(--r-md);
  background: var(--surface);
  color: var(--text-muted);
  font-size: var(--fs-md);
  cursor: pointer;
  transition: background var(--motion) var(--ease), color var(--motion) var(--ease), border-color var(--motion) var(--ease);
}
.dt__pager-btn:hover:not(:disabled) {
  background: var(--surface-2);
  color: var(--text);
  border-color: var(--border-strong);
}
.dt__pager-btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}
.dt__pager-info {
  font-size: var(--fs-sm);
  color: var(--text-muted);
  font-variant-numeric: tabular-nums;
  /* min-width 60px 为分页信息区视觉规格固定值 */
  min-width: 60px;
  text-align: center;
}
</style>
