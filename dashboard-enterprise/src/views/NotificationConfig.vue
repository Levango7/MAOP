<template>
  <div class="notification-config-page view-enter">
    <PageHeader>
      <template #badges>
        <Badge tone="brand">{{ t('view.notificationConfig.badge') }}</Badge>
      </template>
      <button class="btn" :disabled="loading" :title="t('common.refresh')" @click="loadAll">
        <AppIcon name="refresh" :size="14" :class="{ spinning: loading }" aria-hidden="true" />
      </button>
    </PageHeader>

    <div class="cfg-tabs" role="tablist" :aria-label="t('view.notificationConfig.tabsLabel')">
      <button
        v-for="tab in tabs"
        :key="tab.key"
        type="button"
        class="cfg-tab"
        :class="{ active: activeTab === tab.key }"
        role="tab"
        :aria-selected="activeTab === tab.key"
        @click="activeTab = tab.key"
      >
        {{ t(tab.label) }}
        <span class="cfg-tab-count">{{ tab.count }}</span>
      </button>
    </div>

    <div v-if="error" class="cfg-panel">
      <EmptyState
        icon="alert-triangle"
        tone="fail"
        :title="t('view.notificationConfig.loadError')"
        :description="error"
      >
        <template #actions>
          <button class="btn" @click="loadAll">{{ t('common.retry') }}</button>
        </template>
      </EmptyState>
    </div>
    <div v-else-if="loading" class="cfg-panel">
      <Skeleton :lines="6" block />
    </div>
    <template v-else>
      <!-- ── Channels ─────────────────────────────────────────── -->
      <section v-show="activeTab === 'channels'" class="cfg-panel">
        <header class="cfg-panel__head">
          <p class="cfg-hint">{{ t('view.notificationConfig.channelsHint') }}</p>
          <button class="btn btn--primary" @click="openCreateChannel">
            <AppIcon name="plus" :size="15" aria-hidden="true" />
            {{ t('view.notificationConfig.newChannel') }}
          </button>
        </header>
        <EmptyState
          v-if="!channels.length"
          icon="message-square"
          :title="t('view.notificationConfig.channelsEmpty')"
          :description="t('view.notificationConfig.channelsEmptyDesc')"
        >
          <template #actions>
            <button class="btn btn--primary" @click="openCreateChannel">
              {{ t('view.notificationConfig.newChannel') }}
            </button>
          </template>
        </EmptyState>
        <div v-else class="cfg-table cfg-table--channels" role="table" :aria-label="t('view.notificationConfig.channelsHint')">
          <div class="cfg-row cfg-row--head" role="row">
            <span role="columnheader">{{ t('view.notificationConfig.colName') }}</span>
            <span role="columnheader">{{ t('view.notificationConfig.colType') }}</span>
            <span role="columnheader">{{ t('view.notificationConfig.colTarget') }}</span>
            <span role="columnheader">{{ t('view.notificationConfig.colStatus') }}</span>
            <span role="columnheader" class="cfg-actions-head">{{ t('view.notificationConfig.colActions') }}</span>
          </div>
          <div v-for="ch in channels" :key="ch.id" class="cfg-row" role="row">
            <div role="cell">
              <span class="cfg-name">{{ ch.name }}</span>
              <span v-if="ch.description" class="cfg-desc">{{ ch.description }}</span>
            </div>
            <div role="cell">
              <Badge tone="neutral">{{ channelTypeLabel(ch.type) }}</Badge>
            </div>
            <div role="cell">
              <code class="cfg-mono">{{ configSummary(ch) }}</code>
            </div>
            <div role="cell">
              <Badge :tone="ch.enabled ? 'success' : 'neutral'">
                {{ ch.enabled ? t('view.notificationConfig.statusEnabled') : t('view.notificationConfig.statusDisabled') }}
              </Badge>
            </div>
            <div class="cfg-actions" role="cell">
              <button
                class="btn-icon"
                type="button"
                :title="t('common.edit')"
                :aria-label="t('common.edit')"
                @click="openEditChannel(ch)"
              >
                <AppIcon name="gear" :size="14" aria-hidden="true" />
              </button>
              <button
                class="btn-icon btn-icon--danger"
                type="button"
                :title="t('common.delete')"
                :aria-label="t('common.delete')"
                @click="removeChannel(ch)"
              >
                <AppIcon name="trash" :size="14" aria-hidden="true" />
              </button>
            </div>
          </div>
        </div>
      </section>

      <!-- ── Rules ────────────────────────────────────────────── -->
      <section v-show="activeTab === 'rules'" class="cfg-panel">
        <header class="cfg-panel__head">
          <p class="cfg-hint">{{ t('view.notificationConfig.rulesHint') }}</p>
          <button class="btn btn--primary" @click="openCreateRule">
            <AppIcon name="plus" :size="15" aria-hidden="true" />
            {{ t('view.notificationConfig.newRule') }}
          </button>
        </header>
        <EmptyState
          v-if="!rules.length"
          icon="route"
          :title="t('view.notificationConfig.rulesEmpty')"
          :description="t('view.notificationConfig.rulesEmptyDesc')"
        >
          <template #actions>
            <button class="btn btn--primary" @click="openCreateRule">{{ t('view.notificationConfig.newRule') }}</button>
          </template>
        </EmptyState>
        <div v-else class="cfg-table cfg-table--rules" role="table" :aria-label="t('view.notificationConfig.rulesHint')">
          <div class="cfg-row cfg-row--head" role="row">
            <span role="columnheader">{{ t('view.notificationConfig.colName') }}</span>
            <span role="columnheader">{{ t('view.notificationConfig.colEvent') }}</span>
            <span role="columnheader">{{ t('view.notificationConfig.colChannels') }}</span>
            <span role="columnheader">{{ t('view.notificationConfig.colLevel') }}</span>
            <span role="columnheader">{{ t('view.notificationConfig.colStatus') }}</span>
            <span role="columnheader" class="cfg-actions-head">{{ t('view.notificationConfig.colActions') }}</span>
          </div>
          <div v-for="r in rules" :key="r.id" class="cfg-row" role="row">
            <div role="cell">
              <span class="cfg-name">{{ r.name }}</span>
              <span v-if="r.description" class="cfg-desc">{{ r.description }}</span>
            </div>
            <div role="cell"><code class="cfg-mono">{{ r.event_type }}</code></div>
            <div role="cell" class="cfg-badges">
              <Badge v-for="chId in r.channel_ids" :key="chId" tone="neutral">{{ channelName(chId) }}</Badge>
              <span v-if="!r.channel_ids.length" class="cfg-muted">—</span>
            </div>
            <div role="cell"><Badge :tone="levelTone(r.level)">{{ levelLabel(r.level) }}</Badge></div>
            <div role="cell">
              <Badge :tone="r.enabled ? 'success' : 'neutral'">
                {{ r.enabled ? t('view.notificationConfig.statusEnabled') : t('view.notificationConfig.statusDisabled') }}
              </Badge>
            </div>
            <div class="cfg-actions" role="cell">
              <button class="btn-icon" type="button" :title="t('common.edit')" :aria-label="t('common.edit')" @click="openEditRule(r)">
                <AppIcon name="gear" :size="14" aria-hidden="true" />
              </button>
              <button
                class="btn-icon btn-icon--danger"
                type="button"
                :title="t('common.delete')"
                :aria-label="t('common.delete')"
                @click="removeRule(r)"
              >
                <AppIcon name="trash" :size="14" aria-hidden="true" />
              </button>
            </div>
          </div>
        </div>
      </section>

      <!-- ── Templates ────────────────────────────────────────── -->
      <section v-show="activeTab === 'templates'" class="cfg-panel">
        <header class="cfg-panel__head">
          <p class="cfg-hint">{{ t('view.notificationConfig.templatesHint') }}</p>
          <button class="btn btn--primary" @click="openCreateTemplate">
            <AppIcon name="plus" :size="15" aria-hidden="true" />
            {{ t('view.notificationConfig.newTemplate') }}
          </button>
        </header>
        <EmptyState
          v-if="!templates.length"
          icon="scroll"
          :title="t('view.notificationConfig.templatesEmpty')"
          :description="t('view.notificationConfig.templatesEmptyDesc')"
        >
          <template #actions>
            <button class="btn btn--primary" @click="openCreateTemplate">{{ t('view.notificationConfig.newTemplate') }}</button>
          </template>
        </EmptyState>
        <div v-else class="cfg-table cfg-table--templates" role="table" :aria-label="t('view.notificationConfig.templatesHint')">
          <div class="cfg-row cfg-row--head" role="row">
            <span role="columnheader">{{ t('view.notificationConfig.colName') }}</span>
            <span role="columnheader">{{ t('view.notificationConfig.colSubject') }}</span>
            <span role="columnheader">{{ t('view.notificationConfig.colBody') }}</span>
            <span role="columnheader" class="cfg-actions-head">{{ t('view.notificationConfig.colActions') }}</span>
          </div>
          <div v-for="tpl in templates" :key="tpl.id" class="cfg-row" role="row">
            <div role="cell">
              <span class="cfg-name">{{ tpl.name }}</span>
              <span v-if="tpl.description" class="cfg-desc">{{ tpl.description }}</span>
            </div>
            <div role="cell"><span class="cfg-text">{{ tpl.subject || '—' }}</span></div>
            <div role="cell"><code class="cfg-mono cfg-ellipsis">{{ tpl.body }}</code></div>
            <div class="cfg-actions" role="cell">
              <button
                class="btn-icon btn-icon--danger"
                type="button"
                :title="t('common.delete')"
                :aria-label="t('common.delete')"
                @click="removeTemplate(tpl)"
              >
                <AppIcon name="trash" :size="14" aria-hidden="true" />
              </button>
            </div>
          </div>
        </div>
      </section>
    </template>

    <Teleport to="body">
      <!-- ── Channel editor ─────────────────────────────────── -->
      <div
        v-if="channelOpen"
        v-modal-a11y
        class="modal-overlay"
        @click.self="closeChannel"
        @modal:escape="closeChannel"
      >
        <div class="modal cfg-modal" role="dialog" aria-modal="true" aria-labelledby="cfg-channel-title">
          <button class="modal-close" type="button" :aria-label="t('common.close')" @click="closeChannel">
            <AppIcon name="x" :size="16" aria-hidden="true" />
          </button>
          <h3 id="cfg-channel-title">
            {{ editingChannelId ? t('view.notificationConfig.channelEditTitle') : t('view.notificationConfig.channelCreateTitle') }}
          </h3>
          <div class="form">
            <label class="form-label">
              <span>{{ t('view.notificationConfig.fieldName') }}</span>
              <input v-model="channelForm.name" class="input" type="text" maxlength="100" />
            </label>
            <label class="form-label">
              <span>{{ t('view.notificationConfig.fieldType') }}</span>
              <select v-model="channelForm.type" class="input" :disabled="!!editingChannelId">
                <option v-for="tp in CHANNEL_TYPES" :key="tp" :value="tp">{{ channelTypeLabel(tp) }}</option>
              </select>
            </label>
            <label class="form-label">
              <span>{{ t('view.notificationConfig.fieldDescription') }}</span>
              <input v-model="channelForm.description" class="input" type="text" maxlength="500" />
            </label>
            <label class="form-label form-label--inline">
              <input v-model="channelForm.enabled" type="checkbox" />
              <span>{{ t('view.notificationConfig.fieldEnabled') }}</span>
            </label>

            <div class="cfg-config">
              <div class="cfg-config__head">
                <span class="cfg-config__title">{{ t('view.notificationConfig.configTitle') }}</span>
                <button
                  v-if="editingChannelId && !configEditable"
                  type="button"
                  class="btn btn--sm"
                  @click="configEditable = true"
                >
                  {{ t('view.notificationConfig.configEdit') }}
                </button>
              </div>
              <p v-if="editingChannelId && !configEditable" class="cfg-note">
                {{ t('view.notificationConfig.configLocked') }}
              </p>
              <p v-if="editingChannelId && configEditable && maskedSecretCount" class="cfg-note cfg-note--warn">
                {{ t('view.notificationConfig.configSecretWarn') }}
              </p>

              <div v-if="configEditable" class="cfg-fields">
                <template v-if="channelForm.type === 'email'">
                  <label class="form-label">
                    <span>{{ t('view.notificationConfig.fieldHost') }}</span>
                    <input v-model="channelForm.email.host" class="input" type="text" placeholder="smtp.example.com" />
                  </label>
                  <div class="cfg-field-row">
                    <label class="form-label">
                      <span>{{ t('view.notificationConfig.fieldPort') }}</span>
                      <input v-model.number="channelForm.email.port" class="input" type="number" min="1" max="65535" />
                    </label>
                    <label class="form-label">
                      <span>{{ t('view.notificationConfig.fieldUsername') }}</span>
                      <input v-model="channelForm.email.username" class="input" type="text" autocomplete="off" />
                    </label>
                  </div>
                  <label class="form-label">
                    <span>{{ t('view.notificationConfig.fieldPassword') }}</span>
                    <input
                      v-model="channelForm.email.password"
                      class="input"
                      type="password"
                      autocomplete="new-password"
                      :placeholder="secretPlaceholder"
                    />
                  </label>
                  <label class="form-label">
                    <span>{{ t('view.notificationConfig.fieldFromAddr') }}</span>
                    <input v-model="channelForm.email.from_addr" class="input" type="email" placeholder="alerts@example.com" />
                  </label>
                  <label class="form-label">
                    <span>{{ t('view.notificationConfig.fieldToAddrs') }}</span>
                    <input v-model="channelForm.email.to_addrs" class="input" type="text" placeholder="ops@example.com, dev@example.com" />
                  </label>
                  <label class="form-label form-label--inline">
                    <input v-model="channelForm.email.use_tls" type="checkbox" />
                    <span>{{ t('view.notificationConfig.fieldUseTls') }}</span>
                  </label>
                  <label class="form-label form-label--inline">
                    <input v-model="channelForm.email.use_ssl" type="checkbox" />
                    <span>{{ t('view.notificationConfig.fieldUseSsl') }}</span>
                  </label>
                </template>

                <template v-else-if="channelForm.type === 'webhook'">
                  <label class="form-label">
                    <span>{{ t('view.notificationConfig.fieldUrl') }}</span>
                    <input v-model="channelForm.webhook.url" class="input" type="url" placeholder="https://hooks.example.com/maop" />
                  </label>
                  <div class="cfg-field-row">
                    <label class="form-label">
                      <span>{{ t('view.notificationConfig.fieldMethod') }}</span>
                      <select v-model="channelForm.webhook.method" class="input">
                        <option value="POST">POST</option>
                        <option value="PUT">PUT</option>
                        <option value="PATCH">PATCH</option>
                      </select>
                    </label>
                    <label class="form-label">
                      <span>{{ t('view.notificationConfig.fieldTimeoutS') }}</span>
                      <input v-model.number="channelForm.webhook.timeout_s" class="input" type="number" min="1" max="300" />
                    </label>
                  </div>
                  <label class="form-label">
                    <span>{{ t('view.notificationConfig.fieldSecret') }}</span>
                    <input
                      v-model="channelForm.webhook.secret"
                      class="input"
                      type="password"
                      autocomplete="new-password"
                      :placeholder="secretPlaceholder"
                    />
                  </label>
                  <label class="form-label">
                    <span>{{ t('view.notificationConfig.fieldHeaders') }}</span>
                    <textarea
                      v-model="channelForm.webhook.headers"
                      class="input cfg-textarea"
                      rows="3"
                      placeholder='{"X-Environment": "prod"}'
                    />
                  </label>
                  <label class="form-label form-label--inline">
                    <input v-model="channelForm.webhook.ssl_verify" type="checkbox" />
                    <span>{{ t('view.notificationConfig.fieldSslVerify') }}</span>
                  </label>
                </template>

                <template v-else>
                  <label class="form-label">
                    <span>{{ t('view.notificationConfig.fieldUserId') }}</span>
                    <input v-model="channelForm.inapp.user_id" class="input" type="text" />
                  </label>
                </template>
              </div>
            </div>

            <p v-if="channelError" class="cfg-form-error" role="alert">{{ channelError }}</p>
          </div>
          <div class="modal-actions">
            <button class="btn" type="button" @click="closeChannel">{{ t('common.cancel') }}</button>
            <button class="btn btn--primary" type="button" :disabled="channelSaving" @click="saveChannel">
              {{ channelSaving ? t('view.notificationConfig.saving') : t('common.save') }}
            </button>
          </div>
        </div>
      </div>

      <!-- ── Rule editor ────────────────────────────────────── -->
      <div
        v-if="ruleOpen"
        v-modal-a11y
        class="modal-overlay"
        @click.self="closeRule"
        @modal:escape="closeRule"
      >
        <div class="modal cfg-modal" role="dialog" aria-modal="true" aria-labelledby="cfg-rule-title">
          <button class="modal-close" type="button" :aria-label="t('common.close')" @click="closeRule">
            <AppIcon name="x" :size="16" aria-hidden="true" />
          </button>
          <h3 id="cfg-rule-title">
            {{ editingRuleId ? t('view.notificationConfig.ruleEditTitle') : t('view.notificationConfig.ruleCreateTitle') }}
          </h3>
          <div class="form">
            <label class="form-label">
              <span>{{ t('view.notificationConfig.fieldName') }}</span>
              <input v-model="ruleForm.name" class="input" type="text" maxlength="100" />
            </label>
            <label class="form-label">
              <span>{{ t('view.notificationConfig.fieldEventType') }}</span>
              <input
                v-model="ruleForm.event_type"
                class="input"
                type="text"
                list="cfg-event-types"
                placeholder="task_failed"
              />
              <datalist id="cfg-event-types">
                <option v-for="ev in EVENT_TYPES" :key="ev" :value="ev" />
              </datalist>
            </label>
            <div class="form-label">
              <span>{{ t('view.notificationConfig.fieldChannels') }}</span>
              <div class="cfg-checklist">
                <label v-for="ch in channels" :key="ch.id" class="cfg-check">
                  <input v-model="ruleForm.channel_ids" type="checkbox" :value="ch.id" />
                  <span>{{ ch.name }}</span>
                </label>
                <span v-if="!channels.length" class="cfg-muted">{{ t('view.notificationConfig.noChannelsOption') }}</span>
              </div>
            </div>
            <div class="cfg-field-row">
              <label class="form-label">
                <span>{{ t('view.notificationConfig.fieldLevel') }}</span>
                <select v-model="ruleForm.level" class="input">
                  <option v-for="lv in LEVELS" :key="lv" :value="lv">{{ levelLabel(lv) }}</option>
                </select>
              </label>
              <label class="form-label">
                <span>{{ t('view.notificationConfig.fieldTemplate') }}</span>
                <select v-model="ruleForm.template_id" class="input">
                  <option value="">{{ t('view.notificationConfig.templateNone') }}</option>
                  <option v-for="tpl in templates" :key="tpl.id" :value="tpl.id">{{ tpl.name }}</option>
                </select>
              </label>
            </div>
            <label class="form-label">
              <span>{{ t('view.notificationConfig.fieldFilter') }}</span>
              <textarea
                v-model="ruleForm.filter"
                class="input cfg-textarea"
                rows="3"
                placeholder='{"severity": "critical"}'
              />
              <span class="cfg-field-help">{{ t('view.notificationConfig.fieldFilterHelp') }}</span>
            </label>
            <label class="form-label">
              <span>{{ t('view.notificationConfig.fieldDescription') }}</span>
              <input v-model="ruleForm.description" class="input" type="text" maxlength="500" />
            </label>
            <label class="form-label form-label--inline">
              <input v-model="ruleForm.enabled" type="checkbox" />
              <span>{{ t('view.notificationConfig.fieldEnabled') }}</span>
            </label>
            <p v-if="ruleError" class="cfg-form-error" role="alert">{{ ruleError }}</p>
          </div>
          <div class="modal-actions">
            <button class="btn" type="button" @click="closeRule">{{ t('common.cancel') }}</button>
            <button class="btn btn--primary" type="button" :disabled="ruleSaving" @click="saveRule">
              {{ ruleSaving ? t('view.notificationConfig.saving') : t('common.save') }}
            </button>
          </div>
        </div>
      </div>

      <!-- ── Template editor ────────────────────────────────── -->
      <div
        v-if="templateOpen"
        v-modal-a11y
        class="modal-overlay"
        @click.self="closeTemplate"
        @modal:escape="closeTemplate"
      >
        <div class="modal cfg-modal" role="dialog" aria-modal="true" aria-labelledby="cfg-template-title">
          <button class="modal-close" type="button" :aria-label="t('common.close')" @click="closeTemplate">
            <AppIcon name="x" :size="16" aria-hidden="true" />
          </button>
          <h3 id="cfg-template-title">{{ t('view.notificationConfig.templateCreateTitle') }}</h3>
          <div class="form">
            <label class="form-label">
              <span>{{ t('view.notificationConfig.fieldName') }}</span>
              <input v-model="templateForm.name" class="input" type="text" maxlength="100" />
            </label>
            <label class="form-label">
              <span>{{ t('view.notificationConfig.fieldSubject') }}</span>
              <input v-model="templateForm.subject" class="input" type="text" maxlength="500" />
            </label>
            <label class="form-label">
              <span>{{ t('view.notificationConfig.fieldBody') }}</span>
              <textarea v-model="templateForm.body" class="input cfg-textarea" rows="6" />
              <span class="cfg-field-help">{{ t('view.notificationConfig.fieldBodyHelp') }}</span>
            </label>
            <label class="form-label">
              <span>{{ t('view.notificationConfig.fieldDescription') }}</span>
              <input v-model="templateForm.description" class="input" type="text" maxlength="500" />
            </label>
            <p v-if="templateError" class="cfg-form-error" role="alert">{{ templateError }}</p>
          </div>
          <div class="modal-actions">
            <button class="btn" type="button" @click="closeTemplate">{{ t('common.cancel') }}</button>
            <button class="btn btn--primary" type="button" :disabled="templateSaving" @click="saveTemplate">
              {{ templateSaving ? t('view.notificationConfig.saving') : t('common.save') }}
            </button>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue';
