<template>
  <div class="settings-page view-enter">
    <PageHeader />

    <!-- Tab switcher: Configuration | Config History | Hook Management -->
    <div class="settings-tabs" role="tablist">
      <button
        type="button"
        class="settings-tab"
        :class="{ active: activeTab === 'config' }"
        role="tab"
        :aria-selected="activeTab === 'config'"
        @click="activeTab = 'config'"
      >{{ t('view.settings.tabConfig') }}</button>
      <button
        type="button"
        class="settings-tab"
        :class="{ active: activeTab === 'history' }"
        role="tab"
        :aria-selected="activeTab === 'history'"
        @click="activeTab = 'history'"
      >{{ t('view.settings.tabHistory') }}</button>
      <button
        type="button"
        class="settings-tab"
        :class="{ active: activeTab === 'hooks' }"
        role="tab"
        :aria-selected="activeTab === 'hooks'"
        @click="activeTab = 'hooks'"
      >{{ t('view.settings.tabHooks') }}</button>
    </div>

    <!-- ── Tab: Configuration ───────────────────────────────────── -->
    <div v-show="activeTab === 'config'" class="settings-grid">
      <!-- Appearance: theme / density / sidebar rail / language, all wired to the shared ui store -->
      <Card :title="t('settings.appearance')" icon="gear">
        <div class="setting-row">
          <span class="setting-label">{{ t('settings.theme') }}</span>
          <Segmented
            equal
            :model-value="ui.theme"
            :options="[{ value: 'light', label: t('settings.light') }, { value: 'dark', label: t('settings.dark') }]"
            @update:model-value="ui.setTheme"
          />
        </div>
        <div class="setting-row">
          <span class="setting-label">{{ t('settings.density') }}</span>
          <Segmented
            equal
            :model-value="ui.density"
            :options="[{ value: 'comfortable', label: t('settings.comfortable') }, { value: 'compact', label: t('settings.compact') }]"
            @update:model-value="ui.setDensity"
          />
        </div>
        <div class="setting-row">
          <span class="setting-label">{{ t('settings.sidebar') }}</span>
          <Segmented
            equal
            :model-value="ui.rail ? 'collapsed' : 'expanded'"
            :options="[{ value: 'expanded', label: t('settings.expanded') }, { value: 'collapsed', label: t('settings.collapsed') }]"
            @update:model-value="ui.setRail($event === 'collapsed')"
          />
        </div>
        <div class="setting-row">
          <span class="setting-label">{{ t('settings.language') }}</span>
          <Segmented
            equal
            :model-value="ui.locale"
            :options="[{ value: 'zh', label: t('settings.zh') }, { value: 'en', label: t('settings.en') }]"
            @update:model-value="ui.setLocale"
          />
        </div>
      </Card>

      <Card :title="t('settings.edition')">
        <div class="setting-row">
          <span class="setting-label">{{ t('view.settings.currentEdition') }}</span>
          <Badge :tone="edition.edition === 'enterprise' ? 'brand' : 'success'">{{ edition.edition || 'personal' }}</Badge>
        </div>
        <div class="setting-row edition-switch-row">
          <span class="setting-label">{{ t('view.settings.switchEdition') }}</span>
          <span class="setting-value-group">
            <div class="edition-switch-buttons">
              <button
                class="edition-btn"
                :class="{ active: edition.edition === 'personal' }"
                :disabled="!isAdmin || editionStore.switching || edition.edition === 'personal'"
                @click="onSwitchClick('personal')"
              >{{ t('view.settings.personal') }}</button>
              <button
                class="edition-btn"
                :class="{ active: edition.edition === 'enterprise' }"
                :disabled="!isAdmin || editionStore.switching || edition.edition === 'enterprise'"
                @click="onSwitchClick('enterprise')"
              >{{ t('view.settings.enterprise') }}</button>
            </div>
            <span v-if="editionStore.switching" class="switching-indicator">{{ t('view.settings.switching') }}</span>
          </span>
        </div>
        <div v-if="!isAdmin" class="setting-row">
          <span class="setting-label"></span>
          <span class="edition-perm-hint">{{ t('view.settings.adminRequired') }}</span>
        </div>
        <div v-if="editionStore.switchError" class="setting-row">
          <span class="setting-label"></span>
          <span class="edition-error-msg">{{ editionStore.switchError }}</span>
        </div>
        <div v-if="switchNotice" class="setting-row">
          <span class="setting-label"></span>
          <span class="edition-notice" :class="{ degraded: switchNoticeDegraded }">{{ switchNotice }}</span>
        </div>
        <div class="setting-row">
          <span class="setting-label">{{ t('view.settings.enterpriseAvailable') }}</span>
          <span class="setting-value-group">
            <span class="status-dot" :class="edition.enterprise_available ? 'on' : 'off'"></span>
            <span class="setting-value">{{ edition.enterprise_available ? t('view.settings.yes') : t('view.settings.no') }}</span>
          </span>
        </div>
        <div v-if="edition.degradations && edition.degradations.length > 0" class="setting-row">
          <span class="setting-label">{{ t('view.settings.degradations') }}</span>
          <span class="degradation-count">{{ edition.degradations.length }}</span>
        </div>
        <div v-if="edition.degradations && edition.degradations.length > 0" class="degradation-list">
          <div v-for="(d, i) in edition.degradations" :key="i" class="degradation-item">
            <span class="deg-backend">{{ d.backend }}</span>
            <span class="deg-arrow">→</span>
            <span class="deg-fallback">{{ d.fallback }}</span>
            <span class="deg-reason">({{ d.reason }})</span>
          </div>
        </div>
      </Card>

      <Card :title="t('settings.backends')" :badge="t('view.settings.readOnly')" badge-tone="neutral">
        <div v-for="(val, key) in edition.backends" :key="key" class="setting-row">
          <span class="setting-label">{{ key }}</span>
          <span class="backend-tag">{{ val }}</span>
        </div>
      </Card>

      <Card :title="t('settings.server')" :badge="t('view.settings.readOnly')" badge-tone="neutral">
        <div class="setting-row">
          <span class="setting-label">{{ t('view.settings.host') }}</span>
          <span class="setting-value">{{ config.dash_host || '127.0.0.1' }}</span>
        </div>
        <div class="setting-row">
          <span class="setting-label">{{ t('view.settings.port') }}</span>
          <span class="setting-value">{{ config.dash_port || 9079 }}</span>
        </div>
        <div class="setting-row">
          <span class="setting-label">TLS</span>
          <span class="setting-value-group">
            <span class="status-dot" :class="config.tls_enabled ? 'on' : 'off'"></span>
            <span class="setting-value">{{ config.tls_enabled ? t('view.settings.enabled') : t('view.settings.disabled') }}</span>
          </span>
        </div>
        <div class="setting-row">
          <span class="setting-label">Auth</span>
          <span class="setting-value-group">
            <span class="status-dot" :class="config.auth_enabled ? 'on' : 'off'"></span>
            <span class="setting-value">{{ config.auth_enabled ? t('view.settings.enabled') : t('view.settings.disabled') }}</span>
          </span>
        </div>
        <div class="setting-row">
          <span class="setting-label">Debug</span>
          <span class="setting-value-group">
            <span class="status-dot" :class="config.debug ? 'on' : 'off'"></span>
            <span class="setting-value">{{ config.debug ? t('common.on') : t('common.off') }}</span>
          </span>
        </div>
        <div class="setting-row">
          <span class="setting-label">{{ t('view.settings.logLevel') }}</span>
          <span class="setting-value">{{ config.log_level || 'INFO' }}</span>
        </div>
        <div class="setting-row">
          <span class="setting-label">Workers</span>
          <span class="setting-value">{{ config.dash_workers || 1 }}</span>
        </div>
      </Card>

      <Card :title="t('settings.rateLimit')" :badge="t('view.settings.readOnly')" badge-tone="neutral">
        <div class="setting-row">
          <span class="setting-label">{{ t('view.settings.enabled') }}</span>
          <span class="setting-value-group">
            <span class="status-dot" :class="config.rate_limit_enabled !== false ? 'on' : 'off'"></span>
            <span class="setting-value">{{ config.rate_limit_enabled !== false ? t('view.settings.yes') : t('view.settings.no') }}</span>
          </span>
        </div>
        <div class="setting-row">
          <span class="setting-label">{{ t('view.settings.requestsPerSec') }}</span>
          <span class="setting-value">{{ config.rate_limit_rps || 30 }}</span>
        </div>
        <div class="setting-row">
          <span class="setting-label">{{ t('view.settings.burst') }}</span>
          <span class="setting-value">{{ config.rate_limit_burst || 60 }}</span>
        </div>
      </Card>

      <Card :title="t('settings.featureFlags')" :badge="t('view.settings.editionDetermined')" badge-tone="neutral">
        <p class="feature-hint">{{ t('view.settings.featureFlagsHint') }}</p>
        <div class="feature-grid">
          <div v-for="(enabled, name) in edition.features" :key="name" class="feature-item">
            <span class="status-dot small" :class="enabled ? 'on' : 'off'"></span>
            <span class="feature-name">{{ name }}</span>
          </div>
        </div>
      </Card>

      <Card :title="t('settings.dataPaths')" :badge="t('view.settings.readOnly')" badge-tone="neutral">
        <div class="setting-row">
          <span class="setting-label">{{ t('view.settings.rootDir') }}</span>
          <span class="setting-value path">{{ config.root_dir || 'auto' }}</span>
        </div>
        <div class="setting-row">
          <span class="setting-label">{{ t('view.settings.dataDir') }}</span>
          <span class="setting-value path">{{ config.data_dir || 'auto' }}</span>
        </div>
        <div class="setting-row">
          <span class="setting-label">{{ t('view.settings.dbPath') }}</span>
          <span class="setting-value path">{{ config.db_path || 'auto' }}</span>
        </div>
        <div class="setting-row">
          <span class="setting-label">{{ t('view.settings.memoryDb') }}</span>
          <span class="setting-value path">{{ config.memory_db_path || 'auto' }}</span>
        </div>
      </Card>

      <Card :title="t('settings.about')">
        <div class="about-version">
          <span class="about-name">MAOP</span>
          <span class="about-ver">v{{ appVersion }}</span>
        </div>
        <div class="about-section">
          <div class="about-section-title">{{ t('view.settings.techStack') }}</div>
          <div class="about-tags">
            <span class="about-tag">FastAPI</span>
            <span class="about-tag">Vue 3.5</span>
            <span class="about-tag">Vite</span>
            <span class="about-tag">Pinia</span>
            <span class="about-tag">Vitest</span>
            <span class="about-tag">pytest</span>
          </div>
        </div>
        <div class="about-section">
          <div class="about-section-title">{{ t('view.settings.archDecisions') }}</div>
          <ul class="adr-list">
            <li v-for="adr in adrs" :key="adr.number">
              <span class="adr-num">ADR-{{ adr.number }}</span>
              <span class="adr-title">{{ adr.title }}</span>
            </li>
          </ul>
        </div>
      </Card>
    </div>

    <!-- ── Tab: Config History ──────────────────────────────────── -->
    <div v-show="activeTab === 'history'" class="history-panel">
      <Card :title="t('view.settings.historyTitle')">
        <p class="history-hint">{{ t('view.settings.historyHint') }}</p>

        <div v-if="historyLoading" class="history-state">{{ t('view.settings.historyLoading') }}</div>
        <div v-else-if="historyError" class="history-state error">{{ historyError }}</div>
        <div v-else-if="historyItems.length === 0" class="history-state">{{ t('view.settings.historyEmpty') }}</div>

        <table v-else class="history-table">
          <thead>
            <tr>
              <th>{{ t('view.settings.colVersion') }}</th>
              <th>{{ t('view.settings.colChangedBy') }}</th>
              <th>{{ t('view.settings.colChangedAt') }}</th>
              <th class="history-actions-col">{{ t('view.settings.colActions') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in historyItems" :key="item.version">
              <td class="history-version-cell">
                <span class="history-version-num">v{{ item.version }}</span>
                <Badge v-if="item.version === latestVersion" tone="brand">{{ t('view.settings.latestBadge') }}</Badge>
              </td>
              <td>{{ item.changed_by }}</td>
              <td class="history-time-cell">{{ formatTime(item.changed_at) }}</td>
              <td class="history-actions-col">
                <button
                  type="button"
                  class="history-action-btn"
                  :disabled="historyDetailLoading === item.version"
                  @click="onViewDetail(item.version)"
                >{{ t('view.settings.viewDetail') }}</button>
                <button
                  type="button"
                  class="history-action-btn danger"
                  :disabled="!isAdmin || historyRollbackLoading === item.version || item.version === latestVersion"
                  @click="onRollback(item.version)"
                >{{ t('view.settings.rollback') }}</button>
              </td>
            </tr>
          </tbody>
        </table>
      </Card>
    </div>

    <!-- ── Tab: Hook Management（任务199）────────────────────────── -->
    <div v-show="activeTab === 'hooks'" class="hooks-panel">
      <Card :title="t('view.settings.tabHooks')">
        <p class="hooks-hint">{{ t('view.hooks.hint') }}</p>

        <div class="hooks-toolbar">
          <button type="button" class="hooks-btn primary" :disabled="!isAdmin" @click="openHookCreateDialog">
            {{ t('view.hooks.createBtn') }}
          </button>
          <button type="button" class="hooks-btn" :disabled="hooksLoading" @click="loadHooks">
            {{ t('view.hooks.refreshBtn') }}
          </button>
        </div>

        <div v-if="hooksLoading" class="hooks-state">{{ t('view.hooks.loading') }}</div>
        <div v-else-if="hooksError" class="hooks-state error">{{ hooksError }}</div>
        <div v-else-if="hooksList.length === 0" class="hooks-empty">
          <div class="hooks-empty-title">{{ t('view.hooks.empty') }}</div>
          <div class="hooks-empty-desc">{{ t('view.hooks.emptyDesc') }}</div>
        </div>

        <table v-else class="hooks-table">
          <thead>
            <tr>
              <th>{{ t('view.hooks.colName') }}</th>
              <th>{{ t('view.hooks.colEvent') }}</th>
              <th>{{ t('view.hooks.colUrl') }}</th>
              <th>{{ t('view.hooks.colEnabled') }}</th>
              <th class="hooks-actions-col">{{ t('view.hooks.colActions') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="hook in hooksList" :key="hook.id">
              <td class="hooks-name-cell">{{ hook.name }}</td>
              <td><span class="hooks-event-tag">{{ hook.event }}</span></td>
              <td class="hooks-url-cell">{{ hook.url }}</td>
              <td>
                <label class="hooks-toggle">
                  <input
                    type="checkbox"
                    :checked="hook.enabled"
                    :disabled="!isAdmin"
                    @change="onToggleHook(hook, $event.target.checked)"
                  />
                  <span class="hooks-toggle-label">{{ hook.enabled ? t('view.hooks.statusEnabled') : t('view.hooks.statusDisabled') }}</span>
                </label>
              </td>
              <td class="hooks-actions-col">
                <button type="button" class="hooks-action-btn" :disabled="!isAdmin" @click="onTestHook(hook)">{{ t('view.hooks.actionTest') }}</button>
                <button type="button" class="hooks-action-btn" :disabled="!isAdmin" @click="openHookEditDialog(hook)">{{ t('view.hooks.actionEdit') }}</button>
                <button type="button" class="hooks-action-btn danger" :disabled="!isAdmin" @click="onDeleteHook(hook)">{{ t('view.hooks.actionDelete') }}</button>
              </td>
            </tr>
          </tbody>
        </table>
      </Card>
    </div>

    <!-- ── Hook create/edit dialog（任务199）────────────────────── -->
    <div v-if="hookDialogOpen" class="hooks-modal-overlay" @click.self="closeHookDialog">
      <div class="hooks-modal" role="dialog" aria-modal="true">
        <header class="hooks-modal-head">
          <h3>{{ hookDialogMode === 'create' ? t('view.hooks.dialogTitleCreate') : t('view.hooks.dialogTitleEdit') }}</h3>
          <button type="button" class="hooks-modal-close" @click="closeHookDialog">✕</button>
        </header>
        <div class="hooks-modal-body">
          <div class="hooks-form-row">
            <label class="hooks-form-label">{{ t('view.hooks.fieldName') }}</label>
            <input v-model="hookForm.name" type="text" class="hooks-form-input" :placeholder="t('view.hooks.fieldName')" />
          </div>
          <div class="hooks-form-row">
            <label class="hooks-form-label">{{ t('view.hooks.fieldEvent') }}</label>
            <select v-model="hookForm.event" class="hooks-form-input">
              <option value="">{{ t('view.hooks.eventPlaceholder') }}</option>
              <option v-for="ev in hookEvents" :key="ev.name" :value="ev.name">{{ ev.name }}</option>
            </select>
          </div>
          <div class="hooks-form-row">
            <label class="hooks-form-label">{{ t('view.hooks.fieldUrl') }}</label>
            <input v-model="hookForm.url" type="text" class="hooks-form-input" :placeholder="t('view.hooks.placeholderUrl')" />
          </div>
          <div class="hooks-form-row">
            <label class="hooks-form-label">{{ t('view.hooks.fieldMethod') }}</label>
            <select v-model="hookForm.method" class="hooks-form-input">
              <option value="POST">POST</option>
            </select>
          </div>
          <div class="hooks-form-row">
            <label class="hooks-form-label">{{ t('view.hooks.fieldHeaders') }}</label>
            <input v-model="hookFormHeadersText" type="text" class="hooks-form-input" :placeholder="t('view.hooks.placeholderHeaders')" />
          </div>
          <div class="hooks-form-row-inline">
            <div class="hooks-form-row">
              <label class="hooks-form-label">{{ t('view.hooks.fieldTimeout') }}</label>
              <input v-model.number="hookForm.timeout" type="number" min="1" max="300" class="hooks-form-input" />
            </div>
            <div class="hooks-form-row">
              <label class="hooks-form-label">{{ t('view.hooks.fieldRetry') }}</label>
              <input v-model.number="hookForm.retry_count" type="number" min="0" max="10" class="hooks-form-input" />
            </div>
          </div>
          <div class="hooks-form-row">
            <label class="hooks-toggle">
              <input v-model="hookForm.enabled" type="checkbox" />
              <span class="hooks-toggle-label">{{ t('view.hooks.fieldEnabled') }}</span>
            </label>
          </div>
          <div v-if="hookFormError" class="hooks-form-error">{{ hookFormError }}</div>
        </div>
        <footer class="hooks-modal-foot">
          <button type="button" class="hooks-btn" @click="closeHookDialog">{{ t('view.hooks.btnCancel') }}</button>
          <button type="button" class="hooks-btn primary" :disabled="hookFormSaving" @click="onSaveHook">{{ t('view.hooks.btnSave') }}</button>
        </footer>
      </div>
    </div>

    <!-- ── Snapshot detail modal ────────────────────────────────── -->
    <div v-if="detailModalOpen" class="history-modal-overlay" @click.self="closeDetailModal">
      <div class="history-modal" role="dialog" aria-modal="true">
        <header class="history-modal-head">
          <h3>{{ t('view.settings.detailTitle') }}</h3>
          <button type="button" class="history-modal-close" @click="closeDetailModal">✕</button>
        </header>
        <div class="history-modal-body">
          <div v-if="historyDetailLoading !== null" class="history-state">{{ t('view.settings.historyLoading') }}</div>
          <template v-else-if="detailRecord">
            <div class="detail-meta-grid">
              <div class="detail-meta-item">
                <span class="detail-meta-label">{{ t('view.settings.detailVersion') }}</span>
                <span class="detail-meta-value">v{{ detailRecord.version }}</span>
              </div>
              <div class="detail-meta-item">
                <span class="detail-meta-label">{{ t('view.settings.detailChangedBy') }}</span>
                <span class="detail-meta-value">{{ detailRecord.changed_by }}</span>
              </div>
              <div class="detail-meta-item">
                <span class="detail-meta-label">{{ t('view.settings.detailChangedAt') }}</span>
                <span class="detail-meta-value">{{ formatTime(detailRecord.changed_at) }}</span>
              </div>
            </div>
            <div class="detail-payload-section">
              <div class="detail-payload-title">{{ t('view.settings.detailPayload') }}</div>
              <pre class="detail-payload-pre">{{ JSON.stringify(detailRecord.snapshot, null, 2) }}</pre>
            </div>
          </template>
        </div>
        <footer class="history-modal-foot">
          <button type="button" class="history-action-btn" @click="closeDetailModal">{{ t('view.settings.close') }}</button>
        </footer>
      </div>
    </div>
  </div>
</template>
<script setup>
import { ref, onMounted } from 'vue';
import { useApiStore } from '../stores/api.js';
import { useEditionStore } from '../stores/edition.js';
import { useUiStore } from '../stores/ui.js';
import { useI18n } from '../i18n/index.js';
import { Card, Badge, Segmented, PageHeader } from '../components/index.js';

const api = useApiStore();
const editionStore = useEditionStore();
const ui = useUiStore();
const { t } = useI18n();
const edition = ref({});
const config = ref({});
const isAdmin = ref(false);
const switchNotice = ref('');
const switchNoticeDegraded = ref(false);

// ── Tab state ─────────────────────────────────────────────────────
const activeTab = ref('config');

// ── Config history state ──────────────────────────────────────────
const historyItems = ref([]);
const historyLoading = ref(false);
const historyError = ref('');
const latestVersion = ref(null);
const historyDetailLoading = ref(null);
const historyRollbackLoading = ref(null);
const detailModalOpen = ref(false);
const detailRecord = ref(null);

async function detectAdmin() {
  try {
    const rolesStr = localStorage.getItem('maop_roles');
    if (rolesStr) {
      const roles = JSON.parse(rolesStr);
      if (Array.isArray(roles) && roles.some(r => r === 'admin' || r === 'superadmin')) return true;
    }
  } catch { /* ignore malformed roles */ }
  // Auth disabled (e.g. MAOP_AUTH_DISABLED_ADMIN) → treat the session as superuser.
  try {
    const d = await api.get('/api/auth/status');
    if (d && d.auth_enabled === false) return true;
  } catch { /* ignore */ }
  try {
    return localStorage.getItem('maop_user') === 'admin';
  } catch { return false; }
}

async function onSwitchClick(target) {
  if (target === edition.value.edition) return;
  const label = target === 'enterprise' ? t('view.settings.enterprise') : t('view.settings.personal');
  const featureDesc = target === 'enterprise'
    ? t('view.settings.editionToEnterpriseDesc')
    : t('view.settings.editionToPersonalDesc');
  const ok = confirm(t('view.settings.editionSwitchConfirm', { label, featureDesc }));
  if (!ok) return;
  switchNotice.value = '';
  switchNoticeDegraded.value = false;
  try {
    const result = await editionStore.switchEdition(target);
    edition.value = {
      edition: editionStore.edition,
      features: editionStore.features,
      backends: editionStore.backends,
      degradations: editionStore.degradations,
      enterprise_available: editionStore.isEnterprise,
    };
    if (result.degraded) {
      switchNotice.value = switchNoticeText(result);
      switchNoticeDegraded.value = true;
    } else {
      switchNotice.value = switchNoticeText(result);
    }
  } catch { /* error already in store.switchError */ }
}

function switchNoticeText(result) {
  if (result.degraded) {
    return t('view.settings.editionSwitchDegraded', { requested: result.requested, edition: result.edition });
  }
  return t('view.settings.editionSwitchDone', { edition: result.edition });
}

const appVersion = ref('…');

const adrsFallback = [
  { number: '001', filename: '001-python-yaml-bridge.md', title: 'Python ↔ YAML Bridge' },
  { number: '002', filename: '002-server-merge-orchestrator-deprecation.md', title: 'Server Merge / Orchestrator Deprecation' },
  { number: '003', filename: '003-mock-fallback-removal.md', title: 'Mock Fallback Removal' },
  { number: '004', filename: '004-security-hardening.md', title: 'Security Hardening' },
  { number: '005', filename: '005-powershell-retention.md', title: 'PowerShell Retention' },
  { number: '006', filename: '006-sse-removal-sync-architecture.md', title: 'SSE Retained (Superseded Removal)' },
  { number: '007', filename: '007-cache-warmup-fix.md', title: 'Cache Warmup Fix' },
  { number: '008', filename: '008-dual-arch-scheduling-audit.md', title: 'Dual-Arch Scheduling Audit' },
  { number: '009', filename: '009-python-primary-engine.md', title: 'Python Primary Engine' },
  { number: '010', filename: '010-bugfix-batch.md', title: 'Bugfix Batch' },
  { number: '011', filename: '011-state-unification.md', title: 'State Unification' },
  { number: '012', filename: '012-routing-refactor.md', title: 'Routing Refactor' },
  { number: '013', filename: '013-agent-llm-direct-cli-fallback.md', title: 'Agent LLM Direct + CLI Fallback' },
];
const adrs = ref(adrsFallback);

async function loadAdrs() {
  try {
    const data = await api.get('/api/info/adrs');
    if (Array.isArray(data) && data.length > 0) adrs.value = data;
  } catch { /* keep fallback list */ }
}

// ── Config history helpers ────────────────────────────────────────
function formatTime(iso) {
  if (!iso) return '';
  // Show local time; fall back to raw string on parse failure.
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleString();
  } catch { return iso; }
}

async function loadHistory() {
  historyLoading.value = true;
  historyError.value = '';
  try {
    const data = await api.get('/api/config/history?limit=100');
    const items = Array.isArray(data.history) ? data.history : [];
    historyItems.value = items;
    latestVersion.value = items.length > 0 ? items[0].version : null;
  } catch (e) {
    historyError.value = t('view.settings.historyError') + (e && e.message ? `: ${e.message}` : '');
  } finally {
    historyLoading.value = false;
  }
}

async function onViewDetail(version) {
  detailModalOpen.value = true;
  detailRecord.value = null;
  historyDetailLoading.value = version;
  try {
    const data = await api.get(`/api/config/history/${version}`);
    detailRecord.value = data;
  } catch (e) {
    detailRecord.value = null;
    historyError.value = t('view.settings.historyError') + (e && e.message ? `: ${e.message}` : '');
  } finally {
    historyDetailLoading.value = null;
  }
}

function closeDetailModal() {
  detailModalOpen.value = false;
  detailRecord.value = null;
}

async function onRollback(version) {
  const ok = confirm(t('view.settings.rollbackConfirm', { version }));
  if (!ok) return;
  historyRollbackLoading.value = version;
  try {
    const result = await api.post(`/api/config/rollback/${version}`);
    // Refresh history list to show the new rollback snapshot.
    await loadHistory();
    // Best-effort notice via switchNotice (re-using the notice slot).
    const fromV = result && result.restored_from_version !== undefined ? result.restored_from_version : version;
    const newV = result && result.new_version !== undefined ? result.new_version : '?';
    switchNotice.value = t('view.settings.rollbackSuccess', { from: fromV, new: newV });
    switchNoticeDegraded.value = false;
  } catch (e) {
    switchNotice.value = t('view.settings.rollbackFailed') + (e && e.message ? `: ${e.message}` : '');
    switchNoticeDegraded.value = true;
  } finally {
    historyRollbackLoading.value = null;
  }
}

async function load() {
  isAdmin.value = await detectAdmin();
  await editionStore.fetchEdition();
  edition.value = {
    edition: editionStore.edition,
    features: editionStore.features,
    backends: editionStore.backends,
    degradations: editionStore.degradations,
    enterprise_available: editionStore.isEnterprise,
  };
  try { config.value = await api.get('/api/info/config'); } catch { config.value = {}; }
  try { const h = await api.get('/api/health'); if (h && h.version) appVersion.value = h.version; } catch { /* keep placeholder */ }
  loadAdrs();
  // Pre-load config history in parallel (best-effort; admin-gated on server).
  loadHistory().catch(() => { /* non-fatal — tab will show error state */ });
  // 任务199: 预加载 Hook 列表与事件类型
  loadHooks().catch(() => { /* non-fatal */ });
  loadHookEvents().catch(() => { /* non-fatal */ });
}

onMounted(load);

// ── Hook 管理（任务199）─────────────────────────────────────────────
const hooksList = ref([]);
const hooksLoading = ref(false);
const hooksError = ref('');
const hookEvents = ref([]);

const hookDialogOpen = ref(false);
const hookDialogMode = ref('create');  // 'create' | 'edit'
const hookFormSaving = ref(false);
const hookFormError = ref('');
const hookForm = ref({
  id: '',
  name: '',
  event: '',
  url: '',
  method: 'POST',
  headers: {},
  enabled: true,
  timeout: 10,
  retry_count: 0,
});
// headers 用文本框编辑（JSON 字符串），保存时解析
const hookFormHeadersText = ref('{}');

async function loadHooks() {
  hooksLoading.value = true;
  hooksError.value = '';
  try {
    const data = await api.get('/api/hooks');
    hooksList.value = Array.isArray(data.hooks) ? data.hooks : [];
  } catch (e) {
    hooksError.value = t('view.hooks.loadError') + (e && e.message ? `: ${e.message}` : '');
    hooksList.value = [];
  } finally {
    hooksLoading.value = false;
  }
}

async function loadHookEvents() {
  try {
    const data = await api.get('/api/hooks/events');
    hookEvents.value = Array.isArray(data.events) ? data.events : [];
  } catch { /* 静默失败，下拉框为空 */ }
}

function openHookCreateDialog() {
  hookDialogMode.value = 'create';
  hookForm.value = { id: '', name: '', event: '', url: '', method: 'POST', headers: {}, enabled: true, timeout: 10, retry_count: 0 };
  hookFormHeadersText.value = '{}';
  hookFormError.value = '';
  hookDialogOpen.value = true;
}

function openHookEditDialog(hook) {
  hookDialogMode.value = 'edit';
  hookForm.value = {
    id: hook.id,
    name: hook.name || '',
    event: hook.event || '',
    url: hook.url || '',
    method: hook.method || 'POST',
    headers: hook.headers || {},
    enabled: hook.enabled !== false,
    timeout: hook.timeout || 10,
    retry_count: hook.retry_count || 0,
  };
  try { hookFormHeadersText.value = JSON.stringify(hookForm.value.headers); } catch { hookFormHeadersText.value = '{}'; }
  hookFormError.value = '';
  hookDialogOpen.value = true;
}

function closeHookDialog() {
  hookDialogOpen.value = false;
  hookFormError.value = '';
}

function validateHookForm() {
  if (!hookForm.value.name) return t('view.hooks.validateNameRequired');
  if (!hookForm.value.event) return t('view.hooks.validateEventRequired');
  if (!hookForm.value.url) return t('view.hooks.validateUrlRequired');
  try { JSON.parse(hookFormHeadersText.value || '{}'); } catch { return t('view.hooks.validateHeadersJson'); }
  return '';
}

async function onSaveHook() {
  const err = validateHookForm();
  if (err) { hookFormError.value = err; return; }
  hookFormSaving.value = true;
  hookFormError.value = '';
  try {
    let headers = {};
    try { headers = JSON.parse(hookFormHeadersText.value || '{}'); } catch { /* 已校验 */ }
    const payload = {
      name: hookForm.value.name,
      event: hookForm.value.event,
      url: hookForm.value.url,
      method: hookForm.value.method,
      headers,
      enabled: hookForm.value.enabled,
      timeout: hookForm.value.timeout,
      retry_count: hookForm.value.retry_count,
    };
    if (hookDialogMode.value === 'create') {
      await api.post('/api/hooks', payload);
    } else {
      await api.put(`/api/hooks/${hookForm.value.id}`, payload);
    }
    closeHookDialog();
    await loadHooks();
  } catch (e) {
    hookFormError.value = t('view.hooks.saveError') + (e && e.message ? `: ${e.message}` : '');
  } finally {
    hookFormSaving.value = false;
  }
}

async function onDeleteHook(hook) {
  const ok = confirm(t('view.hooks.deleteConfirm', { name: hook.name }));
  if (!ok) return;
  try {
    await api.delete(`/api/hooks/${hook.id}`);
    await loadHooks();
  } catch (e) {
    hooksError.value = t('view.hooks.deleteError') + (e && e.message ? `: ${e.message}` : '');
  }
}

async function onToggleHook(hook, enabled) {
  try {
    if (enabled) {
      await api.post(`/api/hooks/${hook.id}/enable`);
    } else {
      await api.post(`/api/hooks/${hook.id}/disable`);
    }
    hook.enabled = enabled;
  } catch (e) {
    hooksError.value = t('view.hooks.saveError') + (e && e.message ? `: ${e.message}` : '');
    // 失败时回滚 UI 状态
    hook.enabled = !enabled;
  }
}

async function onTestHook(hook) {
  try {
    const result = await api.post(`/api/hooks/${hook.id}/test`);
    if (result.success) {
      if (result.response === 'no listener') {
        alert(t('view.hooks.testNoListener'));
      } else {
        alert(t('view.hooks.testSuccess', { ms: result.duration_ms || 0 }));
      }
    } else {
      alert(t('view.hooks.testFailed', { error: result.error || 'unknown' }));
    }
  } catch (e) {
    alert(t('view.hooks.testError') + (e && e.message ? `: ${e.message}` : ''));
  }
}
</script>
<style scoped>
/* ── Tab switcher ────────────────────────────────────────────────── */
.settings-tabs {
  display: flex;
  gap: var(--sp-1);
  margin-bottom: var(--sp-4);
  border-bottom: 1px solid var(--border);
}
.settings-tab {
  appearance: none;
  background: transparent;
  border: none;
  border-bottom: 2px solid transparent;
  padding: var(--sp-2) var(--sp-4);
  font-size: var(--fs-md);
  font-weight: 500;
  color: var(--text-muted);
  cursor: pointer;
  transition: color var(--motion) var(--ease), border-color var(--motion) var(--ease);
}
.settings-tab:hover { color: var(--text); }
.settings-tab.active {
  color: var(--brand-strong);
  border-bottom-color: var(--brand);
}

/* ── Config history panel ───────────────────────────────────────── */
.history-panel { margin-bottom: var(--sp-4); }
.history-hint {
  margin: 0 0 var(--sp-3) 0;
  font-size: var(--fs-sm);
  color: var(--text-muted);
}
.history-state {
  padding: var(--sp-4);
  text-align: center;
  color: var(--text-muted);
  font-size: var(--fs-sm);
}
.history-state.error { color: var(--danger, #c0392b); }

.history-table {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--fs-sm);
}
.history-table th,
.history-table td {
  padding: var(--sp-2) var(--sp-3);
  text-align: left;
  border-bottom: 1px solid var(--border-subtle, var(--border));
}
.history-table th {
  font-weight: 600;
  color: var(--text-muted);
  background: var(--surface-2, var(--surface));
}
.history-version-cell {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
}
.history-version-num { font-weight: 600; color: var(--text); }
.history-time-cell { color: var(--text-muted); white-space: nowrap; }
.history-actions-col { white-space: nowrap; text-align: right; }
.history-action-btn {
  appearance: none;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r-sm);
  padding: 2px var(--sp-2);
  font-size: var(--fs-sm);
  color: var(--text);
  cursor: pointer;
  margin-left: var(--sp-1);
  transition: border-color var(--motion) var(--ease), background var(--motion) var(--ease);
}
.history-action-btn:hover:not(:disabled) {
  border-color: var(--brand);
  background: var(--brand-soft);
}
.history-action-btn.danger:hover:not(:disabled) {
  border-color: var(--danger, #c0392b);
  background: var(--danger-soft, rgba(192, 57, 43, 0.08));
}
.history-action-btn:disabled { opacity: 0.5; cursor: not-allowed; }

/* ── Snapshot detail modal ──────────────────────────────────────── */
.history-modal-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.4);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
}
.history-modal {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r-lg);
  width: min(720px, 90vw);
  max-height: 80vh;
  display: flex;
  flex-direction: column;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.12);
}
.history-modal-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--sp-3) var(--sp-4);
  border-bottom: 1px solid var(--border);
}
.history-modal-head h3 { margin: 0; font-size: var(--fs-md); font-weight: 600; }
.history-modal-close {
  appearance: none;
  background: transparent;
  border: none;
  font-size: var(--fs-md);
  color: var(--text-muted);
  cursor: pointer;
  padding: 0 var(--sp-1);
}
.history-modal-close:hover { color: var(--text); }
.history-modal-body {
  padding: var(--sp-4);
  overflow-y: auto;
  flex: 1;
}
.detail-meta-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: var(--sp-3);
  margin-bottom: var(--sp-4);
}
.detail-meta-item { display: flex; flex-direction: column; gap: 2px; }
.detail-meta-label { font-size: var(--fs-sm); color: var(--text-muted); }
.detail-meta-value { font-weight: 500; color: var(--text); }
.detail-payload-section { border-top: 1px solid var(--border-subtle, var(--border)); padding-top: var(--sp-3); }
.detail-payload-title { font-size: var(--fs-sm); color: var(--text-muted); margin-bottom: var(--sp-2); }
.detail-payload-pre {
  background: var(--surface-2, var(--surface));
  border: 1px solid var(--border-subtle, var(--border));
  border-radius: var(--r-sm);
  padding: var(--sp-3);
  font-size: var(--fs-sm);
  font-family: var(--font-mono, monospace);
  overflow-x: auto;
  max-height: 320px;
  overflow-y: auto;
  margin: 0;
}
.history-modal-foot {
  padding: var(--sp-3) var(--sp-4);
  border-top: 1px solid var(--border);
  display: flex;
  justify-content: flex-end;
}

