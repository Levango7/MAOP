<template>
  <div class="skill-editor-page">
    <ListPageLayout
      :loading="loading"
      :error="error"
      :empty="!atoms.length && !loading && !error"
      :error-title="t('view.skills.editor.loadError')"
      :empty-title="t('view.skills.editor.noAtoms')"
      :empty-desc="t('view.skills.editor.noAtomsHint')"
      :loading-lines="6"
    >
      <template #badges>
        <Badge tone="brand">{{ t('view.skills.editor.badge') }}</Badge>
      </template>
      <template #actions>
        <button
          class="btn btn--ghost"
          type="button"
          :disabled="loading"
          @click="load"
        >
          <AppIcon name="refresh" :size="15" />
          <span>{{ t('view.skills.editor.refresh') }}</span>
        </button>
        <button
          class="btn btn--primary"
          type="button"
          :disabled="!steps.length"
          @click="openSave"
        >
          <AppIcon name="check" :size="15" />
          <span>{{ t('view.skills.editor.save') }}</span>
        </button>
      </template>

      <template #content>
        <div class="composer">
          <!-- 左侧：原子技能列表 -->
          <aside class="composer__palette" :aria-label="t('view.skills.editor.col.atoms')">
            <header class="composer__head">
              <AppIcon name="sparkles" :size="14" />
              <span>{{ t('view.skills.editor.col.atoms') }}</span>
            </header>
            <p class="composer__hint">{{ t('view.skills.editor.addHint') }}</p>
            <ul class="atom-list">
              <li
                v-for="a in atoms"
                :key="a.id || a.name"
                class="atom-card"
                draggable="true"
                @dragstart="onDragStart($event, a)"
                @click="addStep(a)"
              >
                <div class="atom-card__icon"><AppIcon name="sparkles" :size="14" /></div>
                <div class="atom-card__body">
                  <div class="atom-card__name">{{ a.name || a.id }}</div>
                  <div class="atom-card__desc">{{ a.description || '—' }}</div>
                </div>
                <button
                  class="atom-card__add"
                  type="button"
                  :title="t('view.skills.editor.add')"
                  :aria-label="t('view.skills.editor.add') + ' ' + (a.name || a.id)"
                  @click.stop="addStep(a)"
                >
                  <AppIcon name="plus" :size="14" />
                </button>
              </li>
            </ul>
          </aside>

          <!-- 中间：组合区 -->
          <section
            class="composer__canvas"
            :aria-label="t('view.skills.editor.col.canvas')"
            @dragover.prevent="onCanvasDragOver"
            @drop.prevent="onCanvasDrop"
          >
            <header class="composer__head">
              <AppIcon name="route" :size="14" />
              <span>{{ t('view.skills.editor.col.canvas') }}</span>
              <Badge tone="neutral">{{ t('view.skills.editor.stepsCount', { n: steps.length }) }}</Badge>
            </header>
            <div v-if="!steps.length" class="canvas-empty">
              <AppIcon name="archive" :size="28" />
              <p class="canvas-empty__title">{{ t('view.skills.editor.empty') }}</p>
              <p class="canvas-empty__hint">{{ t('view.skills.editor.emptyHint') }}</p>
            </div>
            <ol v-else class="step-list">
              <li
                v-for="(s, i) in steps"
                :key="s.uid"
                class="step-item"
                :class="{ 'is-active': selectedUid === s.uid }"
                draggable="true"
                @dragstart="onStepDragStart($event, i)"
                @dragover.prevent="onStepDragOver(i)"
                @drop.prevent="onStepDrop(i)"
                @click="selectStep(s.uid)"
              >
                <div class="step-item__index">{{ i + 1 }}</div>
                <div class="step-item__body">
                  <div class="step-item__name">{{ s.name }}</div>
                  <div class="step-item__maps">
                    <Badge tone="info">{{ s.input_map.length }}→</Badge>
                    <Badge tone="brand">→{{ s.output_map.length }}</Badge>
                  </div>
                </div>
                <div class="step-item__actions">
                  <button
                    class="btn-icon"
                    type="button"
                    :title="t('view.skills.editor.moveUp')"
                    :aria-label="t('view.skills.editor.moveUp')"
                    :disabled="i === 0"
                    @click.stop="moveUp(i)"
                  >
                    <AppIcon name="arrow-up" :size="13" />
                  </button>
                  <button
                    class="btn-icon"
                    type="button"
                    :title="t('view.skills.editor.moveDown')"
                    :aria-label="t('view.skills.editor.moveDown')"
                    :disabled="i === steps.length - 1"
                    @click.stop="moveDown(i)"
                  >
                    <AppIcon name="chevrondown" :size="13" />
                  </button>
                  <button
                    class="btn-icon btn-icon--danger"
                    type="button"
                    :title="t('view.skills.editor.remove')"
                    :aria-label="t('view.skills.editor.remove')"
                    @click.stop="removeStep(i)"
                  >
                    <AppIcon name="trash" :size="13" />
                  </button>
                </div>
              </li>
            </ol>
          </section>

          <!-- 右侧：参数配置面板 -->
          <aside class="composer__inspector" :aria-label="t('view.skills.editor.col.inspector')">
            <header class="composer__head">
              <AppIcon name="gear" :size="14" />
              <span>{{ t('view.skills.editor.col.inspector') }}</span>
            </header>
            <div v-if="!selectedStep" class="inspector-empty">
              <AppIcon name="compass" :size="20" />
              <p>{{ t('view.skills.editor.selectStep') }}</p>
            </div>
            <div v-else class="inspector-body">
              <div class="inspector-step">
                <Badge tone="brand">{{ t('view.skills.editor.step') }} {{ selectedIndex + 1 }}</Badge>
                <span class="inspector-step__name">{{ selectedStep.name }}</span>
              </div>

              <section class="map-section">
                <h4 class="map-section__title">{{ t('view.skills.editor.inputMap') }}</h4>
                <p class="map-section__hint">{{ t('view.skills.editor.inputMapHint') }}</p>
                <div v-if="!selectedStep.input_map.length" class="map-empty">{{ t('view.skills.editor.noMappings') }}</div>
                <ul class="map-list">
                  <li v-for="(m, mi) in selectedStep.input_map" :key="mi" class="map-row">
                    <input
                      v-model="m.key"
                      class="map-input"
                      type="text"
                      :placeholder="t('view.skills.editor.mappingKey')"
                    />
                    <input
                      v-model="m.source"
                      class="map-input"
                      type="text"
                      :placeholder="t('view.skills.editor.mappingSource')"
                    />
                    <button class="btn-icon btn-icon--danger" type="button" @click="removeMapping('input', mi)">
                      <AppIcon name="x" :size="12" />
                    </button>
                  </li>
                </ul>
                <button class="btn btn--ghost btn--sm" type="button" @click="addMapping('input')">
                  <AppIcon name="plus" :size="12" />
                  <span>{{ t('view.skills.editor.addMapping') }}</span>
                </button>
              </section>

              <section class="map-section">
                <h4 class="map-section__title">{{ t('view.skills.editor.outputMap') }}</h4>
                <p class="map-section__hint">{{ t('view.skills.editor.outputMapHint') }}</p>
                <div v-if="!selectedStep.output_map.length" class="map-empty">{{ t('view.skills.editor.noMappings') }}</div>
                <ul class="map-list">
                  <li v-for="(m, mi) in selectedStep.output_map" :key="mi" class="map-row">
                    <input
                      v-model="m.key"
                      class="map-input"
                      type="text"
                      :placeholder="t('view.skills.editor.mappingKey')"
                    />
                    <input
                      v-model="m.source"
                      class="map-input"
                      type="text"
                      :placeholder="t('view.skills.editor.mappingSource')"
                    />
                    <button class="btn-icon btn-icon--danger" type="button" @click="removeMapping('output', mi)">
                      <AppIcon name="x" :size="12" />
                    </button>
                  </li>
                </ul>
                <button class="btn btn--ghost btn--sm" type="button" @click="addMapping('output')">
                  <AppIcon name="plus" :size="12" />
                  <span>{{ t('view.skills.editor.addMapping') }}</span>
                </button>
              </section>
            </div>
          </aside>
        </div>
      </template>
    </ListPageLayout>

    <!-- 保存复合 Skill 抽屉 -->
    <DetailDrawer
      :open="showSave"
      :title="t('view.skills.editor.saveTitle')"
      icon="check"
      @close="closeSave"
    >
      <div class="save-form">
        <label class="field">
          <span class="field__label">{{ t('view.skills.editor.compositeName') }}</span>
          <input
            v-model="composite.name"
            class="field__input"
            type="text"
            :placeholder="t('view.skills.editor.compositeName')"
          />
        </label>
        <label class="field">
          <span class="field__label">{{ t('view.skills.editor.compositeDesc') }}</span>
          <textarea
            v-model="composite.description"
            class="field__input"
            rows="3"
            :placeholder="t('view.skills.editor.compositeDesc')"
          ></textarea>
        </label>
        <label class="field">
          <span class="field__label">{{ t('view.skills.editor.compositeCategory') }}</span>
          <input
            v-model="composite.category"
            class="field__input"
            type="text"
            :placeholder="t('view.skills.editor.compositeCategory')"
          />
        </label>

        <section class="preview">
          <h4 class="preview__title">{{ t('view.skills.editor.preview') }}</h4>
          <pre class="preview__code">{{ previewJson }}</pre>
        </section>
      </div>
      <template #footer>
        <button class="btn btn--ghost" type="button" @click="closeSave">{{ t('view.skills.editor.cancel') }}</button>
        <button
          class="btn btn--primary"
          type="button"
          :disabled="saving"
          @click="save"
        >
          {{ saving ? t('view.skills.editor.saving') : t('view.skills.editor.confirm') }}
        </button>
      </template>
    </DetailDrawer>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue';
