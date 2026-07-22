'use strict';
// app-evolve.js — 操作: 自进化
// 操作: 自进化
// ═════════════════════════════════════════
async function loadEvolve() {
  loadEvolveStatus();
  loadEvolveSuggestions();
  const d = await fetchJSON('/api/evolve/report');
  let perf = [];
  if (d && d.performance) perf = arrize(d.performance);
  if (!perf.length && d?.data?.stats?.by_agent) perf = d.data.stats.by_agent.map(a => ({agent: a.agent, success_rate: a.rate, avg_latency_ms: a.avg_duration_ms, fail_count: a.fail, total_count: a.total}));
  if (!perf.length) {
    const st = await fetchJSON('/api/evolve/status');
    if (st?.data?.stats?.by_agent) perf = st.data.stats.by_agent.map(a => ({agent: a.agent, success_rate: a.rate, avg_latency_ms: a.avg_duration_ms, fail_count: a.fail, total_count: a.total}));
  }
  // 按综合评分降序排列
  const scored = perf.map(p => {
    const sr = p.success_rate || 0;
    const lat = p.avg_latency_ms || 0;
    const speedScore = Math.max(0, 1 - lat/5000) * 100;
    const failCount = p.fail_count || Math.round((100 - sr) * (p.total_count || 10) / 100);
    const stabilityScore = Math.max(0, 100 - failCount * 5);
    const total = sr * 0.6 + speedScore * 0.3 + stabilityScore * 0.1;
    const grade = total >= 90 ? 'S' : total >= 75 ? 'A' : total >= 60 ? 'B' : total >= 40 ? 'C' : 'D';
    return { agent: p.agent||'', sr, lat, total, grade };
  }).sort((a, b) => b.total - a.total);
  if (scored.length) {
    el('tb-evolve-perf').innerHTML = scored.map(p => {
      const gradeColor = {S:'#22c55e',A:'#3b82f6',B:'#f59e0b',C:'#f97316',D:'#ef4444'}[p.grade];
      return `<tr><td>${esc(p.agent)}</td><td>${p.sr.toFixed(1)}%</td><td>${fmtMs(p.lat)}</td><td><b style="color:${gradeColor}">${p.total.toFixed(1)}</b></td><td><span class="badge" style="background:${gradeColor}22;color:${gradeColor};font-weight:700">${p.grade}</span></td></tr>`;
    }).join('');
  } else {
    el('tb-evolve-perf').innerHTML = '<tr><td colspan=5 class="empty">无性能数据</td></tr>';
  }
  // 评分规则放在排行榜下方
  el('perf-rules').innerHTML = '<div class="perf-rules-box">' +
    '<div class="perf-rules-title">评分规则</div>' +
    '<div class="perf-rules-item"><b>综合评分</b> = 成功率 × 0.6 + 速度分 × 0.3 + 稳定性分 × 0.1</div>' +
    '<div class="perf-rules-item"><b>速度分</b> = max(0, 1 - 平均延迟 / 5000ms) × 100</div>' +
    '<div class="perf-rules-item"><b>稳定性分</b> = max(0, 100 - 失败次数 × 5)</div>' +
    '<div class="perf-rules-item"><b>等级划分：</b>' +
      '<span style="color:#22c55e;font-weight:700">S</span> ≥ 90分 · ' +
      '<span style="color:#3b82f6;font-weight:700">A</span> ≥ 75分 · ' +
      '<span style="color:#f59e0b;font-weight:700">B</span> ≥ 60分 · ' +
      '<span style="color:#f97316;font-weight:700">C</span> ≥ 40分 · ' +
      '<span style="color:#ef4444;font-weight:700">D</span> < 40分</div>' +
    '<div class="perf-rules-item muted">数据来源：Monitor模块采集各Agent执行统计，经TimeSeries降采样后聚合计算。按综合评分降序排列。</div>' +
    '</div>';
}
let _evolveDetailVisible = false;
async function loadEvolveStatus() {
  const resp = await fetchJSON('/api/evolve/status');
  if (!resp) {
    el('evolve-status-summary').innerHTML = '<div class="empty">无数据</div>';
    el('evolve-status-detail').innerHTML = '';
    el('evolve-summary').innerHTML = '';
    return;
  }
  // API返回 {status:"ok", data:{action, stats:{by_agent:[...]}}}
  const data = resp.data || resp;
  const agents = data?.stats?.by_agent || [];
  // 计算汇总指标
  const totalAgents = agents.length;
  const totalTasks = agents.reduce((s, a) => s + (a.total || 0), 0);
  const totalSuccess = agents.reduce((s, a) => s + (a.success || 0), 0);
  const totalFail = agents.reduce((s, a) => s + (a.fail || 0), 0);
  const overallRate = totalTasks > 0 ? (totalSuccess / totalTasks * 100).toFixed(1) : 0;
  const bestAgent = agents.length ? agents.reduce((b, a) => a.rate > b.rate ? a : b) : null;
  const worstAgent = agents.length ? agents.reduce((w, a) => a.rate < w.rate ? a : w) : null;
  // 控制区摘要：关键指标卡片
  const summaryItems = [
    { label: '引擎状态', value: '运行中', color: 'var(--success)' },
    { label: '监控Agent', value: totalAgents + '个' },
    { label: '总任务数', value: totalTasks },
    { label: '总体成功率', value: overallRate + '%', color: overallRate >= 70 ? 'var(--success)' : 'var(--warn)' },
    { label: '最佳Agent', value: bestAgent ? bestAgent.agent + '(' + bestAgent.rate + '%)' : 'N/A', color: 'var(--success)' },
    { label: '最差Agent', value: worstAgent ? worstAgent.agent + '(' + worstAgent.rate + '%)' : 'N/A', color: 'var(--fail)' },
  ];
  el('evolve-summary').innerHTML = summaryItems.map(s =>
    `<div class="evolve-sum-item"><span class="evolve-sum-label">${esc(s.label)}</span><div class="stat-divider"></div><span class="evolve-sum-value" style="${s.color?'color:'+s.color:''}">${esc(s.value)}</span></div>`
  ).join('');
  // 引擎状态摘要：8个指标卡片(2列4行)，上边框彩色3px
  const avgLatency = agents.length ? (agents.reduce((s,a)=>s+(a.avg_duration_ms||0),0)/agents.length) : 0;
  const keyInfo = [
    { label: '引擎状态', value: '运行中' },
    { label: '监控Agent数', value: totalAgents },
    { label: '总任务数', value: totalTasks },
    { label: '成功/失败', value: totalSuccess + '/' + totalFail },
    { label: '总体成功率', value: overallRate + '%' },
    { label: '平均延迟', value: fmtMs(avgLatency) },
    { label: '最佳Agent', value: bestAgent ? bestAgent.agent + ' (' + bestAgent.rate + '%)' : 'N/A' },
    { label: '最差Agent', value: worstAgent ? worstAgent.agent + ' (' + worstAgent.rate + '%)' : 'N/A' },
  ];
  el('evolve-status-summary').innerHTML = '<div class="evolve-status-grid">' + keyInfo.map((s,i) =>
    '<div class="evolve-status-card ec'+(i+1)+'"><span class="esc-lbl">'+esc(s.label)+'</span><div class="esc-div"></div><span class="esc-val">'+esc(s.value)+'</span></div>'
  ).join('') + '</div>';
  // 详情：各Agent执行统计表格
  let detailHtml = '<div class="evolve-section-title">各Agent执行统计</div>';
  detailHtml += '<table class="info-table"><thead><tr><th>Agent</th><th>总任务</th><th>成功</th><th>失败</th><th>成功率</th><th>平均延迟</th></tr></thead><tbody>';
  agents.forEach(a => {
    const rateColor = a.rate >= 70 ? 'var(--success)' : a.rate >= 50 ? 'var(--warn)' : 'var(--fail)';
    detailHtml += `<tr><td class="info-key">${esc(a.agent)}</td><td>${a.total||0}</td><td>${a.success||0}</td><td>${a.fail||0}</td><td style="color:${rateColor};font-weight:600">${(a.rate||0).toFixed(1)}%</td><td>${fmtMs(a.avg_duration_ms||0)}</td></tr>`;
  });
  detailHtml += '</tbody></table>';
  el('evolve-status-detail').innerHTML = detailHtml;
}
function toggleEvolveDetail() {
  _evolveDetailVisible = !_evolveDetailVisible;
  const detail = el('evolve-status-detail');
  const btn = el('evolve-toggle-btn');
  if (_evolveDetailVisible) {
    detail.classList.remove('evolve-detail-hidden');
    detail.classList.add('evolve-detail-visible');
    btn.textContent = '隐藏详情';
  } else {
    detail.classList.add('evolve-detail-hidden');
    detail.classList.remove('evolve-detail-visible');
    btn.textContent = '显示详情';
  }
}
async function loadEvolveSuggestions() {
  const resp = await fetchJSON('/api/evolve/suggestions');
  if (!resp) { el('evolve-suggestions').innerHTML = '<div class="empty">无建议数据</div>'; return; }
  // API返回 {status:"ok", suggestions:{action, stats:{by_agent:[...]}}}
  const sugData = resp.suggestions || resp;
  const agents = sugData?.stats?.by_agent || [];
  if (!agents.length) { el('evolve-suggestions').innerHTML = '<div class="empty">无Agent统计数据，无法生成建议</div>'; return; }
  // 从统计数据自动生成优化建议
  const suggestions = [];
  // 1. 低成功率Agent建议
  const lowRateAgents = agents.filter(a => a.total >= 3 && a.rate < 50);
  lowRateAgents.sort((a, b) => a.rate - b.rate);
  lowRateAgents.forEach(a => {
    suggestions.push({
      title: a.agent + ' 成功率过低 (' + a.rate.toFixed(1) + '%)',
      desc: '该Agent共执行' + a.total + '次任务，成功' + a.success + '次，失败' + a.fail + '次。建议：检查Agent配置和CLI可用性，考虑降低路由权重或增加重试次数。',
      severity: 'high',
    });
  });
  // 2. 高延迟Agent建议
  const slowAgents = agents.filter(a => a.total >= 3 && a.avg_duration_ms > 20000);
  slowAgents.sort((a, b) => b.avg_duration_ms - a.avg_duration_ms);
  slowAgents.forEach(a => {
    suggestions.push({
      title: a.agent + ' 响应延迟过高 (' + fmtMs(a.avg_duration_ms) + ')',
      desc: '该Agent平均延迟' + fmtMs(a.avg_duration_ms) + '，严重影响整体执行效率。建议：检查网络连接和模型响应时间，考虑配置更快的模型或增加超时降级策略。',
      severity: 'medium',
    });
  });
  // 3. 零成功率Agent
  const zeroAgents = agents.filter(a => a.total > 0 && a.success === 0);
  zeroAgents.forEach(a => {
    suggestions.push({
      title: a.agent + ' 全部失败 (' + a.fail + '次)',
      desc: '该Agent执行' + a.total + '次任务全部失败。建议：立即检查CLI路径和认证配置，确认Agent是否可用。如不可恢复，从路由表中移除该Agent。',
      severity: 'critical',
    });
  });
  // 4. 最佳Agent推荐
  const bestAgent = agents.reduce((b, a) => a.total >= 3 && a.rate > b.rate ? a : b, {rate: 0});
  if (bestAgent.agent && bestAgent.rate >= 70) {
    suggestions.push({
      title: '推荐提升 ' + bestAgent.agent + ' 的路由权重',
      desc: '该Agent成功率' + bestAgent.rate.toFixed(1) + '%，平均延迟' + fmtMs(bestAgent.avg_duration_ms) + '，表现优秀。建议：在路由配置中提升该Agent的权重，使其承担更多任务。',
      severity: 'info',
    });
  }
  // 5. 整体健康度
  const overallRate = agents.reduce((s, a) => s + a.success, 0) / agents.reduce((s, a) => s + a.total, 0) * 100;
  if (overallRate < 60) {
    suggestions.push({
      title: '整体成功率偏低 (' + overallRate.toFixed(1) + '%)',
      desc: '所有Agent的总体成功率仅' + overallRate.toFixed(1) + '%，低于60%警戒线。建议：全面检查Agent配置、网络环境和模型可用性，优先处理高严重度建议。',
      severity: 'high',
    });
  }
  // 渲染建议列表
  const sevColor = { critical: '#ef4444', high: '#f97316', medium: '#f59e0b', info: '#3b82f6' };
  const sevLabel = { critical: '严重', high: '高', medium: '中', info: '建议' };
  el('evolve-suggestions').innerHTML = suggestions.length ? suggestions.map(s =>
    `<div class="suggestion" style="border-left:3px solid ${sevColor[s.severity]}"><div style="display:flex;align-items:center;gap:6px;margin-bottom:4px"><span class="badge" style="background:${sevColor[s.severity]}22;color:${sevColor[s.severity]};font-weight:700;font-size:10px">${sevLabel[s.severity]}</span><b>${esc(s.title)}</b></div><div style="font-size:12px;color:var(--text2);line-height:1.5">${esc(s.desc)}</div></div>`
  ).join('') : '<div class="empty">当前无优化建议，各Agent运行正常</div>';
}
async function triggerEvolveAnalyze() {
  const r = await fetch('/api/evolve/analyze', { method: 'POST', headers: _authHeaders() });
  const d = await r.json().catch(() => null);
  showEvolveResult('触发分析', d);
  loadEvolveStatus();
}
async function applyEvolveSuggestions() {
  const r = await fetch('/api/evolve/analyze', { method: 'POST', headers: _authHeaders({ 'Content-Type': 'application/json' }), body: JSON.stringify({ action: 'apply' }) });
  const d = await r.json().catch(() => null);
  showEvolveResult('应用建议', d);
  loadEvolve();
}
async function exportEvolveReport() {
  const d = await fetchJSON('/api/evolve/report');
  if (!d) { showEvolveResult('导出报告', null, '无报告数据'); return; }
  const blob = new Blob([JSON.stringify(d, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = 'evolve-report-' + Date.now() + '.json';
  a.click(); URL.revokeObjectURL(url);
  showEvolveResult('导出报告', d, '报告已下载');
}
async function resetEvolve() {
  if (!confirm('确认重置进化数据？此操作不可撤销。')) return;
  const r = await fetch('/api/evolve/analyze', { method: 'POST', headers: _authHeaders({ 'Content-Type': 'application/json' }), body: JSON.stringify({ action: 'reset' }) });
  const d = await r.json().catch(() => null);
  showEvolveResult('重置进化', d);
  loadEvolve();
}
function showEvolveResult(action, data, customMsg) {
  const box = el('evolve-result');
  if (!box) return;
  if (!data && !customMsg) {
    box.innerHTML = '<div class="evolve-result-item evolve-result-fail"><b>' + esc(action) + '</b>: 请求失败</div>';
    return;
  }
  const agents = data?.suggestions?.stats?.by_agent || data?.data?.stats?.by_agent || [];
  const st = data?.status || 'unknown';
  const act = data?.suggestions?.action || data?.data?.action || '';
  let html = '<div class="evolve-result-item evolve-result-ok">';
  html += '<div class="evolve-result-head"><span class="badge" style="background:#22c55e22;color:#22c55e;font-weight:700">' + esc(st) + '</span><b>' + esc(action) + '</b>' + (act ? '<span class="evolve-result-act">' + esc(act) + '</span>' : '') + '</div>';
  if (customMsg) {
    html += '<div class="evolve-result-msg">' + esc(customMsg) + '</div>';
  }
  if (agents.length) {
    html += '<table class="evolve-result-table"><thead><tr><th>Agent</th><th>总任务</th><th>成功</th><th>失败</th><th>成功率</th><th>平均延迟</th></tr></thead><tbody>';
    agents.forEach(a => {
      const rc = (a.rate||0) >= 70 ? '#22c55e' : (a.rate||0) >= 50 ? '#f59e0b' : '#ef4444';
      html += '<tr><td>' + esc(a.agent||'') + '</td><td>' + (a.total||0) + '</td><td>' + (a.success||0) + '</td><td>' + (a.fail||0) + '</td><td style="color:' + rc + ';font-weight:600">' + ((a.rate||0).toFixed(1)) + '%</td><td>' + fmtMs(a.avg_duration_ms||0) + '</td></tr>';
    });
    html += '</tbody></table>';
  }
  html += '</div>';
  box.innerHTML = html;
}

// ═════════════════════════════════════════
