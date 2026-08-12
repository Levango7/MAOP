<template>
  <div class="agents-page view-enter">
    <PageHeader>
      <span class="live-tag" :class="{ active: realtime.connected }">
        <AppIcon :name="realtime.connected ? 'radio' : 'radio'" :size="12" />
        {{ realtime.connected ? t('common.live') : t('common.offline') }}
      </span>
      <Segmented
        :model-value="viewMode"
        :options="[{ value: 'grid', label: t('common.grid') }, { value: 'table', label: t('common.table') }]"
        size="sm"
        @update:model-value="viewMode = $event"
      />
      <button class="btn-action" :class="{ 'pulse-once': scanning }" :disabled="scanning || !isAdmin" :title="!isAdmin ? t('nav.editionLocked') : ''" @click="scanLocal">
        <AppIcon name="search" :size="14" />
        {{ scanning ? t('view.agents.scanning') : t('view.agents.scanNow') }}
      </button>
      <button class="btn-action" :class="{ 'pulse-once': refreshing }" :disabled="refreshing" @click="loadAgents">
        <AppIcon name="refresh" :size="14" />
        {{ refreshing ? t('common.loading') : t('common.refresh') }}
      </button>
    </PageHeader>

    <Card :title="t('view.agents.dispatchRouter')" :subtitle="t('view.agents.dispatchRouterSub')" :margin-bottom="24">
      <template #actions>
        <button class="btn-action" :class="{ 'pulse-once': loadingDecisions }" :disabled="loadingDecisions" @click="loadDecisions">
          <AppIcon name="refresh" :size="14" />
          {{ loadingDecisions ? t('common.loading') : t('view.agents.refreshDecisions') }}
        </button>
      </template>

      <!-- 说明文案 -->
      <div class="dispatch-intro">
        <AppIcon name="info" :size="14" />
        <span>{{ t('view.agents.dispatchIntro') }}</span>
      </div>

      <!-- Section 1: 静态路由规则 -->
      <h4 class="dispatch-section-title">{{ t('view.agents.routingRules') }}</h4>
      <div v-if="loading" class="skeleton-grid">
        <Skeleton v-for="n in 4" :key="n" height="46px" radius="8px" />
      </div>
      <div v-else-if="routes.length" class="dispatch-grid">
        <div v-for="route in routes" :key="route.capability" class="dispatch-card">
          <div class="route-cap">
            <AppIcon name="route" :size="14" />
            <span class="route-cap-name">{{ route.capability }}</span>
          </div>
          <div class="route-chain">
            <div class="route-node primary">
              <span class="route-agent">{{ route.primary || '—' }}</span>
              <span v-if="route.primary_model" class="route-model mono">{{ route.primary_model }}</span>
            </div>
            <div v-if="route.fallback" class="route-sep">→</div>
            <div v-if="route.fallback" class="route-node fallback">
              <span class="route-agent">{{ route.fallback }}</span>
              <span v-if="route.fallback_model" class="route-model mono">{{ route.fallback_model }}</span>
            </div>
            <div v-if="route.tertiary" class="route-sep">→</div>
            <div v-if="route.tertiary" class="route-node tertiary">
              <span class="route-agent">{{ route.tertiary }}</span>
            </div>
          </div>
          <div v-if="route.keywords && route.keywords.length" class="route-keywords">
            <span v-for="kw in route.keywords.slice(0, 6)" :key="kw" class="kw-chip">{{ kw }}</span>
            <span v-if="route.keywords.length > 6" class="kw-more">+{{ route.keywords.length - 6 }}</span>
          </div>
        </div>
      </div>
      <EmptyState v-else icon="route" :title="t('view.agents.noRoutes')" :hint="t('view.agents.noRoutesHint')" />

      <!-- Section 2: 最近的实际路由执行记录 -->
      <h4 class="dispatch-section-title">
        {{ t('view.agents.recentDecisions') }}
        <span v-if="decisions.length" class="dispatch-count">({{ decisions.length }})</span>
      </h4>
      <div v-if="loadingDecisions" class="skeleton-grid">
        <Skeleton v-for="n in 3" :key="n" height="42px" radius="8px" />
      </div>
      <div v-else-if="decisions.length" class="decisions-list">
        <div v-for="d in decisions.slice(0, 20)" :key="d.trace_id + d.stage" class="decision-row">
          <span class="dec-stage" :class="d.stage">{{ d.stage }}</span>
          <span class="dec-agent">
            {{ d.output_summary?.selected_agent || d.output_summary?.selected_model || '—' }}
          </span>
          <span v-if="d.explanation" class="dec-reason">{{ d.explanation.slice(0, 80) }}{{ d.explanation.length > 80 ? '…' : '' }}</span>
          <span class="dec-time">{{ formatDecTime(d.timestamp) }}</span>
        </div>
      </div>
      <div v-else class="decisions-empty">
        <AppIcon name="activity" :size="16" />
        <span>{{ t('view.agents.noDecisions') }}</span>
      </div>
    </Card>

    <Card :title="t('view.agents.localScan')" :subtitle="t('view.agents.localScanSub')" :margin-bottom="24">
      <template #actions>
        <button class="btn-action" :class="{ 'pulse-once': scanning }" :disabled="scanning || !isAdmin" :title="!isAdmin ? t('nav.editionLocked') : ''" @click="scanLocal">
          <AppIcon name="search" :size="14" />
          {{ scanning ? t('view.agents.scanning') : t('view.agents.scanNow') }}
        </button>
      </template>
      <div v-if="scanning" class="skeleton-grid">
        <Skeleton v-for="n in 4" :key="n" height="46px" radius="8px" />
      </div>
      <div v-else-if="scanned.length" class="scanned-grid">
        <div v-for="s in scanned" :key="s.name" class="scanned-card">
          <div class="scanned-top">
            <div class="scanned-avatar" :class="s.status">{{ (s.name || '?').charAt(0).toUpperCase() }}</div>
            <div class="scanned-identity">
              <h3>{{ s.name }}</h3>
              <Badge :tone="s.status === 'available' ? 'success' : 'neutral'">{{ s.status === 'available' ? t('view.agents.available') : t('view.agents.unavailable') }}</Badge>
            </div>
            <span v-if="s.version" class="scanned-version mono">{{ s.version }}</span>
          </div>
          <div class="scanned-meta">
            <div class="sm-row"><span class="sm-key">{{ t('common.provider') }}</span><span class="sm-val">{{ s.provider || '—' }}</span></div>
            <div class="sm-row"><span class="sm-key">{{ t('view.agents.cliPath') }}</span><span class="sm-val mono path">{{ s.cli_path || '—' }}</span></div>
          </div>
          <div v-if="(s.capabilities || []).length" class="caps">
            <span v-for="c in s.capabilities" :key="c" class="cap-chip">{{ c }}</span>
          </div>
          <div v-if="s.status === 'available'" class="scanned-actions">
            <button class="act-btn" :disabled="addingAgent[s.name]" :title="t('view.agents.addToMaop')" @click="addToMaop(s)">
              <AppIcon name="plus" :size="14" /> {{ addingAgent[s.name] ? t('common.loading') : t('view.agents.addToMaop') }}
            </button>
          </div>
        </div>
      </div>
      <EmptyState v-else icon="search" :title="t('view.agents.noScanned')" :hint="t('view.agents.noScannedHint')" />
    </Card>

    <div v-if="viewMode === 'grid'" class="agent-grid">
      <Card
        v-for="a in agents"
        :key="a.name"
        class="agent-card"
        :class="{ selected: selectedAgent === a.name }"
        clickable
        @click="selectAgent(a)"
      >
        <div class="agent-top">
          <div class="agent-avatar" :style="{ background: agentColor(a.name) }">{{ (a.name || '?').charAt(0).toUpperCase() }}</div>
          <div class="agent-identity">
            <h3>{{ a.name }}</h3>
            <Badge :tone="statusTone(agentStatus(a))">{{ agentStatus(a) }}</Badge>
          </div>
        </div>
        <p class="agent-desc">{{ a.description || t('view.agents.noDescription') }}</p>
        <div class="agent-metrics">
          <div class="metric"><span class="metric-val mono">{{ a.model || 'auto' }}</span><span class="metric-lbl">{{ t('common.model') }}</span></div>
          <div class="metric"><span class="metric-val mono">{{ a.driver || '—' }}</span><span class="metric-lbl">{{ t('common.driver') }}</span></div>
          <div class="metric"><span class="metric-val">{{ (a.capabilities || []).length }}</span><span class="metric-lbl">{{ t('common.caps') }}</span></div>
          <div class="metric"><span class="metric-val">{{ a.last_latency_ms || 0 }}<small>ms</small></span><span class="metric-lbl">{{ t('common.latency') }}</span></div>
        </div>
        <div class="agent-actions">
          <button class="act-btn" :title="t('view.agents.switchModel')" @click.stop="switchModel(a)">
            <AppIcon name="bot" :size="14" /> {{ t('common.model') }}
          </button>
          <button class="act-btn" :title="t('view.agents.healthCheck')" @click.stop="healthCheck(a)">
            <AppIcon name="activity" :size="14" /> {{ t('view.agents.health') }}
          </button>
          <button class="act-btn" :disabled="repairing[a.name]" :title="t('view.agents.repair')" @click.stop="repairAgent(a)">
            <AppIcon name="wrench" :size="14" /> {{ repairing[a.name] ? t('common.loading') : t('view.agents.repair') }}
          </button>
          <button class="act-btn" :disabled="upgrading[a.name]" :title="t('view.agents.upgrade')" @click.stop="upgradeAgent(a)">
            <AppIcon name="arrow-up" :size="14" /> {{ upgrading[a.name] ? t('common.loading') : t('view.agents.upgrade') }}
          </button>
          <button class="act-btn" :title="t('view.agents.memory')" @click.stop="showMemory(a)">
            <AppIcon name="brain" :size="14" /> {{ t('view.agents.memory') }}
          </button>
          <button class="act-btn" :disabled="evolving[a.name]" :title="t('view.agents.evolve')" @click.stop="evolveAgent(a)">
            <AppIcon name="sparkles" :size="14" /> {{ evolving[a.name] ? t('common.loading') : t('view.agents.evolve') }}
          </button>
          <button class="act-btn danger" :disabled="!isAdmin" :title="t('view.agents.remove')" @click.stop="confirmRemove(a)">
            <AppIcon name="trash" :size="14" /> {{ t('view.agents.remove') }}
          </button>
        </div>
      </Card>
    </div>

    <Card v-else :margin-bottom="24" :title="t('view.agents.allAgents')" :padded="false">
      <div class="agent-table">
        <div class="trow header">
          <span>{{ t('view.agents.colAgent') }}</span><span>{{ t('common.status') }}</span><span>{{ t('common.model') }}</span><span>{{ t('common.driver') }}</span><span>{{ t('common.caps') }}</span><span>{{ t('common.latency') }}</span><span>{{ t('common.actions') }}</span>
        </div>
        <div v-for="a in agents" :key="a.name" class="trow">
          <span class="agent-name">{{ a.name }}</span>
          <span><Badge :tone="statusTone(agentStatus(a))">{{ agentStatus(a) }}</Badge></span>
          <span class="mono">{{ a.model || 'auto' }}</span>
          <span class="mono">{{ a.driver || '—' }}</span>
          <span>{{ (a.capabilities || []).length }}</span>
          <span>{{ a.last_latency_ms || 0 }}ms</span>
          <span class="actions-cell">
            <button class="act-btn small" :title="t('common.model')" @click="switchModel(a)"><AppIcon name="bot" :size="13" /></button>
            <button class="act-btn small" :title="t('view.agents.healthCheck')" @click="healthCheck(a)"><AppIcon name="activity" :size="13" /></button>
            <button class="act-btn small" :disabled="repairing[a.name]" :title="t('view.agents.repair')" @click="repairAgent(a)"><AppIcon name="wrench" :size="13" /></button>
            <button class="act-btn small" :disabled="upgrading[a.name]" :title="t('view.agents.upgrade')" @click="upgradeAgent(a)"><AppIcon name="arrow-up" :size="13" /></button>
            <button class="act-btn small" :title="t('view.agents.memory')" @click="showMemory(a)"><AppIcon name="brain" :size="13" /></button>
            <button class="act-btn small" :disabled="evolving[a.name]" :title="t('view.agents.evolve')" @click="evolveAgent(a)"><AppIcon name="sparkles" :size="13" /></button>
            <button class="act-btn small danger" :disabled="!isAdmin" :title="t('view.agents.remove')" @click="confirmRemove(a)"><AppIcon name="trash" :size="13" /></button>
          </span>
        </div>
        <EmptyState v-if="!agents.length" icon="bot" :title="t('view.agents.noAgentsFound')" :hint="t('view.agents.noAgentsFoundHint')" />
      </div>
    </Card>

    <Card v-if="selectedAgent" :title="selectedAgent" :margin-bottom="24">
      <template #actions>
        <button class="close-btn" :aria-label="t('common.close')" @click="selectedAgent = null"><AppIcon name="x" :size="14" /></button>
      </template>
      <div class="detail-body">
        <div class="detail-section">
          <h4>{{ t('common.configuration') }}</h4>
          <div class="config-grid">
            <div v-for="(v, k) in agentConfig" :key="k" class="cfg-item">
              <span class="cfg-key">{{ k }}</span>
              <span class="cfg-val">{{ v }}</span>
            </div>
          </div>
          <div v-if="selectedCapabilities.length" class="caps-block">
            <h4>{{ t('common.capabilities') }}</h4>
            <div class="caps">
              <span v-for="c in selectedCapabilities" :key="c" class="cap-chip">{{ c }}</span>
            </div>
          </div>
        </div>
        <div class="detail-section">
          <h4>{{ t('view.agents.runtime') }}</h4>
          <div class="perf-bars">
            <div v-for="(v, i) in perfHistory" :key="i" class="perf-row">
              <span class="perf-label">{{ v.label }}</span>
              <div class="perf-bar"><div class="perf-fill" :style="{ width: v.pct + '%', background: v.color }"></div></div>
              <span class="perf-val">{{ v.value }}</span>
            </div>
          </div>
        </div>
      </div>
    </Card>

    <EmptyState v-if="!loading && !agents.length" icon="bot" :title="t('view.agents.noAgents')" :hint="t('view.agents.noAgentsHint')" />

    <!-- 记忆面板 -->
    <div v-if="memoryPanel.visible" v-modal-a11y class="modal-overlay" @click.self="memoryPanel.visible = false" @modal:escape="memoryPanel.visible = false">
      <div class="memory-panel">
        <div class="memory-panel__header">
          <h3>{{ t('view.agents.memoryFor', { name: memoryPanel.agentName }) }}</h3>
          <div class="memory-panel__toolbar">
            <button class="act-btn small" :title="t('common.refresh')" @click="reloadMemory(memoryPanel.agentName)">
              <AppIcon name="refresh" :size="14" />
            </button>
            <button class="act-btn small" :title="t('view.agents.addMemory')" @click="memoryAddForm = !memoryAddForm">
              <AppIcon name="plus" :size="14" />
            </button>
            <button class="close-btn" @click="memoryPanel.visible = false"><AppIcon name="x" :size="16" /></button>
          </div>
        </div>

        <!-- 添加记忆表单 -->
        <div v-if="memoryAddForm" class="memory-add-form">
          <select v-model="memoryAddType" class="mem-add-select" :aria-label="t('view.agents.memoryType')">
            <option value="interaction">interaction</option>
            <option value="preference">preference</option>
            <option value="error_pattern">error_pattern</option>
            <option value="performance">performance</option>
            <option value="lesson">lesson</option>
          </select>
          <textarea
