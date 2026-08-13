<template>
  <div class="sso-page">
    <ListPageLayout
      v-model:filters="filters"
      :loading="loading"
      :error="error"
      :empty="!visibleProviders.length"
      :filter-schema="filterSchema"
      search-key="query"
      :search-placeholder="t('common.search')"
      :results-label="`${visibleProviders.length} / ${providers.length} ${t('view.sso.providers')}`"
      :error-title="t('view.sso.loadError')"
      :empty-title="t('view.sso.noProviders')"
      :empty-desc="t('view.sso.noProvidersDesc')"
      :loading-lines="5"
    >
      <template #badges>
        <Badge tone="brand">{{ t('view.sso.enterprise') }}</Badge>
      </template>
      <template #actions>
        <button class="btn btn--primary" @click="openCreate">
          <AppIcon name="plus" :size="15" /> {{ t('view.sso.addProvider') }}
        </button>
      </template>

      <template #content>
        <div class="sso-table" role="table" :aria-label="t('view.sso.subtitle')">
          <div class="sso-row sso-row--head" role="row">
            <div class="sso-cell sso-cell--name" role="columnheader">{{ t('view.sso.name') }}</div>
            <div class="sso-cell sso-cell--protocol" role="columnheader">{{ t('view.sso.protocol') }}</div>
            <div class="sso-cell sso-cell--status" role="columnheader">{{ t('common.status') }}</div>
            <div class="sso-cell sso-cell--redirect" role="columnheader">{{ t('view.sso.autoRedirect') }}</div>
            <div class="sso-cell sso-cell--created" role="columnheader">{{ t('view.sso.createdAt') }}</div>
            <div class="sso-cell sso-cell--actions" role="columnheader">{{ t('common.actions') }}</div>
          </div>
          <div v-for="p in visibleProviders" :key="p.id" class="sso-row" role="row">
            <div class="sso-cell sso-cell--name" role="cell">
              <span class="sso-name">{{ p.name }}</span>
              <span v-if="p.tenant_id" class="sso-tenant muted">{{ p.tenant_id }}</span>
              <span v-else class="sso-tenant muted">{{ t('view.sso.globalTenant') }}</span>
            </div>
            <div class="sso-cell sso-cell--protocol" role="cell">
              <Badge :tone="p.protocol === 'oidc' ? 'info' : 'brand'">
                {{ protocolLabel(p.protocol) }}
              </Badge>
            </div>
            <div class="sso-cell sso-cell--status" role="cell">
              <span class="sso-status" :class="p.enabled ? 'is-on' : 'is-off'">
                <AppIcon :name="p.enabled ? 'check' : 'x'" :size="13" aria-hidden="true" />
                {{ p.enabled ? t('view.sso.enabled') : t('view.sso.disabled') }}
              </span>
            </div>
            <div class="sso-cell sso-cell--redirect" role="cell">
              <span class="sso-status" :class="p.auto_redirect ? 'is-on' : 'is-off'">
                <AppIcon :name="p.auto_redirect ? 'check' : 'x'" :size="13" aria-hidden="true" />
              </span>
            </div>
            <div class="sso-cell sso-cell--created" role="cell">{{ formatRel(p.created_at) }}</div>
            <div class="sso-cell sso-cell--actions" role="cell">
              <button
                class="btn-icon"
                :title="t('view.sso.test')"
                :aria-label="t('view.sso.test')"
                :disabled="testingId === p.id"
                @click="testProvider(p)"
              >
                <AppIcon name="plug" :size="14" aria-hidden="true" />
              </button>
              <button
                class="btn-icon"
                :title="p.enabled ? t('common.disable') : t('common.enable')"
                :aria-label="p.enabled ? t('common.disable') : t('common.enable')"
                @click="toggleProvider(p)"
              >
                <AppIcon :name="p.enabled ? 'x' : 'check'" :size="14" aria-hidden="true" />
              </button>
              <button
                class="btn-icon"
                :title="t('common.edit')"
                :aria-label="t('common.edit')"
                @click="openEdit(p)"
              >
                <AppIcon name="gear" :size="14" aria-hidden="true" />
              </button>
              <button
                v-if="p.protocol === 'saml'"
                class="btn-icon"
                :title="t('view.sso.metadata.export')"
                :aria-label="t('view.sso.metadata.export')"
                @click="downloadMetadata(p)"
              >
                <AppIcon name="download" :size="14" aria-hidden="true" />
              </button>
              <button
                class="btn-icon btn-icon--danger"
                :title="t('common.delete')"
                :aria-label="t('common.delete')"
                @click="deleteProvider(p)"
              >
                <AppIcon name="trash" :size="14" aria-hidden="true" />
              </button>
            </div>
          </div>
        </div>
      </template>
    </ListPageLayout>

    <!-- 添加/编辑 IdP 对话框 -->
    <div
      v-if="showDialog"
      v-modal-a11y
      class="sso-dialog-overlay"
      @click.self="closeDialog"
      @modal:escape="closeDialog"
    >
      <div class="sso-dialog" role="document">
        <button class="sso-dialog-close" type="button" :aria-label="t('common.close')" @click="closeDialog">
          <AppIcon name="x" :size="16" aria-hidden="true" />
        </button>
        <h3>{{ isEditing ? t('view.sso.editProvider') : t('view.sso.addProvider') }}</h3>

        <div class="sso-form">
          <!-- 通用字段 -->
          <label>
            <span class="sso-label">{{ t('view.sso.name') }}</span>
            <input v-model="form.name" type="text" class="sso-input" :placeholder="t('view.sso.name')" />
          </label>

          <label>
            <span class="sso-label">{{ t('view.sso.protocol') }}</span>
            <select v-model="form.protocol" class="sso-input" :disabled="isEditing">
              <option value="oidc">{{ t('view.sso.protocol.oidc.full') }}</option>
              <option value="saml">{{ t('view.sso.protocol.saml.full') }}</option>
            </select>
          </label>

          <label>
            <span class="sso-label">{{ t('view.sso.tenantOptional') }}</span>
            <input v-model="form.tenant_id" type="text" class="sso-input" placeholder="" />
          </label>

          <div class="sso-toggles">
            <label class="sso-toggle">
              <input v-model="form.enabled" type="checkbox" />
              <span>{{ t('view.sso.enabled') }}</span>
            </label>
            <label class="sso-toggle">
              <input v-model="form.auto_redirect" type="checkbox" />
              <span>{{ t('view.sso.autoRedirect') }}</span>
            </label>
          </div>

          <!-- OIDC 表单 -->
          <fieldset v-if="form.protocol === 'oidc'" class="sso-fieldset">
            <legend>{{ t('view.sso.protocol.oidc.full') }}</legend>
            <label>
              <span class="sso-label">{{ t('view.sso.oidc.issuerUrl') }}</span>
              <input v-model="form.config.issuer_url" type="text" class="sso-input" :placeholder="t('view.sso.oidc.issuerUrlHint')" />
            </label>
            <label>
              <span class="sso-label">{{ t('view.sso.oidc.authorizeUrl') }} <em class="req">*</em></span>
              <input v-model="form.config.authorize_url" type="text" class="sso-input" />
            </label>
            <label>
              <span class="sso-label">{{ t('view.sso.oidc.tokenUrl') }} <em class="req">*</em></span>
              <input v-model="form.config.token_url" type="text" class="sso-input" />
            </label>
            <label>
              <span class="sso-label">{{ t('view.sso.oidc.userinfoUrl') }}</span>
              <input v-model="form.config.userinfo_url" type="text" class="sso-input" :placeholder="t('view.sso.oidc.userinfoUrlHint')" />
            </label>
            <label>
              <span class="sso-label">{{ t('view.sso.oidc.clientId') }} <em class="req">*</em></span>
              <input v-model="form.config.client_id" type="text" class="sso-input" />
            </label>
            <label>
              <span class="sso-label">{{ t('view.sso.oidc.clientSecret') }} <em class="req">*</em></span>
              <input
                v-model="form.config.client_secret"
                type="password"
                class="sso-input"
                autocomplete="new-password"
                :placeholder="isEditing ? t('view.sso.oidc.clientSecretEditHint') : ''"
              />
            </label>
            <label>
              <span class="sso-label">{{ t('view.sso.oidc.redirectUri') }} <em class="req">*</em></span>
              <input v-model="form.config.redirect_uri" type="text" class="sso-input" :placeholder="t('view.sso.oidc.redirectUriHint')" />
            </label>
            <label>
              <span class="sso-label">{{ t('view.sso.oidc.scopes') }}</span>
              <input v-model="scopesText" type="text" class="sso-input" :placeholder="t('view.sso.oidc.scopesHint')" />
            </label>
            <label class="sso-toggle">
              <input v-model="form.config.use_pkce" type="checkbox" />
              <span>{{ t('view.sso.oidc.usePkce') }}</span>
            </label>
          </fieldset>

          <!-- SAML 表单 -->
          <fieldset v-if="form.protocol === 'saml'" class="sso-fieldset">
            <legend>{{ t('view.sso.protocol.saml.full') }}</legend>
            <label>
              <span class="sso-label">{{ t('view.sso.saml.spEntityId') }} <em class="req">*</em></span>
              <input v-model="form.config.sp_entity_id" type="text" class="sso-input" />
            </label>
            <label>
              <span class="sso-label">{{ t('view.sso.saml.idpEntityId') }} <em class="req">*</em></span>
              <input v-model="form.config.entity_id" type="text" class="sso-input" />
            </label>
            <label>
              <span class="sso-label">{{ t('view.sso.saml.ssoUrl') }} <em class="req">*</em></span>
              <input v-model="form.config.sso_url" type="text" class="sso-input" />
            </label>
            <label>
              <span class="sso-label">{{ t('view.sso.saml.sloUrl') }}</span>
              <input v-model="form.config.slo_url" type="text" class="sso-input" :placeholder="t('view.sso.saml.sloUrlHint')" />
            </label>
            <label>
              <span class="sso-label">{{ t('view.sso.saml.acsUrl') }} <em class="req">*</em></span>
              <input v-model="form.config.acs_url" type="text" class="sso-input" :placeholder="t('view.sso.saml.acsUrlHint')" />
            </label>
            <label>
              <span class="sso-label">{{ t('view.sso.saml.x509Cert') }} <em class="req">*</em></span>
              <textarea
                v-model="form.config.x509_cert"
                class="sso-input sso-textarea"
                rows="4"
                :placeholder="isEditing ? t('view.sso.saml.x509CertEditHint') : t('view.sso.saml.x509CertHint')"
              />
            </label>
            <label>
              <span class="sso-label">{{ t('view.sso.saml.nameIdFormat') }}</span>
              <select v-model="form.config.name_id_format" class="sso-input">
                <option value="urn:oasis:names:tc:SAML:2.0:nameid-format:emailAddress">{{ t('view.sso.saml.nameIdFormat.email') }}</option>
                <option value="urn:oasis:names:tc:SAML:2.0:nameid-format:unspecified">{{ t('view.sso.saml.nameIdFormat.unspecified') }}</option>
                <option value="urn:oasis:names:tc:SAML:2.0:nameid-format:persistent">{{ t('view.sso.saml.nameIdFormat.persistent') }}</option>
                <option value="urn:oasis:names:tc:SAML:2.0:nameid-format:transient">{{ t('view.sso.saml.nameIdFormat.transient') }}</option>
              </select>
            </label>
            <label class="sso-toggle">
              <input v-model="form.config.want_signed" type="checkbox" />
              <span>{{ t('view.sso.saml.wantSigned') }}</span>
            </label>
          </fieldset>

          <!-- 属性映射 -->
          <fieldset class="sso-fieldset">
            <legend>{{ t('view.sso.mapping.title') }}</legend>
            <p class="sso-fieldset-desc">{{ t('view.sso.mapping.desc') }}</p>
            <label>
              <span class="sso-label">{{ t('view.sso.mapping.externalId') }}</span>
              <input v-model="form.attribute_mapping.external_id" type="text" class="sso-input" :placeholder="t('view.sso.mapping.externalIdHint')" />
            </label>
            <label>
              <span class="sso-label">{{ t('view.sso.mapping.email') }}</span>
              <input v-model="form.attribute_mapping.email" type="text" class="sso-input" :placeholder="t('view.sso.mapping.emailHint')" />
            </label>
            <label>
              <span class="sso-label">{{ t('view.sso.mapping.displayName') }}</span>
              <input v-model="form.attribute_mapping.display_name" type="text" class="sso-input" :placeholder="t('view.sso.mapping.displayNameHint')" />
            </label>
            <label>
              <span class="sso-label">{{ t('view.sso.mapping.roles') }}</span>
              <input v-model="form.attribute_mapping.roles" type="text" class="sso-input" :placeholder="t('view.sso.mapping.rolesHint')" />
            </label>
            <label>
              <span class="sso-label">{{ t('view.sso.mapping.tenantId') }}</span>
              <input v-model="form.attribute_mapping.tenant_id" type="text" class="sso-input" :placeholder="t('view.sso.mapping.tenantIdHint')" />
            </label>

            <!-- 角色映射表 -->
            <div class="sso-role-mapping">
              <span class="sso-label">{{ t('view.sso.mapping.roleMapping') }}</span>
              <p class="sso-fieldset-desc">{{ t('view.sso.mapping.roleMappingHint') }}</p>
              <div v-if="roleMappings.length" class="sso-role-mapping-rows">
                <div v-for="(rm, idx) in roleMappings" :key="idx" class="sso-role-mapping-row">
                  <input v-model="rm.idpGroup" type="text" class="sso-input" :placeholder="t('view.sso.mapping.roleMappingIdpGroup')" />
                  <span class="sso-role-mapping-arrow">→</span>
                  <input v-model="rm.systemRole" type="text" class="sso-input" :placeholder="t('view.sso.mapping.roleMappingSystemRole')" />
                  <button class="btn-icon btn-icon--danger" :aria-label="t('common.delete')" @click="removeRoleMapping(idx)">
                    <AppIcon name="x" :size="14" aria-hidden="true" />
                  </button>
                </div>
              </div>
              <p v-else class="sso-role-mapping-empty">{{ t('view.sso.mapping.roleMappingEmpty') }}</p>
              <button class="btn btn--sm" @click="addRoleMapping">
                <AppIcon name="plus" :size="13" /> {{ t('view.sso.mapping.roleMappingAdd') }}
              </button>
            </div>
          </fieldset>
        </div>

        <p v-if="formError" class="sso-form-error">{{ formError }}</p>

        <div class="sso-dialog-actions">
          <button class="btn" @click="closeDialog">{{ t('common.cancel') }}</button>
          <button class="btn btn--primary" :disabled="saving" @click="saveProvider">
            {{ saving ? t('common.loading') : t('common.save') }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue';
import { useApiStore } from '../stores/api.js';
import { useToast } from '../composables/useToast.js';
import { useI18n } from '../i18n';
import Badge from '../components/Badge.vue';
import ListPageLayout from '../components/ListPageLayout.vue';
import AppIcon from '../components/AppIcon.vue';

const { t } = useI18n();
const api = useApiStore();
const toast = useToast();

// ── 列表状态 ──────────────────────────────────────────────
const providers = ref([]);
const loading = ref(true);
const error = ref('');
const testingId = ref(null);
const filters = reactive({ protocol: '', enabled: '', query: '' });

const filterSchema = computed(() => [
  {
    key: 'protocol',
    label: t('view.sso.protocol'),
    options: [
      { value: 'oidc', label: t('view.sso.protocol.oidc') },
      { value: 'saml', label: t('view.sso.protocol.saml') },
    ],
  },
  {
    key: 'enabled',
    label: t('common.status'),
    options: [
      { value: 'true', label: t('view.sso.enabled') },
      { value: 'false', label: t('view.sso.disabled') },
    ],
  },
]);

const visibleProviders = computed(() => {
  const fp = filters.protocol;
  const fe = filters.enabled;
  const fq = (filters.query || '').trim().toLowerCase();
  return providers.value.filter((p) => {
    if (fp && p.protocol !== fp) return false;
    if (fe === 'true' && !p.enabled) return false;
    if (fe === 'false' && p.enabled) return false;
    if (fq && !(p.name || '').toLowerCase().includes(fq)) return false;
    return true;
  });
});

// ── 对话框状态 ────────────────────────────────────────────
const showDialog = ref(false);
const saving = ref(false);
const formError = ref('');
const editingId = ref(null);
const isEditing = computed(() => editingId.value !== null);

// 角色映射的临时可编辑列表（idpGroup / systemRole 双向绑定）
const roleMappings = ref([]);

// scopes 文本输入（空格分隔）↔ 数组
const scopesText = ref('');

const form = reactive({
  name: '',
  protocol: 'oidc',
  tenant_id: '',
  enabled: true,
  auto_redirect: false,
  config: {
    // OIDC
    issuer_url: '',
    authorize_url: '',
    token_url: '',
    userinfo_url: '',
    client_id: '',
    client_secret: '',
    redirect_uri: '',
    scopes: ['openid', 'profile', 'email'],
    use_pkce: true,
    // SAML
    sp_entity_id: '',
    entity_id: '',
    sso_url: '',
    slo_url: '',
    acs_url: '',
    x509_cert: '',
    name_id_format: 'urn:oasis:names:tc:SAML:2.0:nameid-format:emailAddress',
    want_signed: true,
  },
  attribute_mapping: {
    external_id: 'sub',
    email: 'email',
    display_name: 'name',
    roles: 'groups',
    tenant_id: 'tid',
    role_mapping: {},
  },
});

// ── 工具函数 ──────────────────────────────────────────────
function protocolLabel(proto) {
  if (proto === 'oidc') return t('view.sso.protocol.oidc');
  if (proto === 'saml') return t('view.sso.protocol.saml');
  return proto || '—';
}

function formatRel(ts) {
  if (ts === null || ts === undefined || ts === 0) return '—';
  const num = typeof ts === 'number' ? ts : parseFloat(ts);
  if (isNaN(num)) return String(ts);
  // 后端返回 Unix 秒级时间戳
  const ms = num < 1e12 ? num * 1000 : num;
  const d = new Date(ms);
  if (isNaN(d.getTime())) return '—';
  const diff = Math.max(0, Math.floor((Date.now() - d.getTime()) / 1000));
  if (diff < 60) return diff + 's ago';
  if (diff < 3600) return Math.floor(diff / 60) + 'm ago';
  if (diff < 86400) return Math.floor(diff / 3600) + 'h ago';
  return Math.floor(diff / 86400) + 'd ago';
}

// ── 数据加载 ──────────────────────────────────────────────
async function load() {
  loading.value = true;
  try {
    const d = await api.get('/api/v1/sso/providers');
    providers.value = d.providers || [];
    error.value = '';
  } catch (e) {
    error.value = e.message || t('view.sso.loadError');
    providers.value = [];
  } finally {
    loading.value = false;
  }
}

// ── 对话框：创建/编辑 ────────────────────────────────────
function resetForm() {
  form.name = '';
  form.protocol = 'oidc';
  form.tenant_id = '';
  form.enabled = true;
  form.auto_redirect = false;
  form.config = {
    issuer_url: '',
    authorize_url: '',
    token_url: '',
    userinfo_url: '',
    client_id: '',
    client_secret: '',
    redirect_uri: '',
    scopes: ['openid', 'profile', 'email'],
    use_pkce: true,
    sp_entity_id: '',
    entity_id: '',
    sso_url: '',
    slo_url: '',
    acs_url: '',
    x509_cert: '',
    name_id_format: 'urn:oasis:names:tc:SAML:2.0:nameid-format:emailAddress',
    want_signed: true,
  };
  form.attribute_mapping = {
    external_id: 'sub',
    email: 'email',
    display_name: 'name',
    roles: 'groups',
    tenant_id: 'tid',
    role_mapping: {},
  };
  scopesText.value = 'openid profile email';
  roleMappings.value = [];
  formError.value = '';
}

function openCreate() {
  editingId.value = null;
  resetForm();
  showDialog.value = true;
}

function openEdit(p) {
  editingId.value = p.id;
  resetForm();
  form.name = p.name || '';
  form.protocol = p.protocol || 'oidc';
  form.tenant_id = p.tenant_id || '';
  form.enabled = !!p.enabled;
  form.auto_redirect = !!p.auto_redirect;

  const cfg = p.config || {};
  // OIDC 字段
  form.config.issuer_url = cfg.issuer_url || '';
  form.config.authorize_url = cfg.authorize_url || '';
  form.config.token_url = cfg.token_url || '';
  form.config.userinfo_url = cfg.userinfo_url || '';
  form.config.client_id = cfg.client_id || '';
  form.config.client_secret = ''; // 编辑时留空表示不修改
  form.config.redirect_uri = cfg.redirect_uri || '';
  form.config.scopes = Array.isArray(cfg.scopes) ? [...cfg.scopes] : ['openid', 'profile', 'email'];
  form.config.use_pkce = cfg.use_pkce !== false;
  // SAML 字段
  form.config.sp_entity_id = cfg.sp_entity_id || '';
  form.config.entity_id = cfg.entity_id || '';
  form.config.sso_url = cfg.sso_url || '';
  form.config.slo_url = cfg.slo_url || '';
  form.config.acs_url = cfg.acs_url || '';
  form.config.x509_cert = ''; // 编辑时留空表示不修改
  form.config.name_id_format = cfg.name_id_format || 'urn:oasis:names:tc:SAML:2.0:nameid-format:emailAddress';
  form.config.want_signed = cfg.want_signed !== false;

  const am = p.attribute_mapping || {};
  form.attribute_mapping.external_id = am.external_id || 'sub';
  form.attribute_mapping.email = am.email || 'email';
  form.attribute_mapping.display_name = am.display_name || 'name';
  form.attribute_mapping.roles = am.roles || 'groups';
  form.attribute_mapping.tenant_id = am.tenant_id || 'tid';
  form.attribute_mapping.role_mapping = { ...(am.role_mapping || {}) };

  // scopes 文本
  scopesText.value = form.config.scopes.join(' ');

  // 角色映射表
  roleMappings.value = Object.entries(form.attribute_mapping.role_mapping).map(([idpGroup, systemRole]) => ({
    idpGroup,
    systemRole,
  }));

  showDialog.value = true;
}

function closeDialog() {
  showDialog.value = false;
  formError.value = '';
}

function addRoleMapping() {
  roleMappings.value.push({ idpGroup: '', systemRole: '' });
}

function removeRoleMapping(idx) {
  roleMappings.value.splice(idx, 1);
}

// ── 表单校验 ──────────────────────────────────────────────
function validate() {
  if (!form.name.trim()) {
    formError.value = t('view.sso.nameRequired');
    return false;
  }
  if (form.protocol === 'oidc') {
    if (!form.config.authorize_url.trim()) { formError.value = t('view.sso.oidc.authorizeUrlRequired'); return false; }
    if (!form.config.token_url.trim()) { formError.value = t('view.sso.oidc.tokenUrlRequired'); return false; }
    if (!form.config.client_id.trim()) { formError.value = t('view.sso.oidc.clientIdRequired'); return false; }
    if (!isEditing.value && !form.config.client_secret.trim()) {
      formError.value = t('view.sso.oidc.clientSecretRequired');
      return false;
    }
    if (!form.config.redirect_uri.trim()) { formError.value = t('view.sso.oidc.redirectUriRequired'); return false; }
  } else if (form.protocol === 'saml') {
    if (!form.config.sp_entity_id.trim()) { formError.value = t('view.sso.saml.spEntityIdRequired'); return false; }
    if (!form.config.entity_id.trim()) { formError.value = t('view.sso.saml.idpEntityIdRequired'); return false; }
    if (!form.config.sso_url.trim()) { formError.value = t('view.sso.saml.ssoUrlRequired'); return false; }
    if (!form.config.acs_url.trim()) { formError.value = t('view.sso.saml.acsUrlRequired'); return false; }
    if (!isEditing.value && !form.config.x509_cert.trim()) {
      formError.value = t('view.sso.saml.x509CertRequired');
      return false;
    }
  }
  formError.value = '';
  return true;
}

// ── 构建请求体 ────────────────────────────────────────────
function buildPayload() {
  // scopes 文本 → 数组
  const scopes = scopesText.value
    .split(/\s+/)
    .map((s) => s.trim())
    .filter(Boolean);

  // 角色映射列表 → 对象
  const roleMapping = {};
  for (const rm of roleMappings.value) {
    const g = (rm.idpGroup || '').trim();
    const r = (rm.systemRole || '').trim();
    if (g && r) roleMapping[g] = r;
  }

  // 构建协议特定 config（只发送对应协议的字段 + 敏感字段处理）
  const config = {};
  if (form.protocol === 'oidc') {
    config.issuer_url = form.config.issuer_url.trim();
    config.authorize_url = form.config.authorize_url.trim();
    config.token_url = form.config.token_url.trim();
    config.userinfo_url = form.config.userinfo_url.trim();
    config.client_id = form.config.client_id.trim();
    // 编辑时留空表示不修改：不发送 client_secret
    if (form.config.client_secret) config.client_secret = form.config.client_secret;
    config.redirect_uri = form.config.redirect_uri.trim();
    config.scopes = scopes.length ? scopes : ['openid', 'profile', 'email'];
    config.use_pkce = form.config.use_pkce;
  } else {
    config.sp_entity_id = form.config.sp_entity_id.trim();
    config.entity_id = form.config.entity_id.trim();
    config.sso_url = form.config.sso_url.trim();
    config.slo_url = form.config.slo_url.trim();
    config.acs_url = form.config.acs_url.trim();
    if (form.config.x509_cert) config.x509_cert = form.config.x509_cert.trim();
    config.name_id_format = form.config.name_id_format;
    config.want_signed = form.config.want_signed;
  }

  return {
    name: form.name.trim(),
    protocol: form.protocol,
    tenant_id: form.tenant_id.trim(),
    enabled: form.enabled,
    auto_redirect: form.auto_redirect,
    config,
    attribute_mapping: {
      external_id: form.attribute_mapping.external_id.trim() || 'sub',
      email: form.attribute_mapping.email.trim() || 'email',
      display_name: form.attribute_mapping.display_name.trim() || 'name',
      roles: form.attribute_mapping.roles.trim() || 'groups',
      tenant_id: form.attribute_mapping.tenant_id.trim() || 'tid',
      role_mapping: roleMapping,
    },
  };
}

async function saveProvider() {
  if (!validate()) return;
  saving.value = true;
  formError.value = '';
  try {
    const payload = buildPayload();
    if (isEditing.value) {
      await api.put(`/api/v1/sso/providers/${editingId.value}`, payload);
    } else {
      await api.post('/api/v1/sso/providers', payload);
    }
    toast.success(t('view.sso.saved'));
    closeDialog();
    await load();
  } catch (e) {
    formError.value = e.message || t('view.sso.saveFailed');
  } finally {
    saving.value = false;
  }
}

// ── 测试连接 ──────────────────────────────────────────────
async function testProvider(p) {
  testingId.value = p.id;
  try {
    const d = await api.post(`/api/v1/sso/providers/${p.id}/test`, {});
    if (d.reachable) {
      const latency = d.details && typeof d.details.latency_ms === 'number' ? ` (${d.details.latency_ms}ms)` : '';
      toast.success(t('view.sso.testSuccess') + latency);
    } else {
      toast.error((d.error || t('view.sso.testFailed')));
    }
  } catch (e) {
    toast.error(e.message || t('view.sso.testFailed'));
  } finally {
    testingId.value = null;
  }
}

// ── 启用/禁用 ─────────────────────────────────────────────
async function toggleProvider(p) {
  try {
    await api.put(`/api/v1/sso/providers/${p.id}`, { enabled: !p.enabled });
    toast.success(!p.enabled ? t('common.enable') : t('common.disable'));
    await load();
  } catch (e) {
    toast.error(e.message || t('view.sso.saveFailed'));
  }
}

// ── 删除 ──────────────────────────────────────────────────
async function deleteProvider(p) {
  if (typeof window !== 'undefined' && window.confirm && !window.confirm(t('view.sso.confirmDelete'))) return;
  try {
    await api.delete(`/api/v1/sso/providers/${p.id}`);
    toast.success(t('view.sso.deleted'));
    await load();
  } catch (e) {
    toast.error(e.message || t('view.sso.saveFailed'));
  }
}

// ── SAML SP Metadata 导出 ─────────────────────────────────
async function downloadMetadata(p) {
  if (p.protocol !== 'saml') {
    toast.warn(t('view.sso.metadata.samlOnly'));
    return;
  }
  try {
    const res = await fetch(`/api/v1/sso/providers/${p.id}/metadata`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const xml = await res.text();
    // 触发浏览器下载
    const blob = new Blob([xml], { type: 'application/xml' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `sp-metadata-${p.id}.xml`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    toast.success(t('view.sso.metadata.exported'));
  } catch (e) {
    toast.error(e.message || t('view.sso.metadata.exportFailed'));
  }
}

onMounted(load);
</script>

<style scoped>
.sso-page { display: flex; flex-direction: column; }

/* ── 表格 ─────────────────────────────────────────────── */
.sso-table {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r-lg);
  overflow: hidden;
}
.sso-row {
  display: grid;
  grid-template-columns: 1.6fr 0.8fr 0.9fr 0.7fr 0.9fr 1.4fr;
  align-items: center;
  padding: 10px 14px;
  border-bottom: 1px solid var(--border);
  font-size: 13px;
}
.sso-row:last-child { border-bottom: none; }
.sso-row--head {
  background: var(--surface-2);
  font-size: 11px;
  font-weight: 700;
  color: var(--text-faint);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}
.sso-cell { padding: 0 4px; }
.sso-cell--name { display: flex; align-items: center; gap: 8px; }
.sso-name { font-weight: 600; color: var(--text); }
.sso-tenant { font-size: 11px; }
.sso-cell--actions { display: flex; gap: 6px; justify-content: flex-end; }
.sso-status { display: inline-flex; align-items: center; gap: 4px; font-size: 12px; }
.sso-status.is-on { color: var(--success); }
.sso-status.is-off { color: var(--text-faint); }

/* ── 按钮 ─────────────────────────────────────────────── */
.btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  background: var(--surface-2);
  color: var(--text);
  border: 1px solid var(--border);
  border-radius: var(--r-md);
  padding: 7px 12px;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  transition: opacity var(--motion) var(--ease), background var(--motion) var(--ease);
}
.btn:hover { background: var(--surface-3); }
.btn--primary {
  background: var(--brand);
  color: var(--brand-contrast);
  border: none;
}
.btn--primary:hover { opacity: 0.9; }
.btn--primary:disabled { opacity: 0.5; cursor: not-allowed; }
.btn--sm { padding: 4px 8px; font-size: 11px; }
.btn-icon {
  display: grid;
  place-items: center;
  width: 28px;
  height: 28px;
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: var(--r-sm);
  color: var(--text-muted);
  cursor: pointer;
  transition: color var(--motion) var(--ease), border-color var(--motion) var(--ease);
}
.btn-icon:hover { color: var(--text); border-color: var(--border-strong); }
.btn-icon:disabled { opacity: 0.5; cursor: not-allowed; }
.btn-icon--danger:hover { color: var(--fail); border-color: var(--fail); }

/* ── 对话框 ───────────────────────────────────────────── */
.sso-dialog-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: var(--z-modal, 200);
  padding: 16px;
}
.sso-dialog {
  position: relative;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r-lg);
  padding: 24px;
  width: 100%;
  max-width: 560px;
  max-height: calc(100vh - 32px);
  overflow-y: auto;
  box-shadow: var(--shadow-lg);
}
.sso-dialog-close {
  position: absolute;
  top: 12px;
  right: 12px;
  display: grid;
  place-items: center;
  width: 28px;
  height: 28px;
  background: transparent;
  border: none;
  border-radius: var(--r-sm);
  color: var(--text-muted);
  cursor: pointer;
  transition: color var(--motion) var(--ease), background var(--motion) var(--ease);
}
.sso-dialog-close:hover { color: var(--text); background: var(--surface-2); }
.sso-dialog h3 { margin: 0 0 16px; font-size: 16px; color: var(--text); }

