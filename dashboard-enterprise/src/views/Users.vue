<template>
  <div class="users-view">
    <PageHeader>
      <template #badges>
        <span class="users-count" v-if="users.length">{{ users.length }}</span>
      </template>
      <button class="btn-primary" @click="openRegister" v-if="isAdmin">
        <AppIcon name="plus" :size="14" /> {{ t('users.registerUser') }}
      </button>
    </PageHeader>

    <!-- 非管理员提示 -->
    <div v-if="!isAdmin" class="users-locked">
      <AppIcon name="shield" :size="32" />
      <p>{{ t('topbar.role.admin') }} {{ t('common.required') || 'required' }}</p>
    </div>

    <!-- 用户列表 -->
    <div v-else class="users-list">
      <div v-if="loading" class="users-loading">{{ t('common.loading') }}</div>
      <div v-else-if="!users.length" class="users-empty">{{ t('users.noUsers') }}</div>
      <div v-else class="users-table">
        <div class="users-row users-row--head">
          <div class="users-cell users-cell--avatar">#</div>
          <div class="users-cell users-cell--name">{{ t('users.username') }}</div>
          <div class="users-cell users-cell--roles">{{ t('users.roles') }}</div>
          <div class="users-cell users-cell--created">{{ t('users.created') }}</div>
          <div class="users-cell users-cell--login">{{ t('users.lastLogin') }}</div>
          <div class="users-cell users-cell--actions">{{ t('common.actions') }}</div>
        </div>
        <div v-for="u in users" :key="u.username" class="users-row">
          <div class="users-cell users-cell--avatar">
            <div class="users-avatar">{{ getInitial(u.username) }}</div>
          </div>
          <div class="users-cell users-cell--name">
            <span class="users-uname">{{ u.username }}</span>
            <span v-if="u.username === currentName" class="users-self">me</span>
          </div>
          <div class="users-cell users-cell--roles">
            <span v-for="r in (u.roles || [])" :key="r" class="users-role" :class="'users-role--' + r">{{ r }}</span>
          </div>
          <div class="users-cell users-cell--created">{{ formatDate(u.created_at) }}</div>
          <div class="users-cell users-cell--login">{{ u.last_login ? formatDate(u.last_login) : '—' }}</div>
          <div class="users-cell users-cell--actions">
            <button class="btn-icon" @click="openEdit(u)" :title="t('common.edit')">
              <AppIcon name="gear" :size="14" />
            </button>
            <button
              v-if="u.username !== 'admin' && u.username !== currentName"
              class="btn-icon btn-icon--danger"
              @click="confirmDelete(u)"
              :title="t('users.deregisterUser')"
            >
              <AppIcon name="trash" :size="14" />
            </button>
          </div>
        </div>
      </div>
    </div>

    <!-- 注册/编辑 弹窗 -->
    <div v-if="dialogOpen" class="users-dialog-overlay" v-modal-a11y @click.self="closeDialog" @modal:escape="closeDialog">
      <div class="users-dialog">
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
            <span>{{ t('users.password') }} ({{ t('common.empty') || 'leave empty to keep' }})</span>
            <input v-model="form.password" type="password" autocomplete="new-password" />
          </label>
          <label>
            <span>{{ t('users.roles') }}</span>
            <div class="users-roles-pick">
              <label v-for="r in roleOptions" :key="r" class="users-role-chip">
                <input type="checkbox" :value="r" v-model="form.roles" />
                <span>{{ r }}</span>
              </label>
            </div>
          </label>
        </div>
        <div class="users-dialog-actions">
          <button class="btn-secondary" @click="closeDialog">{{ t('common.cancel') }}</button>
          <button class="btn-primary" @click="submitForm" :disabled="submitting">
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
import PageHeader from '../components/PageHeader.vue';
import AppIcon from '../components/AppIcon.vue';
import { useI18n } from '../i18n/index.js';
import { useApiStore } from '../stores/api.js';

const { t } = useI18n();
const api = useApiStore();

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
        formError.value = t('users.username') + ' / ' + t('users.password') + ' required';
        submitting.value = false;
        return;
      }
      const d = await api.post('/api/auth/register', {
        username: form.value.username,
        password: form.value.password,
        roles: form.value.roles,
      });
      if (d.status !== 'ok') formError.value = d.error || 'Failed';
    } else {
      const body = { roles: form.value.roles };
      if (form.value.password) body.password = form.value.password;
      const d = await api.put('/api/auth/users/' + encodeURIComponent(form.value.username), body);
      if (d.status !== 'ok') formError.value = d.error || 'Failed';
    }
    if (!formError.value) {
      closeDialog();
      await fetchUsers();
    }
  } catch (e) {
    formError.value = e.message || 'Network error';
  } finally {
    submitting.value = false;
  }
}

