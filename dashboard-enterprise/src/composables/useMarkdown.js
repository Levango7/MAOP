/**
 * useMarkdown — 极简 Markdown → HTML 渲染（无外部依赖）。
 *
 * 设计目标:
 *   - 不引入 marked / markdown-it 等重量级库，保持 dashboard 零依赖增量。
 *   - 覆盖后端 EvolutionNarrative 输出的 Markdown 子集:
 *     标题(#~####)、有序/无序列表、加粗、行内代码、代码块、引用、分隔线、段落。
 *   - 输出经过 DOMPurify 净化，防 XSS。
 *
 * 用法:
 *   const { render } = useMarkdown();
 *   const html = render('# Hello\n- item');
 *
 * 不是完整 Markdown 规范实现：不处理嵌套列表、表格、链接图片等复杂语法。
 * 如需完整渲染，后续可替换为 marked + dompurify。
 */
import DOMPurify from 'dompurify';

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

/** 行内格式化：加粗 + 行内代码。 */
function inline(s) {
  let out = escapeHtml(s);
  // 行内代码 `code` — 先处理，避免内部被加粗规则误伤
  out = out.replace(/`([^`]+)`/g, (_, code) => `<code class="md-code-inline">${code}</code>`);
  // 加粗 **text**
  out = out.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  return out;
}

/**
 * 将 Markdown 文本转为 HTML 片段。
 * @param {string} md Markdown 源文本
 * @returns {string} 净化后的 HTML
 */
function renderMarkdown(md) {
  if (!md || typeof md !== 'string') return '';
  const lines = md.replace(/\r\n/g, '\n').split('\n');
  const out = [];
  let i = 0;
  let inCodeBlock = false;
  let codeBuf = [];


  const flushParagraph = (buf) => {
    if (buf.length) {
      out.push('<p>' + buf.map(inline).join(' ') + '</p>');
      buf.length = 0;
    }
  };

  const paraBuf = [];

  while (i < lines.length) {
    const line = lines[i];

    // 代码块 ```lang ... ```
    if (/^```/.test(line)) {
      if (inCodeBlock) {
        out.push('<pre><code class="md-code-block">' + escapeHtml(codeBuf.join('\n')) + '</code></pre>');
        codeBuf = [];
        inCodeBlock = false;
      } else {
        flushParagraph(paraBuf);
        inCodeBlock = true;
      }
      i++;
      continue;
    }
    if (inCodeBlock) {
      codeBuf.push(line);
      i++;
      continue;
    }

    // 空行 → 段落分隔
    if (/^\s*$/.test(line)) {
      flushParagraph(paraBuf);
      i++;
      continue;
    }

    // 标题 # ~ ######
    const h = line.match(/^(#{1,6})\s+(.*)$/);
    if (h) {
      flushParagraph(paraBuf);
      const level = h[1].length;
      out.push(`<h${level}>${inline(h[2])}</h${level}>`);
      i++;
      continue;
    }

    // 分隔线 --- / ***
    if (/^(\s*[-*]\s*){3,}$/.test(line)) {
      flushParagraph(paraBuf);
      out.push('<hr />');
      i++;
      continue;
    }

    // 引用 >
    if (/^>\s?/.test(line)) {
      flushParagraph(paraBuf);
      const quoteBuf = [];
      while (i < lines.length && /^>\s?/.test(lines[i])) {
        quoteBuf.push(lines[i].replace(/^>\s?/, ''));
        i++;
      }
      out.push('<blockquote>' + quoteBuf.map(inline).join('<br />') + '</blockquote>');
      continue;
    }

    // 无序列表 - / *
    if (/^\s*[-*]\s+/.test(line)) {
      flushParagraph(paraBuf);
      const items = [];
      while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*[-*]\s+/, ''));
        i++;
      }
      out.push('<ul>' + items.map((it) => `<li>${inline(it)}</li>`).join('') + '</ul>');
      continue;
    }

    // 有序列表 1. / 2.
    if (/^\s*\d+\.\s+/.test(line)) {
      flushParagraph(paraBuf);
      const items = [];
      while (i < lines.length && /^\s*\d+\.\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*\d+\.\s+/, ''));
        i++;
      }
      out.push('<ol>' + items.map((it) => `<li>${inline(it)}</li>`).join('') + '</ol>');
      continue;
    }

    // 普通段落行
    paraBuf.push(line);
    i++;
  }

  flushParagraph(paraBuf);
  if (inCodeBlock) {
    // 未闭合代码块保底输出
    out.push('<pre><code class="md-code-block">' + escapeHtml(codeBuf.join('\n')) + '</code></pre>');
  }

  const raw = out.join('\n');
  // DOMPurify 净化：允许常见文档标签，禁用脚本/事件
  try {
    return DOMPurify.sanitize(raw, {
      ALLOWED_TAGS: ['h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'p', 'ul', 'ol', 'li', 'strong', 'em', 'code', 'pre', 'blockquote', 'hr', 'br', 'span'],
      ALLOWED_ATTR: ['class'],
    });
  } catch {
    // dompurify 在 SSR/测试环境可能不可用 — 返回转义后的原文作为保底
    return escapeHtml(md);
  }
}

export function useMarkdown() {
  return { render: renderMarkdown };
}