'use strict';
// app-monitor.js — 监控: Agent监控 + 大模型 + 性能指标
// 监控: Agent监控
// ═════════════════════════════════════════
async function loadMonitor() {
  let d = await fetchJSON('/api/agents');
  let arr = arrize(d?.agents || d);
  if (!arr.length) {
    d = await fetchJSON('/api/model/agents');
    arr = arrize(d?.agents || d);
  }
  el('tb-agents').innerHTML = arr.length ? arr.map(a =>
    `<tr><td>${esc(a.name||a.id||'')}</td><td>${esc(a.cli||'')}</td><td>${esc(a.model||'')}</td><td>${esc(a.driver||'')}</td><td>${esc((a.capabilities||[]).join(', '))}</td><td>${statusBadge(a.status||'ok')}</td></tr>`
  ).join('') : '<tr><td colspan=6 class="empty">无数据</td></tr>';

  const ov = await fetchJSON('/api/overview');
  if (ov && ov.availability) drawAvailChart(ov.availability);
  else if (arr.length) drawAvailChart({available: arr.length, unavailable: 0});
}
function drawAvailChart(data) {
  const ctx = el('chart-avail');
  if (!ctx) return;
  if (_charts.avail) _charts.avail.destroy();
  _charts.avail = new Chart(ctx, {
    type: 'doughnut',
    data: { labels: ['可用','不可用'], datasets: [{ data: [data.available||0, data.unavailable||0], backgroundColor: ['#22c55e','#ef4444'] }] },
    options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: 'bottom' } } }
  });
}

// ═════════════════════════════════════════
// 监控: 大模型
// ═════════════════════════════════════════
async function loadModels() {
  const d = await fetchJSON('/api/model/agents');
  const arr = arrize(d?.agents || d);
  el('tb-model-agents').innerHTML = arr.map(a =>
    `<tr><td>${esc(a.name||'')}</td><td>${esc(a.cli||'')}</td><td>${esc(a.model||'')}</td><td>${esc(a.driver||'')}</td><td>${statusBadge(a.cli_available ? 'ok' : 'error')}</td><td><button onclick="quickSwitch('${esc(a.name)}','${esc(a.model)}')" class="btn-sm btn-blue">编辑</button></td></tr>`
  ).join('') || '<tr><td colspan=6 class="empty">无数据</td></tr>';

  const sa = el('switch-agent');
  sa.innerHTML = '<option value="">-- 选择Agent --</option>' + arr.map(a =>
    `<option value="${esc(a.name||'')}">${esc(a.name||'')}</option>`
  ).join('');

  // Fetch real providers from backend instead of hardcoding
  const provResp = await fetchJSON('/api/model/providers');
  let knownProviders = arrize(provResp?.providers || provResp);
  if (!knownProviders.length) {
    // Fallback: fetch from registry which has provider info
    const regResp = await fetchJSON('/api/model/registry');
    const regStats = regResp?.stats || {};
    const provNames = Object.keys(regStats.providers || {});
    knownProviders = provNames.map(name => ({ name, models: [] }));
  }
  // If providers don't have models list, fetch full model list to populate
  if (knownProviders.length && !knownProviders[0].models) {
    const listResp = await fetchJSON('/api/model/list');
    const allModels = arrize(listResp?.models || []);
    knownProviders.forEach(p => {
      p.models = allModels.filter(m => m.provider === p.name).map(m => m.name);
    });
  }
  // If still empty, use minimal fallback
  if (!knownProviders.length) {
    knownProviders = [{ name: 'openai', models: ['gpt-4o'] }, { name: 'anthropic', models: ['claude-3-sonnet'] }];
  }
  const sp = el('switch-provider');
  sp.innerHTML = '<option value="">-- 选择Provider --</option>' + knownProviders.map(p =>
    `<option value="${esc(p.name)}">${esc(p.name)}</option>`
  ).join('');
  sp.onchange = function() {
    const prov = knownProviders.find(p => p.name === sp.value);
    const sm = el('switch-model');
    sm.innerHTML = '<option value="">-- 选择模型 --</option>' + (prov ? prov.models.map(m =>
      `<option value="${esc(m)}">${esc(m)}</option>`
    ).join('') : knownProviders.flatMap(p => p.models).map(m =>
      `<option value="${esc(m)}">${esc(m)}</option>`
    ).join(''));
  };

  const knownModels = knownProviders.flatMap(p => p.models);
  const sm = el('switch-model');
  sm.innerHTML = '<option value="">-- 选择模型 --</option>' + knownModels.map(m =>
    `<option value="${esc(m)}">${esc(m)}</option>`
  ).join('');

  const q = await fetchJSON('/api/model/quota');
  const qarr = arrize(q?.agents || q);
  el('tb-model-quota').innerHTML = qarr.map(a =>
    `<tr><td>${esc(a.name||a.agent||'')}</td><td>${esc(a.cli_path||a.cli||'')}</td><td>${statusBadge(a.available ? 'ok' : 'error')}</td></tr>`
  ).join('') || '<tr><td colspan=3 class="empty">无数据</td></tr>';

  // Show budget and policies info from backend
  const budget = await fetchJSON('/api/model/budget');
  const policies = await fetchJSON('/api/model/policies');
  let extraHtml = '';
  if (budget?.budget) {
    const b = budget.budget;
    extraHtml += '<div style="margin-top:10px;font-size:12px"><b>预算状态:</b> ';
    extraHtml += `日支出=${esc(b.daily_spend??0)} / 日限额=${esc(b.daily_limit??'N/A')}, `;
    extraHtml += `月支出=${esc(b.monthly_spend??0)} / 月限额=${esc(b.monthly_limit??'N/A')}`;
    extraHtml += '</div>';
  }
  if (policies?.policies?.length) {
    extraHtml += '<div style="margin-top:6px;font-size:12px"><b>选择策略:</b> ';
    extraHtml += policies.policies.map(p => esc(p.name)+'('+esc(p.strategy)+')').join(', ');
    extraHtml += '</div>';
  }
  const quotaExtra = el('model-extra-info');
  if (quotaExtra) quotaExtra.innerHTML = extraHtml;
}
function quickSwitch(agent, model) { el('switch-agent').value = agent; el('switch-model').value = model; }
async function switchModel() {
  const agent = el('switch-agent').value.trim();
  const provider = el('switch-provider').value.trim();
  const model = el('switch-model').value.trim();
  if (!agent || !model) { el('model-msg').innerHTML = '<span class="warn">请选择Agent和模型</span>'; return; }
  el('model-msg').innerHTML = '<span class="info">切换中...</span>';
  const body = { agent, model };
  if (provider) body.provider = provider;
  const r = await fetch('/api/model/switch', { method: 'POST', headers: _authHeaders({ 'Content-Type': 'application/json' }), body: JSON.stringify(body) });
  const d = await r.json().catch(() => null);
  el('model-msg').innerHTML = d ? `<span class="success">已切换: ${esc(JSON.stringify(d))}</span>` : '<span class="warn">切换失败</span>';
  loadModels();
}