/* ── 表单 ─────────────────────────────────────────────── */
.sso-form { display: flex; flex-direction: column; gap: 12px; }
.sso-form label { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--text-muted); }
.sso-label { font-weight: 600; }
.sso-input {
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: var(--r-md);
  padding: 8px 10px;
  color: var(--text);
  font-size: 13px;
  font-family: inherit;
}
.sso-input:focus { outline: none; border-color: var(--brand); }
.sso-input:disabled { opacity: 0.6; cursor: not-allowed; }
.sso-textarea { resize: vertical; font-family: var(--font-mono, monospace); font-size: 12px; }
.req { color: var(--fail); font-style: normal; margin-left: 2px; }

.sso-toggles { display: flex; gap: 20px; }
.sso-toggle {
  display: inline-flex;
  flex-direction: row;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  cursor: pointer;
}
.sso-toggle input { margin: 0; width: 16px; height: 16px; }

/* ── fieldset ─────────────────────────────────────────── */
.sso-fieldset {
  border: 1px solid var(--border);
  border-radius: var(--r-md);
  padding: 14px;
  margin: 4px 0;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.sso-fieldset legend {
  font-size: 12px;
  font-weight: 700;
  color: var(--brand-strong);
  padding: 0 6px;
}
.sso-fieldset-desc { font-size: 11px; color: var(--text-faint); margin: 0; }

/* ── 角色映射表 ───────────────────────────────────────── */
.sso-role-mapping { display: flex; flex-direction: column; gap: 8px; }
.sso-role-mapping-rows { display: flex; flex-direction: column; gap: 6px; }
.sso-role-mapping-row {
  display: grid;
  grid-template-columns: 1fr auto 1fr auto;
  align-items: center;
  gap: 6px;
}
.sso-role-mapping-arrow { color: var(--text-faint); font-size: 14px; }
.sso-role-mapping-empty { font-size: 11px; color: var(--text-faint); margin: 0; }

/* ── 对话框底部 ───────────────────────────────────────── */
.sso-dialog-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 16px; }
.sso-form-error { color: var(--fail); font-size: 12px; margin-top: 8px; }

/* ── 响应式 ───────────────────────────────────────────── */
@media (max-width: 760px) {
  .sso-row { grid-template-columns: 1.4fr 0.7fr 0.8fr 1.2fr; }
  .sso-cell--redirect, .sso-cell--created { display: none; }
}
</style>