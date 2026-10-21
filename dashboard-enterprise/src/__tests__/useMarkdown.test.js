import { describe, it, expect } from 'vitest';
import { useMarkdown } from '../composables/useMarkdown.js';

const { render } = useMarkdown();

describe('useMarkdown', () => {
  it('renders GFM tables', () => {
    const html = render('| Name | State |\n| --- | ---: |\n| run-1 | ok |');
    expect(html).toContain('<table>');
    expect(html).toContain('<th>Name</th>');
    expect(html).toContain('<td>run-1</td>');
    expect(html).not.toContain('| Name |');
  });

  it('escapes code blocks instead of parsing tags inside them', () => {
    const html = render('```js\nif (a < b) { return "<b>"; }\n```');
    expect(html).toContain('&lt;b&gt;');
    expect(html).not.toContain('<b>');
    expect(html).toContain('if (a &lt; b)');
  });

  it('renders an unclosed streaming code fence safely', () => {
    const html = render('```python\nprint("<x>")');
    expect(html).toContain('<pre><code');
    expect(html).toContain('&lt;x&gt;');
  });

  it('keeps markdown metacharacters inside inline code literal', () => {
    const html = render('use `**not bold**` here');
    expect(html).toContain('<code class="md-code-inline">**not bold**</code>');
    expect(html).not.toContain('<strong>not bold</strong>');
  });

  it('renders http links with safe rel', () => {
    const html = render('see [docs](https://example.com/a?b=1)');
    expect(html).toContain('href="https://example.com/a?b=1"');
    expect(html).toContain('rel="noopener noreferrer"');
  });

  it('refuses javascript: links', () => {
    const html = render('[click](javascript:alert(1))');
    expect(html).not.toContain('href="javascript:');
    expect(html).not.toContain('<a');
  });

  it('sanitizes raw HTML in model output', () => {
    const html = render('<img src=x onerror="alert(1)">hello');
    expect(html).not.toContain('<img');
    expect(html).toContain('hello');
  });

  it('preserves single newlines only when breaks is enabled', () => {
    expect(render('a\nb')).toContain('<p>a b</p>');
    expect(render('a\nb', { breaks: true })).toMatch(/<p>a<br\s*\/?>b<\/p>/);
  });

  it('renders headings and lists', () => {
    const html = render('## Title\n- one\n- two');
    expect(html).toContain('<h2>Title</h2>');
    expect(html).toContain('<li>one</li>');
  });
});