import { useApiStore } from '../stores/api.js';
import { useToast } from '../composables/useToast.js';
import { useConfirm } from '../composables/useConfirm.js';
import { useI18n } from '../i18n';
import PageHeader from '../components/PageHeader.vue';
import EmptyState from '../components/EmptyState.vue';
import Skeleton from '../components/Skeleton.vue';
import Badge from '../components/Badge.vue';
import AppIcon from '../components/AppIcon.vue';

const { t } = useI18n();
const api = useApiStore();
const toast = useToast();
const { showConfirm } = useConfirm();

// Channel types and event types mirror MAOS notification.models enums.
// EventBus accepts custom event types too, so the event field stays free text.
const CHANNEL_TYPES = ['inapp', 'email', 'webhook'];
const LEVELS = ['info', 'warning', 'error', 'critical'];
const EVENT_TYPES = [
  'task_completed',
  'task_failed',
  'agent_error',
  'quota_exceeded',
  'license_expiring',
  'audit_alert',
  'system_error',
  'dag_completed',
  'dag_failed',
];

const activeTab = ref('channels');
const loading = ref(true);
const error = ref('');
const channels = ref([]);
const rules = ref([]);
const templates = ref([]);

const tabs = computed(() => [
  { key: 'channels', label: 'view.notificationConfig.tabChannels', count: channels.value.length },
  { key: 'rules', label: 'view.notificationConfig.tabRules', count: rules.value.length },
  { key: 'templates', label: 'view.notificationConfig.tabTemplates', count: templates.value.length },
]);

