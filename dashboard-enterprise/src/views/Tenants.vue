<template>
  <div class="tenant-page">
    <ListPageLayout
      :loading="loading"
      :error="error"
      :empty="!tenants.length"
      :error-title="t('view.tenants.loadError')"
      :empty-title="t('view.tenants.noTenants')"
      :empty-desc="t('view.tenants.noTenantsDesc')"
      :loading-lines="3"
    >
      <template #badges>
        <Badge tone="brand">{{ t('view.tenants.enterprise') }}</Badge>
      </template>
      <template #actions>
        <button class="btn btn--primary" @click="openCreate">
          <AppIcon name="building" :size="15" /> {{ t('view.tenants.createTenant') }}
        </button>
      </template>

      <template #content>
        <div class="tenant-grid">
          <div v-for="tenant in tenants" :key="tenant.tenant_id" class="tenant-card" :class="{ suspended: tenant.status === 'suspended' }">
            <div class="tenant-card__head">
              <div class="tenant-id">
                <h3>{{ tenant.name || tenant.tenant_id }}</h3>
                <span class="muted mono">{{ tenant.tenant_id }}</span>
              </div>
              <Badge :tone="statusTone(tenant.status)">{{ tenant.status || t('view.tenants.unknown') }}</Badge>
            </div>
            <div class="tenant-meta">
              <span class="muted">{{ t('view.tenants.plan') }}</span>
              <b>{{ tenant.plan || '—' }}</b>
            </div>
            <div v-if="hasQuota(tenant)" class="quota">
              <div v-for="q in quotaUsage(tenant)" :key="q.name" class="quota-row">
                <span class="quota-name">{{ q.name }}</span>
                <div class="bar"><div class="bar-fill" :class="{ 'is-high': q.pct > 80 }" :style="{ width: q.pct + '%' }"></div></div>
                <span class="quota-val">{{ q.current }} / {{ q.max || '∞' }}</span>
              </div>
            </div>
            <div class="tenant-actions">
              <button v-if="tenant.status === 'suspended'" class="btn btn--sm" @click="activate(tenant.tenant_id)">{{ t('view.tenants.activate') }}</button>
              <button v-if="tenant.status === 'active'" class="btn btn--sm btn--warn" @click="suspend(tenant.tenant_id)">{{ t('view.tenants.suspend') }}</button>
              <button class="btn btn--sm btn--danger" @click="remove(tenant.tenant_id)">{{ t('common.delete') }}</button>
            </div>
          </div>
        </div>
      </template>
    </ListPageLayout>

    <div v-if="showCreate" v-modal-a11y class="modal-overlay" @click.self="showCreate = false" @modal:escape="showCreate = false">
      <div class="modal">
        <h3>{{ t('view.tenants.createTenant') }}</h3>
        <label>{{ t('view.tenants.tenantId') }}</label>
        <input v-model="newTenant.tenant_id" class="input" placeholder="acme-corp" />
        <label>{{ t('common.name') }}</label>
        <input v-model="newTenant.name" class="input" placeholder="Acme Corporation" />
        <label>{{ t('view.tenants.plan') }}</label>
        <select v-model="newTenant.plan" class="input">
          <option value="starter">{{ t('view.tenants.planStarter') }}</option>
          <option value="pro">{{ t('view.tenants.planPro') }}</option>
          <option value="enterprise">{{ t('view.tenants.planEnterprise') }}</option>
        </select>
        <div class="modal-actions">
          <button class="btn" @click="showCreate = false">{{ t('common.cancel') }}</button>
          <button class="btn btn--primary" :disabled="saving" @click="createTenant">
            {{ saving ? t('view.tenants.creating') : t('view.tenants.create') }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue';
import { useApiStore } from '../stores/api.js';
import { useToast } from '../composables/useToast.js';
import { useI18n } from '../i18n';
import Badge from '../components/Badge.vue';
import ListPageLayout from '../components/ListPageLayout.vue';
import AppIcon from '../components/AppIcon.vue';

const { t } = useI18n();

const api = useApiStore();
const toast = useToast();

const tenants = ref([]);
const loading = ref(true);
const error = ref('');
const showCreate = ref(false);
const saving = ref(false);
const newTenant = ref({ tenant_id: '', name: '', plan: 'starter' });

function statusTone(s) {
  if (s === 'active') return 'success';
  if (s === 'suspended') return 'fail';
  if (s === 'trial') return 'info';
  return 'neutral';
}
function hasQuota(t) {
  return !!(t.quota || t.usage);
}
function quotaUsage(t) {
  const q = t.quota || {};
  const u = t.usage || {};
  return [
    { name: 'API Calls', current: u.api_calls_today ?? 0, max: q.max_api_calls_per_day ?? 0 },
    { name: 'Storage', current: Math.round(u.storage_mb ?? 0), max: q.max_storage_mb ?? 0 },
    { name: 'Agents', current: u.active_agents ?? 0, max: q.max_agents ?? 0 },
  ].map((i) => ({ ...i, pct: i.max ? Math.min(100, Math.round((i.current / i.max) * 100)) : 0 }));
}

async function load() {
  loading.value = true;
  try {
    const d = await api.get('/api/tenant/list');
    tenants.value = d.tenants || [];
    error.value = '';
  } catch (e) {
    error.value = e.message || t('view.tenants.loadFailed');
    tenants.value = [];
  } finally {
    loading.value = false;
  }
}

function openCreate() {
  newTenant.value = { tenant_id: '', name: '', plan: 'starter' };
  showCreate.value = true;
}
async function createTenant() {
  if (!newTenant.value.tenant_id.trim() || !newTenant.value.name.trim()) {
    toast.warn(t('view.tenants.idAndNameRequired'));
    return;
  }
  saving.value = true;
  try {
    await api.post('/api/tenant/create', { ...newTenant.value, tenant_id: newTenant.value.tenant_id.trim(), name: newTenant.value.name.trim() });
    toast.success(`Tenant “${newTenant.value.name}” created`);
    showCreate.value = false;
    await load();
  } catch (e) {
    toast.error(e.message || t('view.tenants.createFailed'));
  } finally {
    saving.value = false;
  }
}
async function suspend(id) {
  try {
    await api.post(`/api/tenant/${id}/suspend`, {});
    toast.success(`Suspended ${id}`);
    await load();
  } catch (e) {
    toast.error(e.message || t('view.tenants.suspendFailed'));
  }
}
async function activate(id) {
  try {
    await api.post(`/api/tenant/${id}/activate`, {});
    toast.success(`Activated ${id}`);
    await load();
  } catch (e) {
    toast.error(e.message || t('view.tenants.activateFailed'));
  }
}
async function remove(id) {
  if (typeof confirm === 'function' && !confirm(t('view.tenants.deleteConfirm', { id }))) return;
  try {
    await api.delete(`/api/tenant/${id}`);
    toast.success(t('view.tenants.deleted', { id }));
    await load();
  } catch (e) {
    toast.error(e.message || t('view.tenants.deleteFailed'));
  }
}

onMounted(load);
</script>

<style scoped>
.tenant-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: var(--sp-4); }
</style>
