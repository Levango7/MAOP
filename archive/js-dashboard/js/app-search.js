'use strict';
// app-search.js — 操作: 搜索中心
// 操作: 搜索中心 (统一搜索)
// ═════════════════════════════════════════
let _searchType = 'memory';
const _searchTypeNames = {memory:'记忆搜索',vector:'向量搜索',graph:'图谱搜索',log:'日志搜索',agent:'Agent搜索'};
const _searchPlaceholders = {
  memory:'搜索记忆条目(关键词)...',
  vector:'搜索向量(语义关键词)...',
  graph:'查找模块节点(模块名)...',
  log:'搜索日志(关键词)...',
  agent:'搜索Agent(名称/能力)...',
};
function switchSearchType(t) {
  _searchType = t;
  document.querySelectorAll('.search-tab').forEach(b => b.classList.toggle('active', b.dataset.stype === t));
  const inp = el('unified-search-input');
  inp.placeholder = _searchPlaceholders[t] || '输入搜索关键词...';
  inp.value = '';
  el('unified-search-results').innerHTML = '<div class="empty">请输入关键词后点击搜索</div>';
  el('search-meta').textContent = '尚未搜索';
  renderSearchOptions();
}
function renderSearchOptions() {
  const opts = el('search-options');
  if (_searchType === 'memory') {
    opts.innerHTML = '<div class="search-opt-row"><label>类型</label><select id="so-mem-type" class="inp"><option value="">全部</option><option value="episode">Episode</option><option value="skill">技能</option><option value="config">配置</option><option value="error">错误</option></select><label>TopK</label><select id="so-mem-topk" class="inp"><option value="10">10</option><option value="20" selected>20</option><option value="50">50</option></select></div>';
  } else if (_searchType === 'vector') {
    opts.innerHTML = '<div class="search-opt-row"><label>TopK</label><select id="so-vec-topk" class="inp"><option value="5">5</option><option value="10" selected>10</option><option value="20">20</option></select></div>';
  } else if (_searchType === 'graph') {
    opts.innerHTML = '<div class="search-opt-row"><label>查找方式</label><select id="so-graph-mode" class="inp"><option value="neighbors">邻居节点</option><option value="nodes">全部节点</option><option value="edges">依赖边</option></select></div>';
  } else if (_searchType === 'log') {
    opts.innerHTML = '<div class="search-opt-row"><label>日志类型</label><select id="so-log-type" class="inp"><option value="all">全部</option><option value="dashboard">Dashboard</option><option value="delegations">委托</option><option value="checker">检查</option></select></div>';
  } else if (_searchType === 'agent') {
    opts.innerHTML = '<div class="search-opt-row"><label>筛选</label><select id="so-agent-filter" class="inp"><option value="">全部</option><option value="ok">仅可用</option><option value="error">仅异常</option></select></div>';
  } else {
    opts.innerHTML = '';
  }
}
async function loadSearchIndex() {
  renderSearchOptions();
  const stats = await fetchJSON('/api/vector/stats');
  const memStats = await fetchJSON('/api/memory/stats');
  const graphStats = await fetchJSON('/api/graph/stats');
  let html = '<div class="search-stat-grid">';
  if (memStats) {
    const me = Object.entries(memStats).filter(([k])=>typeof memStats[k]!=='object');
    html += me.map(([k,v])=>`<div class="search-stat-item"><span class="search-stat-lbl">${esc(k)}</span><div class="stat-divider"></div><b class="search-stat-val">${esc(v)}</b></div>`).join('');
  }
  if (stats) {
    const ve = Object.entries(stats).filter(([k])=>typeof stats[k]!=='object');
    html += ve.map(([k,v])=>`<div class="search-stat-item"><span class="search-stat-lbl">${esc(k)}</span><div class="stat-divider"></div><b class="search-stat-val">${esc(v)}</b></div>`).join('');
  }
  if (graphStats) {
    const ge = Object.entries(graphStats).filter(([k])=>typeof graphStats[k]!=='object');
    html += ge.map(([k,v])=>`<div class="search-stat-item"><span class="search-stat-lbl">${esc(k)}</span><div class="stat-divider"></div><b class="search-stat-val">${esc(v)}</b></div>`).join('');
  }
  html += '</div>';
  el('search-index-stats').innerHTML = html || '<div class="empty">无索引数据</div>';
}
async function execUnifiedSearch() {
  const q = el('unified-search-input').value.trim();
  if (!q && _searchType !== 'graph') { el('unified-search-results').innerHTML = '<div class="empty">请输入搜索关键词</div>'; return; }
  const t0 = Date.now();
  el('search-meta').textContent = '搜索中...';
  el('unified-search-results').innerHTML = '<div class="empty">正在搜索...</div>';
  let results = [];
  let metaText = '';
  if (_searchType === 'memory') {
    const topk = el('so-mem-topk')?.value || 20;
    const d = await fetchJSON(`/api/memory/search?q=${encodeURIComponent(q)}&topk=${topk}`);
    results = arrize(d?.results || d);
    metaText = `记忆搜索 "${q}" - ${results.length} 条结果`;
    el('unified-search-results').innerHTML = results.length ? results.map((r,i)=>{
      const score = r.score||'';
      const content = r.content||r.text||r.value||'';
      return `<div class="search-result"><div class="sr-head"><b>#${i+1}</b> ${score?`<span class="badge g">score ${esc(score)}</span>`:''} <span class="muted">${esc(r.key||r.id||r.type||'')}</span></div><div class="sr-body">${esc(typeof content==='string'?content:JSON.stringify(content).slice(0,300))}</div></div>`;
    }).join('') : '<div class="empty">无匹配结果</div>';
  } else if (_searchType === 'vector') {
    const topk = el('so-vec-topk')?.value || 10;
    const d = await fetchJSON(`/api/vector/search?q=${encodeURIComponent(q)}&topk=${topk}`);
    results = arrize(d?.results || d);
    metaText = `向量搜索 "${q}" - ${results.length} 条结果`;
    el('unified-search-results').innerHTML = results.length ? results.map((r,i)=>{
      const score = parseFloat(r.score||0);
      const bar = `<div class="sr-scorebar"><div style="width:${Math.min(score*100,100)}%"></div></div>`;
      const content = r.content||r.text||r.metadata||'';
      return `<div class="search-result"><div class="sr-head"><b>#${i+1}</b> <span class="badge g">${esc(r.score||'')}</span> <span class="muted">${esc(r.id||r.key||'')}</span></div>${bar}<div class="sr-body">${esc(typeof content==='string'?content:JSON.stringify(content).slice(0,300))}</div></div>`;
    }).join('') : '<div class="empty">无匹配结果</div>';
  } else if (_searchType === 'graph') {
    const mode = el('so-graph-mode')?.value || 'neighbors';
    if (mode === 'neighbors' && q) {
      const d = await fetchJSON(`/api/graph/neighbors?node=${encodeURIComponent(q)}`);
      results = arrize(d?.neighbors || d?.results || d);
      metaText = `图谱邻居 "${q}" - ${results.length} 个邻居`;
    } else if (mode === 'edges') {
      const d = await fetchJSON('/api/graph/edges');
      results = arrize(d?.edges || d);
      metaText = `依赖边 - ${results.length} 条`;
    } else {
      const d = await fetchJSON('/api/graph/nodes');
      results = arrize(d?.nodes || d);
      if (q) results = results.filter(n => JSON.stringify(n).toLowerCase().includes(q.toLowerCase()));
      metaText = `图谱节点 - ${results.length} 个`;
    }
    el('unified-search-results').innerHTML = results.length ? results.map((r,i)=>{
      const name = r.name||r.id||r.node||'';
      const deps = r.deps||r.dependencies||r.targets||[];
      const depStr = Array.isArray(deps) ? deps.join(', ') : '';
      return `<div class="search-result"><div class="sr-head"><b>#${i+1}</b> <span class="muted">${esc(name)}</span></div>${depStr?`<div class="sr-body">依赖: ${esc(depStr)}</div>`:''}</div>`;
    }).join('') : '<div class="empty">无匹配结果</div>';
  } else if (_searchType === 'log') {
    const logType = el('so-log-type')?.value || 'all';
    const d = await fetchJSON(`/api/logs?type=${logType}`);
    const text = typeof d === 'string' ? d : (d?.content||d?.logs||JSON.stringify(d)||'');
    const lines = (text||'').split('\n').filter(l => !q || l.toLowerCase().includes(q.toLowerCase()));
    results = lines.slice(0, 200);
    metaText = `日志搜索 "${q}" - ${lines.length} 行匹配 (显示前200行)`;
    el('unified-search-results').innerHTML = results.length ? `<div class="log-results-box">${results.map(l=>`<div class="log-line">${esc(l)}</div>`).join('')}</div>` : '<div class="empty">无匹配日志</div>';
  } else if (_searchType === 'agent') {
    const d = await fetchJSON('/api/agents');
    let agents = arrize(d?.agents || d);
    if (q) agents = agents.filter(a => JSON.stringify(a).toLowerCase().includes(q.toLowerCase()));
    const filter = el('so-agent-filter')?.value || '';
    if (filter === 'ok') agents = agents.filter(a => (a.status||'ok')==='ok');
    if (filter === 'error') agents = agents.filter(a => (a.status||'')==='error');
    results = agents;
    metaText = `Agent搜索 "${q}" - ${results.length} 个匹配`;
    el('unified-search-results').innerHTML = results.length ? results.map((a,i)=>{
      const name = a.name||a.id||'';
      const model = a.model||'';
      const cli = a.cli||'';
      const caps = (a.capabilities||[]).join(', ');
      return `<div class="search-result"><div class="sr-head"><b>#${i+1}</b> ${statusBadge(a.status||'ok')} <b>${esc(name)}</b></div><div class="sr-body">模型: ${esc(model)} | CLI: ${esc(cli)}${caps?` | 能力: ${esc(caps)}`:''}</div></div>`;
    }).join('') : '<div class="empty">无匹配Agent</div>';
  }
  const elapsed = Date.now() - t0;
  el('search-meta').innerHTML = `${esc(metaText)} <span class="muted">(${elapsed}ms)</span>`;
}
function clearSearchResults() {
  el('unified-search-input').value = '';
  el('unified-search-results').innerHTML = '<div class="empty">请输入关键词后点击搜索</div>';
  el('search-meta').textContent = '尚未搜索';
}

// ═════════════════════════════════════════
