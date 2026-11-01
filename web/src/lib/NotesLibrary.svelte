<script module lang="ts">
  export type Folder = { id: number; title: string; parent_id: number | null; revision: number;position?:number };
  export type Notebook = { id: number; title: string; subject: string; project: string; folder_id: number | null; revision: number;position?:number };
  export type PageSummary = { id: number; notebook_id: number; title: string; revision: number;favorite?:boolean;last_opened?:string|null;position?:number };
</script>

<script lang="ts">
  import { untrack } from "svelte";
  import { Button } from "@nunera/bracket";
  import { api } from "./api";
  import { BLOCK_DRAG_TYPE } from "./BlockEditor.svelte";
  let { folders, notebooks, pages, notebookId, pageId, search, folderFocus = null, busy = false, onsearch, onrefresh, onopenpage, onselectnotebook, onnewpage, onbeforeswitch, onerror, ondeletepage, onblocksdrop }: {
    folders: Folder[]; notebooks: Notebook[]; pages: PageSummary[];
    notebookId: number; pageId: number; search: string; folderFocus?: number | null; busy?: boolean;
    onsearch: (value: string) => void; onrefresh: () => Promise<void>;
    onopenpage: (id: number) => Promise<void>; onselectnotebook: (id: number) => Promise<void>;
    onnewpage: () => Promise<void>; onbeforeswitch: () => Promise<boolean>; onerror: (error: string) => void;
    ondeletepage: (id:number)=>Promise<void>;
    /** Blocks dragged from the editor onto a page: true when the drop carried blocks. */
    onblocksdrop?: (page: number, event: DragEvent) => boolean;
  } = $props();
  let editor = $state<"folder" | "notebook" | null>(null);
  let editId = $state(0), editRevision = $state(0), name = $state(""), parentId = $state(""), subject = $state(""), project = $state("");
  let saving = $state(false), selectedFolder = $state<number | null>(null);
  let collapsed = $state<Set<number>>(new Set());
  let dragged: {kind:"pages"|"notebooks"|"folders";id:number}|null = null;
  let blockTarget = $state(0);
  let tags=$state<{page_id:number;title:string;text:string;tags:string[]}[]>([]);
  let pageMenu=$state<{id:number;x:number;y:number}|null>(null);
  async function drop(event:DragEvent,kind:"pages"|"notebooks"|"folders",id:number) {
    event.preventDefault();
    if(!dragged||dragged.kind!==kind||dragged.id===id)return;
    const values=kind==="pages"?pages:kind==="notebooks"?notebooks:folders;
    const ids=values.map(value=>value.id).filter(value=>value!==dragged!.id);
    ids.splice(ids.indexOf(id),0,dragged.id);dragged=null;
    try{await api("/notes/reorder",{kind,ids});await onrefresh();}catch(cause){onerror((cause as Error).message);}
  }
  async function loadTags(){try{tags=(await api<{tags:typeof tags}>("/notes/tags")).tags;}catch(cause){onerror((cause as Error).message);}}
  const activeNotebook = $derived(notebooks.find(n => n.id === notebookId));
  const folderOptions = $derived(folders.filter(f => editor !== "folder" || !isWithin(f.id, editId)));
  $effect(() => {
    const focus = folderFocus;
    if (focus !== null) untrack(() => {
      selectedFolder = focus;
      collapsed = new Set([...collapsed].filter(id => !isWithin(focus, id)));
    });
  });
  function isWithin(id: number, ancestor: number): boolean {
    const seen = new Set<number>();
    let current: number | null = id;
    while (current !== null && !seen.has(current)) {
      if (current === ancestor) return true;
      seen.add(current);
      current = folders.find(f => f.id === current)?.parent_id ?? null;
    }
    return false;
  }
  function folderPath(id: number): string {
    const parts: string[] = [], seen = new Set<number>();
    let current = folders.find(f => f.id === id);
    while (current && !seen.has(current.id)) {
      seen.add(current.id); parts.unshift(current.title);
      current = folders.find(f => f.id === current!.parent_id);
    }
    return parts.join(" / ");
  }
  function toggle(id: number) {
    const next = new Set(collapsed);
    if (next.has(id)) next.delete(id); else next.add(id);
    collapsed = next;
    selectedFolder = id;
  }
  function begin(kind: "folder" | "notebook", id = 0) {
    editor = kind; editId = id;
    const item = kind === "folder" ? folders.find(f => f.id === id) : notebooks.find(n => n.id === id);
    editRevision = item?.revision ?? 0;
    name = item?.title ?? "";
    parentId = String((kind === "folder" ? (item as Folder)?.parent_id : (item as Notebook)?.folder_id) ?? (id ? "" : selectedFolder ?? ""));
    subject = kind === "notebook" ? (item as Notebook)?.subject ?? "" : "";
    project = kind === "notebook" ? (item as Notebook)?.project ?? "" : "";
  }
  async function save() {
    if (!editor || saving) return;
    if (editor === "notebook" && !editId && !(await onbeforeswitch())) return;
    saving = true;
    try {
      const container = parentId ? Number(parentId) : null;
      if (editor === "folder") {
        const previous = folders.find(f => f.id === editId);
        const result = await api<Folder>(`/notes/folders${editId ? `/${editId}` : ""}`, {
          title: name, parent_id: container, ...(previous ? { revision: editRevision } : {}),
        });
        selectedFolder = result.id;
        collapsed = new Set([...collapsed].filter(id => id !== result.id && id !== container));
      } else {
        const previous = notebooks.find(n => n.id === editId);
        const result = await api<Notebook>(`/notes/notebooks${editId ? `/${editId}` : ""}`, {
          title: name, folder_id: container, subject, project, ...(previous ? { revision: editRevision } : {}),
        });
        await onrefresh();
        if (!editId) {
          await onselectnotebook(result.id);
          await onnewpage();
        }
        if (container !== null) collapsed = new Set([...collapsed].filter(id => !isWithin(container, id)));
      }
      await onrefresh();
      editor = null;
    } catch (cause) { onerror((cause as Error).message); }
    finally { saving = false; }
  }
  async function removeFolder() {
    const folder = folders.find(f => f.id === editId);
    if (!folder || !confirm(`Delete the empty folder “${folder.title}”? Notebooks and pages are never deleted with a folder.`)) return;
    saving = true;
    try {
      await api(`/notes/folders/${folder.id}/delete`, { revision: folder.revision, confirm: true });
      await onrefresh(); selectedFolder = folder.parent_id; editor = null;
    } catch (cause) { onerror((cause as Error).message); }
    finally { saving = false; }
  }