// ── Loading ──────────────────────────────────────────────────────

async function loadAll() {
  loading.value = true;
  error.value = '';
  try {
    const [ch, ru, tp] = await Promise.all([
      api.get('/api/notifications/channels'),
      api.get('/api/notifications/rules'),
      api.get('/api/notifications/templates'),
    ]);
    channels.value = (ch.channels || []).map(normalizeChannel);
    rules.value = (ru.rules || []).map(normalizeRule);
    templates.value = (tp.templates || []).map(normalizeTemplate);
  } catch (e) {
    error.value = e.message || String(e);
    channels.value = [];
    rules.value = [];
    templates.value = [];
  } finally {
    loading.value = false;
  }
}

function normalizeChannel(c) {
  return {
    id: c.channel_id || c.id || '',
    name: c.name || '',
    type: c.type || 'inapp',
    config: c.config && typeof c.config === 'object' ? c.config : {},
    description: c.description || '',
    enabled: c.enabled !== false,
  };
}

function normalizeRule(r) {
  return {
    id: r.rule_id || r.id || '',
    name: r.name || '',
    event_type: r.event_type || '',
    channel_ids: Array.isArray(r.channel_ids) ? r.channel_ids : [],
    template_id: r.template_id || '',
    level: r.level || 'info',
    description: r.description || '',
    enabled: r.enabled !== false,
  };
}