import { useApiStore } from '../stores/api.js';
import { useToast } from '../composables/useToast.js';
import { useI18n } from '../i18n/index.js';
import ListPageLayout from '../components/ListPageLayout.vue';
import DetailDrawer from '../components/DetailDrawer.vue';
import Badge from '../components/Badge.vue';
import AppIcon from '../components/AppIcon.vue';

const api = useApiStore();
const toast = useToast();
const { t } = useI18n();

// ── 原子技能列表 ──
const atoms = ref([]);
const loading = ref(true);
const error = ref('');

// ── 组合步骤 ──
// 每步: { uid, name, skill_id, input_map: [{key, source}], output_map: [{key, source}] }
let uidSeq = 0;
function nextUid() { uidSeq += 1; return 'step-' + uidSeq; }
const steps = ref([]);
const selectedUid = ref('');

const selectedStep = computed(() => steps.value.find((s) => s.uid === selectedUid.value) || null);
const selectedIndex = computed(() => steps.value.findIndex((s) => s.uid === selectedUid.value));

// ── 保存抽屉 ──
const showSave = ref(false);
const saving = ref(false);
const composite = ref({ name: '', description: '', category: 'composite' });

// ── 数据加载 ──
async function load() {
  loading.value = true;
  error.value = '';
  try {
    const d = await api.get('/api/evolution/skills');
    atoms.value = Array.isArray(d) ? d : (d.skills || []);
  } catch (e) {
    error.value = e.message || String(e);
    atoms.value = [];
  } finally {
    loading.value = false;
  }
}

