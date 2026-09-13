<template>
  <div class="users-view">
    <!-- 非管理员提示 -->
    <div v-if="!isAdmin" class="users-locked">
      <AppIcon name="shield" :size="32" />
      <p>{{ t('topbar.role.admin') }} {{ t('common.required') }}</p>
    </div>

    <!-- 管理员视图 -->
    <ListPageLayout
      v-else
      :loading="loading"
      :empty="!users.length"
      :empty-title="t('users.noUsers')"
      :loading-lines="6"
    >
      <template #badges>
        <span v-if="users.length" class="users-count">{{ users.length }}</span>
      </template>
      <template #actions>
        <button class="btn-primary" @click="openRegister">
          <AppIcon name="plus" :size="14" /> {{ t('users.registerUser') }}
        </button>
      </template>
      <template #content>
        <!-- 保留现有的 grid 表格(不支持 DataTable 自定义列渲染) -->
        <div class="users-table" role="table" :aria-label="t('users.title')">
          <div class="users-row users-row--head" role="row">
            <div class="users-cell users-cell--avatar" role="columnheader">#</div>
            <div class="users-cell users-cell--name" role="columnheader">{{ t('users.username') }}</div>
            <div class="users-cell users-cell--roles" role="columnheader">{{ t('users.roles') }}</div>
            <div class="users-cell users-cell--created" role="columnheader">{{ t('users.created') }}</div>
            <div class="users-cell users-cell--login" role="columnheader">{{ t('users.lastLogin') }}</div>
            <div class="users-cell users-cell--actions" role="columnheader">{{ t('common.actions') }}</div>
          </div>
          <div v-for="u in users" :key="u.username" class="users-row" role="row">
            <div class="users-cell users-cell--avatar" role="cell">
              <div class="users-avatar">{{ getInitial(u.username) }}</div>
            </div>
            <div class="users-cell users-cell--name" role="cell">
              <span class="users-uname">{{ u.username }}</span>
              <span v-if="u.username === currentName" class="users-self">{{ t('common.me') }}</span>
            </div>
            <div class="users-cell users-cell--roles" role="cell">
              <span v-for="r in (u.roles || [])" :key="r" class="users-role" :class="'users-role--' + r">{{ r }}</span>
            </div>
            <div class="users-cell users-cell--created" role="cell">{{ formatDate(u.created_at) }}</div>
            <div class="users-cell users-cell--login" role="cell">{{ u.last_login ? formatDate(u.last_login) : '—' }}</div>
            <div class="users-cell users-cell--actions" role="cell">
              <button class="btn-icon" :title="t('common.edit')" :aria-label="t('common.edit')" @click="openEdit(u)">
                <AppIcon name="gear" :size="14" aria-hidden="true" />
              </button>
              <button
                v-if="u.username !== 'admin' && u.username !== currentName"
                class="btn-icon btn-icon--danger"
                :title="t('users.deregisterUser')"
                :aria-label="t('users.deregisterUser')"
                @click="confirmDelete(u)"
              >
                <AppIcon name="trash" :size="14" aria-hidden="true" />
              </button>
            </div>
          </div>
        </div>
      </template>
    </ListPageLayout>

    <!-- 注册/编辑 弹窗 -->
    <div v-if="dialogOpen" v-modal-a11y class="users-dialog-overlay" @click.self="closeDialog" @modal:escape="closeDialog">
      <div class="users-dialog" role="dialog" aria-modal="true">
        <button class="users-dialog-close" type="button" :aria-label="t('common.close')" @click="closeDialog">
          <AppIcon name="x" :size="16" aria-hidden="true" />
        </button>
        <h3>{{ dialogMode === 'register' ? t('users.registerUser') : t('users.updateProfile') }}</h3>
        <div class="users-form">
          <label>
            <span>{{ t('users.username') }}</span>
            <input v-model="form.username" type="text" :disabled="dialogMode === 'edit'" />
          </label>
          <label v-if="dialogMode === 'register'">
            <span>{{ t('users.password') }}</span>
            <input v-model="form.password" type="password" autocomplete="new-password" />
          </label>
          <label v-else>
            <span>{{ t('users.password') }} ({{ t('common.empty') }})</span>
            <input v-model="form.password" type="password" autocomplete="new-password" />
          </label>
          <label>
            <span>{{ t('users.roles') }}</span>
            <div class="users-roles-pick">
              <label v-for="r in roleOptions" :key="r" class="users-role-chip">
                <input v-model="form.roles" type="checkbox" :value="r" />
                <span>{{ r }}</span>
              </label>
            </div>
          </label>
        </div>
        <div class="users-dialog-actions">
          <button class="btn-secondary" @click="closeDialog">{{ t('common.cancel') }}</button>
          <button class="btn-primary" :disabled="submitting" @click="submitForm">
            {{ submitting ? t('common.loading') : (dialogMode === 'register' ? t('common.submit') : t('common.save')) }}
          </button>
        </div>
        <p v-if="formError" class="users-form-error">{{ formError }}</p>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue';