function normalizeTemplate(tpl) {
  return {
    id: tpl.template_id || tpl.id || '',
    name: tpl.name || '',
    subject: tpl.subject || '',
    body: tpl.body || '',
    description: tpl.description || '',
  };
}

// ── Display helpers ──────────────────────────────────────────────

function channelTypeLabel(type) {
  const map = {
    email: 'view.notificationConfig.typeEmail',
    webhook: 'view.notificationConfig.typeWebhook',
    inapp: 'view.notificationConfig.typeInapp',
  };
  return map[type] ? t(map[type]) : type;
}

function levelLabel(level) {
  const map = {
    info: 'view.notificationConfig.levelInfo',
    warning: 'view.notificationConfig.levelWarning',
    error: 'view.notificationConfig.levelError',
    critical: 'view.notificationConfig.levelCritical',
  };
  return map[level] ? t(map[level]) : level;
}

function levelTone(level) {
  if (level === 'critical' || level === 'error') return 'fail';
  if (level === 'warning') return 'warn';
  return 'info';
}

function channelName(id) {
  const ch = channels.value.find((c) => c.id === id);
  return ch ? ch.name : id;
}

function configSummary(ch) {
  const cfg = ch.config || {};
  if (ch.type === 'email') return cfg.host ? `${cfg.host}:${cfg.port || 587}` : '—';
  if (ch.type === 'webhook') return cfg.url || '—';
  return cfg.user_id || '—';
}

