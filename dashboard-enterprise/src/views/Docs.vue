<template>
  <div class="docs-page view-enter">
    <PageHeader />

    <div class="docs-layout">
      <!-- 左：文档列表 -->
      <aside class="docs-sidebar">
        <div v-for="cat in categories" :key="cat.titleKey" class="doc-cat">
          <div class="doc-cat__title">
            <AppIcon :name="cat.icon" :size="14" />
            <span>{{ t(cat.titleKey) }}</span>
          </div>
          <ul class="doc-cat__list">
            <li v-for="doc in cat.items" :key="doc.path">
              <button
                class="doc-cat__item"
                :class="{ active: selectedPath === doc.path }"
                @click="selectDoc(doc)"
              >
                <AppIcon name="file-text" :size="13" class="doc-cat__icon" />
                <span>{{ doc.name }}</span>
              </button>
            </li>
          </ul>
        </div>
      </aside>

      <!-- 右：内容区 -->
      <section class="docs-content">
        <div v-if="loading" class="docs-loading">
          <AppIcon name="refresh" :size="20" class="spinning" />
          <span>{{ t('common.loading') }}</span>
        </div>

        <article v-else-if="selectedHtml" class="docs-article" v-html="selectedHtml"></article>

        <div v-else class="docs-empty">
          <AppIcon name="book-open" :size="32" />
          <p>{{ t('view.docs.empty') }}</p>
        </div>
      </section>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue';
import { useI18n } from '../i18n';
import { AppIcon, PageHeader } from '../components/index.js';
import DOMPurify from 'dompurify';

const { t } = useI18n();

// ── 通过 Vite 的 import.meta.glob 懒加载 docs/*.md ──
// P1-4 性能优化: 改为非 eager（懒加载），每个 md 文件拆为独立小 chunk，
// 按需加载，避免 68 个文档（~830KB）全部内联到 Docs chunk 导致首屏体积膨胀。
const docRawModules = import.meta.glob('../../../docs/**/*.md', { query: '?raw', import: 'default' });

const categories = [
  {
    titleKey: 'view.docs.gettingStarted',
    icon: 'book-open',
    items: [
      { name: 'Deployment Guide', path: 'deployment.md' },
      { name: 'API Reference', path: 'api-reference.md' },
      { name: 'Database Schema', path: 'database-schema.md' },
      { name: 'Design System (Legacy)', path: 'archive/audits/design-system-legacy.md' },
    ],
  },
  {
    titleKey: 'view.docs.guides',
    icon: 'compass',
    items: [
      { name: 'Troubleshooting', path: 'troubleshooting.md' },
      { name: 'Performance Benchmarks', path: 'performance-benchmarks.md' },
      { name: 'Plugin Migration', path: 'plugin-migration.md' },
      { name: 'Platform Evolution', path: 'platform-evolution.md' },
      { name: 'Contributing', path: 'contributing.md' },
    ],
  },
  {
    titleKey: 'view.docs.enterprise',
    icon: 'building',
    items: [
      { name: 'License Issuance Guide', path: 'enterprise/license-issuance-guide.md' },
      { name: 'License CRL Guide', path: 'enterprise/license-crl-guide.md' },
      { name: 'SAML SSO Guide', path: 'enterprise/saml-sso-guide.md' },
    ],
  },
  {
    titleKey: 'view.docs.integrations',
    icon: 'plug',
    items: [
      { name: 'n8n Integration', path: 'integrations/n8n.md' },
      { name: 'OmniRoute Integration', path: 'integrations/omniroute.md' },
    ],
  },
];

const selectedPath = ref('');
const selectedHtml = ref('');
const loading = ref(false);

function resolveRawLoader(path) {
  const key = `../../../docs/${path}`;
  return docRawModules[key] || null;
}

async function selectDoc(doc) {
  if (selectedPath.value === doc.path) return;
  selectedPath.value = doc.path;
  loading.value = true;
  selectedHtml.value = '';
  try {
    const loader = resolveRawLoader(doc.path);
    if (loader) {
      const raw = await loader();
      const html = renderMarkdown(raw);
      selectedHtml.value = DOMPurify.sanitize(html, { ADD_ATTR: ['target'] });
    } else {
      selectedHtml.value = `<p class="docs-not-found">${t('view.docs.notFound')}</p>`;
    }
  } catch {
    selectedHtml.value = `<p class="docs-not-found">${t('view.docs.loadFailed')}</p>`;
  } finally {
    loading.value = false;
  }
}

// 默认选中第一篇
selectDoc(categories[0].items[0]);

