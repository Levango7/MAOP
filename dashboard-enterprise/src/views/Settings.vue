<template>
  <div>
    <div class="topbar"><h1>Settings</h1></div>

    <div class="settings-grid">
      <div class="panel">
        <h3>Edition</h3>
        <div class="setting-row">
          <span class="setting-label">Current Edition</span>
          <span class="edition-badge" :class="edition.edition">{{ edition.edition || 'personal' }}</span>
        </div>
        <div class="setting-row">
          <span class="setting-label">Enterprise Available</span>
          <span class="status-dot" :class="edition.enterprise_available ? 'on' : 'off'"></span>
          <span class="setting-value">{{ edition.enterprise_available ? 'Yes' : 'No' }}</span>
        </div>
        <div class="setting-row" v-if="edition.degradations && edition.degradations.length > 0">
          <span class="setting-label">Degradations</span>
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
      </div>

      <div class="panel">
        <h3>Backends</h3>
        <div class="setting-row" v-for="(val, key) in edition.backends" :key="key">
          <span class="setting-label">{{ key }}</span>
          <span class="backend-tag">{{ val }}</span>
        </div>
      </div>

      <div class="panel">
        <h3>Server</h3>
        <div class="setting-row">
          <span class="setting-label">Host</span>
          <span class="setting-value">{{ config.dash_host || '127.0.0.1' }}</span>
        </div>
        <div class="setting-row">
          <span class="setting-label">Port</span>
          <span class="setting-value">{{ config.dash_port || 9079 }}</span>
        </div>
        <div class="setting-row">
          <span class="setting-label">TLS</span>
          <span class="status-dot" :class="config.tls_enabled ? 'on' : 'off'"></span>
          <span class="setting-value">{{ config.tls_enabled ? 'Enabled' : 'Disabled' }}</span>
        </div>
        <div class="setting-row">
          <span class="setting-label">Auth</span>
          <span class="status-dot" :class="config.auth_enabled ? 'on' : 'off'"></span>
          <span class="setting-value">{{ config.auth_enabled ? 'Enabled' : 'Disabled' }}</span>
        </div>
        <div class="setting-row">
          <span class="setting-label">Debug</span>
          <span class="status-dot" :class="config.debug ? 'on' : 'off'"></span>
          <span class="setting-value">{{ config.debug ? 'On' : 'Off' }}</span>
        </div>
        <div class="setting-row">
          <span class="setting-label">Log Level</span>
          <span class="setting-value">{{ config.log_level || 'INFO' }}</span>
        </div>
        <div class="setting-row">
          <span class="setting-label">Workers</span>
          <span class="setting-value">{{ config.dash_workers || 1 }}</span>
        </div>
      </div>

      <div class="panel">
        <h3>Rate Limiting</h3>
        <div class="setting-row">
          <span class="setting-label">Enabled</span>
          <span class="status-dot" :class="config.rate_limit_enabled !== false ? 'on' : 'off'"></span>
          <span class="setting-value">{{ config.rate_limit_enabled !== false ? 'Yes' : 'No' }}</span>
        </div>
        <div class="setting-row">
          <span class="setting-label">Requests/sec</span>
          <span class="setting-value">{{ config.rate_limit_rps || 30 }}</span>
        </div>
        <div class="setting-row">
          <span class="setting-label">Burst</span>
          <span class="setting-value">{{ config.rate_limit_burst || 60 }}</span>
        </div>
      </div>

      <div class="panel">
        <h3>Feature Flags</h3>
        <div class="feature-grid">
          <div class="feature-item" v-for="(enabled, name) in edition.features" :key="name">
            <span class="status-dot small" :class="enabled ? 'on' : 'off'"></span>
            <span class="feature-name">{{ name }}</span>
          </div>
        </div>
      </div>

      <div class="panel">
        <h3>Data Paths</h3>
        <div class="setting-row">
          <span class="setting-label">Root Dir</span>
          <span class="setting-value path">{{ config.root_dir || 'auto' }}</span>
        </div>
        <div class="setting-row">
          <span class="setting-label">Data Dir</span>
          <span class="setting-value path">{{ config.data_dir || 'auto' }}</span>
        </div>
        <div class="setting-row">
          <span class="setting-label">DB Path</span>
          <span class="setting-value path">{{ config.db_path || 'auto' }}</span>
        </div>
        <div class="setting-row">
          <span class="setting-label">Memory DB</span>
          <span class="setting-value path">{{ config.memory_db_path || 'auto' }}</span>
        </div>
      </div>

      <!-- t21c: About panel — replaces the v3.x-era static HTML dashboard page.
           The native JS dashboard has been archived (archive/js-dashboard/),
           and provider._render_html is deprecated. SSE is retained (see ADR-006). -->
      <div class="panel about-panel">
        <h3>About</h3>
        <div class="about-version">
          <span class="about-name">MAOP</span>
          <span class="about-ver">v4.0.0</span>
        </div>
        <div class="about-section">
          <div class="about-section-title">Tech Stack</div>
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
          <div class="about-section-title">Frontend</div>
          <div class="about-text">
            Unified Vue3 SPA served from <code>dashboard/dist-enterprise/</code>.
            The legacy native-JS dashboard has been archived to
            <code>archive/js-dashboard/</code>.
          </div>
        </div>
        <div class="about-section">
          <div class="about-section-title">Architecture Decisions</div>
          <ul class="adr-list">
            <li v-for="a in adrs" :key="a.num">
              <a :href="`/docs/adr/${a.file}`" target="_blank" rel="noopener">
                <span class="adr-num">ADR-{{ a.num }}</span>
                <span class="adr-title">{{ a.title }}</span>
              </a>
            </li>
          </ul>
        </div>
      </div>
    </div>
  </div>