// ── 拖拽：从原子列表 → 组合区 ──
let dragAtom = null;
function onDragStart(e, atom) {
  dragAtom = atom;
  if (e.dataTransfer) {
    e.dataTransfer.effectAllowed = 'copy';
    e.dataTransfer.setData('text/plain', atom.name || atom.id || '');
  }
}
function onCanvasDragOver(e) {
  if (e.dataTransfer) e.dataTransfer.dropEffect = 'copy';
}
function onCanvasDrop() {
  if (dragAtom) {
    addStep(dragAtom);
    dragAtom = null;
  }
}

// ── 拖拽：步骤重排序 ──
let dragIndex = -1;
function onStepDragStart(e, i) {
  dragIndex = i;
  if (e.dataTransfer) {
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', String(i));
  }
}
function onStepDragOver() { /* allow drop */ }
function onStepDrop(i) {
  if (dragIndex < 0 || dragIndex === i) { dragIndex = -1; return; }
  const arr = [...steps.value];
  const [moved] = arr.splice(dragIndex, 1);
  arr.splice(i, 0, moved);
  steps.value = arr;
  dragIndex = -1;
}

// ── 步骤操作 ──
function addStep(atom) {
  const step = {
    uid: nextUid(),
    name: atom.name || atom.id || 'unknown',
    skill_id: atom.id || atom.name || '',
    input_map: [],
    output_map: [],
  };
  steps.value = [...steps.value, step];
  selectedUid.value = step.uid;
}