// ── Channel editor ───────────────────────────────────────────────

const channelOpen = ref(false);
const channelSaving = ref(false);
const editingChannelId = ref('');
const configEditable = ref(false);
const channelError = ref('');
const maskedSecretCount = ref(0);
const extraConfig = ref({});
const channelForm = ref(defaultChannelForm());

const secretPlaceholder = computed(() => (
  maskedSecretCount.value
    ? t('view.notificationConfig.secretUnchanged')
    : ''
));

function defaultChannelForm() {
  return {
    name: '',
    type: 'inapp',
    description: '',
    enabled: true,
    email: {
      host: '',
      port: 587,
      username: '',
      password: '',
      from_addr: '',
      to_addrs: '',
      use_tls: true,
      use_ssl: false,
    },
    webhook: {
      url: '',
      method: 'POST',
      secret: '',
      headers: '{}',
      timeout_s: 30,
      ssl_verify: true,
    },
    inapp: {
      user_id: '',
    },
  };
}

const KNOWN_CONFIG_KEYS = {
  email: ['host', 'port', 'username', 'password', 'from_addr', 'to_addrs', 'use_tls', 'use_ssl'],
  webhook: ['url', 'method', 'secret', 'headers', 'timeout_s', 'ssl_verify'],
  inapp: ['user_id'],
};

