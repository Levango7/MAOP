<template>
  <div class="skill-market-page">
    <!-- 一号用户实测修复（2026-08-31）：Coming Soon banner 原为常驻 ——
         给人"整个功能待开发"的错觉，实际 marketplace 聚合 + 一键安装
         已实现。仅在数据源未配置/不可达（列表为空）时降级提示。 -->
    <div v-if="!loading && !error && !filteredTools.length" class="coming-soon-banner" role="alert">
      <AppIcon name="info" :size="16" class="coming-soon-banner__icon" />
      <div class="coming-soon-banner__content">
        <span class="coming-soon-banner__title">🔜 {{ t('view.skills.market.comingSoon') }}</span>
        <span class="coming-soon-banner__desc">{{ t('view.skills.market.comingSoonHint') }}</span>
      </div>
      <Badge tone="info">{{ t('view.skills.market.planned') }}</Badge>
    </div>

    <ListPageLayout
      :loading="loading"
      :error="error"
      :empty="!filteredTools.length && !loading && !error"
      :error-title="t('view.skills.market.loadError')"
      :empty-title="t('view.skills.market.noTools')"
      :empty-desc="t('view.skills.market.noToolsHint')"
      :loading-lines="6"
    >
      <template #badges>
        <Badge tone="brand">{{ t('view.skills.market.badge') }}</Badge>
      </template>
      <template #actions>
        <button
          class="btn btn--ghost"
          type="button"
          :disabled="loading"
          @click="load"
        >
          <AppIcon name="refresh" :size="15" />
          <span>{{ t('view.skills.market.refresh') }}</span>
        </button>
      </template>

      <template #content>

        <!-- 搜索 + 分类筛选 -->
        <div class="market-controls">
          <div class="search-box">
            <AppIcon name="search" :size="14" class="search-box__icon" />
            <input
              v-model="query"
              class="search-box__input"
              type="text"
              :placeholder="t('view.skills.market.search')"
            />
          </div>
          <select v-model="category" class="category-select">
            <option value="">{{ t('view.skills.market.allCategories') }}</option>
            <option v-for="c in categories" :key="c" :value="c">{{ c }}</option>
          </select>
        </div>

        <!-- 工具表格 -->
        <DataTable
          :columns="cols"
          :rows="tableRows"
          row-key="uid"
          :empty-text="t('view.skills.market.noTools')"
        >
          <!-- 自定义安装列通过 row-click 在 actions 列里渲染按钮 -->
        </DataTable>

        <!-- 安装操作行(每行右侧按钮) -->
        <div class="install-grid">
          <div
            v-for="row in filteredTools"
            :key="row.uid"
            class="install-row"
          >
            <div class="install-row__info">
              <span class="install-row__name">{{ row.name || row.id }}</span>
              <Badge v-if="row.installed" tone="success">{{ t('view.skills.market.installed') }}</Badge>
            </div>
            <button
              class="btn btn--primary btn--sm"
              type="button"
              :disabled="row.installed || installingUid === row.uid"
              @click="install(row)"
            >
              <AppIcon name="download" :size="13" />
              <span>{{
                installingUid === row.uid
                  ? t('view.skills.market.installing')
                  : row.installed
                    ? t('view.skills.market.alreadyInstalled')
                    : t('view.skills.market.install')
              }}</span>
            </button>
          </div>
        </div>
      </template>
    </ListPageLayout>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue';
import { useApiStore } from '../stores/api.js';
import { useToast } from '../composables/useToast.js';
import { useI18n } from '../i18n/index.js';
import ListPageLayout from '../components/ListPageLayout.vue';
import DataTable from '../components/DataTable.vue';
import Badge from '../components/Badge.vue';
import AppIcon from '../components/AppIcon.vue';

const api = useApiStore();
const toast = useToast();
const { t } = useI18n();

// ── 数据状态 ──
const tools = ref([]);
const loading = ref(true);
const error = ref('');

// ── 筛选状态 ──
const query = ref('');
const category = ref('');

// ── 安装状态 ──
const installingUid = ref('');

// ── 派生 ──
const categories = computed(() => {
  const set = new Set();
  for (const r of tools.value) {
    if (r.category) set.add(r.category);
  }
  return Array.from(set).sort();
});

const filteredTools = computed(() => {
  const q = query.value.trim().toLowerCase();
  const cat = category.value;
  return tools.value.filter((r) => {
    if (cat && r.category !== cat) return false;
    if (!q) return true;
    const name = String(r.name || r.id || '').toLowerCase();
    const desc = String(r.description || '').toLowerCase();
    return name.includes(q) || desc.includes(q);
  });
});

const cols = computed(() => [
  { key: 'name', label: t('view.skills.market.col.name'), width: '30%' },
  { key: 'category', label: t('view.skills.market.col.category'), type: 'badge', width: '15%' },
  { key: 'source', label: t('view.skills.market.col.source'), width: '15%' },
  { key: 'version', label: t('view.skills.market.col.version'), align: 'right', width: '10%' },
  { key: 'installed', label: t('view.skills.market.col.installed'), type: 'badge', width: '15%' },
  { key: 'actions', label: t('view.skills.market.col.actions'), align: 'right', width: '15%' },
]);

