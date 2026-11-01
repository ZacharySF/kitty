<script lang="ts">
  import { onMount } from 'svelte';
  import { Popover } from '@nunera/bracket';
  import { api, type TaskRecord } from './api';
  import DocumentImport from './DocumentImport.svelte';
  import Icon from './Icon.svelte';
  import RichDocumentEditor from './RichDocumentEditor.svelte';
  import { documentHTML } from './notesDocument';
  import { useWorkspaceContext } from './workspaceContext.svelte';
  const pane = useWorkspaceContext();
  type Doc = {id: number; title: string; content: string; format: 'markdown' | 'text'; notebook_id: number | null; revision: number; updated_at: string};
  type Book = {id: number; title: string; folder_id: number | null};
  type Folder = {id: number; title: string; parent_id: number | null};
  type Match = {page_id: number; title: string; snippet: string; terms: string[]};
  type Topic = {term: string; count: number; snippet: string};
  /** Source share of the split, in percent; remembered per browser. */
  const SPLIT_KEY = 'kitty.documents.split', SPLIT_MIN = 28, SPLIT_MAX = 72, SPLIT_STEP = 4;
  let docs = $state<Doc[]>([]), books = $state<Book[]>([]), folders = $state<Folder[]>([]);
  let id = $state(0), revision = $state(1), title = $state('Untitled document'), content = $state(''), format = $state<'markdown' | 'text'>('markdown');
  let destination = $state(''), folder = $state(''), scope = $state('folder');
  let baseline = $state(''), busy = $state(false), error = $state(''), notice = $state('');
  let matches = $state<Match[]>([]), topics = $state<Topic[]>([]);
  let editor = $state<HTMLTextAreaElement>(), gutter = $state<HTMLDivElement>(), preview = $state<HTMLElement>(), panes = $state<HTMLDivElement>();
  let editing = $state<'rich' | 'source'>('source');
  let switching = $state(false), configuring = $state(false);
  let split = $state(storedSplit()), dragging = false;
  let originals = $state<{id: string; name: string}[]>([]);
  const signature = $derived(JSON.stringify({title, content, format, destination, folder}));
  const dirty = $derived(baseline !== signature);
  export function hasUnsavedChanges() { return dirty; }
  const available = $derived(books.filter(book => String(book.folder_id ?? '') === folder));
  const rendered = $derived(documentHTML(content, format));
  const rich = $derived(editing === 'rich' && format === 'markdown');
  const lineCount = $derived(content.split('\n').length);
  const words = $derived(content.split(/\s+/).filter(word => /[\p{L}\p{N}]/u.test(word)).length);
  const status = $derived(id ? `Revision ${revision}${dirty ? ' · unsaved edits' : ' · saved'}` : `Not saved${dirty ? ' · unsaved edits' : ''}`);
  onMount(() => {
    baseline = signature;
    void load();
    const leave = (event: BeforeUnloadEvent) => {if (dirty) {event.preventDefault(); event.returnValue = '';}};
    window.addEventListener('beforeunload', leave);
    return () => window.removeEventListener('beforeunload', leave);
  });
  async function load() {
    try {
      const [documents, notes] = await Promise.all([api<{documents: Doc[]}>('/documents'), api<{notebooks: Book[]; folders: Folder[]}>('/notes')]);
      docs = documents.documents; books = notes.notebooks; folders = notes.folders;
    } catch (cause) {error = (cause as Error).message;}
  }
  function label(id: number) {
    const parts: string[] = [], seen = new Set<number>(); let item = folders.find(folder => folder.id === id);
    while (item && !seen.has(item.id)) {seen.add(item.id); parts.unshift(item.title); item = folders.find(folder => folder.id === item?.parent_id);}
    return parts.join(' / ');
  }
  function edited(at: string) {
    const date = new Date(at);
    return Number.isNaN(date.getTime()) ? '' : date.toLocaleDateString(undefined, {month: 'short', day: 'numeric'});
  }
  async function open(documentId: number) {
    if (dirty && (!pane.active || !confirm('Discard unsaved document edits?'))) return;
    busy = true; error = ''; notice = '';
    try {
      const doc = await api<Doc>(`/documents/${documentId}`);
      id = doc.id; revision = doc.revision; title = doc.title; content = doc.content; format = doc.format;
      destination = String(doc.notebook_id ?? ''); folder = String(books.find(book => book.id === doc.notebook_id)?.folder_id ?? '');
      baseline = JSON.stringify({title: doc.title, content: doc.content, format: doc.format, destination, folder}); matches = []; topics = [];
      originals = (await api<{originals: typeof originals}>(`/documents/originals?document_id=${documentId}`)).originals;
    } catch (cause) {error = (cause as Error).message;}
    finally {busy = false;}
  }
  function fresh() {
    if (!pane.active) return;
    if (dirty && !confirm('Discard unsaved document edits?')) return;
    id = 0; revision = 1; title = 'Untitled document'; content = ''; format = 'markdown'; destination = ''; folder = ''; matches = []; topics = []; originals = []; error = ''; notice = '';
    baseline = JSON.stringify({title, content, format, destination, folder});
  }
  async function save(): Promise<boolean> {
    busy = true; error = ''; notice = '';
    try {
      if (new TextEncoder().encode(content).length > 512 * 1024) throw new Error('Document exceeds 512 KiB. Split it before saving.');
      if (!destination) {
        let book = books.find(book => book.title === 'Imported documents' && String(book.folder_id ?? '') === folder);
        if (!book) { book = await api<Book>('/notes/notebooks', {title: 'Imported documents', folder_id: folder ? Number(folder) : null}); books = [...books, book]; }
        destination = String(book.id);
      }
      const doc = await api<Doc>(id ? `/documents/${id}` : '/documents', {title, content, format, notebook_id: Number(destination), ...(id ? {revision} : {})});
      id = doc.id; revision = doc.revision; title = doc.title; baseline = JSON.stringify({title, content, format, destination, folder});
      await load(); notice = 'Saved locally.'; return true;
    } catch (cause) {error = (cause as Error).message; return false;}
    finally {busy = false;}
  }
  async function download() {
    if ((!id || dirty) && !(await save())) return;
    busy = true; error = '';
    try {
      const response = await fetch(`/api/documents/${id}/pdf`, {cache: 'no-store'});
      if (!response.ok) {const problem = await response.json(); throw new Error(problem.error || 'PDF export failed');}
      const blob = await response.blob(), url = URL.createObjectURL(blob), link = document.createElement('a');
      link.href = url; link.download = (title.replace(/[^A-Za-z0-9._-]+/g, '-').replace(/^\.+/, '').slice(0, 100) || 'document') + '.pdf'; link.click(); setTimeout(() => URL.revokeObjectURL(url), 30000);
      notice = 'PDF downloaded.';
    } catch (cause) {error = (cause as Error).message;}
    finally {busy = false;}
  }
  function openNote(pageId: number) {
    if (!pane.active) return;
    try {sessionStorage.setItem('kitty.notes.open-page', String(pageId));} catch {/* Optional deep-link storage. */}
    location.hash = '#notes'; window.dispatchEvent(new CustomEvent('kitty-open-note', {detail: {page_id: pageId}}));
  }
  async function makeNote() {
    if ((!id || dirty) && !(await save())) return;
    busy = true; error = '';
    try {
      let {task} = await api<{task: TaskRecord}>(`/documents/${id}/note`, {revision});
      while (task.status === 'running') {await new Promise(resolve => setTimeout(resolve, 300)); task = await api<TaskRecord>(`/tasks/${task.id}`);}
      if (task.status === 'failed') throw new Error(task.error || 'Note creation failed');
      const data = task.result as {pages: {page_id: number}[]}; notice = 'Note page created locally.'; openNote(data.pages[0].page_id);
    } catch (cause) {error = (cause as Error).message;}
    finally {busy = false;}
  }
  async function match() {
    busy = true; error = '';
    try {
      const data = await api<{matches: Match[]; topics: Topic[]}>('/documents/topics', {text: content, scope: scope === 'document' ? 'document' : 'folder', folder_id: folder ? Number(folder) : null});
      matches = data.matches; topics = data.topics;
    } catch (cause) {error = (cause as Error).message;}
    finally {busy = false;}
  }
  function jump(term: string) {
    if (!pane.active) return;
    editing = 'source';
    requestAnimationFrame(() => { if (!pane.active) return; const at = content.toLocaleLowerCase().indexOf(term.toLocaleLowerCase()); if (at >= 0 && editor) {editor.focus(); editor.setSelectionRange(at, at + term.length);} });
  }
  async function beforeImport() { return !dirty || await save(); }
  async function imported(documentId: number) { await open(documentId); notice = 'Extracted content appended to this document and saved locally.'; }
  function storedSplit() {
    try { const value = Number(localStorage.getItem(SPLIT_KEY)); return value >= SPLIT_MIN && value <= SPLIT_MAX ? value : 50; }
    catch { return 50; }
  }
  function resize(next: number) { split = Math.round(Math.min(SPLIT_MAX, Math.max(SPLIT_MIN, next))); }
  function remember() { try {localStorage.setItem(SPLIT_KEY, String(split));} catch {/* The split still works for this session. */} }
  function grab(event: PointerEvent) {
    if (event.button !== 0) return;
    const handle = event.currentTarget as HTMLElement;
    event.preventDefault(); handle.focus(); handle.setPointerCapture(event.pointerId); dragging = true;
  }
  function drag(event: PointerEvent) {
    if (!dragging || !panes) return;
    const box = panes.getBoundingClientRect(); resize(((event.clientX - box.left) / box.width) * 100);
  }
  function drop() { if (dragging) {dragging = false; remember();} }
  function nudge(event: KeyboardEvent) {
    const next = {ArrowLeft: split - SPLIT_STEP, ArrowRight: split + SPLIT_STEP, Home: SPLIT_MIN, End: SPLIT_MAX}[event.key];
    if (next === undefined) return;
    event.preventDefault(); resize(next); remember();
  }
  /** The preview follows the source proportionally; the line numbers follow it exactly. */
  function follow(source: HTMLElement) {
    if (preview) preview.scrollTop = (source.scrollTop / Math.max(1, source.scrollHeight - source.clientHeight)) * (preview.scrollHeight - preview.clientHeight);
    if (gutter && source === editor) gutter.scrollTop = source.scrollTop;
  }
