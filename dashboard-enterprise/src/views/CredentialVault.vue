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
        <button class="btn-primary" type="button" @click="openForm">
          <AppIcon name="plus" :size="14" />
          <span>{{ t('view.credentialVault.action.create') }}</span>
        </button>
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

    <!-- 新建凭据：敏感字段按 credential_type 动态显示 -->
    <div v-if="showForm" v-modal-a11y class="cv-confirm-overlay" @click.self="closeForm">
      <div class="cv-confirm cv-form" role="dialog" aria-modal="true">
        <h3>{{ t('view.credentialVault.form.title') }}</h3>

        <label class="cv-form__field">
          <span class="cv-form__label">{{ t('view.credentialVault.form.type') }}</span>
          <select v-model="form.credential_type" class="input" @change="clearSecrets">
            <option v-for="ct in CREDENTIAL_TYPES" :key="ct" :value="ct">{{ ct }}</option>
          </select>
        </label>

        <label class="cv-form__field">
          <span class="cv-form__label">{{ t('view.credentialVault.form.agent') }} *</span>
          <input v-model="form.agent_name" type="text" class="input" :placeholder="t('view.credentialVault.form.agentHint')" />
        </label>

        <label class="cv-form__field">
          <span class="cv-form__label">{{ t('view.credentialVault.form.name') }}</span>
          <input v-model="form.name" type="text" class="input" :placeholder="t('view.credentialVault.form.nameHint')" />
        </label>

        <label v-for="f in activeFields" :key="f" class="cv-form__field">
          <span class="cv-form__label">
            {{ t(`view.credentialVault.field.${f}`) }}
            <span v-if="isRequired(f)" class="cv-form__req">*</span>
          </span>
          <input
            v-model="form[f]"
            :type="FIELD_META[f].secret ? 'password' : 'text'"
            :autocomplete="FIELD_META[f].secret ? 'new-password' : 'off'"
            class="input"
          />
        </label>

        <p v-if="formError" class="cv-error">{{ formError }}</p>

        <div class="cv-confirm__actions">
          <button class="act-btn small" @click="closeForm">{{ t('common.cancel') }}</button>
          <button class="btn-primary" :disabled="creating" @click="submitForm">
            {{ creating ? t('view.credentialVault.form.saving') : t('view.credentialVault.form.submit') }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue';
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

// ── 新建凭据 ───────────────────────────────────────────────────
// 后端只校验 credential_type 合法性，**不校验对应敏感字段是否为空**。
// 也就是说 POST 一个只有类型的空凭据是能成功的 —— 但它永远无法用于认证。
// 所以"哪个字段必填"这条规则必须由前端持有（见 FIELD_SPECS.required）。
const CREDENTIAL_TYPES = [
  'api_key',
  'oauth_token',
  'username_password',
  'license_key',
  'cookie',
  'bearer_token',
];

const FIELD_META = {
  api_key: { secret: true },
  oauth_access_token: { secret: true },
  oauth_refresh_token: { secret: true },
  oauth_expires_at: { secret: false },
  username: { secret: false },
  password: { secret: true },
  license_key: { secret: true },
  cookie_string: { secret: true },
  bearer_token: { secret: true },
};

const FIELD_SPECS = {
  api_key: { fields: ['api_key'], required: ['api_key'] },
  oauth_token: {
    fields: ['oauth_access_token', 'oauth_refresh_token', 'oauth_expires_at'],
    required: ['oauth_access_token'],
  },
  username_password: {
    fields: ['username', 'password'],
    required: ['username', 'password'],
  },
  license_key: { fields: ['license_key'], required: ['license_key'] },
  cookie: { fields: ['cookie_string'], required: ['cookie_string'] },
  bearer_token: { fields: ['bearer_token'], required: ['bearer_token'] },
};

function emptyForm() {
  return {
    credential_type: 'api_key',
    agent_name: '',
    name: '',
    api_key: '',
    oauth_access_token: '',
    oauth_refresh_token: '',
    oauth_expires_at: '',
    username: '',
    password: '',
    license_key: '',
    cookie_string: '',
    bearer_token: '',
  };
}

const form = reactive(emptyForm());
const showForm = ref(false);
const formError = ref('');
const creating = ref(false);

const activeFields = computed(() => FIELD_SPECS[form.credential_type]?.fields || []);
const requiredFields = computed(() => FIELD_SPECS[form.credential_type]?.required || []);

function isRequired(field) {
  return requiredFields.value.includes(field);
}

// 切换类型时把上一类型的敏感值清掉：否则切走再切回来，
// 已输入但界面上看不见的旧密钥仍会被提交。
function clearSecrets() {
  for (const key of Object.keys(FIELD_META)) {
    form[key] = '';
  }
  formError.value = '';
}

function openForm() {
  Object.assign(form, emptyForm());
  formError.value = '';
  showForm.value = true;
}

function closeForm() {
  showForm.value = false;
  // 关掉就把内存里的明文抹掉，不留在组件状态里。
  Object.assign(form, emptyForm());
  formError.value = '';
}

async function submitForm() {
  if (creating.value) return;
  formError.value = '';

  if (!form.agent_name.trim()) {
    formError.value = t('view.credentialVault.form.agentRequired');
    return;
  }
  const missing = requiredFields.value.filter((f) => !String(form[f] ?? '').trim());
  if (missing.length) {
    formError.value = t('view.credentialVault.form.fieldRequired', {
      field: t(`view.credentialVault.field.${missing[0]}`),
    });
    return;
  }

  creating.value = true;
  try {
    const payload = {
      credential_type: form.credential_type,
      agent_name: form.agent_name.trim(),
      name: form.name.trim(),
    };
    // 只提交该类型用得到的字段，避免把无关空值一并塞给后端。
    for (const f of activeFields.value) {
      const raw = form[f];
      payload[f] = f === 'oauth_expires_at' ? Number(raw) || 0 : String(raw ?? '');
    }
    await api.post('/api/credential-vault/credentials', payload);
    notice.value = t('view.credentialVault.created');
    closeForm();
    await load();
  } catch {
    formError.value = t('view.credentialVault.createFailed');
  } finally {
    creating.value = false;
  }
}

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

/* 新建表单：字段多、可能超出视口高度，单独给滚动 */
.cv-form {
  width: min(520px, 92vw);
  max-height: 86vh;
  overflow-y: auto;
}
.cv-form__field {
  display: flex;
  flex-direction: column;
  gap: var(--sp-1);
  margin-bottom: var(--sp-3);
}
.cv-form__label {
  font-size: var(--fs-sm);
  font-weight: 600;
}
.cv-form__req {
  color: var(--danger, #dc2626);
  margin-left: 2px;
}
</style>