import ListPageLayout from '../components/ListPageLayout.vue';
import AppIcon from '../components/AppIcon.vue';
import { useI18n } from '../i18n';
import { useApiStore } from '../stores/api.js';
import { useToast } from '../composables/useToast.js';
import { useConfirm } from '../composables/useConfirm.js';

const { t } = useI18n();
const api = useApiStore();
const toast = useToast();
const { showConfirm } = useConfirm();


const users = ref([]);
const loading = ref(false);
const dialogOpen = ref(false);
const dialogMode = ref('register'); // 'register' | 'edit'
const submitting = ref(false);
const formError = ref('');
const form = ref({ username: '', password: '', roles: ['read'] });

const roleOptions = ['admin', 'superadmin', 'operator', 'write', 'read'];
const currentName = computed(() => { try { return localStorage.getItem('maop_user') || ''; } catch { return ''; } });
const isAdmin = computed(() => {
  try {
    const roles = JSON.parse(localStorage.getItem('maop_roles') || '[]');
    if (Array.isArray(roles) && roles.some((r) => r === 'admin' || r === 'superadmin')) return true;
  } catch { /* ignore */ }
  return currentName.value === 'admin';
});

function getInitial(n) {
  if (!n) return '?';
  if (/[\u4e00-\u9fff]/.test(n)) return n.charAt(n.length - 1);
  return n.charAt(0).toUpperCase();
}

function formatDate(s) {
  if (!s) return '—';
  try { return new Date(s).toLocaleString(); } catch { return s; }
}

async function fetchUsers() {
  if (!isAdmin.value) return;
  loading.value = true;
  try {
    const d = await api.get('/api/auth/users');
    if (d && d.status === 'ok') users.value = d.users || [];
  } catch (e) {
    console.warn('[users] fetch failed', e);
  } finally {
    loading.value = false;
  }
}

function openRegister() {
  dialogMode.value = 'register';
  form.value = { username: '', password: '', roles: ['read'] };
  formError.value = '';
  dialogOpen.value = true;
}

function openEdit(u) {
  dialogMode.value = 'edit';
  form.value = { username: u.username, password: '', roles: [...(u.roles || ['read'])] };
  formError.value = '';
  dialogOpen.value = true;
}

function closeDialog() {
  dialogOpen.value = false;
  formError.value = '';
}

async function submitForm() {
  submitting.value = true;
  formError.value = '';
  try {
    if (dialogMode.value === 'register') {
      if (!form.value.username || !form.value.password) {
        formError.value = t('users.username') + ' / ' + t('users.password') + ' ' + t('common.required');
        submitting.value = false;
        return;
      }
      const d = await api.post('/api/auth/register', {
        username: form.value.username,
        password: form.value.password,
        roles: form.value.roles,
      });
      if (d.status !== 'ok') formError.value = d.error || t('view.users.failed');
    } else {
      const body = { roles: form.value.roles };
      if (form.value.password) body.password = form.value.password;
      const d = await api.put(`/api/auth/users/${encodeURIComponent(form.value.username)}`, body);
      if (d.status !== 'ok') formError.value = d.error || t('view.users.failed');
    }
    if (!formError.value) {
      closeDialog();
      await fetchUsers();
    }
  } catch (e) {
    formError.value = e.message || t('view.users.networkError');
  } finally {
    submitting.value = false;
  }
}

async function confirmDelete(u) {
  const ok = await showConfirm({ message: t('users.confirmDelete'), tone: 'danger' });
  if (!ok) return;
  try {
    await api.delete(`/api/auth/users/${encodeURIComponent(u.username)}`);
    await fetchUsers();
  } catch (e) {
    toast.error(e.message || t('view.users.failed'));
  }
}

onMounted(fetchUsers);
</script>

<style scoped>
.users-view { display: flex; flex-direction: column; }

.users-count {
  display: inline-flex; align-items: center; justify-content: center;
  min-width: 22px; height: 22px; padding: 0 6px;
  background: var(--brand-soft); color: var(--brand-strong);
  border-radius: var(--r-full); font-size: var(--fs-xs); font-weight: 700;
}

.btn-primary {
  display: inline-flex; align-items: center; gap: 5px;
  background: var(--brand); color: var(--brand-contrast); border: none;
  border-radius: var(--r-md); padding: 7px var(--sp-3); font-size: var(--fs-sm); font-weight: 600;
  cursor: pointer; transition: opacity var(--motion) var(--ease);
}
.btn-primary:hover { opacity: .9; }
.btn-primary:disabled { opacity: .5; cursor: not-allowed; }
.btn-secondary {
  background: var(--surface-2); color: var(--text); border: 1px solid var(--border);
  border-radius: var(--r-md); padding: 7px var(--sp-3); font-size: var(--fs-sm); font-weight: 600;
  cursor: pointer;
}
.btn-icon {
  display: grid; place-items: center;
  width: 28px; height: 28px;
  background: var(--surface-2); border: 1px solid var(--border);
  border-radius: var(--r-sm); color: var(--text-muted); cursor: pointer;
  transition: color var(--motion) var(--ease), border-color var(--motion) var(--ease);
}
.btn-icon:hover { color: var(--text); border-color: var(--border-strong); }
.btn-icon--danger:hover { color: var(--fail); border-color: var(--fail); }

