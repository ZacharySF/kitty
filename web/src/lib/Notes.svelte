<script lang="ts">
  import { onMount, tick, untrack } from "svelte";
  import { Button } from "@nunera/bracket";
  import NotesLibrary, { type Folder, type Notebook, type PageSummary } from "./NotesLibrary.svelte";
  import CanvasColorMenu from "./CanvasColorMenu.svelte";
  import SectionGraph, { type SectionNode } from "./SectionGraph.svelte";
  import { api, apiFetch, apiObjectUrl, followApiLink, type TaskRecord } from "./api";
  import AIReview from "./AIReview.svelte";
  import NoteReferences from "./NoteReferences.svelte";
  import NoteOutline, { type BlocksResult } from "./NoteOutline.svelte";
  import { BLOCK_DRAG_TYPE, pageQuery } from "./BlockEditor.svelte";
  import BlockHistory from "./BlockHistory.svelte";
  import BlockTrash from "./BlockTrash.svelte";
  import DocumentImport from "./DocumentImport.svelte";
  import SchemaMaps from "./SchemaMaps.svelte";
  import CanvasPill from "./CanvasPill.svelte";
  import Icon from "./Icon.svelte";
  import Select from "./ui/Select.svelte";
  import OverflowMenu, { type OverflowItem } from "./ui/OverflowMenu.svelte";
  import Popover from "./ui/Popover.svelte";
  import EditDrawer from "./ui/EditDrawer.svelte";
  import { useWorkspaceContext, type SaveStatus } from "./workspaceContext.svelte";
  import { fitHeight } from "./fitHeight";
  import { onBlockChange } from "./live";
  import { followMirrors, mirrors } from "./mirrorStore.svelte";
  import { buildStore, setPreferences } from "./build.svelte";
  import type { OutlineBlock } from "./BlockEditor.svelte";
  const pane = useWorkspaceContext();
  /** Q17/Q19: a page is written (Write), drawn (Canvas) or mapped in the one mind-map editor (Map). */
  type NoteMode = "text" | "canvas" | "map";
  let noteMode = $state<NoteMode>("text");
  let outlineText = $state(""), blockReferences = $state<Record<string,string>>({}), citationCounts = $state<Record<string,number>>({});
  /** The block editor's text-flow blocks in reading order (the floating outline, find and word count read them). */
  let outlineBlocks = $state.raw<OutlineBlock[]>([]);
  let historyOpen = $state(false), trashOpen = $state(false);
  /** The Mirror pane (Q11) beside the writing view: the browser tab the kitty extension mirrors. */
  let mirrorOpen = $state(false);
  let openTabs = $state<number[]>([]), focusBlock = $state(""), tabDrag = 0;
  let pageMenu = $state<{id:number;x:number;y:number}|null>(null);
  let originals = $state<{id:number;name:string}[]>([]);
  function persistTabs() { try { localStorage.setItem("kitty.notes.tabs",JSON.stringify(openTabs)); } catch { /* Navigation remains usable. */ } }
  async function closeTab(id:number) {
    if(id===pageId && !(await beforeSwitch()))return;
    if(id===pageId && openTabs.length===1 && fullscreen)await toggleFullscreen();
    const index=openTabs.indexOf(id);openTabs=openTabs.filter(value=>value!==id);persistTabs();
    if(id===pageId){const next=openTabs[Math.min(index,openTabs.length-1)];if(next)await openPage(next,false,true);else{pageId=0;presentation="map";}}
  }
  function reorderTab(id:number) {
    if(!tabDrag||tabDrag===id)return;
    const next=openTabs.filter(value=>value!==tabDrag);next.splice(next.indexOf(id),0,tabDrag);openTabs=next;tabDrag=0;persistTabs();
  }
  async function openBlock(id:number,bid="") {
    focusBlock="";await openPage(id);
    if(!pane.active||pageId!==id||!(await setMode("text")))return;
    // The block editor reveals the block, opening any collapsed parents.
    focusBlock=bid;
  }
  async function refreshReferences() {
    if(!pane.active||!pageId||document.hidden)return;
    const id=pageId;
    try { const result=await api<BlocksResult>(`/notes/pages/${id}/blocks`);if(id!==pageId)return;blockReferences=result.references;citationCounts=result.citation_counts;outlineText=result.blocks.map(block=>block.text).join("\n");
      if(!dirty&&!saving&&result.revision!==pageRevision)await refreshPage(id);
    } catch { /* Save errors are shown by the writing pipeline, not background refresh. */ }
  }
  // Another window changed this page: take its title and canvas without disturbing the block editor.
  async function refreshPage(id:number) {
    const page=await api<Page>(`/notes/pages/${id}`);
    if(id!==pageId||dirty||saving||gesture)return;
    title=page.title;doc=page.document;pageRevision=page.revision;
    selected=selected.filter(element=>doc.elements.some(e=>e.id===element));
  }
  // The block editor's own transactions move the page revision this view saves against.
  function editorRevision(from:number,to:number,own:boolean) {
    if(own&&pageRevision===from)pageRevision=to;
  }
  async function openLinkedPage(pageBlock:string) {
    try{const found=await api<{root:{type:string;properties:{number?:number}}}>(`/blocks/page?${pageQuery({page:pageBlock})}`);
      if(found.root.type==="page"&&found.root.properties.number)await openPage(found.root.properties.number);}
    catch(cause){error=(cause as Error).message;}
  }
  async function createSubpage(): Promise<{page:string;title:string}|null> {
    if(!notebookId)return null;
    const page=await api<Page>("/notes/pages",{notebook_id:notebookId,title:"Untitled page"});
    const state=await api<{page_id:string;root:{text:string}}>(`/blocks/page?${pageQuery({type:"page",number:page.id})}`);
    void loadIndex();
    return {page:state.page_id,title:state.root.text};
  }
  function blockDrag(event:DragEvent):string[]|null {
    const raw=event.dataTransfer?.getData(BLOCK_DRAG_TYPE);
    if(!raw)return null;
    try{const ids=(JSON.parse(raw) as {ids?:unknown}).ids;return Array.isArray(ids)?ids.filter((id):id is string=>typeof id==="string"):null;}catch{return null;}
  }
  async function moveBlocksToPage(target:number,ids:string[]) {
    if(target===pageId||!noteOutline)return;
    try{if(await noteOutline.moveToPage(ids,{type:"page",number:target}))operationStatus=`Moved ${ids.length===1?"a block":`${ids.length} blocks`} to “${pages.find(page=>page.id===target)?.title??"page"}”`;}
    catch(cause){error=(cause as Error).message;}
  }
  async function deletePageById(id:number) {pageMenu=null;if(id!==pageId){if(!(await beforeSwitch()))return;await openPage(id,false,true);if(pageId!==id)return;}await deletePage();}
  let presentation = $state<"map" | "editor">("map");
  /** Map mode's embedded mind-map editor and the save state it reports (merged into the tab dot). */
  let mapEditor = $state<{flush: () => Promise<boolean>; hasUnsavedChanges: () => boolean}>();
  let mapStatus = $state<SaveStatus | null>(null);
  let schemaFocus = $state<string | null>(null);
  let relatedOpen = $state(true);
  let linkTitle = $state("");
  let pageFind = $state(""), findOpen = $state(false), findFrom = "";
  /** Write: Enter jumps to the next block containing the find text. Canvas: the text outline filters by it. */
  function findNext() {
    const needle = pageFind.trim().toLocaleLowerCase();
    if (!needle) return;
    if (noteMode !== "text") { outline = true; return; }
    const matches = outlineBlocks.filter(block => block.text.toLocaleLowerCase().includes(needle));
    if (!matches.length) return;
    const next = matches[(matches.findIndex(block => block.id === findFrom) + 1) % matches.length];
    findFrom = next.id;
    focusBlock = "";
    void revealBlock(next.id);
  }
  async function rootSchema() { if (await beforeSwitch()) { presentation="map"; schemaFocus=null; } }
  async function notebookSchema(id:number) {
    if (!(await beforeSwitch())) return;
    notebookId=id; schemaFocus=`notebook:${id}`; presentation="map";
  }
  async function dailyNote() {
    if (!(await beforeSwitch())) return;
    try { const now=new Date();const date=`${now.getFullYear()}-${String(now.getMonth()+1).padStart(2,"0")}-${String(now.getDate()).padStart(2,"0")}`;const page=await api<Page>("/notes/daily",{date}); await loadIndex(); await openPage(page.id,false,true); }
    catch(cause) {error=(cause as Error).message;}
  }
  async function resolveLink(value=linkTitle) {
    if(!value.trim() || !(await beforeSwitch()))return;
    try { const page=await api<Page>("/notes/resolve",{title:value.trim(),...(notebookId?{notebook_id:notebookId}:{})});await loadIndex();await openPage(page.id,false,true);linkTitle=""; }
    catch(cause){error=(cause as Error).message;}
  }
  function linkMention(value:string) {
    const next=clone(doc); let changed=false;
    for(const element of next.elements)if(element.text&&!element.text.includes(`[[${value}]]`)){
      const escaped=value.replace(/[.*+?^${}()|[\]\\]/g,"\\$&");
      const pattern=new RegExp(`(^|[^\\p{L}\\p{N}_])(${escaped})(?=$|[^\\p{L}\\p{N}_])`,"iu");
      if(pattern.test(element.text)){element.text=element.text.replace(pattern,"$1[[$2]]");changed=true;}
    }
    if(changed)commit(next);
  }
  function wikiQuery(text:string) { return /\[\[([^\]]*)$/.exec(text)?.[1] ?? null; }
  function setTag(id:string,tag:string) {
    const next=clone(doc),element=next.elements.find(e=>e.id===id);
    if(!element)return;
    element.tags=tag ? [tag as "todo"|"important"|"question"|"definition"] : [];
    commit(next);
  }
  async function favoritePage() {
    try{await api(`/notes/pages/${pageId}/organize`,{favorite:!(pages.find(p=>p.id===pageId) as PageSummary & {favorite?:boolean})?.favorite});await loadIndex();}
    catch(cause){error=(cause as Error).message;}
  }
  // A pane that becomes active again catches up on changes it skipped while inactive.
  $effect(() => {
    if (pane.active) untrack(() => void refreshReferences());
  });
  $effect(() => {
    const book=notebooks.find(n=>n.id===notebookId);
    const crumbs=[];
    if(schemaFocus||presentation==="editor")if(book)crumbs.push({label:book.title,onclick:()=>void notebookSchema(book.id)});
    if(presentation==="editor"&&pageId)crumbs.push({label:title||"Untitled page"});
    pane.setTrail("notes",crumbs,()=>void rootSchema());
    return ()=>pane.clearTrail("notes");
  });
  import {
    bounds,
    CANVAS_INK,
    clone,
    downloadBlob,
    drawingInput,
    editable,
    emptyDocument,
    expandGroups,
    exportSVG,
    finishGesture,
    finishStroke,
    fit,
    hit,
    hitTop,
    lassoSelection,
    noteTabSwipe,
    noteTabEdge,
    paperPattern,
    removeElements,
    renderCanvas,
    resizeElement,
    svgPNG,
    uid,
    world,
    zoomAt,
    type Bounds,
    type CanvasDocument,
    type CanvasElement,
    type ElementKind,
    type Point,
    type Viewport,
  } from "./notesCanvas";
  let {
    revision = 0,
    demo = false,
    onflashcards,
    initialMode,
  }: {
    revision?: number;
    demo?: boolean;
    /** Opens the page in Map mode (the mind-map module). */
    initialMode?: "map";
    onflashcards?: (text: string, pageId: number) => void;
  } = $props();
  type Page = {
    id: number;
    notebook_id: number;
    title: string;
    revision: number;
    viewport_revision: number;
    updated_at?: string;
    document: CanvasDocument;
    viewport: Viewport;
  };
  type Preferences = {
    allow_ai: boolean;
    retention_days: number;
    revision: number;
  };
  type Tool = ElementKind | "select" | "lasso" | "pan" | "eraser";
  type Gesture = {
    mode:
      | "draw"
      | "move"
      | "resize"
      | "rect-select"
      | "lasso"
      | "erase"
      | "pan"
      | "pinch";
    pointer: number;
    start: Point;
    last: Point;
    before: CanvasDocument;
    view: Viewport;
    selected: string[];
    box?: Bounds;
    id?: string;
    distance?: number;
    centroid?: Point;
  };
  type TouchPair = {
    starts: [Point, Point]; ids: [number, number]; started: number; eligible: boolean; scaleDriftSince: number | null;
  };
  let touchPair: TouchPair | null = null;
  const touchPointers = new Set<number>(), consumedPointers = new Set<number>();
  const edgeContacts = new Map<number, {side: -1 | 1; start: Point; time: number; moved: boolean}>();
  let lastEdgeTap: {side: -1 | 1; y: number; time: number} | null = null;
  let tabSwitchPending = false;
  let tabsRevealed = $state(false), indexReady = $state(false), pageInitialized = false;
  let revealTimer: ReturnType<typeof setTimeout> | undefined;
  function revealTabs() {
    if (!fullscreen) return;
    tabsRevealed = true;
    clearTimeout(revealTimer);
    revealTimer = setTimeout(() => tabsRevealed = false, 2800);
  }
  function finishActiveGesture() {
    for (const id of [...pointers.keys()]) finishPointer(id);
  }
  function resetNavigationContacts() {
    if (penActive!==null && edgeContacts.has(penActive)) penActive=null;
    for (const id of [...edgeContacts.keys(), ...consumedPointers])
      if (canvas?.hasPointerCapture(id)) canvas.releasePointerCapture(id);
    edgeContacts.clear(); consumedPointers.clear(); lastEdgeTap=null;
    tabsRevealed=false; clearTimeout(revealTimer);
  }
  async function switchNoteTab(direction: -1 | 1) {
    if (!pane.active || tabSwitchPending) return;
    const index = openTabs.indexOf(pageId), next = openTabs[index + direction];
    if (index < 0 || !next) return;
    tabSwitchPending = true;
    try {
      if (await beforeSwitch()) await openPage(next,false,true);
      revealTabs();
    } finally { tabSwitchPending = false; }
  }
  const PAGE_MODES = [{ value: "text", label: "Write" }, { value: "canvas", label: "Canvas" }, { value: "map", label: "Map" }];
  /** The header's Write/Canvas/Map switch; from the library overview it returns to the open page. */
  async function chooseMode(mode: NoteMode) {
    if (!pageId || !pane.active) return;
    if (await setMode(mode)) presentation = "editor";
  }
  /** Switches the page mode; leaving Map first saves the map, and stays if it cannot. */
  async function setMode(mode: NoteMode): Promise<boolean> {
    if (mode === noteMode) return true;
    if (noteMode === "map" && mapEditor && !(await mapEditor.flush())) {
      error ||= "The map could not save. Retry before leaving Map.";
      return false;
    }
    finishActiveGesture();
    noteMode = mode;
    if (mode !== "map") mapStatus = null;
    return true;
  }
  let noteTitles = $state<PageSummary[]>([]);
  let notebooks = $state<Notebook[]>([]),
    pages = $state<PageSummary[]>([]),
    prefs = $state<Preferences>({
      allow_ai: false,
      retention_days: 30,
      revision: 1,
    });
  let folders = $state<Folder[]>([]);
  let fullscreenPending = false;
  let fullscreenLayout: {
    x: number; y: number; focus: HTMLElement | null;
    scroll: { element: HTMLElement; top: number; left: number }[];
  } | null = null;
  let fullscreen = $state(false);
  // Q43: the library is a slide-over drawer; the writing view floats an "On this page" outline.
  let libraryOpen = $state(false), libraryToggle = $state<HTMLButtonElement>(), libraryDrawer = $state<HTMLElement>();
  let tocOpen = $state(true), tocCurrent = $state("");
  async function openLibrary(open: boolean) {
    libraryOpen = open;
    await tick();
    if (open) libraryDrawer?.querySelector<HTMLInputElement>("input[type=search]")?.focus();
    else libraryToggle?.focus();
  }
  let noteOutline = $state<ReturnType<typeof NoteOutline>>();
  /** Reveals a block in the editor (opening collapsed parents) even when it is already the focus target. */
  async function revealBlock(id: string) { focusBlock = ""; await tick(); focusBlock = id; }
  async function openFromLibrary(id: number) { libraryOpen = false; await openPage(id); }
  const tocEntries = $derived(outlineBlocks.filter(block => !block.parent_id && block.text.trim())
    .map(block => ({id: block.id, label: block.text.replace(/[`*#[\]()]/g, "").split(/[:;,.]/)[0].trim() || block.text.trim().slice(0, 40)})));
  const wordCount = $derived(outlineBlocks.reduce((total, block) => total + (block.text.match(/\S+/g)?.length ?? 0), 0));
  function topBlock(id: string) {
    const seen = new Set<string>();
    let block = outlineBlocks.find(item => item.id === id);
    while (block?.parent_id && !seen.has(block.id)) { seen.add(block.id); block = outlineBlocks.find(item => item.id === block!.parent_id); }
    return block?.id ?? "";
  }
  async function jumpToBlock(id: string) { focusBlock = ""; await tick(); focusBlock = id; tocCurrent = id; }
  function toggleToc() {
    tocOpen = !tocOpen;
    try { localStorage.setItem("kitty.notes.outline", tocOpen ? "open" : "closed"); } catch { /* The outline still toggles. */ }
  }
  // Q6/Q8: rare page actions sit in ⋯; import, privacy and layers open drawers.
  let importOpen = $state(false), privacyOpen = $state(false), layersOpen = $state(false), cleanupResult = $state("");
  let folderFocus = $state<number | null>(null);
  let notebookId = $state(0),
    pageId = $state(0),
    pageRevision = $state(1),
    viewRevision = $state(1),
    title = $state(""),
    pageUpdated = $state("");
  /** Re-renders the "edited 4 min ago" meta line. */
  let clock = $state(Date.now());
  const edited = $derived.by(() => {
    const at = Date.parse(pageUpdated);
    if (!Number.isFinite(at)) return "";
    const minutes = Math.max(0, Math.round((clock - at) / 60000));
    if (minutes < 1) return "edited just now";
    if (minutes < 60) return `edited ${minutes} min ago`;
    if (minutes < 60 * 24) return `edited ${Math.round(minutes / 60)} h ago`;
    const days = Math.round(minutes / (60 * 24));
    if (days < 7) return `edited ${days} ${days === 1 ? "day" : "days"} ago`;
    return `edited ${new Date(at).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" })}`;
  });
  let doc = $state<CanvasDocument>(emptyDocument()),
    view = $state<Viewport>({ x: 0, y: 0, zoom: 1 });
  let tool = $state<Tool>("pen"),
    color = $state(CANVAS_INK),
    fillColor = $state("#fff2a8"),
    lineWidth = $state(3),
    fontSize = $state(20),
    layerId = $state("default");
  let selected = $state<string[]>([]),
    outline = $state(false),
    overlay = $state<Point[]>([]),
    search = $state("");
  let loading = $state(false),
    error = $state(""),
    saveStatus = $state("Saved locally"),
    viewError = $state(""),
    busy = $state(false),
    dirty = $state(false),
    viewDirty = $state(false),
    blocked = $state(false);
  /** Q3: the tab's status dot carries the save state; the page and its map merge, failed > unsaved > saved. */
  const SAVE_RANK = { failed: 2, unsaved: 1, saved: 0 } as const;
  $effect(() => {
    const page: SaveStatus = blocked ? { state: "failed", detail: saveStatus } : dirty || viewDirty ? { state: "unsaved", detail: "Unsaved changes · Ctrl S saves" } : { state: "saved" };
    const map = noteMode === "map" ? mapStatus : null;
    pane.setStatus(map && SAVE_RANK[map.state as keyof typeof SAVE_RANK] > SAVE_RANK[page.state as keyof typeof SAVE_RANK] ? map : page);
    return () => pane.setStatus(null);
  });
  export function hasUnsavedChanges(): boolean {
    return dirty || viewDirty || Boolean(saving) || Boolean(viewSaving) || blocked || Boolean(viewError) ||
      gesture !== null || Boolean(mapEditor?.hasUnsavedChanges()) || Boolean(noteOutline?.pending());
  }
  export function isCanvasFullscreen(): boolean {
    return Boolean(stage && document.fullscreenElement === stage);
  }
  export async function newCanvasTab(): Promise<void> {
    if (!pane.active) return;
    const previous=pageId;
    await createPage();
    if (pane.active && pageId!==previous && noteMode==="text") await setMode("canvas");
  }
  let undoStack = $state<CanvasDocument[]>([]),
    redoStack = $state<CanvasDocument[]>([]),
    canvas = $state<HTMLCanvasElement>(),
    stage = $state<HTMLDivElement>(),
    width = $state(900),
    height = $state(600),
    imageEpoch = $state(0);
  let summary = $state(""),
    summarySelection = $state("");
  let newLayer = $state(""),
    restoreNotebook = $state(true),
    operationStatus = $state("");
  let gesture: Gesture | null = null,
    space = false,
    contentGeneration = 0,
    viewGeneration = 0,
    saveTimer: ReturnType<typeof setTimeout> | undefined,
    viewTimer: ReturnType<typeof setTimeout> | undefined,
    searchTimer: ReturnType<typeof setTimeout> | undefined;
  let saving: Promise<void> | null = null,
    viewSaving: Promise<void> | null = null;
  // Q68: the page is the theme's flat void colour; syncColors follows theme switches.
  let ink = $state(CANVAS_INK), paper = $state(getComputedStyle(document.documentElement).getPropertyValue("--solid-void").trim());
  const images = new Map<string, HTMLImageElement>(),
    pointers = new Map<number, Point>();
  // Pen mode is the build's `ui.notes.pen_only`.
  const penOnly = $derived(buildStore.build.ui.notes.pen_only);
  let penActive: number | null = null,
    penGuardUntil = 0;
  const activeNotebook = $derived(notebooks.find((n) => n.id === notebookId));
  const selectedElements = $derived(
    doc.elements.filter((e) => selected.includes(e.id)),
  );
  const selectedText = $derived(
    selectedElements
      .filter((e) => e.kind === "text" || e.kind === "sticky")
      .map((e) => e.text ?? "")
      .filter(Boolean)
      .join("\n\n"),
  );
  const textElements = $derived(
    doc.elements.filter((e) => e.kind === "text" || e.kind === "sticky"),
  );
  const tools: { id: Tool; label: string; key: string }[] = [
    { id: "select", label: "Select", key: "V" },
    { id: "lasso", label: "Lasso", key: "L" },
    { id: "pan", label: "Pan", key: "H" },
    { id: "pen", label: "Pen", key: "P" },
    { id: "highlighter", label: "Highlight", key: "B" },
    { id: "eraser", label: "Eraser", key: "E" },
    { id: "text", label: "Text", key: "T" },
    { id: "sticky", label: "Sticky", key: "N" },
    { id: "rect", label: "Rectangle", key: "R" },
    { id: "ellipse", label: "Ellipse", key: "O" },
    { id: "line", label: "Line", key: "I" },
    { id: "arrow", label: "Arrow", key: "A" },
    { id: "connector", label: "Connect", key: "C" },
  ];
  // Q46: one floating pill (CanvasPill) at the bottom of the canvas stage; the rest sit in its ⋯.
  const PILL_TOOLS: Tool[] = ["select", "pen", "highlighter", "eraser", "text", "sticky"];
  const pillTools = tools.filter(item => PILL_TOOLS.includes(item.id));
  const moreTools = tools.filter(item => !PILL_TOOLS.includes(item.id));
  const STROKE_SIZES = [2, 4, 8];
  const PAPERS = ["blank", "lined", "grid", "dotted", "cornell"].map(value => ({ value, label: value[0].toUpperCase() + value.slice(1) }));
  const TAGS = [
    { value: "", label: "None" }, { value: "todo", label: "☐ To-do" }, { value: "important", label: "★ Important" },
    { value: "question", label: "? Question" }, { value: "definition", label: "Definition" },
  ];
  function chooseTool(id: Tool) {
    finishActiveGesture();
    tool = id;
  }
  function moveSelectionToLayer(next: string) {
    if (doc.layers.find((l) => l.id === next)?.locked) {
      error = "Target layer is locked.";
      return;
    }
    layerId = next;
    commit({
      ...doc,
      elements: doc.elements.map((v) => selected.includes(v.id) && editable(v, doc) ? { ...v, layer: next } : v),
    });
  }

  async function loadIndex() {
    try {
      const data = await api<{
        notebooks: Notebook[];
        folders: Folder[];
        pages: PageSummary[];
        preferences: Preferences;
      }>(`/notes${search ? "?search=" + encodeURIComponent(search) : ""}`);
      notebooks = data.notebooks;
      folders = data.folders;
      pages = data.pages;
      noteTitles = search ? (await api<{pages:PageSummary[]}>("/notes")).pages : data.pages;
      prefs = data.preferences;
      if (!notebookId && notebooks.length) notebookId = notebooks[0].id;
    } catch (cause) {
      error = (cause as Error).message;
    }
  }
  function recoveryKey(id = pageId) {
    return `kitty.notes.pending.${id}`;
  }
  function keepRecovery() {
    if (!pageId) return;
    try {
      sessionStorage.setItem(
        recoveryKey(),
        JSON.stringify({ revision: pageRevision, title, document: doc }),
      );
    } catch {
      error =
        "Browser recovery storage is full. Save now or export before closing.";
    }
  }
  function remember(before: CanvasDocument) {
    undoStack = [...undoStack.slice(-29), clone(before)];
    redoStack = [];
  }
  function scheduleContent() {
    dirty = true;
    blocked = false;
    contentGeneration++;
    saveStatus = "Unsaved changes";
    keepRecovery();
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => void saveContent(), 650);
  }
  function resumeAutosave() {
    if (dirty && !blocked) {
      clearTimeout(saveTimer);
      saveTimer = setTimeout(() => void saveContent(), 650);
    }
    if (viewDirty && !viewError) {
      clearTimeout(viewTimer);
      viewTimer = setTimeout(() => void saveView(), 700);
    }
  }
  function commit(next: CanvasDocument, before = clone(doc)) {
    doc = next;
    remember(before);
    scheduleContent();
  }
  async function saveContent(): Promise<void> {
    if (saving) {
      await saving;
      if (dirty && !blocked) return saveContent();
      return;
    }
    if (!dirty || !pageId || gesture || blocked) return;
    const id = pageId,
      generation = contentGeneration,
      document = clone(doc),
      pageTitle = title;
    saveStatus = "Saving locally…";
    saving = (async () => {
      try {
        // Title and canvas save over HTTP; the block editor's own transactions wait meanwhile.
        const write = async () => {
          const saved = await api<Page>(`/notes/pages/${id}`, { revision: pageRevision, title: pageTitle, document });
          if (pageId === id) pageRevision = saved.revision;
          return saved.revision;
        };
        await (noteOutline ? noteOutline.exclusive(write) : write());
        if (pageId !== id) return;
        pages = pages.map(page => page.id === id ? {...page, title: pageTitle} : page);
        noteTitles = noteTitles.map(page => page.id === id ? {...page, title: pageTitle} : page);
        pageUpdated = new Date().toISOString();
        if (generation === contentGeneration) {
          dirty = false;
          saveStatus = "Saved locally";
          try {
            sessionStorage.removeItem(recoveryKey(id));
          } catch {
            /* Recovery is best effort. */
          }
        } else {
          keepRecovery();
          saveStatus = "Unsaved changes";
        }
      } catch (cause) {
        if (pageId === id) {
          error = (cause as Error).message;
          blocked = true;
          saveStatus = "Save failed — local edits preserved; export or retry";
        }
      } finally {
        saving = null;
      }
    })();
    await saving;
    if (dirty && !blocked) await saveContent();
  }
  function scheduleView() {
    viewDirty = true;
    viewGeneration++;
    clearTimeout(viewTimer);
    viewTimer = setTimeout(() => void saveView(), 700);
  }
  async function saveView(): Promise<void> {
    if (viewSaving) {
      await viewSaving;
      if (viewDirty && !viewError) return saveView();
      return;
    }
    if (!viewDirty || !pageId || gesture || viewError) return;
    const id = pageId,
      generation = viewGeneration,
      viewport = clone(view),
      rev = viewRevision;
    viewSaving = (async () => {
      try {
        const result = await api<{ viewport_revision: number }>(
          `/notes/pages/${id}/viewport`,
          { revision: rev, viewport },
        );
        if (pageId === id) {
          viewRevision = result.viewport_revision;
          if (generation === viewGeneration) viewDirty = false;
        }
      } catch (cause) {
        viewError = (cause as Error).message;
      } finally {
        viewSaving = null;
      }
    })();
    await viewSaving;
    if (viewDirty && !viewError) await saveView();
  }
  async function openPage(id: number, force = false, prepared = false) {
    if (!pane.active) return;
    if (id === pageId && !force && presentation === "editor") return;
    if (!prepared && id !== pageId && noteMode === "map" && mapEditor && !(await mapEditor.flush())) {
      error ||= "The map could not save. Retry before switching pages.";
      return;
    }
    if (loading) return;
    finishActiveGesture();
    await saveContent();
    await saveView();
    await noteOutline?.flush();
    if (dirty) { error ||= "Save or export these local edits before switching pages."; return; }
    loading = true;
    error = "";
    try {
      const page = await api<Page>(`/notes/pages/${id}`);
      const blockData = await api<BlocksResult>(`/notes/pages/${id}/blocks`);
      if(page.revision!==blockData.revision)throw new Error("Page changed while opening; open it again to load one consistent revision.");
      outlineText=blockData.blocks.map(block=>block.text).join("\n");blockReferences=blockData.references;citationCounts=blockData.citation_counts;
      if(!openTabs.includes(id)){openTabs=[...openTabs,id];persistTabs();}
      originals=[];
      void api<{originals:typeof originals}>(`/documents/originals?page_id=${id}`).then(result=>{if(pageId===id)originals=result.originals;}).catch(()=>{});
      pageId = page.id;
      presentation = "editor";
      schemaFocus = `notebook:${page.notebook_id}`;
      void api(`/notes/pages/${page.id}/organize`,{visited:true}).catch(()=>{});
      notebookId = page.notebook_id;
      title = page.title;
      pageUpdated = page.updated_at ?? "";
      pageRevision = page.revision;
      viewRevision = page.viewport_revision;
      doc = page.document;
      view = page.viewport;
      layerId =
        doc.layers.find((l) => l.visible && !l.locked)?.id ?? doc.layers[0].id;
      selected = [];
      undoStack = [];
      redoStack = [];
      dirty = false;
      viewDirty = false;
      blocked = false;
      viewError = "";
      saveStatus = "Saved locally";
      summary = "";
      summarySelection = "";
      try {
        const raw = sessionStorage.getItem(recoveryKey(id));
        if (raw) {
          const pending = JSON.parse(raw);
          {
            doc = pending.document;
            title = pending.title;
            dirty = true;
            contentGeneration++;
            if (pending.revision !== pageRevision) {
              blocked = true;
              error =
                "Recovered edits are based on an older version. Export them before reloading; they will not overwrite the newer page.";
              saveStatus = "Recovered version conflict";
            } else {
              scheduleContent();
            }
          }
        }
      } catch {
        error =
          "Could not restore browser recovery data; the server copy is loaded.";
      }
    } catch (cause) {
      error = (cause as Error).message;
    } finally {
      loading = false;
    }
  }
  async function beforeSwitch() {
    if (!pane.active || loading || busy) return false;
    if (noteMode === "map" && mapEditor && !(await mapEditor.flush())) {
      error ||= "The map could not save. Retry before switching pages.";
      return false;
    }
    finishActiveGesture();
    await saveContent();
    await saveView();
    await noteOutline?.flush();
    if (dirty) error ||= "These edits could not save. Retry or export before switching pages.";
    else if (noteOutline?.pending()) error ||= "Block edits are still being saved. Try again in a moment.";
    return pane.active && !dirty && !noteOutline?.pending();
  }
  function finishFullscreenLayout() {
    resetNavigationContacts();
    if (gesture) {
      for (const id of [...pointers.keys()]) finishPointer(id);
    }
    fullscreen = false;
    const layout = fullscreenLayout;
    fullscreenLayout = null;
    if (!layout) return;
    requestAnimationFrame(() => {
      if (!pane.active) return;
      if (layout.focus?.isConnected) layout.focus.focus({ preventScroll: true });
      for (const { element, top, left } of layout.scroll) {
        element.scrollTop = top;
        element.scrollLeft = left;
      }
      window.scrollTo(layout.x, layout.y);
    });
  }
  async function toggleFullscreen() {
    if (!pane.active || fullscreenPending) return;
    if (document.fullscreenElement !== stage && noteMode !== "canvas") { if (!(await setMode("canvas"))) return; await tick(); }
    if (!pane.active) return;
    fullscreenPending = true;
    try {
      if (document.fullscreenElement === stage) {
        if (gesture) for (const id of [...pointers.keys()]) finishPointer(id);
        await document.exitFullscreen();
        finishFullscreenLayout();
        void saveContent();
        void saveView();
      } else if (stage && pageId && presentation === "editor") {
        const scroll: NonNullable<typeof fullscreenLayout>["scroll"] = [];
        for (const element of document.querySelectorAll<HTMLElement>("body *")) {
          if (element.scrollTop || element.scrollLeft)
            scroll.push({ element, top: element.scrollTop, left: element.scrollLeft });
        }
        fullscreenLayout = {
          x: window.scrollX, y: window.scrollY,
          focus: document.activeElement instanceof HTMLElement ? document.activeElement : null,
          scroll,
        };
        // Fullscreen only the existing stage: no canvas remount or sidebar state changes.
        await stage.requestFullscreen();
        if (!pane.active) { await document.exitFullscreen(); return; }
        fullscreen = document.fullscreenElement === stage;
        if (pane.active) canvas?.focus({ preventScroll: true });
      }
    } catch (cause) {
      if (document.fullscreenElement !== stage) finishFullscreenLayout();
      error = `Canvas fullscreen is unavailable: ${(cause as Error).message}. Allow fullscreen in your browser or desktop host and try again.`;
    } finally {
      fullscreenPending = false;
    }
  }
  function selectGraphNode(node: SectionNode) {
    if(node.entity==="page"&&typeof node.ref==="number")void openPage(node.ref);
    else if(node.entity==="notebook"&&typeof node.ref==="number")void notebookSchema(node.ref);
    else if(node.entity==="folder"&&typeof node.ref==="number"){schemaFocus=`folder:${node.ref}`;folderFocus=node.ref;}
  }
  async function createPage() {
    if (!notebookId) return;
    if (!(await beforeSwitch())) return;
    try {
      const page = await api<Page>("/notes/pages", {
        notebook_id: notebookId,
        title: "Untitled page",
      });
      await loadIndex();
      await openPage(page.id,false,true);
    } catch (cause) {
      error = (cause as Error).message;
    }
  }
  async function deletePage() {
    if (
      !pane.active || !pageId ||
      !confirm(
        "Move this page to Trash? You can restore it from Trash for 30 days.",
      )
    )
      return;
    try {
      await saveContent();
      if (dirty) return;
      const id = pageId;
      const remove = async () => {
        await api(`/notes/pages/${id}/delete`, { revision: pageRevision, confirm: true });
        return null;
      };
      await (noteOutline ? noteOutline.exclusive(remove) : remove());
      try {
        sessionStorage.removeItem(recoveryKey());
      } catch {
        /* best effort */
      }
      openTabs=openTabs.filter(id=>id!==pageId);persistTabs();
      pageId = 0;
      doc = emptyDocument();
      title="";
      presentation="map";
      await loadIndex();
      if(openTabs.length)await openPage(openTabs[openTabs.length-1]);
    } catch (cause) {
      error = (cause as Error).message;
    }
  }
  function applyUndo(redo = false) {
    if (gesture) cancelGesture();
    const source = redo ? redoStack : undoStack;
    if (!source.length) return;
    const next = source[source.length - 1];
    if (redo) {
      redoStack = source.slice(0, -1);
      undoStack = [...undoStack, clone(doc)];
    } else {
      undoStack = source.slice(0, -1);
      redoStack = [...redoStack, clone(doc)];
    }
    doc = clone(next);
    selected = selected.filter((id) => doc.elements.some((e) => e.id === id));
    scheduleContent();
  }
  function eraseAt(p: Point) {
    const ids = new Set(
      doc.elements
        .filter(
          (e) =>
            editable(e, doc) && hit(e, p, lineWidth / view.zoom, doc.elements),
        )
        .map((e) => e.id),
    );
    if (ids.size) doc = removeElements(doc, ids);
  }
  function localPoint(event: PointerEvent): Point {
    const rect = canvas!.getBoundingClientRect();
    return [
      event.clientX - rect.left,
      event.clientY - rect.top,
      event.pressure > 0 ? event.pressure : 0.5,
    ];
  }
  function pointerDown(event: PointerEvent) {
    if (!pane.active || !pageId || busy || loading || tabSwitchPending || !canvas) return;
    if (consumedPointers.size && event.pointerType === "touch") {
      event.preventDefault(); consumedPointers.add(event.pointerId); canvas.setPointerCapture(event.pointerId); return;
    }
    if (event.pointerType === "pen" && fullscreen && event.button === 0) {
      const point = localPoint(event);
      const side = noteTabEdge(point,width,height);
      if (side) {
        event.preventDefault();
        finishActiveGesture();
        penGuardUntil = Date.now() + 500;
        penActive=event.pointerId;
        edgeContacts.set(event.pointerId, {side, start: point, time: performance.now(), moved: false});
        canvas.setPointerCapture(event.pointerId);
        return;
      }
    }
    if (event.pointerType==="pen") lastEdgeTap=null;
    const input = drawingInput(
      event,
      tool,
      penOnly,
      penActive !== null,
      Date.now() < penGuardUntil,
    );
    event.preventDefault();
    if (
      input === "ignore" ||
      (event.pointerType === "pen" &&
        penActive !== null &&
        penActive !== event.pointerId)
    )
      return;
    if (event.pointerType === "pen") {
      if (gesture) cancelGesture();
      penActive = event.pointerId;
      penGuardUntil = Date.now() + 500;
    }
    canvas.focus({ preventScroll: true });
    const screen = localPoint(event);
    if (event.pointerType === "touch") touchPointers.add(event.pointerId);
    pointers.set(event.pointerId, screen);
    canvas.setPointerCapture(event.pointerId);
    if (pointers.size === 2) {
      if (gesture) cancelGesture(false);
      const [a, b] = [...pointers.values()];
      const ids = [...pointers.keys()] as [number, number];
      touchPair = ids.every(id => touchPointers.has(id))
        ? {starts: [[...a], [...b]], ids, started: performance.now(), eligible: true, scaleDriftSince: null}
        : null;
      gesture = {
        mode: "pinch",
        pointer: event.pointerId,
        start: world(screen[0], screen[1], view),
        last: screen,
        before: clone(doc),
        view: clone(view),
        selected: [...selected],
        distance: Math.max(1, Math.hypot(a[0] - b[0], a[1] - b[1])),
        centroid: [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2, 1],
      };
      return;
    }
    if (pointers.size > 2 || gesture) { if (touchPair) touchPair.eligible = false; return; }
    const p = world(screen[0], screen[1], view);
    p[2] = screen[2];
    const mode = space ? "pan" : input;
    const base = {
      pointer: event.pointerId,
      start: p,
      last: p,
      before: clone(doc),
      view: clone(view),
      selected: [...selected],
    };
    if (mode === "pan") {
      gesture = { ...base, mode: "pan", start: screen, last: screen };
      return;
    }
    if (
      event.pointerType !== "pen" &&
      event.pointerType !== "touch" &&
      event.button !== 0
    )
      return;
    if (mode === "select") {
      const b = bounds(selectedElements);
      if (
        b &&
        Math.hypot(p[0] - b.x - b.w, p[1] - b.y - b.h) < 10 / view.zoom
      ) {
        gesture = { ...base, mode: "resize", box: b };
        return;
      }
      const found = hitTop(doc, p, 5 / view.zoom);
      if (found) {
        selected = expandGroups(
          doc,
          event.shiftKey
            ? [...new Set([...selected, found.id])]
            : selected.includes(found.id)
              ? selected
              : [found.id],
        );
        gesture = { ...base, mode: "move", selected: [...selected] };
      } else {
        if (!event.shiftKey) selected = [];
        gesture = { ...base, mode: "rect-select" };
        overlay = [p, p];
      }
      return;
    }
    if (mode === "lasso") {
      gesture = { ...base, mode: "lasso" };
      overlay = [p];
      return;
    }
    if (mode === "eraser") {
      gesture = { ...base, mode: "erase" };
      eraseAt(p);
      return;
    }
    const layer = doc.layers.find((l) => l.id === layerId);
    if (!layer?.visible || layer.locked) {
      error = "Choose a visible unlocked layer to draw.";
      return;
    }
    if (mode === "connector") {
      const found = hitTop(doc, p, 6 / view.zoom);
      if (!found) return;
      if (selected.length !== 1 || selected[0] === found.id) {
        selected = [found.id];
        saveStatus = "Choose the second element to connect";
        return;
      }
      const from = doc.elements.find((e) => e.id === selected[0]);
      if (!from) { selected=[found.id]; return; }
      const e: CanvasElement = {
        id: uid(),
        kind: "connector",
        layer: layerId,
        x: Math.min(from.x, found.x),
        y: Math.min(from.y, found.y),
        w: Math.abs(found.x - from.x),
        h: Math.abs(found.y - from.y),
        color,
        fill: "none",
        width: lineWidth,
        points: [
          [0, 0, 1],
          [1, 1, 1],
        ],
        from: from.id,
        to: found.id,
      };
      commit({ ...doc, elements: [...doc.elements, e] });
      selected = [e.id];
      return;
    }
    const x = doc.snap ? Math.round(p[0] / 25) * 25 : p[0],
      y = doc.snap ? Math.round(p[1] / 25) * 25 : p[1];
    const e: CanvasElement = {
      id: uid(),
      kind: mode as ElementKind,
      layer: layerId,
      x,
      y,
      w: 0,
      h: 0,
      color,
      fill: mode === "sticky" ? fillColor : "none",
      width: lineWidth,
    };
    if (mode === "text" || mode === "sticky") {
      e.w = mode === "sticky" ? 240 : 320;
      e.h = mode === "sticky" ? 180 : 100;
      e.text = mode === "sticky" ? "New sticky note" : "New text";
      e.fontSize = fontSize;
      commit({ ...doc, elements: [...doc.elements, e] });
      selected = [e.id];
      outline = true;
      return;
    }
    e.points = [[0, 0, p[2]]];
    if (mode !== "pen" && mode !== "highlighter") e.points.push([0, 0, p[2]]);
    doc = { ...doc, elements: [...doc.elements, e] };
    gesture = { ...base, start: [x, y, p[2]], mode: "draw", id: e.id };
  }
  function pointerMove(event: PointerEvent) {
    if (consumedPointers.has(event.pointerId)) { event.preventDefault(); return; }
    const edge = edgeContacts.get(event.pointerId);
    if (edge && canvas) {
      const point = localPoint(event);
      if (Math.hypot(point[0] - edge.start[0], point[1] - edge.start[1]) > 10) edge.moved = true;
      event.preventDefault(); return;
    }
    if (event.pointerType === "pen") penGuardUntil = Date.now() + 500;
    if (!pointers.has(event.pointerId) || !canvas) return;
    const screen = localPoint(event);
    pointers.set(event.pointerId, screen);
    if (!gesture) return;
    event.preventDefault();
    if (gesture.mode === "pinch") {
      if (pointers.size < 2) return;
      const [a, b] = [...pointers.values()];
      const c: Point = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2, 1],
        distance = Math.hypot(a[0] - b[0], a[1] - b[1]);
      if (touchPair) {
        if (Math.abs(c[1]-gesture.centroid![1])>45) touchPair.eligible=false;
        if (Math.abs(distance/gesture.distance!-1)>0.08) {
          touchPair.scaleDriftSince ??= performance.now();
          if (performance.now()-touchPair.scaleDriftSince>100) touchPair.eligible=false;
        } else touchPair.scaleDriftSince=null;
      }
      view = zoomAt(
        gesture.view,
        gesture.centroid![0],
        gesture.centroid![1],
        distance / gesture.distance!,
      );
      view = {
        ...view,
        x: view.x + c[0] - gesture.centroid![0],
        y: view.y + c[1] - gesture.centroid![1],
      };
      return;
    }
    if (event.pointerId !== gesture.pointer) return;
    if (gesture.mode === "pan") {
      view = {
        ...gesture.view,
        x: gesture.view.x + screen[0] - gesture.start[0],
        y: gesture.view.y + screen[1] - gesture.start[1],
      };
      return;
    }
    const p = world(screen[0], screen[1], view);
    p[2] = screen[2];
    const dx = p[0] - gesture.start[0],
      dy = p[1] - gesture.start[1];
    if (gesture.mode === "draw") {
      const e = doc.elements.find((e) => e.id === gesture!.id);
      if (!e) return;
      if (e.kind === "pen" || e.kind === "highlighter") {
        const events = event.getCoalescedEvents?.() ?? [event];
        for (const sample of events.length ? events : [event]) {
          const s = localPoint(sample),
            q = world(s[0], s[1], view);
          q[2] = s[2];
          if (e.points!.length >= 20000) {
            error = "Stroke limit reached; lift the pen to continue.";
            break;
          }
          const last = e.points!.at(-1)!;
          if (
            Math.hypot(q[0] - e.x - last[0], q[1] - e.y - last[1]) >
            0.2 / view.zoom
          )
            e.points!.push([q[0] - e.x, q[1] - e.y, q[2]]);
        }
        doc = { ...doc, elements: [...doc.elements] };
      } else {
        const endX = doc.snap ? Math.round(p[0] / 25) * 25 : p[0],
          endY = doc.snap ? Math.round(p[1] / 25) * 25 : p[1],
          x = Math.min(gesture.start[0], endX),
          y = Math.min(gesture.start[1], endY);
        const next = {
          ...e,
          x,
          y,
          w: Math.abs(endX - gesture.start[0]),
          h: Math.abs(endY - gesture.start[1]),
          points: [
            [gesture.start[0] - x, gesture.start[1] - y, 1],
            [endX - x, endY - y, 1],
          ] as Point[],
        };
        doc = {
          ...doc,
          elements: doc.elements.map((v) => (v.id === e.id ? next : v)),
        };
      }
    } else if (gesture.mode === "erase") {
      const steps = Math.min(
        100,
        Math.max(
          1,
          Math.ceil(
            Math.hypot(p[0] - gesture.last[0], p[1] - gesture.last[1]) /
              Math.max(1, lineWidth / view.zoom),
          ),
        ),
      );
      for (let i = 1; i <= steps; i++)
        eraseAt([
          gesture.last[0] + ((p[0] - gesture.last[0]) * i) / steps,
          gesture.last[1] + ((p[1] - gesture.last[1]) * i) / steps,
          1,
        ]);
    } else if (gesture.mode === "move") {
      const mx = doc.snap ? Math.round(dx / 25) * 25 : dx,
        my = doc.snap ? Math.round(dy / 25) * 25 : dy;
      doc = {
        ...doc,
        elements: gesture.before.elements.map((e) =>
          gesture!.selected.includes(e.id)
            ? { ...e, x: e.x + mx, y: e.y + my }
            : e,
        ),
      };
    } else if (gesture.mode === "resize") {
      const b = gesture.box!,
        next = { ...b, w: Math.max(10, b.w + dx), h: Math.max(10, b.h + dy) };
      doc = {
        ...doc,
        elements: gesture.before.elements.map((e) =>
          gesture!.selected.includes(e.id) && editable(e, gesture!.before)
            ? resizeElement(e, b, next)
            : e,
        ),
      };
    } else if (gesture.mode === "rect-select") {
      overlay = [
        gesture.start,
        [p[0], gesture.start[1], 1],
        p,
        [gesture.start[0], p[1], 1],
        gesture.start,
      ];
    } else if (gesture.mode === "lasso") {
      overlay = [...overlay, p];
    }
    gesture.last = p;
  }
  function pointerUp(event: PointerEvent) {
    if (consumedPointers.delete(event.pointerId)) {
      event.preventDefault();
      touchPointers.delete(event.pointerId);
      if (canvas?.hasPointerCapture(event.pointerId)) canvas.releasePointerCapture(event.pointerId);
      return;
    }
    const edge = edgeContacts.get(event.pointerId);
    if (edge) {
      pointerMove(event);
      event.preventDefault();
      if (penActive===event.pointerId) penActive=null;
      edgeContacts.delete(event.pointerId);
      if (canvas?.hasPointerCapture(event.pointerId)) canvas.releasePointerCapture(event.pointerId);
      const now = performance.now();
      if (!edge.moved && now - edge.time <= 260) {
        if (lastEdgeTap && lastEdgeTap.side === edge.side && now - lastEdgeTap.time <= 420 && Math.abs(lastEdgeTap.y - edge.start[1]) <= 40) {
          lastEdgeTap = null; void switchNoteTab(edge.side);
        } else lastEdgeTap = {side: edge.side, y: edge.start[1], time: now};
      } else lastEdgeTap = null;
      penGuardUntil = Date.now() + 500;
      return;
    }
    if (!pointers.has(event.pointerId)) return;
    pointerMove(event);
    if (gesture?.mode === "pinch" && touchPair?.eligible) {
      const [a,b]=touchPair.ids.map(id=>pointers.get(id));
      const direction=a&&b?noteTabSwipe(touchPair.starts,[a,b],width,performance.now()-touchPair.started):0;
      if (direction) {
        view=gesture.view;gesture=null;touchPair=null;
        for (const id of pointers.keys()) consumedPointers.add(id);
        pointers.clear();touchPointers.clear();overlay=[];
        consumedPointers.delete(event.pointerId);
        if (canvas?.hasPointerCapture(event.pointerId))canvas.releasePointerCapture(event.pointerId);
        resumeAutosave();
        void switchNoteTab(direction);
        return;
      }
    }
    finishPointer(event.pointerId);
  }
  function finishPointer(pointerId: number) {
    pointers.delete(pointerId);
    touchPointers.delete(pointerId);
    if (pointerId === penActive) {
      penActive = null;
      penGuardUntil = Date.now() + 500;
    }
    if (canvas?.hasPointerCapture(pointerId))
      canvas.releasePointerCapture(pointerId);
    if (!gesture) return;
    if (gesture.mode === "pinch") {
      touchPair = null;
      if (pointers.size>=2) {
        const [a,b]=[...pointers.values()];
        gesture={...gesture,view:clone(view),distance:Math.max(1,Math.hypot(a[0]-b[0],a[1]-b[1])),centroid:[(a[0]+b[0])/2,(a[1]+b[1])/2,1]};
        return;
      }
      scheduleView();
      const next = [...pointers.entries()][0];
      if (next)
        gesture = {
          mode: "pan",
          pointer: next[0],
          start: next[1],
          last: next[1],
          before: clone(doc),
          view: clone(view),
          selected: [...selected],
        };
      else {
        gesture = null;
        resumeAutosave();
      }
      return;
    }
    if (gesture.pointer !== pointerId) return;
    const g = gesture;
    gesture = null;
    if (g.mode === "pan") {
      scheduleView();
      resumeAutosave();
      return;
    }
    if (g.mode === "rect-select" || g.mode === "lasso") {
      selected = expandGroups(doc, [
        ...g.selected,
        ...lassoSelection(doc, overlay),
      ]);
      overlay = [];
      resumeAutosave();
      return;
    }
    if (g.mode === "draw")
      doc = {
        ...doc,
        elements: doc.elements.map((e) =>
          e.id === g.id ? finishStroke(e) : e,
        ),
      };
    if (JSON.stringify(g.before) !== JSON.stringify(doc)) {
      doc = finishGesture(g.before, doc, false);
      remember(g.before);
      scheduleContent();
    }
    overlay = [];
    resumeAutosave();
  }
  function patchCanvasElement(id: string, patch: Partial<CanvasElement>) {
    const element = doc.elements.find(item=>item.id===id);
    if (!element || !editable(element,doc)) return;
    commit({...doc,elements:doc.elements.map(item=>item.id===id?{...item,...patch}:item)});
  }
  function cancelGesture(clear = true) {
    if (gesture) {
      doc = finishGesture(gesture.before, doc, true);
      view = gesture.view;
      selected = gesture.selected;
      gesture = null;
    }
    overlay = [];
    if (clear) {
      penActive = null;
      penGuardUntil = Date.now() + 500;
      for (const id of pointers.keys())
        if (canvas?.hasPointerCapture(id)) canvas.releasePointerCapture(id);
      pointers.clear();
      touchPair = null; touchPointers.clear();
    }
    resumeAutosave();
  }
  function wheel(event: WheelEvent) {
    if (!pageId) return;
    event.preventDefault();
    if (gesture) return;
    const r = canvas!.getBoundingClientRect();
    if (event.ctrlKey || event.metaKey || !event.shiftKey)
      view = zoomAt(
        view,
        event.clientX - r.left,
        event.clientY - r.top,
        Math.exp(-event.deltaY * 0.0015),
      );
    else view = { ...view, x: view.x - event.deltaY, y: view.y - event.deltaX };
    scheduleView();
  }
  function deleteSelected() {
    if (!selected.length) return;
    const ids = new Set(
      selectedElements.filter((e) => editable(e, doc)).map((e) => e.id),
    );
    commit(removeElements(doc, ids));
    selected = [];
  }
  function contextDeleteDrawing(event: MouseEvent) {
    event.preventDefault();
    if (!pageId || !canvas || busy || loading || gesture) return;
    const rect = canvas.getBoundingClientRect();
    const found = hitTop(doc, world(event.clientX - rect.left, event.clientY - rect.top, view), 5 / view.zoom);
    if (!found || !editable(found, doc)) return;
    selected = expandGroups(doc, selected.includes(found.id) ? selected : [found.id]);
    if (pane.active && window.confirm("Delete the selected drawing? Locked layers are kept. You can undo this.")) deleteSelected();
  }
  function groupSelection(ungroup = false) {
    if (!selected.length) return;
    const group = ungroup ? undefined : uid();
    commit({
      ...doc,
      elements: doc.elements.map((e) =>
        selected.includes(e.id) && editable(e, doc) ? { ...e, group } : e,
      ),
    });
  }
  function patchText(id: string, value: string) {
    const target = doc.elements.find((e) => e.id === id);
    if (!target || !editable(target, doc)) return;
    commit({
      ...doc,
      elements: doc.elements.map((e) =>
        e.id === id ? { ...e, text: value } : e,
      ),
    });
  }
  function patchLayer(
    id: string,
    patch: Partial<CanvasDocument["layers"][number]>,
  ) {
    commit({
      ...doc,
      layers: doc.layers.map((l) => (l.id === id ? { ...l, ...patch } : l)),
    });
    selected = selected.filter((id) => {
      const e = doc.elements.find((e) => e.id === id);
      return e && editable(e, doc);
    });
  }
  function reorderLayer(id: string, delta: number) {
    const index = doc.layers.findIndex((l) => l.id === id),
      to = index + delta;
    if (to < 0 || to >= doc.layers.length) return;
    const layers = [...doc.layers];
    [layers[index], layers[to]] = [layers[to], layers[index]];
    commit({ ...doc, layers });
  }
  function reorderSelection(front: boolean) {
    const ids = new Set(
      selectedElements.filter((e) => editable(e, doc)).map((e) => e.id),
    );
    const picked = doc.elements.filter((e) => ids.has(e.id)),
      other = doc.elements.filter((e) => !ids.has(e.id));
    commit({
      ...doc,
      elements: front ? [...other, ...picked] : [...picked, ...other],
    });
  }
  function keydown(event: KeyboardEvent) {
    // App also blurs a focused search field on Escape; the drawer closes either way.
    if (pane.active && libraryOpen && event.key === "Escape") {
      event.preventDefault(); event.stopPropagation(); void openLibrary(false); return;
    }
    if (!pane.active || presentation !== "editor" || event.defaultPrevented || !(event.target instanceof HTMLElement))
      return;
    if(event.ctrlKey&&event.altKey&&event.key.toLowerCase()==="n"){event.preventDefault();void createPage();return;}
    if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==="s"){event.preventDefault();if(noteMode==="map")void mapEditor?.flush();retrySave();return;}
    if((event.ctrlKey||event.metaKey)&&!event.shiftKey&&!event.altKey&&event.key.toLowerCase()==="f"&&noteMode!=="map"&&pageId){event.preventDefault();findOpen=true;return;}
    const target = event.target as HTMLElement;
    if (
      ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName) ||
      target.isContentEditable ||
      target.closest("button, a, summary")
    )
      return;
    if(noteMode!=="canvas")return;
    if (event.key === " ") {
      space = true;
      event.preventDefault();
      event.stopPropagation();
      return;
    }
    if (event.key === "Escape") {
      cancelGesture();
      selected = [];
      event.stopPropagation();
      return;
    }
    if (event.ctrlKey || event.metaKey) {
      if (event.key.toLowerCase() === "z") {
        event.preventDefault();
        event.stopPropagation();
        applyUndo(event.shiftKey);
      } else if (event.key.toLowerCase() === "y") {
        event.preventDefault();
        event.stopPropagation();
        applyUndo(true);
      } else if (event.key.toLowerCase() === "g") {
        event.preventDefault();
        event.stopPropagation();
        groupSelection(event.shiftKey);
      }
      return;
    }
    if (["Delete", "Backspace"].includes(event.key)) {
      event.preventDefault();
      event.stopPropagation();
      deleteSelected();
      return;
    }
    const next = tools.find(
      (t) => t.key.toLowerCase() === event.key.toLowerCase(),
    );
    if (next) {
      tool = next.id;
      event.preventDefault();
      event.stopPropagation();
    }
    if (event.key === "0") {
      view = { x: 0, y: 0, zoom: 1 };
      scheduleView();
      event.stopPropagation();
    }
  }
  async function awaitImport<T>(started: { task: TaskRecord }): Promise<T> {
    let task = started.task;
    while (task.status === "running") {
      await new Promise((resolve) => setTimeout(resolve, 500));
      task = await api<TaskRecord>(`/tasks/${task.id}`);
    }
    if (task.status !== "completed")
      throw new Error(task.error ?? "Local import failed");
    return task.result as T;
  }
  async function importReference(event: Event) {
    const input = event.target as HTMLInputElement,
      file = input.files?.[0];
    input.value = "";
    if (!file || !pageId) return;
    if (file.size > 12 * 1024 * 1024) {
      error = "Import is limited to 12 MiB.";
      return;
    }
    const layer = doc.layers.find((l) => l.id === layerId);
    if (!layer?.visible || layer.locked) {
      error = "Choose a visible unlocked layer before importing.";
      return;
    }
    busy = true;
    error = "";
    operationStatus = "Rasterizing reference locally…";
    try {
      const data = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result).split(",")[1]);
        reader.onerror = () => reject(new Error("Cannot read file"));
        reader.readAsDataURL(file);
      });
      const result = await awaitImport<{
        attachments: { id: string; width: number; height: number }[];
      }>(await api("/notes/attachments", { data, name: file.name }));
      const center = world(width / 2, height / 2, view);
      let y = center[1];
      const added = result.attachments.map((a) => {
        const w = Math.min(800, a.width),
          h = (a.height * w) / a.width;
        const e: CanvasElement = {
          id: uid(),
          kind: "image",
          layer: layerId,
          x: center[0] - w / 2,
          y,
          w,
          h,
          color,
          fill: "none",
          width: 1,
          attachment: a.id,
        };
        y += h + 30;
        return e;
      });
      commit({ ...doc, elements: [...doc.elements, ...added] });
      selected = added.map((e) => e.id);
    } catch (cause) {
      error = (cause as Error).message;
    } finally {
      busy = false;
      operationStatus = "";
    }
  }
  async function exportPage(kind: "json" | "svg" | "png") {
    if (!pageId) return;
    busy = true;
    error = "";
    try {
      const name =
        (title || "Notebook page")
          .replace(/[^a-zA-Z0-9 _-]/g, "")
          .slice(0, 80) || "Notebook page";
      if (kind === "json") {
        // Export the actual local content even when a stale-tab save is blocked.
        const refs = [
          ...new Set(
            doc.elements
              .filter((e) => e.kind === "image")
              .map((e) => e.attachment!),
          ),
        ];
        const attachments = [];
        let bytes = 0;
        for (const id of refs) {
          const response = await apiFetch(`/notes/attachments/${id}`);
          if (!response.ok)
            throw new Error("Reference unavailable; export stopped");
          const blob = await response.blob();
          bytes += blob.size;
          if (bytes > 12 * 1024 * 1024)
            throw new Error(
              "Portable page exceeds 12 MiB of references. Split references across pages.",
            );
          const data = await new Promise<string>((resolve, reject) => {
            const r = new FileReader();
            r.onload = () => resolve(String(r.result).split(",")[1]);
            r.onerror = () => reject(new Error("Cannot export reference"));
            r.readAsDataURL(blob);
          });
          attachments.push({ id, name: "Reference.png", data });
        }
        await noteOutline?.flush();
        const blocks = (await api<BlocksResult>(`/notes/pages/${pageId}/blocks`)).blocks;
        downloadBlob(
          new Blob(
            [
              JSON.stringify(
                {
                  format: "kitty-notes",
                  version: 1,
                  title,
                  notebook: {
                    title: activeNotebook?.title ?? "",
                    subject: activeNotebook?.subject ?? "",
                    project: activeNotebook?.project ?? "",
                  },
                  document: doc,
                  blocks,
                  viewport: view,
                  attachments,
                },
                null,
                2,
              ),
            ],
            { type: "application/json" },
          ),
          `${name}.kitty.json`,
        );
      } else {
        const svg = await exportSVG(doc, paper);
        downloadBlob(
          kind === "svg"
            ? new Blob([svg], { type: "image/svg+xml" })
            : await svgPNG(svg),
          `${name}.${kind}`,
        );
      }
    } catch (cause) {
      error = (cause as Error).message;
    } finally {
      busy = false;
    }
  }
  async function importPage(event: Event) {
    const input = event.target as HTMLInputElement,
      file = input.files?.[0];
    input.value = "";
    if (!file || (!restoreNotebook && !notebookId)) return;
    if (file.size > 20 * 1024 * 1024) {
      error = "Portable JSON is limited to 20 MiB.";
      return;
    }
    busy = true;
    operationStatus = "Restoring page locally…";
    try {
      await saveContent();
      if (dirty) return;
      const page = await awaitImport<Page>(
        await api("/notes/import", {
          notebook_id: restoreNotebook ? 0 : notebookId,
          page: JSON.parse(await file.text()),
        }),
      );
      await loadIndex();
      await openPage(page.id);
    } catch (cause) {
      error = (cause as Error).message;
    } finally {
      busy = false;
      operationStatus = "";
    }
  }
  function makeFlashcards() {
    if (!selectedText) return;
    if (onflashcards) onflashcards(selectedText, pageId);
    else
      window.dispatchEvent(
        new CustomEvent("kitty-study-text", {
          detail: {
            purpose: "flashcards",
            text: selectedText,
            page_id: pageId,
          },
        }),
      );
  }
  function previewSummary() {
    if (!selectedText) return;
    if (!prefs.allow_ai) {
      error =
        "Enable selected-text AI sharing under Local privacy first. No notebook content has been sent.";
      return;
    }
    summarySelection = selectedText;
  }
  async function savePrivacy() {
    try {
      prefs = await api("/notes/preferences", {
        revision: prefs.revision,
        allow_ai: prefs.allow_ai,
        retention_days: Number(prefs.retention_days),
      });
    } catch (cause) {
      error = (cause as Error).message;
    }
  }
  function retrySave() {
    blocked = false;
    error = "";
    void saveContent();
  }
  function reloadSaved() {
    if (dirty && !confirm("Discard local edits and reload the saved page?")) return;
    dirty = false;
    blocked = false;
    try {
      sessionStorage.removeItem(recoveryKey());
    } catch {
      /* best effort */
    }
    void openPage(pageId, true);
  }
  const favourite = $derived(Boolean((pages.find(p=>p.id===pageId) as PageSummary & {favorite?:boolean|number})?.favorite));
  /** Q6: the page header's ⋯, its last control. */
  const pageActions = $derived<OverflowItem[]>([
    { label: "Today's page", onselect: () => void dailyNote() },
    { label: "Library overview", onselect: () => void rootSchema(), disabled: presentation === "map" },
    { label: "Import PDF or Markdown…", onselect: () => { importOpen = true; } },
    { label: favourite ? "Unfavourite" : "Favourite", onselect: () => void favoritePage(), disabled: !pageId },
    { label: "Page history…", onselect: () => { historyOpen = true; }, disabled: !pageId },
    { label: "Trash…", onselect: () => { trashOpen = true; } },
    ...(noteMode === "canvas" ? [{ label: "Canvas fullscreen", onselect: () => void toggleFullscreen(), disabled: !pageId || presentation !== "editor" }] : []),
    { label: "Privacy & storage…", onselect: () => { cleanupResult = ""; privacyOpen = true; } },
    { label: "Reload saved", onselect: reloadSaved, disabled: !pageId || loading },
    { separator: true },
    { label: "Delete page…", onselect: () => void deletePage(), danger: true, disabled: !pageId || busy },
  ]);
  async function cleanup() {
    if (
      !pane.active ||
      !confirm(
        "Remove unreferenced image/PDF rasters older than the retention setting? Drawings, pages, and referenced images are never removed.",
      )
    )
      return;
    try {
      const result = await api<{ removed: number }>("/notes/cleanup", {
        confirm: true,
      });
      cleanupResult = `Removed ${result.removed} unused ${result.removed === 1 ? "reference" : "references"}`;
    } catch (cause) {
      error = (cause as Error).message;
    }
  }
  onMount(() => {
    // The floating outline's open state is device state (pen mode is the build's `ui.notes.pen_only`).
    try {
      const outlinePreference = localStorage.getItem("kitty.notes.outline");
      tocOpen = outlinePreference ? outlinePreference === "open" : innerWidth >= 1100;
    } catch {
      tocOpen = innerWidth >= 1100;
    }
    void loadIndex().then(() => { indexReady = true; });
    followMirrors();
    const openLinkedPage=(event:Event)=>{if(!pane.active)return;const id=(event as CustomEvent).detail?.page_id;if(id)void openPage(Number(id));};
    window.addEventListener("kitty-open-note",openLinkedPage);
    // Live push replaces polling: a change saved in any window refreshes this one now.
    let indexTimer: ReturnType<typeof setTimeout> | undefined;
    const stopLive=onBlockChange(change=>{
      if(change&&change.root?.type!=="page")return;
      clearTimeout(indexTimer);indexTimer=setTimeout(()=>void loadIndex(),250);
      if(!change||change.root?.number===pageId)void refreshReferences();
    });
    const clockTimer=setInterval(()=>clock=Date.now(),30000);
    window.addEventListener("focus",refreshReferences);
    const syncColors=()=>{
      const previous=ink,style=getComputedStyle(document.documentElement);
      ink=style.getPropertyValue("--ink").trim()||CANVAS_INK;
      paper=style.getPropertyValue("--solid-void").trim();
      if(color===previous||color===CANVAS_INK)color=ink;
    };
    syncColors();window.addEventListener("kitty:theme",syncColors);
    const fullscreenChanged = () => {
      if (document.fullscreenElement === stage) fullscreen = true;
      else if (fullscreen || fullscreenLayout) {
        finishFullscreenLayout();
        void saveContent();
        void saveView();
      }
    };
    const fullscreenEscape = (event: KeyboardEvent) => {
      if (!pane.active || event.key !== "Escape" || document.fullscreenElement !== stage) return;
      event.preventDefault();
      event.stopImmediatePropagation();
      void toggleFullscreen();
    };
    document.addEventListener("fullscreenchange", fullscreenChanged);
    window.addEventListener("keydown", fullscreenEscape, true);
    const observer = new ResizeObserver((entries) => {
      const rect = entries[0]?.contentRect;
      if (rect) {
        width = Math.max(200, rect.width);
        height = Math.max(300, rect.height);
      }
    });
    if (stage) observer.observe(stage);
    const unload = (event: BeforeUnloadEvent) => {
      if (dirty) {
        keepRecovery();
        event.preventDefault();
        event.returnValue = "";
      }
    };
    const visibility = () => {
      if (document.hidden) {
        finishActiveGesture();
        void saveContent();
        void saveView();
      } else void refreshReferences();
    };
    window.addEventListener("beforeunload", unload);
    document.addEventListener("visibilitychange", visibility);
    return () => {
      observer.disconnect();
      stopLive();
      clearTimeout(indexTimer);
      clearInterval(clockTimer);
      window.removeEventListener("focus",refreshReferences);
      window.removeEventListener("kitty-open-note",openLinkedPage);
      window.removeEventListener("kitty:theme",syncColors);
      document.removeEventListener("fullscreenchange", fullscreenChanged);
      window.removeEventListener("keydown", fullscreenEscape, true);
      if (document.fullscreenElement === stage) void document.exitFullscreen();
      finishActiveGesture();
      clearTimeout(saveTimer);
      clearTimeout(viewTimer);
      clearTimeout(searchTimer);
      clearTimeout(revealTimer);
      if (dirty) keepRecovery();
      void saveContent();
      void saveView();
      window.removeEventListener("beforeunload", unload);
      document.removeEventListener("visibilitychange", visibility);
    };
  });
  $effect(() => {
    if (!pane.active) {
      space = false;
      finishActiveGesture();
      resetNavigationContacts();
      if (document.fullscreenElement === stage) void document.exitFullscreen();
      return;
    }
    if (!indexReady || pageInitialized) return;
    pageInitialized = true;
    if (initialMode === "map") noteMode = "map";
    let requested = 0;
    try {
      const stored = JSON.parse(localStorage.getItem("kitty.notes.tabs") || "[]");
      openTabs = Array.isArray(stored) ? stored.filter((id: unknown) => typeof id === "number" && pages.some(page => page.id === id)) : [];
      requested = Number(sessionStorage.getItem("kitty.notes.open-page"));
      sessionStorage.removeItem("kitty.notes.open-page");
    } catch { /* Optional saved navigation. */ }
    if (requested && pages.some(page => page.id === requested)) void openPage(requested);
    else void dailyNote();
  });
  $effect(() => {
    if (revision > 0 && !dirty && !gesture) void loadIndex();
  });
  $effect(() => {
    for (const e of doc.elements) {
      if (e.kind === "image" && e.attachment && !images.has(e.attachment)) {
        const image = new Image();
        images.set(e.attachment, image);
        const failed = () => {
          error = "A registered local reference could not load.";
        };
        // An <img> cannot carry the UI secret's header: load the bytes, then show them.
        apiObjectUrl(`/notes/attachments/${e.attachment}`).then((url) => {
          image.onload = () => { URL.revokeObjectURL(url); imageEpoch++; };
          image.onerror = () => { URL.revokeObjectURL(url); failed(); };
          image.src = url;
        }, failed);
      }
    }
  });
  $effect(() => {
    imageEpoch;
    if (pane.active && canvas && presentation === "editor" && noteMode === "canvas")
      renderCanvas(
        canvas,
        doc,
        view,
        images,
        selected,
        overlay,
        width,
        height,
        ink,
        paper,
      );
  });
