<script lang="ts">
  import { documentHTML, documentMarkdown } from './notesDocument';
  let {value, onchange, disabled = false}: {value: string; onchange: (value: string) => void; disabled?: boolean} = $props();
  let surface = $state<HTMLDivElement>(), last = '';
  $effect(() => {
    if (surface && value !== last) { surface.innerHTML = documentHTML(value); last = value; }
  });
  function changed() { if (!surface) return; last = documentMarkdown(surface); onchange(last); }
  function command(name: string, argument?: string) {
    surface?.focus(); document.execCommand(name, false, argument); changed();
  }
  function paste(event: ClipboardEvent) {
    // Use real clipboard text only. Do not let foreign HTML introduce scripts/assets.
    event.preventDefault(); command('insertText', event.clipboardData?.getData('text/plain') || '');
  }
  function keyboard(event: KeyboardEvent) {
    if (disabled || event.isComposing) return;
    if (event.key === 'Tab') { event.preventDefault(); command(event.shiftKey ? 'outdent' : 'indent'); }
    if ((event.ctrlKey || event.metaKey) && ['b', 'i'].includes(event.key.toLowerCase())) {
      event.preventDefault(); command(event.key.toLowerCase() === 'b' ? 'bold' : 'italic');
    }
  }
</script>
<div class="rich-document">
  <div class="formatting" role="toolbar" tabindex="-1" aria-label="Document formatting" onmousedown={event => {if (event.target instanceof HTMLButtonElement) event.preventDefault();}}>
    <button type="button" {disabled} onclick={() => command('bold')} title="Bold (Ctrl+B)"><strong>B</strong></button>
    <button type="button" {disabled} onclick={() => command('italic')} title="Italic (Ctrl+I)"><em>I</em></button>
    <button type="button" {disabled} onclick={() => command('formatBlock', 'p')}>Paragraph</button>
    <button type="button" {disabled} onclick={() => command('formatBlock', 'h1')}>Heading 1</button>
    <button type="button" {disabled} onclick={() => command('formatBlock', 'h2')}>Heading 2</button>
    <button type="button" {disabled} onclick={() => command('insertUnorderedList')}>• Bullets</button>
    <button type="button" {disabled} onclick={() => command('insertOrderedList')}>1. Numbered</button>
    <button type="button" {disabled} onclick={() => command('indent')}>Nest →</button>
    <button type="button" {disabled} onclick={() => command('outdent')}>← Unnest</button>
    <button type="button" {disabled} onclick={() => command('formatBlock', 'blockquote')}>Quote</button>
  </div>
  <div class="rich-surface" bind:this={surface} contenteditable={!disabled} tabindex={disabled ? -1 : 0} role="textbox" aria-label="Rich document editor" aria-multiline="true" spellcheck="true" oninput={changed} onpaste={paste} onkeydown={keyboard} ondrop={event => event.preventDefault()}></div>
  <p class="hint">Rich text stays portable Markdown. Enter continues a list; Tab / Shift+Tab nests and unnests. Switch to source for code fences and exact Markdown.</p>
</div>
<style>
  .rich-document{min-width:0;border:1px solid var(--border);border-radius:8px;overflow:hidden}.formatting{display:flex;gap:4px;flex-wrap:wrap;padding:8px;background:var(--surface);border-bottom:1px solid var(--border)}.formatting button{font:inherit;font-size:12px;padding:6px 9px;border:1px solid var(--border);border-radius:5px;background:var(--bg);color:var(--ink);cursor:pointer}.formatting button:hover{border-color:var(--accent)}.formatting button:disabled{opacity:.5;cursor:default}.rich-surface{padding:28px;min-height:430px;outline:none;background:var(--bg);color:var(--ink);font-size:15px;line-height:1.8;overflow-wrap:anywhere}.rich-surface:focus{box-shadow:inset 0 0 0 2px color-mix(in srgb,var(--accent) 25%,transparent)}.rich-surface :global(p){min-height:1.6em;margin:0 0 8px}.rich-surface :global(h1){font-size:1.8em}.rich-surface :global(h2){font-size:1.4em}.rich-surface :global(ul),.rich-surface :global(ol){padding-left:26px}.rich-surface :global(blockquote){border-left:3px solid var(--accent);padding-left:16px;color:var(--muted)}.rich-surface :global(pre){white-space:pre-wrap;font-size:13px;background:var(--surface);padding:12px}.hint{margin:0;padding:10px;color:var(--muted);font-size:11px;border-top:1px solid var(--border)}
</style>
