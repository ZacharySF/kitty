<script lang="ts">
  import { onMount, tick } from "svelte";
  import { Button } from "@nunera/bracket";
  import NotesLibrary, { type Folder, type Notebook, type PageSummary } from "./NotesLibrary.svelte";
  import CanvasColorMenu from "./CanvasColorMenu.svelte";
  import SectionGraph, { type SectionNode } from "./SectionGraph.svelte";
  import { api, type TaskRecord } from "./api";
  import AIReview from "./AIReview.svelte";
  import NoteReferences from "./NoteReferences.svelte";
  import NoteBlocks, { type NoteBlock, type BlocksResult } from "./NoteBlocks.svelte";
  import DocumentImport from "./DocumentImport.svelte";
  import SchemaMaps from "./SchemaMaps.svelte";
  import MindMapFeedback from "./MindMapFeedback.svelte";
  import Icon from "./Icon.svelte";
  import { useWorkspaceContext } from "./workspaceContext.svelte";
  const pane = useWorkspaceContext();
  let noteMode = $state<"text" | "canvas" | "mindmap">("text");
  let blocks = $state<NoteBlock[]>([]), blockReferences = $state<Record<string,string>>({}), citationCounts = $state<Record<string,number>>({});
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
    if(!pane.active||pageId!==id)return;
    setMode("text");
    const ancestors=new Set<string>();let parent=blocks.find(block=>block.id===bid)?.parent_id;
    while(parent&&!ancestors.has(parent)){ancestors.add(parent);const ancestor=parent;parent=blocks.find(block=>block.id===ancestor)?.parent_id;}
    if(blocks.some(block=>ancestors.has(block.id)&&block.collapsed)){blocks=blocks.map(block=>ancestors.has(block.id)?{...block,collapsed:false}:block);scheduleContent();}
    focusBlock=bid;
  }
  async function refreshReferences() {
    if(!pane.active||!pageId||document.hidden)return;
    const id=pageId;
    try { const result=await api<BlocksResult>(`/notes/pages/${id}/blocks`);if(id!==pageId)return;blockReferences=result.references;citationCounts=result.citation_counts;
      if(!dirty&&!saving&&result.revision!==pageRevision){await openPage(id,true);}
    } catch { /* Save errors are shown by the writing pipeline, not background refresh. */ }
  }
  async function deletePageById(id:number) {pageMenu=null;if(id!==pageId){if(!(await beforeSwitch()))return;await openPage(id,false,true);if(pageId!==id)return;}await deletePage();}
  let presentation = $state<"map" | "editor" | "saved">("map");
  let savedMapEditor = $state<{beforeLeave: () => boolean; hasUnsavedChanges: () => boolean}>();
  let schemaFocus = $state<string | null>(null);
  let relatedOpen = $state(true);
  let linkTitle = $state("");
  let pageFind = $state("");
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
  $effect(() => {
    if (presentation === "saved") return;
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
    initialMode?: "mindmap";
    onflashcards?: (text: string, pageId: number) => void;
  } = $props();
  type Page = {
    id: number;
    notebook_id: number;
    title: string;
    revision: number;
    viewport_revision: number;
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
  function setMode(mode: "text" | "canvas" | "mindmap") {
    finishActiveGesture();
    noteMode = mode;
    if (mode === "mindmap") tool = "select";
  }
  async function showSavedMaps() {
    if (await beforeSwitch()) presentation = "saved";
  }
  function addConcept(at?: Point) {
    if (!pageId || !pane.active) return;
    const layer = doc.layers.find(item => item.id === layerId);
    if (!layer?.visible || layer.locked) { error = "Choose a visible unlocked layer for the concept."; return; }
    finishActiveGesture();
    const anchor=selectedElements.length===1&&["text","sticky"].includes(selectedElements[0].kind)?selectedElements[0]:null;
    at ??= anchor ? [anchor.x+anchor.w+150,anchor.y+anchor.h/2,1] : world(width/2,height/2,view);
    const element: CanvasElement = {
      id: uid(), kind: "sticky", layer: layerId, x: at[0] - 110, y: at[1] - 48,
      w: 220, h: 96, color: "#292438", fill: fillColor, width: 2,
      text: "New concept", fontSize,
    };
    commit({...doc, elements: [...doc.elements, element]});
    selected = [element.id]; tool = "select";
    if (anchor) {
      view={...view,x:width/2-at[0]*view.zoom,y:height/2-at[1]*view.zoom};
      scheduleView();
    }
  }
  const feedbackGraph = $derived.by(() => {
    const nodes = doc.elements.filter(element => (element.kind === "text" || element.kind === "sticky") && element.text?.trim())
      .map(element => ({id: element.id, label: element.text!.trim()}));
    const ids = new Set(nodes.map(node => node.id));
    const edges = doc.elements.filter(element => element.kind === "connector" && element.from && element.to && ids.has(element.from) && ids.has(element.to))
      .map(element => ({from: element.from!, to: element.to!, ...(element.text?.trim() ? {label: element.text.trim()} : {})}));
    return {nodes, edges};
  });
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
  async function openFromLibrary(id: number) { libraryOpen = false; await openPage(id); }
  const tocEntries = $derived(blocks.filter(block => !block.parent_id && block.text.trim())
    .sort((a, b) => a.ordering - b.ordering)
    .map(block => ({id: block.id, label: block.text.replace(/[`*#[\]()]/g, "").split(/[:;,.]/)[0].trim() || block.text.trim().slice(0, 40)})));
  const wordCount = $derived(blocks.reduce((total, block) => total + (block.text.match(/\S+/g)?.length ?? 0), 0));
  function topBlock(id: string) {
    const seen = new Set<string>();
    let block = blocks.find(item => item.id === id);
    while (block?.parent_id && !seen.has(block.id)) { seen.add(block.id); block = blocks.find(item => item.id === block!.parent_id); }
    return block?.id ?? "";
  }
  async function jumpToBlock(id: string) { focusBlock = ""; await tick(); focusBlock = id; tocCurrent = id; }
  function toggleToc() {
    tocOpen = !tocOpen;
    try { localStorage.setItem("kitty.notes.outline", tocOpen ? "open" : "closed"); } catch { /* The outline still toggles. */ }
  }
  // Compact editor toolbar: views and infrequent page actions live in labelled overflow panels.
  let viewsOpen = $state(false), moreOpen = $state(false), blockPreview = $state(false);
  let noteBlocks = $state<ReturnType<typeof NoteBlocks>>();
  // Escape closes an open overflow panel and returns focus to its summary.
  function closeOnEscape(menu: HTMLDetailsElement) {
    const close = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || !menu.open) return;
      event.preventDefault();
      event.stopPropagation();
      menu.open = false;
      menu.querySelector("summary")?.focus();
    };
    menu.addEventListener("keydown", close);
    return { destroy: () => menu.removeEventListener("keydown", close) };
  }
  let folderFocus = $state<number | null>(null);
  let notebookId = $state(0),
    pageId = $state(0),
    pageRevision = $state(1),
    viewRevision = $state(1),
    title = $state("");
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
  /** Save-state words that the tab's status dot (Q3) reports instead of the header. */
  const SAVE_WORDS = new Set(["Saved locally", "Saving locally…", "Unsaved changes"]);
  $effect(() => {
    pane.setStatus(blocked ? { state: "failed", detail: saveStatus } : dirty || viewDirty ? { state: "unsaved", detail: "Unsaved changes · Ctrl S saves" } : { state: "saved" });
    return () => pane.setStatus(null);
  });
  export function hasUnsavedChanges(): boolean {
    return dirty || viewDirty || Boolean(saving) || Boolean(viewSaving) || blocked || Boolean(viewError) ||
      gesture !== null || Boolean(savedMapEditor?.hasUnsavedChanges());
  }
  export function isCanvasFullscreen(): boolean {
    return Boolean(stage && document.fullscreenElement === stage);
  }
  export async function newCanvasTab(): Promise<void> {
    if (!pane.active) return;
    const previous=pageId;
    await createPage();
    if (pane.active && pageId!==previous && noteMode==="text") setMode("canvas");
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
  let penOnly = $state(false),
    penActive: number | null = null,
    penGuardUntil = 0;
  function savePenMode() {
    try {
      localStorage.setItem("kitty.notes.pen-only", String(penOnly));
    } catch {
      /* The current writing mode still works. */
    }
  }
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
  // Q46: one floating pill at the bottom of the canvas stage; the rest sit in its overflow.
  const pillTools = $derived(tools.filter(item => (noteMode === "mindmap" ? ["select", "sticky", "connector", "pen", "eraser"] : ["select", "pen", "highlighter", "eraser", "text", "sticky"]).includes(item.id)));
  const moreTools = $derived(tools.filter(item => !pillTools.includes(item)));
  const STROKE_SIZES = [2, 4, 8];
  let toolsCollapsed = $state(false), canvasMore = $state(false), canvasMoreMenu = $state<HTMLDetailsElement>();
  function chooseTool(id: Tool) {
    finishActiveGesture();
    tool = id;
  }
  function toggleTools() {
    toolsCollapsed = !toolsCollapsed;
    canvasMore = false;
    try { localStorage.setItem("kitty.notes.canvas-tools", toolsCollapsed ? "collapsed" : "open"); } catch { /* The pill still toggles. */ }
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
        JSON.stringify({ revision: pageRevision, title, document: doc, blocks }),
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
      pageTitle = title,
      outlineBlocks = $state.snapshot(blocks),
      rev = pageRevision;
    saveStatus = "Saving locally…";
    saving = (async () => {
      try {
        const result = await api<BlocksResult>(`/notes/pages/${id}/blocks`, {
          revision: rev,
          title: pageTitle,
          document,
          blocks: outlineBlocks,
        });
        if (pageId !== id) return;
        pageRevision = result.revision;
        blockReferences = result.references;
        citationCounts = result.citation_counts;
        pages = pages.map(page => page.id === id ? {...page, title: pageTitle} : page);
        noteTitles = noteTitles.map(page => page.id === id ? {...page, title: pageTitle} : page);
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
    if (!prepared && presentation === "saved" && savedMapEditor && !savedMapEditor.beforeLeave()) return;
    if (id === pageId && !force && presentation === "editor") return;
    if (loading) return;
    finishActiveGesture();
    await saveContent();
    await saveView();
    if (dirty) { error ||= "Save or export these local edits before switching pages."; return; }
    loading = true;
    error = "";
    try {
      const page = await api<Page>(`/notes/pages/${id}`);
      const blockData = await api<BlocksResult>(`/notes/pages/${id}/blocks`);
      if(page.revision!==blockData.revision)throw new Error("Page changed while opening; open it again to load one consistent revision.");
      blocks=blockData.blocks;blockReferences=blockData.references;citationCounts=blockData.citation_counts;
      if(!openTabs.includes(id)){openTabs=[...openTabs,id];persistTabs();}
      originals=[];
      void api<{originals:typeof originals}>(`/documents/originals?page_id=${id}`).then(result=>{if(pageId===id)originals=result.originals;}).catch(()=>{});
      pageId = page.id;
      presentation = "editor";
      schemaFocus = `notebook:${page.notebook_id}`;
      void api(`/notes/pages/${page.id}/organize`,{visited:true}).catch(()=>{});
      notebookId = page.notebook_id;
      title = page.title;
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
            if(Array.isArray(pending.blocks))blocks=pending.blocks;
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
    if (presentation === "saved" && savedMapEditor && !savedMapEditor.beforeLeave()) return false;
    finishActiveGesture();
    await saveContent();
    await saveView();
    if (dirty) error ||= "These edits could not save. Retry or export before switching pages.";
    return pane.active && !dirty;
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
    if (document.fullscreenElement !== stage && noteMode === "text") { noteMode="canvas"; await tick(); }
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
        "Permanently delete this page? Export it first if you want a copy.",
      )
    )
      return;
    try {
      await saveContent();
      if (dirty) return;
      await api(`/notes/pages/${pageId}/delete`, {
        revision: pageRevision,
        confirm: true,
      });
      try {
        sessionStorage.removeItem(recoveryKey());
      } catch {
        /* best effort */
      }
      openTabs=openTabs.filter(id=>id!==pageId);persistTabs();
      blocks=[];
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
      if (!found || (noteMode==="mindmap" && !["text","sticky"].includes(found.kind))) return;
      if (selected.length !== 1 || selected[0] === found.id) {
        selected = [found.id];
        saveStatus = "Choose the second element to connect";
        return;
      }
      const from = doc.elements.find((e) => e.id === selected[0]);
      if (!from || (noteMode==="mindmap" && !["text","sticky"].includes(from.kind))) { selected=[found.id]; return; }
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
      e.h = noteMode==="mindmap" ? 96 : mode === "sticky" ? 180 : 100;
      e.text = noteMode==="mindmap" ? "New concept" : mode === "sticky" ? "New sticky note" : "New text";
      if (noteMode==="mindmap" && mode==="sticky") e.color="#292438";
      e.fontSize = fontSize;
      commit({ ...doc, elements: [...doc.elements, e] });
      selected = [e.id];
      if (noteMode==="mindmap") tool="select"; else outline = true;
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
    if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==="s"){event.preventDefault();blocked=false;void saveContent();return;}
    const target = event.target as HTMLElement;
    if (
      ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName) ||
      target.isContentEditable ||
      target.closest("button, a, summary")
    )
      return;
    if(noteMode==="text")return;
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
          const response = await fetch(`/api/notes/attachments/${id}`);
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
      saveStatus = `Removed ${result.removed} unused local references`;
    } catch (cause) {
      error = (cause as Error).message;
    }
  }
  onMount(() => {
    try {
      penOnly = localStorage.getItem("kitty.notes.pen-only") === "true";
      toolsCollapsed = localStorage.getItem("kitty.notes.canvas-tools") === "collapsed";
      const outlinePreference = localStorage.getItem("kitty.notes.outline");
      tocOpen = outlinePreference ? outlinePreference === "open" : innerWidth >= 1100;
    } catch {
      /* Use normal pointer input when storage is unavailable. */
    }
    void loadIndex().then(() => { indexReady = true; });
    const openLinkedPage=(event:Event)=>{if(!pane.active)return;const id=(event as CustomEvent).detail?.page_id;if(id)void openPage(Number(id));};
    window.addEventListener("kitty-open-note",openLinkedPage);
    const referenceTimer=setInterval(()=>void refreshReferences(),4000);
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
      }
    };
    window.addEventListener("beforeunload", unload);
    document.addEventListener("visibilitychange", visibility);
    return () => {
      observer.disconnect();
      clearInterval(referenceTimer);
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
    if (initialMode === "mindmap") setMode("mindmap");
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
        image.onload = () => imageEpoch++;
        image.onerror = () => {
          error = "A registered local reference could not load.";
        };
        image.src = `/api/notes/attachments/${e.attachment}`;
      }
    }
  });
  $effect(() => {
    imageEpoch;
    if (pane.active && canvas && presentation === "editor" && noteMode !== "text")
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
  onpointerdown={(event) => {
    if (canvasMore && event.target instanceof Node && !canvasMoreMenu?.contains(event.target)) canvasMore = false;
  }}
/>
{#snippet pageTabs()}
  <nav class="note-tabs" aria-label="Open note pages">
    {#each openTabs as id (id)}
      {@const summary = pages.find(page=>page.id===id)}
      {@const book = notebooks.findIndex(notebook=>notebook.id===(id===pageId?notebookId:summary?.notebook_id))}
      <div class:active={id===pageId && presentation==="editor"} class="note-tab">
        <button aria-current={id===pageId && presentation==="editor"?"page":undefined} draggable={!fullscreen}
          ondragstart={()=>tabDrag=id} ondragover={event=>event.preventDefault()} ondrop={event=>{event.preventDefault();reorderTab(id);}}
          oncontextmenu={event=>{event.preventDefault();if(!fullscreen)pageMenu={id,x:event.clientX,y:event.clientY};}}
          onclick={()=>void openPage(id)}><i class="tab-dot" aria-hidden="true" style:background={book<0?"var(--subtle)":`var(--c${book%6+1})`}></i><span>{id===pageId?title:summary?.title||`Page ${id}`}</span></button>
        <button class="tab-close" aria-label={`Close ${summary?.title||"page"} tab`} onclick={()=>void closeTab(id)}><Icon name="close" size={13}/></button>
      </div>
    {/each}
    <button class="tab-new" aria-label="New page" title="New page (Ctrl+Alt+N)" disabled={!notebookId||busy||loading} onclick={createPage}>+</button>
  </nav>
{/snippet}
<section class="notes-workspace" aria-label="Notes workspace">
  <header class="notes-heading">
    <button bind:this={libraryToggle} class="library-toggle" aria-label="Open library" aria-expanded={libraryOpen} aria-controls={`${pane.id}-notes-library`} title="Library" onclick={() => void openLibrary(!libraryOpen)}><Icon name="menu" size={17}/></button>
    <h2 title={`My notes${activeNotebook ? ` / ${activeNotebook.title}` : ""}`}>Notes</h2>
    {#if !fullscreen}{@render pageTabs()}{/if}
    {#if !blocked && !SAVE_WORDS.has(saveStatus)}<span role="status" class="save-status" title={saveStatus}>{saveStatus}</span>{/if}
    <details class="overflow notes-views" bind:open={viewsOpen} use:closeOnEscape>
      <summary>Views <span aria-hidden="true">▾</span></summary>
      <div class="overflow-menu" role="group" aria-label="Notes views">
        <Button variant="ghost" size="sm" aria-pressed={presentation==="saved"} onclick={() => { viewsOpen = false; void showSavedMaps(); }}>Saved maps</Button>
        <Button variant="ghost" size="sm" aria-pressed={presentation==="map"} onclick={() => { viewsOpen = false; void rootSchema(); }}>Library overview</Button>
        <Button variant="ghost" size="sm" onclick={() => { viewsOpen = false; void dailyNote(); }}>Today</Button>
      </div>
    </details>
    <Button variant="outline" size="sm" onclick={toggleFullscreen} disabled={!pageId || presentation !== "editor"} aria-label="Open canvas fullscreen" title="Canvas fullscreen">⛶</Button>
  </header>
  {#if operationStatus}<p class="muted" role="status">{operationStatus}</p>{/if}
  {#if error}<div class="error-banner" role="alert">
      {error}<button
        onclick={() => (error = "")}
        aria-label="Dismiss notes error">×</button
      >
    </div>{/if}
  {#if viewError}<p class="error-banner" role="alert">
      Viewport: {viewError}. Content saves independently.
      <button
        onclick={() => {
          viewError = "";
          void saveView();
        }}>Retry viewport</button
      >
    </p>{/if}
  <div class="notebook-layout">
    {#if libraryOpen}<button class="library-scrim" tabindex="-1" aria-label="Close library" onclick={() => void openLibrary(false)}></button>{/if}
    <aside bind:this={libraryDrawer} id={`${pane.id}-notes-library`} class="notebook-sidebar" aria-label="Notes library" hidden={!libraryOpen}>
      <NotesLibrary {folders} {notebooks} {pages} {notebookId} {pageId} {search} {folderFocus}
        busy={loading || busy} onrefresh={loadIndex} onopenpage={openFromLibrary} onselectnotebook={notebookSchema}
        onnewpage={async () => { libraryOpen = false; await createPage(); }} onbeforeswitch={beforeSwitch} onerror={message => error = message} ondeletepage={deletePageById}
        onsearch={value => { search = value; clearTimeout(searchTimer); searchTimer = setTimeout(() => void loadIndex(), 250); }} />
      <details>
        <summary>Local privacy & retention</summary>
        <p class="muted">
          Stored only in the local database and private reference directory.
          Unsaved recovery stays in this browser session. No full notebook is
          sent to AI.
        </p>
        <label class="check"
          ><input type="checkbox" bind:checked={prefs.allow_ai} />Allow explicit
          selected-text AI sharing</label
        ><label
          >Unused reference retention (days)<input
            type="number"
            min="0"
            max="3650"
            bind:value={prefs.retention_days}
          /></label
        >
        <p class="muted">
          0 removes unused references immediately when you choose cleanup. Pages
          and used references are kept until you delete them.
        </p>
        <button class="button" onclick={savePrivacy}>Save privacy</button
        ><button class="button" onclick={cleanup}
          >Clean unused references</button
        >
      </details>
      {#if presentation==="editor"}
        <form class="wiki-link" onsubmit={event=>{event.preventDefault();libraryOpen=false;void resolveLink();}}>
          <label for={`${pane.id}-note-link`}>[[ Link a page ]]</label>
          <input id={`${pane.id}-note-link`} bind:value={linkTitle} list={`${pane.id}-note-pages`} placeholder="Page title…" />
          <datalist id={`${pane.id}-note-pages`}>{#each pages as page}<option value={page.title}></option>{/each}</datalist>
          <Button size="sm" type="submit" disabled={!linkTitle.trim()}>Open / create</Button>
        </form>
        <Button size="sm" variant="ghost" aria-pressed={relatedOpen} onclick={()=>relatedOpen=!relatedOpen}>Connections</Button>
        {#if relatedOpen && noteMode!=="text"}<NoteReferences pageId={pageId} text={`${title}\n${textElements.map(e=>e.text||"").join("\n")}\n${blocks.map(b=>b.text).join("\n")}`} onopen={id=>void openFromLibrary(id)} onlink={linkMention}/>{/if}
      {/if}
    </aside>
    <div class="canvas-main">
      {#if presentation==="saved"}<SchemaMaps bind:this={savedMapEditor} embedded {revision} {demo}/>{/if}
      {#if presentation==="map" && pane.active}
        <SectionGraph section="notes" {revision} focus={schemaFocus} onselect={selectGraphNode}/>
        <div class="actions">
          {#if schemaFocus}<Button size="sm" variant="ghost" onclick={()=>void rootSchema()}>All notebooks</Button>{/if}
          {#if schemaFocus?.startsWith("notebook:")}<Button size="sm" onclick={createPage}>New page</Button>{/if}
        </div>
      {/if}
      <div class="editor-shell" hidden={presentation!=="editor"} inert={loading}>
      <div class="page-bar">
        <div class="note-mode" role="group" aria-label="Page mode">
          <Button variant="ghost" size="sm" aria-pressed={noteMode==="text"} onclick={()=>setMode("text")}>Writing</Button>
          <Button variant="ghost" size="sm" aria-pressed={noteMode==="canvas"} onclick={()=>setMode("canvas")}>Canvas</Button>
          <Button variant="ghost" size="sm" aria-pressed={noteMode==="mindmap"} onclick={()=>setMode("mindmap")}>Mind map</Button>
        </div>
        <input
          class="page-title"
          aria-label="Page title"
          placeholder="Untitled page"
          disabled={!pageId}
          value={title}
          maxlength="200"
          oninput={(e) => {
            title = e.currentTarget.value;
            scheduleContent();
          }}
        /><button
          class="button"
          aria-label="Save / retry"
          title="Save / retry (Ctrl+S)"
          disabled={!dirty || busy}
          onclick={() => {
            blocked = false;
            error = "";
            void saveContent();
          }}>Save</button
        >
        <input type="search" aria-label="Find in page" placeholder="Find in page…" bind:value={pageFind} oninput={()=>{if(pageFind)outline=true;}}/>
        <details class="overflow page-actions" bind:open={moreOpen} use:closeOnEscape><summary>More <span aria-hidden="true">▾</span></summary><div class="overflow-menu" role="group" aria-label="More page actions and help">
        {#if noteMode==="text"}
          <div class="menu-row">
            <button class="button" aria-pressed={blockPreview} onclick={()=>blockPreview=!blockPreview}>{blockPreview?"Edit outline":"Preview outline"}</button>
            <button class="button" disabled={!pageId} onclick={()=>{moreOpen=false;noteBlocks?.add();}}>＋ Block</button>
          </div>
          <p class="muted help">Outline · Enter new bullet · Tab nest · Shift+Tab unnest</p>
        {:else}
          <p class="muted help">{tools.map(item=>`${item.key} ${item.label}`).join(" · ")} · Space+drag pan · Ctrl+Z undo · Esc clear selection</p>
        {/if}
        <p class="muted help">Ctrl+S save · Ctrl+Alt+N new page</p>
        <label>Paper<select value={paperPattern(doc)} onchange={event=>commit({...doc,background:event.currentTarget.value as CanvasDocument["background"]})}>
          {#each ["blank","lined","grid","dotted","cornell"] as pattern}<option value={pattern}>{pattern}</option>{/each}
        </select></label>
        <Button size="sm" onclick={favoritePage}>☆ Favorite</Button>
        <button
          class="button"
          disabled={!pageId || loading}
          onclick={() => {
            if (
              !dirty ||
              confirm("Discard local edits and reload the saved page?")
            ) {
              dirty = false;
              blocked = false;
              try {
                sessionStorage.removeItem(recoveryKey());
              } catch {
                /* best effort */
              }
              void openPage(pageId, true);
            }
          }}>Reload saved</button
        ><button class="button" disabled={!pageId || busy} onclick={deletePage}
          >Delete page</button
        >
        </div></details>
      </div>
      <div hidden={noteMode!=="text"}>
        <div class="writing" class:toc-open={tocOpen && tocEntries.length>0} onfocusin={event=>{const id=(event.target as HTMLElement).closest<HTMLElement>("[data-block]")?.dataset.block;if(id)tocCurrent=topBlock(id);}}>
          {#if pageId}{#key pageId}<NoteBlocks bind:this={noteBlocks} bind:preview={blockPreview} {pageId} {blocks} references={blockReferences} counts={citationCounts} pages={noteTitles} {focusBlock} onchange={value=>{blocks=value;scheduleContent();}} onpage={value=>void resolveLink(value)} onopen={openBlock}/>{/key}{/if}
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
        <details class="note-import"><summary>Import PDF / Markdown</summary><DocumentImport {notebookId} onimport={id=>{void loadIndex().then(()=>openBlock(id));}}/>{#each originals as original}<a href={`/api/documents/originals/${original.id}`} download>{original.name}</a>{/each}</details>
      </div>
      <div class="canvas-panel" hidden={noteMode==="text"}>
      <div class="canvas-stage" bind:this={stage} onpointerdowncapture={event=>{if(event.pointerType==="touch")revealTabs();}}>
        {#if fullscreen}
          <div class="focus-tab-dock" class:revealed={tabsRevealed} onpointerenter={revealTabs} role="group" aria-label="Fullscreen page navigation">
            <button class="focus-tab-handle" aria-label="Show note page tabs" onclick={revealTabs} onfocus={revealTabs}>Pages ▾</button>
            <div class="focus-tab-strip">{@render pageTabs()}</div>
          </div>
          <div class="focus-corner">
            <button class="focus-exit" onclick={toggleFullscreen} aria-label="Exit canvas fullscreen">Exit fullscreen · Esc</button>
            <span class="focus-save" role="status" aria-live="polite">{saveStatus}</span>
            <button onclick={() => { blocked = false; void saveContent(); }} title={saveStatus} aria-label={`Save note: ${saveStatus}`}>Save</button>
          </div>
          {#if error}<div class="focus-error" role="alert">{error}</div>{/if}
        {/if}
        {#if noteMode==="mindmap" && selectedElements.length===1}
          {@const element = selectedElements[0]}
          {#if ["text","sticky"].includes(element.kind)}
            <aside class="concept-inspector" aria-label="Selected concept">
              <label>Concept
                <textarea rows="2" maxlength="20000" readonly={!editable(element,doc)} value={element.text||""} oninput={event=>patchText(element.id,event.currentTarget.value)}></textarea>
              </label>
              <div class="concept-colour"><span class="eyebrow">Ink</span><CanvasColorMenu label="Ink" placement="down" bind:value={()=>element.color,(value:string)=>patchCanvasElement(element.id,{color:value})}/></div>
              {#if element.kind==="sticky"}<div class="concept-colour"><span class="eyebrow">Card</span><CanvasColorMenu label="Card" placement="down" bind:value={()=>element.fill,(value:string)=>patchCanvasElement(element.id,{fill:value})}/></div>{/if}
              <div class="concept-actions">
                <Button size="sm" variant="ghost" disabled={!editable(element,doc)} onclick={deleteSelected}>Delete</Button>
                <Button size="sm" variant="ghost" aria-label="Close concept editor" onclick={()=>selected=[]}>×</Button>
              </div>
            </aside>
          {/if}
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
          ondblclick={event=>{
            if(noteMode!=="mindmap"||tool!=="select"||!pane.active)return;
            const rect=canvas!.getBoundingClientRect(), point=world(event.clientX-rect.left,event.clientY-rect.top,view);
            const found=hitTop(doc,point,5/view.zoom);
            if(found)selected=[found.id];else addConcept(point);
          }}
          oncontextmenu={contextDeleteDrawing}
        ></canvas>
        {#if !pageId}<div class="canvas-empty">
            <h3>A place for your next idea</h3>
            <p>Create a notebook and page to start drawing.</p>
          </div>{/if}
        <div class="canvas-pill" style:--stage-height={`${height}px`}>
          {#if !toolsCollapsed}
            <div id={`${pane.id}-drawing-tools`} class="pill-bar" role="toolbar" aria-label="Drawing tools">
              {#each pillTools as item (item.id)}
                <button class="pill-btn" aria-label={item.label} aria-keyshortcuts={item.key} aria-pressed={tool === item.id} title={`${item.label} (${item.key})`} disabled={!pageId} onclick={() => chooseTool(item.id)}><Icon name={`tool-${item.id}`} size={18}/></button>
              {/each}
              {#if noteMode==="mindmap"}<button class="pill-btn pill-word" disabled={!pageId} onclick={()=>addConcept()}>+ Concept</button>{/if}
              <span class="pill-rule" aria-hidden="true"></span>
              <CanvasColorMenu label="Ink" bind:value={color}/>
              <span class="pill-rule" aria-hidden="true"></span>
              <button class="pill-btn" aria-label={`Stroke size ${lineWidth}`} title="Stroke size: click for the next size" onclick={() => lineWidth = STROKE_SIZES.find(size => size > lineWidth) ?? STROKE_SIZES[0]}><span class="stroke-dot" style:width={`${Math.min(lineWidth, 12) + 2}px`} style:height={`${Math.min(lineWidth, 12) + 2}px`}></span></button>
              <span class="pill-rule" aria-hidden="true"></span>
              <button class="pill-btn" aria-label="Undo" title="Undo (Ctrl+Z)" disabled={!undoStack.length} onclick={() => applyUndo()}><Icon name="undo" size={17}/></button>
              <button class="pill-btn" aria-label="Redo" title="Redo (Ctrl+Y)" disabled={!redoStack.length} onclick={() => applyUndo(true)}><Icon name="redo" size={17}/></button>
              <details class="pill-more" bind:this={canvasMoreMenu} bind:open={canvasMore} use:closeOnEscape>
                <summary class="pill-btn" aria-label="More canvas tools" title="More canvas tools"><Icon name="more" size={18}/></summary>
                <div class="pill-menu" role="group" aria-label="Canvas options">
                  <span class="eyebrow">Tools</span>
                  <div class="pill-menu-tools">
                    {#each moreTools as item (item.id)}
                      <button aria-label={item.label} aria-keyshortcuts={item.key} aria-pressed={tool === item.id} disabled={!pageId} onclick={() => { chooseTool(item.id); canvasMore = false; }}><Icon name={`tool-${item.id}`} size={16}/>{item.label}<kbd aria-hidden="true">{item.key}</kbd></button>
                    {/each}
                  </div>
                  {#if ["sticky","rect","ellipse"].includes(tool)}
                    <span class="eyebrow">{noteMode==="mindmap"?"Card":"Fill"}</span>
                    <CanvasColorMenu label={noteMode==="mindmap"?"Card":"Fill"} placement="inline" bind:value={fillColor}/>
                  {/if}
                  <span class="eyebrow">Stroke &amp; text</span>
                  <label class="pill-range">{tool === "eraser" ? "Eraser radius (screen px)" : "Stroke width"}<span><input type="range" min="1" max="60" bind:value={lineWidth}/><output>{lineWidth}</output></span></label>
                  <label class="pill-range">Text size<span><input type="number" min="8" max="120" bind:value={fontSize}/></span></label>
                  <span class="eyebrow">Canvas</span>
                  <div class="pill-menu-row">
                    <label class="check"><input type="checkbox" bind:checked={penOnly} onchange={savePenMode}/>Pen mode</label>
                    <label class="check"><input type="checkbox" checked={doc.snap} onchange={(e) => commit({ ...doc, snap: e.currentTarget.checked })}/>Snap</label>
                  </div>
                  <div class="pill-menu-row zoom-row">
                    <button aria-label="Zoom out" onclick={() => { view = zoomAt(view, width / 2, height / 2, 0.8); scheduleView(); }}>−</button>
                    <output>{Math.round(view.zoom * 100)}%</output>
                    <button aria-label="Zoom in" onclick={() => { view = zoomAt(view, width / 2, height / 2, 1.25); scheduleView(); }}>＋</button>
                    <button onclick={() => { view = { x: 0, y: 0, zoom: 1 }; scheduleView(); }}>Reset (0)</button>
                    <button onclick={() => { view = fit(doc.elements, width, height); scheduleView(); }}>Fit</button>
                  </div>
                  <button aria-pressed={outline} onclick={() => (outline = !outline)}>Text outline</button>
                </div>
              </details>
            </div>
          {/if}
          <button class="pill-handle" aria-label={toolsCollapsed ? "Show drawing tools" : "Hide drawing tools"} aria-expanded={!toolsCollapsed} aria-controls={toolsCollapsed ? undefined : `${pane.id}-drawing-tools`} title={toolsCollapsed ? "Show drawing tools" : "Hide drawing tools"} onclick={toggleTools}><span aria-hidden="true"></span></button>
        </div>
      </div>
      <div class="canvas-footer"><span class="canvas-hint">Space + drag: pan · Scroll: zoom · Two-finger swipe: pages · Fullscreen pen double-tap: middle left/right edge</span><span class="canvas-hint">{doc.elements.length} elements</span></div>
      {#if noteMode==="mindmap"}
        <p class="mindmap-hint">Add a concept or double-click empty space. Select to drag and edit; Connect joins two concepts. Pen strokes remain drawings.</p>
        <MindMapFeedback title={title||"Untitled note map"} graph={feedbackGraph} {demo}/>
      {/if}
      {#if selected.length}<div class="selection-actions" aria-label="Selected element actions">
        <button disabled={!selected.length} onclick={deleteSelected}
          >Delete selection</button
        ><button disabled={selected.length < 2} onclick={() => groupSelection()}
          >Group</button
        ><button
          disabled={!selected.length}
          onclick={() => groupSelection(true)}>Ungroup</button
        ><button
          disabled={!selected.length}
          onclick={() => reorderSelection(true)}>Bring forward</button
        ><button
          disabled={!selected.length}
          onclick={() => reorderSelection(false)}>Send backward</button
        ><label
          >Move selection to layer<select
            disabled={!selected.length}
            value={layerId}
            onchange={(e) => {
              const next = e.currentTarget.value;
              if (doc.layers.find((l) => l.id === next)?.locked) {
                error = "Target layer is locked.";
                return;
              }
              layerId = next;
              commit({
                ...doc,
                elements: doc.elements.map((v) =>
                  selected.includes(v.id) && editable(v, doc)
                    ? { ...v, layer: next }
                    : v,
                ),
              });
            }}
            >{#each doc.layers as layer}<option value={layer.id}
                >{layer.name}</option
              >{/each}</select
          ></label
        ><button disabled={!selectedText} onclick={makeFlashcards}
          >Make flashcards from selected text</button
        ><button disabled={!selectedText} onclick={previewSummary}
          >Summarize selection…</button
        >
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
          /><button onclick={() => (summarySelection = "")}
            >Close summary review</button
          >
        </section>{/if}
      {#if summary}<section class="ai-review">
          <h3>Summary draft — verify before using</h3>
          <pre>{summary}</pre>
          <button onclick={() => (summary = "")}>Dismiss</button>
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
              <label>Tag<select value={element.tags?.[0]||""} onchange={event=>setTag(element.id,event.currentTarget.value)}>
                <option value="">None</option><option value="todo">☐ To-do</option><option value="important">★ Important</option><option value="question">? Question</option><option value="definition">Definition</option>
              </select></label>
              <button
                onclick={() => {
                  selected = [element.id];
                  view = fit([element], width, height);
                  scheduleView();
                }}>Locate on canvas</button
              >
            </article>{/each}{#if !textElements.length}<p>
              Add Text or Sticky notes anywhere on the canvas.
            </p>{/if}
        </section>{/if}
      <details class="layers">
        <summary>Layers ({doc.layers.length})</summary>
        <p class="muted">
          Layers are drawn bottom to top. Hidden layers are not exported
          visually. Locked layers cannot be drawn on, moved, resized, or erased.
        </p>
        {#each doc.layers as layer, i}<div class="layer-row">
            <input
              type="radio"
              name={`${pane.id}-notes-layer`}
              aria-label={`Draw on ${layer.name}`}
              checked={layerId === layer.id}
              onchange={() => (layerId = layer.id)}
            /><input
              aria-label="Layer name"
              maxlength="100"
              value={layer.name}
              onchange={(e) =>
                patchLayer(layer.id, { name: e.currentTarget.value })}
            /><label class="check"
              ><input
                type="checkbox"
                checked={layer.visible}
                onchange={(e) =>
                  patchLayer(layer.id, { visible: e.currentTarget.checked })}
              />Show</label
            ><label class="check"
              ><input
                type="checkbox"
                checked={layer.locked}
                onchange={(e) =>
                  patchLayer(layer.id, { locked: e.currentTarget.checked })}
              />Lock</label
            ><button
              disabled={i === 0}
              onclick={() => reorderLayer(layer.id, -1)}>Lower</button
            ><button
              disabled={i === doc.layers.length - 1}
              onclick={() => reorderLayer(layer.id, 1)}>Raise</button
            >
          </div>{/each}
        <form
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
            maxlength="100"
            bind:value={newLayer}
          /><button disabled={doc.layers.length >= 32 || !pageId}
            >Add layer</button
          >
        </form>
      </details>
      <details class="imports"><summary>Import & export</summary>
      <div class="export-actions">
        <label class="file-button"
          >Import image / PDF<input
            type="file"
            accept="image/png,image/jpeg,image/webp,application/pdf"
            disabled={!pageId || busy}
            onchange={importReference}
          /></label
        ><label class="check"
          ><input type="checkbox" bind:checked={restoreNotebook} />Restore
          original notebook & links in a new notebook</label
        ><label class="file-button"
          >Restore JSON page<input
            type="file"
            accept="application/json,.json"
            disabled={(!restoreNotebook && !notebookId) || busy}
            onchange={importPage}
          /></label
        ><button disabled={!pageId || busy} onclick={() => exportPage("json")}
          >Export portable JSON</button
        ><button disabled={!pageId || busy} onclick={() => exportPage("svg")}
          >Export SVG</button
        ><button disabled={!pageId || busy} onclick={() => exportPage("png")}
          >Export PNG</button
        ><span class="muted"
          >PDF: up to 20 pages, rasterized locally for safe annotation. PNG
          export bounded to 4096 px.</span
        >
      </div>
      </details>
      </div>
      </div>
    </div>
  </div>
</section>
{#if pageMenu}<div class="note-page-menu" role="menu" style:left={`${Math.min(pageMenu.x,window.innerWidth-180)}px`} style:top={`${Math.min(pageMenu.y,window.innerHeight-70)}px`}><button role="menuitem" onclick={()=>pageMenu&&void deletePageById(pageMenu.id)}>Delete page…</button><button role="menuitem" onclick={()=>pageMenu=null}>Cancel</button></div>{/if}

<style>
  /* Q43: browser-style page tabs; the active tab joins the page under it. */
  .note-tabs{display:flex;align-items:flex-end;gap:2px;overflow-x:auto;scrollbar-width:none;min-width:0}
  .note-tab{position:relative;display:flex;align-items:center;flex-shrink:0;max-width:210px;height:34px;padding:0 4px 0 12px;border-radius:10px 10px 0 0;font-size:12.5px;color:var(--muted)}
  .note-tab:hover{background:color-mix(in srgb,var(--ink) 4%,transparent);color:var(--ink)}
  .note-tab.active{background:var(--surface);color:var(--ink);box-shadow:inset 0 1px 0 var(--hairline),inset 1px 0 0 var(--hairline),inset -1px 0 0 var(--hairline)}
  .note-tab.active::after{content:"";position:absolute;left:12px;right:12px;top:0;height:2px;border-radius:0 0 2px 2px;background:var(--accent)}
  .note-mode button{font:inherit;color:inherit;background:none;border:0;cursor:pointer;padding:8px}
  /* Prefixed to outrank the workspace's generic bordered button. */
  .notes-workspace :is(.note-tab button,.tab-new,.library-toggle){font:inherit;color:inherit;background:none;border:0;border-radius:6px;cursor:pointer;padding:0}
  .notes-workspace .note-tab>button:first-child{display:flex;align-items:center;gap:7px;min-width:0;height:100%}
  .note-tab>button:first-child span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .tab-dot{width:6px;height:6px;border-radius:50%;flex-shrink:0}
  .notes-workspace .tab-close{display:grid;place-items:center;width:22px;height:22px;margin-left:4px;color:var(--subtle);opacity:0}
  .note-tab:hover .tab-close,.note-tab.active .tab-close,.tab-close:focus-visible{opacity:1}
  .notes-workspace .tab-close:hover{background:var(--wash);color:var(--ink)}
  .notes-workspace .tab-new{align-self:center;display:grid;place-items:center;flex-shrink:0;width:26px;height:26px;margin-left:2px;border-radius:999px;color:var(--muted);font-size:17px;line-height:1}
  .notes-workspace .library-toggle{display:grid;place-items:center;flex-shrink:0;width:30px;height:30px;border-radius:999px;color:var(--muted)}
  .notes-workspace :is(.tab-new:hover:not(:disabled),.library-toggle:hover,.library-toggle[aria-expanded="true"]){background:var(--wash);color:var(--ink)}
  .note-mode{display:flex;align-items:center;background:var(--chrome);border-radius:5px}
  .note-mode :global(button[aria-pressed="true"]){background:var(--surface-raised);color:var(--accent)}
  .note-import{padding:12px 24px;border-top:1px solid var(--border);display:flex;gap:12px;flex-wrap:wrap}
  .note-page-menu{position:fixed;z-index:90;background:var(--surface);border:1px solid var(--border);box-shadow:0 8px 20px #0003;padding:8px;display:flex;gap:8px}
  .canvas-panel[hidden]{display:none}
  .notes-heading .note-tabs{flex:1 1 auto;align-self:stretch;margin-bottom:-1px}
  .focus-tab-dock{position:absolute;inset:0 180px auto 0;height:20px;z-index:5}
  .focus-tab-strip{transform:translateY(calc(-100% - 2px));opacity:0;transition:transform .16s ease,opacity .16s ease;pointer-events:none;background:var(--chrome);box-shadow:0 4px 18px #0003}
  .focus-tab-dock:hover .focus-tab-strip,.focus-tab-dock:focus-within .focus-tab-strip,.focus-tab-dock.revealed .focus-tab-strip{transform:translateY(0);opacity:1;pointer-events:auto}
  .focus-tab-handle{position:absolute;top:0;left:50%;transform:translateX(-50%);font-size:10px!important;padding:2px 12px!important;border-radius:0 0 7px 7px!important;white-space:nowrap}
  .focus-tab-dock:hover .focus-tab-handle,.focus-tab-dock:focus-within .focus-tab-handle,.focus-tab-dock.revealed .focus-tab-handle{opacity:0}
  .focus-tab-strip .note-tabs{padding:5px;min-height:40px;align-items:center;border:0}
  .concept-inspector{position:absolute;right:16px;top:16px;z-index:2;width:min(270px,calc(100% - 60px));padding:12px;box-sizing:border-box;border:1px solid var(--border);border-radius:10px;background:var(--surface);box-shadow:0 6px 24px #0002;display:grid;gap:10px}
  .canvas-stage:fullscreen .concept-inspector{top:118px}
  .concept-inspector textarea{width:100%;box-sizing:border-box;resize:vertical;max-height:25vh}
  .concept-actions{display:flex;gap:5px;align-items:center;flex-wrap:wrap}
  .concept-colour{display:flex;align-items:center;justify-content:space-between;gap:8px}
  .mindmap-hint{font-size:12px;line-height:1.6;color:var(--muted);margin:8px 0}
  /* Q43: the library slides over the page instead of taking a column. */
  .library-scrim{position:absolute;inset:0;z-index:20;padding:0;border:0;cursor:default;background:color-mix(in srgb,var(--shadow-tint) 40%,transparent)}
  .notebook-sidebar[hidden]{display:none}
  /* The writing column reserves room for the floating outline instead of running under it. */
  .writing{display:grid;grid-template-columns:minmax(0,1fr);align-items:start}
  .writing.toc-open{grid-template-columns:minmax(0,1fr) auto}
  .page-toc{position:sticky;top:12px;z-index:3;display:grid;gap:1px;width:196px;margin:14px 12px 0 0;padding:10px;box-sizing:border-box;border:1px solid var(--hairline);border-radius:12px;background:color-mix(in srgb,var(--solid-raised) 55%,transparent);backdrop-filter:var(--glass);-webkit-backdrop-filter:var(--glass);box-shadow:0 14px 40px -18px color-mix(in srgb,var(--shadow-tint) 70%,transparent)}
  /* Collapsed, the outline is a small pill that rides the bottom-right corner instead of taking a column. */
  .page-toc:not(.open){top:auto;bottom:12px;justify-self:end;width:auto;margin:0 12px 12px 0;padding:4px 6px;border-radius:999px}
  .notes-workspace .page-toc button{box-sizing:border-box;font:inherit;background:none;border:0;cursor:pointer}
  .notes-workspace .toc-head{padding:2px 8px 4px;text-align:left;color:var(--muted)}
  .page-toc:not(.open) .toc-head{padding:2px 6px}
  .toc-head::after{content:" ▾";font-style:normal;font-size:9px}
  .page-toc:not(.open) .toc-head::after{content:" ▸"}
  .notes-workspace .toc-head:hover{color:var(--ink)}
  .page-toc ol{display:grid;grid-template-columns:minmax(0,1fr);gap:1px;min-width:0;margin:0;padding:0;list-style:none}
  .page-toc li{min-width:0}
  .notes-workspace .page-toc li button{position:relative;display:block;width:100%;padding:4px 8px 4px 14px;border-radius:7px;font-size:12.5px;text-align:left;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .page-toc li button::before{content:"";position:absolute;left:4px;top:7px;bottom:7px;width:2px;border-radius:2px;background:var(--hairline)}
  .notes-workspace .page-toc li button:hover{color:var(--ink)}
  .notes-workspace .page-toc li button.on{color:var(--ink);background:var(--wash)}
  .page-toc li button.on::before{background:var(--accent)}
  .toc-words{margin-top:6px;padding:6px 8px 0;border-top:1px solid var(--hairline);color:var(--subtle);font:10px var(--font-mono);letter-spacing:.08em;text-transform:uppercase}
  @media(prefers-reduced-motion:reduce){.focus-tab-strip{transition:none}}
  .notes-workspace {
    background: var(--surface);
    color: var(--ink);
    border: 1px solid var(--border);
    border-radius: 10px;
    /* clip, not hidden: hidden would make this the sticky outline's scroll container. */
    overflow: clip;
    display: flex;
    flex-direction: column;
    min-height: 720px;
  }
  .notes-heading {
    display: flex;
    align-items: center;
    gap: 8px;
    min-height: 44px;
    padding: 6px 10px 0;
    box-sizing: border-box;
    background: color-mix(in srgb, var(--solid-void) 55%, transparent);
  }
  .notes-heading > :not(.note-tabs) { margin-bottom: 5px; }
  .notes-heading h2 { margin: 0 4px 5px 2px; font-size: 18px; flex-shrink: 0; }
  .save-status { font-size: 11px; color: var(--muted); max-width: 150px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex-shrink: 1; }
  .notebook-layout {
    position: relative;
    display: flex;
    flex-direction: column;
    border-top: 1px solid var(--hairline);
    flex: 1;
    min-height: 0;
  }
  .notebook-layout > .canvas-main { flex: 1; }
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
    border-radius: 12px;
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
  @keyframes library-slide { from { transform: translateX(-14px); opacity: 0; } }
  @media (prefers-reduced-motion: reduce) { .notebook-sidebar { animation: none; } }
  .notebook-sidebar > :global(*) { flex-shrink: 0; }
  .canvas-main {
    position: relative;
    min-width: 0;
    min-height: 0;
    padding: 8px 12px 12px;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .notes-workspace details.overflow { position: relative; flex-shrink: 0; padding: 0; border: 0; }
  .notes-workspace .overflow > summary {
    display: flex;
    align-items: center;
    gap: 6px;
    list-style: none;
    height: var(--control-h);
    min-height: 0;
    padding: 0 12px;
    border: 1px solid var(--hairline);
    border-radius: var(--r-control);
    color: var(--ink);
    font: 500 var(--fs-control) / 1 var(--font-ui);
    white-space: nowrap;
  }
  .overflow > summary::-webkit-details-marker { display: none; }
  .overflow[open] > summary { color: var(--accent); border-color: color-mix(in srgb, var(--accent) 50%, var(--border)); }
  .overflow-menu {
    position: absolute; right: 0; top: calc(100% + 6px); z-index: 10;
    display: grid; gap: 8px; padding: 10px; min-width: 160px; max-width: min(320px, 80vw);
    background: color-mix(in srgb, var(--solid-void, #141414) 78%, var(--surface)); border: 1px solid var(--border); border-radius: 8px;
    box-shadow: 0 8px 24px #0004;
    backdrop-filter: blur(20px) saturate(140%);
    -webkit-backdrop-filter: blur(20px) saturate(140%);
  }
  .page-actions .overflow-menu { width: 280px; }
  .overflow-menu .menu-row { display: flex; gap: 6px; flex-wrap: wrap; }
  .overflow-menu .help { margin: 0; }
  .selection-actions,
  .export-actions,
  .layer-row {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
  }
  .page-bar { display: flex; align-items: center; gap: 6px; min-width: 0; }
  .page-bar .note-mode { flex-shrink: 0; }
  .page-bar .page-title { flex: 1 1 180px; min-width: 80px; }
  .page-bar > button { flex-shrink: 0; }
  .selection-actions button,
  .export-actions button,
  .layer-row button {
    font-size: 12px;
  }
  .canvas-stage {
    position: relative;
    height: clamp(420px, 65vh, 900px);
    min-width: 0;
    overflow: hidden;
    background: var(--solid-void);
    border: 1px solid var(--border);
    border-radius: 8px;
  }
  .canvas-stage:fullscreen {
    width: 100vw;
    height: 100dvh;
    min-width: 0;
    min-height: 0;
    border: 0;
    border-radius: 0;
    background: var(--solid-void);
    color: var(--ink);
  }
  .focus-corner {
    position: absolute;
    top: 12px;
    right: 12px;
    z-index: 3;
    display: grid;
    grid-template-columns: auto auto;
    justify-items: end;
    align-items: center;
    gap: 6px 8px;
  }
  .notes-workspace .focus-exit { grid-column: 1 / -1; min-height: 40px; box-shadow: 0 4px 18px #0003; }
  .focus-save { max-width: 130px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 11px; color: var(--muted); }
  .focus-error { position: absolute; bottom: 64px; left: 12px; right: 12px; z-index: 2; padding: 8px; background: var(--surface); color: var(--danger, var(--ink)); }
  @media (max-width: 700px) {
    .focus-save { display: none; }
  }
  /* Q46: the canvas tools are one floating glass pill docked at the bottom of the stage.
     Its bottom edge is a handle that collapses it to a small tab, like the dock. */
  .canvas-pill {
    position: absolute;
    left: 50%;
    bottom: 0;
    z-index: 3;
    transform: translateX(-50%);
    display: flex;
    flex-direction: column;
    align-items: center;
    max-width: calc(100% - 24px);
  }
  .pill-bar {
    display: flex;
    align-items: center;
    gap: 2px;
    max-width: 100%;
    box-sizing: border-box;
    padding: 5px 8px;
    border: 1px solid var(--hairline);
    border-radius: 999px;
    background: color-mix(in srgb, var(--solid-raised) 72%, transparent);
    backdrop-filter: var(--glass);
    -webkit-backdrop-filter: var(--glass);
    box-shadow: 0 12px 30px -16px color-mix(in srgb, var(--shadow-tint) 80%, transparent);
    white-space: nowrap;
    animation: pill-rise 180ms ease-out;
  }
  @keyframes pill-rise { from { opacity: 0; transform: translateY(8px); } }
  .canvas-pill .pill-btn {
    position: relative;
    flex-shrink: 0;
    display: grid;
    place-items: center;
    width: 30px;
    height: 30px;
    padding: 0;
    border: 0;
    border-radius: 999px;
    background: none;
    color: var(--muted);
    transition: transform 140ms ease, background-color 140ms ease, color 140ms ease;
  }
  .canvas-pill .pill-btn:hover:not(:disabled) { color: var(--ink); transform: translateY(-2px); }
  .canvas-pill .pill-btn[aria-pressed="true"] { color: var(--ink); background: var(--wash); }
  .canvas-pill .pill-btn[aria-pressed="true"]::after {
    content: "";
    position: absolute;
    bottom: 2px;
    left: calc(50% - 2px);
    width: 4px;
    height: 4px;
    border-radius: 50%;
    background: var(--accent);
  }
  .canvas-pill .pill-word { width: auto; padding: 0 10px; font-size: 12px; }
  .pill-rule { flex-shrink: 0; width: 1px; height: 20px; margin: 0 4px; background: var(--hairline); }
  .stroke-dot { display: block; border-radius: 50%; background: currentColor; }
  .canvas-pill details.pill-more { position: relative; flex-shrink: 0; padding: 0; border: 0; }
  .pill-more > summary { list-style: none; cursor: pointer; }
  .pill-more > summary::-webkit-details-marker { display: none; }
  .canvas-pill .pill-more[open] > summary { color: var(--ink); background: var(--wash); }
  .pill-menu {
    position: absolute;
    right: -8px;
    bottom: calc(100% + 12px);
    z-index: 4;
    display: grid;
    gap: 8px;
    width: 270px;
    max-height: calc(var(--stage-height, 420px) - 72px);
    overflow: auto;
    box-sizing: border-box;
    padding: 12px;
    border: 1px solid var(--hairline);
    border-radius: 14px;
    background: color-mix(in srgb, var(--solid-raised) 94%, transparent);
    backdrop-filter: var(--glass);
    -webkit-backdrop-filter: var(--glass);
    box-shadow: var(--overlay-shadow);
    white-space: normal;
  }
  .pill-menu .eyebrow { margin: 2px 0 -4px; }
  .pill-menu-tools { display: grid; grid-template-columns: 1fr 1fr; gap: 2px; }
  .canvas-pill .pill-menu-tools button {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 5px 8px;
    border: 0;
    border-radius: 8px;
    background: none;
    color: var(--muted);
    font-size: 12px;
  }
  .canvas-pill .pill-menu-tools button:hover:not(:disabled),
  .canvas-pill .pill-menu-tools button[aria-pressed="true"] { color: var(--ink); background: var(--wash); }
  .pill-menu-tools kbd { margin-left: auto; padding: 0; border: 0; background: none; box-shadow: none; color: var(--subtle); font: 10px var(--font-mono); text-decoration: none; }
  .pill-menu-row { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 12px; }
  .pill-menu-row.zoom-row { flex-wrap: nowrap; column-gap: 6px; }
  .canvas-pill .pill-menu button:not(.pill-btn) { padding: 4px 9px; font-size: 12px; }
  .notes-workspace .pill-range { flex-direction: row; align-items: center; justify-content: space-between; gap: 8px; }
  .pill-range span { display: flex; align-items: center; gap: 6px; }
  .notes-workspace .pill-range input[type="range"] { width: 110px; padding: 0; accent-color: var(--accent); }
  .notes-workspace .pill-range input[type="number"] { width: 64px; padding: 4px 6px; }
  .pill-menu output { min-width: 36px; text-align: center; color: var(--muted); font: 11px var(--font-mono); }
  .canvas-pill .pill-handle {
    display: grid;
    place-items: center;
    width: 64px;
    height: 14px;
    padding: 0;
    border: 0;
    border-radius: 8px 8px 0 0;
    background: transparent;
  }
  .canvas-pill .pill-handle[aria-expanded="false"] {
    height: 16px;
    border: 1px solid var(--hairline);
    border-bottom: 0;
    background: color-mix(in srgb, var(--solid-raised) 72%, transparent);
    backdrop-filter: var(--glass);
    -webkit-backdrop-filter: var(--glass);
  }
  .pill-handle span {
    width: 36px;
    height: 3px;
    border-radius: 999px;
    background: var(--muted);
    opacity: 0.6;
    transition: opacity 140ms ease, background-color 140ms ease;
  }
  .pill-handle:hover span,
  .pill-handle:focus-visible span { opacity: 1; background: var(--violet); }
  @media (prefers-reduced-motion: reduce) {
    .pill-bar { animation: none; }
    .canvas-pill .pill-btn, .pill-handle span { transition: none; }
    .canvas-pill .pill-btn:hover:not(:disabled) { transform: none; }
  }
  .canvas-footer { display: flex; align-items: center; justify-content: space-between; gap: 10px; flex-wrap:wrap; }
  .page-bar .page-title { font-size: 16px; font-weight: 550; background: transparent; }
  .selection-actions { padding: 10px; border: 1px solid var(--border); border-radius: 7px; background: var(--chrome); }
  .export-actions { padding: 12px 0 0; }
  .canvas-stage canvas {
    display: block;
    touch-action: none;
    cursor: crosshair;
    outline-offset: -3px;
  }
  .canvas-empty {
    position: absolute;
    inset: 0;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    pointer-events: none;
  }
  .canvas-hint {
    font-size: 12px;
    color: var(--muted);
    margin: 0;
  }
  .notes-workspace label {
    display: flex;
    flex-direction: column;
    gap: 5px;
    font-size: 12px;
    color: var(--muted);
  }
  .notes-workspace label.check {
    flex-direction: row;
    align-items: center;
    gap: 6px;
  }
  .notes-workspace input,
  .notes-workspace select,
  .notes-workspace textarea {
    color: var(--ink);
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 5px;
    padding: 7px;
    min-width: 0;
  }
  .notes-workspace button,
  .file-button {
    border: 1px solid var(--border);
    background: var(--surface-raised);
    color: var(--ink);
    border-radius: 6px;
    padding: 7px 10px;
    cursor: pointer;
  }
  .notes-workspace button:disabled {
    opacity: 0.45;
    cursor: not-allowed;
  }
  .notes-workspace details {
    padding: 8px;
    border: 1px solid var(--border);
    border-radius: 7px;
  }
  .notes-workspace summary {
    cursor: pointer;
    font-size: 13px;
    font-weight: 600;
  }
  .notes-workspace form {
    display: flex;
    flex-direction: column;
    gap: 8px;
    margin-top: 12px;
  }
  .layer-row {
    padding: 7px 0;
  }
  .layer-row > input:not([type]) {
    flex: 1;
  }
  .layer-row input[type="radio"] {
    width: auto;
  }
  .layers form {
    flex-direction: row;
  }
  .text-outline,
  .ai-review {
    padding: 16px;
    background: var(--surface-raised);
    border: 1px solid var(--border);
    border-radius: 8px;
  }
  .text-outline article {
    padding: 10px 0;
    border-top: 1px solid var(--border);
    display: grid;
    gap: 8px;
  }
  .text-outline textarea {
    width: 100%;
    box-sizing: border-box;
    resize: vertical;
  }
  .ai-review pre {
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    max-height: 350px;
    overflow: auto;
    font-family: inherit;
    background: var(--surface);
    padding: 10px;
  }
  .file-button {
    position: relative;
    overflow: hidden;
    display: inline-flex !important;
    font-size: 12px !important;
  }
  .file-button input {
    position: absolute;
    inset: 0;
    opacity: 0;
    cursor: pointer;
    width: 100%;
  }
  .error-banner {
    margin: 0 14px 12px;
    padding: 10px;
    border: 1px solid var(--warning);
    background: var(--surface-raised);
    color: var(--warning);
    overflow-wrap: anywhere;
  }
  .error-banner button {
    margin-left: 8px;
  }
  .muted {
    color: var(--muted);
    font-size: 12px;
    line-height: 1.5;
  }
  @media (max-width: 1000px) {
    /* Keep the page title readable beside the mode switch at the 760px minimum. */
    .page-bar { gap: 4px; }
    .notes-workspace .note-mode :global(.br-btn) { padding-inline: 6px; letter-spacing: 0.02em; }
    .page-bar .page-title { font-size: 15px; }
    .notes-workspace .overflow > summary { padding-inline: 8px; }
    .notes-workspace .page-bar > button { padding-inline: 8px; }
  }
  @media (max-width: 720px) {
    .writing { grid-template-columns: minmax(0, 1fr); }
    .page-toc { position: static; order: -1; width: auto; margin: 8px 12px 0; }
    .notes-heading { flex-wrap: wrap; }
    .notes-heading .note-tabs { order: 1; flex-basis: 100%; }
    .canvas-stage {
      height: 480px;
    }
    .page-bar { flex-wrap: wrap; }
    .save-status { display: none; }
  }
  .editor-shell {display:flex;flex-direction:column;gap:8px;min-height:0;flex:1}
  .editor-shell[hidden]{display:none}
  .wiki-link {display:grid;gap:6px;border-top:1px solid var(--border);padding-top:12px}
  .wiki-link label{font-size:12px;color:var(--muted)}
  .page-bar>input[type=search]{flex:0 4 150px;min-width:56px;font-size:12px}
  .wiki-links,.wiki-suggestions{display:flex;gap:5px;flex-wrap:wrap}
</style>
