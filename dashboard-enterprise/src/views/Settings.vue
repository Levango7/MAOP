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
        <div class="setting-row edition-switch-row">
          <span class="setting-label">Switch Edition</span>
          <div class="edition-switch-buttons">
            <button
              class="edition-btn"
              :class="{ active: edition.edition === 'personal' }"
              :disabled="!isAdmin || editionStore.switching || edition.edition === 'personal'"
              @click="onSwitchClick('personal')"
            >Personal</button>
            <button
              class="edition-btn"
              :class="{ active: edition.edition === 'enterprise' }"
              :disabled="!isAdmin || editionStore.switching || edition.edition === 'enterprise'"
              @click="onSwitchClick('enterprise')"
            >Enterprise</button>
          </div>
          <span class="switching-indicator" v-if="editionStore.switching">切换中…</span>
        </div>
        <div class="setting-row" v-if="!isAdmin">
          <span class="setting-label"></span>
          <span class="edition-perm-hint">需要管理员权限</span>
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
          <span class="about-ver">v{{ appVersion }}</span>
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
            <li v-for="adr in adrs" :key="adr.number">
              <a :href="`/docs/adr/${adr.filename}`" target="_blank" rel="noopener">
                <span class="adr-num">ADR-{{ adr.number }}</span>
                <span class="adr-title">{{ adr.title }}</span>
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
// edition 切换相关状态
const isAdmin = ref(false);
const switchNotice = ref('');
const switchNoticeDegraded = ref(false);

/**
 * 检测当前登录用户是否拥有 admin 角色。
 * 优先从 localStorage 读取 maop_roles（由 App.vue doLogin 保存），
 * 回退到用户名 'admin' 判断（兼容旧版前端未保存 roles 的场景）。
 */
function detectAdmin() {
  try {
    const rolesStr = localStorage.getItem('maop_roles');
    if (rolesStr) {
      const roles = JSON.parse(rolesStr);
      return Array.isArray(roles) && roles.some(r => r === 'admin' || r === 'superadmin');
    }
  } catch (e) { /* ignore malformed roles */ }
  // fallback：用户名 'admin' 视为 admin
  try {
    return localStorage.getItem('maop_user') === 'admin';
  } catch (e) { return false; }
}

/**
 * 切换 edition 入口：弹确认框 -> 调用 store.switchEdition -> 更新 UI。
 * @param {string} target 'personal' | 'enterprise'
 */
async function onSwitchClick(target) {
  if (target === edition.value.edition) return;
  const label = target === 'enterprise' ? 'Enterprise' : 'Personal';
  const featureDesc = target === 'enterprise'
    ? '将启用 SSO/RBAC/审计日志等企业级功能'
    : '将关闭 SSO/RBAC/审计日志等企业级功能，回到精简模式';
  const ok = confirm(`切换到 ${label} 版本${featureDesc}。确认切换？`);
  if (!ok) return;
  switchNotice.value = '';
  switchNoticeDegraded.value = false;
  try {
    const result = await editionStore.switchEdition(target);
    // 同步刷新本地 edition 视图
    edition.value = {
      edition: editionStore.edition,
      features: editionStore.features,
      backends: editionStore.backends,
      degradations: editionStore.degradations,
      enterprise_available: editionStore.isEnterprise,
    };
    if (result.degraded) {
      switchNotice.value = `已请求切换到 ${result.requested}，但 license 无效，实际仍为 ${result.edition}`;
      switchNoticeDegraded.value = true;
    } else {
      switchNotice.value = `已切换到 ${result.edition} 版本`;
    }
  } catch (e) {
    // 错误信息已由 store 写入 switchError，这里无需重复设置
  }
}

// t22: 从 vite define 注入的全局常量读取版本号（vite.config.js 中由 package.json 注入）。
// fallback 'unknown' 仅在未注入时使用（如单元测试环境）。
const appVersion = typeof __APP_VERSION__ !== 'undefined' ? __APP_VERSION__ : 'unknown';

// t21c: ADR index fallback. 当 /api/info/adrs 端点不可用时（如后端旧版本或网络故障），
// 使用此硬编码列表作为兜底，确保 About 面板始终有内容显示。
// 字段名与后端 /api/info/adrs 返回结构保持一致：number / filename / title。
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

// t22: ADR 列表初始用 fallback，onMounted 时尝试从后端动态加载完整列表（含 014+）。
const adrs = ref(adrsFallback);

async function loadAdrs() {
  try {
    const res = await fetch('/api/info/adrs');
    if (res.ok) {
      const data = await res.json();
      if (Array.isArray(data) && data.length > 0) {
        adrs.value = data;
      }
    }
  } catch (e) {
    console.warn('Failed to load ADRs:', e);
  }
}

async function load() {
  isAdmin.value = detectAdmin();
  await editionStore.fetchEdition();
  edition.value = {
    edition: editionStore.edition,
    features: editionStore.features,
    backends: editionStore.backends,
    degradations: editionStore.degradations,
    enterprise_available: editionStore.isEnterprise,
  };
  try { config.value = await api.get('/api/info/config'); } catch { config.value = {}; }
  loadAdrs();
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
/* edition 切换 UI */
.edition-switch-row { flex-wrap: wrap; }
.edition-switch-buttons { display: inline-flex; gap: 6px; }
.edition-btn {
  padding: 4px 14px;
  border-radius: 6px;
  font-size: 12px;
  font-weight: 600;
  border: 1px solid var(--border);
  background: var(--bg3);
  color: var(--text2);
  cursor: pointer;
  transition: all .15s ease;
}
.edition-btn:hover:not(:disabled) {
  border-color: var(--accent);
  color: var(--accent);
}
.edition-btn.active {
  background: rgba(0,113,227,.12);
  color: var(--accent);
  border-color: var(--accent);
}
.edition-btn:disabled {
  opacity: .5;
  cursor: not-allowed;
}
.edition-btn.active:disabled {
  opacity: .8;
}
.switching-indicator {
  font-size: 12px;
  color: var(--text3);
  margin-left: 4px;
}
.edition-perm-hint {
  font-size: 12px;
  color: var(--text3);
  font-style: italic;
}
.edition-error-msg {
  font-size: 12px;
  color: var(--fail);
}
.edition-notice {
  font-size: 12px;
  color: var(--success);
}
.edition-notice.degraded {
  color: var(--fail);
}
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
