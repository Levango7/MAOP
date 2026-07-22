'use strict';
// app-control.js — 操作: 控制面板 + Agent升级

async function loadControl() {
  const execBtns = [
    { label: '运行任务', action: 'run', color: 'btn-green' },
    { label: '暂停', action: 'pause', color: 'btn-orange' },
    { label: '恢复', action: 'resume', color: 'btn-blue' },
    { label: '停止', action: 'stop', color: 'btn-red' },
    { label: '验证配置', action: 'validate', color: 'btn-purple' },
    { label: '查看状态', action: 'status', color: 'btn-blue' },
  ];
  el('exec-btns').innerHTML = execBtns.map(b =>
    `<button class="btn-md ${b.color}" data-action="execAction" data-arg="${b.action}">${b.label}</button>`
  ).join('');

  const maintBtns = [
    { label: '日志轮转', action: 'log-rotate' },
    { label: '记忆清理', action: 'prune' },
    { label: '健康检查', action: 'health' },
    { label: '备份', action: 'backup' },
    { label: '缓存清理', action: 'cache-clear' },
    { label: '配置重载', action: 'reload' },
  ];
  el('maint-btns').innerHTML = maintBtns.map(b =>
    `<button class="btn-md btn-blue" data-action="maintAction" data-arg="${b.action}">${b.label}</button>`
  ).join('');

  const d = await fetchJSON('/api/control/status');
  const arr = arrize(d?.jobs || d);
  el('job-list').innerHTML = arr.length ? arr.map(j =>
    `<div class="job-item"><span>${esc(j.task||j.action||j.name||j.id||'')}</span> ${statusBadge(j.status||'')} <span class="muted">${esc(j.start||j.time||'')}</span></div>`
  ).join('') : '<div class="empty">无运行中任务</div>';
}

async function execAction(action) {
  if (action === 'run') {
    const task = prompt('请输入要运行的任务名称:', 'default');
    if (!task) return;
    const d = await postJSON('/api/control/run', { action: 'run', task });
    showCtrlMsg('run', d ? `已启动任务: ${esc(d.task||task)} (job: ${esc(d.job_id||'')})` : '启动失败', d ? 'success' : 'warn');
  } else {
    const d = await postJSON('/api/control/run', { action });
    showCtrlMsg(action, d ? esc(JSON.stringify(d)) : '操作失败', d ? 'success' : 'warn');
  }
  loadControl();
}

async function maintAction(action) {
  const d = await postJSON('/api/control/maintain', { action });
  let msg = '';
  if (!d) { msg = '操作失败'; showCtrlMsg(action, msg, 'warn'); return; }
  if (action === 'prune') {
    msg = `已清理 ${d.pruned||0} 条过期记忆, 剩余 ${d.remaining||0} 条`;
  } else if (action === 'log-rotate') {
    msg = d.msg || '日志已轮转';
  } else if (action === 'health') {
    msg = d.healthy ? '所有组件健康' : `存在问题: ${esc(JSON.stringify(d.components||[]))}`;
  } else if (action === 'backup') {
    msg = `备份完成: ${esc(d.path||'N/A')}`;
  } else if (action === 'cache-clear') {
    msg = '缓存已清空';
  } else if (action === 'reload') {
    msg = d.msg || '配置已重载';
  } else {
    msg = esc(JSON.stringify(d));
  }
  showCtrlMsg(action, msg, 'success');
}

function showCtrlMsg(action, msg, type) {
  const el2 = el('ctrl-msg');
  if (!el2) return;
  el2.innerHTML = `<span class="${type}">${msg}</span>`;
  setTimeout(() => { if (el2) el2.innerHTML = ''; }, 5000);
}

// ═════════════════════════════════════════
// 操作: Agent升级
// ═════════════════════════════════════════
async function loadUpgrade() {
  const v = await fetchJSON('/api/agent/upgrade');
  const agents = v && v.agents ? v.agents : [];
  if (agents.length) {
    el('tb-versions').innerHTML = agents.map(a =>
      `<tr><td>${esc(a.name||'')}</td><td>${esc(a.current||'')}</td><td>${esc(a.latest||'?')}</td><td>${statusBadge(a.status||'')}</td><td><button data-action="upgradeAgentByName" data-arg="${esc(a.name)}" class="btn-sm btn-orange">升级</button></td></tr>`
    ).join('');
    const sel = el('upgrade-name');
    sel.innerHTML = '<option value="">-- 选择Agent --</option>' + agents.map(a =>
      `<option value="${esc(a.name||'')}">${esc(a.name||'')} (v${esc(a.current||'?')})</option>`
    ).join('');
  } else {
    el('tb-versions').innerHTML = '<tr><td colspan=5 class="empty">无数据</td></tr>';
    const ag = await fetchJSON('/api/agents');
    const arr = arrize(ag?.agents || ag);
    el('upgrade-name').innerHTML = '<option value="">-- 选择Agent --</option>' + arr.map(a =>
      `<option value="${esc(a.name||a.id||'')}">${esc(a.name||a.id||'')}</option>`
    ).join('');
  }
}

async function upgradeAgent() {
  const name = el('upgrade-name').value.trim();
  if (!name) { el('upgrade-result').innerHTML = '<span class="warn">请输入Agent名称</span>'; return; }
  upgradeAgentByName(name);
}

async function upgradeAgentByName(name) {
  el('upgrade-result').innerHTML = '<span class="info">检测中...</span>';
  const r = await fetch(`/api/agent/upgrade?agent=${encodeURIComponent(name)}`, { method: 'POST', headers: _authHeaders() });
  const d = await r.json().catch(() => null);
  el('upgrade-result').innerHTML = d ? `<pre>${esc(JSON.stringify(d, null, 2))}</pre>` : '<span class="warn">检测失败</span>';
}
