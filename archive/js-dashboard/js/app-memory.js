'use strict';
// app-memory.js — 操作: 记忆系统
// 操作: 记忆系统
// ═════════════════════════════════════════
async function loadMemory() {
  const d = await fetchJSON('/api/memory/stats');
  if (d) {
    const entries = Object.entries(d).filter(([k]) => typeof d[k] !== 'object');
    el('tb-mem-graph').innerHTML = entries.length ? entries.map(([k,v]) => `<tr><td>${esc(k)}</td><td>${esc(v)}</td></tr>`).join('') : '<tr><td colspan=2 class="empty">无数据</td></tr>';
  } else {
    el('tb-mem-graph').innerHTML = '<tr><td colspan=2 class="empty">无数据</td></tr>';
  }
  const v = await fetchJSON('/api/vector/stats');
  if (v) {
    const entries = Object.entries(v).filter(([k]) => typeof v[k] !== 'object');
    el('tb-mem-vector').innerHTML = entries.length ? entries.map(([k,val]) => `<tr><td>${esc(k)}</td><td>${esc(val)}</td></tr>`).join('') : '<tr><td colspan=2 class="empty">无数据</td></tr>';
  } else {
    el('tb-mem-vector').innerHTML = '<tr><td colspan=2 class="empty">无数据</td></tr>';
  }
  const dm = await fetchJSON('/api/memory/deep');
  if (dm) {
    const s = dm.stats || {};
    const ok = dm.status === 'ok';
    // 深度记忆统计：结构化卡片
    const dmCards = [
      { label: '记忆条目', value: s.total_entries ?? 0, icon: 'M', color: '#3b82f6' },
      { label: '追踪记录', value: s.total_traces ?? 0, icon: 'T', color: '#a78bfa' },
      { label: '轨迹步数', value: s.total_trajectory_steps ?? 0, icon: 'S', color: '#06b6d4' },
      { label: '向量索引', value: s.vector_count ?? 0, icon: 'V', color: '#22c55e' },
    ];
    let dmHtml = `<div style="margin-bottom:10px"><span class="badge ${ok?'g':'r'}">${ok?'深度记忆正常':'深度记忆异常'}</span></div>`;
    dmHtml += '<div class="dm-stat-grid">';
    dmHtml += dmCards.map(c =>
      `<div class="dm-stat-card" style="border-top:3px solid ${c.color}"><div class="dm-stat-top"><div class="dm-stat-icon" style="background:${c.color}22;color:${c.color}">${c.icon}</div><div class="dm-stat-lbl">${c.label}</div></div><div class="dm-stat-divider"></div><div class="dm-stat-val">${c.value}</div></div>`
    ).join('');
    dmHtml += '</div>';
    // 功能模块状态
    const mods = [
      { name: '布隆过滤器', enabled: s.bloom_filter !== false, desc: '快速判重，避免重复存储' },
      { name: '向量索引', enabled: s.vector_index !== false, desc: '余弦相似度TopK检索' },
      { name: 'FTS5全文检索', enabled: true, desc: 'SQLite全文搜索引擎' },
      { name: '深度追踪', enabled: (s.total_traces ?? 0) > 0, desc: '记录记忆形成过程' },
    ];
    dmHtml += '<div class="dm-mod-row">';
    dmHtml += mods.map(m =>
      `<div class="dm-mod-item"><div class="dm-mod-head"><span class="dm-mod-name">${m.name}</span><span class="health-dot ${m.enabled?'h-ok':'h-err'}"></span></div><div class="dm-mod-divider"></div><div class="dm-mod-desc">${m.desc}</div></div>`
    ).join('');
    dmHtml += '</div>';
    // Agent分布
    const byAgent = s.by_agent || {};
    const agentKeys = Object.keys(byAgent);
    if (agentKeys.length) {
      dmHtml += '<div class="dm-section-title">Agent分布</div>';
      dmHtml += '<div class="dm-dist-row">';
      dmHtml += agentKeys.map(k =>
        `<div class="dm-dist-item"><span class="dm-dist-lbl">${esc(k)}</span><span class="dm-dist-val">${byAgent[k]}</span></div>`
      ).join('');
      dmHtml += '</div>';
    }
    // Topic分布
    const byTopic = s.by_topic || {};
    const topicKeys = Object.keys(byTopic);
    if (topicKeys.length) {
      dmHtml += '<div class="dm-section-title">主题分布</div>';
      dmHtml += '<div class="dm-dist-row">';
      dmHtml += topicKeys.map(k =>
        `<div class="dm-dist-item"><span class="dm-dist-lbl">${esc(k)}</span><span class="dm-dist-val">${byTopic[k]}</span></div>`
      ).join('');
      dmHtml += '</div>';
    }
    // 时间范围
    if (s.oldest || s.newest) {
      dmHtml += `<div class="dm-time-range"><span class="muted">时间范围:</span> ${esc(s.oldest||'?')} ~ ${esc(s.newest||'?')}</div>`;
    }
    el('deepmem-stats').innerHTML = dmHtml;

    // 记忆追踪：展示最近条目
    const recent = arrize(s.recent_entries || []);
    if (recent.length) {
      el('deepmem-trace').innerHTML = recent.map((r,i) => {
        const content = r.content || r.text || r.value || r.key || '';
        const agent = r.agent || r.source || '';
        const topic = r.topic || r.type || '';
        const ts = r.timestamp || r.created_at || '';
        return `<div class="dm-trace-item">
          <div class="dm-trace-head"><b>#${i+1}</b> ${agent?`<span class="badge g" style="font-size:10px">${esc(agent)}</span>`:''} ${topic?`<span class="badge" style="font-size:10px;background:var(--bg3);color:var(--text2)">${esc(topic)}</span>`:''} <span class="muted" style="font-size:11px">${esc(ts)}</span></div>
          <div class="dm-trace-body">${esc(typeof content==='string'?content.slice(0,200):JSON.stringify(content).slice(0,200))}</div>
        </div>`;
      }).join('');
    } else {
      // 尝试从 /api/memory/trace 获取追踪数据
      const tr = await fetchJSON('/api/memory/trace');
      const traces = arrize(tr?.traces || []);
      if (traces.length) {
        el('deepmem-trace').innerHTML = traces.slice(0,20).map((t,i) => {
          const src = t.source || t.from || '';
          const tgt = t.target || t.to || '';
          const rel = t.relation || t.type || '';
          return `<div class="dm-trace-item">
            <div class="dm-trace-head"><b>#${i+1}</b> ${src?`<span class="badge g" style="font-size:10px">${esc(src)}</span>`:''} -> ${tgt?`<span class="badge" style="font-size:10px;background:var(--bg3);color:var(--text2)">${esc(tgt)}</span>`:''} ${rel?`<span class="muted" style="font-size:11px">(${esc(rel)})</span>`:''}</div>
          </div>`;
        }).join('');
      } else {
        el('deepmem-trace').innerHTML = '<div class="empty">暂无追踪记录</div>';
      }
    }
  } else {
    el('deepmem-stats').innerHTML = '<div class="empty">无数据</div>';
    el('deepmem-trace').innerHTML = '<div class="empty">暂无追踪记录</div>';
  }
  const n = await fetchJSON('/api/neural/status');
  if (n) {
    const mechNames = {attention:'注意力机制',transform:'变换层',embedding:'嵌入层',vector_store:'向量存储'};
    const mechIcons = {attention:'Attn',transform:'Trfm',embedding:'Embd',vector_store:'VecS'};
    const mechDescs = {attention:'计算查询与记忆条目的相关性权重，决定"关注什么"',transform:'多层变换处理，对输入进行非线性映射',embedding:'将文本映射为高维向量，支持相似度计算',vector_store:'持久化存储向量并支持TopK检索'};
    const m = n.mechanisms || {};
    let html = `<div style="margin-bottom:10px"><span class="badge ${n.status==='ok'?'g':'r'}">${n.status==='ok'?'系统正常':'系统异常'}</span></div>`;
    html += Object.entries(m).map(([k,v]) => {
      const cn = mechNames[k]||k, icon = mechIcons[k]||'?', desc = mechDescs[k]||'';
      const enabled = v.enabled !== false;
      const dot = enabled ? 'h-ok' : 'h-err';
      const pnames = {mechanism:'机制类型',layers:'网络层数',dim:'向量维度',model:'使用模型',count:'索引数量',error:'错误信息'};
      const paramEntries = Object.entries(v).filter(([pk])=>pk!=='enabled');
      const paramHtml = paramEntries.map(([pk,pv])=>{
        const lbl = pnames[pk]||pk;
        if (pk==='error') return `<div class="neural-param-row" style="color:#f87171"><span class="neural-param-lbl">${lbl}</span><span class="neural-param-val">${esc(String(pv).slice(0,80))}</span></div>`;
        return `<div class="neural-param-row"><span class="neural-param-lbl">${lbl}</span><span class="neural-param-val">${esc(String(pv))}</span></div>`;
      }).join('');
      return `<div class="neural-mech-card" style="border-left:3px solid ${enabled?'#22c55e':'#64748b'}">
        <div class="neural-mech-head"><span class="health-dot ${dot}"></span> <div class="neural-mech-icon" style="background:${enabled?'#22c55e22':'#64748b22'};color:${enabled?'#22c55e':'#64748b'}">${icon}</div> <b style="font-size:13px">${cn}</b> <span class="muted" style="font-size:11px">(${k})</span> <span style="margin-left:auto;font-size:11px;color:${enabled?'#22c55e':'#64748b'}">${enabled?'已启用':'未启用'}</span></div>
        <div style="font-size:12px;color:var(--text2);margin:6px 0;line-height:1.5">${desc}</div>
        ${paramHtml ? `<div class="neural-param-grid">${paramHtml}</div>` : ''}
      </div>`;
    }).join('');
    el('neural-status').innerHTML = html || '<div class="empty">无数据</div>';
  }
}
async function computeAttention() {
  const q = el('attn-query').value.trim();
  if (!q) return;
  const d = await fetchJSON(`/api/neural/attention?q=${encodeURIComponent(q)}`);
  if (!d) { el('attn-results').innerHTML = '<span class="warn">计算失败</span>'; return; }
  let html = `<div style="margin-bottom:8px"><b>查询:</b> ${esc(d.query||q)}</div>`;
  const results = arrize(d.results||[]);
  const weights = arrize(d.attention_weights||[]);
  if (results.length) {
    html += `<div style="font-size:12px;color:var(--text2);margin-bottom:6px">匹配结果 (${results.length} 条)</div>`;
    html += results.map((r,i) => {
      const score = r.score!=null ? r.score : (weights[i]!=null ? weights[i] : '');
      const scoreBar = score!=='' ? `<div style="height:3px;background:var(--bg3);border-radius:2px;margin:4px 0"><div style="height:100%;width:${Math.min(parseFloat(score)*100||0,100)}%;background:var(--accent);border-radius:2px"></div></div>` : '';
      const content = r.content||r.text||r.key||r.id||'';
      return `<div class="search-result"><div style="display:flex;align-items:center;gap:6px"><b>#${i+1}</b> ${score!==''?`<span class="badge g" style="font-size:10px">权重 ${esc(String(score))}</span>`:''}</div>${scoreBar}<div style="font-size:12px;color:var(--text2);margin-top:2px">${esc(typeof content==='string'?content:JSON.stringify(content).slice(0,200))}</div></div>`;
    }).join('');
  } else {
    html += `<div class="empty">无匹配结果</div>`;
  }
  if (weights.length && !results.length) {
    html += `<div style="font-size:12px;color:var(--text2);margin:8px 0 6px">注意力权重 (${weights.length} 个)</div>`;
    html += weights.map((w,i) => `<div class="kv"><span>#${i+1}</span><b>${esc(String(w))}</b></div>`).join('');
  }
  el('attn-results').innerHTML = html;
}

// ═════════════════════════════════════════