function removeStep(i) {
  const removed = steps.value[i];
  const arr = [...steps.value];
  arr.splice(i, 1);
  steps.value = arr;
  if (selectedUid.value === removed.uid) {
    selectedUid.value = arr.length ? arr[Math.min(i, arr.length - 1)].uid : '';
  }
}

function moveUp(i) {
  if (i <= 0) return;
  const arr = [...steps.value];
  [arr[i - 1], arr[i]] = [arr[i], arr[i - 1]];
  steps.value = arr;
}

function moveDown(i) {
  if (i >= steps.value.length - 1) return;
  const arr = [...steps.value];
  [arr[i], arr[i + 1]] = [arr[i + 1], arr[i]];
  steps.value = arr;
}

function selectStep(uid) {
  selectedUid.value = uid;
}

// ── 映射编辑 ──
function addMapping(kind) {
  if (!selectedStep.value) return;
  const step = steps.value.find((s) => s.uid === selectedUid.value);
  if (!step) return;
  const entry = { key: '', source: '' };
  if (kind === 'input') step.input_map = [...step.input_map, entry];
  else step.output_map = [...step.output_map, entry];
}

function removeMapping(kind, mi) {
  const step = steps.value.find((s) => s.uid === selectedUid.value);
  if (!step) return;
  if (kind === 'input') step.input_map = step.input_map.filter((_, i) => i !== mi);
  else step.output_map = step.output_map.filter((_, i) => i !== mi);
}

// ── 保存复合 Skill ──
const previewJson = computed(() => {
  return JSON.stringify(
    {
      name: composite.value.name || '<unnamed>',
      description: composite.value.description || '',
      category: composite.value.category || 'composite',
      steps: steps.value.map((s) => ({
        skill: s.skill_id || s.name,
        input_map: s.input_map.filter((m) => m.key || m.source),
        output_map: s.output_map.filter((m) => m.key || m.source),
      })),
    },
    null,
    2,
  );
});

function openSave() {
  if (!steps.value.length) {
    toast.warn(t('view.skills.editor.noSteps'));
    return;
  }
  composite.value = { name: '', description: '', category: 'composite' };
  showSave.value = true;
}

function closeSave() {
  showSave.value = false;
}