.users-locked {
  display: flex; flex-direction: column; align-items: center; gap: var(--sp-3);
  padding: 60px var(--sp-5); color: var(--text-faint); text-align: center;
}

.users-table {
  background: var(--surface); border: 1px solid var(--border); border-radius: var(--r-lg);
  overflow: hidden;
}
.users-row {
  display: grid;
  grid-template-columns: 50px 1.5fr 1.5fr 1fr 1fr 90px;
  align-items: center;
  padding: 10px 14px;
  border-bottom: 1px solid var(--border);
  font-size: var(--fs-base);
}
.users-row:last-child { border-bottom: none; }
.users-row--head {
  background: var(--surface-2);
  font-size: var(--fs-xs); font-weight: 700; color: var(--text-faint);
  text-transform: uppercase; letter-spacing: .05em;
}
.users-cell { padding: 0 var(--sp-1); }
.users-cell--name { display: flex; align-items: center; gap: 6px; }
.users-cell--actions { display: flex; gap: 6px; justify-content: flex-end; }

.users-avatar {
  width: 30px; height: 30px; border-radius: var(--r-full);
  background: var(--brand);
  color: var(--brand-contrast); font-size: var(--fs-sm); font-weight: 700;
  display: grid; place-items: center;
}
.users-uname { font-weight: 600; color: var(--text); }
.users-self {
  font-size: var(--fs-3xs); padding: 1px 5px; border-radius: var(--r-sm);
  background: var(--brand-soft); color: var(--brand-strong); font-weight: 600;
}

.users-role {
  display: inline-block; padding: 1px 6px; margin-right: var(--sp-1);
  border-radius: var(--r-sm); font-size: var(--fs-2xs); font-weight: 600;
  background: var(--surface-3); color: var(--text-muted);
}
.users-role--admin { background: var(--fail-soft); color: var(--fail); }
.users-role--superadmin { background: var(--brand-soft); color: var(--chart-6); }
.users-role--operator, .users-role--write { background: var(--info-soft); color: var(--info); }
.users-role--read { background: var(--surface-3); color: var(--text-muted); }

/* 弹窗 */
.users-dialog-overlay {
  position: fixed; inset: 0; background: var(--overlay-scrim);
  display: flex; align-items: center; justify-content: center;
  z-index: var(--z-modal);
}
.users-dialog {
  position: relative;
  background: var(--surface); border: 1px solid var(--border);
  border-radius: var(--r-lg); padding: var(--sp-6);
  width: calc(100% - 32px); max-width: 440px;
  box-shadow: var(--shadow-lg);
}
.users-dialog-close {
  position: absolute; top: 12px; right: 12px;
  display: grid; place-items: center;
  width: 28px; height: 28px;
  background: transparent; border: none; border-radius: var(--r-sm);
  color: var(--text-muted); cursor: pointer;
  transition: color var(--motion) var(--ease), background var(--motion) var(--ease);
}
.users-dialog-close:hover { color: var(--text); background: var(--surface-2); }
.users-dialog h3 { margin: 0 0 var(--sp-4); font-size: var(--fs-lg); color: var(--text); }
.users-form { display: flex; flex-direction: column; gap: var(--sp-3); }
.users-form label { display: flex; flex-direction: column; gap: var(--sp-1); font-size: var(--fs-sm); color: var(--text-muted); }
.users-form input[type="text"], .users-form input[type="password"] {
  background: var(--bg); border: 1px solid var(--border);
  border-radius: var(--r-md); padding: var(--sp-2) 10px; color: var(--text); font-size: var(--fs-base);
}
.users-form input:focus { outline: none; border-color: var(--brand); }
.users-form input:disabled { opacity: .6; }

.users-roles-pick { display: flex; flex-wrap: wrap; gap: 6px; }
.users-role-chip {
  display: inline-flex; align-items: center; gap: var(--sp-1);
  padding: var(--sp-1) var(--sp-2); background: var(--surface-2); border: 1px solid var(--border);
  border-radius: var(--r-sm); font-size: var(--fs-xs); cursor: pointer; flex-direction: row;
}
.users-role-chip input { margin: 0; }

.users-dialog-actions { display: flex; justify-content: flex-end; gap: var(--sp-2); margin-top: var(--sp-4); }
.users-form-error { color: var(--fail); font-size: var(--fs-sm); margin-top: var(--sp-2); }

@media (max-width: 640px) {
  .users-row { grid-template-columns: 40px 1fr 1fr 60px; }
  .users-cell--created, .users-cell--login { display: none; }
}
</style>
