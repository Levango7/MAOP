<template>
  <Teleport to="body">
    <Transition name="cm">
      <div v-if="active" class="coach-marks" data-coach-root>
        <!-- 半透明遮罩: 点击任意处跳到下一步, 但高亮区本身可点穿 -->
        <div class="cm-scrim" @click="next"></div>

        <!-- 高亮框(四个角标, 用 border 描出聚焦区域) -->
        <div v-if="current" class="cm-spotlight" :style="spotTop ? spotlightStyle : { display: 'none' }" aria-hidden="true"></div>

        <!-- 气泡 -->
        <div
          v-if="current"
          ref="popoverEl"
          class="cm-popover"
          :style="popoverStyle"
          role="dialog"
          aria-modal="true"
          aria-live="polite"
          tabindex="-1"
          :aria-label="t('a11y.guideStep', { n: step + 1 })"
        >
          <div class="cm-step">{{ step + 1 }} / {{ steps.length }}</div>
          <div class="cm-title">{{ current.title }}</div>
          <div class="cm-body" v-html="current.body"></div>
          <div class="cm-actions">
            <button class="cm-skip" type="button" :aria-label="t('action.skip')" @click="finish">{{ t('action.skip') }}</button>
            <button class="cm-next" type="button" @click="next">
              {{ step === steps.length - 1 ? t('action.done') : t('action.next') }}
            </button>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<script setup>
import { ref, computed, watch, onMounted, onBeforeUnmount, nextTick } from 'vue';
import { useI18n } from '../i18n';

/**
 * CoachMarks — 首次访问引导(迭代 B2)。
 *
 * 4 步轻提示, 高亮当前聚焦的 DOM 元素 (数据驱动, 不侵入业务视图):
 *   0. 快捷操作磁贴   .ov-actions
 *   1. 侧栏导航       .sidebar
 *   2. 顶栏状态       .topbar__statusline
 *   3. 演化特性       .evolve-tabs (Evolution 页里的 Segmented)
 *
 * 行为:
 *   - localStorage 'maop_onboarding_done' = 1 → 永不显示(幂等)
 *   - Esc / 点击遮罩 → 下一步; 完成或跳过 → 写入标记并清空
 *   - 目标不在视图树时自动跳过该步(健壮: 用户已离开 Overview 等场景)
 *
 * 无第三方库, 纯 CSS+JS。样式跟随 --sp-* / --r-* token。
 */
const { t } = useI18n();

const STORAGE_KEY = 'maop_onboarding_done';

const steps = [
  { sel: '.ov-actions', title: t('coach.actions.title'), body: t('coach.actions.body') },
  { sel: '.sidebar', title: t('coach.nav.title'), body: t('coach.nav.body') },
  { sel: '.topbar__statusline', title: t('coach.topbar.title'), body: t('coach.topbar.body') },
  { sel: '.evolve-tabs', title: t('coach.evolve.title'), body: t('coach.evolve.body') },
];

const active = ref(false);
const step = ref(0);
const spotTop = ref(0); // 记录当前高亮元素 rect(供计算)
const popoverEl = ref(null);
let previousFocus = null;

// 目标元素最新几何: 每次 step 变化 + 短暂延时后测量
const rect = ref({ top: 0, left: 0, width: 0, height: 0 });

const current = computed(() => (active.value && step.value < steps.length ? steps[step.value] : null));

function measure() {
  const el = document.querySelector(current.value?.sel || '.nothing');
  if (!el) return;
  const r = el.getBoundingClientRect();
  rect.value = {
    top: r.top + window.scrollY,
    left: r.left + window.scrollX,
    width: r.width,
    height: r.height,
  };
  spotTop.value = rect.value.top;
}

const spotlightStyle = computed(() => ({
  top: rect.value.top + 'px',
  left: rect.value.left + 'px',
  width: rect.value.width + 'px',
  height: rect.value.height + 'px',
}));

const popoverStyle = computed(() => {
  // 默认在目标右下方
  return {
    top: rect.value.top + rect.value.height + 16 + 'px',
    left: rect.value.left + 'px',
  };
});