</script>

<section class="documents-view" aria-labelledby="documents-title">
  <h1 id="documents-title" class="sr-only">Documents & PDF</h1>
  <header class="bar">
    <div class="doc-pill">
      <span class="fmt" title={format === 'markdown' ? 'Markdown' : 'Plain text'}>{format === 'markdown' ? 'MD' : 'TXT'}</span>
      <input class="doc-title" aria-label="Document title" bind:value={title} maxlength="200" disabled={busy} />
      <span class="switch">
        <Popover bind:open={switching} placement="bottom-end" arrow={false}>
          {#snippet trigger(props)}<button {...props} type="button" class="chev" aria-label="Switch document" title="Switch document"><span aria-hidden="true">⌄</span></button>{/snippet}
          <div class="menu" role="group" aria-label="Saved documents">
            {#each docs as doc (doc.id)}
              <button type="button" class:on={doc.id === id} aria-current={doc.id === id ? 'true' : undefined} disabled={busy} onclick={() => {switching = false; void open(doc.id);}}>
                <span class="fmt">{doc.format === 'markdown' ? 'MD' : 'TXT'}</span><span class="name">{doc.title}</span><small>{edited(doc.updated_at)}</small>
              </button>
            {:else}
              <p class="empty">Your saved documents appear here.</p>
            {/each}
            <button type="button" class="new" disabled={busy} onclick={() => {switching = false; fresh();}}>+ New document</button>
          </div>
        </Popover>
      </span>
    </div>
    <span class="eyebrow status" class:fresh={!id} class:dirty><i></i>{status}</span>
    <span class="grow"></span>
    <div class="seg" role="group" aria-label="Editor mode">
      <button type="button" aria-pressed={!rich} onclick={() => editing = 'source'}>Source</button>
      <button type="button" aria-pressed={rich} disabled={format !== 'markdown'} onclick={() => editing = 'rich'}>Rich text</button>
    </div>
    <Popover bind:open={configuring} placement="bottom-end" arrow={false}>
      {#snippet trigger(props)}<button {...props} type="button" class="pill icon-pill" aria-label="Document settings" title="Format and notes destination"><Icon name="settings" size={15} /></button>{/snippet}
      <div class="settings">
        <label>Format<select bind:value={format} disabled={busy}><option value="markdown">Markdown</option><option value="text">Plain text</option></select></label>
        <label>Notes destination folder<select bind:value={folder} disabled={busy} onchange={() => destination = ''}><option value="">Root folder</option>{#each folders as item}<option value={String(item.id)}>{label(item.id)}</option>{/each}</select></label>
        <label>Notes destination notebook<select bind:value={destination} disabled={busy}><option value="">Create / use “Imported documents”</option>{#each available as book}<option value={String(book.id)}>{book.title}</option>{/each}</select></label>
      </div>
    </Popover>
    <button type="button" class="pill" disabled={busy || !title.trim()} onclick={save}>Save{dirty ? ' changes' : ''}</button>
    <button type="button" class="pill" disabled={busy || !content.trim()} onclick={makeNote}>→ Note page</button>
    <button type="button" class="pill primary" disabled={busy || !title.trim()} onclick={download}><Icon name="download" size={14} />Export PDF</button>
  </header>
  {#if error}<p class="message error" role="alert">{error}</p>{/if}
  {#if notice}<p class="message" role="status">{notice}</p>{/if}
  <div class="panes" bind:this={panes} style:--split={`${split}%`}>
    <div class="src" id="documents-source">
      {#if rich}
        <div class="rich-pane" onscroll={event => follow(event.currentTarget)}><RichDocumentEditor value={content} onchange={value => content = value} disabled={busy}/></div>
      {:else}
        <div class="gutter" data-testid="line-numbers" aria-hidden="true" bind:this={gutter}>{#each { length: lineCount }, line}<span>{line + 1}</span>{/each}<span class="pad"></span></div>
        <textarea bind:this={editor} bind:value={content} onscroll={event => follow(event.currentTarget)} disabled={busy} wrap="off" spellcheck="true"
          aria-label={format === 'markdown' ? 'Markdown source' : 'Plain text source'}
          placeholder={format === 'markdown' ? '# Start writing\n\nYour Markdown stays editable.' : 'Start writing…'}></textarea>
      {/if}
    </div>
    <!-- A focusable separator with a value is the WAI-ARIA window splitter, an interactive widget. -->
    <!-- svelte-ignore a11y_no_noninteractive_tabindex, a11y_no_noninteractive_element_interactions -->
    <div class="divider" role="separator" aria-label="Resize source and preview" aria-orientation="vertical" aria-controls="documents-source"
      aria-valuemin={SPLIT_MIN} aria-valuemax={SPLIT_MAX} aria-valuenow={split} aria-valuetext={`Source ${split}%`} tabindex="0"
      onpointerdown={grab} onpointermove={drag} onpointerup={drop} onpointercancel={drop} onkeydown={nudge}><span></span></div>
    <section class="pv" aria-label="Document preview" bind:this={preview}>
      <div class="sheet"><h1>{title}</h1><div class="authored-preview">{@html rendered}</div></div>
    </section>
  </div>
  <footer class="foot">
    <span>{words} {words === 1 ? 'word' : 'words'} · {lineCount} {lineCount === 1 ? 'line' : 'lines'}</span>
    {#if topics.length}<span class="topics">Local topics {#each topics as topic}<button type="button" class="chip" title={topic.snippet} onclick={() => jump(topic.term)}>{topic.term} <small>{topic.count}</small></button>{/each}</span>{/if}
    <span class="grow"></span>
    <span class="drag-hint">Drag the divider or use ← →</span>
  </footer>
</section>
<div class="extras">
  <details class="connections"><summary>Local topics</summary>
    <div class="actions"><label>Match topics<select bind:value={scope}><option value="folder">Within selected notes folder</option><option value="document">Inside this document</option></select></label><button type="button" class="pill" disabled={busy || !content.trim()} onclick={match}>Find local topics</button></div>
    <p class="hint">Keyword matching only; no AI or network. Folder searches include subfolders; root searches all local notes.</p>
    {#each matches as item}<article><button type="button" onclick={() => openNote(item.page_id)}>{item.title}</button><p>{item.snippet}</p><small>{item.terms.join(' · ')}</small></article>{/each}
  </details>
  <details class="import"><summary>Import files, Markdown folders, or clipboard content</summary><DocumentImport notebookId={destination ? Number(destination) : undefined} documentId={id} documentRevision={revision} onbeforeimport={beforeImport} ondocumentimport={imported} onimportstate={value => busy = value}/>{#each originals as original}<p><a href={`/api/documents/originals/${original.id}`} download>Retained original: {original.name}</a></p>{/each}{#if !id}<p class="hint">Save this document first to append imported content directly to it.</p>{/if}</details>
</div>

<style>
  .documents-view { display: flex; flex-direction: column; overflow: hidden; color: var(--ink); border: 1px solid var(--hairline); border-radius: 14px; background: color-mix(in srgb, var(--solid-panel) 55%, transparent); }
  button { font: inherit; color: inherit; cursor: pointer; }
  button:disabled { cursor: default; }
  .grow { flex: 1; }

  /* Compact bar: document switcher, save state, mode, actions. */
  .bar { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; padding: 10px 14px; border-bottom: 1px solid var(--hairline); background: color-mix(in srgb, var(--solid-void) 50%, transparent); }
  .bar .pill { min-height: 30px; padding: 3px 12px; }
  .bar .pill:disabled { opacity: 0.55; }
  .icon-pill { width: 30px; justify-content: center; padding: 0 !important; }
  .doc-pill { display: flex; align-items: center; gap: 8px; min-width: 0; min-height: 32px; padding: 0 4px 0 10px; border: 1px solid var(--hairline); border-radius: 999px; background: color-mix(in srgb, var(--solid-panel) 40%, transparent); }
  .doc-pill:focus-within { border-color: color-mix(in srgb, var(--violet) 55%, var(--hairline)); }
  /* Outrank the global glass field fill: the title and source sit flush in their chrome. */
  .documents-view .bar .doc-title { width: clamp(140px, 22vw, 300px); min-width: 0; padding: 4px 0; border: 0; background: none; color: var(--ink); font: 600 13px var(--heading); outline: none; text-overflow: ellipsis; }
  .fmt { flex: none; padding: 0 5px; border-radius: 4px; background: var(--wash); color: var(--violet); font: 500 9px/16px var(--font-mono); letter-spacing: 0.08em; }
  .switch { display: inline-flex; }
  .chev { display: grid; place-items: center; width: 26px; height: 26px; padding: 0; border: 0; border-radius: 50%; background: none; color: var(--muted); }
  .chev:hover, .chev[aria-expanded="true"] { background: var(--wash); color: var(--ink); }
  /* Bracket's *-end placement pins the panel's bottom to the trigger; drop it below the bar instead. */
  .bar :global([data-placement] > [role="dialog"][data-state]) { top: calc(100% + 8px); bottom: auto; right: 0; left: auto; padding: 6px; }
  .menu { display: grid; gap: 2px; width: 300px; max-height: min(420px, 60dvh); overflow: auto; }
  .menu button { display: flex; align-items: center; gap: 8px; padding: 7px 8px; border: 0; border-radius: 8px; background: none; font-size: 13px; text-align: left; }
  .menu button:hover:not(:disabled), .menu button.on { background: var(--wash); }
  .menu .name { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .menu small { margin-left: auto; flex: none; color: var(--subtle); font: 9.5px var(--font-mono); }
  .menu .empty { margin: 0; padding: 7px 8px; color: var(--muted); font-size: 12px; }
  .menu .new { margin-top: 2px; color: var(--muted); border-top: 1px solid var(--hairline) !important; border-radius: 0 0 8px 8px !important; }
  .status { display: flex; align-items: center; gap: 6px; color: var(--subtle); white-space: nowrap; }
  .status i { width: 6px; height: 6px; border-radius: 50%; background: var(--success); }
  .status.fresh i { background: var(--subtle); }
  .status.dirty i { background: var(--warning); }
  .seg { display: flex; padding: 2px; border: 1px solid var(--hairline); border-radius: 999px; }
  .seg button { padding: 3px 11px; border: 0; border-radius: 999px; background: none; color: var(--muted); font-size: 12px; }
  .seg button[aria-pressed="true"] { background: var(--ink); color: var(--solid-void); }
  .seg button:disabled { opacity: 0.45; }
  .settings { display: grid; gap: 10px; width: 280px; padding: 6px; }
  .settings label, .actions label { display: grid; gap: 4px; color: var(--muted); font-size: 11.5px; }
  select { min-width: 0; padding: 6px 8px; border: 1px solid var(--border); border-radius: 7px; background: var(--surface); color: var(--ink); font: inherit; font-size: 13px; }
  .message { max-width: none; margin: 0; padding: 6px 14px; border-bottom: 1px solid var(--hairline); color: var(--muted); font-size: 12px; }
  .message.error { color: var(--error); }

  /* Resizable split: numbered source | divider | preview. */
  .panes { height: clamp(360px, calc(100dvh - 290px), 1400px); display: grid; grid-template-columns: var(--split) 9px minmax(0, 1fr); grid-template-rows: minmax(0, 1fr); }
  .src { position: relative; display: flex; min-width: 0; min-height: 0; overflow: hidden; background: color-mix(in srgb, var(--solid-void) 45%, transparent); }
  .gutter { flex: none; display: flex; flex-direction: column; padding: 14px 8px 0 12px; color: var(--subtle); font: 12.5px/1.75 var(--font-mono); text-align: right; user-select: none; overflow: hidden; }
  .gutter .pad { flex: 0 0 40px; }
  .documents-view .panes .src > textarea { flex: 1; min-width: 0; margin: 0; padding: 14px 14px 14px 4px; border: 0; border-radius: 0; resize: none; background: transparent; color: var(--ink); font: 12.5px/1.75 var(--font-mono); outline: none; scrollbar-width: thin; }
  .src textarea::placeholder { color: var(--subtle); }
  .rich-pane { flex: 1; min-width: 0; overflow: auto; scrollbar-width: thin; }
  .rich-pane :global(.rich-document) { border: 0; border-radius: 0; }
  .rich-pane :global(.formatting) { position: sticky; top: 0; z-index: 1; }
  .divider { display: grid; place-items: center; cursor: col-resize; touch-action: none; border-inline: 1px solid var(--hairline); outline: none; }
  /* The grip never takes the pointer: the separator itself is the target, so the app keeps focus on it. */
  .divider span { width: 3px; height: 34px; border-radius: 3px; background: var(--hairline); pointer-events: none; transition: background-color 140ms, height 140ms; }
  .divider:hover span, .divider:focus-visible span { height: 52px; background: var(--violet); }
  .pv { overflow: auto; padding: 18px 22px; scrollbar-width: thin; overflow-wrap: anywhere; }
  .sheet { max-width: 640px; margin: 0 auto; }
  .sheet > h1 { margin: 0 0 18px; font-size: 24px; }
  .authored-preview { font-size: 13.5px; line-height: 1.7; }
  .authored-preview :global(p) { max-width: none; min-height: 1.5em; margin: 0 0 9px; color: var(--ink); white-space: pre-wrap; }
  .authored-preview :global(:is(h2, h3, h4, h5, h6)) { margin: 16px 0 8px; }
  .authored-preview :global(:is(ul, ol)) { padding-left: 24px; }
  .authored-preview :global(blockquote) { margin: 0 0 9px; padding-left: 12px; border-left: 3px solid var(--accent); color: var(--muted); }
  .authored-preview :global(pre) { padding: 10px; border-radius: 8px; background: var(--surface); white-space: pre-wrap; }
  .authored-preview :global(code) { font-family: var(--font-mono); }
  .foot { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; padding: 6px 14px; border-top: 1px solid var(--hairline); color: var(--subtle); font: 10px var(--font-mono); letter-spacing: 0.08em; text-transform: uppercase; }
  .topics { display: flex; flex-wrap: wrap; align-items: center; gap: 5px; }
  .chip { padding: 0 8px; border: 1px solid var(--hairline); border-radius: 999px; background: none; color: var(--violet); font: 10px/17px var(--font-mono); text-transform: none; letter-spacing: 0; }
  .chip small { color: var(--subtle); }

  /* Topic matching and imports stay one click below the editor. */
  .extras { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); align-items: start; gap: 12px; margin-top: 12px; }
  .extras details { display: grid; gap: 10px; min-width: 0; }
  .actions { display: flex; flex-wrap: wrap; align-items: end; gap: 8px; margin-top: 10px; }
  .hint { margin: 8px 0; color: var(--muted); font-size: 12px; line-height: 1.6; }
  article { display: grid; gap: 6px; margin-top: 8px; padding: 10px; border: 1px solid var(--hairline); border-radius: 8px; }
  article button { padding: 0; border: 0; background: none; color: var(--accent); text-align: left; }
  article p, article small { margin: 0; color: var(--muted); font-size: 12px; line-height: 1.6; }
  .import a { color: var(--accent); font-size: 12px; }

  @media (max-width: 760px) {
    .panes { height: auto; grid-template-columns: minmax(0, 1fr); grid-template-rows: 360px 360px; }
    .divider, .drag-hint { display: none; }
    .pv { border-top: 1px solid var(--hairline); }
    .extras { grid-template-columns: minmax(0, 1fr); }
  }
  @media (prefers-reduced-motion: reduce) {
    .divider span { transition: none; }
  }
</style>
