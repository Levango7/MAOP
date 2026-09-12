<template>
  <div class="agent-registry-view">
    <ListPageLayout>
      <template #actions>
        <Segmented
          :model-value="activeTab"
          :options="tabOptions"
          size="sm"
          @update:model-value="activeTab = $event"
        />
        <button class="btn-ghost" :class="{ 'is-busy': loading }" :disabled="loading" @click="loadAll">
          <AppIcon name="refresh" :size="15" />
          <span>{{ t('view.agentRegistry.action.refresh') }}</span>
        </button>
        <button class="btn-primary" type="button" @click="openRegister">
          <AppIcon name="plus" :size="15" />
          <span>{{ t('view.agentRegistry.action.register') }}</span>
        </button>
      </template>
      <template #content>
        <!-- ── Tab 1: Agents ───────────────────────────────────── -->
        <div v-show="activeTab === 'agents'">
          <!-- Stats overview -->
          <div class="stats-row">
            <div class="stat-card">
              <span class="stat-card__label">{{ t('view.agentRegistry.stats.total') }}</span>
              <span class="stat-card__value">{{ stats.total ?? 0 }}</span>
            </div>
            <div class="stat-card">
              <span class="stat-card__label">{{ t('view.agentRegistry.stats.enabled') }}</span>
              <span class="stat-card__value is-ok">{{ stats.enabled ?? 0 }}</span>
            </div>
            <div class="stat-card">
              <span class="stat-card__label">{{ t('view.agentRegistry.stats.healthy') }}</span>
              <span class="stat-card__value is-ok">{{ stats.healthy ?? 0 }}</span>
            </div>
            <div class="stat-card">
              <span class="stat-card__label">{{ t('view.agentRegistry.stats.capabilities') }}</span>
              <span class="stat-card__value">{{ stats.capabilities ?? 0 }}</span>
            </div>
          </div>

          <Card :title="t('view.agentRegistry.title')" icon="bot" margin-bottom="var(--sp-4)">
            <template #actions>
              <div class="toolbar">
                <div class="search-box">
                  <AppIcon name="search" :size="14" />
                  <input
                    v-model="searchQuery"
                    type="text"
                    class="search-input"
                    :placeholder="t('view.agentRegistry.action.search')"
                  />
                </div>
                <label class="cap-filter">
                  <AppIcon name="filter" :size="14" />
                  <select v-model="capabilityFilter" class="cap-filter__select">
                    <option value="">{{ t('view.agentRegistry.action.filterCapability') }}</option>
                    <option v-for="cap in CAPABILITY_KEYS" :key="cap" :value="cap">
                      {{ capabilityLabel(cap) }}
                    </option>
                  </select>
                </label>
              </div>
            </template>

            <div v-if="loading" class="blk">
              <Skeleton block height="44px" />
              <Skeleton block height="44px" />
              <Skeleton block height="44px" />
            </div>
            <EmptyState
              v-else-if="errors.agents"
              icon="alert-triangle"
              tone="fail"
              :title="t('view.agentRegistry.failedLoad')"
              :description="errors.agents"
            />
            <EmptyState
              v-else-if="!filteredAgents.length"
              icon="bot"
              :title="t('view.agentRegistry.empty')"
              :description="t('view.agentRegistry.emptyHint')"
            />
            <div v-else class="ar-table" role="table" :aria-label="t('view.agentRegistry.title')">
              <div class="ar-table__head" role="row">
                <div class="ar-table__th" role="columnheader">{{ t('view.agentRegistry.col.name') }}</div>
                <div class="ar-table__th ar-table__th--vendor" role="columnheader">{{ t('view.agentRegistry.col.vendor') }}</div>
                <div class="ar-table__th ar-table__th--adapter" role="columnheader">{{ t('view.agentRegistry.col.adapter') }}</div>
                <div class="ar-table__th ar-table__th--caps" role="columnheader">{{ t('view.agentRegistry.col.capabilities') }}</div>
                <div class="ar-table__th ar-table__th--billing" role="columnheader">{{ t('view.agentRegistry.col.billing') }}</div>
                <div class="ar-table__th ar-table__th--auth" role="columnheader">{{ t('view.agentRegistry.col.auth') }}</div>
                <div class="ar-table__th ar-table__th--status" role="columnheader">{{ t('view.agentRegistry.col.status') }}</div>
                <div class="ar-table__th ar-table__th--act" role="columnheader">{{ t('view.agentRegistry.col.actions') }}</div>
              </div>
              <div v-for="a in filteredAgents" :key="a.name" class="ar-table__row" role="row">
                <div class="ar-table__td" role="cell">
                  <div class="ar-table__name">{{ a.display_name || a.name }}</div>
                  <div class="ar-table__id muted mono">{{ a.name }}</div>
                </div>
                <div class="ar-table__td ar-table__td--vendor" role="cell">
                  {{ a.vendor || '—' }}
                </div>
                <div class="ar-table__td ar-table__td--adapter" role="cell">
                  <Badge tone="neutral">{{ a.adapter_type || '—' }}</Badge>
                </div>
                <div class="ar-table__td ar-table__td--caps" role="cell">
                  <div class="cap-tags">
                    <Badge
                      v-for="cap in (a.capabilities || []).slice(0, 3)"
                      :key="cap"
                      tone="brand"
                    >{{ capabilityLabel(cap) }}</Badge>
                    <span v-if="(a.capabilities || []).length > 3" class="cap-more">
                      +{{ a.capabilities.length - 3 }}
                    </span>
                  </div>
                </div>
                <div class="ar-table__td ar-table__td--billing" role="cell">
                  {{ billingLabel(a.billing_model) }}
                </div>
                <div class="ar-table__td ar-table__td--auth" role="cell">
                  {{ authLabel(a.auth_method) }}
                </div>
                <div class="ar-table__td ar-table__td--status" role="cell">
                  <div class="status-stack">
                    <Badge :tone="enabledTone(a)">
                      {{ enabledLabel(a) }}
                    </Badge>
                    <Badge :tone="healthTone(a)">
                      {{ healthLabel(a) }}
                    </Badge>
                  </div>
                </div>
                <div class="ar-table__td ar-table__td--act" role="cell">
                  <button
                    class="icon-btn"
                    type="button"
                    :aria-label="t('view.agentRegistry.action.edit')"
                    @click="openEdit(a)"
                  >
                    <AppIcon name="gear" :size="14" />
                  </button>
                  <button
                    class="icon-btn"
                    type="button"
                    :aria-label="enabledLabel(a) === t('view.agentRegistry.status.enabled') ? t('view.agentRegistry.action.disable') : t('view.agentRegistry.action.enable')"
                    @click="toggleEnable(a)"
                  >
                    <AppIcon name="power" :size="14" />
                  </button>
                  <button
                    class="icon-btn icon-btn--danger"
                    type="button"
                    :aria-label="t('view.agentRegistry.action.delete')"
                    @click="confirmDelete(a)"
                  >
                    <AppIcon name="trash" :size="14" />
                  </button>
                </div>
              </div>
            </div>
          </Card>
        </div>

        <!-- ── Tab 2: Capabilities ─────────────────────────────── -->
        <div v-show="activeTab === 'capabilities'">
          <Card :title="t('view.agentRegistry.cap.title')" icon="zap" margin-bottom="var(--sp-4)">
            <div v-if="loading" class="blk">
              <Skeleton block height="80px" />
              <Skeleton block height="80px" />
            </div>
            <EmptyState
              v-else-if="errors.capabilities"
              icon="alert-triangle"
              tone="fail"
              :title="t('view.agentRegistry.cap.failedLoad')"
              :description="errors.capabilities"
            />
            <EmptyState
              v-else-if="!capabilityGroups.length"
              icon="zap"
              :title="t('view.agentRegistry.cap.empty')"
              :description="t('view.agentRegistry.cap.emptyHint')"
            />
            <div v-else class="cap-grid">
              <div v-for="group in capabilityGroups" :key="group.capability" class="cap-card">
                <div class="cap-card__head">
                  <AppIcon name="zap" :size="16" />
                  <span class="cap-card__title">{{ capabilityLabel(group.capability) }}</span>
                  <Badge tone="neutral">{{ group.agents.length }}</Badge>
                </div>
                <div class="cap-card__body">
                  <div v-for="a in group.agents" :key="a.name" class="cap-card__agent">
                    <AppIcon name="bot" :size="14" />
                    <span class="cap-card__agent-name">{{ a.display_name || a.name }}</span>
                    <span class="cap-card__agent-vendor muted">{{ a.vendor || '' }}</span>
                  </div>
                </div>
              </div>
            </div>
          </Card>
        </div>

        <!-- ── Tab 3: Fallback Chains ──────────────────────────── -->
        <div v-show="activeTab === 'fallback'">
          <Card :title="t('view.agentRegistry.fallback.title')" icon="route" margin-bottom="var(--sp-4)">
            <EmptyState
              v-if="!loading && !errors.agents && !agents.length"
              icon="route"
              :title="t('view.agentRegistry.fallback.empty')"
              :description="t('view.agentRegistry.fallback.emptyHint')"
            />
            <div v-else class="fallback-list">
              <div v-for="a in agents" :key="a.name" class="fallback-item">
                <div class="fallback-item__head">
                  <div class="fallback-item__title">
                    <Badge tone="brand">{{ t('view.agentRegistry.fallback.primary') }}</Badge>
                    <span class="fallback-item__name">{{ a.display_name || a.name }}</span>
                  </div>
                  <span v-if="!(a.fallback_agents || []).length" class="muted">
                    {{ t('view.agentRegistry.fallback.none') }}
                  </span>
                </div>
                <div v-if="(a.fallback_agents || []).length" class="fallback-item__chain">
                  <div
                    v-for="(fb, idx) in (a.fallback_agents || [])"
                    :key="fb"
                    class="chain-node"
                    draggable="true"
                    @dragstart="onDragStart($event, a.name, idx)"
                    @dragover.prevent="onDragOver($event, a.name, idx)"
                    @drop="onDrop($event, a.name, idx)"
                    @dragend="onDragEnd"
                  >
                    <span class="chain-node__idx">{{ idx + 1 }}</span>
                    <AppIcon name="arrow-down" :size="12" class="chain-node__arrow" />
                    <span class="chain-node__name">{{ agentDisplayName(fb) }}</span>
                    <button
                      class="icon-btn icon-btn--danger icon-btn--xs"
                      type="button"
                      :aria-label="t('view.agentRegistry.fallback.remove')"
                      @click="removeFallback(a.name, idx)"
                    >
                      <AppIcon name="x" :size="12" />
                    </button>
                  </div>
                  <div class="fallback-item__actions">
                    <label class="add-fallback">
                      <select @change="addFallback($event, a.name)">
                        <option value="">{{ t('view.agentRegistry.fallback.addAgent') }}</option>
                        <option
                          v-for="candidate in fallbackCandidates(a)"
                          :key="candidate.name"
                          :value="candidate.name"
                        >{{ candidate.display_name || candidate.name }}</option>
                      </select>
                    </label>
                    <button
                      v-if="hasChainChanged(a)"
                      class="btn-ghost btn-ghost--sm"
                      type="button"
                      :disabled="savingChain === a.name"
                      @click="saveChain(a)"
                    >
                      <AppIcon name="check" :size="14" />
                      <span>{{ savingChain === a.name ? t('view.agentRegistry.fallback.saving') : t('view.agentRegistry.fallback.save') }}</span>
                    </button>
                  </div>
                </div>
                <div v-else class="fallback-item__actions">
                  <label class="add-fallback">
                    <select @change="addFallback($event, a.name)">
                      <option value="">{{ t('view.agentRegistry.fallback.addAgent') }}</option>
                      <option
                        v-for="candidate in fallbackCandidates(a)"
                        :key="candidate.name"
                        :value="candidate.name"
                      >{{ candidate.display_name || candidate.name }}</option>
                    </select>
                  </label>
                </div>
              </div>
            </div>
          </Card>
        </div>
      </template>
    </ListPageLayout>

    <!-- ── Register / Edit Modal ───────────────────────────────── -->
    <Teleport to="body">
      <div
        v-if="showFormModal"
        v-modal-a11y
        class="modal-overlay"
        @click.self="closeForm"
        @modal:escape="closeForm"
      >
        <div class="modal modal--wide" role="dialog" aria-modal="true">
          <div class="modal__head">
            <h3>{{ editingAgent ? t('view.agentRegistry.form.editTitle') : t('view.agentRegistry.form.title') }}</h3>
            <button class="modal__x" type="button" :aria-label="t('common.close')" @click="closeForm">
              <AppIcon name="x" :size="16" />
            </button>
          </div>
          <div class="modal__body">
            <div class="form-grid">
              <label class="field">
                <span class="field__label">{{ t('view.agentRegistry.form.name') }} *</span>
                <input
                  v-model="form.name"
                  type="text"
                  class="field__input"
                  :placeholder="t('view.agentRegistry.form.nameHint')"
                  :disabled="editingAgent"
                />
              </label>
              <label class="field">
                <span class="field__label">{{ t('view.agentRegistry.form.displayName') }}</span>
                <input
                  v-model="form.display_name"
                  type="text"
                  class="field__input"
                  :placeholder="t('view.agentRegistry.form.displayNameHint')"
                />
              </label>
              <label class="field">
                <span class="field__label">{{ t('view.agentRegistry.form.vendor') }}</span>
                <input
                  v-model="form.vendor"
                  type="text"
                  class="field__input"
                  :placeholder="t('view.agentRegistry.form.vendorHint')"
                />
              </label>
              <label class="field">
                <span class="field__label">{{ t('view.agentRegistry.form.adapterType') }}</span>
                <select v-model="form.adapter_type" class="field__input field__select">
                  <option v-for="t_ in ADAPTER_TYPES" :key="t_" :value="t_">{{ t_ }}</option>
                </select>
              </label>
              <label class="field field--full">
                <span class="field__label">{{ t('view.agentRegistry.form.capabilities') }}</span>
                <div class="multi-select">
                  <label
                    v-for="cap in CAPABILITY_KEYS"
                    :key="cap"
                    class="multi-select__item"
                  >
                    <input
                      type="checkbox"
                      :value="cap"
                      :checked="form.capabilities.includes(cap)"
                      @change="toggleCapability(cap)"
                    />
                    <span>{{ capabilityLabel(cap) }}</span>
                  </label>
                </div>
              </label>
              <label class="field">
                <span class="field__label">{{ t('view.agentRegistry.form.billingModel') }}</span>
                <select v-model="form.billing_model" class="field__input field__select">
                  <option v-for="b in BILLING_KEYS" :key="b" :value="b">{{ billingLabel(b) }}</option>
                </select>
              </label>
              <label class="field">
                <span class="field__label">{{ t('view.agentRegistry.form.authMethod') }}</span>
                <select v-model="form.auth_method" class="field__input field__select">
                  <option v-for="a_ in AUTH_KEYS" :key="a_" :value="a_">{{ authLabel(a_) }}</option>
                </select>
              </label>
              <label class="field">
                <span class="field__label">{{ t('view.agentRegistry.form.maxConcurrent') }}</span>
                <input
                  v-model.number="form.max_concurrent"
                  type="number"
                  min="1"
                  class="field__input"
                />
              </label>
              <label class="field">
                <span class="field__label">{{ t('view.agentRegistry.form.timeout') }}</span>
                <input
                  v-model.number="form.timeout_s"
                  type="number"
                  min="1"
                  class="field__input"
                />
              </label>
              <label class="field">
                <span class="field__label">{{ t('view.agentRegistry.form.retryCount') }}</span>
                <input
                  v-model.number="form.retry_count"
                  type="number"
                  min="0"
                  class="field__input"
                />
              </label>
              <label class="field field--full">
                <span class="field__label">{{ t('view.agentRegistry.form.fallbackAgents') }}</span>
                <span class="field__hint muted">{{ t('view.agentRegistry.form.fallbackHint') }}</span>
                <div class="multi-select">
                  <label
                    v-for="candidate in formFallbackCandidates"
                    :key="candidate.name"
                    class="multi-select__item"
                  >
                    <input
                      type="checkbox"
                      :value="candidate.name"
                      :checked="form.fallback_agents.includes(candidate.name)"
                      @change="toggleFallback(candidate.name)"
                    />
                    <span>{{ candidate.display_name || candidate.name }}</span>
                  </label>
                </div>
              </label>
            </div>
            <p v-if="formError" class="modal__err">{{ formError }}</p>
          </div>
          <div class="modal__foot">
            <button class="btn-ghost" type="button" @click="closeForm">
              {{ t('view.agentRegistry.form.cancel') }}
            </button>
            <button
              class="btn-primary"
              type="button"
              :disabled="formSaving"
              @click="submitForm"
            >
              {{ formSaving ? t('view.agentRegistry.form.saving') : t('view.agentRegistry.form.submit') }}
            </button>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- ── Delete Confirm Modal ────────────────────────────────── -->
    <Teleport to="body">
      <div
        v-if="showDeleteModal"
        v-modal-a11y
        class="modal-overlay"
        @click.self="closeDelete"
        @modal:escape="closeDelete"
      >
        <div class="modal" role="dialog" aria-modal="true">
          <div class="modal__head">
            <h3>{{ t('view.agentRegistry.action.delete') }}</h3>
            <button class="modal__x" type="button" :aria-label="t('common.close')" @click="closeDelete">
              <AppIcon name="x" :size="16" />
            </button>
          </div>
          <div class="modal__body">
            <p class="modal__hint">{{ t('view.agentRegistry.confirmDelete') }}</p>
            <p v-if="deleteTarget" class="modal__target mono">{{ deleteTarget.name }}</p>
          </div>
          <div class="modal__foot">
            <button class="btn-ghost" type="button" @click="closeDelete">
              {{ t('view.agentRegistry.form.cancel') }}
            </button>
            <button class="btn-danger" type="button" @click="doDelete">
              {{ t('view.agentRegistry.action.delete') }}
            </button>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue';