v-model="memoryAddContent" class="mem-add-textarea" rows="3"
                    :aria-label="t('view.agents.memoryContentPlaceholder')"
                    :placeholder="t('view.agents.memoryContentPlaceholder')"></textarea>
          <div class="mem-add-row">
            <label class="mem-add-importance">
              {{ t('view.agents.importance') }}: {{ memoryAddImportance }}
              <input v-model.number="memoryAddImportance" type="range" min="0" max="1" step="0.1" :aria-label="t('view.agents.importance')" />
            </label>
            <button class="act-btn" :disabled="!memoryAddContent.trim()" @click="addMemory(memoryPanel.agentName)">
              {{ t('view.agents.addMemory') }}
            </button>
          </div>
        </div>

        <div v-if="memoryPanel.summary" class="memory-panel__summary">
          <div v-for="(val, key) in memoryPanel.summary.by_type" :key="key" class="mem-stat">
            <span class="mem-stat__label">{{ key }}</span>
            <span class="mem-stat__val">{{ val }}</span>
          </div>
          <div class="mem-stat">
            <span class="mem-stat__label">{{ t('view.agents.totalMemories') }}</span>
            <span class="mem-stat__val">{{ memoryPanel.summary.total_memories }}</span>
          </div>
          <div class="mem-stat">
            <span class="mem-stat__label">{{ t('view.agents.evolutionCount') }}</span>
            <span class="mem-stat__val">{{ memoryPanel.summary.evolution_count }}</span>
          </div>
        </div>
        <div v-if="memoryPanel.records.length" class="memory-panel__list">
          <div v-for="r in memoryPanel.records" :key="r.id" class="mem-record">
            <span class="mem-type" :class="r.memory_type">{{ r.memory_type }}</span>
            <span class="mem-content">{{ JSON.stringify(r.content).slice(0, 200) }}</span>
            <span class="mem-time">{{ r.created_at?.slice(0, 19) }}</span>
          </div>
        </div>
        <div v-else class="memory-panel__empty">
          <AppIcon name="brain" :size="24" />
          <span>{{ t('view.agents.noMemories') }}</span>
        </div>
        <div class="memory-panel__actions">
          <button class="act-btn danger" :disabled="!isAdmin" @click="clearMemory(memoryPanel.agentName)">
            <AppIcon name="trash" :size="14" /> {{ t('view.agents.clearMemory') }}
          </button>
        </div>
      </div>
    </div>

    <!-- 自进化结果面板 -->
    <div v-if="evolutionPanel.visible" v-modal-a11y class="modal-overlay" @click.self="evolutionPanel.visible = false" @modal:escape="evolutionPanel.visible = false">
      <div class="evolution-panel">
        <div class="evolution-panel__header">
          <h3>{{ t('view.agents.evolutionFor', { name: evolutionPanel.agentName }) }}</h3>
          <button class="close-btn" @click="evolutionPanel.visible = false"><AppIcon name="x" :size="16" /></button>
        </div>
        <p v-if="evolutionPanel.result" class="evolution-summary">{{ evolutionPanel.result.summary }}</p>
        <div v-if="evolutionPanel.result?.suggestions?.length" class="evolution-suggestions">
          <div
