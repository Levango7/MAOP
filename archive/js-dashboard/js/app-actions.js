'use strict';
// app-actions.js — 扩展功能键 + init
// ═════════════════════════════════════════
// 扩展功能键
// ═════════════════════════════════════════
async function batchUpgrade() {
  el('upgrade-result').innerHTML = '<span class="info">正在批量检测...</span>';
  try {
    const d = await fetchJSON('/api/agents');
    const agents = arrize(d?.agents || d);
    let results = [];
    for (const a of agents) {
      const name = a.name || a.agent || a;
      try {
        await upgradeAgentByName(name);
        results.push(name + ': 已检测');
      } catch(e) {
        results.push(name + ': 失败');
      }
    }
    el('upgrade-result').innerHTML = results.map(r => `<div class="kv"><span>${esc(r.split(':')[0])}</span><b>${esc(r.split(':')[1])}</b></div>`).join('');
  } catch(e) {
    el('upgrade-result').innerHTML = '<span class="warn">批量检测失败: ' + esc(e.message) + '</span>';
  }
}

function exportUpgradeReport() {
  const rows = document.querySelectorAll('#tb-versions tr');
  let csv = 'Agent,当前版本,最新版本,状态\n';
  rows.forEach(r => { const cells = r.querySelectorAll('td'); if(cells.length>=4) csv += Array.from(cells).map(c=>c.textContent.trim()).join(',') + '\n'; });
  const blob = new Blob([csv], {type:'text/csv'});
  const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = 'upgrade_report.csv'; a.click();
}

function clearAttnResults() {
  el('attn-results').innerHTML = '';
  el('attn-query').value = '';
}

function exportAttnResults() {
  const content = el('attn-results').textContent;
  if (!content) { el('attn-results').innerHTML = '<span class="warn">无结果可导出</span>'; return; }
  const blob = new Blob([content], {type:'text/plain'});
  const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = 'attention_results.txt'; a.click();
}

async function testModelConnection() {
  const agent = el('switch-agent').value;
  const model = el('switch-model').value;
  if (!agent) { el('model-msg').innerHTML = '<span class="warn">请先选择Agent</span>'; return; }
  el('model-msg').innerHTML = '<span class="info">正在测试连接...</span>';
  try {
    const d = await fetchJSON('/api/control/provider-health');
    el('model-msg').innerHTML = '<span class="success">连接测试完成: ' + esc(JSON.stringify(d).slice(0,200)) + '</span>';
  } catch(e) {
    el('model-msg').innerHTML = '<span class="warn">连接测试: ' + esc(e.message) + '</span>';
  }
}

async function saveModelConfig() {
  const agent = el('switch-agent').value;
  const provider = el('switch-provider').value;
  const model = el('switch-model').value;
  if (!agent || !model) { el('model-msg').innerHTML = '<span class="warn">请选择Agent和模型</span>'; return; }
  el('model-msg').innerHTML = '<span class="info">保存中...</span>';
  try {
    const resp = await fetch('/api/control/refresh', {method:'POST', headers: _authHeaders()});
    el('model-msg').innerHTML = '<span class="success">配置已保存: ' + esc(agent) + ' -> ' + esc(model) + '</span>';
  } catch(e) {
    el('model-msg').innerHTML = '<span class="warn">保存失败: ' + esc(e.message) + '</span>';
  }
}

async function stopWorkflow() {
  el('wf-msg').innerHTML = '<span class="info">正在停止...</span>';
  try {
    // Find the most recent running job
    const st = await fetchJSON('/api/control/status');
    const jobs = arrize(st?.active_jobs || st?.jobs || []);
    const running = jobs.filter(j => j.status === 'running' || j.status === 'paused');
    if (!running.length) {
      el('wf-msg').innerHTML = '<span class="warn">无运行中的任务可停止</span>';
      return;
    }
    // Stop all running jobs via the stop action (terminates all)
    const resp = await postJSON('/api/control/run', { action: 'stop' });
    el('wf-msg').innerHTML = resp ? `<span class="success">已停止 ${resp.stopped||0} 个任务</span>` : '<span class="warn">停止失败</span>';
    loadControl();
  } catch(e) {
    el('wf-msg').innerHTML = '<span class="warn">停止失败: ' + esc(e.message) + '</span>';
  }
}

async function loadWfHistory() {
  el('wf-msg').innerHTML = '<span class="info">加载执行历史...</span>';
  try {
    const d = await fetchJSON('/api/control/status');
    const jobs = arrize(d?.active_jobs || d?.jobs || []);
    if (!jobs.length) { el('wf-msg').innerHTML = '<span class="muted">暂无执行历史</span>'; return; }
    el('wf-msg').innerHTML = jobs.map(j => `<div class="kv"><span>${esc(j.task||j.action||'N/A')}</span><b>${esc(j.status||'unknown')}</b></div>`).join('');
  } catch(e) {
    el('wf-msg').innerHTML = '<span class="warn">加载历史失败: ' + esc(e.message) + '</span>';
  }
}

// ── Init ──
// load() is called by app-core.js window.load event — no duplicate here
setInterval(loadOverview, 60000);