</script>

{#snippet glyph(kind: "folder" | "book" | "page")}
  <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true">
    {#if kind === "folder"}<path d="M3 7V5h7l2 3h9v12H3V7Z" />
    {:else if kind === "book"}<path d="M5 3h14v18H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2Zm2 0v18M11 7h5" />
    {:else}<path d="M6 3h8l4 4v14H6V3Zm8 0v5h4M9 12h6M9 16h6" />{/if}
  </svg>
{/snippet}

{#snippet branch(parent: number | null)}
  {#each folders.filter(f => f.parent_id === parent) as folder (folder.id)}
    <div class="folder-branch">
      <div class="tree-row folder-row" class:chosen={selectedFolder === folder.id}>
        <button class="tree-item" aria-expanded={!collapsed.has(folder.id) || !!search} onclick={() => toggle(folder.id)}
          draggable="true" ondragstart={()=>dragged={kind:"folders",id:folder.id}} ondragover={event=>event.preventDefault()} ondrop={event=>void drop(event,"folders",folder.id)}
        >
          <span class="chevron">{collapsed.has(folder.id) && !search ? "›" : "⌄"}</span>{@render glyph("folder")}<span class="item-title">{folder.title}</span>
        </button>
        <button class="row-menu" aria-label={`Edit folder ${folder.title}`} title="Rename or move folder" onclick={() => begin("folder", folder.id)}>⋯</button>
      </div>
      {#if !collapsed.has(folder.id) || search}<div class="tree-children">{@render branch(folder.id)}</div>{/if}
    </div>
  {/each}
  {#each notebooks.filter(n => n.folder_id === parent) as notebook (notebook.id)}
    <div class="notebook-branch">
      <div class="tree-row" class:chosen={notebookId === notebook.id}>
        <button class="tree-item" draggable="true" ondragstart={()=>dragged={kind:"notebooks",id:notebook.id}} ondragover={event=>event.preventDefault()} ondrop={event=>void drop(event,"notebooks",notebook.id)} aria-expanded={notebookId === notebook.id || !!search} onclick={() => { selectedFolder = notebook.folder_id; void onselectnotebook(notebook.id); }} disabled={busy || saving}>
          <span class="chevron">{notebookId === notebook.id || search ? "⌄" : "›"}</span>{@render glyph("book")}<span class="item-title">{notebook.title}</span>
        </button>
        <button class="row-menu" aria-label={`Edit notebook ${notebook.title}`} title="Rename, move or link notebook" onclick={() => begin("notebook", notebook.id)}>⋯</button>
      </div>
      {#if notebookId === notebook.id || search}
        <div class="tree-children pages">
          {#each pages.filter(p => p.notebook_id === notebook.id) as page (page.id)}
            <button class="tree-item page-item" class:block-target={blockTarget === page.id} draggable="true" ondragstart={()=>dragged={kind:"pages",id:page.id}}
              ondragover={event=>{event.preventDefault();if(event.dataTransfer?.types.includes(BLOCK_DRAG_TYPE))blockTarget=page.id;}} ondragleave={()=>{if(blockTarget===page.id)blockTarget=0;}}
              ondrop={event=>{blockTarget=0;if(onblocksdrop?.(page.id,event)){event.preventDefault();dragged=null;return;}void drop(event,"pages",page.id);}}
              oncontextmenu={event=>{event.preventDefault();pageMenu={id:page.id,x:event.clientX,y:event.clientY};}} class:current={pageId === page.id} aria-current={pageId === page.id ? "page" : undefined} onclick={() => void onopenpage(page.id)} disabled={busy || saving}>
              {@render glyph("page")}<span class="item-title">{page.title}</span>
            </button>
          {:else}<span class="empty-branch">{search ? "No matching pages" : "No pages yet"}</span>{/each}
          {#if notebookId === notebook.id && !search}<button class="tree-item add-page" onclick={() => void onnewpage()} disabled={busy || saving}>＋ New page</button>{/if}
        </div>
      {/if}
    </div>
  {/each}
{/snippet}

<div class="library">
  <div class="library-heading"><span>LIBRARY</span></div>
  <label class="library-search"><span class="sr-only">Search pages and text</span><input type="search" value={search} oninput={e => onsearch(e.currentTarget.value)} placeholder="Search your notes…" /></label>
  <div class="library-actions">
    <Button variant="ghost" size="sm" onclick={() => begin("folder")} disabled={saving}>＋ Folder</Button>
    <Button variant="ghost" size="sm" onclick={() => begin("notebook")} disabled={saving}>＋ Notebook</Button>
  </div>
  {#if pages.some(page=>page.favorite)}<details open><summary>Favorites</summary>{#each pages.filter(page=>page.favorite) as page}<button class="tree-item" onclick={()=>void onopenpage(page.id)}>★ {page.title}</button>{/each}</details>{/if}
  {#if pages.some(page=>page.last_opened)}<details><summary>Recent</summary>{#each [...pages].filter(page=>page.last_opened).sort((a,b)=>(b.last_opened||"").localeCompare(a.last_opened||"")).slice(0,6) as page}<button class="tree-item" onclick={()=>void onopenpage(page.id)}>{page.title}</button>{/each}</details>{/if}
  <details ontoggle={event=>{if(event.currentTarget.open)void loadTags();}}><summary>Tags</summary>{#each tags as tag}<button class="tree-item" title={tag.text} onclick={()=>void onopenpage(tag.page_id)}>{tag.tags.join(" · ")} · {tag.title}</button>{:else}<small>No tagged notes</small>{/each}</details>
  <button class="root-row" class:chosen={selectedFolder === null} onclick={() => selectedFolder = null}>{@render glyph("folder")}<span>My notes</span><span class="item-count">{notebooks.length}</span></button>
  <nav class="file-tree" aria-label="Folders, notebooks and pages">{@render branch(null)}</nav>
  {#if !notebooks.length && !folders.length}<div class="library-empty"><Button size="sm" onclick={() => begin("notebook")}>Create notebook</Button></div>{/if}
  {#if editor}
    <form class="library-editor" onsubmit={e => { e.preventDefault(); void save(); }}>
      <div class="editor-heading"><strong>{editId ? "Edit" : "New"} {editor}</strong><button type="button" class="row-menu" aria-label="Close library editor" onclick={() => editor = null}>×</button></div>
      <label>Name<input aria-label={`${editor === "folder" ? "Folder" : "Notebook"} name`} required maxlength="200" bind:value={name} /></label>
      <label>{editor === "folder" ? "Parent folder" : "Folder"}<select bind:value={parentId}><option value="">My notes (root)</option>{#each folderOptions as folder}<option value={String(folder.id)}>{folderPath(folder.id)}</option>{/each}</select></label>
      {#if editor === "notebook"}<details><summary>Subject & project links</summary><div class="link-fields"><label>Subject<input maxlength="200" bind:value={subject} placeholder="e.g. Digital systems" /></label><label>Project<input maxlength="200" bind:value={project} placeholder="Optional project" /></label></div></details>{/if}
      <div class="editor-actions"><Button type="submit" size="sm" disabled={saving}>{saving ? "Saving…" : editId ? "Save changes" : `Create ${editor}`}</Button><Button type="button" variant="ghost" size="sm" onclick={() => editor = null}>Cancel</Button></div>
      {#if editor === "folder" && editId}<button class="delete-folder" type="button" disabled={saving || folders.some(f => f.parent_id === editId) || notebooks.some(n => n.folder_id === editId)} onclick={removeFolder}>Delete empty folder</button>{/if}
    </form>
  {/if}
  {#if activeNotebook}<div class="library-context"><span>Current notebook</span><strong>{activeNotebook.title}</strong>{#if activeNotebook.subject || activeNotebook.project}<small>{[activeNotebook.subject, activeNotebook.project].filter(Boolean).join(" · ")}</small>{/if}</div>{/if}
</div>
{#if pageMenu}<div class="page-context" role="menu" style:left={`${Math.min(pageMenu.x,window.innerWidth-180)}px`} style:top={`${Math.min(pageMenu.y,window.innerHeight-70)}px`}><button role="menuitem" onclick={()=>{if(pageMenu)void ondeletepage(pageMenu.id);pageMenu=null;}}>Delete page…</button><button role="menuitem" onclick={()=>pageMenu=null}>Cancel</button></div>{/if}

<style>
  .page-context{position:fixed;z-index:90;padding:8px;background:var(--surface);border:1px solid var(--border);box-shadow:0 8px 20px #0003;display:flex;gap:8px}
  .library { display:flex; flex-direction:column; gap:12px; min-height:0; }
  .library-heading,.editor-heading { display:flex;align-items:center;justify-content:space-between;gap:8px; }
  .library-heading { font-size:10px;letter-spacing:.11em;font-weight:650;color:var(--muted); }
  .library-search input {width:100%;padding:9px 10px;font-size:13px;border-radius:6px;background:var(--chrome);}
  .library-actions {display:flex;gap:4px;}
  .library-actions :global(.br-btn) {flex:1;}
  .root-row,.tree-item,.row-menu {font:inherit;color:inherit;background:none;border:0;cursor:pointer;}
  .root-row {display:flex;align-items:center;gap:8px;padding:8px;border-radius:5px;font-size:13px;text-align:left;color:var(--muted);}
  .item-count {margin-left:auto;font-size:11px;}
  .file-tree {font-size:13px;overflow:auto;min-height:100px;max-height:48vh;padding:2px;}
  .tree-row {display:flex;align-items:center;border-radius:5px;gap:2px;}
  .tree-item {display:flex;align-items:center;gap:7px;width:100%;min-width:0;text-align:left;padding:7px 5px;border-radius:5px;line-height:1.4;}
  .item-title {overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
  .tree-item svg,.root-row svg {flex-shrink:0;color:var(--muted);}
  .tree-item:hover,.root-row:hover,.row-menu:hover {background:var(--surface-raised);color:var(--ink);}
  .tree-item:disabled {opacity:.5;cursor:wait;}
  .chosen {background:color-mix(in srgb,var(--accent) 8%,transparent);}
  .current {background:color-mix(in srgb,var(--accent) 16%,transparent);color:var(--accent);}
  .current svg {color:var(--accent);}
  .chevron {width:10px;flex-shrink:0;color:var(--muted);font-size:15px;}
  .row-menu {padding:3px 6px;flex-shrink:0;border-radius:4px;color:var(--muted);font-size:18px;line-height:1;}
  .tree-children {margin-left:13px;padding-left:7px;border-left:1px solid var(--border);}
  .page-item {padding-left:7px;}
  .page-item.block-target {outline:2px solid var(--accent);outline-offset:-2px;}
  .add-page {color:var(--muted);font-size:12px;}
  .empty-branch {display:block;padding:6px;color:var(--muted);font-size:11px;}
  .library-empty {padding:12px 4px;color:var(--muted);font-size:12px;line-height:1.6;}
  .library-editor {padding:12px;border:1px solid var(--border);border-radius:8px;display:flex;flex-direction:column;gap:12px;background:var(--surface);}
  .editor-heading {font-size:13px;}
  .library-editor label {display:flex;flex-direction:column;gap:5px;font-size:11px;color:var(--muted);}
  .library-editor input,.library-editor select {width:100%;min-width:0;font-size:13px;padding:8px;border-radius:5px;}
  .library-editor select {padding-right:30px;}
  .library-editor details {font-size:11px;}
  .link-fields {display:grid;gap:10px;padding-top:10px;}
  .editor-actions {display:flex;flex-wrap:wrap;gap:5px;}
  .delete-folder {font:inherit;font-size:11px;color:var(--error);padding:5px;background:none;border:0;cursor:pointer;}
  .delete-folder:disabled {opacity:.4;cursor:not-allowed;}
  .library-context {border-top:1px solid var(--border);padding:14px 3px 0;display:flex;flex-direction:column;gap:4px;font-size:11px;color:var(--muted);}
  .library-context strong {color:var(--ink);font-weight:550;font-size:12px;}
</style>