// ── 极简 Markdown 渲染器 ──────────────────────────────────────────────
// 支持：标题、代码块、行内代码、粗体/斜体、链接、列表、引用、水平线、段落
function renderMarkdown(md) {
  if (!md) return '';
  const lines = md.split('\n');
  let html = '';
  let inCode = false;
  let codeLang = '';
  let codeBuf = '';
  let inList = false;
  let listType = '';
  let inQuote = false;

  const esc = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  const inline = (s) => {
    let r = esc(s);
    r = r.replace(/`([^`]+)`/g, '<code>$1</code>');
    r = r.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    r = r.replace(/\*([^*]+)\*/g, '<em>$1</em>');
    r = r.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
    return r;
  };

  const closeList = () => { if (inList) { html += `</${listType}>`; inList = false; } };
  const closeQuote = () => { if (inQuote) { html += '</blockquote>'; inQuote = false; } };

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];

    // 代码块
    if (line.trim().startsWith('```')) {
      if (inCode) {
        html += `<pre><code${codeLang ? ` class="lang-${codeLang}"` : ''}>${esc(codeBuf)}</code></pre>`;
        inCode = false; codeBuf = ''; codeLang = '';
      } else {
        closeList(); closeQuote();
        inCode = true;
        codeLang = line.trim().slice(3);
      }
      continue;
    }
    if (inCode) { codeBuf += line + '\n'; continue; }

    // 空行
    if (line.trim() === '') { closeList(); closeQuote(); continue; }

    // 标题
    const h = line.match(/^(#{1,6})\s+(.*)$/);
    if (h) {
      closeList(); closeQuote();
      const lvl = h[1].length;
      html += `<h${lvl}>${inline(h[2])}</h${lvl}>`;
      continue;
    }

    // 水平线
    if (/^(-{3,}|\*{3,}|_{3,})$/.test(line.trim())) {
      closeList(); closeQuote();
      html += '<hr/>';
      continue;
    }

    // 引用
    if (line.startsWith('>')) {
      closeList();
      if (!inQuote) { html += '<blockquote>'; inQuote = true; }
      html += `<p>${inline(line.replace(/^>\s?/, ''))}</p>`;
      continue;
    }
    closeQuote();

    // 有序列表
    const ol = line.match(/^\s*(\d+)\.\s+(.*)$/);
    if (ol) {
      if (!inList || listType !== 'ol') { closeList(); html += '<ol>'; inList = true; listType = 'ol'; }
      html += `<li>${inline(ol[2])}</li>`;
      continue;
    }

    // 无序列表
    const ul = line.match(/^\s*[-*+]\s+(.*)$/);
    if (ul) {
      if (!inList || listType !== 'ul') { closeList(); html += '<ul>'; inList = true; listType = 'ul'; }
      html += `<li>${inline(ul[1])}</li>`;
      continue;
    }

    // 普通段落
    closeList(); closeQuote();
    html += `<p>${inline(line)}</p>`;
  }

  // 收尾
  if (inCode) html += `<pre><code>${esc(codeBuf)}</code></pre>`;
  closeList(); closeQuote();
  return html;
}
</script>

<style scoped>
.docs-page { display: flex; flex-direction: column; }

.docs-layout {
  display: grid;
  grid-template-columns: 260px 1fr;
  gap: var(--sp-5);
  align-items: stretch;
  min-height: calc(100vh - var(--topbar-h) - 200px);
}

/* ── 左侧文档列表 ────────────────────────────────────────────────── */
.docs-sidebar {
  background: var(--card-sheen), var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r-lg);
  padding: var(--sp-3);
  box-shadow: var(--shadow-sm);
  position: sticky;
  top: var(--sp-4);
  align-self: start;
  max-height: calc(100vh - var(--topbar-h) - 120px);
  overflow-y: auto;
}
.doc-cat + .doc-cat { margin-top: var(--sp-4); }
.doc-cat__title {
  display: flex; align-items: center; gap: 6px;
  font-size: var(--fs-xs); font-weight: 700; color: var(--text-faint);
  text-transform: uppercase; letter-spacing: .06em;
  padding: var(--sp-2) var(--sp-2) var(--sp-1);
}
.doc-cat__list { list-style: none; margin: 0; padding: 0; }
.doc-cat__item {
  display: flex; align-items: center; gap: 8px;
  width: 100%; text-align: left;
  padding: 7px 10px;
  border: none; background: transparent;
  color: var(--text-muted); font-size: var(--fs-sm); font-weight: 500;
  border-radius: var(--r-md);
  cursor: pointer;
  transition: background var(--motion) var(--ease), color var(--motion) var(--ease);
}
.doc-cat__item:hover { background: var(--surface-2); color: var(--text); }
.doc-cat__item.active {
  background: var(--brand-soft);
  color: var(--brand-strong);
  font-weight: 600;
}
.doc-cat__icon { flex-shrink: 0; opacity: .7; }
.doc-cat__item.active .doc-cat__icon { opacity: 1; }