/* ── Hook 管理 Tab（任务199）────────────────────────────────────── */
.hooks-panel { margin-bottom: var(--sp-4); }
.hooks-hint {
  margin: 0 0 var(--sp-3) 0;
  font-size: var(--fs-sm);
  color: var(--text-muted);
}
.hooks-toolbar {
  display: flex;
  gap: var(--sp-2);
  margin-bottom: var(--sp-3);
}
.hooks-btn {
  appearance: none;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r-sm);
  padding: var(--sp-2) var(--sp-3);
  font-size: var(--fs-sm);
  color: var(--text);
  cursor: pointer;
  transition: border-color var(--motion) var(--ease), background var(--motion) var(--ease);
}
.hooks-btn:hover:not(:disabled) {
  border-color: var(--brand);
  background: var(--brand-soft);
}
.hooks-btn.primary {
  background: var(--brand);
  border-color: var(--brand);
  color: white;
}
.hooks-btn.primary:hover:not(:disabled) {
  background: var(--brand-strong);
}
.hooks-btn:disabled { opacity: 0.5; cursor: not-allowed; }

.hooks-state {
  padding: var(--sp-4);
  text-align: center;
  color: var(--text-muted);
  font-size: var(--fs-sm);
}
.hooks-state.error { color: var(--danger, #c0392b); }
.hooks-empty {
  padding: var(--sp-5);
  text-align: center;
}
.hooks-empty-title { font-size: var(--fs-md); color: var(--text); margin-bottom: var(--sp-2); }
.hooks-empty-desc { font-size: var(--fs-sm); color: var(--text-muted); }

.hooks-table {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--fs-sm);
}
.hooks-table th,
.hooks-table td {
  padding: var(--sp-2) var(--sp-3);
  text-align: left;
  border-bottom: 1px solid var(--border-subtle, var(--border));
}
.hooks-table th {
  font-weight: 600;
  color: var(--text-muted);
  background: var(--surface-2, var(--surface));
}
.hooks-name-cell { font-weight: 500; color: var(--text); }
.hooks-url-cell {
  color: var(--text-muted);
  max-width: 280px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.hooks-event-tag {
  display: inline-block;
  padding: 1px var(--sp-2);
  border-radius: var(--r-sm);
  background: var(--brand-soft);
  color: var(--brand-strong);
  font-size: var(--fs-xs);
  font-family: var(--font-mono, monospace);
}
.hooks-actions-col { white-space: nowrap; text-align: right; }
.hooks-action-btn {
  appearance: none;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r-sm);
  padding: 2px var(--sp-2);
  font-size: var(--fs-sm);
  color: var(--text);
  cursor: pointer;
  margin-left: var(--sp-1);
  transition: border-color var(--motion) var(--ease), background var(--motion) var(--ease);
}
.hooks-action-btn:hover:not(:disabled) {
  border-color: var(--brand);
  background: var(--brand-soft);
}
.hooks-action-btn.danger:hover:not(:disabled) {
  border-color: var(--danger, #c0392b);
  background: var(--danger-soft, rgba(192, 57, 43, 0.08));
}
.hooks-action-btn:disabled { opacity: 0.5; cursor: not-allowed; }

.hooks-toggle {
  display: inline-flex;
  align-items: center;
  gap: var(--sp-2);
  cursor: pointer;
}
.hooks-toggle input[type="checkbox"] { cursor: pointer; }
.hooks-toggle-label { font-size: var(--fs-sm); color: var(--text-muted); }

/* ── Hook dialog ─────────────────────────────────────────────── */
.hooks-modal-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.4);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
}
.hooks-modal {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r-lg);
  width: min(560px, 90vw);
  max-height: 85vh;
  display: flex;
  flex-direction: column;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.12);
}
.hooks-modal-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--sp-3) var(--sp-4);
  border-bottom: 1px solid var(--border);
}
.hooks-modal-head h3 { margin: 0; font-size: var(--fs-md); font-weight: 600; }
.hooks-modal-close {
  appearance: none;
  background: transparent;
  border: none;
  font-size: var(--fs-md);
  color: var(--text-muted);
  cursor: pointer;
  padding: 0 var(--sp-1);
}
.hooks-modal-close:hover { color: var(--text); }
.hooks-modal-body {
  padding: var(--sp-4);
  overflow-y: auto;
  flex: 1;
}
.hooks-form-row {
  display: flex;
  flex-direction: column;
  gap: var(--sp-1);
  margin-bottom: var(--sp-3);
}
.hooks-form-row-inline {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--sp-3);
}
.hooks-form-label {
  font-size: var(--fs-sm);
  color: var(--text-muted);
  font-weight: 500;
}
.hooks-form-input {
  appearance: none;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r-sm);
  padding: var(--sp-2) var(--sp-3);
  font-size: var(--fs-sm);
  color: var(--text);
  width: 100%;
  box-sizing: border-box;
}
.hooks-form-input:focus {
  outline: none;
  border-color: var(--brand);
  box-shadow: 0 0 0 2px var(--brand-soft);
}
.hooks-form-error {
  color: var(--danger, #c0392b);
  font-size: var(--fs-sm);
  margin-top: var(--sp-2);
}
.hooks-modal-foot {
  padding: var(--sp-3) var(--sp-4);
  border-top: 1px solid var(--border);
  display: flex;
  justify-content: flex-end;
  gap: var(--sp-2);
}
</style>
