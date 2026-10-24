<template>
  <div class="credential-vault">
    <PageHeader
      icon="key"
      :title="t('view.credentialVault.title')"
      :subtitle="t('view.credentialVault.subtitle')"
    />

    <p v-if="error" class="cv-error">{{ error }}</p>
    <p v-if="notice" class="cv-notice">{{ notice }}</p>

    <Card :title="t('view.credentialVault.listTitle')" icon="key">
      <template #actions>
        <button class="btn-ghost" :disabled="loading" @click="load">
          <AppIcon name="refresh" :size="14" />
          <span>{{ t('common.refresh') }}</span>
        </button>
      </template>

      <p v-if="loading" class="muted">{{ t('common.loading') }}</p>
      <p v-else-if="!credentials.length" class="muted">{{ t('view.credentialVault.empty') }}</p>
      <table v-else class="cv-table">
        <thead>
          <tr>
            <th>{{ t('view.credentialVault.col.agent') }}</th>
            <th>{{ t('view.credentialVault.col.type') }}</th>
            <th>{{ t('view.credentialVault.col.preview') }}</th>
            <th>{{ t('view.credentialVault.col.lastUsed') }}</th>
            <th>{{ t('view.credentialVault.col.actions') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="c in credentials" :key="c.id">
            <td>{{ c.agent_name || '—' }}</td>
            <td><Badge tone="neutral">{{ c.credential_type }}</Badge></td>
            <!-- 只显示后端给的掩码预览；明文永远不进这个页面。 -->
            <td class="cv-table__preview">{{ c.preview || '—' }}</td>
            <td class="muted">{{ formatTs(c.last_used_at) }}</td>
            <td class="cv-table__actions">
              <button
                class="act-btn small"
                :disabled="busy === c.id"
                @click="askRotate(c)"
              >
                {{ t('view.credentialVault.action.rotate') }}
              </button>
              <button
                class="act-btn small act-btn--danger"
                :disabled="busy === c.id"
                @click="askDelete(c)"
              >
                {{ t('view.credentialVault.action.delete') }}
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </Card>

    <p class="cv-note muted">{{ t('view.credentialVault.noPlaintextNote') }}</p>

    <!-- 二次确认：删除 / 轮换都是不可逆操作 -->
    <div v-if="pending" v-modal-a11y class="cv-confirm-overlay" @click.self="pending = null">
      <div class="cv-confirm" role="dialog" aria-modal="true">
        <h3>{{ pending.kind === 'delete' ? t('view.credentialVault.confirm.deleteTitle') : t('view.credentialVault.confirm.rotateTitle') }}</h3>
        <p class="muted">
          {{
            pending.kind === 'delete'
              ? t('view.credentialVault.confirm.deleteBody', { agent: pending.cred.agent_name })
              : t('view.credentialVault.confirm.rotateBody', { agent: pending.cred.agent_name })
          }}
        </p>
        <div class="cv-confirm__actions">
          <button class="act-btn small" @click="pending = null">
            {{ t('common.cancel') }}
          </button>
          <button class="act-btn small act-btn--danger" :disabled="busy !== ''" @click="confirmPending">
            {{ t('view.credentialVault.confirm.proceed') }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue';
import { useApiStore } from '../stores/api.js';
import { useI18n } from '../i18n';
import PageHeader from '../components/PageHeader.vue';
import Card from '../components/Card.vue';
import Badge from '../components/Badge.vue';
import AppIcon from '../components/AppIcon.vue';

const { t } = useI18n();
const api = useApiStore();

const credentials = ref([]);
const loading = ref(false);
const error = ref('');
const notice = ref('');
const busy = ref('');
const pending = ref(null);

function formatTs(ts) {
  if (!ts) return '—';
  // 后端给的是 epoch 秒；0 表示"从未使用过"，显示破折号而不是 1970。
  return new Date(ts * 1000).toLocaleString();
}

async function load() {
  loading.value = true;
  error.value = '';
  try {
    const data = await api.get('/api/credential-vault/credentials');
    credentials.value = data.credentials || [];
  } catch {
    error.value = t('view.credentialVault.loadFailed');
  } finally {
    loading.value = false;
  }
}

function askRotate(cred) {
  pending.value = { kind: 'rotate', cred };
}

function askDelete(cred) {
  pending.value = { kind: 'delete', cred };
}

async function confirmPending() {
  const job = pending.value;
  if (!job) return;
  busy.value = job.cred.id;
  error.value = '';
  notice.value = '';
  try {
    if (job.kind === 'delete') {
      await api.delete(`/api/credential-vault/credentials/${encodeURIComponent(job.cred.id)}`);
      notice.value = t('view.credentialVault.deleted');
    } else {
      await api.post(
        `/api/credential-vault/credentials/${encodeURIComponent(job.cred.id)}/rotate`,
        {},
      );
      notice.value = t('view.credentialVault.rotated');
    }
    pending.value = null;
    await load();
  } catch {
    error.value =
      job.kind === 'delete'
        ? t('view.credentialVault.deleteFailed')
        : t('view.credentialVault.rotateFailed');
  } finally {
    busy.value = '';
  }
}

onMounted(() => load());
</script>

<style scoped>
.cv-error { color: var(--danger, #dc2626); font-size: 13px; }
.cv-notice { color: var(--success, #16a34a); font-size: 13px; }
.cv-table { width: 100%; border-collapse: collapse; font-size: var(--fs-sm); }
.cv-table th,
.cv-table td {
  padding: var(--sp-2);
  text-align: left;
  border-bottom: 1px solid var(--border);
}
.cv-table__preview { font-family: var(--font-mono, monospace); }
.cv-table__actions { display: flex; gap: var(--sp-2); }
.cv-note { margin-top: var(--sp-3); font-size: var(--fs-xs); }
.cv-confirm-overlay {
  position: fixed;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(0, 0, 0, 0.4);
  z-index: 50;
}
.cv-confirm {
  width: min(420px, 90vw);
  padding: var(--sp-4);
  border-radius: var(--r-md, 8px);
  background: var(--surface, #fff);
}
.cv-confirm__actions {
  display: flex;
  justify-content: flex-end;
  gap: var(--sp-2);
  margin-top: var(--sp-3);
}
</style>
