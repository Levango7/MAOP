<template>
  <Card :title="t('view.agentRecommend.title')" icon="sparkles" margin-bottom="var(--sp-4)">
    <p class="recommend__subtitle muted">{{ t('view.agentRecommend.subtitle') }}</p>

    <form class="recommend__form" @submit.prevent="submit">
      <label class="recommend__field">
        <span class="recommend__label">{{ t('view.agentRecommend.form.taskType') }}</span>
        <input
          v-model="taskType"
          type="text"
          class="input"
          :placeholder="t('view.agentRecommend.form.taskTypePlaceholder')"
        />
      </label>

      <label class="recommend__field">
        <span class="recommend__label">{{ t('view.agentRecommend.form.capabilities') }}</span>
        <input
          v-model="capabilities"
          type="text"
          class="input"
          placeholder="code_generation, tool_use"
        />
        <span class="recommend__hint muted">{{ t('view.agentRecommend.form.hint') }}</span>
      </label>

      <label class="recommend__check">
        <input v-model="preferFree" type="checkbox" />
        <span>{{ t('view.agentRecommend.form.preferFree') }}</span>
      </label>

      <button class="btn-primary" type="submit" :disabled="loading">
        <AppIcon v-if="loading" name="refresh" :size="15" class="is-spinning" />
        <span>{{ t('view.agentRecommend.form.submit') }}</span>
      </button>
    </form>

    <p v-if="error" class="recommend__error">{{ error }}</p>

    <!-- 未查询过 -->
    <EmptyState
      v-if="!loading && scored === null && !error"
      icon="sparkles"
      :title="t('view.agentRecommend.result.empty')"
      :description="t('view.agentRecommend.result.emptyHint')"
    />

    <!-- 查过了，但后端没有任何使用历史（scored=false）—— 这不是错误 -->
    <EmptyState
      v-else-if="!loading && scored === false && !error"
      icon="clock"
      :title="t('view.agentRecommend.result.noHistory')"
      :description="t('view.agentRecommend.result.noHistoryHint')"
    />

    <!-- 有结果 -->
    <div v-else-if="results.length" class="recommend__list">
      <div v-for="r in results" :key="r.agent_name" class="recommend__item">
        <div class="recommend__item__head">
          <span class="recommend__item__name">{{ r.agent_name }}</span>
          <Badge tone="brand">{{ (r.score * 100).toFixed(0) }}%</Badge>
        </div>
        <p v-if="r.reason" class="recommend__item__reason muted">{{ r.reason }}</p>
        <span class="recommend__item__cost muted">
          {{ t('view.agentRecommend.result.cost') }}: {{ r.estimated_cost }}
        </span>
      </div>
    </div>
  </Card>
</template>

<script setup>
import { ref } from 'vue';
import { useApiStore } from '../stores/api.js';
import { useI18n } from '../i18n';
import Card from '../components/Card.vue';
import Badge from '../components/Badge.vue';
import EmptyState from '../components/EmptyState.vue';
import AppIcon from '../components/AppIcon.vue';

const { t } = useI18n();
const api = useApiStore();

const taskType = ref('');
const capabilities = ref('');
const preferFree = ref(true);
const loading = ref(false);
const results = ref([]);
// null = 还没查过；false = 查过但后端无历史（scored=false）；true = 有结果
const scored = ref(null);
const error = ref('');

async function submit() {
  loading.value = true;
  error.value = '';
  try {
    const params = new URLSearchParams();
    if (taskType.value.trim()) params.set('task_type', taskType.value.trim());
    if (capabilities.value.trim()) params.set('capability', capabilities.value.trim());
    params.set('prefer_free', String(preferFree.value));
    const data = await api.get(`/api/agent-catalog/recommend?${params.toString()}`);
    results.value = data.agents || [];
    scored.value = data.scored;
  } catch {
    // 具体异常不进 UI：对用户而言"推荐失败"就是可执行的信息，底层堆栈属于日志。
    error.value = t('view.agentRecommend.result.error');
  } finally {
    loading.value = false;
  }
}
</script>

<style scoped>
.recommend__subtitle {
  margin: 0 0 var(--sp-4);
}

.recommend__form {
  display: flex;
  flex-direction: column;
  gap: var(--sp-3);
  margin-bottom: var(--sp-4);
}

.recommend__field {
  display: flex;
  flex-direction: column;
  gap: var(--sp-2);
}

.recommend__label {
  font-weight: 600;
  font-size: 13px;
}

.recommend__hint {
  font-size: 12px;
}

.recommend__check {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  font-size: 13px;
}

.recommend__error {
  color: var(--danger, #dc2626);
  font-size: 13px;
}

.recommend__list {
  display: flex;
  flex-direction: column;
  gap: var(--sp-3);
}

.recommend__item {
  padding: var(--sp-3);
  border: 1px solid var(--border, #e5e7eb);
  border-radius: 8px;
}

.recommend__item__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--sp-2);
}

.recommend__item__name {
  font-weight: 600;
}

.recommend__item__reason {
  margin: var(--sp-2) 0 0;
  font-size: 13px;
}

.recommend__item__cost {
  display: block;
  margin-top: var(--sp-2);
  font-size: 12px;
}
</style>
