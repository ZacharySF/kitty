<script lang="ts">
  import { onMount } from 'svelte';
  import { Button } from '@nunera/bracket';
  import { api, type TaskRecord } from './api';
  import DocumentImport from './DocumentImport.svelte';
  import RichDocumentEditor from './RichDocumentEditor.svelte';
  import { documentHTML } from './notesDocument';
  import { useWorkspaceContext } from './workspaceContext.svelte';
  const pane = useWorkspaceContext();
  type Doc = {id: number; title: string; content: string; format: 'markdown' | 'text'; notebook_id: number | null; revision: number; updated_at: string};
  type Book = {id: number; title: string; folder_id: number | null};
  type Folder = {id: number; title: string; parent_id: number | null};
  type Match = {page_id: number; title: string; snippet: string; terms: string[]};
  type Topic = {term: string; count: number; snippet: string};
  let docs = $state<Doc[]>([]), books = $state<Book[]>([]), folders = $state<Folder[]>([]);
  let id = $state(0), revision = $state(1), title = $state('Untitled document'), content = $state(''), format = $state<'markdown' | 'text'>('markdown');
  let destination = $state(''), folder = $state(''), scope = $state('folder');
  let baseline = $state(''), busy = $state(false), error = $state(''), notice = $state('');
  let matches = $state<Match[]>([]), topics = $state<Topic[]>([]);
  let editor = $state<HTMLTextAreaElement>();
  let editing = $state<'rich' | 'source'>('rich');
  let originals = $state<{id: string; name: string}[]>([]);
  const signature = $derived(JSON.stringify({title, content, format, destination, folder}));
  const dirty = $derived(baseline !== signature);
  export function hasUnsavedChanges() { return dirty; }
  const available = $derived(books.filter(book => String(book.folder_id ?? '') === folder));
  const rendered = $derived(documentHTML(content, format));
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
</script>