function openCreateChannel() {
  editingChannelId.value = '';
  channelForm.value = defaultChannelForm();
  configEditable.value = true;
  maskedSecretCount.value = 0;
  extraConfig.value = {};
  channelError.value = '';
  channelOpen.value = true;
}

function openEditChannel(ch) {
  const form = defaultChannelForm();
  const cfg = ch.config || {};
  form.name = ch.name;
  form.type = ch.type;
  form.description = ch.description;
  form.enabled = ch.enabled;

  if (ch.type === 'email') {
    form.email.host = str(cfg.host);
    form.email.port = Number(cfg.port) || 587;
    form.email.username = str(cfg.username);
    form.email.from_addr = str(cfg.from_addr);
    form.email.to_addrs = Array.isArray(cfg.to_addrs) ? cfg.to_addrs.join(', ') : str(cfg.to_addrs);
    form.email.use_tls = cfg.use_tls !== false;
    form.email.use_ssl = cfg.use_ssl === true;
  } else if (ch.type === 'webhook') {
    form.webhook.url = str(cfg.url);
    form.webhook.method = str(cfg.method) || 'POST';
    form.webhook.headers = JSON.stringify(
      cfg.headers && typeof cfg.headers === 'object' ? cfg.headers : {},
      null,
      2,
    );
    form.webhook.timeout_s = Number(cfg.timeout_s) || 30;
    form.webhook.ssl_verify = cfg.ssl_verify !== false;
  } else {
    form.inapp.user_id = str(cfg.user_id);
  }

  // Masked secrets ("***") are never written back. They are counted only to
  // tell the operator that re-entering the value is required when config is
  // replaced (the update endpoint replaces the whole config object).
  maskedSecretCount.value = Object.values(cfg).filter((v) => v === '***').length;
  const known = KNOWN_CONFIG_KEYS[ch.type] || [];
  extraConfig.value = Object.fromEntries(
    Object.entries(cfg).filter(([k, v]) => !known.includes(k) && v !== '***'),
  );

  editingChannelId.value = ch.id;
  channelForm.value = form;
  configEditable.value = false;
  channelError.value = '';
  channelOpen.value = true;
}

function str(v) {
  return v === null || v === undefined ? '' : String(v);
}

function closeChannel() {
  channelOpen.value = false;
}

function buildChannelConfig() {
  const form = channelForm.value;
  const cfg = { ...extraConfig.value };

  if (form.type === 'email') {
    cfg.host = form.email.host.trim();
    cfg.port = Number(form.email.port) || 587;
    cfg.from_addr = form.email.from_addr.trim();
    cfg.use_tls = !!form.email.use_tls;
    cfg.use_ssl = !!form.email.use_ssl;
    if (form.email.username.trim()) cfg.username = form.email.username.trim();
    // Blank secret = "do not keep it": the API replaces the whole config.
    if (form.email.password) cfg.password = form.email.password;
    const toAddrs = form.email.to_addrs.split(',').map((s) => s.trim()).filter(Boolean);
    if (toAddrs.length) cfg.to_addrs = toAddrs;
  } else if (form.type === 'webhook') {
    cfg.url = form.webhook.url.trim();
    cfg.method = form.webhook.method || 'POST';
    cfg.timeout_s = Number(form.webhook.timeout_s) || 30;
    cfg.ssl_verify = !!form.webhook.ssl_verify;
    if (form.webhook.secret) cfg.secret = form.webhook.secret;
    const rawHeaders = form.webhook.headers.trim();
    if (rawHeaders) {
      let parsed;
      try {
        parsed = JSON.parse(rawHeaders);
      } catch {
        throw new Error(t('view.notificationConfig.validateHeadersJson'));
      }
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
        throw new Error(t('view.notificationConfig.validateHeadersObject'));
      }
      cfg.headers = parsed;
    }
  } else {
    if (form.inapp.user_id.trim()) cfg.user_id = form.inapp.user_id.trim();
  }

  return cfg;
}

function validateChannel() {
  if (!channelForm.value.name.trim()) return t('view.notificationConfig.validateName');
  if (channelForm.value.type === 'email') {
    if (!channelForm.value.email.host.trim()) return t('view.notificationConfig.validateHost');
    if (!channelForm.value.email.from_addr.trim()) return t('view.notificationConfig.validateFromAddr');
  }
  if (channelForm.value.type === 'webhook' && !channelForm.value.webhook.url.trim()) {
    return t('view.notificationConfig.validateUrl');
  }
  return '';
}

async function saveChannel() {
  const invalid = validateChannel();
  if (invalid) {
    channelError.value = invalid;
    return;
  }

  let payload;
  try {
    payload = {
      name: channelForm.value.name.trim(),
      description: channelForm.value.description.trim(),
      enabled: !!channelForm.value.enabled,
    };
    if (!editingChannelId.value) {
      payload.type = channelForm.value.type;
      payload.config = buildChannelConfig();
    } else if (configEditable.value) {
      payload.config = buildChannelConfig();
    }
    // Editing without touching config omits it entirely: the API replaces
    // `config` wholesale, so sending the masked round-trip would store "***"
    // as the real secret.
  } catch (e) {
    channelError.value = e.message;
    return;
  }

  channelSaving.value = true;
  try {
    if (editingChannelId.value) {
      await api.put(`/api/notifications/channels/${encodeURIComponent(editingChannelId.value)}`, payload);
    } else {
      await api.post('/api/notifications/channels', payload);
    }
    toast.success(t('view.notificationConfig.channelSaved'));
    channelOpen.value = false;
    await loadAll();
  } catch (e) {
    channelError.value = e.message || t('view.notificationConfig.saveFailed');
  } finally {
    channelSaving.value = false;
  }
}

