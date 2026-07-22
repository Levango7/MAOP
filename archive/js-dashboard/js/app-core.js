'use strict';
// app-core.js — Nav, helpers, load()
// MAOP Dashboard v7 - Grouped Navigation + Detailed Info Sections
const API = '';
let _logData = [];
let _charts = {};

// ── Auth ──
let _authToken = localStorage.getItem('maop_token') || '';
let _authUser = localStorage.getItem('maop_user') || '';

async function checkAuth() {
  try {
    const r = await fetch('/api/auth/status');
    const d = await r.json();
    if (d.auth_enabled && !_authToken) { showLogin(); return false; }
    return true;
  } catch(e) {
    // Network error: if we have a token, proceed (server may recover);
    // if no token and can't reach server, show login as safe default
    if (_authToken) return true;
    showLogin(); return false;
  }
}
function showLogin() {
  const o = document.getElementById('login-overlay');
  if (o) { o.style.display = 'flex'; const u = document.getElementById('login-user'); if (u) u.focus(); }
}
function hideLogin() { const o = document.getElementById('login-overlay'); if (o) o.style.display = 'none'; }
async function doLogin() {
  const username = document.getElementById('login-user').value.trim() || 'admin';
  const password = document.getElementById('login-pass').value;
  const errDiv = document.getElementById('login-error');
  const btn = document.getElementById('login-btn');
  if (errDiv) errDiv.style.display = 'none';
  if (btn) { btn.disabled = true; btn.textContent = 'Signing in...'; }
  try {
    const r = await fetch('/api/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username, password }) });
    const d = await r.json();
    if (d.status === 'ok' && d.token) {
      _authToken = d.token; _authUser = d.username;
      localStorage.setItem('maop_token', d.token); localStorage.setItem('maop_user', d.username);
      hideLogin(); load();
    } else { if (errDiv) { errDiv.textContent = d.error || 'Login failed'; errDiv.style.display = 'block'; } }
  } catch(e) { if (errDiv) { errDiv.textContent = 'Network error: ' + e.message; errDiv.style.display = 'block'; } }
  finally { if (btn) { btn.disabled = false; btn.textContent = 'Sign In'; } }
}
function doLogout() { _authToken = ''; _authUser = ''; localStorage.removeItem('maop_token'); localStorage.removeItem('maop_user'); showLogin(); }
function _authHeaders(extra) { const h = extra || {}; if (_authToken) h['Authorization'] = 'Bearer ' + _authToken; return h; }

// ── Nav: single-page scroll with smooth jump ──
// Deferred to window.load so all function defs from later JS files are available
window.addEventListener('load', function() {
  const _sectionLoaders = {
    overview: window.loadOverview, control: window.loadControl, chat: () => { if(window.MAOP && MAOP.chat) MAOP.chat.init(); }, agents: () => { if(window.MAOP && MAOP.agents) MAOP.agents.load(); }, upgrade: window.loadUpgrade, memory: window.loadMemory,
    evolve: window.loadEvolve, search: window.loadSearchIndex, monitor: window.loadMonitor, models: window.loadModels,
    performance: window.loadPerformance, logs: () => window.loadLogs('dashboard'), skills: window.loadSkills,
    mcp: window.loadMCP, prompts: window.loadPrompts, pillars: window.loadPillars, roles: window.loadRoles,
    modules: window.loadModules, workflow: window.loadWorkflow, architecture: window.loadArchitecture,
    wfexec: window.loadWfExec,
  };
  document.querySelectorAll('.nav-item').forEach(item => {
    item.addEventListener('click', () => {
      document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
      item.classList.add('active');
      const sec = document.getElementById('sec-' + item.dataset.sec);
      if (sec) {
        sec.scrollIntoView({ behavior: 'smooth', block: 'start' });
        const loader = _sectionLoaders[item.dataset.sec];
        if (loader) setTimeout(loader, 50);
      }
    });
  });
  // Auto-load all sections on first load
  if (typeof window.load === 'function') window.load();
});

// ── Helpers ──
async function fetchJSON(url) {
  try {
    const r = await fetch(API + url, { headers: _authHeaders() });
    if (r.status === 401) { showLogin(); return null; }
    if (!r.ok) return null;
    return await r.json();
  } catch(e) { return null; }
}
async function postJSON(url, body) {
  try {
    const r = await fetch(API + url, { method: 'POST', headers: _authHeaders({'Content-Type':'application/json'}), body: JSON.stringify(body) });
    if (r.status === 401) { showLogin(); return null; }
    if (!r.ok) return null;
    return await r.json();
  } catch(e) { return null; }
}
function el(id) { return document.getElementById(id); }
function esc(s) { return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;'); }
function fmtMs(ms) { return ms < 1000 ? ms+'ms' : (ms/1000).toFixed(1)+'s'; }
function statusBadge(s) {
  const c = s==='ok'||s==='success'||s==='pass'||s===true ? 'g' : s==='error'||s==='fail'||s===false ? 'r' : 'y';
  return `<span class="badge ${c}">${esc(s)}</span>`;
}
function arrize(d) { return Array.isArray(d) ? d : (d ? Object.values(d) : []); }

// ── Load all ──
async function load() {
  const ok = await checkAuth();
  if (!ok) return;
  loadOverview();
  loadControl();
  if(window.MAOP && MAOP.chat) MAOP.chat.init();
  if(window.MAOP && MAOP.agents) MAOP.agents.load();
  loadUpgrade();
  loadMemory();
  loadEvolve();
  loadMonitor();
  loadModels();
  loadPerformance();
  loadLogs('dashboard');
  loadSkills();
  loadMCP();
  loadPrompts();
  loadPillars();
  loadRoles();
  loadModules();
  loadWorkflow();
  loadArchitecture();
  loadWfExec();
  connectWebSocket();
}

// ── WebSocket real-time updates ──
let _ws = null;
let _wsReconnectTimer = null;
function connectWebSocket() {
  if (_ws && _ws.readyState <= 1) return;
  try {
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const url = `${proto}//${location.host}/ws${_authToken ? '?token='+encodeURIComponent(_authToken) : ''}`;
    _ws = new WebSocket(url);
    _ws.onmessage = function(ev) {
      try {
        const data = JSON.parse(ev.data);
        if (data.type === 'snapshot' || data.live || data.report) {
          if (typeof loadOverview === 'function') loadOverview();
        }
      } catch(e) {}
    };
    _ws.onclose = function() {
      _ws = null;
      if (!_wsReconnectTimer) _wsReconnectTimer = setTimeout(function() { _wsReconnectTimer = null; connectWebSocket(); }, 15000);
    };
    _ws.onerror = function() { _ws.close(); };
  } catch(e) { _ws = null; }
}

// ── SSE execution stream ──
function watchTrace(traceId) {
  if (!traceId) return;
  const box = document.getElementById('stream-output');
  if (!box) return;
  box.innerHTML = '<div class="info">Connecting to trace ' + esc(traceId) + '...</div>';
  try {
    const source = new EventSource('/api/stream/' + encodeURIComponent(traceId));
    source.onmessage = function(ev) {
      try {
        const data = JSON.parse(ev.data);
        const line = document.createElement('div');
        line.textContent = data.content || data.text || ev.data;
        box.appendChild(line);
        box.scrollTop = box.scrollHeight;
      } catch(e) {
        const line = document.createElement('div');
        line.textContent = ev.data;
        box.appendChild(line);
      }
    };
    source.onerror = function() {
      source.close();
      const line = document.createElement('div');
      line.className = 'muted';
      line.textContent = '[stream ended]';
      box.appendChild(line);
    };
  } catch(e) {
    box.innerHTML = '<div class="warn">Failed to connect: ' + esc(e.message) + '</div>';
  }
}

// ── Theme Toggle ──
(function initTheme() {
  const saved = localStorage.getItem('maop_theme') || 'light';
  document.documentElement.classList.toggle('light-theme', saved === 'light');
  const btn = document.getElementById('theme-toggle');
  if (btn) {
    btn.addEventListener('click', function() {
      const isLight = document.documentElement.classList.toggle('light-theme');
      localStorage.setItem('maop_theme', isLight ? 'light' : 'dark');
    });
  }
})();