import { useApiStore } from '../stores/api.js';
import { useToast } from '../composables/useToast.js';
import { useI18n } from '../i18n';
import ListPageLayout from '../components/ListPageLayout.vue';
import AppIcon from '../components/AppIcon.vue';
import Card from '../components/Card.vue';
import Badge from '../components/Badge.vue';
import Segmented from '../components/Segmented.vue';
import Skeleton from '../components/Skeleton.vue';
import EmptyState from '../components/EmptyState.vue';

const api = useApiStore();
const toast = useToast();
const { t } = useI18n();

// ── Constants ───────────────────────────────────────────────────
const CAPABILITY_KEYS = [
  'code_generation', 'code_review', 'chat', 'reasoning', 'multimodal',
  'long_context', 'tool_use', 'web_search', 'file_edit', 'terminal',
];
const ADAPTER_TYPES = ['cli', 'http', 'mcp', 'web'];
const BILLING_KEYS = ['per_token', 'per_call', 'subscription', 'credit', 'blackbox', 'free'];
const AUTH_KEYS = ['api_key', 'oauth', 'username_password', 'license', 'none'];

// ── Tab state ───────────────────────────────────────────────────
const activeTab = ref('agents');
const tabOptions = [
  { value: 'agents', label: t('view.agentRegistry.tab.agents'), icon: 'bot' },
  { value: 'capabilities', label: t('view.agentRegistry.tab.capabilities'), icon: 'zap' },
  { value: 'fallback', label: t('view.agentRegistry.tab.fallback'), icon: 'route' },
];

