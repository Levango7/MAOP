<template>
  <div class="rbac-page">
    <ListPageLayout>
      <template #badges>
        <Badge tone="brand">{{ t('view.rbac.enterprise') }}</Badge>
      </template>
      <template #actions>
        <button class="btn btn--primary" @click="openGrant">
          <AppIcon name="shield" :size="15" /> {{ t('view.rbac.grantRole') }}
        </button>
      </template>
      <template #content>
        <Card :title="t('view.rbac.roles')" icon="shield" :margin-bottom="16">
          <div v-if="!rolesLoading" class="role-grid">
            <div v-for="r in roles" :key="r.role" class="role-card">
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
          <div v-else class="role-grid">
            <Skeleton v-for="n in 4" :key="n" height="92px" />
          </div>
          <p v-if="rolesError" class="inline-error">{{ rolesError }}</p>
        </Card>

        <Card :title="t('view.rbac.activeGrants')" icon="clipboard" :margin-bottom="16">
          <div v-if="grants.length" class="grant-list">
            <div v-for="g in grants" :key="g.__key" class="grant-row">
              <div class="grant-meta">
                <span class="grant-user">{{ g.user_id }}</span>
                <Badge tone="brand">{{ g.role }}</Badge>
                <span v-if="g.tenant_id" class="muted">{{ t('view.rbac.tenantLabel') }} {{ g.tenant_id }}</span>
                <span v-if="g.granted_by" class="muted">{{ t('view.rbac.grantedBy') }} {{ g.granted_by }}</span>
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

        <Card :title="t('view.rbac.permissionCatalog')" icon="lock" :margin-bottom="16">
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
      </template>
    </ListPageLayout>

    <div v-if="showGrant" v-modal-a11y class="modal-overlay" @click.self="showGrant = false" @modal:escape="showGrant = false">
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
import { ref, computed, onMounted } from 'vue';
import { useApiStore } from '../stores/api.js';
import { useToast } from '../composables/useToast.js';
import { useI18n } from '../i18n';
import ListPageLayout from '../components/ListPageLayout.vue';
import Card from '../components/Card.vue';
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
const grantsError = ref('');

const permissions = ref([]);
const permsLoading = ref(true);

const showGrant = ref(false);
const saving = ref(false);
const newGrant = ref({ user_id: '', role: '', tenant_id: '' });

// P1 fix: permCols 改为 computed 使其响应式。原实现是普通数组，当 i18n locale
// 切换或权限列需要动态更新时 UI 不刷新。computed 依赖 t()（响应式 locale），
// locale 变化时 permCols 自动重算并触发 DataTable 重渲染。
const permCols = computed(() => [
  { key: 'value', label: t('view.rbac.permission') },
  { key: 'name', label: t('view.rbac.constant') },
]);

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
    toast.warn(t('view.rbac.userIdRequired'));
    return;
  }
  saving.value = true;
  try {
    await api.post('/api/rbac/grant', {
      user_id: newGrant.value.user_id.trim(),
      role: newGrant.value.role,
      tenant_id: newGrant.value.tenant_id.trim(),
    });
    toast.success(t('view.rbac.granted', { role: newGrant.value.role, user: newGrant.value.user_id }));
    showGrant.value = false;
    await loadGrants();
  } catch (e) {
    toast.error(e.message || t('view.rbac.grantFailed'));
  } finally {
    saving.value = false;
  }
}
async function revoke(g) {
  // P1 fix: 撤销权限是破坏性操作，需先弹确认对话框避免误点击直接撤销。
  // 使用 window.confirm 与项目其他视图（Users/Tenants/Audit/ApiKeys 等）的
  // 确认模式保持一致。取消则直接返回，不调用后端。
  const confirmMsg = t('view.rbac.revokeConfirm', { role: g.role, user: g.user_id });
  if (typeof window !== 'undefined' && window.confirm && !window.confirm(confirmMsg)) return;
  try {
    await api.post('/api/rbac/revoke', { user_id: g.user_id, role: g.role, tenant_id: g.tenant_id || '' });
    toast.success(t('view.rbac.revoked', { role: g.role, user: g.user_id }));
    await loadGrants();
  } catch (e) {
    toast.error(e.message || t('view.rbac.revokeFailed'));
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