async function removeChannel(ch) {
  const ok = await showConfirm({
    message: t('view.notificationConfig.deleteChannelConfirm', { name: ch.name }),
    tone: 'danger',
  });
  if (!ok) return;
  try {
    await api.delete(`/api/notifications/channels/${encodeURIComponent(ch.id)}`);
    toast.success(t('view.notificationConfig.channelDeleted'));
    await loadAll();
  } catch (e) {
    toast.error(e.message || t('view.notificationConfig.deleteFailed'));
  }
}

// ── Rule editor ──────────────────────────────────────────────────

const ruleOpen = ref(false);
const ruleSaving = ref(false);
const editingRuleId = ref('');
const ruleError = ref('');
const ruleForm = ref(defaultRuleForm());

function defaultRuleForm() {
  return {
    name: '',
    event_type: 'task_failed',
    channel_ids: [],
    template_id: '',
    level: 'info',
    filter: '{}',
    description: '',
    enabled: true,
  };
}

function openCreateRule() {
  editingRuleId.value = '';
  ruleForm.value = defaultRuleForm();
  ruleError.value = '';
  ruleOpen.value = true;
}

function openEditRule(r) {
  editingRuleId.value = r.id;
  ruleForm.value = {
    name: r.name,
    event_type: r.event_type,
    channel_ids: [...r.channel_ids],
    template_id: r.template_id || '',
    level: r.level,
    filter: JSON.stringify(r.filter || {}, null, 2),
    description: r.description,
    enabled: r.enabled,
  };
  ruleError.value = '';
  ruleOpen.value = true;
}

function closeRule() {
  ruleOpen.value = false;
}

async function saveRule() {
  if (!ruleForm.value.name.trim()) {
    ruleError.value = t('view.notificationConfig.validateName');
    return;
  }
  if (!ruleForm.value.event_type.trim()) {
    ruleError.value = t('view.notificationConfig.validateEventType');
    return;
  }
  if (!ruleForm.value.channel_ids.length) {
    ruleError.value = t('view.notificationConfig.validateChannels');
    return;
  }

  let filter;
  try {
    filter = JSON.parse(ruleForm.value.filter.trim() || '{}');
  } catch {
    ruleError.value = t('view.notificationConfig.validateFilterJson');
    return;
  }
  if (!filter || typeof filter !== 'object' || Array.isArray(filter)) {
    ruleError.value = t('view.notificationConfig.validateFilterObject');
    return;
  }

  const payload = {
    name: ruleForm.value.name.trim(),
    event_type: ruleForm.value.event_type.trim(),
    channel_ids: [...ruleForm.value.channel_ids],
    template_id: ruleForm.value.template_id || '',
    filter,
    level: ruleForm.value.level,
    description: ruleForm.value.description.trim(),
    enabled: !!ruleForm.value.enabled,
  };

  ruleSaving.value = true;
  try {
    if (editingRuleId.value) {
      await api.put(`/api/notifications/rules/${encodeURIComponent(editingRuleId.value)}`, payload);
    } else {
      await api.post('/api/notifications/rules', payload);
    }
    toast.success(t('view.notificationConfig.ruleSaved'));
    ruleOpen.value = false;
    await loadAll();
  } catch (e) {
    ruleError.value = e.message || t('view.notificationConfig.saveFailed');
  } finally {
    ruleSaving.value = false;
  }
}

async function removeRule(r) {
  const ok = await showConfirm({
    message: t('view.notificationConfig.deleteRuleConfirm', { name: r.name }),
    tone: 'danger',
  });
  if (!ok) return;
  try {
    await api.delete(`/api/notifications/rules/${encodeURIComponent(r.id)}`);
    toast.success(t('view.notificationConfig.ruleDeleted'));
    await loadAll();
  } catch (e) {
    toast.error(e.message || t('view.notificationConfig.deleteFailed'));
  }
}

// ── Template editor ──────────────────────────────────────────────

const templateOpen = ref(false);
const templateSaving = ref(false);
const templateError = ref('');
const templateForm = ref({ name: '', subject: '', body: '', description: '' });

function openCreateTemplate() {
  templateForm.value = { name: '', subject: '', body: '', description: '' };
  templateError.value = '';
  templateOpen.value = true;
}

function closeTemplate() {
  templateOpen.value = false;
}

async function saveTemplate() {
  if (!templateForm.value.name.trim()) {
    templateError.value = t('view.notificationConfig.validateName');
    return;
  }
  if (!templateForm.value.body.trim()) {
    templateError.value = t('view.notificationConfig.validateBody');
    return;
  }

  templateSaving.value = true;
  try {
    // Templates are create/delete only — the API exposes no PUT for them.
    await api.post('/api/notifications/templates', {
      name: templateForm.value.name.trim(),
      subject: templateForm.value.subject.trim(),
      body: templateForm.value.body,
      description: templateForm.value.description.trim(),
    });
    toast.success(t('view.notificationConfig.templateSaved'));
    templateOpen.value = false;
    await loadAll();
  } catch (e) {
    templateError.value = e.message || t('view.notificationConfig.saveFailed');
  } finally {
    templateSaving.value = false;
  }
}

async function removeTemplate(tpl) {
  const ok = await showConfirm({
    message: t('view.notificationConfig.deleteTemplateConfirm', { name: tpl.name }),
    tone: 'danger',
  });
  if (!ok) return;
  try {
    await api.delete(`/api/notifications/templates/${encodeURIComponent(tpl.id)}`);
    toast.success(t('view.notificationConfig.templateDeleted'));
    await loadAll();
  } catch (e) {
    toast.error(e.message || t('view.notificationConfig.deleteFailed'));
  }
}