<section class="documents-view">
  <header><div><h1>Documents & PDF</h1><p>Write locally, export a real PDF, or turn portable files into editable notes.</p></div><Button onclick={fresh} disabled={busy}>New document</Button></header>
  {#if error}<p class="error" role="alert">{error}</p>{/if}
  {#if notice}<p role="status" class="notice">{notice}</p>{/if}
  <div class="workspace">
    <aside aria-label="Saved documents"><h2>Saved documents</h2>{#if !docs.length}<p class="hint">Your saved documents appear here.</p>{/if}{#each docs as doc}<button class:active={doc.id === id} disabled={busy} onclick={() => open(doc.id)}>{doc.title}<small>{doc.format === 'markdown' ? 'Markdown' : 'Plain text'}</small></button>{/each}</aside>
    <main>
      <div class="metadata"><label>Document title<input bind:value={title} maxlength="200" disabled={busy} /></label><label>Format<select bind:value={format} disabled={busy}><option value="markdown">Markdown</option><option value="text">Plain text</option></select></label></div>
      <div class="metadata"><label>Notes destination folder<select bind:value={folder} disabled={busy} onchange={() => destination = ''}><option value="">Root folder</option>{#each folders as item}<option value={String(item.id)}>{label(item.id)}</option>{/each}</select></label><label>Notes destination notebook<select bind:value={destination} disabled={busy}><option value="">Create / use “Imported documents”</option>{#each available as book}<option value={String(book.id)}>{book.title}</option>{/each}</select></label></div>
      <div class="actions"><Button disabled={busy || !title.trim()} onclick={save}>Save{dirty ? ' changes' : ''}</Button><Button disabled={busy || !title.trim()} variant="ghost" onclick={download}>Download PDF</Button><Button disabled={busy || !content.trim()} variant="ghost" onclick={makeNote}>Create note page</Button><span class="hint">{id ? `Revision ${revision}` : 'Not saved'}{dirty ? ' · unsaved edits' : ''}</span></div>
      <div class="actions" role="group" aria-label="Editor mode"><Button size="sm" variant={editing === 'rich' ? 'outline' : 'ghost'} disabled={format !== 'markdown'} onclick={() => editing = 'rich'}>Rich text & outline</Button><Button size="sm" variant={editing === 'source' ? 'outline' : 'ghost'} onclick={() => editing = 'source'}>Markdown / text source</Button></div>
      <div class="editor-preview">
        {#if editing === 'rich' && format === 'markdown'}<RichDocumentEditor value={content} onchange={value => content = value} disabled={busy}/>{:else}<label class="source">Editable source<textarea bind:this={editor} bind:value={content} disabled={busy} placeholder={format === 'markdown' ? '# Start writing\n\nYour Markdown stays editable.' : 'Start writing…'} spellcheck="true"></textarea></label>{/if}
        <section class="preview" aria-label="Document preview"><h2>Preview</h2><h1>{title}</h1><div class="authored-preview">{@html rendered}</div></section>
      </div>
      <section class="connections"><div class="actions"><label>Match topics<select bind:value={scope}><option value="folder">Within selected notes folder</option><option value="document">Inside this document</option></select></label><Button size="sm" variant="ghost" disabled={busy || !content.trim()} onclick={match}>Find local topics</Button></div><p class="hint">Keyword matching only; no AI or network. Folder searches include subfolders; root searches all local notes.</p>{#if topics.length}<div class="topics">{#each topics as topic}<button title={topic.snippet} onclick={() => jump(topic.term)}>{topic.term} <small>{topic.count}</small></button>{/each}</div>{/if}{#each matches as item}<article><button onclick={() => openNote(item.page_id)}>{item.title}</button><p>{item.snippet}</p><small>{item.terms.join(' · ')}</small></article>{/each}</section>
      <details class="import"><summary>Import files, Markdown folders, or clipboard content</summary><DocumentImport notebookId={destination ? Number(destination) : undefined} documentId={id} documentRevision={revision} onbeforeimport={beforeImport} ondocumentimport={imported} onimportstate={value => busy = value}/>{#each originals as original}<p><a href={`/api/documents/originals/${original.id}`} download>Retained original: {original.name}</a></p>{/each}{#if !id}<p class="hint">Save this document first to append imported content directly to it.</p>{/if}</details>
    </main>
  </div>
</section>
<style>
.authored-preview :global(p){min-height:1.5em;margin:0 0 9px}.authored-preview :global(ul),.authored-preview :global(ol){padding-left:24px}.authored-preview :global(blockquote){border-left:3px solid var(--accent);padding-left:12px;color:var(--muted)}.authored-preview :global(pre){white-space:pre-wrap;background:var(--surface);padding:10px}.authored-preview :global(code){font-family:monospace}
.documents-view{padding:24px;display:grid;gap:20px;min-height:0;overflow:auto}header{display:flex;gap:16px;align-items:center;justify-content:space-between}h1,h2,p{margin:0}header h1{font-size:23px}header p,.hint{color:var(--muted);font-size:12px;line-height:1.6}header p{margin-top:6px}.workspace{display:grid;grid-template-columns:220px minmax(0,1fr);gap:20px;min-height:0}aside{border-right:1px solid var(--border);padding-right:16px;display:flex;flex-direction:column;gap:7px;max-height:85vh;overflow:auto}aside h2{font-size:12px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);margin-bottom:8px}aside button{background:none;border:1px solid transparent;border-radius:7px;padding:10px;text-align:left;cursor:pointer;color:var(--text);font:inherit;overflow-wrap:anywhere}aside button.active{background:var(--surface);border-color:var(--border)}aside small{display:block;color:var(--muted);font-size:10px;margin-top:4px}main{min-width:0;display:grid;gap:16px}.metadata{display:grid;grid-template-columns:minmax(0,2fr) minmax(160px,1fr);gap:12px}label{display:grid;gap:6px;font-size:12px;color:var(--muted);min-width:0}input,select,textarea{font:inherit;min-width:0;border:1px solid var(--border);border-radius:7px;background:var(--surface);color:var(--text);padding:9px}.actions{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.editor-preview{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:16px;min-height:420px}.source textarea{font:13px/1.7 monospace;resize:vertical;min-height:420px;height:100%}.preview{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:20px;max-height:650px;overflow:auto;overflow-wrap:anywhere}.preview>h2:first-child{font-size:11px;text-transform:uppercase;color:var(--muted);margin:0 0 20px}.preview h1{font-size:24px;margin:0 0 20px}.preview :global(p){white-space:pre-wrap;line-height:1.7;font-size:13px}.preview :global(h2),.preview :global(h3),.preview :global(h4),.preview :global(h5),.preview :global(h6){margin:16px 0 8px}.connections{display:grid;gap:10px;border-top:1px solid var(--border);padding-top:16px}.topics{display:flex;gap:7px;flex-wrap:wrap}.topics button{border:1px solid var(--border);border-radius:20px;padding:6px 10px;background:var(--surface);color:var(--text);cursor:pointer}.topics small{color:var(--muted)}article{border:1px solid var(--border);border-radius:8px;padding:10px;display:grid;gap:7px}article button{border:0;padding:0;text-align:left;color:var(--accent);background:none;cursor:pointer;font:inherit}article p,article small{font-size:12px;color:var(--muted);line-height:1.6}.error{color:var(--danger,#c44);font-size:13px}.notice{color:var(--muted);font-size:12px}.import{border-top:1px solid var(--border);padding-top:16px}.import summary{cursor:pointer;font-size:13px;margin-bottom:16px}@media(max-width:1000px){.workspace{grid-template-columns:160px minmax(0,1fr)}.editor-preview{grid-template-columns:1fr}.preview{max-height:400px}}@media(max-width:650px){.documents-view{padding:14px}.workspace{grid-template-columns:1fr}aside{border:0;max-height:150px}.metadata{grid-template-columns:1fr}}
</style>
