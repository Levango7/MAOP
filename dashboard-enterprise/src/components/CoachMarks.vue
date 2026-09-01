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
  // 2026-09-01 IA 重设计适配：.evolve-tabs 已不存在（演化入口并入记忆
  // 站）。目标改为侧边栏的"记忆"链接本身（在所有页面都存在）。
  { sel: '.sidebar a[href="/memory"]', title: t('coach.evolve.title'), body: t('coach.evolve.body') },
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
  // 2026-09-01 一号用户实测修复（第 2 步悬浮窗"看不到"）：
  // 原逻辑无条件放目标下方 —— 目标是长侧边栏（.sidebar 高度 > 视口）
  // 时，弹窗被定位到视口外，"下一步"按钮不可见也不可点（Playwright
  // 实证 element is outside of the viewport）。
  // 改：先测下方是否放得下（目标底 + 16 + 弹窗高 ~ 220px 视口内），
  // 放不下则改放目标右上侧（仍在视口内）；目标本身在下半屏时放上方。
  const vh = typeof window !== 'undefined' ? window.innerHeight : 900;
  const belowTop = rect.value.top + rect.value.height + 16;
  const POPOVER_H = 240; // 保守估计（标题+正文+按钮），超设只会更安全
  if (belowTop + POPOVER_H <= vh) {
    // 下方放得下 → 右下（原始行为）
    return { top: belowTop + 'px', left: rect.value.left + 'px' };
  }
  if (rect.value.top - 16 - POPOVER_H >= 0) {
    // 下方放不下、上方放得下 → 目标上方
    return { top: (rect.value.top - 16 - POPOVER_H) + 'px', left: rect.value.left + 'px' };
  }
  // 上下都放不下（目标很高，如整条侧边栏）→ 定位到视口中部、目标右侧
  return {
    top: Math.max(16, Math.round(vh / 2 - POPOVER_H / 2)) + 'px',
    left: (rect.value.left + rect.value.width + 24) + 'px',
  };
});

function next() {
  if (step.value < steps.length - 1) {
    step.value += 1;
    // 2026-09-01 容错：目标元素不存在（页面在窄屏/新 IA 改动/视图未挂
    // 载时）→ 自动跳到下一步，不再让该步成为"看不到的悬浮窗"。
    nextTick(() => {
      if (!document.querySelector(steps[step.value]?.sel || '.nothing')) {
        next(); // 递归跳过（若后面也缺，直到找到或跳完）
      } else {
        measure();
      }
    });
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

  // 一号用户实测修复（2026-08-31 登录无限循环根因）：CoachMarks 与登录
  // 遮罩同为 z-modal(90) 且 Teleport 到 body 末尾（后挂载压前）——
  // .cm-scrim 盖住登录表单，submit 根本发不出去 → 401 循环弹窗。
  // 修复：登录弹窗激活期间绝不启动/立即隐藏；登录完成后再启动引导。
  // App.vue 在登录遮罩打开时置 body[data-auth-open="1"]。
  const tryStart = () => {
    const authOpen = document.body?.dataset?.authOpen === '1';
    if (authOpen) {
      active.value = false;
      setTimeout(tryStart, 800);
      return;
    }
    // 若首次进入不是 Overview(如深链), steps[0] 的 .ov-actions 不在 → 跳过
    const el0 = document.querySelector(steps[0].sel);
    if (!el0) { finish(); return; }
    active.value = true;
    nextTick(measure);
  };
  // 延迟到路由视图渲染(SVG 图绘制耗时), 再测量目标元素
  setTimeout(tryStart, 1200);
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
  background: var(--overlay-scrim);
  cursor: pointer;
}
.cm-spotlight {
  position: absolute;
  border-radius: var(--r-md);
  box-shadow: 0 0 0 9999px var(--overlay-scrim), 0 0 0 2px var(--brand), var(--shadow-card);
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