<template>
  <div>
    <div class="topbar">
      <h1>Tenant Management</h1>
      <span class="badge">Enterprise</span>
      <button class="btn-primary" @click="showCreateModal = true">+ Create Tenant</button>
    </div>
    <div class="tenant-grid">
      <div class="tenant-card" v-for="t in tenants" :key="t.tenant_id" :class="{ suspended: t.status === 'suspended' }">
        <div class="tenant-header">
          <h3>{{ t.name }}</h3>
          <span class="status-badge" :class="t.status">{{ t.status }}</span>
        </div>
        <div class="tenant-meta">
          <span>ID: {{ t.tenant_id }}</span>
          <span>Plan: {{ t.plan }}</span>
        </div>
        <div class="quota-bars">
          <div class="quota-item" v-for="q in getQuotaUsage(t)" :key="q.name">
            <span class="quota-name">{{ q.name }}</span>
            <div class="quota-bar"><div class="quota-fill" :style="{ width: q.pct + '%', background: q.pct > 80 ? 'var(--fail)' : 'var(--accent)' }"></div></div>
            <span class="quota-val">{{ q.current }}/{{ q.max }}</span>
          </div>
        </div>
        <div class="tenant-actions">
          <button class="btn-sm" v-if="t.status === 'suspended'" @click="activateTenant(t.tenant_id)">Activate</button>
          <button class="btn-sm btn-warn" v-if="t.status === 'active'" @click="suspendTenant(t.tenant_id)">Suspend</button>
          <button class="btn-sm btn-danger" @click="deleteTenant(t.tenant_id)">Delete</button>
        </div>
      </div>
      <div class="empty-card" v-if="!tenants.length">
        <p>No tenants yet. Create one to get started.</p>
      </div>
    </div>
    <div class="modal-overlay" v-if="showCreateModal" @click.self="showCreateModal = false">
      <div class="modal">
        <h3>Create Tenant</h3>
        <label>Tenant ID</label><input v-model="newTenant.tenant_id" class="input" placeholder="acme-corp" />
        <label>Name</label><input v-model="newTenant.name" class="input" placeholder="Acme Corporation" />
        <label>Plan</label>
        <select v-model="newTenant.plan" class="input">
          <option value="starter">Starter</option><option value="pro">Pro</option><option value="enterprise">Enterprise</option>
        </select>
        <div class="modal-actions">
          <button class="btn-secondary" @click="showCreateModal = false">Cancel</button>
          <button class="btn-primary" @click="createTenant">Create</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue';
import { useApiStore } from '../stores/api.js';

const api = useApiStore();
const tenants = ref([]);
const showCreateModal = ref(false);
const newTenant = ref({ tenant_id: '', name: '', plan: 'starter' });

function getQuotaUsage(t) {
  const q = t.quota || {};
  return [
    { name: 'API Calls', current: t.usage?.api_calls_today || 0, max: q.max_api_calls_per_day || 10000 },
    { name: 'Storage', current: Math.round(t.usage?.storage_mb || 0), max: q.max_storage_mb || 5120 },
    { name: 'Agents', current: t.usage?.active_agents || 0, max: q.max_agents || 50 },
  ].map(i => ({ ...i, pct: Math.round(i.current / Math.max(1, i.max) * 100) }));
}

async function loadTenants() {
  try { tenants.value = (await api.get('/api/tenant/list')).tenants || []; } catch { tenants.value = []; }
}

async function createTenant() {
  try { await api.post('/api/tenant/create', newTenant.value); showCreateModal.value = false; await loadTenants(); } catch {}
}

async function suspendTenant(id) {
  try { await api.post('/api/tenant/suspend', { tenant_id: id }); await loadTenants(); } catch {}
}

async function activateTenant(id) {
  try { await api.post('/api/tenant/activate', { tenant_id: id }); await loadTenants(); } catch {}
}

async function deleteTenant(id) {
  if (!confirm('Delete tenant ' + id + '? This cannot be undone.')) return;
  try { await api.post('/api/tenant/delete', { tenant_id: id }); await loadTenants(); } catch {}
}

onMounted(loadTenants);
</script>

<style scoped>
.topbar { display: flex; align-items: center; gap: 12px; margin-bottom: 24px; }
.topbar h1 { font-size: 24px; font-weight: 700; }
.badge { background: #7c3aed; color: #fff; padding: 2px 10px; border-radius: 6px; font-size: 11px; font-weight: 600; }
.btn-primary { margin-left: auto; background: var(--accent); color: #fff; border: none; padding: 8px 16px; border-radius: 8px; cursor: pointer; font-size: 13px; font-weight: 600; }
.btn-secondary { background: var(--bg3); color: var(--text); border: 1px solid var(--border); padding: 8px 16px; border-radius: 8px; cursor: pointer; font-size: 13px; }
.btn-sm { padding: 4px 10px; border: 1px solid var(--border); border-radius: 6px; background: var(--bg3); color: var(--text); font-size: 11px; cursor: pointer; }
.btn-warn { border-color: rgba(245,158,11,.3); color: var(--warn); }
.btn-danger { border-color: rgba(239,68,68,.3); color: var(--fail); }
.tenant-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 16px; }
.tenant-card { background: var(--bg2); border: 1px solid var(--border); border-radius: var(--radius); padding: 20px; box-shadow: var(--shadow); }
.tenant-card.suspended { opacity: .6; }
.tenant-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
.tenant-header h3 { font-size: 16px; }
.status-badge { padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; }
.status-badge.active { background: rgba(34,197,94,.15); color: var(--success); }
.status-badge.suspended { background: rgba(239,68,68,.15); color: var(--fail); }
.status-badge.trial { background: rgba(59,130,246,.15); color: var(--accent); }
.tenant-meta { font-size: 12px; color: var(--text3); display: flex; gap: 12px; margin-bottom: 12px; }
.quota-bars { margin-bottom: 12px; }
.quota-item { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
.quota-name { width: 70px; font-size: 11px; color: var(--text3); }
.quota-bar { flex: 1; height: 5px; background: var(--bg3); border-radius: 3px; overflow: hidden; }
.quota-fill { height: 100%; border-radius: 3px; transition: width .3s; }
.quota-val { width: 80px; font-size: 11px; color: var(--text3); text-align: right; }
.tenant-actions { display: flex; gap: 6px; }
.empty-card { text-align: center; padding: 40px; color: var(--text3); }
.modal-overlay { position: fixed; inset: 0; background: rgba(0,0,0,.4); display: flex; align-items: center; justify-content: center; z-index: 100; }
.modal { background: var(--bg); border: 1px solid var(--border); border-radius: var(--radius); padding: 24px; width: 400px; }
.modal h3 { margin-bottom: 16px; }
.modal label { display: block; font-size: 12px; color: var(--text3); margin: 10px 0 4px; }
.input { width: 100%; padding: 8px 12px; border: 1px solid var(--border); border-radius: 8px; background: var(--bg2); color: var(--text); font-size: 13px; }
.modal-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 20px; }
</style>