</script>

<svelte:window
  onkeydown={keydown}
  onkeyup={(event) => {
    if (pane.active && event.key === " ") space = false;
  }}
/>
{#snippet pageTabs()}
  <nav class="note-tabs" aria-label="Open note pages">
    {#each openTabs as id (id)}
      {@const summary = pages.find(page=>page.id===id)}
      {@const book = notebooks.findIndex(notebook=>notebook.id===(id===pageId?notebookId:summary?.notebook_id))}
      <div class:active={id===pageId && presentation==="editor"} class="note-tab">
        <button aria-current={id===pageId && presentation==="editor"?"page":undefined} draggable={!fullscreen}
          ondragstart={()=>tabDrag=id} ondragover={event=>event.preventDefault()} ondrop={event=>{event.preventDefault();const ids=blockDrag(event);if(ids)void moveBlocksToPage(id,ids);else reorderTab(id);}}
          oncontextmenu={event=>{event.preventDefault();if(!fullscreen)pageMenu={id,x:event.clientX,y:event.clientY};}}
          onclick={()=>void openPage(id)}><i class="tab-dot" aria-hidden="true" style:background={book<0?"var(--subtle)":`var(--c${book%6+1})`}></i><span>{id===pageId?title||"Untitled":summary?.title||`Page ${id}`}</span></button>
        <button class="tab-close" aria-label={`Close ${summary?.title||"page"} tab`} onclick={()=>void closeTab(id)}><Icon name="close" size={12}/></button>
      </div>
    {/each}
    <button class="icon-button tab-new" aria-label="New page" title="New page · Ctrl Alt N" disabled={!notebookId||busy||loading} onclick={createPage}><Icon name="plus" size={14}/></button>
  </nav>
{/snippet}
<section class="notes-workspace" aria-label="Notes workspace">
  <!-- Q17: one row. Library, the open pages, the page mode, Find and ⋯ (always last). -->
  <header class="notes-heading">
    <button bind:this={libraryToggle} class="icon-button library-toggle" aria-label="Open library" aria-expanded={libraryOpen} aria-controls={`${pane.id}-notes-library`} title="Library" onclick={() => void openLibrary(!libraryOpen)}><Icon name="menu" size={16}/></button>
    {#if fullscreen}<span class="note-tabs"></span>{:else}{@render pageTabs()}{/if}
    <Select kind="segments" label="Page mode" options={PAGE_MODES} disabled={!pageId}
      bind:value={() => noteMode, (next: string) => void chooseMode(next as NoteMode)} />
    <Popover bind:open={findOpen} label="Find in page" placement="bottom-end" class="notes-find">
      {#snippet trigger(props)}
        <button class="icon-button" aria-label="Find in page" title="Find in page · Ctrl F" disabled={!pageId || noteMode === "map" || presentation !== "editor"} {...props}><Icon name="jobs" size={15}/></button>
      {/snippet}
      <input class="find-field" type="search" aria-label="Find text" placeholder="Find in page…" bind:value={pageFind}
        oninput={() => { findFrom = ""; if (pageFind && noteMode === "canvas") outline = true; }}
        onkeydown={(event) => { if (event.key === "Enter") { event.preventDefault(); findNext(); } }} />
    </Popover>
    <button class="icon-button mirror-toggle" aria-pressed={mirrorOpen} disabled={!pageId || noteMode!=="text" || presentation!=="editor"}
      aria-label={mirrors.list.length ? `Mirror (${mirrors.list.length} live)` : "Mirror"}
      title="Mirror: the browser tab the rika! extension is mirroring, beside this page"
      onclick={()=>mirrorOpen=!mirrorOpen}><Icon name="external" size={15}/>{#if mirrors.list.length}<span class="mirror-live" aria-hidden="true"></span>{/if}</button>
    <OverflowMenu items={pageActions} label="More page actions" />
  </header>
  {#if operationStatus}<p class="op-status" role="status">{operationStatus}</p>{/if}
  {#if error || blocked}<div class="error-banner" role="alert">
      <span>{error || "This page could not save. Your edits are kept here."}</span>
      {#if blocked}<button class="button" onclick={retrySave}>Retry save</button>{/if}
      {#if error}<button class="icon-button" onclick={() => (error = "")} aria-label="Dismiss notes error"><Icon name="close" size={12}/></button>{/if}
    </div>{/if}
  {#if viewError}<p class="error-banner" role="alert">
      Viewport: {viewError}. Content saves independently.
      <button
        class="button"
        onclick={() => {
          viewError = "";
          void saveView();
        }}>Retry viewport</button
      >
    </p>{/if}
  <div class="notebook-layout" use:fitHeight>
    {#if libraryOpen}<button class="library-scrim" tabindex="-1" aria-label="Close library" onclick={() => void openLibrary(false)}></button>{/if}
    <aside bind:this={libraryDrawer} id={`${pane.id}-notes-library`} class="notebook-sidebar" aria-label="Notes library" hidden={!libraryOpen}>
      <NotesLibrary {folders} {notebooks} {pages} {notebookId} {pageId} {search} {folderFocus}
        busy={loading || busy} onrefresh={loadIndex} onopenpage={openFromLibrary} onselectnotebook={notebookSchema}
        onnewpage={async () => { libraryOpen = false; await createPage(); }} onbeforeswitch={beforeSwitch} onerror={message => error = message} ondeletepage={deletePageById}
        onblocksdrop={(page, event) => { const ids = blockDrag(event); if (ids) void moveBlocksToPage(page, ids); return !!ids; }}
        onsearch={value => { search = value; clearTimeout(searchTimer); searchTimer = setTimeout(() => void loadIndex(), 250); }} />
      {#if presentation==="editor"}
        <form class="wiki-link" onsubmit={event=>{event.preventDefault();libraryOpen=false;void resolveLink();}}>
          <label for={`${pane.id}-note-link`}>[[ Link a page ]]</label>
          <input id={`${pane.id}-note-link`} bind:value={linkTitle} list={`${pane.id}-note-pages`} placeholder="Page title…" />
          <datalist id={`${pane.id}-note-pages`}>{#each pages as page}<option value={page.title}></option>{/each}</datalist>
          <Button size="sm" type="submit" disabled={!linkTitle.trim()}>Open / create</Button>
        </form>
        <Button size="sm" variant="ghost" aria-pressed={relatedOpen} onclick={()=>relatedOpen=!relatedOpen}>Connections</Button>
        {#if relatedOpen && noteMode!=="text"}<NoteReferences pageId={pageId} text={`${title}\n${textElements.map(e=>e.text||"").join("\n")}\n${outlineText}`} onopen={id=>void openFromLibrary(id)} onlink={linkMention}/>{/if}
      {/if}
    </aside>
    <div class="canvas-main">
      {#if presentation==="map" && pane.active}
        <SectionGraph section="notes" {revision} focus={schemaFocus} onselect={selectGraphNode}/>
        <div class="actions">
          {#if schemaFocus}<Button size="sm" variant="ghost" onclick={()=>void rootSchema()}>All notebooks</Button>{/if}
          {#if schemaFocus?.startsWith("notebook:")}<Button size="sm" onclick={createPage}>New page</Button>{/if}
        </div>
      {/if}
      <div class="editor-shell" hidden={presentation!=="editor"} inert={loading}>
      <div class="writing-view" hidden={noteMode!=="text"}>
        <div class="writing" class:toc-open={tocOpen && tocEntries.length>0} class:mirror-open={mirrorOpen} onfocusin={event=>{const id=(event.target as HTMLElement).closest<HTMLElement>("[data-block-id]")?.dataset.blockId;if(id)tocCurrent=topBlock(id);}}>
          <div class="page-column">
            {#if pageId}<p class="page-meta">{[activeNotebook?.title, edited].filter(Boolean).join(" · ")}</p>{/if}
            <!-- Q17: the title is the page's H1, set in the title face; the field is its content. -->
            <!-- svelte-ignore a11y_missing_content -->
            <h1 class="page-h1"><input
              class="title-input"
              aria-label="Page title"
              placeholder="Untitled"
              disabled={!pageId}
              value={title}
              maxlength="200"
              oninput={(e) => {
                title = e.currentTarget.value;
                scheduleContent();
              }}
            /></h1>
            {#if pageId}{#key pageId}<NoteOutline bind:this={noteOutline} {pageId} references={blockReferences} counts={citationCounts} pages={noteTitles} {focusBlock}
              onpage={value=>void resolveLink(value)} onopen={openBlock} onopenpage={pageBlock=>void openLinkedPage(pageBlock)} oncreatepage={createSubpage}
              mirror={mirrorOpen} onclosemirror={()=>mirrorOpen=false} onblocks={list=>outlineBlocks=list}
              onrevision={editorRevision} onerror={message=>error=message}/>{/key}{/if}
          </div>
          {#if tocEntries.length}
            <nav class="page-toc" class:open={tocOpen} aria-label="On this page">
              <button class="toc-head" aria-expanded={tocOpen} onclick={toggleToc}><span class="eyebrow">On this page</span></button>
              {#if tocOpen}
                <ol>{#each tocEntries as entry (entry.id)}<li><button class:on={tocCurrent===entry.id} aria-current={tocCurrent===entry.id?"location":undefined} onclick={()=>void jumpToBlock(entry.id)}>{entry.label}</button></li>{/each}</ol>
                <span class="toc-words">{wordCount} {wordCount===1?"word":"words"}</span>
              {/if}
            </nav>
          {/if}
        </div>
      {#if historyOpen && pageId}<BlockHistory page={{type:"page",number:pageId}} title={title} onclose={()=>historyOpen=false}/>{/if}
      {#if trashOpen}<BlockTrash onclose={()=>trashOpen=false} onopen={id=>{trashOpen=false;void loadIndex().then(()=>openPage(id));}}/>{/if}
      </div>
      {#if noteMode==="map" && pageId}
        <!-- Q19: Map is the one mind-map editor, full-bleed in the stage; it loads the page's own map. -->
        <div class="map-stage" role="region" aria-label="Page map">
          <SchemaMaps bind:this={mapEditor} embedded pageId={pageId} {revision} {demo} onstatus={(status) => mapStatus = status} />
        </div>
      {/if}
      <div class="canvas-panel" hidden={noteMode!=="canvas"}>
      <div class="canvas-stage" bind:this={stage} onpointerdowncapture={event=>{if(event.pointerType==="touch")revealTabs();}}>
        {#if fullscreen}
          <div class="focus-tab-dock" class:revealed={tabsRevealed} onpointerenter={revealTabs} role="group" aria-label="Fullscreen page navigation">
            <button class="focus-tab-handle" aria-label="Show note page tabs" onclick={revealTabs} onfocus={revealTabs}>Pages ▾</button>
            <div class="focus-tab-strip">{@render pageTabs()}</div>
          </div>
          <div class="focus-corner">
            <button class="button focus-exit" onclick={toggleFullscreen} aria-label="Exit canvas fullscreen">Exit fullscreen · Esc</button>
            <span class="focus-save" role="status" aria-live="polite">{saveStatus}</span>
            <button class="button" onclick={retrySave} title={saveStatus} aria-label={`Save note: ${saveStatus}`}>Save</button>
          </div>
          {#if error}<div class="focus-error" role="alert">{error}</div>{/if}
        {/if}
        <canvas
          bind:this={canvas}
          style={`width:${width}px;height:${height}px;`}
          aria-label="Infinite drawing canvas. Write with a pressure-sensitive pen. Two-finger horizontal swipe changes page tabs. In fullscreen, pen double-tap the middle of the far left or right edge to change tabs; that narrow edge is reserved. Use drawing tools or the accessible text outline for notes."
          tabindex="0"
          onblur={() => {
            space = false;
          }}
          onpointerdown={pointerDown}
          onpointermove={pointerMove}
          onpointerup={pointerUp}
          onpointercancel={(e) => {
            if(penActive===e.pointerId)penActive=null;
            consumedPointers.delete(e.pointerId); edgeContacts.delete(e.pointerId); lastEdgeTap=null;
            if (pointers.has(e.pointerId)) cancelGesture();
          }}
          onlostpointercapture={(e) => {
            if(penActive===e.pointerId && edgeContacts.has(e.pointerId))penActive=null;
            consumedPointers.delete(e.pointerId);
            if(edgeContacts.delete(e.pointerId))lastEdgeTap=null;
            if (
              gesture &&
              gesture.pointer === e.pointerId &&
              pointers.has(e.pointerId)
            )
              cancelGesture();
          }}
          onwheel={wheel}
          oncontextmenu={contextDeleteDrawing}
        ></canvas>
        {#if !pageId}<div class="canvas-empty">
            <h3>A place for your next idea</h3>
            <p>Create a notebook and page to start drawing.</p>
          </div>{/if}
        <CanvasPill label="Drawing tools" id={`${pane.id}-drawing-tools`} storageKey="kitty.notes.canvas-tools" moreLabel="More canvas tools" menuLabel="Canvas options" stageHeight={height} disabled={!pageId}>
          {#each pillTools as item (item.id)}
            <button class="pill-btn" aria-label={item.label} aria-keyshortcuts={item.key} aria-pressed={tool === item.id} title={`${item.label} (${item.key})`} disabled={!pageId} onclick={() => chooseTool(item.id)}><Icon name={`tool-${item.id}`} size={18}/></button>
          {/each}
          <span class="pill-rule" aria-hidden="true"></span>
          <CanvasColorMenu label="Ink" bind:value={color}/>
          <span class="pill-rule" aria-hidden="true"></span>
          <button class="pill-btn" aria-label={`Stroke size ${lineWidth}`} title="Stroke size: click for the next size" onclick={() => lineWidth = STROKE_SIZES.find(size => size > lineWidth) ?? STROKE_SIZES[0]}><span class="stroke-dot" style:width={`${Math.min(lineWidth, 12) + 2}px`} style:height={`${Math.min(lineWidth, 12) + 2}px`}></span></button>
          <span class="pill-rule" aria-hidden="true"></span>
          <button class="pill-btn" aria-label="Undo" title="Undo (Ctrl+Z)" disabled={!undoStack.length} onclick={() => applyUndo()}><Icon name="undo" size={17}/></button>
          <button class="pill-btn" aria-label="Redo" title="Redo (Ctrl+Y)" disabled={!redoStack.length} onclick={() => applyUndo(true)}><Icon name="redo" size={17}/></button>
          {#snippet more(close)}
            <span class="eyebrow">Tools</span>
            <div class="pill-menu-tools">
              {#each moreTools as item (item.id)}
                <button aria-label={item.label} aria-keyshortcuts={item.key} aria-pressed={tool === item.id} disabled={!pageId} onclick={() => { chooseTool(item.id); close(); }}><Icon name={`tool-${item.id}`} size={16}/>{item.label}<kbd aria-hidden="true">{item.key}</kbd></button>
              {/each}
            </div>
            {#if ["sticky","rect","ellipse"].includes(tool)}
              <span class="eyebrow">Fill</span>
              <CanvasColorMenu label="Fill" placement="inline" bind:value={fillColor}/>
            {/if}
            <span class="eyebrow">Stroke &amp; text</span>
            <label class="pill-range">{tool === "eraser" ? "Eraser radius" : "Stroke width"}<span><input type="range" min="1" max="60" bind:value={lineWidth}/><output>{lineWidth}</output></span></label>
            <label class="pill-range">Text size<span><input type="number" min="8" max="120" bind:value={fontSize}/></span></label>
            <span class="eyebrow">Canvas</span>
            <div class="pill-menu-row">
              <label class="check" title="Only a pen draws; touch pans"><input type="checkbox" checked={penOnly} onchange={(event) => void setPreferences({ ui: { notes: { pen_only: event.currentTarget.checked } } })}/>Pen mode</label>
              <label class="check"><input type="checkbox" checked={doc.snap} onchange={(e) => commit({ ...doc, snap: e.currentTarget.checked })}/>Snap</label>
            </div>
            <div class="pill-menu-row zoom-row">
              <button class="pill-menu-item" aria-label="Zoom out" onclick={() => { view = zoomAt(view, width / 2, height / 2, 0.8); scheduleView(); }}>−</button>
              <output>{Math.round(view.zoom * 100)}%</output>
              <button class="pill-menu-item" aria-label="Zoom in" onclick={() => { view = zoomAt(view, width / 2, height / 2, 1.25); scheduleView(); }}>＋</button>
              <button class="pill-menu-item" title="Reset zoom (0)" onclick={() => { view = { x: 0, y: 0, zoom: 1 }; scheduleView(); }}>Reset</button>
              <button class="pill-menu-item" onclick={() => { view = fit(doc.elements, width, height); scheduleView(); }}>Fit</button>
            </div>
            <div class="pill-range">Paper<Select label="Paper" kind="list" options={PAPERS} disabled={!pageId}
              bind:value={() => paperPattern(doc), (next: string) => commit({...doc, background: next as CanvasDocument["background"]})} /></div>
            <button class="pill-menu-item" aria-pressed={outline} onclick={() => (outline = !outline)}>Text outline</button>
            <button class="pill-menu-item" onclick={() => { close(); layersOpen = true; }}>Layers…<kbd aria-hidden="true">{doc.layers.length}</kbd></button>
            <span class="eyebrow">File</span>
            <label class="pill-menu-item file-button" title="PDFs up to 20 pages are rasterized locally">Import image or PDF<input
                type="file"
                accept="image/png,image/jpeg,image/webp,application/pdf"
                disabled={!pageId || busy}
                onchange={importReference}
              /></label>
            <label class="pill-menu-item file-button">Restore JSON page<input
                type="file"
                accept="application/json,.json"
                disabled={(!restoreNotebook && !notebookId) || busy}
                onchange={importPage}
              /></label>
            <label class="check restore-check"><input type="checkbox" bind:checked={restoreNotebook} />Restore into its own notebook</label>
            <button class="pill-menu-item" disabled={!pageId || busy} onclick={() => exportPage("json")}>Export JSON</button>
            <button class="pill-menu-item" disabled={!pageId || busy} onclick={() => exportPage("svg")}>Export SVG</button>
            <button class="pill-menu-item" disabled={!pageId || busy} title="Up to 4096 px" onclick={() => exportPage("png")}>Export PNG</button>
          {/snippet}
        </CanvasPill>
      </div>
      {#if selected.length}<div class="selection-actions" role="group" aria-label="Selected element actions">
        <button class="button" onclick={deleteSelected}>Delete selection</button>
        <button class="button" disabled={selected.length < 2} onclick={() => groupSelection()}>Group</button>
        <button class="button" onclick={() => groupSelection(true)}>Ungroup</button>
        <button class="button" onclick={() => reorderSelection(true)}>Bring forward</button>
        <button class="button" onclick={() => reorderSelection(false)}>Send backward</button>
        <Select label="Move selection to layer" kind="list" options={doc.layers.map(layer => ({ value: layer.id, label: layer.name }))}
          bind:value={() => layerId, moveSelectionToLayer} />
        <button class="button" disabled={!selectedText} onclick={makeFlashcards}>Make flashcards from selected text</button>
        <button class="button" disabled={!selectedText} onclick={previewSummary}>Summarize selection…</button>
      </div>
      {/if}
      {#if summarySelection && prefs.allow_ai}<section
          class="ai-review"
          aria-label="Review selected-text AI request"
        >
          <h3>Summarize only this selected text</h3>
          <AIReview
            purpose="summarize"
            text={summarySelection}
            {demo}
            onresult={(draft) => {
              summary = draft;
              summarySelection = "";
            }}
          /><button class="button" onclick={() => (summarySelection = "")}
            >Close summary review</button
          >
        </section>{/if}
      {#if summary}<section class="ai-review">
          <h3>Summary draft — verify before using</h3>
          <pre>{summary}</pre>
          <button class="button" onclick={() => (summary = "")}>Dismiss</button>
        </section>{/if}
      {#if outline}<section
          class="text-outline"
          aria-label="Accessible editable text outline"
        >
          <h3>Text outline</h3>
          {#each textElements.filter(element=>!pageFind||element.text?.toLocaleLowerCase().includes(pageFind.toLocaleLowerCase())) as element}<article>
              <label class="check"
                ><input
                  type="checkbox"
                  checked={selected.includes(element.id)}
                  onchange={(e) =>
                    (selected = e.currentTarget.checked
                      ? [...new Set([...selected, element.id])]
                      : selected.filter((id) => id !== element.id))}
                />{element.kind === "sticky" ? "Sticky note" : "Text"} · {doc.layers.find(
                  (l) => l.id === element.layer,
                )?.name}</label
              ><textarea
                aria-label={`Edit ${element.kind} ${element.id.slice(0, 8)}`}
                rows="3"
                maxlength="20000"
                readonly={!editable(element, doc)}
                value={element.text ?? ""}
                oninput={(e) => patchText(element.id, e.currentTarget.value)}
              ></textarea>
              {#if wikiQuery(element.text||"")!==null}
                <div class="wiki-suggestions">{#each pages.filter(page=>page.title.toLocaleLowerCase().includes((wikiQuery(element.text||"")||"").toLocaleLowerCase())).slice(0,6) as page}
                  <Button size="sm" variant="ghost" onclick={()=>patchText(element.id,(element.text||"").replace(/\[\[[^\]]*$/,`[[${page.title}]]`))}>{page.title}</Button>
                {/each}</div>
              {/if}
              <div class="wiki-links">{#each [...(element.text||"").matchAll(/\[\[([^\]]+)\]\]/g)] as match}<Button size="sm" variant="ghost" onclick={()=>void resolveLink(match[1])}>{match[1]} ↗</Button>{/each}</div>
              <div class="outline-row">
                <Select label="Tag" kind="list" options={TAGS} bind:value={() => element.tags?.[0] || "", (next: string) => setTag(element.id, next)} />
                <button
                  class="button"
                  onclick={() => {
                    selected = [element.id];
                    view = fit([element], width, height);
                    scheduleView();
                  }}>Locate on canvas</button
                >
              </div>
            </article>{/each}{#if !textElements.length}<p class="muted">
              Add Text or Sticky notes anywhere on the canvas.
            </p>{/if}
        </section>{/if}
      </div>
      </div>
    </div>
  </div>
</section>
{#if pageMenu}<div class="note-page-menu" role="menu" style:left={`${Math.min(pageMenu.x,window.innerWidth-180)}px`} style:top={`${Math.min(pageMenu.y,window.innerHeight-70)}px`}><button class="button" role="menuitem" onclick={()=>pageMenu&&void deletePageById(pageMenu.id)}>Delete page…</button><button class="button" role="menuitem" onclick={()=>pageMenu=null}>Cancel</button></div>{/if}
{#if importOpen}
  <EditDrawer title="Import PDF or Markdown" meta={activeNotebook?.title ?? ""} onclose={() => (importOpen = false)}>
    <DocumentImport {notebookId} onimport={id=>{importOpen=false;void loadIndex().then(()=>openBlock(id));}}/>
    {#if originals.length}
      <div class="drawer-group">
        <span class="eyebrow">Originals</span>
        {#each originals as original}<a href={`/api/documents/originals/${original.id}`} download onclick={followApiLink}>{original.name}</a>{/each}
      </div>
    {/if}
  </EditDrawer>
{/if}
{#if privacyOpen}
  <EditDrawer title="Privacy & storage" onclose={() => (privacyOpen = false)}>
    <div class="drawer-group">
      <label class="drawer-check" title="Only text you select is ever sent; whole notebooks never are"><input type="checkbox" bind:checked={prefs.allow_ai} />Allow selected-text AI sharing</label>
      <label class="drawer-field" title="0 removes unused references as soon as you clean up">Unused reference retention (days)<input type="number" min="0" max="3650" bind:value={prefs.retention_days} /></label>
    </div>
    <div class="drawer-group">
      <p class="drawer-note">Deletes unused image and PDF copies older than the retention.</p>
      <div class="drawer-row"><button class="button" onclick={cleanup}>Clean unused references</button>{#if cleanupResult}<span class="drawer-result" role="status">{cleanupResult}</span>{/if}</div>
    </div>
    {#snippet footer()}<button class="button" onclick={() => void savePrivacy()}>Save</button>{/snippet}
  </EditDrawer>
{/if}
{#if layersOpen}
  <EditDrawer title="Layers" meta="Drawn bottom to top" onclose={() => (layersOpen = false)}>
    <div class="drawer-group">
      {#each doc.layers as layer, i (layer.id)}<div class="layer-row">
          <input
            type="radio"
            name={`${pane.id}-notes-layer`}
            aria-label={`Draw on ${layer.name}`}
            title="Draw on this layer"
            checked={layerId === layer.id}
            onchange={() => (layerId = layer.id)}
          /><input
            aria-label="Layer name"
            maxlength="100"
            value={layer.name}
            onchange={(e) =>
              patchLayer(layer.id, { name: e.currentTarget.value })}
          /><label class="drawer-check" title="Hidden layers are left out of exports"
            ><input
              type="checkbox"
              checked={layer.visible}
              onchange={(e) =>
                patchLayer(layer.id, { visible: e.currentTarget.checked })}
            />Show</label
          ><label class="drawer-check" title="Locked layers cannot be drawn on, moved or erased"
            ><input
              type="checkbox"
              checked={layer.locked}
              onchange={(e) =>
                patchLayer(layer.id, { locked: e.currentTarget.checked })}
            />Lock</label
          ><button class="icon-button" aria-label={`Lower ${layer.name}`} title="Lower"
            disabled={i === 0}
            onclick={() => reorderLayer(layer.id, -1)}>↓</button
          ><button class="icon-button" aria-label={`Raise ${layer.name}`} title="Raise"
            disabled={i === doc.layers.length - 1}
            onclick={() => reorderLayer(layer.id, 1)}>↑</button
          >
        </div>{/each}
    </div>
    <form
      class="drawer-row"
      onsubmit={(e) => {
        e.preventDefault();
        if (doc.layers.length >= 32) return;
        const id = uid();
        commit({
          ...doc,
          layers: [
            ...doc.layers,
            {
              id,
              name: newLayer || "New layer",
              visible: true,
              locked: false,
            },
          ],
        });
        layerId = id;
        newLayer = "";
      }}
    >
      <input
        aria-label="New layer name"
        placeholder="New layer"
        maxlength="100"
        bind:value={newLayer}
      /><button class="button" disabled={doc.layers.length >= 32 || !pageId}
        >Add layer</button
      >
    </form>
  </EditDrawer>
{/if}

<style>
  /* Q2: the workspace is one surface; header, page and stage are split by hairlines only. */
  .notes-workspace {
    background: var(--surface);
    color: var(--ink);
    border: 1px solid var(--hairline);
    border-radius: var(--r-panel);
    /* clip, not hidden: hidden would make this the sticky outline's scroll container. */
    overflow: clip;
    display: flex;
    flex-direction: column;
  }
  /* Q17: one row. ☰, the open pages, then the page mode, Find and ⋯ on the right. */
  .notes-heading {
    display: flex;
    align-items: center;
    gap: 6px;
    height: 44px;
    padding: 0 10px 0 8px;
    box-sizing: border-box;
    border-bottom: 1px solid var(--hairline);
  }
  .notes-heading > :global(*) { flex-shrink: 0; }
  .notes-heading .note-tabs { flex: 1 1 auto; }
  .library-toggle[aria-expanded="true"] { background: var(--wash); color: var(--ink); }
  :global(.kitty-popover.notes-find) { width: 260px; padding: 8px; }
  .find-field { width: 100%; box-sizing: border-box; }

  /* Q43: the open pages as pill tabs on the control line. */
  .note-tabs { display: flex; align-items: center; gap: 2px; min-width: 0; overflow-x: auto; scrollbar-width: none; }
  .note-tab { display: flex; align-items: center; flex-shrink: 0; max-width: 200px; height: var(--control-h); padding: 0 4px 0 12px; box-sizing: border-box; border-radius: var(--r-control); font-size: var(--fs-control); color: var(--muted); }
  .note-tab:hover { background: var(--wash); color: var(--ink); }
  .note-tab.active { background: color-mix(in srgb, var(--ink) 8%, transparent); color: var(--ink); }
  .note-tab > button:first-child { display: flex; align-items: center; gap: 7px; min-width: 0; height: 100%; padding: 0; background: none; color: inherit; font: inherit; }
  .note-tab > button:first-child span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .tab-dot { width: 6px; height: 6px; border-radius: 50%; flex-shrink: 0; }
  .tab-close { display: grid; place-items: center; width: 20px; height: 20px; margin-left: 4px; padding: 0; border-radius: var(--r-control); background: none; color: var(--subtle); opacity: 0; }
  .note-tab:hover .tab-close, .note-tab.active .tab-close, .tab-close:focus-visible { opacity: 1; }
  .tab-close:hover { background: var(--wash); color: var(--ink); }
  .note-tabs .tab-new { flex-shrink: 0; margin-left: 2px; }
  .note-page-menu { position: fixed; z-index: 90; display: flex; gap: 8px; padding: 8px; background: var(--surface); border: 1px solid var(--hairline); border-radius: var(--r-menu); box-shadow: var(--overlay-shadow); }

  .op-status { margin: 6px 14px 0; color: var(--muted); font-size: 12px; }
  .error-banner { display: flex; align-items: center; gap: 8px; margin: 8px 12px 0; padding: 6px 6px 6px 12px; border: 1px solid color-mix(in srgb, var(--warning) 55%, transparent); border-radius: var(--r-field); color: var(--warning); font-size: 12.5px; overflow-wrap: anywhere; }
  .error-banner > span { flex: 1; }

  /* The canvas and map fill the window above the dock under whatever sits above the page (fitHeight). */
  .notebook-layout { --stage-h: var(--fit-h, 480px); position: relative; display: flex; flex-direction: column; flex: 1; min-height: 0; }
  .notebook-layout > .canvas-main { flex: 1; }
  /* Q43: the library slides over the page instead of taking a column. */
  .library-scrim { position: absolute; inset: 0; z-index: 20; padding: 0; border: 0; cursor: default; background: color-mix(in srgb, var(--shadow-tint) 40%, transparent); }
  .notebook-sidebar {
    position: absolute;
    z-index: 21;
    top: 8px;
    left: 8px;
    width: min(280px, calc(100% - 32px));
    max-height: min(calc(100% - 16px), 82vh);
    box-sizing: border-box;
    padding: 14px 12px;
    border: 1px solid var(--hairline);
    border-radius: var(--r-panel);
    background: color-mix(in srgb, var(--solid-panel) 88%, transparent);
    backdrop-filter: var(--glass);
    -webkit-backdrop-filter: var(--glass);
    box-shadow: var(--overlay-shadow);
    display: flex;
    flex-direction: column;
    gap: 18px;
    overflow: auto;
    scrollbar-width: thin;
    animation: library-slide 180ms ease-out;
  }
  .notebook-sidebar[hidden] { display: none; }
  @keyframes library-slide { from { transform: translateX(-14px); opacity: 0; } }
  @media (prefers-reduced-motion: reduce) { .notebook-sidebar { animation: none; } }
  .notebook-sidebar > :global(*) { flex-shrink: 0; }
  .wiki-link { display: grid; gap: 6px; padding-top: 12px; border-top: 1px solid var(--hairline); }
  .wiki-link label { font-size: 12px; color: var(--muted); }
  .wiki-links, .wiki-suggestions { display: flex; gap: 5px; flex-wrap: wrap; }

  .canvas-main { position: relative; display: flex; flex-direction: column; min-width: 0; min-height: 0; }
  .editor-shell { display: flex; flex-direction: column; flex: 1; min-height: 0; }
  .editor-shell[hidden], .writing-view[hidden], .canvas-panel[hidden] { display: none; }
  /* The Mirror pane (Q11) sits beside the blocks: the column takes the room it needs. */
  .writing.mirror-open .page-column { max-width: none; }
  .mirror-toggle { position: relative; }
  .mirror-live { position: absolute; top: 4px; right: 4px; width: 7px; height: 7px; border-radius: 50%; background: var(--accent); }

  /* Write: the title is the page's H1 with a small meta line, then the blocks. The column
     reserves room for the floating outline instead of running under it. */
  .writing { display: grid; grid-template-columns: minmax(0, 1fr); align-items: start; }
  .writing.toc-open { grid-template-columns: minmax(0, 1fr) auto; }
  .page-column { min-width: 0; max-width: 780px; padding: 20px 24px 32px; box-sizing: border-box; }
  .page-meta { margin: 0 0 4px; color: var(--subtle); font: 600 var(--fs-label) / 1.3 var(--font-ui); }
  .page-h1 { margin: 0 0 12px; }
  /* Outranks the global themed field (:root body :is(input…)). */
  :global(:root body) .notes-workspace .page-h1 .title-input {
    display: block;
    width: 100%;
    height: auto;
    min-height: 0;
    padding: 0;
    border: 0;
    border-radius: 0;
    background: transparent;
    box-shadow: none;
    color: var(--ink);
    font: inherit;
    letter-spacing: inherit;
  }
  .title-input::placeholder { color: var(--subtle); }
  :global(:root body) .notes-workspace .page-h1 .title-input:focus-visible { outline: none; box-shadow: 0 1px 0 var(--accent); }
  .page-toc { position: sticky; top: 12px; z-index: 3; display: grid; gap: 1px; width: 196px; margin: 14px 12px 0 0; padding: 10px; box-sizing: border-box; border: 1px solid var(--hairline); border-radius: var(--r-menu); background: color-mix(in srgb, var(--solid-raised) 55%, transparent); backdrop-filter: var(--glass); -webkit-backdrop-filter: var(--glass); box-shadow: 0 14px 40px -18px color-mix(in srgb, var(--shadow-tint) 70%, transparent); }
  /* Collapsed, the outline is a small pill that rides the bottom-right corner instead of taking a column. */
  .page-toc:not(.open) { top: auto; bottom: 12px; justify-self: end; width: auto; margin: 0 12px 12px 0; padding: 4px 6px; border-radius: var(--r-control); }
  .page-toc button { box-sizing: border-box; font: inherit; background: none; border: 0; cursor: pointer; }
  .toc-head { padding: 2px 8px 4px; text-align: left; color: var(--muted); }
  .page-toc:not(.open) .toc-head { padding: 2px 6px; }
  .toc-head::after { content: " ▾"; font-size: 9px; }
  .page-toc:not(.open) .toc-head::after { content: " ▸"; }
  .toc-head:hover { color: var(--ink); }
  .page-toc ol { display: grid; grid-template-columns: minmax(0, 1fr); gap: 1px; min-width: 0; margin: 0; padding: 0; list-style: none; }
  .page-toc li { min-width: 0; }
  .page-toc li button { position: relative; display: block; width: 100%; padding: 4px 8px 4px 14px; border-radius: var(--r-item); font-size: 12.5px; text-align: left; color: var(--muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .page-toc li button::before { content: ""; position: absolute; left: 4px; top: 7px; bottom: 7px; width: 2px; border-radius: 2px; background: var(--hairline); }
  .page-toc li button:hover { color: var(--ink); }
  .page-toc li button.on { color: var(--ink); background: var(--wash); }
  .page-toc li button.on::before { background: var(--accent); }
  .toc-words { margin-top: 6px; padding: 6px 8px 0; border-top: 1px solid var(--hairline); color: var(--subtle); font: 10px var(--font-mono); letter-spacing: 0.08em; text-transform: uppercase; }

  /* Map (Q19): the one mind-map editor, full-bleed in the stage. */
  .map-stage { display: flex; flex-direction: column; height: var(--stage-h); min-height: 0; overflow: hidden; }
  .map-stage > :global(*) { flex: 1; min-height: 0; }

  /* Canvas: the stage fills the window above the dock; the tools float in its bottom pill. */
  .canvas-stage { position: relative; height: var(--stage-h); min-width: 0; overflow: hidden; background: var(--solid-void); }
  .canvas-stage:fullscreen { width: 100vw; height: 100dvh; min-width: 0; min-height: 0; background: var(--solid-void); color: var(--ink); }
  .canvas-stage canvas { display: block; touch-action: none; cursor: crosshair; outline-offset: -3px; }
  .canvas-empty { position: absolute; inset: 0; display: flex; flex-direction: column; align-items: center; justify-content: center; pointer-events: none; }
  .stroke-dot { display: block; border-radius: 50%; background: currentColor; }
  .zoom-row { flex-wrap: nowrap; column-gap: 4px; }
  .pill-range :global(.select-trigger) { min-width: 110px; }
  .check { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--muted); }
  .restore-check { padding: 0 8px; }
  .file-button { position: relative; overflow: hidden; }
  .file-button input { position: absolute; inset: 0; width: 100%; opacity: 0; cursor: pointer; }
  .focus-tab-dock { position: absolute; inset: 0 180px auto 0; height: 20px; z-index: 5; }
  .focus-tab-strip { transform: translateY(calc(-100% - 2px)); opacity: 0; transition: transform 0.16s ease, opacity 0.16s ease; pointer-events: none; background: var(--chrome); box-shadow: 0 4px 18px #0003; }
  .focus-tab-dock:hover .focus-tab-strip, .focus-tab-dock:focus-within .focus-tab-strip, .focus-tab-dock.revealed .focus-tab-strip { transform: translateY(0); opacity: 1; pointer-events: auto; }
  .focus-tab-handle { position: absolute; top: 0; left: 50%; transform: translateX(-50%); padding: 2px 12px; border-radius: 0 0 7px 7px; background: var(--chrome); color: var(--muted); font-size: 10px; white-space: nowrap; }
  .focus-tab-dock:hover .focus-tab-handle, .focus-tab-dock:focus-within .focus-tab-handle, .focus-tab-dock.revealed .focus-tab-handle { opacity: 0; }
  .focus-tab-strip .note-tabs { padding: 5px; min-height: 40px; }
  @media (prefers-reduced-motion: reduce) { .focus-tab-strip { transition: none; } }
  .focus-corner { position: absolute; top: 12px; right: 12px; z-index: 3; display: grid; grid-template-columns: auto auto; justify-items: end; align-items: center; gap: 6px 8px; }
  .focus-exit { grid-column: 1 / -1; box-shadow: 0 4px 18px #0003; }
  .focus-save { max-width: 130px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 11px; color: var(--muted); }
  .focus-error { position: absolute; bottom: 64px; left: 12px; right: 12px; z-index: 2; padding: 8px; background: var(--surface); color: var(--danger, var(--ink)); }
  @media (max-width: 700px) { .focus-save { display: none; } }

  /* Under the stage: selection actions, AI review and the text outline, split by hairlines. */
  .selection-actions { display: flex; align-items: center; flex-wrap: wrap; gap: 6px; padding: 8px 12px; border-top: 1px solid var(--hairline); }
  .text-outline, .ai-review { padding: 12px 16px; border-top: 1px solid var(--hairline); }
  .text-outline h3, .ai-review h3 { margin: 0 0 8px; font-size: var(--fs-subheading); }
  .text-outline article { display: grid; gap: 8px; padding: 10px 0; border-top: 1px solid var(--hairline); }
  .text-outline textarea { width: 100%; box-sizing: border-box; resize: vertical; }
  .outline-row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
  .ai-review pre { white-space: pre-wrap; overflow-wrap: anywhere; max-height: 350px; overflow: auto; font-family: inherit; padding: 10px; }
  .muted { color: var(--muted); font-size: 12px; line-height: 1.5; }

  /* Drawers (Q8): import, privacy and layers. */
  .drawer-group { display: grid; gap: 10px; padding-bottom: 14px; }
  .drawer-group + .drawer-group { padding-top: 14px; border-top: 1px solid var(--hairline); }
  .drawer-group a { font-size: 12.5px; }
  .drawer-field { display: grid; gap: 5px; font-size: 12px; color: var(--muted); }
  .drawer-check { display: flex; align-items: center; gap: 6px; font-size: 12.5px; color: var(--ink); }
  .drawer-note { margin: 0; font-size: 12px; color: var(--muted); }
  .drawer-row { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; }
  .drawer-row > input { flex: 1; min-width: 0; }
  .drawer-result { font-size: 12px; color: var(--muted); }
  .layer-row { display: flex; align-items: center; gap: 6px; }
  .layer-row > input:not([type]) { flex: 1; min-width: 0; }
  .layer-row input[type="radio"] { width: auto; }

  @media (max-width: 720px) {
    .writing { grid-template-columns: minmax(0, 1fr); }
    .page-toc { position: static; order: -1; width: auto; margin: 8px 12px 0; }
    .page-column { padding-inline: 16px; }
  }
</style>
