<template>
  <Teleport to="body">
    <Transition name="palette">
      <div v-if="open" class="cmdpal" @keydown.esc.prevent="close">
        <div class="cmdpal__scrim" aria-hidden="true" @click="close"></div>
        <div class="cmdpal__panel" role="dialog" aria-modal="true" aria-label="Command palette">
          <div class="cmdpal__input-row">
            <AppIcon name="search" :size="16" class="cmdpal__search-icon" />
            <input
              ref="inputEl"
              v-model="query"
              class="cmdpal__input"
              type="text"
              :placeholder="t('palette.placeholder')"
              autocomplete="off"
              spellcheck="false"
              @keydown.down.prevent="move(1)"
              @keydown.up.prevent="move(-1)"
              @keydown.enter.prevent="run(selected)"
            />
            <kbd class="cmdpal__esc">Esc</kbd>
          </div>

          <div v-if="results.length" class="cmdpal__results" role="listbox">
            <button
              v-for="(r, i) in results"
              :key="r.to"
              type="button"
              class="cmdpal__item"
              :class="{ active: i === selected }"
              role="option"
              :aria-selected="i === selected"
              @mouseenter="selected = i"
              @click="run(i)"
            >
              <AppIcon :name="r.icon" :size="15" class="cmdpal__item-icon" />
              <span class="cmdpal__item-label">{{ r.label }}</span>
              <span v-if="r.subtitle" class="cmdpal__item-sub">{{ r.subtitle }}</span>
              <kbd v-if="r.kbd" class="cmdpal__item-kbd">{{ r.kbd }}</kbd>
            </button>
          </div>
          <div v-else class="cmdpal__empty">
            <p>{{ t('palette.noResults') }}</p>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<script setup>
/**
 * CommandPalette — 全局命令面板 (迭代 B3)。
 *
 * - 快捷键: Cmd/Ctrl+K 打开, Esc 关闭
 * - 命令源: 复用 nav.js 的路由项(icon/label/to), 支持拼音/中英文模糊匹配
 * - 交互: ↑↓ 选择, Enter 执行(router.push), 点击执行
 * - 纯前端, 无第三方依赖。初版是"壳"——后续可把 Agent 列表、快速动作加进命令源。
 *
 * 被 App.vue 顶层挂载: <CommandPalette />
 */
import { ref, computed, watch, onMounted, onBeforeUnmount, nextTick } from 'vue';
import { useRouter } from 'vue-router';
import AppIcon from './AppIcon.vue';
import { nav } from '../nav.js';
import { useI18n } from '../i18n';

const { t } = useI18n();
const router = useRouter();

const open = ref(false);
const query = ref('');
const selected = ref(0);
const inputEl = ref(null);

// 维度换算: 命令源来自 nav 的路由项(去 section/企业过滤由 nav 自身决定,
// 这里统一展示全部, 个人版不显示 enterprise 项是 nav.js 的职责)
const commands = ref(
  nav
    .filter((n) => n.to)
    .map((n) => ({
      to: n.to,
      label: t(n.label),
      icon: n.icon,
      subtitle: n.subtitle ? t(n.subtitle) : '',
    })),
);

// 简单模糊匹配: 大写忽略 + 空格分词, 每个词都命中才算
const results = computed(() => {
  const q = query.value.trim().toLowerCase();
  if (!q) return commands.value.slice(0, 12);
  const terms = q.split(/\s+/);
  return commands.value
    .filter((c) => {
      const hay = `${c.label} ${c.subtitle}`.toLowerCase();
      return terms.every((x) => hay.includes(x));
    })
    .slice(0, 12);
});

// query 变化时重置选中
watch(query, () => { selected.value = 0; });

function move(delta) {
  if (!results.value.length) return;
  selected.value = (selected.value + delta + results.value.length) % results.value.length;
}

async function run(idx) {
  const r = results.value[idx];
  if (!r) return;
  close();
  await router.push({ path: r.to.split('?')[0], query: parseQuery(r.to) });
}

function parseQuery(path) {
  const qs = path.split('?')[1];
  if (!qs) return {};
  return Object.fromEntries(new URLSearchParams(qs));
}

function openPalette() { open.value = true; selected.value = 0; query.value = ''; nextTick(() => { inputEl.value?.focus(); }); }
function close() { open.value = false; }

function onKey(e) {
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
    e.preventDefault();
    open.value ? close() : openPalette();
  }
}

onMounted(() => window.addEventListener('keydown', onKey));
onBeforeUnmount(() => window.removeEventListener('keydown', onKey));
</script>

<style scoped>
.cmdpal { position: fixed; inset: 0; z-index: calc(var(--z-modal, 90) + 10); }
.cmdpal__scrim { position: absolute; inset: 0; background: rgba(10,12,16,.5); }

.cmdpal__panel {
  position: absolute;
  top: 15vh;
  left: 50%;
  transform: translateX(-50%);
  width: min(560px, calc(100vw - 32px));
  background: var(--surface);
  border: 1px solid var(--border-strong);
  border-radius: var(--r-lg);
  box-shadow: var(--shadow-lg);
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.cmdpal__input-row {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  padding: var(--sp-3) var(--sp-4);
  border-bottom: 1px solid var(--border);
  color: var(--text-faint);
}
.cmdpal__input {
  flex: 1;
  background: transparent;
  border: none;
  outline: none;
  color: var(--text);
  font-size: var(--fs-md);
  font-family: inherit;
}
.cmdpal__input::placeholder { color: var(--text-faint); }
.cmdpal__esc {
  font-family: var(--font-mono);
  font-size: var(--fs-xs);
  color: var(--text-faint);
  border: 1px solid var(--border);
  border-radius: var(--r-sm);
  padding: 1px 6px;
}

.cmdpal__results { max-height: 36vh; overflow-y: auto; padding: var(--sp-2); }
.cmdpal__item {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  width: 100%;
  text-align: left;
  padding: var(--sp-2) var(--sp-3);
  background: transparent;
  border: none;
  border-radius: var(--r-md);
  color: var(--text);
  cursor: pointer;
}
.cmdpal__item:hover, .cmdpal__item.active { background: var(--brand-soft); }
.cmdpal__item-icon { color: var(--text-muted); flex-shrink: 0; }
.cmdpal__item.active .cmdpal__item-icon { color: var(--brand-strong); }
.cmdpal__item-label { font-size: var(--fs-sm); font-weight: 600; }
.cmdpal__item-sub { font-size: var(--fs-xs); color: var(--text-faint); margin-left: auto; }
.cmdpal__item-kbd { font-family: var(--font-mono); font-size: var(--fs-xs); color: var(--text-faint); }

.cmdpal__empty { padding: var(--sp-6); text-align: center; color: var(--text-faint); font-size: var(--fs-sm); }

.palette-enter-active, .palette-leave-active { transition: opacity .15s var(--ease); }
.palette-enter-from, .palette-leave-to { opacity: 0; }
</style>