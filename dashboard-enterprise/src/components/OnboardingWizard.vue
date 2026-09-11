<template>
  <div
    v-if="visible"
    ref="rootRef"
    class="onboard-wizard__overlay"
    data-modal-root="true"
    aria-modal="true"
    role="dialog"
    :aria-label="t('view.onboard.title')"
    @click.self="skip"
  >
    <Card class="onboard-wizard" :padded="false">
      <template #title>
        <h2>{{ t('view.onboard.title') }}</h2>
      </template>
      <template #actions>
        <button class="onboard-wizard__skip" @click="skip">{{ t('view.onboard.skip') }}</button>
      </template>

      <nav class="onboard-wizard__steps">
        <button
v-for="(s, i) in steps" :key="i"
          class="onboard-wizard__step-dot" :class="{ 'onboard-wizard__step-dot--active': i === step, 'onboard-wizard__step-dot--done': i < step }"
          :disabled="i > step" @click="step = i">
          <span>{{ i + 1 }}</span>
          <small>{{ s.label }}</small>
        </button>
      </nav>

      <div class="onboard-wizard__body">
        <div v-if="step === 0" class="onboard-wizard__step-content">
          <p>{{ t('view.onboard.step1Desc') }}</p>
          <div class="onboard-wizard__quick-actions">
            <router-link to="/agents/new" class="btn btn-primary">{{ t('view.onboard.createAgent') }}</router-link>
            <router-link to="/chat" class="btn btn-ghost">{{ t('view.onboard.openChat') }}</router-link>
          </div>
          <p class="onboard-wizard__hint">{{ t('view.onboard.step1Hint') }}</p>
        </div>

        <div v-if="step === 1" class="onboard-wizard__step-content">
          <p>{{ t('view.onboard.step2Desc') }}</p>
          <div class="onboard-wizard__quick-actions">
            <router-link to="/agents" class="btn btn-ghost">{{ t('view.onboard.browseAgents') }}</router-link>
            <router-link to="/models" class="btn btn-ghost">{{ t('view.onboard.manageModels') }}</router-link>
          </div>
          <p class="onboard-wizard__hint">{{ t('view.onboard.step2Hint') }}</p>
        </div>

        <div v-if="step === 2" class="onboard-wizard__step-content">
          <p>{{ t('view.onboard.step3Desc') }}</p>
          <div class="onboard-wizard__quick-actions">
            <a :href="t('view.onboard.docsUrl')" target="_blank" class="btn btn-ghost">{{ t('view.onboard.readDocs') }}</a>
          </div>
          <button class="btn btn-primary" @click="finish">{{ t('view.onboard.done') }}</button>
        </div>
      </div>
    </Card>
  </div>
</template>

<script setup>
import { ref } from 'vue';
import { useRouter } from 'vue-router';
import { useI18n } from '../i18n';
import Card from './Card.vue';
import { useModalA11y } from '../composables/useModalA11y.js';

const { t } = useI18n();
const router = useRouter();

const STORAGE_KEY = 'maop_onboarding_completed';

// R7 修复: localStorage 访问需 try-catch 防护 (隐私模式 / 配额超限 / 禁用场景)
function lsGet(key) { try { return localStorage.getItem(key); } catch { return null; } }
function lsSet(key, val) { try { localStorage.setItem(key, val); } catch { /* noop */ } }

const completed = ref(lsGet(STORAGE_KEY) === '1');
const visible = ref(!completed.value);
const step = ref(0);
const rootRef = ref(null);

const steps = [
  { label: t('view.onboard.step1') },
  { label: t('view.onboard.step2') },
  { label: t('view.onboard.step3') },
];

function skip() {
  visible.value = false;
  lsSet(STORAGE_KEY, '1');
}

function finish() {
  skip();
  router.push('/agents');
}

// 焦点管理 + focus trap + Esc 关闭(复用 useModalA11y)
useModalA11y(
  () => visible.value,
  () => skip(),
  () => rootRef.value,
);
</script>

<style scoped>
/* F4: BEM 命名统一 — block: onboard-wizard, element: __*, modifier: --* */
.onboard-wizard__overlay {
  position: fixed; inset: 0;
  /* 修复: z-index: 100 硬编码 token 化 → var(--z-modal) */
  z-index: var(--z-modal);
  /* 修复: fallback 与 tokens.css 中 --overlay-scrim 定义 rgba(15, 23, 42, .65) 不一致, 修正 */
  background: var(--overlay-scrim);
  display: flex; align-items: center; justify-content: center;
  /* 修复: 1rem 硬编码 token 化 → var(--sp-4) */
  padding: var(--sp-4);
}
.onboard-wizard { max-width: 520px; width: 100%; }
/* 修复: 0.875rem 硬编码 token 化 → var(--fs-sm) */
.onboard-wizard__skip { background: none; border: none; color: var(--text-muted); cursor: pointer; font-size: var(--fs-sm); }
.onboard-wizard__steps { display: flex; gap: 0; /* 修复: 1rem token 化 */ padding: var(--sp-4) var(--sp-6); border-bottom: 1px solid var(--border); }
/* 修复: 4px 硬编码 token 化 → var(--r-sm); 0.75rem → var(--fs-2xs) */
.onboard-wizard__step-dot { flex: 1; display: flex; flex-direction: column; align-items: center; gap: var(--r-sm); background: none; border: none; cursor: pointer; color: var(--text-muted); font-size: var(--fs-2xs); padding: 0; }
.onboard-wizard__step-dot span { width: 28px; height: 28px; border-radius: var(--r-full); display: flex; align-items: center; justify-content: center; background: var(--bg-muted); font-weight: 600; font-size: var(--fs-xs); }
.onboard-wizard__step-dot--active span { background: var(--brand); color: var(--brand-contrast); }
.onboard-wizard__step-dot--done span { background: var(--success); color: var(--brand-contrast); }
.onboard-wizard__step-dot--active { color: var(--brand); }
.onboard-wizard__step-dot--done { color: var(--success); }
.onboard-wizard__step-dot:disabled { opacity: 0.4; cursor: not-allowed; }
.onboard-wizard__body { padding: var(--sp-6); }
/* 修复: 1rem token 化 → var(--sp-4); 0.5rem → var(--sp-2) */
.onboard-wizard__step-content p { margin-bottom: var(--sp-4); color: var(--text-muted); }
.onboard-wizard__quick-actions { display: flex; gap: var(--sp-2); margin-bottom: var(--sp-4); }
.onboard-wizard__hint { font-size: var(--fs-xs); color: var(--text-muted); }
</style>