async function confirmDelete(u) {
  if (!window.confirm(t('users.confirmDelete'))) return;
  try {
    await api.delete('/api/auth/users/' + encodeURIComponent(u.username));
    await fetchUsers();
  } catch (e) {
    window.alert(e.message || 'Failed');
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
  border-radius: var(--r-full); font-size: 11px; font-weight: 700;
}

.btn-primary {
  display: inline-flex; align-items: center; gap: 5px;
  background: var(--brand); color: #fff; border: none;
  border-radius: var(--r-md); padding: 7px 12px; font-size: 12px; font-weight: 600;
  cursor: pointer; transition: opacity var(--motion) var(--ease);
}
.btn-primary:hover { opacity: .9; }
.btn-primary:disabled { opacity: .5; cursor: not-allowed; }
.btn-secondary {
  background: var(--surface-2); color: var(--text); border: 1px solid var(--border);
  border-radius: var(--r-md); padding: 7px 12px; font-size: 12px; font-weight: 600;
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
  display: flex; flex-direction: column; align-items: center; gap: 12px;
  padding: 60px 20px; color: var(--text-faint); text-align: center;
}

.users-list { margin-top: var(--sp-4); }

.users-loading, .users-empty {
  padding: 40px; text-align: center; color: var(--text-muted);
  background: var(--surface); border: 1px solid var(--border); border-radius: var(--r-lg);
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
  font-size: 13px;
}
.users-row:last-child { border-bottom: none; }
.users-row--head {
  background: var(--surface-2);
  font-size: 11px; font-weight: 700; color: var(--text-faint);
  text-transform: uppercase; letter-spacing: .05em;
}
.users-cell { padding: 0 4px; }
.users-cell--name { display: flex; align-items: center; gap: 6px; }
.users-cell--actions { display: flex; gap: 6px; justify-content: flex-end; }

.users-avatar {
  width: 30px; height: 30px; border-radius: var(--r-full);
  background: linear-gradient(135deg, var(--brand), var(--chart-6));
  color: #fff; font-size: 12px; font-weight: 700;
  display: grid; place-items: center;
}
.users-uname { font-weight: 600; color: var(--text); }
.users-self {
  font-size: 9px; padding: 1px 5px; border-radius: var(--r-sm);
  background: var(--brand-soft); color: var(--brand-strong); font-weight: 600;
}

.users-role {
  display: inline-block; padding: 1px 6px; margin-right: 4px;
  border-radius: var(--r-sm); font-size: 10px; font-weight: 600;
  background: var(--surface-3); color: var(--text-muted);
}
.users-role--admin { background: var(--fail-soft, rgba(239,68,68,.16)); color: var(--fail, #f85149); }
.users-role--superadmin { background: var(--brand-soft, rgba(168,85,247,.18)); color: var(--chart-6, #a78bfa); }
.users-role--operator, .users-role--write { background: var(--info-soft, rgba(56,189,248,.16)); color: var(--info, #38bdf8); }
.users-role--read { background: var(--border-subtle, rgba(148,163,184,.16)); color: var(--text-muted, #6e7686); }

/* 弹窗 */
.users-dialog-overlay {
  position: fixed; inset: 0; background: rgba(0,0,0,.5);
  display: flex; align-items: center; justify-content: center;
  z-index: var(--z-modal, 200);
}
.users-dialog {
  background: var(--surface); border: 1px solid var(--border);
  border-radius: var(--r-lg); padding: 24px;
  width: calc(100% - 32px); max-width: 440px;
  box-shadow: var(--shadow-lg);
}
.users-dialog h3 { margin: 0 0 16px; font-size: 16px; color: var(--text); }
.users-form { display: flex; flex-direction: column; gap: 12px; }
.users-form label { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--text-muted); }
.users-form input[type="text"], .users-form input[type="password"] {
  background: var(--bg); border: 1px solid var(--border);
  border-radius: var(--r-md); padding: 8px 10px; color: var(--text); font-size: 13px;
}
.users-form input:focus { outline: none; border-color: var(--brand); }
.users-form input:disabled { opacity: .6; }

.users-roles-pick { display: flex; flex-wrap: wrap; gap: 6px; }
.users-role-chip {
  display: inline-flex; align-items: center; gap: 4px;
  padding: 4px 8px; background: var(--surface-2); border: 1px solid var(--border);
  border-radius: var(--r-sm); font-size: 11px; cursor: pointer; flex-direction: row;
}
.users-role-chip input { margin: 0; }

.users-dialog-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 16px; }
.users-form-error { color: var(--fail); font-size: 12px; margin-top: 8px; }

@media (max-width: 700px) {
  .users-row { grid-template-columns: 40px 1fr 1fr 60px; }
  .users-cell--created, .users-cell--login { display: none; }
}
</style>