/* ── 右侧内容区 ──────────────────────────────────────────────────── */
.docs-content {
  background: var(--card-sheen), var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r-lg);
  padding: var(--sp-6);
  box-shadow: var(--shadow-sm);
  min-width: 0;
  overflow-x: hidden;
}
.docs-loading {
  display: flex; align-items: center; gap: var(--sp-2);
  color: var(--text-muted); font-size: var(--fs-sm);
  padding: var(--sp-8) 0; justify-content: center;
}
.spinning { animation: doc-spin 1s linear infinite; }
@keyframes doc-spin { to { transform: rotate(360deg); } }

.docs-empty {
  display: flex; flex-direction: column; align-items: center; gap: var(--sp-3);
  color: var(--text-faint);
  padding: var(--sp-9) 0;
}
.docs-empty p { font-size: var(--fs-sm); }

/* ── 渲染后的 Markdown 样式 ──────────────────────────────────────── */
.docs-article {
  color: var(--text);
  font-size: var(--fs-md);
  line-height: 1.7;
}
.docs-article :deep(h1) {
  font-size: var(--fs-2xl); font-weight: 700; color: var(--text);
  margin: 0 0 var(--sp-4); padding-bottom: var(--sp-2);
  border-bottom: 1px solid var(--border);
  letter-spacing: -0.02em;
}
.docs-article :deep(h2) {
  font-size: var(--fs-xl); font-weight: 700; color: var(--text);
  margin: var(--sp-6) 0 var(--sp-3);
  letter-spacing: -0.01em;
}
.docs-article :deep(h3) {
  font-size: var(--fs-lg); font-weight: 600; color: var(--text);
  margin: var(--sp-5) 0 var(--sp-2);
}
.docs-article :deep(h4) { font-size: var(--fs-md); font-weight: 600; margin: var(--sp-4) 0 var(--sp-2); }
.docs-article :deep(p) { margin: 0 0 var(--sp-3); color: var(--text-muted); }
.docs-article :deep(a) { color: var(--brand-strong); text-decoration: none; border-bottom: 1px dashed var(--brand-faint); }
.docs-article :deep(a:hover) { border-bottom-style: solid; }
.docs-article :deep(strong) { color: var(--text); font-weight: 700; }
.docs-article :deep(em) { color: var(--text); }
.docs-article :deep(code) {
  font-family: var(--font-mono); font-size: .9em;
  background: var(--surface-2); color: var(--brand-strong);
  padding: 1px 5px; border-radius: var(--r-sm);
  border: 1px solid var(--border-subtle);
}
.docs-article :deep(pre) {
  background: var(--bg); border: 1px solid var(--border);
  border-radius: var(--r-md); padding: var(--sp-4);
  overflow-x: auto; margin: 0 0 var(--sp-4);
}
.docs-article :deep(pre code) {
  background: none; border: none; padding: 0;
  color: var(--text); font-size: var(--fs-sm);
}
.docs-article :deep(ul), .docs-article :deep(ol) {
  margin: 0 0 var(--sp-3); padding-left: var(--sp-5);
}
.docs-article :deep(li) { margin-bottom: 4px; color: var(--text-muted); }
.docs-article :deep(blockquote) {
  border-left: 3px solid var(--brand);
  background: var(--brand-faint);
  padding: var(--sp-2) var(--sp-4);
  margin: 0 0 var(--sp-3);
  border-radius: 0 var(--r-md) var(--r-md) 0;
}
.docs-article :deep(blockquote p) { margin: 0; color: var(--text-muted); }
.docs-article :deep(hr) { border: none; border-top: 1px solid var(--border); margin: var(--sp-5) 0; }
.docs-article :deep(table) { width: 100%; border-collapse: collapse; margin: 0 0 var(--sp-3); }
.docs-article :deep(th), .docs-article :deep(td) {
  border: 1px solid var(--border); padding: var(--sp-2) var(--sp-3);
  text-align: left; font-size: var(--fs-sm);
}
.docs-article :deep(th) { background: var(--surface-2); font-weight: 600; color: var(--text); }
.docs-not-found { color: var(--fail); font-size: var(--fs-sm); }

@media (max-width: 900px) {
  .docs-layout { grid-template-columns: 1fr; }
  .docs-sidebar { position: static; max-height: none; }
}
</style>
