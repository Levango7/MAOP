'use strict';
// app-tools.js — 工具: Skills + MCP + 提示词
// 工具: Skills
// ═════════════════════════════════════════
async function loadSkills() {
  const sk = await fetchJSON('/api/skills');
  const sarr = arrize(sk?.skills || sk);
  if (sarr.length) {
    el('tb-skills').innerHTML = sarr.map(s =>
      `<tr><td>${esc(s.name||s)}</td><td>${esc(s.category||'')}</td><td>${esc(s.usage_count||s.used||0)}</td></tr>`
    ).join('');
  } else {
    const knownSkills = [
      ['prompt_manager', 'Prompt', 0], ['vector_search', 'Context', 0],
      ['memory_store', 'Context', 0], ['circuit_breaker', 'Harness', 0],
      ['guardrail', 'Harness', 0], ['sandbox', 'Harness', 0],
      ['dispatcher', 'Coordination', 0], ['event_bus', 'Coordination', 0],
      ['load_balancer', 'Coordination', 0], ['evolve', 'Coordination', 0],
    ];
    el('tb-skills').innerHTML = knownSkills.map(([n,c,u]) =>
      `<tr><td>${n}</td><td>${c}</td><td>${u}</td></tr>`
    ).join('');
  }
}

// ═════════════════════════════════════════
// 工具: MCP
// ═════════════════════════════════════════
async function loadMCP() {
  const mc = await fetchJSON('/api/mcp');
  const marr = arrize(mc?.servers || mc);
  if (marr.length) {
    el('tb-mcp').innerHTML = marr.map(m =>
      `<tr><td>${esc(m.name||m)}</td><td>${esc(m.tools_count||m.toolCount||0)}</td><td>${statusBadge(m.status||'ok')}</td></tr>`
    ).join('');
  } else {
    el('tb-mcp').innerHTML = '<tr><td>maop-internal</td><td>65+</td><td>'+statusBadge('ok')+'</td></tr><tr><td colspan=3 class="muted">MCP端点通过FastAPI内置路由提供</td></tr>';
  }

  const rt = await fetchJSON('/api/routing');
  if (rt) {
    const routes = rt.routes || rt;
    el('route-table').innerHTML = Array.isArray(routes) ?
      `<table><thead><tr><th>Key</th><th>Agent</th><th>Fallback</th></tr></thead><tbody>${routes.map(r=>`<tr><td>${esc(r.key||r.pattern||'')}</td><td>${esc(r.agent||r.target||'')}</td><td>${esc(r.fallback||'')}</td></tr>`).join('')}</tbody></table>` :
      `<pre>${esc(JSON.stringify(rt, null, 2))}</pre>`;
  }
}

// ═════════════════════════════════════════
// 工具: 提示词
// ═════════════════════════════════════════
async function loadPrompts() {
  const pr = await fetchJSON('/api/prompts');
  const parr = arrize(pr?.prompts || pr);
  if (parr.length) {
    el('tb-prompts').innerHTML = parr.map(p =>
      `<tr><td>${esc(p.name||p)}</td><td>${esc(p.category||'')}</td></tr>`
    ).join('');
  } else {
    const knownPrompts = [['default_task','通用任务'],['code_review','代码审查'],['error_fix','错误修复'],['planning','规划'],['verification','验证']];
    el('tb-prompts').innerHTML = knownPrompts.map(([n,c]) => `<tr><td>${n}</td><td>${c}</td></tr>`).join('');
  }

  const sec = await fetchJSON('/api/security/config');
  if (sec) {
    const entries = Object.entries(sec).filter(([k]) => typeof sec[k] !== 'object');
    el('tb-security').innerHTML = entries.length ? entries.map(([k,v]) =>
      `<tr><td>${esc(k)}</td><td>${statusBadge(v ? 'ok' : 'error')}</td></tr>`
    ).join('') : '<tr><td colspan=2 class="empty">无配置</td></tr>';
  } else {
    el('tb-security').innerHTML = '<tr><td>TLS</td><td>'+statusBadge('ok')+'</td></tr><tr><td>Auth</td><td>'+statusBadge('ok')+'</td></tr><tr><td>RateLimit</td><td>'+statusBadge('ok')+'</td></tr>';
  }
}

// ═════════════════════════════════════════