// ── Data state ──────────────────────────────────────────────────
const loading = ref(false);
const errors = ref({ agents: null, capabilities: null });
const agents = ref([]);
const stats = ref({});
const searchQuery = ref('');
const capabilityFilter = ref('');

// ── Label helpers ───────────────────────────────────────────────
function capabilityLabel(cap) {
  return t(`view.agentRegistry.capability.${cap}`) !== `view.agentRegistry.capability.${cap}`
    ? t(`view.agentRegistry.capability.${cap}`)
    : cap;
}
function billingLabel(b) {
  if (!b) return '—';
  return t(`view.agentRegistry.billing.${b}`) !== `view.agentRegistry.billing.${b}`
    ? t(`view.agentRegistry.billing.${b}`)
    : b;
}
function authLabel(a) {
  if (!a) return '—';
  return t(`view.agentRegistry.auth.${a}`) !== `view.agentRegistry.auth.${a}`
    ? t(`view.agentRegistry.auth.${a}`)
    : a;
}

function normalizeStatus(s) {
  return String(s || '').toLowerCase();
}

function enabledLabel(a) {
  const enabled = a.enabled !== false && a.enabled !== 0 && a.enabled !== 'false';
  return enabled
    ? t('view.agentRegistry.status.enabled')
    : t('view.agentRegistry.status.disabled');
}
function enabledTone(a) {
  const enabled = a.enabled !== false && a.enabled !== 0 && a.enabled !== 'false';
  return enabled ? 'success' : 'neutral';
}

