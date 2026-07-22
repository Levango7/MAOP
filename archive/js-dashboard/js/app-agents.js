/* MAOP Dashboard — Agent Management UI Component */

(function(){
  const MAOP = window.MAOP || (window.MAOP = {});

  MAOP.agents = {
    agents: [],

    init(){
      this.load();
    },

    async load(){
      try{
        const resp = await fetch('/api/agents');
        const data = await resp.json();
        if(data.status === 'ok'){
          this.agents = data.data.agents || [];
          this.render();
        }
      }catch(e){
        console.warn('Failed to load agents:', e);
      }
    },

    render(){
      const panel = document.getElementById('agents-panel');
      if(!panel) return;
      panel.innerHTML = `
        <div class="agents-container">
          <div class="agents-header">
            <h3>Agent Registry</h3>
            <div class="agents-controls">
              <button class="btn btn-sm" data-action="agentsScan">Scan</button>
              <button class="btn btn-sm" data-action="agentsRefresh">Refresh</button>
            </div>
          </div>
          <div class="agents-stats" id="agents-stats"></div>
          <div class="agents-list" id="agents-list"></div>
        </div>
      `;
      this.renderStats();
      this.renderList();
    },

    renderStats(){
      const el = document.getElementById('agents-stats');
      if(!el) return;
      const total = this.agents.length;
      const healthy = this.agents.filter(a => a.status === 'healthy').length;
      const enabled = this.agents.filter(a => a.enabled !== false).length;
      el.innerHTML = `
        <div class="stat-card"><span class="stat-value">${total}</span><span class="stat-label">Total</span></div>
        <div class="stat-card"><span class="stat-value">${healthy}</span><span class="stat-label">Healthy</span></div>
        <div class="stat-card"><span class="stat-value">${enabled}</span><span class="stat-label">Enabled</span></div>
      `;
    },

    renderList(){
      const el = document.getElementById('agents-list');
      if(!el) return;

      if(!this.agents.length){
        el.innerHTML = '<div class="empty-state">No agents registered. Click "Scan" to discover local agents.</div>';
        return;
      }

      el.innerHTML = this.agents.map(a => `
        <div class="agent-card ${a.enabled === false ? 'agent-disabled' : ''}">
          <div class="agent-header">
            <span class="agent-name">${a.name || a.agent_id}</span>
            <span class="agent-status status-${a.status || 'unknown'}">${a.status || 'unknown'}</span>
          </div>
          <div class="agent-meta">
            ${a.provider ? `<span class="agent-tag">${a.provider}</span>` : ''}
            ${a.version ? `<span class="agent-tag">v${a.version}</span>` : ''}
            ${a.capabilities ? a.capabilities.slice(0,4).map(c => `<span class="agent-cap">${c}</span>`).join('') : ''}
          </div>
          <div class="agent-actions">
            <button class="btn btn-xs" data-action="agentHealth" data-agent="${a.agent_id || a.name}">Health Check</button>
            ${a.enabled !== false
              ? `<button class="btn btn-xs btn-warn" data-action="agentDisable" data-agent="${a.agent_id || a.name}">Disable</button>`
              : `<button class="btn btn-xs btn-ok" data-action="agentEnable" data-agent="${a.agent_id || a.name}">Enable</button>`
            }
          </div>
        </div>
      `).join('');
    },

    async scan(){
      try{
        const resp = await fetch('/api/agents/scan', {method: 'POST'});
        const data = await resp.json();
        if(data.status === 'ok') this.load();
      }catch(e){ console.warn('Scan failed:', e); }
    },

    async healthCheck(agentId){
      try{
        const resp = await fetch(`/api/agents/${agentId}/health-check`, {method: 'POST'});
        const data = await resp.json();
        if(data.status === 'ok') this.load();
      }catch(e){ console.warn('Health check failed:', e); }
    },

    async toggleAgent(agentId, enable){
      try{
        const resp = await fetch(`/api/agents/${agentId}/${enable ? 'enable' : 'disable'}`, {method: 'POST'});
        const data = await resp.json();
        if(data.status === 'ok') this.load();
      }catch(e){ console.warn('Toggle failed:', e); }
    },

    refresh(){
      this.load();
    }
  };

  document.addEventListener('DOMContentLoaded', () => MAOP.agents.init());
})();