onMounted(loadAll);
</script>

<style scoped>
.notification-config-page { display: flex; flex-direction: column; }

.cfg-tabs {
  display: flex; gap: var(--sp-1); flex-wrap: wrap;
  border-bottom: 1px solid var(--border); margin-bottom: var(--sp-4);
}
.cfg-tab {
  display: inline-flex; align-items: center; gap: var(--sp-2);
  background: transparent; border: 0; border-bottom: 2px solid transparent;
  padding: var(--sp-2) var(--sp-3); margin-bottom: -1px;
  color: var(--text-muted); font-size: var(--fs-sm); font-weight: 600; cursor: pointer;
}
.cfg-tab:hover { color: var(--text); }
.cfg-tab.active { color: var(--brand-strong); border-bottom-color: var(--brand); }
.cfg-tab-count {
  min-width: 20px; padding: 0 6px; border-radius: var(--r-full);
  background: var(--surface-2); border: 1px solid var(--border);
  font-size: var(--fs-xs); color: var(--text-faint); text-align: center;
}

.cfg-panel {
  background: var(--surface); border: 1px solid var(--border);
  border-radius: var(--r-lg); padding: var(--sp-4);
}
.cfg-panel__head {
  display: flex; align-items: center; justify-content: space-between;
  gap: var(--sp-3); margin-bottom: var(--sp-3); flex-wrap: wrap;
}
.cfg-hint { margin: 0; font-size: var(--fs-sm); color: var(--text-muted); line-height: 1.5; }

.cfg-table { display: flex; flex-direction: column; border-top: 1px solid var(--border); }
.cfg-row {
  display: grid; align-items: center; gap: var(--sp-3);
  padding: var(--sp-3) var(--sp-2); border-bottom: 1px solid var(--border);
}
.cfg-table--channels .cfg-row { grid-template-columns: minmax(160px, 2fr) 100px minmax(160px, 2fr) 100px 84px; }
.cfg-table--rules .cfg-row { grid-template-columns: minmax(160px, 2fr) minmax(120px, 1fr) minmax(160px, 2fr) 90px 100px 84px; }
.cfg-table--templates .cfg-row { grid-template-columns: minmax(160px, 1.5fr) minmax(140px, 1fr) minmax(200px, 3fr) 84px; }
.cfg-row--head {
  font-size: var(--fs-xs); font-weight: 600; color: var(--text-faint);
  text-transform: uppercase; letter-spacing: .04em;
}
.cfg-row--head span { padding-left: var(--sp-1); }
.cfg-actions-head { text-align: right; }
.cfg-actions { display: flex; gap: var(--sp-1); justify-content: flex-end; }
.cfg-badges { display: flex; gap: 4px; flex-wrap: wrap; }
.cfg-name { display: block; font-weight: 600; color: var(--text); }
.cfg-desc { display: block; font-size: var(--fs-xs); color: var(--text-faint); margin-top: 2px; }
.cfg-text { font-size: var(--fs-sm); color: var(--text-muted); }
.cfg-muted { color: var(--text-faint); }
.cfg-mono {
  font-family: var(--font-mono, ui-monospace, SFMono-Regular, Consolas, monospace);
  font-size: var(--fs-xs); color: var(--text-muted);
  background: var(--surface-2); border: 1px solid var(--border);
  border-radius: var(--r-sm); padding: 2px 6px;
}
.cfg-ellipsis { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

.btn-icon {
  display: grid; place-items: center; width: 28px; height: 28px;
  background: var(--surface-2); border: 1px solid var(--border);
  border-radius: var(--r-sm); color: var(--text-muted); cursor: pointer;
}
.btn-icon:hover { color: var(--text); border-color: var(--border-strong); }
.btn-icon--danger:hover { color: var(--fail); border-color: var(--fail); }
.spinning { animation: maop-spin 1s linear infinite; }
@keyframes maop-spin { to { transform: rotate(360deg); } }

.cfg-modal { max-width: 560px; width: calc(100vw - 32px); }
.cfg-config {
  display: flex; flex-direction: column; gap: var(--sp-2);
  border: 1px solid var(--border); border-radius: var(--r-md);
  padding: var(--sp-3); background: var(--surface-2);
}
.cfg-config__head { display: flex; align-items: center; justify-content: space-between; gap: var(--sp-2); }
.cfg-config__title { font-size: var(--fs-sm); font-weight: 600; color: var(--text-muted); }
.cfg-note { margin: 0; font-size: var(--fs-xs); color: var(--text-faint); line-height: 1.5; }
.cfg-note--warn { color: var(--warn-strong, var(--warn)); }
.cfg-fields { display: flex; flex-direction: column; gap: var(--sp-3); }
.cfg-field-row { display: grid; grid-template-columns: 1fr 1fr; gap: var(--sp-3); }
.cfg-field-help { font-size: var(--fs-xs); color: var(--text-faint); }
.cfg-textarea { resize: vertical; font-family: var(--font-mono, ui-monospace, SFMono-Regular, Consolas, monospace); }
.cfg-checklist {
  display: flex; flex-direction: column; gap: 6px;
  border: 1px solid var(--border); border-radius: var(--r-md);
  padding: var(--sp-2) var(--sp-3); background: var(--surface-2);
}
.cfg-check { display: flex; align-items: center; gap: 8px; font-size: var(--fs-sm); color: var(--text); }
.cfg-form-error {
  margin: 0; padding: var(--sp-2) var(--sp-3);
  background: var(--fail-soft); border: 1px solid var(--fail);
  border-radius: var(--r-md); color: var(--fail-strong); font-size: var(--fs-sm);
}
</style>
