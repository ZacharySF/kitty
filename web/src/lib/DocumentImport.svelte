<script lang="ts">
  import { onMount, tick } from 'svelte';
  import { Button } from '@nunera/bracket';
  import { api, type TaskRecord } from './api';
  let { notebookId, onimport, documentId = 0, documentRevision = 0, onbeforeimport, ondocumentimport, onimportstate }: {
    notebookId?: number; onimport?: (pageId: number) => void;
    documentId?: number; documentRevision?: number; onbeforeimport?: () => Promise<boolean>;
    ondocumentimport?: (documentId: number) => void | Promise<void>;
    onimportstate?: (busy: boolean) => void;
  } = $props();
  let importMode = $state<'notes' | 'document'>('notes');
  type Folder = { id: number; title: string; parent_id: number | null };
  type Book = { id: number; title: string; folder_id: number | null };
  type Match = { page_id: number; title: string; snippet: string; terms: string[] };
  type Imported = { page_id: number; title: string; original_id: string | null; matches: Match[] };
  type Upload = { name: string; path: string; data: string };
  let folders = $state<Folder[]>([]), books = $state<Book[]>([]);
  let folder = $state('all'), destination = $state('');
  let uploads = $state<Upload[]>([]), pasted = $state(''), title = $state('Pasted text');
  let preview = $state(''), matches = $state<Match[]>([]), results = $state<Imported[]>([]);
  let busy = $state(false), error = $state('');
  let selectedProp = $state<number | undefined>();
  $effect(() => {
    if (notebookId !== selectedProp && notebookId && books.some(book => book.id === notebookId)) {
      selectedProp = notebookId; destination = String(notebookId); folder = 'all'; matches = [];
    }
  });
  const available = $derived(folder === 'all' ? books : books.filter(book => String(book.folder_id ?? '') === folder));
  const bytesLimit = 12 * 1024 * 1024;
  onMount(() => { void load(); });
  async function load() {
    try {
      const data = await api<{ folders: Folder[]; notebooks: Book[] }>('/notes');
      folders = data.folders; books = data.notebooks;
    } catch (cause) { error = (cause as Error).message; }
  }
  function folderLabel(id: number): string {
    const parts: string[] = []; const seen = new Set<number>();
    let current = folders.find(item => item.id === id);
    while (current && !seen.has(current.id)) { seen.add(current.id); parts.unshift(current.title); current = folders.find(item => item.id === current?.parent_id); }
    return parts.join(' / ');
  }
  function target() {
    return { notebook_id: destination ? Number(destination) : null, folder_id: folder === 'all' || folder === '' ? null : Number(folder) };
  }
  async function task<T>(path: string, body: unknown): Promise<T> {
    const response = await api<{ task: TaskRecord }>(path, body);
    let record = response.task;
    while (record.status === 'running') { await new Promise(resolve => setTimeout(resolve, 300)); record = await api<TaskRecord>(`/tasks/${record.id}`); }
    if (record.status === 'failed') throw new Error(record.error || 'Local import failed');
    return record.result as T;
  }
  async function encode(file: File): Promise<Upload> {
    const data = await new Promise<string>((resolve, reject) => {
      const reader = new FileReader(); reader.onerror = () => reject(new Error('Unable to read selected file'));
      reader.onload = () => resolve(String(reader.result).split(',')[1]); reader.readAsDataURL(file);
    });
    return { name: file.name, path: file.webkitRelativePath || file.name, data };
  }
  async function choose(files: File[]) {
    if (busy || !files.length) return;
    busy = true; error = ''; results = []; preview = ''; matches = [];
    try {
      if (files.length > 100 || files.reduce((sum, file) => sum + file.size, 0) > bytesLimit) throw new Error('Choose at most 100 files totaling 12 MiB.');
      if (files.some(file => !/\.(md|markdown|txt|pdf)$/i.test(file.name))) throw new Error('Supported files: UTF-8 .md, .markdown, .txt and text-bearing .pdf. Remove other file types first.');
      uploads = await Promise.all(files.map(encode)); pasted = '';
      await inspect();
    } catch (cause) { uploads = []; error = (cause as Error).message; }
    finally { busy = false; }
  }
  async function inspect() {
    if (!uploads.length) return;
    const data = await task<{content: string; matches: Match[]}>('/documents/prepare', { file: uploads[0], ...target() });
    preview = data.content; matches = data.matches;
  }
  async function findMatches() {
    busy = true; error = '';
    try {
      if (uploads.length) await inspect();
      else { const data = await api<{matches: Match[]}>('/documents/topics', { text: pasted, ...target() }); preview = pasted; matches = data.matches; }
    } catch (cause) { error = (cause as Error).message; }
    finally { busy = false; }
  }
  function paste(event: ClipboardEvent) {
    const files = Array.from(event.clipboardData?.files || []);
    if (files.length) { event.preventDefault(); void choose(files); }
  }
  async function clipboard() {
    busy = true; error = '';
    try {
      if (!navigator.clipboard?.read) throw new Error('Clipboard file access is unavailable. Paste into the text field or choose files.');
      const items = await navigator.clipboard.read(); const files: File[] = []; const texts: string[] = [];
      for (const item of items) {
        if (item.types.includes('application/pdf')) { const blob = await item.getType('application/pdf'); files.push(new File([blob], 'Pasted document.pdf', {type: 'application/pdf'})); }
        else if (item.types.includes('text/plain')) texts.push(await (await item.getType('text/plain')).text());
      }
      busy = false;
      if (files.length) await choose(files);
      else if (texts.length) { uploads = []; pasted = texts.join('\n\n'); preview = ''; results = []; }
      else throw new Error('Clipboard has no PDF file or plain text. Use the file picker for other formats.');
    } catch (cause) { error = (cause as Error).message; }
    finally { busy = false; }
  }
  async function save() {
    let importingDocument = false;
    busy = true; error = '';
    try {
      if (importMode === 'document' && documentId) {
        if (onbeforeimport && !(await onbeforeimport())) return;
        await tick();
        onimportstate?.(true);
        importingDocument = true;
        const data = await task<{document: {id: number}}>('/documents/import', {
          document_id: documentId, revision: documentRevision,
          ...(uploads.length ? {files: uploads} : {text: pasted, title}),
        });
        uploads = []; pasted = ''; preview = ''; matches = []; results = [];
        await ondocumentimport?.(data.document.id);
        return;
      }
      const data = await task<{pages: Imported[]}>('/documents/import', { ...target(), ...(uploads.length ? {files: uploads} : {text: pasted, title}) });
      results = data.pages; uploads = []; pasted = ''; preview = ''; matches = [];
    } catch (cause) { error = (cause as Error).message; }
    finally { busy = false; if (importingDocument) onimportstate?.(false); }
  }
  function open(id: number) {
    if (onimport) onimport(id);
    else { try { sessionStorage.setItem('kitty.notes.open-page', String(id)); } catch { /* Event remains available. */ } location.hash = '#notes'; window.dispatchEvent(new CustomEvent('kitty-open-note', {detail: {page_id: id}})); }
  }
