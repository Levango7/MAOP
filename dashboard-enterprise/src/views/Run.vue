<template>
  <div class="run-view">
    <PageHeader>
      <Segmented
        v-model="tab"
        :options="tabOptions"
        size="md"
        class="run-tabs"
        aria-label="Run mode"
      />
    </PageHeader>

    <!-- keep-alive: 切 Tab 不销毁子组件,保留其内部表单/会话状态
         embedded=true 让子视图隐藏自身 PageHeader,避免双层标题 -->
    <KeepAlive>
      <ControlPanel v-if="tab === 'structured'" embedded />
      <Chat v-else embedded />
    </KeepAlive>
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
 */
import { ref, computed, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import PageHeader from '../components/PageHeader.vue';
import Segmented from '../components/Segmented.vue';
import ControlPanel from './ControlPanel.vue';
import Chat from './Chat.vue';
import { useI18n } from '../i18n/index.js';

const { t } = useI18n();
const route = useRoute();
const router = useRouter();

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
</script>

<style scoped>
.run-view { display: block; }
/* Segmented 内嵌进 PageHeader 操作区,不额外占行 */
.run-tabs { margin-left: auto; }
</style>
