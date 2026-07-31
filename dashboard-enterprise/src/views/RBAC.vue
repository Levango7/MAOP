<template>
  <div class="rbac-page">
    <PageHeader>
      <Badge tone="brand">{{ t('view.rbac.enterprise') }}</Badge>
      <button class="btn btn--primary" @click="openGrant">
        <AppIcon name="shield" :size="15" /> {{ t('view.rbac.grantRole') }}
      </button>
    </PageHeader>

    <Card :title="t('view.rbac.roles')" icon="shield" :marginBottom="16">
      <div class="role-grid" v-if="!rolesLoading">
        <div class="role-card" v-for="r in roles" :key="r.role">
          <div class="role-card__head">
            <AppIcon name="shield" :size="16" />
            <h3>{{ roleLabel(r.role) }}</h3>
            <Badge tone="brand">{{ r.permission_count }} {{ t('view.rbac.perms') }}</Badge>
          </div>
          <div class="perm-wrap">
            <Badge v-for="p in r.permissions" :key="p" tone="neutral">{{ p }}</Badge>
          </div>
        </div>
      </div>
      <div class="role-grid" v-else>
        <Skeleton height="92px" v-for="n in 4" :key="n" />
      </div>
      <p class="inline-error" v-if="rolesError">{{ rolesError }}</p>
    </Card>

    <Card :title="t('view.rbac.activeGrants')" icon="clipboard" :marginBottom="16">
      <div class="grant-list" v-if="grants.length">
        <div class="grant-row" v-for="g in grants" :key="g.__key">
          <div class="grant-meta">
            <span class="grant-user">{{ g.user_id }}</span>
            <Badge tone="brand">{{ g.role }}</Badge>
            <span class="muted" v-if="g.tenant_id">{{ t('view.rbac.tenantLabel') }} {{ g.tenant_id }}</span>
            <span class="muted" v-if="g.granted_by">{{ t('view.rbac.grantedBy') }} {{ g.granted_by }}</span>
          </div>
          <button class="btn btn--sm btn--danger" @click="revoke(g)">{{ t('view.rbac.revoke') }}</button>
        </div>
      </div>
      <EmptyState
        v-else-if="!grantsLoading"
        icon="clipboard"
        :title="t('view.rbac.noGrants')"
        :description="grantsError || t('view.rbac.noGrantsDesc')"
      />
      <Skeleton v-else height="120px" />
    </Card>

    <Card :title="t('view.rbac.permissionCatalog')" icon="lock" :marginBottom="16">
      <DataTable
        v-if="permissions.length"
        :columns="permCols"
        :rows="permissions"
        :loading="permsLoading"
        row-key="value"
        :empty-text="t('view.rbac.noPermissions')"
      />
      <EmptyState v-else-if="!permsLoading" icon="shield" :title="t('view.rbac.noPermissions')" :description="t('view.rbac.noPermissionsDesc')" />
      <Skeleton v-else height="120px" />
    </Card>

    <div class="modal-overlay" v-if="showGrant" @click.self="showGrant = false">
      <div class="modal">
        <h3>{{ t('view.rbac.grantRole') }}</h3>
        <label>{{ t('view.rbac.userId') }}</label>
        <input v-model="newGrant.user_id" class="input" placeholder="user@example.com" />
        <label>{{ t('view.rbac.role') }}</label>
        <select v-model="newGrant.role" class="input">
          <option v-for="r in roles" :key="r.role" :value="r.role">{{ roleLabel(r.role) }}</option>
        </select>
        <label>{{ t('view.rbac.tenantOptional') }}</label>
        <input v-model="newGrant.tenant_id" class="input" placeholder="default" />
        <div class="modal-actions">
          <button class="btn" @click="showGrant = false">{{ t('common.cancel') }}</button>
          <button class="btn btn--primary" :disabled="saving" @click="grantRole">
            {{ saving ? t('view.rbac.granting') : t('view.rbac.grant') }}
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
import Card from '../components/Card.vue';
import PageHeader from '../components/PageHeader.vue';
import Badge from '../components/Badge.vue';
import DataTable from '../components/DataTable.vue';
import Skeleton from '../components/Skeleton.vue';
import EmptyState from '../components/EmptyState.vue';
import AppIcon from '../components/AppIcon.vue';

const { t } = useI18n();

const api = useApiStore();
const toast = useToast();

const roles = ref([]);
const rolesLoading = ref(true);
const rolesError = ref('');

const grants = ref([]);
const grantsLoading = ref(true);

const permissions = ref([]);
const permsLoading = ref(true);

const showGrant = ref(false);
const saving = ref(false);
const newGrant = ref({ user_id: '', role: '', tenant_id: '' });

const permCols = [
  { key: 'value', label: t('view.rbac.permission') },
  { key: 'name', label: t('view.rbac.constant') },
];

function cap(s) {
  if (!s) return '—';
  return String(s).charAt(0).toUpperCase() + String(s).slice(1);
}
function roleLabel(r) {
  if (!r) return '—';
  const key = 'view.rbac.role.' + r;
  const tr = t(key);
  return tr === key ? cap(r) : tr;
}

async function loadRoles() {
  rolesLoading.value = true;
  try {
    const d = await api.get('/api/rbac/roles');
    roles.value = d.roles || [];
  } catch {
    rolesError.value = 'Failed to load roles';
  } finally {
    rolesLoading.value = false;
  }
}
async function loadGrants() {
  grantsLoading.value = true;
  try {
    const d = await api.get('/api/rbac/grants');
    grants.value = (d.grants || []).map((g) => ({ ...g, __key: `${g.user_id}|${g.role}|${g.tenant_id || ''}` }));
  } catch {
    grants.value = [];
  } finally {
    grantsLoading.value = false;
  }
}
async function loadPerms() {
  permsLoading.value = true;
  try {
    const d = await api.get('/api/rbac/permissions');
    permissions.value = d.permissions || [];
  } catch {
    permissions.value = [];
  } finally {
    permsLoading.value = false;
  }
}

function openGrant() {
  newGrant.value = { user_id: '', role: roles.value[0]?.role || '', tenant_id: '' };
  showGrant.value = true;
}

async function grantRole() {
  if (!newGrant.value.user_id.trim() || !newGrant.value.role) {
    toast.warn('User ID and role are required');
    return;
  }
  saving.value = true;
  try {
    await api.post('/api/rbac/grant', {
      user_id: newGrant.value.user_id.trim(),
      role: newGrant.value.role,
      tenant_id: newGrant.value.tenant_id.trim(),
    });
    toast.success(`Granted ${newGrant.value.role} to ${newGrant.value.user_id}`);
    showGrant.value = false;
    await loadGrants();
  } catch (e) {
    toast.error(e.message || 'Grant failed');
  } finally {
    saving.value = false;
  }
}
async function revoke(g) {
  try {
    await api.post('/api/rbac/revoke', { user_id: g.user_id, role: g.role, tenant_id: g.tenant_id || '' });
    toast.success(`Revoked ${g.role} from ${g.user_id}`);
    await loadGrants();
  } catch (e) {
    toast.error(e.message || 'Revoke failed');
  }
}

onMounted(() => {
  loadRoles();
  loadGrants();
  loadPerms();
});
</script>

<style scoped>
</style>