v-for="(s, i) in evolutionPanel.result.suggestions" :key="i" class="evo-suggestion"
               :class="['prio-' + s.priority, s.action === 'auto_applied' ? 'auto' : 'manual']">
            <span class="evo-cat">{{ s.category }}</span>
            <span class="evo-prio">{{ s.priority }}</span>
            <p class="evo-desc">{{ s.description }}</p>
            <span v-if="s.action === 'auto_applied'" class="evo-action">{{ t('view.agents.autoApplied') }}</span>
          </div>
        </div>
        <div v-if="evolutionPanel.result?.auto_applied?.length" class="evolution-applied">
          <h4>{{ t('view.agents.autoAppliedChanges') }}</h4>
          <div v-for="(a, i) in evolutionPanel.result.auto_applied" :key="i" class="applied-item">
            <span class="applied-cat">{{ a.category }}</span>
            <span class="applied-desc">{{ a.description }}</span>
          </div>
        </div>
      </div>
    </div>

    <!-- 移除确认对话框 -->
    <div v-if="removeConfirm.visible" v-modal-a11y class="modal-overlay" @click.self="removeConfirm.visible = false" @modal:escape="removeConfirm.visible = false">
      <div class="confirm-dialog">
        <div class="confirm-dialog__icon"><AppIcon name="alert-triangle" :size="28" /></div>
        <h3>{{ t('view.agents.confirmRemove', { name: removeConfirm.agentName }) }}</h3>
        <p class="confirm-warning">{{ t('view.agents.removeWarning') }}</p>
        <div class="confirm-dialog__actions">
          <button class="act-btn" @click="removeConfirm.visible = false">{{ t('common.cancel') }}</button>
          <button class="act-btn danger" :disabled="removing" @click="executeRemove">
            {{ removing ? t('common.loading') : t('view.agents.confirmRemoveBtn') }}
          </button>
        </div>
      </div>
    </div>

    <!-- 模型切换对话框 -->
    <div v-if="modelSwitchPanel.visible" v-modal-a11y class="modal-overlay" @click.self="modelSwitchPanel.visible = false" @modal:escape="modelSwitchPanel.visible = false">
      <div class="model-switch-panel">
        <div class="model-switch-panel__header">
          <h3>{{ t('view.agents.switchModelFor', { name: modelSwitchPanel.agentName }) }}</h3>
          <button class="close-btn" @click="modelSwitchPanel.visible = false"><AppIcon name="x" :size="16" /></button>
        </div>
        <div v-if="modelSwitchPanel.currentModel" class="model-switch-panel__current">
          <span class="ms-label">{{ t('view.agents.currentModel') }}:</span>
          <span class="ms-value mono">{{ modelSwitchPanel.currentModel }}</span>
        </div>
        <div v-if="modelSwitchPanel.loading" class="model-switch-panel__list">
          <Skeleton height="36px" radius="6px" />
          <Skeleton height="36px" radius="6px" />
          <Skeleton height="36px" radius="6px" />
        </div>
        <div v-else-if="modelSwitchPanel.models.length" class="model-switch-panel__list">
          <label