async function save() {
  if (!composite.value.name.trim()) {
    toast.warn(t('view.skills.editor.nameRequired'));
    return;
  }
  saving.value = true;
  try {
    const payload = {
      name: composite.value.name.trim(),
      description: composite.value.description.trim(),
      category: composite.value.category.trim() || 'composite',
      steps: steps.value.map((s) => ({
        skill: s.skill_id || s.name,
        input_map: s.input_map.filter((m) => m.key || m.source),
        output_map: s.output_map.filter((m) => m.key || m.source),
      })),
    };
    await api.post('/api/evolution/skills/composite', payload);
    toast.success(t('view.skills.editor.saved'));
    showSave.value = false;
    steps.value = [];
    selectedUid.value = '';
  } catch (e) {
    toast.error(e.message || t('view.skills.editor.saveFailed'));
  } finally {
    saving.value = false;
  }
}

onMounted(load);
</script>

<style scoped>
.skill-editor-page { display: flex; flex-direction: column; }

/* ── 三栏布局 ── */
.composer {
  display: grid;
  grid-template-columns: 280px 1fr 320px;
  gap: var(--sp-4);
  align-items: stretch;
  min-height: 480px;
}
@media (max-width: 1100px) {
  .composer { grid-template-columns: 1fr; }
}

.composer__palette,
.composer__canvas,
.composer__inspector {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r-lg);
  display: flex;
  flex-direction: column;
  min-height: 420px;
}
.composer__head {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  padding: var(--sp-3) var(--sp-4);
  border-bottom: 1px solid var(--border);
  font-size: var(--fs-sm);
  font-weight: 600;
  color: var(--text);
}
.composer__head .badge { margin-left: auto; }
.composer__hint {
  margin: 0;
  padding: var(--sp-2) var(--sp-4);
  font-size: 11px;
  color: var(--text-muted);
}

