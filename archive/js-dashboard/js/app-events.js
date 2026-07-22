/* app-events.js - Event binding for MAOP Dashboard
 *
 * Replaces all inline onclick/onkeydown/oninput/onchange handlers
 * with addEventListener + event delegation for CSP compliance
 * (script-src 'self' without 'unsafe-inline').
 *
 * Binding strategy:
 *   - Click events: event delegation on document.body via data-action attr
 *   - Keydown/input/change: direct addEventListener by element ID
 */

document.addEventListener('DOMContentLoaded', function() {
  'use strict';

  // ── Click delegation via data-action ──────────────────────────
  document.body.addEventListener('click', function(e) {
    var el = e.target.closest('[data-action]');
    if (!el) return;
    var action = el.dataset.action;
    var arg = el.dataset.arg;

    switch (action) {
      // Login
      case 'doLogin':            doLogin(); break;
      // Topbar
      case 'load':               load(); break;
      // Agent upgrade
      case 'upgradeAgent':       upgradeAgent(); break;
      case 'batchUpgrade':       batchUpgrade(); break;
      case 'loadUpgrade':        loadUpgrade(); break;
      case 'exportUpgradeReport': exportUpgradeReport(); break;
      // Memory / attention
      case 'computeAttention':   computeAttention(); break;
      case 'clearAttnResults':   clearAttnResults(); break;
      case 'exportAttnResults':  exportAttnResults(); break;
      // Evolve
      case 'loadEvolveStatus':   loadEvolveStatus(); break;
      case 'triggerEvolveAnalyze': triggerEvolveAnalyze(); break;
      case 'loadEvolveSuggestions': loadEvolveSuggestions(); break;
      case 'applyEvolveSuggestions': applyEvolveSuggestions(); break;
      case 'exportEvolveReport': exportEvolveReport(); break;
      case 'resetEvolve':        resetEvolve(); break;
      case 'toggleEvolveDetail': toggleEvolveDetail(); break;
      // Search
      case 'switchSearchType':   switchSearchType(arg); break;
      case 'execUnifiedSearch':  execUnifiedSearch(); break;
      case 'clearSearchResults': clearSearchResults(); break;
      // Workflow
      case 'runWorkflow':        runWorkflow(); break;
      case 'stopWorkflow':       stopWorkflow(); break;
      case 'loadWfHistory':      loadWfHistory(); break;
      case 'loadWfExec':         loadWfExec(); break;
      // Model
      case 'switchModel':        switchModel(); break;
      case 'testModelConnection': testModelConnection(); break;
      case 'saveModelConfig':    saveModelConfig(); break;
      // Logs
      case 'loadLogs':           loadLogs(arg); break;
      case 'clearLogTime':       clearLogTime(); break;
      // Control panel (CSP-compliant)
      case 'execAction':         execAction(arg); break;
      case 'maintAction':        maintAction(arg); break;
      // Chat
      case 'chatSend':           if(window.MAOP && MAOP.chat) MAOP.chat.send(); break;
      case 'chatClear':          if(window.MAOP && MAOP.chat) MAOP.chat.clear(); break;
      // Agents
      case 'agentsScan':         if(window.MAOP && MAOP.agents) MAOP.agents.scan(); break;
      case 'agentsRefresh':      if(window.MAOP && MAOP.agents) MAOP.agents.refresh(); break;
      case 'agentHealth':        if(window.MAOP && MAOP.agents) MAOP.agents.healthCheck(el.dataset.agent); break;
      case 'agentEnable':        if(window.MAOP && MAOP.agents) MAOP.agents.toggleAgent(el.dataset.agent, true); break;
      case 'agentDisable':       if(window.MAOP && MAOP.agents) MAOP.agents.toggleAgent(el.dataset.agent, false); break;
      case 'upgradeAgentByName': upgradeAgentByName(arg); break;
    }
  });

  // ── Keydown: search input Enter ───────────────────────────────
  var searchInput = document.getElementById('unified-search-input');
  if (searchInput) {
    searchInput.addEventListener('keydown', function(e) {
      if (e.key === 'Enter') execUnifiedSearch();
    });
  }

  // ── Input: log filter ─────────────────────────────────────────
  var logFilter = document.getElementById('log-filter');
  if (logFilter) {
    logFilter.addEventListener('input', filterLogs);
  }

  // ── Change: log time range ────────────────────────────────────
  var logStart = document.getElementById('log-start');
  if (logStart) logStart.addEventListener('change', filterLogs);

  var logEnd = document.getElementById('log-end');
  if (logEnd) logEnd.addEventListener('change', filterLogs);
});