function healthLabel(a) {
  const n = normalizeStatus(a.health || a.health_status);
  if (n === 'healthy' || n === 'ok' || n === 'up') return t('view.agentRegistry.status.healthy');
  if (n === 'unhealthy' || n === 'fail' || n === 'down') return t('view.agentRegistry.status.unhealthy');
  return t('view.agentRegistry.status.unknown');
}
function healthTone(a) {
  const n = normalizeStatus(a.health || a.health_status);
  if (n === 'healthy' || n === 'ok' || n === 'up') return 'success';
  if (n === 'unhealthy' || n === 'fail' || n === 'down') return 'fail';
  return 'neutral';
}

function agentDisplayName(name) {
  const found = agents.value.find((a) => a.name === name);
  return found ? (found.display_name || found.name) : name;
}

// ── Filtered agents (search + capability) ───────────────────────
const filteredAgents = computed(() => {
  let list = agents.value;
  const q = searchQuery.value.trim().toLowerCase();
  if (q) {
    list = list.filter((a) => {
      const name = String(a.name || '').toLowerCase();
      const vendor = String(a.vendor || '').toLowerCase();
      const display = String(a.display_name || '').toLowerCase();
      return name.includes(q) || vendor.includes(q) || display.includes(q);
    });
  }
  const cap = capabilityFilter.value;
  if (cap) {
    list = list.filter((a) => Array.isArray(a.capabilities) && a.capabilities.includes(cap));
  }
  return list;
});

// ── Capability groups (Tab 2) ───────────────────────────────────
const capabilityGroups = computed(() => {
  const map = new Map();
  for (const a of agents.value) {
    for (const cap of (a.capabilities || [])) {
      if (!map.has(cap)) map.set(cap, []);
      map.get(cap).push(a);
    }
  }
  return Array.from(map.entries())
    .map(([capability, list]) => ({ capability, agents: list }))
    .sort((x, y) => y.agents.length - x.agents.length);
});

// ── Fallback chain helpers ──────────────────────────────────────
const originalChains = ref({});
const savingChain = ref('');

function fallbackCandidates(agent) {
  const current = new Set(agent.fallback_agents || []);
  return agents.value.filter((a) => a.name !== agent.name && !current.has(a.name));
}