v-for="m in modelSwitchPanel.models" :key="m.name" class="model-option"
                 :class="{ selected: modelSwitchPanel.selectedModel === m.name, disabled: !m.enabled }">
            <input v-model="modelSwitchPanel.selectedModel" type="radio" :value="m.name" :disabled="!m.enabled" :aria-label="t('view.agents.switchModelTo', { name: m.name })" />
            <span class="model-name mono">{{ m.name }}</span>
            <span class="model-provider">{{ m.provider }}</span>
            <span v-if="!m.enabled" class="model-status">{{ t('common.disabled') }}</span>
          </label>
        </div>
        <EmptyState v-else icon="bot" :title="t('view.agents.noModelsAvailable')" />
        <div class="model-switch-panel__actions">
          <button class="act-btn" @click="modelSwitchPanel.visible = false">{{ t('common.cancel') }}</button>
          <button class="act-btn" :disabled="!modelSwitchPanel.selectedModel || modelSwitchPanel.selectedModel === modelSwitchPanel.currentModel" @click="executeModelSwitch">
            {{ t('view.agents.confirmSwitch') }}
          </button>
        </div>
      </div>
    </div>

    <!-- 升级确认弹窗 -->
    <div v-if="upgradePanel.visible" v-modal-a11y class="modal-overlay" @click.self="upgradePanel.visible = false" @modal:escape="upgradePanel.visible = false">
      <div class="upgrade-panel">
        <div class="upgrade-panel__header">
          <h3>{{ t('view.agents.upgradeFor', { name: upgradePanel.agentName }) }}</h3>
          <button class="close-btn" @click="upgradePanel.visible = false"><AppIcon name="x" :size="16" /></button>
        </div>

        <!-- 检查中 -->
        <div v-if="upgradePanel.checking" class="upgrade-checking">
          <Skeleton height="24px" radius="4px" />
          <Skeleton height="24px" radius="4px" />
          <Skeleton height="24px" radius="4px" />
        </div>

        <!-- 检查结果 -->
        <div v-else-if="!upgradePanel.result?.error" class="upgrade-info">
          <div class="upgrade-info-row">
            <span class="ui-label">{{ t('view.agents.currentVersion') }}:</span>
            <span class="ui-value mono">{{ upgradePanel.currentVersion || '—' }}</span>
          </div>
          <div class="upgrade-info-row">
            <span class="ui-label">{{ t('view.agents.latestVersion') }}:</span>
            <span class="ui-value mono">{{ upgradePanel.latestVersion || '—' }}</span>
          </div>
          <div class="upgrade-info-row">
            <span class="ui-label">{{ t('view.agents.installMethod') }}:</span>
            <span class="ui-value">{{ upgradePanel.installMethod }}</span>
          </div>

          <!-- 版本对比结果 -->
          <div v-if="upgradePanel.installMethod === 'binary'" class="upgrade-notice warn">
            <AppIcon name="alert-triangle" :size="14" />
            <span>{{ t('view.agents.binaryNoAutoUpgrade') }}</span>
          </div>
          <div v-else-if="!upgradePanel.updateAvailable && upgradePanel.latestVersion !== 'unknown'" class="upgrade-notice ok">
            <AppIcon name="check-circle" :size="14" />
            <span>{{ t('view.agents.alreadyLatest') }}</span>
          </div>
          <div v-else-if="upgradePanel.updateAvailable" class="upgrade-notice info">
            <AppIcon name="arrow-up" :size="14" />
            <span>{{ t('view.agents.updateAvailableHint') }}</span>
          </div>
          <div v-else class="upgrade-notice warn">
            <AppIcon name="alert-triangle" :size="14" />
            <span>{{ t('view.agents.versionCheckFailed') }}</span>
          </div>

          <!-- 升级结果 -->
          <div v-if="upgradePanel.result" class="upgrade-result" :class="upgradePanel.result.upgrade_status">
            <span class="ur-status">{{ upgradePanel.result.upgrade_status }}</span>
            <span v-if="upgradePanel.result.output" class="ur-output">{{ upgradePanel.result.output.slice(0, 200) }}</span>
            <span v-if="upgradePanel.result.error" class="ur-error">{{ upgradePanel.result.error }}</span>
          </div>
        </div>

        <!-- 错误 -->
        <div v-else class="upgrade-error">
          <AppIcon name="alert-triangle" :size="20" />
          <span>{{ upgradePanel.result.error }}</span>
        </div>

        <!-- 操作按钮 -->
        <div v-if="!upgradePanel.checking && !upgradePanel.result?.error" class="upgrade-panel__actions">
          <button class="act-btn" @click="upgradePanel.visible = false">{{ t('common.cancel') }}</button>
          <button
            v-if="upgradePanel.installMethod !== 'binary' && upgradePanel.updateAvailable"
            class="act-btn"
            :disabled="upgradePanel.upgrading"
            @click="executeUpgrade"
          >
            {{ upgradePanel.upgrading ? t('common.loading') : t('view.agents.confirmUpgrade') }}
          </button>
          <button
            v-else-if="upgradePanel.installMethod !== 'binary' && !upgradePanel.updateAvailable && upgradePanel.latestVersion !== 'unknown'"
            class="act-btn"
            :disabled="upgradePanel.upgrading"
            @click="executeUpgrade"
          >
            {{ upgradePanel.upgrading ? t('common.loading') : t('view.agents.forceUpgrade') }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted, watch } from 'vue';
import { useApiStore } from '../stores/api.js';
import { useRealtimeStore } from '../stores/realtime.js';
import { useToast } from '../composables/useToast.js';
import { useI18n } from '../i18n';
import { Card, Badge, Skeleton, EmptyState, Segmented, AppIcon, PageHeader } from '../components/index.js';

const api = useApiStore();
const realtime = useRealtimeStore();
const toast = useToast();
const { t } = useI18n();

const agents = ref([]);
const routes = ref([]);
const viewMode = ref('grid');
const selectedAgent = ref(null);
const agentConfig = ref({});
const selectedCapabilities = ref([]);
const perfHistory = ref([]);
const loading = ref(true);
const refreshing = ref(false);
const scanned = ref([]);
const scanning = ref(false);
const isAdmin = ref(false);

// 新功能状态
const repairing = reactive({});      // { agentName: bool }
const upgrading = reactive({});      // { agentName: bool }
const addingAgent = reactive({});    // { agentName: bool }
const evolving = reactive({});       // { agentName: bool }
const removing = ref(false);
const memoryPanel = reactive({ visible: false, agentName: '', records: [], summary: null });
const memoryAddForm = ref(false);
const memoryAddType = ref('interaction');
const memoryAddContent = ref('');
const memoryAddImportance = ref(0.5);
const evolutionPanel = reactive({ visible: false, agentName: '', result: null });
const removeConfirm = reactive({ visible: false, agentName: '' });
const modelSwitchPanel = reactive({ visible: false, agentName: '', currentModel: '', models: [], selectedModel: '', loading: false });
const decisions = ref([]);
const loadingDecisions = ref(false);
const upgradePanel = reactive({ visible: false, agentName: '', checking: false, currentVersion: '', latestVersion: '', installMethod: '', updateAvailable: false, releaseNotes: '', upgrading: false, result: null });

// /api/agents (and /api/agents/routes) return a dict whose value is a LIST of
// agent objects — not a list and not { agents: [...] }. Normalize accordingly.
function toList(data) {
  if (Array.isArray(data)) return data;
  if (data && typeof data === 'object') {
    const arr = Object.values(data).find((v) => Array.isArray(v));
    return arr || [];
  }
  return [];
}

function agentStatus(a) {
  if (!a || a.enabled === false) return 'disabled';
  if (a.health === 'healthy') return 'active';
  if (a.health === 'unhealthy') return 'error';
  return 'idle';
}

function statusTone(status) {
  if (status === 'active' || status === 'healthy') return 'success';
  if (status === 'error' || status === 'unhealthy') return 'fail';
  if (status === 'busy') return 'warn';
  return 'neutral';
}

function agentColor(name) {
  // Theme-aware palette — reference chart tokens so colors follow dark/light.
  const colors = [
    'var(--chart-1)', 'var(--chart-5)', 'var(--chart-3)', 'var(--chart-4)',
    'var(--chart-fail)', 'var(--chart-8)',
  ];
  const s = name || '';
  let hash = 0;
  for (let i = 0; i < s.length; i++) hash = s.charCodeAt(i) + ((hash << 5) - hash);
  return colors[Math.abs(hash) % colors.length];
}

