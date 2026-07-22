<template>
  <div>
    <div class="topbar">
      <h1>RBAC Management</h1>
      <span class="badge">Enterprise</span>
      <button class="btn-primary" @click="showGrantModal = true">+ Grant Role</button>
    </div>
    <div class="role-cards">
      <div class="role-card" v-for="role in roleDefinitions" :key="role.name" :style="{ borderTopColor: role.color }">
        <h3>{{ role.icon }} {{ role.name }}</h3>
        <p class="role-desc">{{ role.desc }}</p>
        <div class="perm-tags">
          <span class="perm-tag" v-for="p in role.perms" :key="p">{{ p }}</span>
        </div>
        <div class="role-count">{{ grants.filter(g => g.role === role.name).length }} users</div>
      </div>
    </div>
    <div class="panel">
      <h3>Active Grants</h3>
      <table class="data-table">
        <thead><tr><th>User</th><th>Role</th><th>Tenant</th><th>Granted By</th><th>Actions</th></tr></thead>
        <tbody>
          <tr v-for="g in grants" :key="g.user_id + g.role">
            <td>{{ g.user_id }}</td>
            <td><span class="role-badge">{{ g.role }}</span></td>
            <td>{{ g.tenant_id || '—' }}</td>
            <td>{{ g.granted_by || 'system' }}</td>
            <td><button class="btn-sm btn-danger" @click="revokeGrant(g)">Revoke</button></td>
          </tr>
          <tr v-if="!grants.length"><td colspan="5" class="empty">No grants yet</td></tr>
        </tbody>
      </table>
    </div>
    <div class="modal-overlay" v-if="showGrantModal" @click.self="showGrantModal = false">
      <div class="modal">
        <h3>Grant Role</h3>
        <label>User ID</label><input v-model="newGrant.user_id" class="input" placeholder="user@example.com" />
        <label>Role</label>
        <select v-model="newGrant.role" class="input">
          <option v-for="r in roleDefinitions" :key="r.name" :value="r.name">{{ r.name }}</option>
        </select>
        <label>Tenant (optional)</label><input v-model="newGrant.tenant_id" class="input" placeholder="default" />
        <div class="modal-actions">
          <button class="btn-secondary" @click="showGrantModal = false">Cancel</button>
          <button class="btn-primary" @click="grantRole">Grant</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue';
import { useApiStore } from '../stores/api.js';

const api = useApiStore();
const showGrantModal = ref(false);
const grants = ref([]);
const newGrant = ref({ user_id: '', role: 'viewer', tenant_id: '' });

const roleDefinitions = [
  { name: 'superadmin', icon: '👑', color: '#ef4444', desc: 'Full system access', perms: ['All permissions'] },
  { name: 'admin', icon: '🛡️', color: '#f59e0b', desc: 'Tenant admin, no system-level', perms: ['agents:*', 'config:*', 'rbac:read', 'audit:read'] },
  { name: 'operator', icon: '⚙️', color: '#3b82f6', desc: 'Execute and manage agents', perms: ['agents:read', 'agents:write', 'agents:execute', 'memory:*', 'models:read'] },
  { name: 'viewer', icon: '👁️', color: '#6b7280', desc: 'Read-only access', perms: ['agents:read', 'config:read', 'memory:read', 'models:read', 'cost:read'] },
];

async function loadGrants() {
  try { grants.value = (await api.get('/api/rbac/grants')).grants || []; } catch { grants.value = []; }
}

async function grantRole() {
  try { await api.post('/api/rbac/grant', newGrant.value); showGrantModal.value = false; await loadGrants(); } catch {}
}

async function revokeGrant(g) {
  try { await api.post('/api/rbac/revoke', { user_id: g.user_id, role: g.role, tenant_id: g.tenant_id }); await loadGrants(); } catch {}
}

onMounted(loadGrants);
</script>

<style scoped>
.topbar { display: flex; align-items: center; gap: 12px; margin-bottom: 24px; }
.topbar h1 { font-size: 24px; font-weight: 700; }
.badge { background: #7c3aed; color: #fff; padding: 2px 10px; border-radius: 6px; font-size: 11px; font-weight: 600; }
.btn-primary { margin-left: auto; background: var(--accent); color: #fff; border: none; padding: 8px 16px; border-radius: 8px; cursor: pointer; font-size: 13px; font-weight: 600; }
.btn-secondary { background: var(--bg3); color: var(--text); border: 1px solid var(--border); padding: 8px 16px; border-radius: 8px; cursor: pointer; font-size: 13px; }
.btn-sm { padding: 4px 10px; border: 1px solid var(--border); border-radius: 6px; background: var(--bg3); color: var(--text); font-size: 11px; cursor: pointer; }
.btn-danger { border-color: rgba(239,68,68,.3); color: var(--fail); }
.role-cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 16px; margin-bottom: 24px; }
.role-card { background: var(--bg2); border: 1px solid var(--border); border-radius: var(--radius); padding: 16px; border-top: 3px solid; box-shadow: var(--shadow); }
.role-card h3 { font-size: 16px; margin-bottom: 4px; }
.role-desc { font-size: 12px; color: var(--text3); margin-bottom: 10px; }
.perm-tags { display: flex; flex-wrap: wrap; gap: 4px; margin-bottom: 8px; }
.perm-tag { background: var(--bg3); border: 1px solid var(--border); border-radius: 4px; padding: 1px 6px; font-size: 10px; color: var(--text2); }
.role-count { font-size: 12px; color: var(--accent); }
.panel { background: var(--bg2); border: 1px solid var(--border); border-radius: var(--radius); padding: 20px; box-shadow: var(--shadow); }
.panel h3 { font-size: 14px; font-weight: 600; margin-bottom: 16px; color: var(--text2); }
.data-table { width: 100%; border-collapse: collapse; }
.data-table th, .data-table td { padding: 10px 16px; text-align: left; border-bottom: 1px solid var(--border); font-size: 13px; }
.data-table th { color: var(--text3); font-size: 11px; text-transform: uppercase; letter-spacing: .5px; }
.role-badge { background: color-mix(in srgb, var(--accent) 15%, transparent); color: var(--accent); padding: 2px 8px; border-radius: 4px; font-size: 12px; font-weight: 600; }
.empty { text-align: center; color: var(--text3); padding: 20px; }
.modal-overlay { position: fixed; inset: 0; background: rgba(0,0,0,.4); display: flex; align-items: center; justify-content: center; z-index: 100; }
.modal { background: var(--bg); border: 1px solid var(--border); border-radius: var(--radius); padding: 24px; width: 400px; box-shadow: 0 8px 32px rgba(0,0,0,.15); }
.modal h3 { margin-bottom: 16px; }
.modal label { display: block; font-size: 12px; color: var(--text3); margin: 10px 0 4px; }
.input { width: 100%; padding: 8px 12px; border: 1px solid var(--border); border-radius: 8px; background: var(--bg2); color: var(--text); font-size: 13px; }
.modal-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 20px; }
</style>