/* ── 原子列表 ── */
.atom-list { list-style: none; margin: 0; padding: var(--sp-2); overflow-y: auto; flex: 1; }
.atom-card {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  padding: var(--sp-2) var(--sp-3);
  border: 1px solid var(--border);
  border-radius: var(--r-md);
  margin-bottom: var(--sp-2);
  cursor: grab;
  transition: border-color var(--motion) var(--ease), background var(--motion) var(--ease);
}
.atom-card:hover { border-color: var(--brand); background: var(--surface-2); }
.atom-card__icon {
  display: grid; place-items: center;
  width: 28px; height: 28px;
  background: var(--brand-soft); color: var(--brand-strong);
  border-radius: var(--r-sm); flex-shrink: 0;
}
.atom-card__body { flex: 1; min-width: 0; }
.atom-card__name { font-size: 13px; font-weight: 600; color: var(--text); }
.atom-card__desc {
  font-size: 11px; color: var(--text-muted);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.atom-card__add {
  display: grid; place-items: center;
  width: 24px; height: 24px;
  background: transparent; border: 1px solid var(--border);
  border-radius: var(--r-sm); color: var(--text-muted); cursor: pointer;
  flex-shrink: 0;
}
.atom-card__add:hover { color: var(--brand); border-color: var(--brand); }

/* ── 组合区 ── */
.canvas-empty {
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  flex: 1; gap: var(--sp-2); color: var(--text-faint); padding: var(--sp-6);
}
.canvas-empty__title { font-size: var(--fs-sm); font-weight: 600; color: var(--text-muted); margin: 0; }
.canvas-empty__hint { font-size: 11px; color: var(--text-faint); margin: 0; }
.step-list { list-style: none; margin: 0; padding: var(--sp-3); flex: 1; }
.step-item {
  display: flex;
  align-items: center;
  gap: var(--sp-3);
  padding: var(--sp-3);
  border: 1px solid var(--border);
  border-radius: var(--r-md);
  margin-bottom: var(--sp-2);
  cursor: pointer;
  transition: border-color var(--motion) var(--ease), background var(--motion) var(--ease);
}
.step-item:hover { background: var(--surface-2); }
.step-item.is-active { border-color: var(--brand); background: var(--brand-soft); }
.step-item__index {
  display: grid; place-items: center;
  width: 28px; height: 28px;
  background: var(--brand); color: var(--brand-contrast);
  border-radius: var(--r-full); font-size: 12px; font-weight: 700; flex-shrink: 0;
}
.step-item__body { flex: 1; min-width: 0; }
.step-item__name { font-size: 13px; font-weight: 600; color: var(--text); }
.step-item__maps { display: flex; gap: 4px; margin-top: 2px; }
.step-item__actions { display: flex; gap: 4px; }

/* ── 参数面板 ── */
.inspector-empty {
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  flex: 1; gap: var(--sp-2); color: var(--text-faint); padding: var(--sp-6);
}
.inspector-empty p { font-size: 12px; color: var(--text-muted); margin: 0; }
.inspector-body { padding: var(--sp-3) var(--sp-4); overflow-y: auto; flex: 1; }
.inspector-step { display: flex; align-items: center; gap: var(--sp-2); margin-bottom: var(--sp-3); }
.inspector-step__name { font-size: 13px; font-weight: 600; color: var(--text); }

.map-section { margin-bottom: var(--sp-4); }
.map-section__title { font-size: 11px; font-weight: 700; color: var(--text); text-transform: uppercase; letter-spacing: .04em; margin: 0 0 4px; }
.map-section__hint { font-size: 11px; color: var(--text-muted); margin: 0 0 var(--sp-2); }
.map-empty { font-size: 11px; color: var(--text-faint); margin-bottom: var(--sp-2); }
.map-list { list-style: none; margin: 0 0 var(--sp-2); padding: 0; display: flex; flex-direction: column; gap: 4px; }
.map-row { display: flex; gap: 4px; align-items: center; }
.map-input {
  flex: 1; min-width: 0;
  background: var(--bg); border: 1px solid var(--border);
  border-radius: var(--r-sm); padding: 5px 8px; font-size: 12px; color: var(--text);
}
.map-input:focus { outline: none; border-color: var(--brand); }

/* ── 保存抽屉表单 ── */
.save-form { display: flex; flex-direction: column; gap: var(--sp-3); }
.field { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--text-muted); }
.field__input {
  background: var(--bg); border: 1px solid var(--border);
  border-radius: var(--r-md); padding: 8px 10px; color: var(--text); font-size: 13px;
  font-family: inherit;
}
.field__input:focus { outline: none; border-color: var(--brand); }
.preview { margin-top: var(--sp-2); }
.preview__title { font-size: 11px; font-weight: 700; color: var(--text-muted); text-transform: uppercase; letter-spacing: .04em; margin: 0 0 6px; }
.preview__code {
  background: var(--surface-2); border: 1px solid var(--border);
  border-radius: var(--r-md); padding: var(--sp-3);
  font-family: var(--font-mono); font-size: 11px; color: var(--text);
  overflow-x: auto; margin: 0; white-space: pre;
}

/* ── 按钮 ── */
.btn {
  display: inline-flex; align-items: center; gap: 5px;
  background: var(--surface-2); color: var(--text); border: 1px solid var(--border);
  border-radius: var(--r-md); padding: 7px 12px; font-size: 12px; font-weight: 600;
  cursor: pointer; transition: opacity var(--motion) var(--ease);
}
.btn:hover { opacity: .9; }
.btn:disabled { opacity: .5; cursor: not-allowed; }
.btn--primary { background: var(--brand); color: var(--brand-contrast); border: none; }
.btn--ghost { background: transparent; }
.btn--sm { padding: 4px 8px; font-size: 11px; }
.btn-icon {
  display: grid; place-items: center;
  width: 26px; height: 26px;
  background: var(--surface-2); border: 1px solid var(--border);
  border-radius: var(--r-sm); color: var(--text-muted); cursor: pointer;
  transition: color var(--motion) var(--ease), border-color var(--motion) var(--ease);
}
.btn-icon:hover { color: var(--text); border-color: var(--border-strong); }
.btn-icon:disabled { opacity: .4; cursor: not-allowed; }
.btn-icon--danger:hover { color: var(--fail); border-color: var(--fail); }
</style>