</template>
<script setup>
import { ref, onMounted } from 'vue';
import { useApiStore } from '../stores/api.js';
import { useEditionStore } from '../stores/edition.js';
const api = useApiStore();
const editionStore = useEditionStore();
const edition = ref({});
const config = ref({});

// t21c: ADR index. Static to avoid an extra API round-trip; update here when
// a new ADR is added. Linked from the About panel.
const adrs = ref([
  { num: '001', file: '001-python-yaml-bridge.md', title: 'Python ↔ YAML Bridge' },
  { num: '002', file: '002-server-merge-orchestrator-deprecation.md', title: 'Server Merge / Orchestrator Deprecation' },
  { num: '003', file: '003-mock-fallback-removal.md', title: 'Mock Fallback Removal' },
  { num: '004', file: '004-security-hardening.md', title: 'Security Hardening' },
  { num: '005', file: '005-powershell-retention.md', title: 'PowerShell Retention' },
  { num: '006', file: '006-sse-removal-sync-architecture.md', title: 'SSE Retained (Superseded Removal)' },
  { num: '007', file: '007-cache-warmup-fix.md', title: 'Cache Warmup Fix' },
  { num: '008', file: '008-dual-arch-scheduling-audit.md', title: 'Dual-Arch Scheduling Audit' },
  { num: '009', file: '009-python-primary-engine.md', title: 'Python Primary Engine' },
  { num: '010', file: '010-bugfix-batch.md', title: 'Bugfix Batch' },
  { num: '011', file: '011-state-unification.md', title: 'State Unification' },
  { num: '012', file: '012-routing-refactor.md', title: 'Routing Refactor' },
  { num: '013', file: '013-agent-llm-direct-cli-fallback.md', title: 'Agent LLM Direct + CLI Fallback' },
]);

async function load() {
  await editionStore.fetchEdition();
  edition.value = {
    edition: editionStore.edition,
    features: editionStore.features,
    backends: editionStore.backends,
    degradations: editionStore.degradations,
    enterprise_available: editionStore.isEnterprise,
  };
  try { config.value = await api.get('/api/info/config'); } catch { config.value = {}; }
}