const tableRows = computed(() =>
  filteredTools.value.map((r) => ({
    uid: r.uid,
    name: r.name || r.id || '—',
    category: r.category || '—',
    source: r.source || r.registry || 'mcp',
    version: r.version || '—',
    installed: r.installed ? t('view.skills.market.installed') : t('common.off'),
    actions: r.installed ? t('view.skills.market.alreadyInstalled') : t('view.skills.market.install'),
  })),
);

// ── 数据加载 ──
function normalizeTool(raw, i) {
  return {
    uid: raw.uid || raw.id || raw.name || ('tool-' + i),
    id: raw.id || raw.name || '',
    name: raw.name || raw.id || '',
    description: raw.description || '',
    category: raw.category || '',
    source: raw.source || raw.registry || 'mcp',
    version: raw.version || '',
    installed: !!raw.installed,
  };
}

async function load() {
  loading.value = true;
  error.value = '';
  try {
    const d = await api.get('/api/mcp/marketplace/tools');
    const list = Array.isArray(d) ? d : (d.tools || []);
    tools.value = list.map(normalizeTool);
  } catch (e) {
    error.value = e.message || String(e);
    tools.value = [];
  } finally {
    loading.value = false;
  }
}

// ── 一键安装 ──
async function install(row) {
  if (row.installed) return;
  installingUid.value = row.uid;
  try {
    const id = encodeURIComponent(row.id || row.name || row.uid);
    await api.post('/api/mcp/marketplace/tools/' + id + '/install', {});
    row.installed = true;
    toast.success(t('view.skills.market.installOk'));
  } catch (e) {
    toast.error(e.message || t('view.skills.market.installFailed'));
  } finally {
    installingUid.value = '';
  }
}

onMounted(load);
</script>

<style scoped>
.skill-market-page { display: flex; flex-direction: column; gap: var(--sp-3); }

/* ── Coming Soon banner ── */
.coming-soon-banner {
  display: flex;
  align-items: center;
  gap: var(--sp-3);
  padding: var(--sp-3) var(--sp-4);
  background: var(--info-soft, rgba(76, 194, 255, .13));
  border: 1px solid color-mix(in srgb, var(--info) 30%, transparent);
  border-radius: var(--r-md);
  margin-bottom: var(--sp-2);
}
.coming-soon-banner__icon {
  color: var(--info);
  flex-shrink: 0;
}
.coming-soon-banner__content {
  display: flex;
  flex-direction: column;
  gap: 2px;
  flex: 1;
  min-width: 0;
}
.coming-soon-banner__title {
  font-size: 13px;
  font-weight: 600;
  color: var(--info-strong, #79c0ff);
}
.coming-soon-banner__desc {
  font-size: 12px;
  color: var(--text-muted);
  line-height: 1.5;
}

/* ── 控制栏 ── */
.market-controls {
  display: flex;
  gap: var(--sp-3);
  align-items: center;
  margin-bottom: var(--sp-3);
  flex-wrap: wrap;
}
.search-box {
  position: relative;
  display: flex;
  align-items: center;
  flex: 1;
  min-width: 220px;
  max-width: 360px;
}
.search-box__icon {
  position: absolute;
  left: 10px;
  color: var(--text-muted);
  pointer-events: none;
}
.search-box__input {
  width: 100%;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r-md);
  padding: 8px 10px 8px 32px;
  font-size: 13px;
  color: var(--text);
}
.search-box__input:focus { outline: none; border-color: var(--brand); }
.category-select {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r-md);
  padding: 8px 10px;
  font-size: 13px;
  color: var(--text);
  cursor: pointer;
  min-width: 160px;
}
.category-select:focus { outline: none; border-color: var(--brand); }

/* ── 安装操作行 ── */
.install-grid {
  margin-top: var(--sp-3);
  display: flex;
  flex-direction: column;
  gap: var(--sp-2);
}
.install-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--sp-3);
  padding: var(--sp-2) var(--sp-3);
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r-md);
}
.install-row__info {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  min-width: 0;
}
.install-row__name {
  font-size: 13px;
  font-weight: 600;
  color: var(--text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* ── 按钮 ── */
.btn {
  display: inline-flex; align-items: center; gap: 5px;
  background: var(--surface-2); color: var(--text); border: 1px solid var(--border);
  border-radius: var(--r-md); padding: 7px 12px; font-size: 12px; font-weight: 600;
  cursor: pointer; transition: opacity var(--motion) var(--ease);
}
.btn:hover { opacity: .9; }
.btn:disabled { opacity: .5; cursor: not-allowed; }
.btn--primary { background: var(--brand); color: var(--brand-contrast); border: none; }
.btn--ghost { background: transparent; }
.btn--sm { padding: 4px 8px; font-size: 11px; }
</style>