function hasChainChanged(a) {
  const orig = originalChains.value[a.name] || [];
  const curr = a.fallback_agents || [];
  return orig.length !== curr.length || orig.some((v, i) => v !== curr[i]);
}

// ── Drag & Drop (native HTML5) ──────────────────────────────────
const dragState = ref({ agentName: null, fromIdx: null });

function onDragStart(e, agentName, idx) {
  dragState.value = { agentName, fromIdx: idx };
  e.dataTransfer.effectAllowed = 'move';
  e.dataTransfer.setData('text/plain', String(idx));
  e.target.classList.add('is-dragging');
}
function onDragOver(e, agentName, _idx) {
  if (dragState.value.agentName !== agentName) return;
  e.dataTransfer.dropEffect = 'move';
}
function onDrop(e, agentName, toIdx) {
  if (dragState.value.agentName !== agentName) return;
  const fromIdx = dragState.value.fromIdx;
  if (fromIdx === null || fromIdx === toIdx) return;
  const agent = agents.value.find((a) => a.name === agentName);
  if (!agent || !Array.isArray(agent.fallback_agents)) return;
  const arr = agent.fallback_agents.slice();
  const [moved] = arr.splice(fromIdx, 1);
  arr.splice(toIdx, 0, moved);
  agent.fallback_agents = arr;
}
function onDragEnd(e) {
  if (e.target && e.target.classList) e.target.classList.remove('is-dragging');
  dragState.value = { agentName: null, fromIdx: null };
}

function removeFallback(agentName, idx) {
  const agent = agents.value.find((a) => a.name === agentName);
  if (!agent || !Array.isArray(agent.fallback_agents)) return;
  agent.fallback_agents = agent.fallback_agents.filter((_, i) => i !== idx);
}

function addFallback(e, agentName) {
  const target = e.target;
  const fbName = target.value;
  if (!fbName) return;
  const agent = agents.value.find((a) => a.name === agentName);
  if (!agent) return;
  if (!Array.isArray(agent.fallback_agents)) agent.fallback_agents = [];
  if (!agent.fallback_agents.includes(fbName)) {
    agent.fallback_agents.push(fbName);
  }
  target.value = '';
}

async function saveChain(agent) {
  savingChain.value = agent.name;
  try {
    await api.put(`/api/agent-catalog/agents/${encodeURIComponent(agent.name)}`, {
      fallback_agents: agent.fallback_agents || [],
    });
    originalChains.value[agent.name] = (agent.fallback_agents || []).slice();
    toast.success(t('view.agentRegistry.fallback.saved'));
  } catch (err) {
    toast.error((err && err.message) || t('view.agentRegistry.fallback.saveFailed'));
  } finally {
    savingChain.value = '';
  }
}

// ── Register / Edit form modal ──────────────────────────────────
const showFormModal = ref(false);
const editingAgent = ref(null);
const formSaving = ref(false);
const formError = ref('');
const form = reactive({
  name: '',
  display_name: '',
  vendor: '',
  adapter_type: 'cli',
  capabilities: [],
  billing_model: 'per_token',
  auth_method: 'api_key',
  max_concurrent: 1,
  timeout_s: 30,
  retry_count: 3,
  fallback_agents: [],
});

const formFallbackCandidates = computed(() =>
  agents.value.filter((a) => a.name !== form.name),
);

function resetForm() {
  form.name = '';
  form.display_name = '';
  form.vendor = '';
  form.adapter_type = 'cli';
  form.capabilities = [];
  form.billing_model = 'per_token';
  form.auth_method = 'api_key';
  form.max_concurrent = 1;
  form.timeout_s = 30;
  form.retry_count = 3;
  form.fallback_agents = [];
}

function openRegister() {
  editingAgent.value = null;
  resetForm();
  formError.value = '';
  showFormModal.value = true;
}

function openEdit(a) {
  editingAgent.value = a;
  form.name = a.name || '';
  form.display_name = a.display_name || '';
  form.vendor = a.vendor || '';
  form.adapter_type = a.adapter_type || 'cli';
  form.capabilities = Array.isArray(a.capabilities) ? a.capabilities.slice() : [];
  form.billing_model = a.billing_model || 'per_token';
  form.auth_method = a.auth_method || 'api_key';
  form.max_concurrent = a.max_concurrent ?? 1;
  form.timeout_s = a.timeout_s ?? 30;
  form.retry_count = a.retry_count ?? 3;
  form.fallback_agents = Array.isArray(a.fallback_agents) ? a.fallback_agents.slice() : [];
  formError.value = '';
  showFormModal.value = true;
}

function closeForm() {
  showFormModal.value = false;
  editingAgent.value = null;
  formError.value = '';
}

function toggleCapability(cap) {
  const idx = form.capabilities.indexOf(cap);
  if (idx >= 0) form.capabilities.splice(idx, 1);
  else form.capabilities.push(cap);
}

function toggleFallback(name) {
  const idx = form.fallback_agents.indexOf(name);
  if (idx >= 0) form.fallback_agents.splice(idx, 1);
  else form.fallback_agents.push(name);
}

async function submitForm() {
  formError.value = '';
  if (!form.name.trim()) {
    formError.value = t('view.agentRegistry.form.validateName');
    return;
  }
  formSaving.value = true;
  try {
    const payload = {
      name: form.name.trim(),
      display_name: form.display_name.trim(),
      vendor: form.vendor.trim(),
      adapter_type: form.adapter_type,
      capabilities: form.capabilities.slice(),
      billing_model: form.billing_model,
      auth_method: form.auth_method,
      max_concurrent: Number(form.max_concurrent) || 1,
      timeout_s: Number(form.timeout_s) || 30,
      retry_count: Number(form.retry_count) || 0,
      fallback_agents: form.fallback_agents.slice(),
    };
    if (editingAgent.value) {
      await api.put(`/api/agent-catalog/agents/${encodeURIComponent(payload.name)}`, payload);
      toast.success(t('view.agentRegistry.form.updateSuccess'));
    } else {
      await api.post('/api/agent-catalog/agents', payload);
      toast.success(t('view.agentRegistry.form.success'));
    }
    showFormModal.value = false;
    editingAgent.value = null;
    await loadAll();
  } catch (err) {
    formError.value = (err && err.message) || t('view.agentRegistry.form.failed');
  } finally {
    formSaving.value = false;
  }
}

