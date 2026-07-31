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
            @click="sortable && col.sortable !== false ? toggleSort(col.key) : null"
          >
            <span class="dt__th">
              {{ col.label }}
              <AppIcon v-if="sortable && col.sortable !== false" :name="sortKey === col.key ? (sortDir === 'asc' ? 'chevrondown' : 'chevrondown') : 'chevrondown'" :size="12" class="dt__sort" :class="{ 'is-active': sortKey === col.key }" />
            </span>
          </th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="(row, i) in sortedRows" :key="rowKey ? row[rowKey] : i">
          <td v-for="col in cols" :key="col.key" :style="{ textAlign: col.align || 'left' }">
            <Badge v-if="col.type === 'badge'" :tone="toneFor(row[col.key])">{{ row[col.key] }}</Badge>
            <span v-else-if="col.type === 'num'" class="dt__num">{{ row[col.key] }}</span>
            <span v-else-if="col.type === 'time'" class="dt__time">{{ formatRel(row[col.key]) }}</span>
            <span v-else class="dt__text">{{ row[col.key] }}</span>
          </td>
        </tr>
        <tr v-if="!loading && !sortedRows.length">
          <td :colspan="cols.length" class="dt__empty">{{ emptyText }}</td>
        </tr>
      </tbody>
    </table>
    <div v-if="loading" class="dt__skeleton">
      <div v-for="n in 4" :key="n" class="dt__sk-row" :style="{ gridTemplateColumns: 'repeat(' + cols.length + ', 1fr)' }">
        <Skeleton v-for="c in cols.length" :key="c" height="12px" />
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue';
import AppIcon from './AppIcon.vue';
import Badge from './Badge.vue';
import Skeleton from './Skeleton.vue';

const props = defineProps({
  columns: { type: Array, default: null }, // [{ key, label, align?, type?, width? }]
  rows: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
  rowKey: { type: String, default: 'id' },
  emptyText: { type: String, default: 'No data' },
  sortable: { type: Boolean, default: false },
  compact: { type: Boolean, default: false },
});

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
function formatRel(ts) {
  if (ts === null || ts === undefined) return '—';
  const d = new Date(typeof ts === 'number' ? ts : String(ts));
  if (isNaN(d.getTime())) return String(ts);
  const diff = Math.max(0, Math.floor((Date.now() - d.getTime()) / 1000));
  if (diff < 60) return diff + 's ago';
  if (diff < 3600) return Math.floor(diff / 60) + 'm ago';
  if (diff < 86400) return Math.floor(diff / 3600) + 'h ago';
  return Math.floor(diff / 86400) + 'd ago';
}
</script>

<style scoped>
.dt-wrap { position: relative; width: 100%; overflow-x: auto; }
.dt { width: 100%; border-collapse: collapse; font-size: var(--fs-sm); }
.dt thead th {
  position: sticky; top: 0; z-index: 1;
  background: var(--surface-2);
  color: var(--text-muted);
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: .03em;
  font-size: var(--fs-xs);
  padding: var(--sp-2) var(--sp-3);
  border-bottom: 1px solid var(--border);
  white-space: nowrap;
  user-select: none;
}
.dt th.sortable { cursor: pointer; }
.dt th.sortable:hover { color: var(--text); }
.dt__th { display: inline-flex; align-items: center; gap: 4px; }
.dt__sort { opacity: .35; transition: opacity var(--motion) var(--ease), transform var(--motion) var(--ease); }
.dt__sort.is-active { opacity: 1; color: var(--brand-strong); }
.dt__sort.is-active { transform: rotate(180deg); }
.dt tbody td { padding: var(--sp-3); border-bottom: 1px solid var(--border); color: var(--text); vertical-align: middle; }
.dt--compact tbody td { padding: var(--sp-2) var(--sp-3); }
.dt tbody tr { transition: background var(--motion) var(--ease); }
.dt tbody tr:hover { background: var(--surface-2); }
.dt__num { font-variant-numeric: tabular-nums; font-family: var(--font-mono); }
.dt__time { color: var(--text-muted); white-space: nowrap; }
.dt__text { overflow: hidden; text-overflow: ellipsis; }
.dt__empty { text-align: center; color: var(--text-faint); padding: var(--sp-6); }
.dt__skeleton { padding: var(--sp-3); display: flex; flex-direction: column; gap: var(--sp-3); }
.dt__sk-row { display: grid; gap: var(--sp-3); }
</style>