function selectAgent(a) {
  selectedAgent.value = a.name;
  agentConfig.value = {
    model: a.model || 'auto',
    provider: a.provider || '—',
    driver: a.driver || '—',
    version: a.version || '—',
    enabled: a.enabled === false ? 'false' : 'true',
    health: a.health || 'unknown',
    timeout_s: a.timeout_s ?? '—',
    registered_at: a.registered_at || '—',
    last_health_check: a.last_health_check || '—',
  };
  selectedCapabilities.value = a.capabilities || [];
  // Real runtime signals only — no synthetic fillers.
  perfHistory.value = [
    { label: 'Last Latency', pct: Math.min(100, (a.last_latency_ms || 0) / 50), value: (a.last_latency_ms || 0) + ' ms', color: 'var(--chart-1)' },
    { label: 'Consecutive Failures', pct: Math.min(100, (a.consecutive_failures || 0) * 10), value: String(a.consecutive_failures || 0), color: 'var(--chart-fail)' },
  ];
}

async function switchModel(a) {
  modelSwitchPanel.agentName = a.name;
  modelSwitchPanel.currentModel = a.model || '';
  modelSwitchPanel.selectedModel = a.model || '';
  modelSwitchPanel.models = [];
  modelSwitchPanel.visible = true;
  modelSwitchPanel.loading = true;
  try {
    const data = await api.get('/api/model/list');
    const all = data?.models || [];
    modelSwitchPanel.models = all.map(m => ({
      name: m.name,
      provider: m.provider,
      label: `${m.name} (${m.provider})`,
      enabled: m.enabled,
    }));
  } catch (e) {
    toast.error(t('view.agents.switchModelLoadFailed') + (e.message ? ': ' + e.message : ''));
  } finally {
    modelSwitchPanel.loading = false;
  }
}

async function executeModelSwitch() {
  const { agentName, selectedModel } = modelSwitchPanel;
  if (!selectedModel) return;
  try {
    const data = await api.post('/api/model/switch', { agent: agentName, model: selectedModel });
    if (data?.status === 'ok') {
      toast.success(t('view.agents.switchSuccess', { name: agentName, model: selectedModel }));
      modelSwitchPanel.visible = false;
      await loadAgents();
    } else {
      toast.error(t('view.agents.switchFailed', { name: agentName }) + (data?.error ? ': ' + data.error : ''));
    }
  } catch (e) {
    toast.error(t('view.agents.switchFailed', { name: agentName }) + (e.message ? ': ' + e.message : ''));
  }
}
async function healthCheck(a) {
  try { await api.post(`/api/agents/${a.name}/health-check`, {}); await loadAgents(); toast.success(t('view.agents.healthCheckSent', { name: a.name })); }
  catch (e) { toast.error(t('view.agents.healthCheckFailed') + (e.message ? ': ' + e.message : '')); }
}
// eslint-disable-next-line no-unused-vars
async function restartAgent(a) {
  // 真正的重启 = 修复 + 健康检查
  repairing[a.name] = true;
  try {
    await api.post(`/api/agents/${a.name}/repair`, {});
    await api.post(`/api/agents/${a.name}/health-check`, {});
    await loadAgents();
    toast.success(t('view.agents.restarted', { name: a.name }));
  } catch (e) {
    toast.error(t('view.agents.restartFailed') + (e.message ? ': ' + e.message : ''));
  } finally {
    repairing[a.name] = false;
  }
}

// ── 修复 ──
async function repairAgent(a) {
  repairing[a.name] = true;
  try {
    // 先诊断
    const diag = await api.get(`/api/agents/${a.name}/diagnose`);
    const status = diag?.diagnosis?.overall_status || 'unknown';
    if (status === 'healthy') {
      toast.info(t('view.agents.alreadyHealthy', { name: a.name }));
      return;
    }
    // 执行修复
    const data = await api.post(`/api/agents/${a.name}/repair`, {});
    const result = data?.result;
    if (result?.success) {
      const actions = (result.actions_taken || []).join('; ');
      toast.success(t('view.agents.repairSuccess', { name: a.name }) + (actions ? ': ' + actions : ''));
    } else {
      const errs = (result?.errors || []).join('; ');
      toast.error(t('view.agents.repairFailed', { name: a.name }) + (errs ? ': ' + errs : ''));
    }
    await loadAgents();
  } catch (e) {
    toast.error(t('view.agents.repairFailed', { name: a.name }) + (e.message ? ': ' + e.message : ''));
  } finally {
    repairing[a.name] = false;
  }
}

// ── 升级 ──
async function upgradeAgent(a) {
  // 打开升级面板，先检查版本
  upgradePanel.agentName = a.name;
  upgradePanel.visible = true;
  upgradePanel.checking = true;
  upgradePanel.currentVersion = '';
  upgradePanel.latestVersion = '';
  upgradePanel.installMethod = '';
  upgradePanel.updateAvailable = false;
  upgradePanel.releaseNotes = '';
  upgradePanel.result = null;
  upgradePanel.upgrading = false;

  try {
    const data = await api.get(`/api/agents/${a.name}/upgrade/check`);
    if (data?.status === 'error') {
      upgradePanel.result = { error: data.error };
      toast.error(t('view.agents.upgradeFailed', { name: a.name }) + ': ' + data.error);
      return;
    }
    upgradePanel.currentVersion = data?.current_version || 'unknown';
    upgradePanel.latestVersion = data?.latest_version || 'unknown';
    upgradePanel.installMethod = data?.install_method || 'unknown';
    upgradePanel.updateAvailable = data?.update_available || false;
    upgradePanel.releaseNotes = data?.release_notes || '';
  } catch (e) {
    upgradePanel.result = { error: e.message || 'check failed' };
    toast.error(t('view.agents.upgradeFailed', { name: a.name }) + (e.message ? ': ' + e.message : ''));
  } finally {
    upgradePanel.checking = false;
  }
}

async function executeUpgrade() {
  const { agentName } = upgradePanel;
  upgradePanel.upgrading = true;
  upgrading[agentName] = true;
  upgradePanel.result = null;
  try {
    const data = await api.post(`/api/agents/${agentName}/upgrade`, {});
    const info = data?.info;
    upgradePanel.result = info;
    if (info?.upgrade_status === 'success') {
      toast.success(t('view.agents.upgradeSuccess', { name: agentName }));
      upgradePanel.visible = false;
      await loadAgents();
    } else if (info?.upgrade_status === 'not_supported') {
      toast.info(t('view.agents.upgradeNotSupported', { name: agentName, method: info.install_method || '' }));
    } else {
      const err = info?.error || info?.output || 'unknown error';
      toast.error(t('view.agents.upgradeFailed', { name: agentName }) + ': ' + err);
    }
  } catch (e) {
    upgradePanel.result = { error: e.message || 'upgrade failed' };
    toast.error(t('view.agents.upgradeFailed', { name: agentName }) + (e.message ? ': ' + e.message : ''));
  } finally {
    upgradePanel.upgrading = false;
    upgrading[agentName] = false;
  }
}

// ── 记忆 ──
async function showMemory(a) {
  memoryPanel.agentName = a.name;
  memoryPanel.visible = true;
  memoryPanel.records = [];
  memoryPanel.summary = null;
  memoryAddForm.value = false;
  memoryAddContent.value = '';
  await reloadMemory(a.name);
}

async function reloadMemory(agentName) {
  try {
    const [memData, sumData] = await Promise.all([
      api.get(`/api/agents/${agentName}/memory?limit=50`),
      api.get(`/api/agents/${agentName}/memory/summary`),
    ]);
    memoryPanel.records = memData?.memories || [];
    memoryPanel.summary = sumData?.summary || null;
  } catch (e) {
    toast.error(t('view.agents.loadMemoryFailed') + (e.message ? ': ' + e.message : ''));
  }
}