// ═════════════════════════════════════════
// 监控: 性能指标
// ═════════════════════════════════════════
async function loadPerformance() {
  const d = await fetchJSON('/api/overview');
  if (!d) return;
  if (d.timeseries && (d.timeseries.labels || d.timeseries.values)) drawTsChart(d.timeseries);
  else {
    const ctx = el('chart-ts');
    if (ctx) ctx.innerHTML = '<div class="empty" style="display:flex;align-items:center;justify-content:center;height:100%">暂无时序数据</div>';
  }

  const fails = arrize(d.fail_ranking);
  el('tb-fail').innerHTML = fails.length ? fails.map(r =>
    `<tr><td>${esc(r.agent||'')}</td><td>${r.fail_count||0}</td><td>${(r.fail_rate||0).toFixed(1)}%</td></tr>`
  ).join('') : '<tr><td colspan=3 class="empty">无失败记录</td></tr>';

  const live = arrize(d.recent_delegations);
  el('tb-live').innerHTML = live.length ? live.map(r =>
    `<tr><td>${esc(r.timestamp||'')}</td><td>${esc(r.agent||'')}</td><td>${esc(r.task||'')}</td><td>${fmtMs(r.duration_ms||0)}</td><td>${statusBadge(r.exit_code===0?'success':'failed')}</td></tr>`
  ).join('') : '<tr><td colspan=5 class="empty">无最近委托记录</td></tr>';
}
function drawTsChart(data) {
  const ctx = el('chart-ts');
  if (!ctx) return;
  if (_charts.ts) _charts.ts.destroy();
  _charts.ts = new Chart(ctx, {
    type: 'line',
    data: { labels: data.labels||[], datasets: [{ label: '委托数', data: data.values||[], borderColor: '#3b82f6', backgroundColor: 'rgba(59,130,246,.1)' }] },
    options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } } }
  });
}

// ═════════════════════════════════════════
