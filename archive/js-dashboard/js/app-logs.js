'use strict';
// app-logs.js — 监控: 日志
// 监控: 日志
// ═════════════════════════════════════════
async function loadLogs(type) {
  const d = await fetchJSON(`/api/logs?type=${type}`);
  const text = typeof d === 'string' ? d : (d?.content || d?.logs || (d == null || (Array.isArray(d) && d.length === 0) ? '暂无日志数据' : JSON.stringify(d, null, 2)));
  _logData = (text || '').split('\n');
  renderLogs();
  loadLogAnalysis();
}
async function loadLogAnalysis() {
  const d = await fetchJSON('/api/logs/analysis');
  if (!d || d.error) { el('log-analysis').innerHTML = '<div class="empty">无分析数据</div>'; return; }
  let html = '';
  const total = d.total || 0;
  const bs = d.by_status || {};
  html += '<div class="stat-row" style="display:flex;gap:8px;margin-bottom:10px">';
  html += '<div class="mini-stat" style="flex:1;text-align:center;padding:6px;background:var(--bg3);border-radius:6px"><div style="font-size:11px;color:var(--text2)">总记录</div><div style="font-size:16px;font-weight:700">' + total + '</div></div>';
  html += '<div class="mini-stat" style="flex:1;text-align:center;padding:6px;background:var(--bg3);border-radius:6px"><div style="font-size:11px;color:var(--text2)">成功</div><div style="font-size:16px;font-weight:700;color:var(--success)">' + (bs.success||0) + '</div></div>';
  html += '<div class="mini-stat" style="flex:1;text-align:center;padding:6px;background:var(--bg3);border-radius:6px"><div style="font-size:11px;color:var(--text2)">失败</div><div style="font-size:16px;font-weight:700;color:var(--fail)">' + (bs.failure||0) + '</div></div>';
  html += '<div class="mini-stat" style="flex:1;text-align:center;padding:6px;background:var(--bg3);border-radius:6px"><div style="font-size:11px;color:var(--text2)">超时</div><div style="font-size:16px;font-weight:700;color:var(--warn)">' + (bs.timeout||0) + '</div></div>';
  html += '</div>';
  const ba = d.by_agent || {};
  const agentEntries = Object.entries(ba).sort((a,b) => b[1]-a[1]);
  if (agentEntries.length) {
    html += '<div style="font-size:12px;color:var(--text2);margin:8px 0 4px">按Agent分布</div>';
    html += '<table class="info-table"><thead><tr><th>Agent</th><th>次数</th></tr></thead><tbody>';
    for (const [ag, cnt] of agentEntries) { html += '<tr><td>' + esc(ag) + '</td><td>' + cnt + '</td></tr>'; }
    html += '</tbody></table>';
  }
  const ep = d.error_patterns || [];
  if (ep.length) {
    html += '<div style="font-size:12px;color:var(--text2);margin:8px 0 4px">错误模式 Top10</div>';
    html += '<table class="info-table"><thead><tr><th>错误</th><th>次数</th></tr></thead><tbody>';
    for (const [err, cnt] of ep) { html += '<tr><td style="font-size:11px">' + esc(err) + '</td><td>' + cnt + '</td></tr>'; }
    html += '</tbody></table>';
  }
  if (!total) html += '<div class="empty">暂无委派日志数据</div>';
  el('log-analysis').innerHTML = html;
}
function filterLogs() { renderLogs(); }
function clearLogTime() {
  el('log-start').value = '';
  el('log-end').value = '';
  renderLogs();
}
function renderLogs() {
  const f = (el('log-filter')?.value || '').toLowerCase();
  const start = el('log-start')?.value || '';
  const end = el('log-end')?.value || '';
  let lines = _logData;
  if (f) lines = lines.filter(l => l.toLowerCase().includes(f));
  if (start || end) {
    const startTs = start ? new Date(start).getTime() : 0;
    const endTs = end ? new Date(end).getTime() : Date.now();
    lines = lines.filter(l => {
      const m = l.match(/(\d{4}[-/]\d{2}[-/]\d{2}[ T]\d{2}:\d{2})/);
      if (!m) return true;
      const ts = new Date(m[1].replace(/-/g,'/')).getTime();
      return ts >= startTs && ts <= endTs;
    });
  }
  el('log-content').textContent = lines.slice(-500).join('\n');
}

// ═════════════════════════════════════════