async function addMemory(agentName) {
  if (!memoryAddContent.value.trim()) return;
  let content;
  try {
    content = JSON.parse(memoryAddContent.value);
  } catch {
    content = { text: memoryAddContent.value.trim() };
  }
  try {
    await api.post(`/api/agents/${agentName}/memory`, {
      memory_type: memoryAddType.value,
      content,
      importance: memoryAddImportance.value,
    });
    toast.success(t('view.agents.memoryAdded'));
    memoryAddContent.value = '';
    memoryAddForm.value = false;
    await reloadMemory(agentName);
  } catch (e) {
    toast.error(t('view.agents.addMemoryFailed') + (e.message ? ': ' + e.message : ''));
  }
}

async function clearMemory(agentName) {
  try {
    await api.delete(`/api/agents/${agentName}/memory`);
    memoryPanel.records = [];
    memoryPanel.summary = null;
    toast.success(t('view.agents.memoryCleared', { name: agentName }));
  } catch (e) {
    toast.error(t('view.agents.clearMemoryFailed') + (e.message ? ': ' + e.message : ''));
  }
}

// ── 自进化 ──
async function evolveAgent(a) {
  evolving[a.name] = true;
  try {
    const data = await api.post(`/api/agents/${a.name}/evolve`, {});
    const result = data?.result;
    evolutionPanel.agentName = a.name;
    evolutionPanel.result = result;
    evolutionPanel.visible = true;
    if (result?.auto_applied?.length) {
      toast.success(t('view.agents.evolveSuccess', { name: a.name, count: result.auto_applied.length }));
    } else if (result?.suggestions?.length) {
      toast.info(t('view.agents.evolveSuggestions', { name: a.name, count: result.suggestions.length }));
    } else {
      toast.info(t('view.agents.evolveNoData', { name: a.name }));
    }
  } catch (e) {
    toast.error(t('view.agents.evolveFailed', { name: a.name }) + (e.message ? ': ' + e.message : ''));
  } finally {
    evolving[a.name] = false;
  }
}

// ── 剔除 ──
function confirmRemove(a) {
  removeConfirm.agentName = a.name;
  removeConfirm.visible = true;
}

async function executeRemove() {
  removing.value = true;
  try {
    const data = await api.delete(`/api/agents/${removeConfirm.agentName}`);
    if (data?.deleted) {
      toast.success(t('view.agents.removed', { name: removeConfirm.agentName }));
      removeConfirm.visible = false;
      await loadAgents();
    } else {
      const errs = (data?.errors || []).join('; ');
      toast.error(t('view.agents.removeFailed', { name: removeConfirm.agentName }) + (errs ? ': ' + errs : ''));
    }
  } catch (e) {
    toast.error(t('view.agents.removeFailed', { name: removeConfirm.agentName }) + (e.message ? ': ' + e.message : ''));
  } finally {
    removing.value = false;
  }
}

// Local agent scan — hits POST /api/agents/scan which probes the environment
// for known agent CLIs (claude/codex/gemini/…) and syncs them into the registry.
// This surfaces the "本地 agent 扫描，纳入 MAOP" capability directly in the UI.
async function scanLocal() {
  scanning.value = true;
  try {
    const data = await api.post('/api/agents/scan', {});
    scanned.value = Array.isArray(data.agents) ? data.agents : [];
    const cnt = data.scanned ?? scanned.value.length;
    const syn = data.synced ?? 0;
    toast.success(`${t('view.agents.scanned')} ${cnt} · ${t('view.agents.synced')} ${syn}`);
    loadAgents();
  } catch (e) {
    toast.error(t('view.agents.scanFailed') + (e.message ? ': ' + e.message : ''));
  } finally {
    scanning.value = false;
  }
}

async function addToMaop(s) {
  addingAgent[s.name] = true;
  try {
    await api.post('/api/agents/register', {
      name: s.name,
      cli_path: s.cli_path || s.cli || '',
      capabilities: s.capabilities || [],
      provider: s.provider || '',
      description: s.description || `${s.name} agent (added from scan)`,
      model: 'auto',
      driver: 'cli',
      cli_args: '--task "{task}"',
      timeout_s: 120,
    });
    toast.success(t('view.agents.addedToMaop', { name: s.name }));
    await loadAgents();
  } catch (e) {
    toast.error(t('view.agents.addToMaopFailed', { name: s.name }) + (e.message ? ': ' + e.message : ''));
  } finally {
    addingAgent[s.name] = false;
  }
}

async function loadAgents() {
  refreshing.value = true;
  let ok = true;
  try {
    const data = await api.get('/api/agents');
    agents.value = toList(data);
  } catch (e) {
    ok = false;
    toast.error(t('view.agents.loadFailed') + (e.message ? ': ' + e.message : ''));
  }
  try {
    const r = await api.get('/api/agents/routes');
    routes.value = toList(r);
  } catch {
    routes.value = [];
  }
  loading.value = false;
  refreshing.value = false;
  if (!ok && !agents.value.length) { /* error already surfaced via toast + empty state */ }
}

async function loadDecisions() {
  loadingDecisions.value = true;
  try {
    const data = await api.get('/api/routing/decisions/recent?limit=20');
    decisions.value = data?.decisions || [];
  } catch {
    decisions.value = [];
  } finally {
    loadingDecisions.value = false;
  }
}

function formatDecTime(ts) {
  if (!ts) return '';
  const d = new Date(ts * 1000);
  const hh = String(d.getHours()).padStart(2, '0');
  const mm = String(d.getMinutes()).padStart(2, '0');
  const ss = String(d.getSeconds()).padStart(2, '0');
  return `${hh}:${mm}:${ss}`;
}

watch(
  () => realtime.snapshot,
  (snap) => {
    if (!snap) return;
    const hasAgents = Array.isArray(snap.agents) || (typeof snap.type === 'string' && snap.type.toLowerCase().includes('agent'));
    if (hasAgents) loadAgents();
  }
);

async function detectAdmin() {
  try {
    const rolesStr = localStorage.getItem('maop_roles');
    if (rolesStr) {
      const roles = JSON.parse(rolesStr);
      if (Array.isArray(roles) && roles.some((r) => r === 'admin' || r === 'superadmin')) return true;
    }
  } catch { /* ignore */ }
  try {
    const d = await api.get('/api/auth/status');
    if (d && d.auth_enabled === false) return true;
  } catch { /* ignore */ }
  try { return localStorage.getItem('maop_user') === 'admin'; } catch { return false; }
}

onMounted(() => {
  loadAgents();
  loadDecisions();
  detectAdmin().then((v) => (isAdmin.value = v));
});
</script>

<style scoped>
/* ── 操作按钮变体 ── */
.act-btn.danger {
  color: var(--fail);
  border-color: color-mix(in srgb, var(--fail) 30%, var(--border));
}
.act-btn.danger:hover:not(:disabled) {
  background: color-mix(in srgb, var(--fail) 10%, var(--surface-3));
  border-color: var(--fail);
}

/* ── 模态覆盖层 ── */
.modal-overlay {
  position: fixed; inset: 0; z-index: var(--z-modal);
  display: flex; align-items: center; justify-content: center;
  background: rgba(15, 23, 42, .65);
  backdrop-filter: blur(8px);
  animation: maop-view-in .2s ease both;
}