onMounted(load);
</script>
<style scoped>
.topbar { display: flex; align-items: center; gap: 12px; margin-bottom: 24px; }
.topbar h1 { font-size: 24px; font-weight: 700; }
.settings-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 16px; }
.panel { background: var(--bg2); border: 1px solid var(--border); border-radius: var(--radius); padding: 20px; box-shadow: var(--shadow); }
.panel h3 { font-size: 14px; font-weight: 600; margin-bottom: 16px; color: var(--text2); }
.setting-row { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; }
.setting-label { width: 120px; font-size: 13px; color: var(--text2); flex-shrink: 0; }
.setting-value { font-size: 13px; color: var(--text1); }
.setting-value.path { font-family: monospace; font-size: 12px; color: var(--text3); word-break: break-all; }
.edition-badge { padding: 3px 10px; border-radius: 6px; font-size: 12px; font-weight: 600; text-transform: capitalize; }
.edition-badge.enterprise { background: rgba(0,113,227,.12); color: var(--accent); }
.edition-badge.personal { background: rgba(34,197,94,.12); color: var(--success); }
.status-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.status-dot.on { background: var(--success); }
.status-dot.off { background: var(--text3); opacity: .4; }
.status-dot.small { width: 6px; height: 6px; }
.backend-tag { padding: 2px 8px; border-radius: 4px; font-size: 12px; font-weight: 500; background: var(--bg3); color: var(--text2); }
.degradation-count { padding: 2px 8px; border-radius: 4px; font-size: 12px; font-weight: 600; background: rgba(239,68,68,.12); color: var(--fail); }
.degradation-list { margin-top: 8px; padding: 10px; background: var(--bg1); border: 1px solid var(--border); border-radius: 8px; }
.degradation-item { display: flex; align-items: center; gap: 6px; font-size: 12px; margin-bottom: 4px; }
.deg-backend { color: var(--fail); font-weight: 600; }
.deg-arrow { color: var(--text3); }
.deg-fallback { color: var(--success); font-weight: 600; }
.deg-reason { color: var(--text3); font-size: 11px; }
.feature-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 6px; }
.feature-item { display: flex; align-items: center; gap: 6px; }
.feature-name { font-size: 12px; color: var(--text2); }

/* t21c: About panel */
.about-panel .about-version { display: flex; align-items: baseline; gap: 10px; margin-bottom: 16px; }
.about-name { font-size: 22px; font-weight: 700; letter-spacing: .5px; }
.about-ver { font-size: 13px; padding: 3px 10px; border-radius: 6px; background: rgba(59,130,246,.12); color: var(--accent); font-weight: 600; }
.about-section { margin-bottom: 14px; }
.about-section:last-child { margin-bottom: 0; }
.about-section-title { font-size: 11px; text-transform: uppercase; letter-spacing: .5px; color: var(--text3); margin-bottom: 6px; }
.about-tags { display: flex; flex-wrap: wrap; gap: 6px; }
.about-tag { padding: 3px 9px; border-radius: 6px; font-size: 12px; font-weight: 500; background: var(--bg3); color: var(--text2); border: 1px solid var(--border); }
.about-text { font-size: 12px; color: var(--text2); line-height: 1.5; }
.about-text code { font-family: monospace; font-size: 11px; padding: 1px 5px; border-radius: 3px; background: var(--bg1); border: 1px solid var(--border); color: var(--text1); }
.adr-list { list-style: none; padding: 0; margin: 0; }
.adr-list li { margin-bottom: 4px; }
.adr-list a { display: flex; gap: 8px; align-items: baseline; font-size: 12px; color: var(--text2); text-decoration: none; }
.adr-list a:hover { color: var(--accent); }
.adr-num { font-family: monospace; font-size: 11px; color: var(--text3); flex-shrink: 0; width: 56px; }
.adr-title { flex: 1; }
</style>