// ── Delete modal ────────────────────────────────────────────────
const showDeleteModal = ref(false);
const deleteTarget = ref(null);

function confirmDelete(a) {
  deleteTarget.value = a;
  showDeleteModal.value = true;
}
function closeDelete() {
  showDeleteModal.value = false;
  deleteTarget.value = null;
}
async function doDelete() {
  if (!deleteTarget.value) return;
  const name = deleteTarget.value.name;
  try {
    await api.delete(`/api/agent-catalog/agents/${encodeURIComponent(name)}`);
    toast.success(t('view.agentRegistry.toast.deleteSuccess'));
    showDeleteModal.value = false;
    deleteTarget.value = null;
    await loadAll();
  } catch (err) {
    toast.error((err && err.message) || t('view.agentRegistry.toast.deleteFailed'));
  }
}

// ── Toggle enable / disable ─────────────────────────────────────
async function toggleEnable(a) {
  const currentlyEnabled = a.enabled !== false && a.enabled !== 0 && a.enabled !== 'false';
  const nextEnabled = !currentlyEnabled;
  try {
    await api.put(`/api/agent-catalog/agents/${encodeURIComponent(a.name)}`, {
      enabled: nextEnabled,
    });
    a.enabled = nextEnabled;
    toast.success(
      nextEnabled
        ? t('view.agentRegistry.toast.enableSuccess')
        : t('view.agentRegistry.toast.disableSuccess'),
    );
  } catch (err) {
    toast.error((err && err.message) || t('view.agentRegistry.toast.toggleFailed'));
  }
}

// ── Data loading ────────────────────────────────────────────────
async function loadAgents() {
  try {
    const data = await api.get('/api/agent-catalog/agents');
    const list = Array.isArray(data) ? data : (data.agents || data.data || []);
    agents.value = list;
    // Snapshot original fallback chains for change detection
    const snap = {};
    for (const a of list) {
      snap[a.name] = Array.isArray(a.fallback_agents) ? a.fallback_agents.slice() : [];
    }
    originalChains.value = snap;
    errors.value = { ...errors.value, agents: null };
  } catch (err) {
    agents.value = [];
    errors.value = { ...errors.value, agents: (err && err.message) || t('view.agentRegistry.failedLoad') };
  }
}

async function loadStats() {
  try {
    const data = await api.get('/api/agent-catalog/stats');
    stats.value = data || {};
  } catch {
    // Stats are non-critical; derive from agents if endpoint missing
    const list = agents.value;
    const capSet = new Set();
    for (const a of list) for (const c of (a.capabilities || [])) capSet.add(c);
    stats.value = {
      total: list.length,
      enabled: list.filter((a) => a.enabled !== false).length,
      healthy: list.filter((a) => {
        const n = normalizeStatus(a.health || a.health_status);
        return n === 'healthy' || n === 'ok' || n === 'up';
      }).length,
      capabilities: capSet.size,
    };
  }
}

async function loadAll() {
  loading.value = true;
  await Promise.allSettled([loadAgents()]);
  // agents加载完成后再加载stats，确保fallback派生数据可用
  await Promise.allSettled([loadStats()]);
  loading.value = false;
}

onMounted(() => {
  loadAll();
});
</script>

<style scoped>
/* ── Layout helpers ────────────────────────────────────────────── */
.blk { display: flex; flex-direction: column; gap: var(--sp-2); }
.muted { color: var(--text-muted); }
.mono { font-family: var(--font-mono); font-size: var(--fs-xs); }

