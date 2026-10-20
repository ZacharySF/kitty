// Local Markdown subset shared by the rich editor and preview. Raw HTML is text.
export function escapeDocumentText(text: string): string {
  return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}
export function inlineDocument(text: string): string {
  const tokens = text.split(/(`[^`\n]+`|\*\*\*[^*\n]+\*\*\*|___[^_\n]+___|\*\*[^*\n]+\*\*|__[^_\n]+__|\*[^*\n]+\*|_[^_\n]+_)/g);
  return tokens.map(token => {
    if (/^`[^`]+`$/.test(token)) return `<code>${escapeDocumentText(token.slice(1, -1))}</code>`;
    if (/^(\*\*\*[^*]+\*\*\*|___[^_]+___)$/.test(token)) return `<strong><em>${escapeDocumentText(token.slice(3, -3))}</em></strong>`;
    if (/^(\*\*[^*]+\*\*|__[^_]+__)$/.test(token)) return `<strong>${escapeDocumentText(token.slice(2, -2))}</strong>`;
    if (/^(\*[^*]+\*|_[^_]+_)$/.test(token)) return `<em>${escapeDocumentText(token.slice(1, -1))}</em>`;
    return escapeDocumentText(token);
  }).join('');
}
export function documentHTML(content: string, format: 'markdown' | 'text' = 'markdown'): string {
  if (format === 'text') return content.split('\n').map(line => `<p>${escapeDocumentText(line) || '<br>'}</p>`).join('');
  const html: string[] = [], lists: {indent: number; tag: string}[] = [];
  let fenced = false, code: string[] = [];
  function closeList() { const list = lists.pop(); if (list) html.push(`</li></${list.tag}>`); }
  function closeLists() { while (lists.length) closeList(); }
  for (const line of content.split('\n')) {
    if (/^\s*```/.test(line)) {
      closeLists();
      if (fenced) { html.push(`<pre><code>${escapeDocumentText(code.join('\n'))}</code></pre>`); code = []; }
      fenced = !fenced; continue;
    }
    if (fenced) { code.push(line); continue; }
    const list = /^(\s*)([-+*]|\d+\.)\s+(.*)$/.exec(line);
    if (list) {
      const indent = list[1].replace(/\t/g, '  ').length, tag = /^\d/.test(list[2]) ? 'ol' : 'ul';
      while (lists.length && lists[lists.length - 1].indent > indent) closeList();
      if (lists.length && lists[lists.length - 1].indent === indent && lists[lists.length - 1].tag !== tag) closeList();
      if (!lists.length || lists[lists.length - 1].indent < indent) { html.push(`<${tag}><li>`); lists.push({indent, tag}); }
      else html.push('</li><li>');
      html.push(inlineDocument(list[3])); continue;
    }
    closeLists();
    const heading = /^(#{1,6})\s+(.*)$/.exec(line), quote = /^>\s?(.*)$/.exec(line);
    if (heading) html.push(`<h${heading[1].length}>${inlineDocument(heading[2])}</h${heading[1].length}>`);
    else if (quote) html.push(`<blockquote>${inlineDocument(quote[1]) || '<br>'}</blockquote>`);
    else html.push(`<p>${inlineDocument(line) || '<br>'}</p>`);
  }
  closeLists();
  if (fenced) html.push(`<pre><code>${escapeDocumentText(code.join('\n'))}</code></pre>`);
  return html.join('');
}
// Serialize only supported formatting; pasted links/images/HTML never become assets.
export function documentMarkdown(root: HTMLElement): string {
  function inline(node: Node): string {
    if (node.nodeType === Node.TEXT_NODE) return (node.textContent || '').replace(/\u00a0/g, ' ');
    if (!(node instanceof HTMLElement)) return '';
    const text = Array.from(node.childNodes).map(inline).join('');
    if (node.tagName === 'BR') return '\n';
    if (['STRONG', 'B'].includes(node.tagName)) return text ? `**${text}**` : '';
    if (['EM', 'I'].includes(node.tagName)) return text ? `*${text}*` : '';
    if (node.tagName === 'CODE') return `\`${text}\``;
    return text;
  }
  function block(node: Node, depth = 0): string {
    if (!(node instanceof HTMLElement)) return inline(node);
    if (['UL', 'OL'].includes(node.tagName)) return Array.from(node.children).map((item, index) => {
      const body = Array.from(item.childNodes).filter(child => !(child instanceof HTMLElement && ['UL', 'OL'].includes(child.tagName))).map(inline).join('').replace(/\n+$/, '');
      const nested = Array.from(item.children).filter(child => ['UL', 'OL'].includes(child.tagName)).map(child => block(child, depth + 1)).join('');
      return `${'  '.repeat(depth)}${node.tagName === 'OL' ? `${index + 1}.` : '-'} ${body}\n${nested}`;
    }).join('');
    if (node.tagName === 'PRE') return `\`\`\`\n${node.textContent || ''}\n\`\`\`\n`;
    if (/^H[1-6]$/.test(node.tagName)) return `${'#'.repeat(Number(node.tagName[1]))} ${inline(node).trim()}\n`;
    if (node.tagName === 'BLOCKQUOTE') return inline(node).trim().split('\n').map(line => `> ${line}`).join('\n') + '\n';
    if (['P', 'DIV'].includes(node.tagName)) {
      if (Array.from(node.children).some(child => ['DIV', 'P', 'UL', 'OL'].includes(child.tagName))) return Array.from(node.childNodes).map(child => block(child, depth)).join('') + '\n';
      return inline(node).replace(/\n$/, '') + '\n';
    }
    return inline(node);
  }
  return Array.from(root.childNodes).map(node => block(node)).join('').replace(/\n$/, '');
}