/* ── 记忆面板 ── */
.memory-panel {
  background: var(--card-sheen), var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r-xl);
  padding: var(--sp-6);
  max-width: 700px; width: calc(100% - 32px);
  max-height: 80vh; overflow-y: auto;
  box-shadow: var(--shadow-lg);
}
.memory-panel__header {
  display: flex; align-items: center; justify-content: space-between;
  margin-bottom: var(--sp-4);
}
.memory-panel__header h3 { font-size: var(--fs-lg); font-weight: 700; }
.memory-panel__summary {
  display: flex; flex-wrap: wrap; gap: var(--sp-3);
  padding: var(--sp-3); background: var(--surface-2);
  border-radius: var(--r-md); margin-bottom: var(--sp-4);
}
.mem-stat { display: flex; flex-direction: column; align-items: center; min-width: 80px; }
.mem-stat__label { font-size: var(--fs-xs); color: var(--text-faint); text-transform: uppercase; letter-spacing: .04em; }
.mem-stat__val { font-size: var(--fs-xl); font-weight: 700; color: var(--brand-strong); }
.memory-panel__list { display: flex; flex-direction: column; gap: var(--sp-2); }
.mem-record {
  display: flex; align-items: center; gap: var(--sp-2);
  padding: var(--sp-2) var(--sp-3);
  background: var(--surface-2); border-radius: var(--r-sm);
  border-left: 3px solid var(--border);
}
.mem-type {
  font-size: 10px; font-weight: 700; text-transform: uppercase;
  padding: 2px 6px; border-radius: var(--r-sm);
  background: var(--surface-3); color: var(--text-muted);
  flex-shrink: 0;
}
.mem-type.interaction { background: color-mix(in srgb, var(--chart-1) 15%, transparent); color: var(--chart-1); }
.mem-type.error_pattern { background: color-mix(in srgb, var(--fail) 15%, transparent); color: var(--fail); }
.mem-type.lesson { background: color-mix(in srgb, var(--warn) 15%, transparent); color: var(--warn); }
.mem-type.performance { background: color-mix(in srgb, var(--chart-4) 15%, transparent); color: var(--chart-4); }
.mem-content { flex: 1; font-size: var(--fs-xs); color: var(--text-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.mem-time { font-size: 10px; color: var(--text-faint); flex-shrink: 0; }
.memory-panel__actions { margin-top: var(--sp-4); display: flex; justify-content: flex-end; }

/* ── 自进化面板 ── */
.evolution-panel {
  background: var(--card-sheen), var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r-xl);
  padding: var(--sp-6);
  max-width: 700px; width: calc(100% - 32px);
  max-height: 80vh; overflow-y: auto;
  box-shadow: var(--shadow-lg);
}
.evolution-panel__header {
  display: flex; align-items: center; justify-content: space-between;
  margin-bottom: var(--sp-4);
}
.evolution-panel__header h3 { font-size: var(--fs-lg); font-weight: 700; }
.evolution-summary { color: var(--text-muted); font-size: var(--fs-sm); margin-bottom: var(--sp-4); }
.evolution-suggestions { display: flex; flex-direction: column; gap: var(--sp-2); }
.evo-suggestion {
  padding: var(--sp-3); border-radius: var(--r-md);
  border-left: 3px solid var(--border);
  background: var(--surface-2);
}
.evo-suggestion.prio-high { border-left-color: var(--fail); }
.evo-suggestion.prio-medium { border-left-color: var(--warn); }
.evo-suggestion.prio-low { border-left-color: var(--chart-4); }
.evo-suggestion.auto { background: color-mix(in srgb, var(--success) 8%, var(--surface-2)); }
.evo-cat { font-size: 10px; font-weight: 700; text-transform: uppercase; color: var(--brand-strong); margin-right: var(--sp-2); }
.evo-prio { font-size: 10px; font-weight: 700; text-transform: uppercase; padding: 1px 5px; border-radius: var(--r-sm); }
.prio-high .evo-prio { background: color-mix(in srgb, var(--fail) 15%, transparent); color: var(--fail); }
.prio-medium .evo-prio { background: color-mix(in srgb, var(--warn) 15%, transparent); color: var(--warn); }
.prio-low .evo-prio { background: color-mix(in srgb, var(--chart-4) 15%, transparent); color: var(--chart-4); }
.evo-desc { font-size: var(--fs-sm); color: var(--text); margin: var(--sp-1) 0 0; line-height: 1.5; }
.evo-action { font-size: var(--fs-xs); color: var(--success); font-weight: 600; }
.evolution-applied { margin-top: var(--sp-4); }
.evolution-applied h4 { font-size: var(--fs-sm); font-weight: 700; margin-bottom: var(--sp-2); }
.applied-item {
  display: flex; align-items: center; gap: var(--sp-2);
  padding: var(--sp-2); background: color-mix(in srgb, var(--success) 5%, var(--surface-2));
  border-radius: var(--r-sm); margin-bottom: 4px;
}
.applied-cat { font-size: 10px; font-weight: 700; color: var(--success); text-transform: uppercase; }
.applied-desc { font-size: var(--fs-xs); color: var(--text-muted); }

/* ── 确认对话框 ── */
.confirm-dialog {
  background: var(--card-sheen), var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r-xl);
  padding: var(--sp-6);
  max-width: 420px; width: calc(100% - 32px);
  text-align: center;
  box-shadow: var(--shadow-lg);
}
.confirm-dialog__icon {
  display: grid; place-items: center;
  width: 56px; height: 56px; margin: 0 auto var(--sp-4);
  border-radius: var(--r-full);
  background: color-mix(in srgb, var(--fail) 12%, var(--surface-2));
  color: var(--fail);
}
.confirm-dialog h3 { font-size: var(--fs-lg); font-weight: 700; margin-bottom: var(--sp-2); }
.confirm-warning { font-size: var(--fs-sm); color: var(--text-muted); margin-bottom: var(--sp-4); line-height: 1.5; }
.confirm-dialog__actions { display: flex; gap: var(--sp-3); justify-content: center; }
.close-btn {
  background: none; border: none; color: var(--text-faint);
  cursor: pointer; padding: 4px; border-radius: var(--r-sm);
  display: grid; place-items: center;
}
.close-btn:hover { color: var(--text); background: var(--surface-2); }

