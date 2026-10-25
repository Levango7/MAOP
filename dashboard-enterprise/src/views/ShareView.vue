<template>
  <div class="share-view">
    <div v-if="loading" class="share-view__state muted">{{ t('common.loading') }}</div>

    <div v-else-if="error" class="share-view__state">
      <h2>{{ t('view.share.unavailable') }}</h2>
      <p class="muted">{{ t('view.share.unavailableHint') }}</p>
    </div>

    <template v-else-if="summary">
      <header class="share-view__head">
        <Badge :tone="tone">{{ summary.status }}</Badge>
        <h1 class="share-view__task">{{ summary.task || t('view.share.untitled') }}</h1>
        <p class="share-view__meta muted">
          {{ summary.agent || '—' }} · {{ summary.duration_ms || 0 }}ms ·
          {{ t('view.share.steps', { n: summary.steps || 0 }) }}
        </p>
      </header>

      <dl class="share-view__facts">
        <dt>{{ t('view.share.success') }}</dt>
        <dd>{{ summary.success === true ? t('view.share.yes') : summary.success === false ? t('view.share.no') : '—' }}</dd>
        <dt>{{ t('view.share.verify') }}</dt>
        <dd>{{ summary.verify_status || '—' }}</dd>
        <dt v-if="summary.verify_summary">{{ t('view.share.verifySummary') }}</dt>
        <dd v-if="summary.verify_summary">{{ summary.verify_summary }}</dd>
        <dt>{{ t('view.share.startedAt') }}</dt>
        <dd>{{ summary.started_at || '—' }}</dd>
      </dl>

      <p class="share-view__note muted">{{ t('view.share.readOnlyNote') }}</p>
    </template>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue';
import { useRoute } from 'vue-router';
import { useApiStore } from '../stores/api.js';
import { useI18n } from '../i18n';
import Badge from '../components/Badge.vue';

const { t } = useI18n();
const api = useApiStore();
const route = useRoute();

const loading = ref(true);
const error = ref(false);
const summary = ref(null);

// 只读摘要：后端 GET /api/share/{token} 返回的就是 summary，
// 没有 events / stdout / stderr —— 这个页面也不需要它们。
const tone = computed(() => {
  const s = summary.value?.status;
  if (s === 'finished') return summary.value?.success ? 'success' : 'danger';
  return 'neutral';
});

onMounted(async () => {
  try {
    const data = await api.get(`/api/share/${encodeURIComponent(route.params.token)}`);
    summary.value = data.share?.summary || null;
    if (!summary.value) error.value = true;
  } catch {
    // 过期与不存在在后端都返回 404，这里统一显示"链接不可用"，
    // 不向访问者区分"曾存在但已过期"和"从未存在"。
    error.value = true;
  } finally {
    loading.value = false;
  }
});
</script>

<style scoped>
.share-view {
  max-width: 720px;
  margin: 0 auto;
  padding: var(--sp-6) var(--sp-4);
}
.share-view__state { text-align: center; padding: var(--sp-6) 0; }
.share-view__head { margin-bottom: var(--sp-4); }
.share-view__task {
  margin: var(--sp-2) 0;
  font-size: 1.4rem;
  line-height: 1.35;
}
.share-view__meta { font-size: var(--fs-sm); }
.share-view__facts {
  display: grid;
  grid-template-columns: max-content 1fr;
  gap: var(--sp-2) var(--sp-4);
  font-size: var(--fs-sm);
}
.share-view__facts dt { font-weight: 600; }
.share-view__facts dd { margin: 0; }
.share-view__note { margin-top: var(--sp-5); font-size: var(--fs-xs); }
</style>
