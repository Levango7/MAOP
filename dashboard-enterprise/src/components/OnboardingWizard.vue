<template>
  <div v-if="visible" class="onboard-overlay" @click.self="skip">
    <Card class="onboard-wizard" :padded="false">
      <template #title>
        <h2>{{ t('view.onboard.title') }}</h2>
      </template>
      <template #actions>
        <button class="onboard-skip" @click="skip">{{ t('view.onboard.skip') }}</button>
      </template>

      <nav class="onboard-steps">
        <button v-for="(s, i) in steps" :key="i"
          class="onboard-step-dot" :class="{ active: i === step, done: i < step }"
          :disabled="i > step" @click="step = i">
          <span>{{ i + 1 }}</span>
          <small>{{ s.label }}</small>
        </button>
      </nav>

      <div class="onboard-body">
        <div v-if="step === 0" class="onboard-step-content">
          <p>{{ t('view.onboard.step1Desc') }}</p>
          <div class="onboard-quick-actions">
            <router-link to="/agents/new" class="btn btn-primary">{{ t('view.onboard.createAgent') }}</router-link>
            <router-link to="/chat" class="btn btn-outline">{{ t('view.onboard.openChat') }}</router-link>
          </div>
          <p class="onboard-hint">{{ t('view.onboard.step1Hint') }}</p>
        </div>

        <div v-if="step === 1" class="onboard-step-content">
          <p>{{ t('view.onboard.step2Desc') }}</p>
          <div class="onboard-quick-actions">
            <router-link to="/agents" class="btn btn-outline">{{ t('view.onboard.browseAgents') }}</router-link>
            <router-link to="/models" class="btn btn-outline">{{ t('view.onboard.manageModels') }}</router-link>
          </div>
          <p class="onboard-hint">{{ t('view.onboard.step2Hint') }}</p>
        </div>

        <div v-if="step === 2" class="onboard-step-content">
          <p>{{ t('view.onboard.step3Desc') }}</p>
          <div class="onboard-quick-actions">
            <a :href="t('view.onboard.docsUrl')" target="_blank" class="btn btn-outline">{{ t('view.onboard.readDocs') }}</a>
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

const { t } = useI18n();
const router = useRouter();

const STORAGE_KEY = 'maop_onboarding_completed';

const completed = ref(localStorage.getItem(STORAGE_KEY) === '1');
const visible = ref(!completed.value);
const step = ref(0);

const steps = [
  { label: t('view.onboard.step1') },
  { label: t('view.onboard.step2') },
  { label: t('view.onboard.step3') },
];

function skip() {
  visible.value = false;
  localStorage.setItem(STORAGE_KEY, '1');
}

function finish() {
  skip();
  router.push('/agents');
}
</script>

<style scoped>
.onboard-overlay {
  position: fixed; inset: 0; z-index: 100;
  background: rgba(0,0,0,0.5);
  display: flex; align-items: center; justify-content: center;
  padding: 1rem;
}
.onboard-wizard { max-width: 520px; width: 100%; }
.onboard-skip { background: none; border: none; color: var(--text-muted); cursor: pointer; font-size: 0.875rem; }
.onboard-steps { display: flex; gap: 0; padding: 1rem 1.5rem; border-bottom: 1px solid var(--border); }
.onboard-step-dot { flex: 1; display: flex; flex-direction: column; align-items: center; gap: 4px; background: none; border: none; cursor: pointer; color: var(--text-muted); font-size: 0.75rem; padding: 0; }
.onboard-step-dot span { width: 28px; height: 28px; border-radius: 50%; display: flex; align-items: center; justify-content: center; background: var(--bg-muted); font-weight: 600; font-size: 0.8125rem; }
.onboard-step-dot.active span { background: var(--primary); color: #fff; }
.onboard-step-dot.done span { background: var(--success); color: #fff; }
.onboard-step-dot.active { color: var(--primary); }
.onboard-step-dot.done { color: var(--success); }
.onboard-step-dot:disabled { opacity: 0.4; cursor: not-allowed; }
.onboard-body { padding: 1.5rem; }
.onboard-step-content p { margin-bottom: 1rem; color: var(--text-secondary); }
.onboard-quick-actions { display: flex; gap: 0.5rem; margin-bottom: 1rem; }
.onboard-hint { font-size: 0.8125rem; color: var(--text-muted); }
</style>