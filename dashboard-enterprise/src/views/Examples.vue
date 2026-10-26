<template>
  <div class="examples">
    <PageHeader
      icon="beaker"
      :title="t('view.examples.title')"
      :subtitle="t('view.examples.subtitle')"
    />

    <div class="ex-filters">
      <input
        v-model="query"
        type="text"
        class="input"
        :placeholder="t('view.examples.searchPlaceholder')"
      />
      <select v-model="category" class="input">
        <option value="">{{ t('view.examples.allCategories') }}</option>
        <option v-for="c in EXAMPLE_CATEGORIES" :key="c" :value="c">
          {{ t(`view.examples.category.${c}`) }}
        </option>
      </select>
    </div>

    <p v-if="notice" class="ex-notice">{{ notice }}</p>

    <p v-if="!filtered.length" class="muted">{{ t('view.examples.empty') }}</p>

    <div v-else class="ex-grid">
      <Card v-for="item in filtered" :key="item.id" :title="t(`view.examples.item.${item.id}.title`)" icon="beaker">
        <p class="ex-why muted">{{ t(`view.examples.item.${item.id}.why`) }}</p>

        <pre class="ex-cmd">{{ command(item) }}</pre>

        <template #actions>
          <button class="act-btn small" @click="copy(item)">
            <AppIcon name="clipboard" :size="12" />
            {{ t('view.examples.copy') }}
          </button>
        </template>
      </Card>
    </div>

    <p class="ex-note muted">{{ t('view.examples.note') }}</p>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue';
import { useI18n } from '../i18n';
import PageHeader from '../components/PageHeader.vue';
import Card from '../components/Card.vue';
import AppIcon from '../components/AppIcon.vue';
import { EXAMPLES, EXAMPLE_CATEGORIES } from '../data/examples.js';

const { t } = useI18n();

const query = ref('');
const category = ref('');
const notice = ref('');

// 命令形态与 README 一致：maop run --task "<prompt>"
function command(item) {
  return `maop run --task "${item.task}"`;
}

const filtered = computed(() => {
  const q = query.value.trim().toLowerCase();
  return EXAMPLES.filter((item) => {
    if (category.value && item.category !== category.value) return false;
    if (!q) return true;
    // 同时搜 id / task / 本地化标题，用户可能记得任一种写法
    const title = t(`view.examples.item.${item.id}.title`).toLowerCase();
    return (
      item.id.includes(q) || item.task.toLowerCase().includes(q) || title.includes(q)
    );
  });
});

async function copy(item) {
  try {
    await navigator.clipboard.writeText(command(item));
    notice.value = t('view.examples.copied');
  } catch {
    // 非 HTTPS / 无剪贴板权限时不可用；命令就在卡片上，用户可手动选。
    notice.value = t('view.examples.copyFailed');
  }
}
</script>

<style scoped>
.ex-filters {
  display: flex;
  flex-wrap: wrap;
  gap: var(--sp-2);
  margin-bottom: var(--sp-4);
}
.ex-notice {
  color: var(--success, #16a34a);
  font-size: 13px;
}
.ex-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: var(--sp-3);
}
.ex-why {
  margin: 0 0 var(--sp-2);
  font-size: var(--fs-sm);
}
.ex-cmd {
  margin: 0;
  padding: var(--sp-2);
  border: 1px solid var(--border);
  border-radius: var(--r-sm);
  background: var(--bg);
  font-family: var(--font-mono, monospace);
  font-size: var(--fs-xs);
  white-space: pre-wrap;
  word-break: break-word;
}
.ex-note {
  margin-top: var(--sp-4);
  font-size: var(--fs-xs);
}
</style>