/* ── 调度路由器 ── */
.dispatch-intro {
  display: flex; align-items: flex-start; gap: var(--sp-2);
  padding: var(--sp-2) var(--sp-3);
  margin-bottom: var(--sp-4);
  background: color-mix(in srgb, var(--primary) 6%, var(--surface-2));
  border-radius: var(--r-sm);
  font-size: var(--fs-sm);
  color: var(--text-muted);
  line-height: 1.5;
}
.dispatch-intro svg { flex-shrink: 0; margin-top: 2px; }
.dispatch-section-title {
  font-size: var(--fs-sm);
  font-weight: 600;
  color: var(--text);
  margin: var(--sp-4) 0 var(--sp-3);
  display: flex; align-items: center; gap: var(--sp-2);
}
.dispatch-count { color: var(--text-muted); font-weight: 400; }
.dispatch-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: var(--sp-3);
}
.dispatch-card {
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: var(--r-md);
  padding: var(--sp-3) var(--sp-4);
  display: flex; flex-direction: column; gap: var(--sp-2);
}
.route-cap {
  display: flex; align-items: center; gap: var(--sp-2);
  font-weight: 600; font-size: var(--fs-sm);
  color: var(--text);
}
.route-cap-name { text-transform: uppercase; letter-spacing: .03em; }
.route-chain {
  display: flex; align-items: center; gap: var(--sp-2);
  flex-wrap: wrap;
}
.route-node {
  display: flex; flex-direction: column; gap: 2px;
  padding: var(--sp-1) var(--sp-2);
  border-radius: var(--r-sm);
  background: var(--surface-3);
}
.route-node.primary { border-left: 3px solid var(--ok, #3fb950); }
.route-node.fallback { border-left: 3px solid var(--warn, #d29922); }
.route-node.tertiary { border-left: 3px solid var(--text-faint, #6e7686); }
.route-agent { font-weight: 600; font-size: var(--fs-sm); }
.route-model { font-size: 11px; color: var(--text-muted); }
.route-sep { color: var(--text-faint); font-size: var(--fs-sm); }
.route-keywords {
  display: flex; flex-wrap: wrap; gap: 4px;
  margin-top: 2px;
}
.kw-chip {
  font-size: 11px; padding: 1px 6px;
  border-radius: var(--r-xs);
  background: var(--surface-3);
  color: var(--text-muted);
}
.kw-more { font-size: 11px; color: var(--text-faint); }

/* ── 路由决策记录 ── */
.decisions-list {
  display: flex; flex-direction: column; gap: var(--sp-1);
}
.decision-row {
  display: grid;
  grid-template-columns: 120px 140px 1fr auto;
  align-items: center; gap: var(--sp-2);
  padding: var(--sp-2) var(--sp-3);
  background: var(--surface-2);
  border-radius: var(--r-sm);
  font-size: var(--fs-sm);
}
.dec-stage {
  font-size: 11px; font-weight: 600;
  padding: 2px 8px; border-radius: var(--r-xs);
  text-align: center; text-transform: uppercase;
  letter-spacing: .03em;
}
.dec-stage.route_scorer { background: color-mix(in srgb, var(--brand) 15%, transparent); color: var(--brand); }
.dec-stage.load_balancer { background: color-mix(in srgb, var(--warn) 15%, transparent); color: var(--warn); }
.dec-stage.model_selector { background: color-mix(in srgb, var(--chart-5) 15%, transparent); color: var(--chart-5); }
.dec-stage.dispatcher { background: color-mix(in srgb, var(--success) 15%, transparent); color: var(--success); }
.dec-agent { font-weight: 600; color: var(--text); }
.dec-reason { color: var(--text-muted); font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dec-time { color: var(--text-faint); font-size: 11px; font-variant-numeric: tabular-nums; }
.decisions-empty {
  display: flex; align-items: center; gap: var(--sp-2);
  padding: var(--sp-4); color: var(--text-faint);
  font-size: var(--fs-sm); justify-content: center;
}

/* ── 模型切换面板 ── */
.model-switch-panel {
  background: var(--card-sheen), var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r-xl);
  padding: var(--sp-6);
  max-width: 520px; width: calc(100% - 32px);
  max-height: 80vh; overflow-y: auto;
  box-shadow: var(--shadow-lg);
}
.model-switch-panel__header {
  display: flex; align-items: center; justify-content: space-between;
  margin-bottom: var(--sp-4);
}
.model-switch-panel__header h3 { font-size: var(--fs-lg); font-weight: 700; }
.model-switch-panel__current {
  display: flex; align-items: center; gap: var(--sp-2);
  margin-bottom: var(--sp-3);
  font-size: var(--fs-sm);
}
.ms-label { color: var(--text-muted); }
.ms-value { font-weight: 600; }
.model-switch-panel__list {
  display: flex; flex-direction: column; gap: var(--sp-2);
  margin-bottom: var(--sp-4);
}
.model-option {
  display: flex; align-items: center; gap: var(--sp-2);
  padding: var(--sp-2) var(--sp-3);
  border: 1px solid var(--border);
  border-radius: var(--r-sm);
  cursor: pointer; transition: all .15s ease;
}
.model-option:hover:not(.disabled) {
  border-color: var(--accent, var(--primary));
  background: var(--surface-2);
}
.model-option.selected {
  border-color: var(--accent, var(--primary));
  background: color-mix(in srgb, var(--primary) 8%, var(--surface));
}
.model-option.disabled { opacity: .5; cursor: not-allowed; }
.model-option input[type="radio"] { accent-color: var(--primary); }
.model-name { font-size: var(--fs-sm); font-weight: 500; }
.model-provider { font-size: 11px; color: var(--text-muted); margin-left: auto; }
.model-status { font-size: 11px; color: var(--text-faint); }
.model-switch-panel__actions {
  display: flex; gap: var(--sp-3); justify-content: flex-end;
}

/* ── 升级弹窗 ── */
.upgrade-panel {
  background: var(--card-sheen), var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r-xl);
  padding: var(--sp-6);
  max-width: 480px; width: calc(100% - 32px);
  max-height: 80vh; overflow-y: auto;
  box-shadow: var(--shadow-lg);
}
.upgrade-panel__header {
  display: flex; align-items: center; justify-content: space-between;
  margin-bottom: var(--sp-4);
}
.upgrade-panel__header h3 { font-size: var(--fs-lg); font-weight: 700; }
.upgrade-checking { display: flex; flex-direction: column; gap: var(--sp-2); }
.upgrade-info { display: flex; flex-direction: column; gap: var(--sp-2); }
.upgrade-info-row {
  display: flex; align-items: center; gap: var(--sp-2);
  font-size: var(--fs-sm);
}
.ui-label { color: var(--text-muted); min-width: 100px; }
.ui-value { font-weight: 500; }
.upgrade-notice {
  display: flex; align-items: center; gap: var(--sp-2);
  padding: var(--sp-2) var(--sp-3);
  border-radius: var(--r-sm);
  font-size: var(--fs-sm);
  margin: var(--sp-2) 0;
}
.upgrade-notice.ok { background: color-mix(in srgb, var(--success) 10%, var(--surface)); color: var(--success); }
.upgrade-notice.info { background: color-mix(in srgb, var(--brand) 10%, var(--surface)); color: var(--brand); }
.upgrade-notice.warn { background: color-mix(in srgb, var(--warn) 10%, var(--surface)); color: var(--warn); }
.upgrade-result {
  margin-top: var(--sp-3); padding: var(--sp-2) var(--sp-3);
  border-radius: var(--r-sm); background: var(--surface-2);
  font-size: 12px; display: flex; flex-direction: column; gap: 2px;
}
.upgrade-result.success { border-left: 3px solid var(--success); }
.upgrade-result.failed { border-left: 3px solid var(--fail); }
.upgrade-result.not_supported { border-left: 3px solid var(--warn); }
.ur-status { font-weight: 600; text-transform: uppercase; }
.ur-output, .ur-error { color: var(--text-muted); word-break: break-all; }
.upgrade-error {
  display: flex; align-items: center; gap: var(--sp-2);
  padding: var(--sp-3); color: var(--fail);
}
.upgrade-panel__actions {
  display: flex; gap: var(--sp-3); justify-content: flex-end;
  margin-top: var(--sp-4);
}

/* ── 扫描卡片加入按钮 ── */
.scanned-actions {
  margin-top: var(--sp-2);
  padding-top: var(--sp-2);
  border-top: 1px solid var(--border);
}

/* ── 记忆面板增强 ── */
.memory-panel__toolbar {
  display: flex; align-items: center; gap: var(--sp-1);
}
.memory-add-form {
  display: flex; flex-direction: column; gap: var(--sp-2);
  padding: var(--sp-3);
  background: var(--surface-2);
  border-radius: var(--r-sm);
  margin-bottom: var(--sp-3);
}
.mem-add-select {
  padding: var(--sp-1) var(--sp-2);
  border: 1px solid var(--border);
  border-radius: var(--r-xs);
  background: var(--surface);
  color: var(--text);
  font-size: var(--fs-sm);
}
.mem-add-textarea {
  padding: var(--sp-2);
  border: 1px solid var(--border);
  border-radius: var(--r-xs);
  background: var(--surface);
  color: var(--text);
  font-size: var(--fs-sm);
  resize: vertical;
  font-family: inherit;
}
.mem-add-row {
  display: flex; align-items: center; justify-content: space-between; gap: var(--sp-2);
}
.mem-add-importance {
  display: flex; align-items: center; gap: var(--sp-2);
  font-size: var(--fs-sm); color: var(--text-muted);
}
.mem-add-importance input[type="range"] { width: 120px; }
.memory-panel__empty {
  display: flex; flex-direction: column; align-items: center; gap: var(--sp-2);
  padding: var(--sp-6); color: var(--text-faint);
  font-size: var(--fs-sm);
}
</style>
