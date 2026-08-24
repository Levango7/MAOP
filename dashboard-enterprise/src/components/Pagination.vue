<template>
  <div class="pagination" :class="{ 'is-loading': loading }">
    <button
      type="button"
      class="pg-btn"
      :disabled="page <= 1 || loading"
      @click="goPage(page - 1)"
    >
      <AppIcon name="chevron-right" :size="14" class="pg-icon-prev" />
      <span>{{ prevLabel }}</span>
    </button>

    <span class="pg-info">{{ pageInfo }}</span>

    <select
      v-if="showSizeSelector"
      v-model.number="selectedLimit"
      class="pg-size"
      :disabled="loading"
      @change="onSizeChange"
    >
      <option v-for="opt in sizeOptions" :key="opt" :value="opt">{{ opt }}</option>
    </select>

    <button
      type="button"
      class="pg-btn"
      :disabled="page >= totalPages || loading"
      @click="goPage(page + 1)"
    >
      <span>{{ nextLabel }}</span>
      <AppIcon name="chevron-right" :size="14" />
    </button>
  </div>
</template>

<script setup>
import { ref, computed, watch } from 'vue';
import AppIcon from './AppIcon.vue';

const props = defineProps({
  limit: { type: Number, default: 20 },
  offset: { type: Number, default: 0 },
  total: { type: Number, default: 0 },
  loading: { type: Boolean, default: false },
  prevLabel: { type: String, default: 'Prev' },
  nextLabel: { type: String, default: 'Next' },
  pageLabel: { type: String, default: 'Page {page} / {total}' },
  showSizeSelector: { type: Boolean, default: true },
  sizeOptions: { type: Array, default: () => [10, 20, 50, 100] },
});

const emit = defineEmits(['change']);

const selectedLimit = ref(props.limit);

watch(() => props.limit, (v) => { selectedLimit.value = v; });

const page = computed(() => Math.floor(props.offset / Math.max(1, props.limit)) + 1);
const totalPages = computed(() => Math.max(1, Math.ceil(props.total / Math.max(1, props.limit))));

const pageInfo = computed(() =>
  props.pageLabel
    .replace('{page}', String(page.value))
    .replace('{total}', String(totalPages.value))
);

function goPage(p) {
  if (p < 1 || p > totalPages.value) return;
  const newOffset = (p - 1) * props.limit;
  emit('change', { limit: props.limit, offset: newOffset });
}

function onSizeChange() {
  emit('change', { limit: selectedLimit.value, offset: 0 });
}
</script>

<style scoped>
.pagination {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--sp-3);
  padding: var(--sp-3) 0 0;
  flex-wrap: wrap;
}

.pagination.is-loading { opacity: .6; }

.pg-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 5px 12px;
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: var(--r-md);
  color: var(--text);
  font-size: var(--fs-sm);
  font-weight: 600;
  cursor: pointer;
  transition: background var(--motion) var(--ease), border-color var(--motion) var(--ease), color var(--motion) var(--ease);
}

.pg-btn:hover:not(:disabled) { border-color: var(--border-strong); }
.pg-btn:disabled { opacity: .4; cursor: not-allowed; }

.pg-icon-prev { transform: rotate(180deg); }

.pg-info {
  font-size: var(--fs-sm);
  color: var(--text-muted);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.pg-size {
  padding: 4px 8px;
  border: 1px solid var(--border);
  border-radius: var(--r-md);
  background: var(--surface);
  color: var(--text);
  font-size: var(--fs-sm);
  cursor: pointer;
}

.pg-size:focus { outline: none; border-color: var(--brand); }
</style>