</script>

<section class="document-import" onpaste={paste} aria-label="Import documents">
  <h3>Import documents</h3>
  {#if documentId}<label>Import destination<select bind:value={importMode} disabled={busy}><option value="notes">New notes in selected notebook / folder</option><option value="document">Append to the currently open document</option></select></label>{/if}
  <p class="hint">UTF-8 Markdown / text and text-bearing PDF. Folder imports preserve their relative folders under your destination. No proprietary binary formats or scanned-PDF OCR.</p>
  {#if importMode === 'notes' || !documentId}<div class="destinations">
    <label>Destination folder<select bind:value={folder} disabled={busy} onchange={() => {destination = ''; matches = [];}}><option value="all">All notebooks / root folder</option><option value="">Root folder</option>{#each folders as item}<option value={String(item.id)}>{folderLabel(item.id)}</option>{/each}</select></label>
    <label>Destination notebook<select bind:value={destination} disabled={busy} onchange={() => matches = []}><option value="">Create / use “Imported documents” in chosen folder</option>{#each available as book}<option value={String(book.id)}>{book.title}{book.folder_id ? ` · ${folderLabel(book.folder_id)}` : ' · root'}</option>{/each}</select></label>
  </div>{:else}<p class="hint">Text extracted from every selected file is appended to this document. Existing document content and its notebook / folder are preserved; original files remain downloadable.</p>{/if}
  <div class="actions">
    <label class="picker">Choose files<input type="file" multiple accept=".md,.markdown,.txt,.pdf" disabled={busy} onchange={event => {void choose(Array.from(event.currentTarget.files || [])); event.currentTarget.value = '';}} /></label>
    <label class="picker">Choose Markdown folder<input type="file" multiple webkitdirectory disabled={busy} onchange={event => {void choose(Array.from(event.currentTarget.files || [])); event.currentTarget.value = '';}} /></label>
    <Button size="sm" variant="ghost" disabled={busy} onclick={clipboard}>Read clipboard</Button>
  </div>
  {#if uploads.length}<p>{uploads.length} file(s) selected. Preview shows {uploads[0].name}; all files are validated before any note is saved.</p><Button size="sm" variant="ghost" disabled={busy} onclick={() => {uploads = []; preview = ''; matches = [];}}>Clear files</Button>{:else}<label>Note title<input bind:value={title} maxlength="200" disabled={busy} /></label><label>Paste text or a PDF file<textarea bind:value={pasted} disabled={busy} rows="6" placeholder="Paste Markdown, plain text, or a clipboard PDF file here"></textarea></label>{/if}
  <div class="actions"><Button size="sm" variant="ghost" disabled={busy || (!uploads.length && !pasted.trim())} onclick={findMatches}>Preview & match topics in destination</Button><Button size="sm" disabled={busy || (!uploads.length && !pasted.trim())} onclick={save}>{busy ? 'Working locally…' : importMode === 'document' && documentId ? 'Append to open document' : 'Save as new notes'}</Button></div>
  {#if error}<p role="alert" class="error">{error}</p>{/if}
  {#if preview}<details open><summary>Extracted text preview</summary><pre>{preview}</pre></details>{/if}
  {#if preview}<h4>Matching local notes</h4>{#if !matches.length}<p class="hint">No shared topic keywords found in the selected destination.</p>{/if}{#each matches as match}<article><button onclick={() => open(match.page_id)}>{match.title}</button><p>{match.snippet}</p><small>{match.terms.join(' · ')}</small></article>{/each}{/if}
  {#each results as result}<article class="saved"><button onclick={() => open(result.page_id)}>Open imported note: {result.title}</button>{#if result.original_id}<a href={`/api/documents/originals/${result.original_id}`} download>Download retained original</a>{/if}{#each result.matches as match}<p><button onclick={() => open(match.page_id)}>{match.title}</button> — {match.snippet}</p>{/each}</article>{/each}
</section>
<style>
.document-import{display:grid;gap:12px;min-width:0}h3,h4,p{margin:0}h3{font-size:16px}h4{font-size:13px}.hint,small{color:var(--muted);font-size:12px;line-height:1.6}.destinations{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:12px}label{display:grid;gap:5px;font-size:12px;color:var(--muted)}input,select,textarea{background:var(--surface);color:var(--text);border:1px solid var(--border);border-radius:7px;padding:8px;min-width:0;font:inherit}textarea{font-family:monospace;resize:vertical}.actions{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.picker{position:relative;display:inline-flex;overflow:hidden;border:1px solid var(--border);border-radius:7px;padding:8px;color:var(--text);cursor:pointer}.picker input{position:absolute;inset:0;opacity:0;cursor:pointer;width:100%}pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:320px;overflow:auto;background:var(--surface);padding:12px;font-size:12px;border-radius:7px}summary{cursor:pointer}article{display:grid;gap:6px;padding:10px;border:1px solid var(--border);border-radius:7px}article button{border:0;background:none;padding:0;text-align:left;font:inherit;color:var(--accent);cursor:pointer}article p{font-size:12px;line-height:1.6}a{font-size:12px;color:var(--accent)}.error{color:var(--danger,#c44);font-size:13px}.saved{background:var(--surface)}
</style>
