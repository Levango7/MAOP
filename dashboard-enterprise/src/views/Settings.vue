<template>
  <div class="settings-page view-enter">
    <PageHeader />

    <div class="settings-grid">
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
            <span class="switching-indicator" v-if="editionStore.switching">{{ t('view.settings.switching') }}</span>
          </span>
        </div>
        <div class="setting-row" v-if="!isAdmin">
          <span class="setting-label"></span>
          <span class="edition-perm-hint">{{ t('view.settings.adminRequired') }}</span>
        </div>
        <div class="setting-row" v-if="editionStore.switchError">
          <span class="setting-label"></span>
          <span class="edition-error-msg">{{ editionStore.switchError }}</span>
        </div>
        <div class="setting-row" v-if="switchNotice">
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
        <div class="setting-row" v-if="edition.degradations && edition.degradations.length > 0">
          <span class="setting-label">{{ t('view.settings.degradations') }}</span>
          <span class="degradation-count">{{ edition.degradations.length }}</span>
        </div>
        <div v-if="edition.degradations && edition.degradations.length > 0" class="degradation-list">
          <div class="degradation-item" v-for="(d, i) in edition.degradations" :key="i">
            <span class="deg-backend">{{ d.backend }}</span>
            <span class="deg-arrow">→</span>
            <span class="deg-fallback">{{ d.fallback }}</span>
            <span class="deg-reason">({{ d.reason }})</span>
          </div>
        </div>
      </Card>

      <Card :title="t('settings.backends')" :badge="t('view.settings.readOnly')" badge-tone="neutral">
        <div class="setting-row" v-for="(val, key) in edition.backends" :key="key">
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
          <div class="feature-item" v-for="(enabled, name) in edition.features" :key="name">
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

async function detectAdmin() {
  try {
    const rolesStr = localStorage.getItem('maop_roles');
    if (rolesStr) {
      const roles = JSON.parse(rolesStr);
      if (Array.isArray(roles) && roles.some(r => r === 'admin' || r === 'superadmin')) return true;
    }
  } catch (e) { /* ignore malformed roles */ }
  // Auth disabled (e.g. MAOP_AUTH_DISABLED_ADMIN) → treat the session as superuser.
  try {
    const d = await api.get('/api/auth/status');
    if (d && d.auth_enabled === false) return true;
  } catch (e) { /* ignore */ }
  try {
    return localStorage.getItem('maop_user') === 'admin';
  } catch (e) { return false; }
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
  } catch (e) { /* error already in store.switchError */ }
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
  } catch (e) { /* keep fallback list */ }
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
}

onMounted(load);
</script>
<style scoped>
</style>