/* ── Buttons ───────────────────────────────────────────────────── */
.btn-ghost {
  display: inline-flex; align-items: center; gap: var(--sp-1);
  background: var(--surface-2); color: var(--text);
  border: 1px solid var(--border); border-radius: var(--r-md);
  padding: 7px var(--sp-3); font-size: var(--fs-sm); font-weight: 600;
  cursor: pointer; transition: opacity var(--motion) var(--ease);
}
.btn-ghost:hover { opacity: .9; }
.btn-ghost:disabled { opacity: .5; cursor: not-allowed; }
.btn-ghost--sm { padding: var(--sp-1) var(--sp-2); font-size: var(--fs-xs); }
.btn-primary {
  display: inline-flex; align-items: center; gap: var(--sp-1);
  background: var(--brand); color: var(--brand-contrast);
  border: none; border-radius: var(--r-md);
  padding: 7px var(--sp-4); font-size: var(--fs-sm); font-weight: 600;
  cursor: pointer; transition: opacity var(--motion) var(--ease);
}
.btn-primary:hover { opacity: .9; }
.btn-primary:disabled { opacity: .5; cursor: not-allowed; }
.btn-danger {
  display: inline-flex; align-items: center; gap: var(--sp-1);
  background: var(--danger, var(--fail)); color: var(--on-danger, var(--text-inverse, #fff));
  border: none; border-radius: var(--r-md);
  padding: 7px var(--sp-4); font-size: var(--fs-sm); font-weight: 600;
  cursor: pointer; transition: opacity var(--motion) var(--ease);
}
.btn-danger:hover { opacity: .9; }
.icon-btn {
  display: grid; place-items: center;
  width: 28px; height: 28px;
  background: var(--surface-2); border: 1px solid var(--border);
  border-radius: var(--r-sm); color: var(--text-muted); cursor: pointer;
  transition: color var(--motion) var(--ease), border-color var(--motion) var(--ease);
}
.icon-btn:hover { color: var(--text); border-color: var(--border-strong); }
.icon-btn:disabled { opacity: .5; cursor: not-allowed; }
.icon-btn--xs { width: 20px; height: 20px; }
.icon-btn--danger:hover { color: var(--fail); border-color: var(--fail); }

/* ── Stats row ─────────────────────────────────────────────────── */
.stats-row {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: var(--sp-3);
  margin-bottom: var(--sp-4);
}
.stat-card {
  display: flex; flex-direction: column; gap: var(--sp-1);
  padding: var(--sp-3); border: 1px solid var(--border-subtle);
  border-radius: var(--r-md); background: var(--surface);
}
.stat-card__label {
  font-size: var(--fs-xs); font-weight: 600;
  text-transform: uppercase; letter-spacing: .03em;
  color: var(--text-muted);
}
.stat-card__value {
  font-size: var(--fs-2xl); font-weight: 700; color: var(--text);
  font-variant-numeric: tabular-nums;
}
.stat-card__value.is-ok { color: var(--success); }

/* ── Toolbar ───────────────────────────────────────────────────── */
.toolbar {
  display: flex; align-items: center; gap: var(--sp-2); flex-wrap: wrap;
}
.search-box {
  display: inline-flex; align-items: center; gap: var(--sp-1);
  background: var(--surface-2); border: 1px solid var(--border);
  border-radius: var(--r-md); padding: 0 var(--sp-2);
}
.search-input {
  font-family: inherit; font-size: var(--fs-sm);
  background: transparent; color: var(--text);
  border: none; border-radius: var(--r-md);
  padding: 7px 0; width: 180px; outline: none;
}
.search-input::placeholder { color: var(--text-muted); }
.cap-filter {
  display: inline-flex; align-items: center; gap: var(--sp-1);
  background: var(--surface-2); border: 1px solid var(--border);
  border-radius: var(--r-md); padding: 0 var(--sp-2);
}
.cap-filter__select {
  appearance: none; -webkit-appearance: none;
  font-family: inherit; font-size: var(--fs-sm);
  background: transparent; color: var(--text);
  border: none; border-radius: var(--r-md);
  padding: 7px var(--sp-6) 7px 0; cursor: pointer; outline: none;
  background-image: var(--icon-chevron);
  background-repeat: no-repeat;
  background-position: right 0 center;
}

/* ── Agent table ───────────────────────────────────────────────── */
.ar-table {
  width: 100%;
  border: 1px solid var(--border-subtle);
  border-radius: var(--r-md);
  overflow: hidden;
}
.ar-table__head,
.ar-table__row {
  display: grid;
  grid-template-columns: 1.4fr 0.8fr 0.7fr 1.4fr 0.8fr 0.8fr 0.9fr 0.9fr;
  align-items: center;
  gap: var(--sp-2);
}
.ar-table__head {
  background: var(--surface-2);
  padding: var(--sp-2) var(--sp-3);
  border-bottom: 1px solid var(--border-strong);
}
.ar-table__th {
  font-size: var(--fs-xs); font-weight: 600;
  text-transform: uppercase; letter-spacing: .03em;
  color: var(--text-muted);
}
.ar-table__th--act { text-align: right; }
.ar-table__row {
  padding: var(--sp-3);
  border-bottom: 1px solid var(--border-subtle);
  transition: background var(--motion) var(--ease);
}
.ar-table__row:hover { background: var(--surface-2); }
.ar-table__row:last-child { border-bottom: none; }
.ar-table__td { min-width: 0; }
.ar-table__name { font-weight: 600; color: var(--text); font-size: var(--fs-sm); }
.ar-table__id { font-size: var(--fs-xs); margin-top: 2px; }
.ar-table__td--caps .cap-tags { display: flex; flex-wrap: wrap; gap: var(--sp-1); }
.cap-more {
  font-size: var(--fs-xs); color: var(--text-muted);
  display: inline-flex; align-items: center;
}
.status-stack { display: flex; flex-direction: column; gap: var(--sp-1); align-items: flex-start; }
.ar-table__td--act {
  display: flex; gap: var(--sp-1); justify-content: flex-end; flex-wrap: wrap;
}

/* ── Capability grid (Tab 2) ───────────────────────────────────── */
.cap-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: var(--sp-3);
}
.cap-card {
  border: 1px solid var(--border-subtle);
  border-radius: var(--r-md);
  background: var(--surface);
  overflow: hidden;
}
.cap-card__head {
  display: flex; align-items: center; gap: var(--sp-2);
  padding: var(--sp-2) var(--sp-3);
  background: var(--surface-2);
  border-bottom: 1px solid var(--border-subtle);
}
.cap-card__title { font-weight: 600; font-size: var(--fs-sm); color: var(--text); flex: 1; }
.cap-card__body { display: flex; flex-direction: column; }
.cap-card__agent {
  display: flex; align-items: center; gap: var(--sp-2);
  padding: var(--sp-2) var(--sp-3);
  border-bottom: 1px solid var(--border-subtle);
}
.cap-card__agent:last-child { border-bottom: none; }
.cap-card__agent-name { font-size: var(--fs-sm); color: var(--text); }
.cap-card__agent-vendor { font-size: var(--fs-xs); margin-left: auto; }

/* ── Fallback chains (Tab 3) ───────────────────────────────────── */
.fallback-list { display: flex; flex-direction: column; gap: var(--sp-3); }
.fallback-item {
  border: 1px solid var(--border-subtle);
  border-radius: var(--r-md);
  background: var(--surface);
  padding: var(--sp-3);
}
.fallback-item__head {
  display: flex; align-items: center; justify-content: space-between;
  margin-bottom: var(--sp-2);
}
.fallback-item__title {
  display: flex; align-items: center; gap: var(--sp-2);
}
.fallback-item__name { font-weight: 600; font-size: var(--fs-sm); color: var(--text); }
.fallback-item__chain { display: flex; flex-direction: column; gap: var(--sp-1); }
.chain-node {
  display: flex; align-items: center; gap: var(--sp-2);
  padding: var(--sp-2) var(--sp-3);
  background: var(--surface-2);
  border: 1px solid var(--border-subtle);
  border-radius: var(--r-sm);
  cursor: grab;
  transition: border-color var(--motion) var(--ease), opacity var(--motion) var(--ease);
}
.chain-node:hover { border-color: var(--border-strong); }
.chain-node.is-dragging { opacity: .4; }
.chain-node__idx {
  display: grid; place-items: center;
  width: 20px; height: 20px;
  border-radius: 50%; background: var(--brand); color: var(--brand-contrast);
  font-size: var(--fs-xs); font-weight: 700; flex-shrink: 0;
}
.chain-node__arrow { color: var(--text-muted); flex-shrink: 0; }
.chain-node__name { font-size: var(--fs-sm); color: var(--text); flex: 1; min-width: 0; }
.fallback-item__actions {
  display: flex; align-items: center; gap: var(--sp-2); flex-wrap: wrap;
  margin-top: var(--sp-2);
}
.add-fallback select {
  appearance: none; -webkit-appearance: none;
  font-family: inherit; font-size: var(--fs-sm);
  background: var(--surface-2); color: var(--text);
  border: 1px solid var(--border); border-radius: var(--r-md);
  padding: 5px var(--sp-6) 5px var(--sp-2); cursor: pointer; outline: none;
  background-image: var(--icon-chevron);
  background-repeat: no-repeat;
  background-position: right var(--sp-1) center;
}

/* ── Form fields ───────────────────────────────────────────────── */
.form-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--sp-3);
}
.field { display: flex; flex-direction: column; gap: var(--sp-1); }
.field--full { grid-column: 1 / -1; }
.field__label {
  font-size: var(--fs-sm); font-weight: 600; color: var(--text-muted);
}
.field__hint { font-size: var(--fs-xs); }
.field__input {
  font-family: inherit; font-size: var(--fs-base);
  background: var(--surface-2); color: var(--text);
  border: 1px solid var(--border); border-radius: var(--r-md);
  padding: 7px 10px; width: 100%;
  transition: border-color var(--motion) var(--ease);
}
.field__input:focus { outline: none; border-color: var(--brand); }
.field__input:disabled { opacity: .6; cursor: not-allowed; }
.field__select {
  appearance: none; -webkit-appearance: none;
  background-image: var(--icon-chevron);
  background-repeat: no-repeat;
  background-position: right var(--sp-2) center;
  padding-right: var(--sp-7);
  cursor: pointer;
}
.multi-select {
  display: flex; flex-wrap: wrap; gap: var(--sp-2);
  padding: var(--sp-2);
  border: 1px solid var(--border-subtle);
  border-radius: var(--r-md);
  background: var(--surface-2);
}
.multi-select__item {
  display: inline-flex; align-items: center; gap: var(--sp-1);
  font-size: var(--fs-sm); color: var(--text);
  cursor: pointer; user-select: none;
}
.multi-select__item input { cursor: pointer; }