function next() {
  if (step.value < steps.length - 1) {
    step.value += 1;
    nextTick(measure);
  } else {
    finish();
  }
}

function finish() {
  active.value = false;
  try { localStorage.setItem(STORAGE_KEY, '1'); } catch { /* ignore */ }
  // 恢复焦点到引导开始前的元素 (a11y: 焦点还原)
  if (previousFocus && typeof previousFocus.focus === 'function') {
    nextTick(() => { previousFocus.focus({ preventScroll: true }); previousFocus = null; });
  } else {
    previousFocus = null;
  }
}

function onKey(e) {
  if (!active.value) return;
  if (e.key === 'Escape') { e.preventDefault(); finish(); }
  if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); next(); }
}

// 目标 mount 后测量; 打开时把焦点移入气泡 (a11y: 焦点管理)
watch(active, (v) => {
  if (v) {
    previousFocus = document.activeElement;
    nextTick(() => {
      measure();
      popoverEl.value?.focus({ preventScroll: true });
    });
  }
});

onMounted(() => {
  // 只在从未完成引导时启动
  let done = false;
  try { done = localStorage.getItem(STORAGE_KEY) === '1'; } catch { /* ignore */ }
  if (done) return;
  // 延迟到路由视图渲染(SVG 图绘制耗时), 再测量目标元素
  setTimeout(() => {
    // 若首次进入不是 Overview(如深链), steps[0] 的 .ov-actions 不在 → 跳过
    const el0 = document.querySelector(steps[0].sel);
    if (!el0) { finish(); return; }
    active.value = true;
    nextTick(measure);
  }, 1200);
  window.addEventListener('keydown', onKey);
  window.addEventListener('resize', onResize);
});

function onResize() { if (active.value) nextTick(measure); }

onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKey);
  window.removeEventListener('resize', onResize);
});
</script>

<style scoped>
.coach-marks {
  position: fixed;
  inset: 0;
  z-index: var(--z-modal, 90);
}
.cm-scrim {
  position: absolute;
  inset: 0;
  background: rgba(10, 12, 16, .55);
  cursor: pointer;
}
.cm-spotlight {
  position: absolute;
  border-radius: var(--r-md);
  box-shadow: 0 0 0 9999px rgba(10, 12, 16, .55), 0 0 0 2px var(--brand), 0 4px 24px rgba(0,0,0,.5);
  pointer-events: none;
  transition: top .25s var(--ease), left .25s var(--ease), width .25s var(--ease), height .25s var(--ease);
}
.cm-popover {
  position: absolute;
  max-width: 320px;
  z-index: 1;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r-lg);
  box-shadow: var(--shadow-lg);
  padding: var(--sp-4);
}
.cm-step { font-size: var(--fs-xs); color: var(--text-faint); font-weight: 700; letter-spacing: .08em; text-transform: uppercase; }
.cm-title { font-size: var(--fs-md); font-weight: 700; color: var(--text); margin: 4px 0 var(--sp-2); }
.cm-body { font-size: var(--fs-sm); color: var(--text-muted); line-height: 1.55; margin-bottom: var(--sp-3); }
.cm-actions { display: flex; justify-content: space-between; gap: var(--sp-2); }
.cm-skip {
  background: transparent; border: none; color: var(--text-faint);
  font-size: var(--fs-sm); cursor: pointer; padding: var(--sp-1) var(--sp-2);
}
.cm-skip:hover { color: var(--text); }
.cm-next {
  background: var(--brand); color: var(--brand-contrast);
  border: 1px solid var(--brand); border-radius: var(--r-md);
  padding: var(--sp-1) var(--sp-3); font-size: var(--fs-sm); font-weight: 600; cursor: pointer;
}
.cm-next:hover { background: var(--brand-strong); }

.cm-enter-active, .cm-leave-active { transition: opacity .2s var(--ease); }
.cm-enter-from, .cm-leave-to { opacity: 0; }
</style>