/* ── Modal ─────────────────────────────────────────────────────── */
.modal-overlay {
  position: fixed; inset: 0; background: var(--overlay-scrim);
  display: flex; align-items: center; justify-content: center;
  z-index: var(--z-modal);
}
.modal {
  position: relative;
  background: var(--surface); border: 1px solid var(--border);
  border-radius: var(--r-lg); padding: var(--sp-6);
  width: calc(100% - 32px); max-width: 560px; max-height: 88vh; overflow-y: auto;
  box-shadow: var(--shadow-lg);
}
.modal--wide { max-width: 720px; }
.modal__head {
  display: flex; align-items: center; justify-content: space-between;
  margin-bottom: var(--sp-4);
}
.modal__head h3 { margin: 0; font-size: var(--fs-lg); color: var(--text); }
.modal__x {
  display: grid; place-items: center;
  width: 28px; height: 28px;
  background: transparent; border: none; border-radius: var(--r-sm);
  color: var(--text-muted); cursor: pointer;
  transition: color var(--motion) var(--ease), background var(--motion) var(--ease);
}
.modal__x:hover { color: var(--text); background: var(--surface-2); }
.modal__body { display: flex; flex-direction: column; gap: var(--sp-2); }
.modal__target { color: var(--text-muted); font-size: var(--fs-sm); }
.modal__hint { color: var(--text-muted); font-size: var(--fs-sm); margin: 0; }
.modal__err { color: var(--fail); font-size: var(--fs-sm); margin: 0; }
.modal__foot {
  display: flex; justify-content: flex-end; gap: var(--sp-2);
  margin-top: var(--sp-4);
}

/* ── Responsive ────────────────────────────────────────────────── */
@media (max-width: 900px) {
  .stats-row { grid-template-columns: repeat(2, 1fr); }
  .ar-table__head,
  .ar-table__row {
    grid-template-columns: 1fr 0.8fr 0.8fr 0.9fr;
  }
  .ar-table__th--vendor,
  .ar-table__td--vendor,
  .ar-table__th--billing,
  .ar-table__td--billing,
  .ar-table__th--auth,
  .ar-table__td--auth { display: none; }
  .form-grid { grid-template-columns: 1fr; }
}
@media (max-width: 640px) {
  .stats-row { grid-template-columns: 1fr; }
  .ar-table__head,
  .ar-table__row {
    grid-template-columns: 1fr 0.9fr;
  }
  .ar-table__th--adapter,
  .ar-table__td--adapter,
  .ar-table__th--caps,
  .ar-table__td--caps,
  .ar-table__th--act,
  .ar-table__td--act { display: none; }
  .cap-grid { grid-template-columns: 1fr; }
}
</style>