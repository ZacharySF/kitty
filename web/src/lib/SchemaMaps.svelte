<script lang="ts">
  import { onMount, tick, untrack } from "svelte";
  import { Panel, Button, Badge } from "@nunera/bracket";
  import { api, safeUrl } from "./api";
  import SchemaNetwork from "./SchemaNetwork.svelte";
  import type { GraphLayout, GraphLink, GraphNode } from "./graph3d";
  import { useWorkspaceContext, type SaveStatus } from "./workspaceContext.svelte";
  import MindMapFeedback from "./MindMapFeedback.svelte";
  import { activeModules } from "./modules.svelte";
  import { inkOutline, lassoed, nearestBox, penAction, simplify, strokeHit, type InkPoint } from "./mapInk";
  import Icon from "./Icon.svelte";
  import CanvasPill from "./CanvasPill.svelte";
  import Popover from "./ui/Popover.svelte";
  import OverflowMenu, { type OverflowItem } from "./ui/OverflowMenu.svelte";
  import Select from "./ui/Select.svelte";
  import EditDrawer from "./ui/EditDrawer.svelte";
  import QuickAdd from "./ui/QuickAdd.svelte";
  import { fitHeight } from "./fitHeight";
  /**
   * The one mind-map editor (Q18 D, Q19 A): Notes' Map mode (embedded, the page's own map),
   * Study's Mind map tab and the Mind maps view (Ctrl K) all open it. Chrome: a slim top bar
   * (map switcher, title, layout, view, ⋯), the Notes canvas pill for building, a small bar on
   * the selected topic and a zoom corner; the canvas fills the rest. Edits autosave and the
   * save state is a dot on the tab.
   */
  let {
    revision = 0,
    demo = false,
    section = "schema",
    embedded = false,
    pageId = 0,
    onstatus,
  }: {
    revision?: number;
    demo?: boolean;
    section?: "schema" | "study";
    /** Inside Notes' Map mode: no top bar or breadcrumb; the map belongs to `pageId`. */
    embedded?: boolean;
    pageId?: number;
    /** Receives the save state instead of the tab (a host that merges it with its own). */
    onstatus?: (status: SaveStatus | null) => void;
  } = $props();
  const pane = useWorkspaceContext();
  // Importing tracked jobs reads Apply AI data; it exists only while that module is active.
  const canImportJobs = $derived(activeModules.enabled.includes("applyai"));
  let container = $state<HTMLElement>();
  function confirm(message: string) {
    return pane.active && window.confirm(message);
  }
  export function hasUnsavedChanges(): boolean {
    return dirty || busy || !!saving || blocked;
  }
  /** Saves pending edits now; false when they could not be saved (the host keeps the user here). */
  export async function flush(): Promise<boolean> {
    clearTimeout(autosaveTimer);
    finishInline(false);
    if (saving) await saving;
    if (dirty && !blocked) await save();
    return !dirty && !blocked;
  }
  type Source = { kind: "subject" | "job" | "company" | "calendar_task" | "note_page"; id: number | string };
  type Node = {
    id: string;
    kind: string;
    label: string;
    notes: string;
    x: number;
    y: number;
    url: string;
    source: Source | null;
    collapsed?: boolean;
    map_id?: number | null;
    /** Placed by hand (detached or free-positioned); the tree layout lays its branch out around it. */
    free?: boolean;
    /** Q65 D: a theme palette slot (`c1`–`c6`); absent means the automatic branch colour. */
    color?: string;
  };
  /** Q65 E: `cross` marks a labelled connection outside the tree; other edges may shape the tree. */
  type Edge = { id: string; from: string; to: string; label: string; kind?: "cross" };
  /** Q69: pen ink attached to a node; points are [x, y, pressure] relative to the node's top-left corner. */
  type Stroke = { id: string; node: string; points: InkPoint[] };
  type Graph = { nodes: Node[]; edges: Edge[]; strokes?: Stroke[] };
  type View = { x: number; y: number; zoom: number };
  type Live = {
    label?: string;
    url?: string;
    status?: string;
    queue_state?: string;
    company?: string;
    closed_at?: string;
    missing?: boolean;
  };
  type MapItem = {
    id: number;
    title: string;
    mode: string;
    revision: number;
    graph: Graph;
    viewport: View;
    live: Record<string, Live>;
    can_undo: boolean;
    can_redo?: boolean;
    paired_history?: boolean;
  };
  type Summary = { id: number; title: string; mode: string; revision: number };
  type Job = {
    id: number;
    title: string;
    company: string;
    status: string | null;
    queue_state: string;
  };
  type Snapshot = { title: string; graph: Graph; viewport: View };
  type View3D = "edit" | "map3d" | "network";
  type NetworkNode = {
    id: string;
    kind: string;
    label: string;
    source: Source | null;
    live: Live | null;
    url: string;
    maps: number[];
    members: {
      map_id: number;
      node_id: string;
      label: string;
      notes: string;
    }[];
    degree: number;
    cluster: number;
  };
  type NetworkEdge = {
    id: string;
    from: string;
    to: string;
    label: string;
    weight: number;
    maps: number[];
    members: { map_id: number; edge_id: string }[];
  };
  type Network = {
    mode: string | null;
    merge_labels: boolean;
    maps: Summary[];
    nodes: NetworkNode[];
    edges: NetworkEdge[];
    clusters: { id: number; size: number; hub: string; label: string }[];
    unified: number;
  };
  type Point = { x: number; y: number };
  type Reference = {
    kind: string;
    id: number | string;
    title: string;
    snippet: string;
    view: string;
    match: "wikilink" | "mention";
    linked: boolean;
  };
  type AutoLinks = {
    map_id: number;
    revision: number;
    suggestions: { node_id: string; label: string; references: Reference[]; truncated?: boolean }[];
    scanned: Record<string, number>;
  };
  const REF_KINDS: Record<string, string> = {
    note_page: "Note page",
    study_subject: "Study subject",
    study_project: "Study project",
    study_task: "Study task",
    calendar_event: "Calendar event",
    calendar_task: "Calendar task",
    job: "Job",
    flashcard_deck: "Flashcard deck",
  };
  const LIMIT = 100000;
  // Q44: nodes are rounded pills sized to their label; parentless nodes are inverted ink root pills.
  const PILL_H = 34, ROOT_H = 38, LABEL_MAX = 30;
  const pillWidths = new Map<string, number>();
  let measurer: CanvasRenderingContext2D | null = null, uiFont = "Inter, sans-serif";
  let fontsReady = $state(0);
  function textWidth(text: string, font: string) {
    void fontsReady;
    const key = `${font}\u0000${text}`;
    let width = pillWidths.get(key);
    if (width === undefined) {
      measurer ??= document.createElement("canvas").getContext("2d");
      if (measurer) measurer.font = font;
      width = measurer ? measurer.measureText(text).width : text.length * 7;
      pillWidths.set(key, width);
    }
    return width;
  }
  function isRoot(n: Node) { return !parents.has(n.id); }
  function pillText(n: Node) {
    if (hiddenLabel(n)) return "?";
    const text = name(n);
    return `${n.source?.kind === "note_page" ? "▤ " : ""}${text.length > LABEL_MAX ? `${text.slice(0, LABEL_MAX - 1)}…` : text}`;
  }
  function hiddenCount(n: Node) { return n.collapsed ? descendants(n.id).size - 1 : 0; }
  function box(n: Node) {
    const root = isRoot(n), fold = hiddenCount(n);
    const text = textWidth(pillText(n), root ? `600 13px ${uiFont}` : `500 12.5px ${uiFont}`);
    return {w: Math.round(Math.min(320, Math.max(root ? 72 : 64, text + (root ? 36 : 44) + (fold ? 26 : 0)))), h: root ? ROOT_H : PILL_H};
  }
  let maps = $state<Summary[]>([]),
    subjects = $state<{ id: number; name: string }[]>([]);
  let current = $state<MapItem | null>(null),
    busy = $state(false),
    dirty = $state(false),
    blocked = $state(false),
    /** Counts local edits, so an autosave that lands after a newer edit keeps the map dirty. */
    edits = $state(0),
    /** The autosave in flight; edits continue meanwhile. */
    saving = $state<Promise<void> | null>(null),
    saveFailed = $state(false);
  let autosaveTimer: ReturnType<typeof setTimeout> | undefined;
  /** Embedded: whether the page's map lookup finished (so the empty state never flashes). */
  let pageChecked = $state(false);
  /** The first non-loop, non-cross edge into each node is its tree edge; any other edge is drawn as a connection. */
  const treeLinks = $derived([...(current?.graph.edges ?? [])].reverse().filter(e => e.from !== e.to && e.kind !== "cross"));
  const parents = $derived(new Map(treeLinks.map(e => [e.to, e.from])));
  const treeEdges = $derived(new Set([...new Map(treeLinks.map(e => [e.to, e.id])).values()]));
  let error = $state(""),
    newTitle = $state(""),
    mode = $state("study"),
    newSubject = $state("");
  let selected = $state(""),
    selectedEdge = $state(""),
    search = $state(""),
    kind = $state("topic"),
    label = $state(""),
    subjectId = $state("");
  let draftLabel = $state(""),
    draftNotes = $state(""),
    draftUrl = $state("");
  // Q8: the new-map popover (and its full form), the topic details drawer and the ⋯ drawers.
  let creating = $state(false), creatingFull = $state(false), details = $state(false);
  let drawer = $state<"" | "links" | "jobs" | "ai">("");
  let importInput = $state<HTMLInputElement>();
  let history = $state<Snapshot[]>([]),
    future = $state<Snapshot[]>([]),
    stage = $state<SVGSVGElement>();
  let jobSearch = $state(""),
    jobs = $state<Job[]>([]),
    jobIds = $state<number[]>([]),
    jobOffset = $state(0),
    moreJobs = $state(false);
  let gesture: {
    pointer: number;
    x: number;
    y: number;
    node: string;
    before: Snapshot;
    moved: boolean;
    branch: string[];
    /** Shift-drag on empty space draws a selection box instead of panning. */
    box?: boolean;
  } | null = null;
  // Q65 F: further selected topics (with `selected` among them) and the selection box in map units.
  let multi = $state<string[]>([]), marquee = $state<{x0: number; y0: number; x1: number; y1: number} | null>(null);
  const picked = $derived(new Set(multi.includes(selected) ? multi : selected ? [selected] : []));
  /** Picked topics whose ancestors are not picked: what moves, reparents or detaches as a whole. */
  function pickedTops() {
    return [...picked].filter(id => {
      for (let p = parents.get(id), seen = new Set<string>(); p !== undefined && !seen.has(p); seen.add(p), p = parents.get(p))
        if (picked.has(p)) return false;
      return true;
    });
  }
  // Q65 B/C: the node being dragged, the pill it would snap into, whether D detached it and whether Alt places it freely.
  let dragging = $state(""), dropTarget = $state(""), detaching = $state(false), placing = $state(false);
  // Q65 D: the swatch row paints the node or (with Whole branch) its descendants too; copied style pastes onto others.
  const PALETTE = ["c1", "c2", "c3", "c4", "c5", "c6"];
  let paintBranch = $state(false), copiedStyle = $state<{color?: string} | null>(null), stageWidth = $state(0), styleWidth = $state(0);
  // Q65 E: the topic a "Connect to…" starts from, and the cross connection whose label is being typed.
  let linking = $state(""), labelling = $state(""), edgeDraft = $state("");
  // Q69: ink mode (pen or mouse draws), the stroke or barrel-button lasso being drawn (map units), the eraser
  // pass (with whether it has erased yet), and pen proximity: touches are palms while a pen hovers or was just used.
  const INK_WIDTH = 3.2, PALM_MS = 600, MAX_INK_POINTS = 1000;
  let inkMode = $state(false),
    inking = $state<{pointer: number; points: InkPoint[]} | null>(null),
    lassoLoop = $state<{pointer: number; points: [number, number][]} | null>(null);
  let erasing: {pointer: number; erased: boolean} | null = null, penHover = false, penSeen = -Infinity;
  /** The last tap (node id, `edge:` id or `stage`), so a second tap on the same spot is a double tap. */
  let lastTap = {key: "", time: 0, x: 0, y: 0};
  let view = $state<View3D>("edit"),
    mapLayout = $state<GraphLayout>("tree"),
    netLayout = $state<GraphLayout>("tree");
  let network = $state<Network | null>(null),
    netMode = $state(""),
    netMaps = $state<number[]>([]),
    mergeLabels = $state(false),
    colorBy = $state<"map" | "kind" | "cluster">("cluster"),
    netSelected = $state(""),
    netBusy = $state(false),
    netError = $state("");
  let autoLinks = $state<AutoLinks | null>(null),
    autoPicked = $state<string[]>([]),
    autoBusy = $state(false),
    autoError = $state("");
  let autoRequestedFor = 0;
  let outline = $state(false), recall = $state(false), revealed = $state<string[]>([]);
  let editing = $state(""), inlineLabel = $state("");
  let noteSearch = $state(""), notePages = $state<{id: number; title: string}[]>([]);
  let template = $state("Blank");
  const draftKey = (id: number) => `kitty.schema.draft.${id}`;
  /** Tree children of each node in edge order; cross connections never make a branch. */
  const childIds = $derived.by(() => {
    const map = new Map<string, string[]>();
    for (const e of current?.graph.edges ?? []) if (treeEdges.has(e.id)) map.set(e.from, [...(map.get(e.from) ?? []), e.to]);
    return map;
  });
  function descendants(id: string) {
    const ids = new Set([id]);
    for (const item of ids) for (const child of childIds.get(item) ?? []) ids.add(child);
    return ids;
  }
  function reparent(parent: string) {
    if (!current || !node || !editable) return;
    if (parent && descendants(node.id).has(parent)) { error = "A topic cannot be moved inside its own branch."; return; }
    remember();
    current.graph.edges = current.graph.edges.filter(edge => !(treeEdges.has(edge.id) && edge.to === node.id));
    if (parent) {
      current.graph.edges.push({id: crypto.randomUUID(), from: parent, to: node.id, label: ""});
      const target = current.graph.nodes.find(item => item.id === parent);
      if (target) target.collapsed = false;
    }
    relayout();
  }
  const visibleNodes = $derived.by(() => {
    if (!current) return [];
    const hidden = new Set<string>();
    const visit = (id: string, seen = new Set<string>()) => {
      if (seen.has(id)) return;
      seen.add(id);
      for (const child of childIds.get(id) ?? []) {
        if (!seen.has(child)) {hidden.add(child); visit(child, seen);}
      }
    };
    for (const n of current.graph.nodes) if (n.collapsed) visit(n.id);
    const result: Node[] = [], seen = new Set<string>();
    const byId = new Map(current.graph.nodes.map(n => [n.id, n]));
    const append = (n: Node) => {
      if (seen.has(n.id) || hidden.has(n.id)) return;
      seen.add(n.id); result.push(n);
      for (const id of childIds.get(n.id) ?? []) {
        const child = byId.get(id);
        if (child) append(child);
      }
    };
    for (const n of current.graph.nodes.filter(isRoot)) append(n);
    for (const n of current.graph.nodes) append(n);
    return result;
  });
  /** Edges whose two ends are shown, so connections fold away with either end. */
  const shownEdges = $derived.by(() => {
    const shown = new Set(visibleNodes.map(n => n.id));
    return current?.graph.edges.filter(e => shown.has(e.from) && shown.has(e.to)) ?? [];
  });
  /** Shown nodes by id: ink is drawn only on these, so it folds away with its node. */
  const shownById = $derived(new Map(visibleNodes.map(n => [n.id, n])));
  function depth(id: string) {
    const seen = new Set<string>();
    while (!seen.has(id)) {
      seen.add(id);
      const parent = parents.get(id);
      if (parent === undefined) break;
      id = parent;
    }
    return seen.size - 1;
  }
  function branchColor(n: Node) {
    let branch = n.id;
    const seen = new Set<string>();
    while (!seen.has(branch)) {
      seen.add(branch);
      const parent = parents.get(branch);
      if (parent === undefined || !parents.has(parent)) break;
      branch = parent;
    }
    const index = current?.graph.nodes.findIndex(n => n.id === branch) ?? 0;
    return `var(--c${Math.max(0, index) % 6 + 1}, var(--accent))`;
  }
  function nodeColor(n: Node) { return n.color ? `var(--${n.color})` : branchColor(n); }
  /** The nodes a style action applies to: every picked topic, with their branches under Whole branch. */
  function styleTargets(branches = paintBranch) {
    const ids = new Set<string>();
    for (const id of picked) for (const item of branches ? descendants(id) : [id]) ids.add(item);
    return current?.graph.nodes.filter(n => ids.has(n.id)) ?? [];
  }
  function paint(color: string | undefined) {
    if (!editable || !picked.size) return;
    remember();
    for (const n of styleTargets()) { if (color) n.color = color; else delete n.color; }
  }
  function copyStyle() {
    if (!node) return;
    copiedStyle = node.color ? {color: node.color} : {};
  }
  function pasteStyle() {
    if (!editable || !picked.size || !copiedStyle) return;
    const style = copiedStyle;
    remember();
    for (const n of styleTargets(false)) { if (style.color) n.color = style.color; else delete n.color; }
  }
  function hiddenLabel(n: Node) {
    return recall && parents.has(n.id) && !revealed.includes(n.id);
  }
  function collapse(n: Node) { remember(); n.collapsed = !n.collapsed; }
  async function editInline(n: Node, initial = name(n)) {
    if (!pane.active) return;
    choose(n); editing = n.id; inlineLabel = initial;
    await tick();
    if (pane.active) container?.querySelector<HTMLInputElement>(`#${CSS.escape(`${pane.id}-map-inline-label`)}`)?.focus();
  }
  function updateInline(value: string) {
    inlineLabel = value;
    const target = current?.graph.nodes.find(item => item.id === editing);
    if (!target || !value.trim() || target.label === value.trim()) return;
    remember(); target.label = value.trim();
    if (selected === target.id) draftLabel = target.label;
  }
  function finishInline(focus = true) {
    const target = current?.graph.nodes.find(item => item.id === editing);
    if (target && inlineLabel.trim() && target.label !== inlineLabel.trim()) {
      remember(); target.label = inlineLabel.trim();
      if (selected === target.id) draftLabel = target.label;
    }
    editing = "";
    if (focus) focusOn(selected);
  }
  /** Adds a topic under `parent` (a root when empty), directly after the sibling `after` when given. */
  function quickAdd(parent = selected, after = "") {
    if (!current || !editable || current.graph.nodes.length >= 300) return;
    remember();
    const n: Node = {id: crypto.randomUUID(), kind: "topic", label: "Topic", notes: "", url: "", source: null, x: 0, y: 0};
    if (parent) {
      const edge = {id: crypto.randomUUID(), from: parent, to: n.id, label: ""};
      const index = after ? current.graph.edges.findIndex(e => treeEdges.has(e.id) && e.to === after) : -1;
      if (index < 0) current.graph.edges.push(edge); else current.graph.edges.splice(index + 1, 0, edge);
      const p = current.graph.nodes.find(n => n.id === parent);
      if (p) p.collapsed = false;
      current.graph.nodes.push(n);
    } else {
      const index = after ? current.graph.nodes.findIndex(item => item.id === after) : -1;
      if (index < 0) current.graph.nodes.push(n); else current.graph.nodes.splice(index + 1, 0, n);
    }
    relayout(); void editInline(n, "");
  }
  /** Lays the tree out again without a separate history step (the caller already remembered). */
  function relayout(fit = true) {
    if (!current) return;
    const positions = treeLayout(current.graph);
    for (const n of current.graph.nodes) {
      const point = positions.get(n.id)!;
      n.x = clampPosition(point.x);
      n.y = clampPosition(point.y);
    }
    if (fit) frame();
  }
  async function findNoteCards() {
    try { const result = await api<{pages: {id: number; title: string}[]}>(`/notes?search=${encodeURIComponent(noteSearch)}`); notePages = result.pages; }
    catch (e) { failure(e); }
  }
  async function addNoteCard(page: {id: number; title: string}) {
    if (!current) return;
    const parent = selected;
    await save();
    if (dirty || blocked) return;
    try {
      accepted(await api<MapItem>("/schema/add-node", {map_id: current.id, revision: current.revision, label: page.title, parent_id: parent || undefined, source: {kind: "note_page", id: page.id}}));
      arrange("tree"); notePages = [];
    } catch (e) { failure(e); }
  }
  function openCard(n: Node) {
    if (n.source?.kind === "note_page") {
      if (dirty && !confirm("Leave unsaved map edits?")) return;
      sessionStorage.setItem("kitty.notes.open-page", String(n.source.id)); location.hash = "#notes";
    } else if (n.map_id) void open(n.map_id);
    else void editInline(n);
  }
  async function studyBranch(purpose: "flashcards" | "quiz" = "flashcards") {
    if (!current || !node) return;
    finishInline(false);
    const ids = descendants(node.id);
    const text = current.graph.nodes.filter(n => ids.has(n.id)).map(n => `${name(n)}\n${n.notes}`).join("\n\n");
    if (dirty) await save();
    if (!pane.active || dirty || blocked) return;
    if (purpose === "flashcards") {
      try { sessionStorage.setItem("kitty.flashcards.import-text", text); } catch { /* The live event still works. */ }
      window.dispatchEvent(new CustomEvent("kitty-study-text", {detail: {text, purpose}}));
      location.hash = "#flashcards";
    } else {
      recall = true; revealed = current.graph.nodes.filter(n => !ids.has(n.id)).map(n => n.id); outline = true;
    }
  }
  async function startBranchSession() {
    if (!current || !node) return;
    finishInline(false);
    const detail = {subject_id: current.graph.nodes.find(n => n.source?.kind === "subject")?.source?.id, concept: name(node)};
    if (dirty) await save();
    if (!pane.active || dirty || blocked) return;
    window.dispatchEvent(new CustomEvent("kitty-study-focus", {detail}));
  }
  async function subjectMap(id: number, title = "") {
    const subject = subjects.find(s => s.id === id);
    if (!subject) return;
    title ||= `${subject.name} · ${template}`;
    newTitle = title; mode = "study";
    await create();
    if (!current || current.title !== title) return;
    kind = "class"; subjectId = String(id); addNode();
    const root = selected;
    for (const title of template === "Lecture" ? ["Key ideas", "Examples", "Questions"] : template === "Chapter" ? ["Overview", "Concepts", "Summary"] : ["Topics", "Practice", "Gaps"]) {
      label = title; kind = "topic"; addNode();
      current.graph.edges.push({id: crypto.randomUUID(), from: root, to: selected, label: ""});
    }
    arrange("tree"); await save();
  }
  async function createTemplate() {
    const expected=newTitle.trim();
    await create();
    if(!current||current.title!==expected||current.graph.nodes.length)return;
    kind="topic";label=current.title;addNode();
    const root=selected;
    for(const title of template === "Blank" ? [] : template==="Lecture"?["Key ideas","Examples","Questions"]:template==="Chapter"?["Overview","Concepts","Summary"]:["Topics","Practice","Gaps"]){
      label=title;addNode();current.graph.edges.push({id:crypto.randomUUID(),from:root,to:selected,label:""});
    }
    arrange("tree");await save();
    const rootNode = current?.graph.nodes.find(n => n.id === root);
    if (rootNode) {
      choose(rootNode);
      await tick();
      if (pane.active) stage?.querySelector<SVGGElement>(`[data-node="${CSS.escape(root)}"]`)?.focus();
    }
  }
  /** Q8: the new-map popover's Enter (or its full form's Create map). */
  async function startMap(text: string) {
    const title = text.trim();
    if (!title) return false;
    newTitle = title;
    if (newSubject) await subjectMap(Number(newSubject), title); else await createTemplate();
    if (!current || current.title !== title) return false;
    creating = false; creatingFull = false; newSubject = "";
    // The popover hands focus back to its + when it closes; the new root takes it instead.
    const root = selected;
    setTimeout(() => { if (pane.active && root) stage?.querySelector<SVGGElement>(`[data-node="${CSS.escape(root)}"]`)?.focus(); });
  }
  $effect(() => {
    if (embedded) return;
    pane.setTrail(section, current ? [
      {label: current.title, onclick: () => {if (pane.active) selected = "";}},
      ...(node ? [{label: name(node), onclick: () => {if (pane.active) focusNode(node.id);}}] : []),
    ] : [], () => {if (!pane.active || (dirty && !confirm("Discard unsaved map edits?"))) return; current = null; selected = "";});
    return ()=>pane.clearTrail(section);
  });
  const autoView = $derived(
    autoLinks && current && autoLinks.map_id === current.id ? autoLinks : null,
  );
  const autoPending = $derived(
    autoView?.suggestions
      .filter((s) => s.references.some((r) => !r.linked))
      .map((s) => s.node_id) ?? [],
  );
  const autoNew = $derived(
    autoView?.suggestions.reduce(
      (count, s) => count + s.references.filter((r) => !r.linked).length,
      0,
    ) ?? 0,
  );
  const netNode = $derived(network?.nodes.find((n) => n.id === netSelected));
  const netChoices = $derived(
    netMode ? maps.filter((m) => m.mode === netMode) : maps,
  );
  const node = $derived(current?.graph.nodes.find((n) => n.id === selected));
  const filtered = $derived(
    current?.graph.nodes.filter((n) =>
      `${name(n)} ${n.kind} ${n.notes}`
        .toLowerCase()
        .includes(search.toLowerCase()),
    ) ?? [],
  );
  const editable = $derived(!!current && !busy && !blocked);
  function clone<T>(value: T): T {
    return JSON.parse(JSON.stringify(value));
  }
  function name(n: Node) {
    return (n.source ? current?.live[n.id]?.label : undefined) ?? n.label;
  }
  function snapshot(): Snapshot {
    return clone({
      title: current!.title,
      graph: current!.graph,
      viewport: current!.viewport,
    });
  }
  function remember() {
    history = [...history.slice(-29), snapshot()];
    future = [];
    touch();
  }
  /** Marks a local edit; the autosave picks it up once the map is idle. */
  function touch() {
    dirty = true;
    saveFailed = false;
    edits += 1;
  }
  function accepted(item: MapItem, restoreDraft = false, keepHistory = false) {
    current = item;
    dirty = false;
    blocked = false;
    saveFailed = false;
    if (!keepHistory) { history = []; future = []; }
    selected = "";
    selectedEdge = "";
    editing = "";
    if (restoreDraft) {
      try {
        const raw = localStorage.getItem(draftKey(item.id));
        if (raw) {
          const draft = JSON.parse(raw);
          if (draft.snapshot?.graph?.nodes && draft.snapshot?.graph?.edges) {
            Object.assign(item, draft.snapshot);
            touch();
            blocked = draft.revision !== item.revision;
            if (blocked) error = "Recovered edits, but the saved map changed elsewhere.";
          }
        }
      } catch { error = "The local map draft could not be restored."; }
    }
    if (!embedded) try { localStorage.setItem(`kitty.schema.last-map.${section}`, String(item.id)); } catch { /* Map selection is optional; SQLite remains authoritative. */ }
  }
  function failure(e: unknown) {
    error = e instanceof Error ? e.message : String(e);
  }
  async function refresh() {
    const data = await api<{
      maps: Summary[];
      subjects: { id: number; name: string }[];
    }>("/schema");
    maps = data.maps;
    subjects = data.subjects;
  }
  async function open(id: number, force = false) {
    if (busy) return;
    if (!force && !(await flush()) && !confirm("Discard unsaved map edits? Export them first to keep a copy.")) return;
    if (force && dirty && !confirm("Discard local edits and reload the saved map?")) return;
    if (force) { try { localStorage.removeItem(draftKey(id)); } catch { /* Optional cache. */ } }
    busy = true;
    error = "";
    try {
      accepted(await api<MapItem>(`/schema/map?id=${id}`), !force);
    } catch (e) {
      failure(e);
    } finally {
      busy = false;
    }
  }
  async function create() {
    if (
      busy ||
      !newTitle.trim() ||
      !(await flush() || confirm("Discard unsaved map edits?"))
    )
      return;
    busy = true;
    error = "";
    try {
      accepted(await api<MapItem>("/schema/create", { title: newTitle, mode }));
      view = "edit";
      newTitle = "";
      await refresh();
    } catch (e) {
      failure(e);
    } finally {
      busy = false;
    }
  }
  /** Saves the open map in the background: editing goes on, and an edit made meanwhile keeps it dirty. */
  function save(): Promise<void> {
    if (saving) return saving.then(() => (dirty && !blocked ? save() : undefined));
    if (!current || !dirty || blocked || busy) return Promise.resolve();
    clearTimeout(autosaveTimer);
    const item = current, sent = edits;
    const body = {id: item.id, revision: item.revision, title: item.title, graph: clone(item.graph), viewport: clone(item.viewport)};
    const run = (async () => {
      try {
        const result = await api<MapItem>("/schema/save", body);
        if (current !== item) return;
        item.revision = result.revision;
        item.can_undo = result.can_undo;
        item.can_redo = result.can_redo;
        item.paired_history = result.paired_history;
        item.live = result.live;
        if (edits === sent) {
          dirty = false;
          try { localStorage.removeItem(draftKey(item.id)); } catch { /* Saved on server. */ }
        }
        if (!embedded) void refresh().catch(failure);
      } catch (e) {
        failure(e);
        saveFailed = true;
        blocked = error.includes("changed in another window");
      } finally {
        saving = null;
      }
    })();
    saving = run;
    return run;
  }
  async function removeMap() {
    if (
      !current ||
      !editable ||
      !confirm(
        `Permanently delete “${current.title}” and its graph? Export first to keep a copy.`,
      )
    )
      return;
    busy = true;
    error = "";
    try {
      await api("/schema/delete", {
        id: current.id,
        revision: current.revision,
        confirm: true,
      });
      try { localStorage.removeItem(draftKey(current.id)); } catch { /* Optional draft cache. */ }
      current = null;
      history = [];
      dirty = false;
      await refresh();
    } catch (e) {
      failure(e);
      blocked = error.includes("changed in another window");
    } finally {
      busy = false;
    }
  }
  async function undo() {
    if (!current || !editable) return;
    if (history.length) {
      const previous = history[history.length - 1];
      future = [...future, snapshot()];
      history = history.slice(0, -1);
      Object.assign(current, clone(previous));
      touch();
      selected = "";
      multi = []; editing = ""; labelling = "";
      selectedEdge = "";
      return;
    }
    if (saving) await saving;
    if (dirty || !current.can_undo) return;
    busy = true;
    error = "";
    try {
      const forward = snapshot(), paired = current.paired_history;
      accepted(
        await api<MapItem>("/schema/undo", {
          id: current.id,
          revision: current.revision,
        }),
      );
      await refresh();
      if (!paired) future = [forward];
    } catch (e) {
      failure(e);
      blocked = error.includes("changed in another window");
    } finally {
      busy = false;
    }
  }
  async function redo() {
    if (!current || !editable) return;
    if (!future.length) {
      if (saving) await saving;
      if (!current.can_redo || dirty) return;
      busy = true; error = "";
      try {
        accepted(await api<MapItem>("/schema/undo", {id: current.id, revision: current.revision, redo: true}));
        await refresh();
      } catch (e) { failure(e); }
      finally { busy = false; }
      return;
    }
    const next = future[future.length - 1];
    history = [...history, snapshot()];
    future = future.slice(0, -1);
    editing = ""; labelling = ""; selected = ""; multi = []; selectedEdge = "";
    Object.assign(current, clone(next));
    touch();
  }
  async function transferBranch(nodeId: string, targetId?: number) {
    if (!current || !editable || !(await flush()) || !current) return;
    busy = true; error = "";
    try {
      const target = maps.find(m => m.id === targetId);
      accepted(await api<MapItem>("/schema/transfer", {
        id: current.id, revision: current.revision, node_id: nodeId,
        ...(target ? {target_id: target.id, target_revision: target.revision} : {}),
      }));
      frame();
      await refresh();
    } catch (e) { failure(e); }
    finally { busy = false; }
  }
  /** Selects `n`; a topic outside the multi-selection replaces it. */
  function choose(n: Node) {
    if (!multi.includes(n.id)) multi = [];
    selected = n.id;
    selectedEdge = "";
    draftLabel = n.label;
    draftNotes = n.notes;
    draftUrl = n.url;
  }
  function addNode() {
    if (!current || !editable || current.graph.nodes.length >= 300) return;
    const subject = subjects.find((s) => s.id === Number(subjectId));
    if (kind === "class" && !subject) {
      error = "Choose an existing subject first. Add subjects in Study → Subjects & concepts.";
      return;
    }
    if (kind !== "class" && !label.trim()) return;
    if (
      subject &&
      kind === "class" &&
      current.graph.nodes.some(
        (n) => n.source?.kind === "subject" && n.source.id === subject.id,
      )
    ) {
      error = "This class already has a node in this map.";
      return;
    }
    remember();
    const rect = stage?.getBoundingClientRect(),
      view = current.viewport;
    const n: Node = {
      id: crypto.randomUUID(),
      kind,
      label: kind === "class" ? subject!.name : label.trim(),
      notes: "",
      url: "",
      source: kind === "class" ? { kind: "subject", id: subject!.id } : null,
      x: Math.max(
        -100000,
        Math.min(100000, ((rect?.width ?? 700) / 2 - view.x) / view.zoom - 60),
      ),
      y: Math.max(
        -100000,
        Math.min(100000, ((rect?.height ?? 500) / 2 - view.y) / view.zoom - PILL_H / 2),
      ),
    };
    current.graph.nodes.push(n);
    label = "";
    choose(n);
  }
  function updateNode() {
    if (!node || !editable || !draftLabel.trim()) return;
    remember();
    node.label = draftLabel.trim();
    node.notes = draftNotes;
    node.url = draftUrl.trim();
  }
  /** Deletes every picked topic and its connections. */
  function deleteNode() {
    if (!current || !node || !editable) return;
    const gone = new Set(picked);
    remember();
    current.graph.nodes = current.graph.nodes.filter((n) => !gone.has(n.id));
    current.graph.edges = current.graph.edges.filter((e) => !gone.has(e.from) && !gone.has(e.to));
    if (current.graph.strokes) current.graph.strokes = current.graph.strokes.filter((s) => !gone.has(s.node));
    selected = "";
    multi = [];
  }
  function deleteAtPointer(event: MouseEvent, target: Node) {
    if ((event.target as Element).closest('input,textarea,[contenteditable="true"]') || window.getSelection()?.toString()) return;
    event.preventDefault();
    // A pen's barrel button or a resting palm also raises this menu; with a pen near, it never deletes.
    if (penNear()) return;
    if (!editable || !confirm(`Remove “${name(target)}” from this map? Linked notes, cards and jobs stay unchanged.`)) return;
    multi = [];
    choose(target);
    deleteNode();
  }
  function unlink() {
    if (!node || !editable) return;
    remember();
    node.label = name(node);
    node.url = current?.live[node.id]?.url ?? node.url;
    node.source = null;
    choose(node);
  }
  /** Q65 E: adds a labelled cross connection, which never changes the tree or its layout. */
  function link(from: string, to: string, text: string) {
    if (!current || !editable || current.graph.edges.length >= 600 ||
      !current.graph.nodes.some(n => n.id === from) || !current.graph.nodes.some(n => n.id === to)) return "";
    remember();
    const id = crypto.randomUUID();
    current.graph.edges.push({id, from, to, label: text, kind: "cross"});
    return id;
  }
  /** Finishes "Connect to…" on `target`, then asks for the connection's label in place. */
  async function linkTo(target: string) {
    const from = linking;
    linking = "";
    const id = target === from ? "" : link(from, target, "");
    if (!id) return;
    labelling = id; edgeDraft = "";
    await tick();
    if (pane.active) container?.querySelector<HTMLInputElement>(`#${CSS.escape(`${pane.id}-edge-label`)}`)?.focus();
  }
  async function relabel(id: string) {
    const e = current?.graph.edges.find(e => e.id === id);
    if (!e || !editable) return;
    labelling = id; edgeDraft = e.label;
    await tick();
    if (pane.active) container?.querySelector<HTMLInputElement>(`#${CSS.escape(`${pane.id}-edge-label`)}`)?.focus();
  }
  function finishLabel(keep = true, focus = false) {
    const e = current?.graph.edges.find(e => e.id === labelling);
    labelling = "";
    if (e && keep && e.label !== edgeDraft.trim()) { remember(); e.label = edgeDraft.trim(); }
    if (e && focus) focusOn(e.to);
  }
  function removeEdge(id: string) {
    if (!current || !editable) return;
    remember();
    current.graph.edges = current.graph.edges.filter((e) => e.id !== id);
    selectedEdge = "";
  }
  function editEdge(id: string, field: "label" | "from" | "to", value: string) {
    if (!current || !editable) return;
    const e = current.graph.edges.find((e) => e.id === id);
    if (!e) return;
    remember();
    e[field] = value;
  }
  /** Siblings in display order: the children of the same parent, or the parentless roots. */
  function siblingIds(id: string) {
    if (!current) return [];
    const parent = parents.get(id);
    return parent === undefined
      ? current.graph.nodes.filter(isRoot).map(n => n.id)
      : current.graph.edges.filter(e => treeEdges.has(e.id) && e.from === parent).map(e => e.to);
  }
  function focusOn(id: string) {
    void tick().then(() => { if (pane.active) stage?.querySelector<SVGGElement>(`[data-node="${CSS.escape(id)}"]`)?.focus(); });
  }
  /** Ctrl+↑/↓: swap a node with its previous/next sibling, then lay the tree out again. */
  function reorder(n: Node, direction: -1 | 1) {
    if (!current || !editable) return;
    const sibs = siblingIds(n.id), other = sibs[sibs.indexOf(n.id) + direction];
    if (!other) return;
    remember();
    const list: {id: string}[] = parents.has(n.id) ? current.graph.edges : current.graph.nodes;
    const at = (id: string) => parents.has(n.id) ? current!.graph.edges.findIndex(e => treeEdges.has(e.id) && e.to === id) : current!.graph.nodes.findIndex(item => item.id === id);
    const a = at(n.id), b = at(other);
    [list[a], list[b]] = [list[b], list[a]];
    relayout(false);
    focusOn(n.id);
  }
  /** Window keys: Escape leaves "Connect to…"; mid-drag, D breaks the branch off (press again to reattach on drop), wherever focus is. */
  function dragKey(e: KeyboardEvent) {
    lastTap.key = ""; // A key between two taps makes them two single taps.
    if (pane.active && current && editable && !gesture && (e.ctrlKey || e.metaKey) && !e.altKey && e.key.toLowerCase() === "z") {
      e.preventDefault(); e.stopPropagation();
      if (e.shiftKey) redo(); else void undo();
      return;
    }
    if (pane.active && current && view === "edit" && (e.ctrlKey || e.metaKey) && !e.altKey && !e.shiftKey && e.key.toLowerCase() === "s") {
      e.preventDefault();
      finishInline(false);
      void save();
      return;
    }
    if (pane.active && linking && e.key === "Escape") { e.preventDefault(); e.stopPropagation(); linking = ""; return; }
    if (!pane.active || !gesture?.node || e.key.toLowerCase() !== "d" || e.ctrlKey || e.metaKey || e.altKey) return;
    e.preventDefault(); e.stopPropagation();
    detaching = !detaching; dropTarget = "";
  }
  function nodeKey(e: KeyboardEvent, n: Node) {
    // A drag owns the keyboard (see dragKey); keys must not rename the dragged node.
    if (!editable || gesture?.node) return;
    // Alt+C / Alt+V copy and paste a node's style (Ctrl+Alt is the app's jump-to-Plan chord).
    if (e.altKey && !e.ctrlKey && !e.metaKey && !e.shiftKey && (e.code === "KeyC" || e.code === "KeyV")) {
      e.preventDefault(); e.stopPropagation(); choose(n);
      if (e.code === "KeyC") copyStyle(); else pasteStyle();
      return;
    }
    if (e.altKey) return;
    // "Connect to…": move to the other topic and press Enter (Escape cancels, see dragKey).
    if (linking && e.key === "Enter") { e.preventDefault(); e.stopPropagation(); void linkTo(n.id); return; }
    if (e.ctrlKey || e.metaKey) {
      if (e.key === "Enter") { e.preventDefault(); e.stopPropagation(); void editInline(n); }
      else if (e.key === "ArrowUp" || e.key === "ArrowDown") { e.preventDefault(); e.stopPropagation(); choose(n); reorder(n, e.key === "ArrowUp" ? -1 : 1); }
      return;
    }
    if (e.key === "Escape" && multi.length) { e.preventDefault(); e.stopPropagation(); multi = []; return; }
    // Shift+arrows extend the selection to the topic the arrow moves to.
    const extend = e.shiftKey && e.key.startsWith("Arrow");
    if (!extend) choose(n);
    if (["Tab", "Enter", " ", "Delete", "Backspace", "ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(e.key)) {
      e.preventDefault(); e.stopPropagation();
    }
    if (e.key === "Tab") quickAdd(n.id);
    else if (e.key === "Enter") quickAdd(parents.get(n.id) ?? "", n.id);
    else if (e.key === " ") { if (recall) revealed = [...revealed, n.id]; else collapse(n); }
    else if (e.key === "Delete" || e.key === "Backspace") deleteNode();
    else if (e.key.startsWith("Arrow")) {
      const visible = new Set(visibleNodes.map(item => item.id));
      const sibs = siblingIds(n.id).filter(id => visible.has(id)), index = visibleNodes.findIndex(item => item.id === n.id);
      const step = e.key === "ArrowUp" ? -1 : 1;
      const id = e.key === "ArrowLeft" ? parents.get(n.id)
        : e.key === "ArrowRight" ? current?.graph.edges.find(edge => treeEdges.has(edge.id) && edge.from === n.id && visible.has(edge.to))?.to
        : sibs[sibs.indexOf(n.id) + step] ?? visibleNodes[index + step]?.id;
      const target = visibleNodes.find(item => item.id === id);
      if (target && extend) {
        multi = [...new Set([...picked, n.id, target.id])];
        choose(target);
        focusOn(target.id);
      } else if (target) { choose(target); focusOn(target.id); }
    } else if (e.key.length === 1) { e.preventDefault(); e.stopPropagation(); void editInline(n, e.key); }
  }
  /** Q69: whether a pen hovers over the map or was used within PALM_MS; touches are then resting palms. */
  function penNear() {
    return penHover || performance.now() - penSeen < PALM_MS;
  }
  function notePen(e: PointerEvent) {
    if (e.pointerType !== "pen") return;
    penHover = e.type !== "pointerleave";
    penSeen = performance.now();
  }
  /** A second tap on the same spot within 450 ms is a double tap, for pen, touch and mouse alike. */
  function doubleTap(key: string, e: PointerEvent) {
    const now = performance.now(), again = key === lastTap.key && now - lastTap.time < 450 && Math.hypot(e.clientX - lastTap.x, e.clientY - lastTap.y) < 16;
    lastTap = again ? {key: "", time: 0, x: 0, y: 0} : {key, time: now, x: e.clientX, y: e.clientY};
    return again;
  }
  /** Every press on the map (pen, mouse or touch) starts here: palms are dropped, pen ends and ink mode draw, the rest selects, drags and pans. */
  function down(e: PointerEvent, id = "") {
    notePen(e);
    const action = penAction(e, inkMode, penNear());
    if (action === "point") { if (id) start(e, id); else pick(e); return; }
    e.preventDefault();
    e.stopPropagation();
    if (action === "ignore" || !current || !editable || gesture || inking || lassoLoop || erasing) return;
    stage?.setPointerCapture(e.pointerId);
    const p = worldPoint(e);
    if (action === "ink") inking = {pointer: e.pointerId, points: [[p.x, p.y, pressure(e)]]};
    else if (action === "lasso") lassoLoop = {pointer: e.pointerId, points: [[p.x, p.y]]};
    else { erasing = {pointer: e.pointerId, erased: false}; erase(e); }
  }
  /** Pen pressure; a mouse (or a pen reporting none) draws at half pressure. */
  function pressure(e: PointerEvent) {
    return Math.round(Math.min(1, e.pressure || 0.5) * 100) / 100;
  }
  /** One pen sample: extends the stroke or lasso, or erases ink under the eraser. */
  function penMove(e: PointerEvent) {
    const p = worldPoint(e);
    if (inking) inking.points.push([p.x, p.y, pressure(e)]);
    else if (lassoLoop) lassoLoop.points.push([p.x, p.y]);
    else erase(e);
  }
  /** Removes every shown stroke under the eraser; topics are never touched. */
  function erase(e: PointerEvent) {
    if (!current?.graph.strokes?.length || !erasing) return;
    const p = worldPoint(e), reach = 10 / current.viewport.zoom + INK_WIDTH / 2;
    const hit = new Set(current.graph.strokes.filter(s => {
      const owner = shownById.get(s.node);
      return owner && strokeHit(s.points, [p.x - owner.x, p.y - owner.y], reach);
    }).map(s => s.id));
    if (!hit.size) return;
    if (!erasing.erased) { remember(); erasing.erased = true; }
    current.graph.strokes = current.graph.strokes.filter(s => !hit.has(s.id));
  }
  /** Simplifies the lifted stroke and attaches it to the nearest shown topic, relative to that topic. */
  function finishInk(drawn: InkPoint[]) {
    if (!current) return;
    const owner = shownById.get(nearestBox(visibleNodes.map(n => ({id: n.id, x: n.x, y: n.y, ...box(n)})), drawn) ?? "");
    if (!owner) { error = "Add a topic first: ink sticks to the nearest topic."; return; }
    if ((current.graph.strokes?.length ?? 0) >= 400) { error = "A map holds at most 400 ink strokes; erase some first."; return; }
    // Ramer–Douglas–Peucker within about a screen pixel, coarser only if the stroke is still too long.
    let tolerance = 0.6 / current.viewport.zoom, points = simplify(drawn, tolerance);
    while (points.length > MAX_INK_POINTS) points = simplify(drawn, tolerance *= 2);
    const local = (value: number) => Math.max(-LIMIT, Math.min(LIMIT, Math.round(value * 10) / 10));
    remember();
    current.graph.strokes = [...current.graph.strokes ?? [], {id: crypto.randomUUID(), node: owner.id, points: points.map(([x, y, p]): InkPoint => [local(x - owner.x), local(y - owner.y), p])}];
  }
  /** Selects every shown topic whose centre lies inside the barrel-button loop (the Q65 multi-selection). */
  function finishLasso(loop: [number, number][]) {
    const ids = new Set(lassoed(visibleNodes.map(n => ({id: n.id, x: n.x, y: n.y, ...box(n)})), loop));
    const hits = visibleNodes.filter(n => ids.has(n.id));
    if (hits.length) { multi = hits.map(n => n.id); choose(hits[0]); focusOn(hits[0].id); }
  }
  function start(e: PointerEvent, id = "") {
    if (!current || gesture || e.button !== 0) return;
    if (!editable) {
      // While saving, a tap still selects so the inspector can show the topic.
      const n = id && current.graph.nodes.find(n => n.id === id);
      if (n) choose(n);
      return;
    }
    e.preventDefault();
    e.stopPropagation();
    if (linking) {
      if (id) void linkTo(id); else linking = "";
      return;
    }
    if (id && e.shiftKey) {
      // Shift-click adds a topic to (or takes it out of) the selection.
      (e.currentTarget as SVGGElement).focus();
      const ids = new Set(picked);
      if (ids.has(id) && ids.size > 1) ids.delete(id); else ids.add(id);
      multi = [...ids];
      const n = current.graph.nodes.find((n) => n.id === (ids.has(id) ? id : multi[0]))!;
      choose(n);
      return;
    }
    if (id) {
      (e.currentTarget as SVGGElement).focus();
      const n = current.graph.nodes.find((n) => n.id === id)!;
      choose(n);
    }
    const branch = new Set<string>();
    if (id) for (const top of picked.has(id) ? pickedTops() : [id]) for (const item of descendants(top)) branch.add(item);
    gesture = {
      pointer: e.pointerId,
      x: e.clientX,
      y: e.clientY,
      node: id,
      branch: [...branch],
      before: snapshot(),
      moved: false,
      box: !id && e.shiftKey,
    };
    stage?.setPointerCapture(e.pointerId);
  }
  /** Map coordinates of a pointer position. */
  function worldPoint(e: PointerEvent) {
    const rect = stage!.getBoundingClientRect(), v = current!.viewport;
    return {x: (e.clientX - rect.left - v.x) / v.zoom, y: (e.clientY - rect.top - v.y) / v.zoom};
  }
  /** The pill under the pointer that a dragged branch may move into (never its own branch). */
  function dropCandidate(e: PointerEvent, branch: string[]) {
    if (!current || !stage) return undefined;
    const {x, y} = worldPoint(e);
    return visibleNodes.find(n => !branch.includes(n.id) && x >= n.x && x <= n.x + box(n).w && y >= n.y && y <= n.y + box(n).h);
  }
  function move(e: PointerEvent) {
    notePen(e);
    if ((inking ?? lassoLoop ?? erasing)?.pointer === e.pointerId) {
      // Coalesced samples keep fast pen strokes smooth.
      const samples = e.getCoalescedEvents?.() ?? [];
      for (const sample of samples.length ? samples : [e]) penMove(sample);
      return;
    }
    if (!gesture || !current || gesture.pointer !== e.pointerId) return;
    const dx = e.clientX - gesture.x,
      dy = e.clientY - gesture.y;
    if (Math.abs(dx) + Math.abs(dy) < 2 && !gesture.moved) return;
    gesture.moved = true;
    if (gesture.node) {
      const branch = gesture.branch, origin = gesture.before.graph.nodes.find((n) => n.id === gesture!.node)!;
      placing = e.altKey && !detaching;
      const target = detaching || placing ? undefined : dropCandidate(e, branch);
      let ox = dx / current.viewport.zoom, oy = dy / current.viewport.zoom;
      if (target) {
        // Preview the drop: the branch snaps to where it would land, after the target's last child.
        const size = box(target), last = visibleNodes.filter(n => parents.get(n.id) === target.id && !branch.includes(n.id)).reduce((low, n) => Math.max(low, n.y + box(n).h), -Infinity);
        ox = target.x + size.w + 56 - origin.x;
        oy = (Number.isFinite(last) ? last + 14 : target.y + size.h / 2 - box(origin).h / 2) - origin.y;
      }
      // The whole branch travels with the dragged node.
      for (const id of branch) {
        const n = current.graph.nodes.find((n) => n.id === id), old = gesture.before.graph.nodes.find((n) => n.id === id);
        if (!n || !old) continue;
        n.x = Math.max(-LIMIT, Math.min(LIMIT, old.x + ox));
        n.y = Math.max(-LIMIT, Math.min(LIMIT, old.y + oy));
      }
      dragging = gesture.node;
      dropTarget = target?.id ?? "";
    } else if (gesture.box) {
      const a = worldPoint({clientX: gesture.x, clientY: gesture.y} as PointerEvent), b = worldPoint(e);
      marquee = {x0: Math.min(a.x, b.x), y0: Math.min(a.y, b.y), x1: Math.max(a.x, b.x), y1: Math.max(a.y, b.y)};
    } else {
      current.viewport.x = Math.max(
        -1e7,
        Math.min(1e7, gesture.before.viewport.x + dx),
      );
      current.viewport.y = Math.max(
        -1e7,
        Math.min(1e7, gesture.before.viewport.y + dy),
      );
    }
  }
  /** Breaks a node (and its branch) off its parent as a floating root that keeps its place. */
  function detachBranch(n: Node) {
    if (!current) return;
    current.graph.edges = current.graph.edges.filter(e => !(treeEdges.has(e.id) && e.to === n.id));
    n.free = true;
  }
  function detachSelected() {
    if (!current || !node || !editable || isRoot(node)) return;
    remember();
    detachBranch(node);
    relayout(false);
  }
  /** Q65 C: a freely placed node rejoins the automatic layout. */
  function resetPosition() {
    if (!current || !node?.free || !editable) return;
    remember();
    delete node.free;
    relayout(false);
  }
  function end(e: PointerEvent, cancel = false) {
    notePen(e);
    if ((inking ?? lassoLoop ?? erasing)?.pointer === e.pointerId) {
      const drawn = inking?.points, loop = lassoLoop?.points;
      inking = null; lassoLoop = null; erasing = null;
      if (stage?.hasPointerCapture(e.pointerId)) stage.releasePointerCapture(e.pointerId);
      if (cancel) return; // A cancelled stroke or lasso leaves nothing; erased ink stays erased (Undo restores it).
      if (drawn) finishInk(drawn); else if (loop) finishLasso(loop);
      return;
    }
    if (!gesture || !current || gesture.pointer !== e.pointerId) return;
    if (gesture.moved) lastTap.key = "";
    const mergeTarget = !cancel && gesture.node && gesture.moved
      ? Number(document.elementFromPoint(e.clientX, e.clientY)?.closest("[data-merge-map]")?.getAttribute("data-merge-map")) : 0;
    if (mergeTarget && isRoot(current.graph.nodes.find(n => n.id === gesture!.node)!)) {
      const id = gesture.node;
      Object.assign(current, clone(gesture.before));
      gesture = null; dragging = ""; dropTarget = ""; detaching = false; placing = false;
      if (stage?.hasPointerCapture(e.pointerId)) stage.releasePointerCapture(e.pointerId);
      void transferBranch(id, mergeTarget);
      return;
    }
    const target = dropTarget;
    // Every picked branch moves when a picked topic is dragged; otherwise just the dragged one.
    const tops = (picked.has(gesture.node) ? pickedTops() : [gesture.node]).map(id => current!.graph.nodes.find(n => n.id === id)!).filter(Boolean);
    if (cancel) Object.assign(current, clone(gesture.before));
    else if (gesture.box) {
      const area = marquee, hits = area ? visibleNodes.filter(n => n.x < area.x1 && n.x + box(n).w > area.x0 && n.y < area.y1 && n.y + box(n).h > area.y0) : [];
      if (hits.length) { multi = hits.map(n => n.id); choose(hits[0]); focusOn(hits[0].id); }
    } else if (gesture.moved && gesture.node) {
      const dragged = current.graph.nodes.find(n => n.id === gesture!.node)!;
      const free = !detaching && e.altKey;
      if (detaching || target || free || dragged.free) {
        history = [...history.slice(-29), gesture.before];
        future = [];
        for (const top of tops) {
          if (detaching) { if (!isRoot(top)) detachBranch(top); }
          else if (free) top.free = true; // Alt-drag: it keeps its parent but leaves the automatic layout.
          else if (target) {
            current.graph.edges = current.graph.edges.filter(e => !(treeEdges.has(e.id) && e.to === top.id));
            current.graph.edges.push({id: crypto.randomUUID(), from: target, to: top.id, label: ""});
            delete top.free;
          }
        }
        const parent = target ? current.graph.nodes.find(n => n.id === target) : undefined;
        if (parent) parent.collapsed = false;
        relayout(false);
        touch();
      } else Object.assign(current, clone(gesture.before)); // No target: the branch snaps back into the layout.
    } else if (gesture.node && !gesture.moved) {
      if (multi.length) multi = []; // A plain click picks just that topic.
      const tapped = current.graph.nodes.find(n => n.id === gesture!.node);
      if (tapped && doubleTap(tapped.id, e)) openCard(tapped);
    } else if (gesture.moved) {
      history = [...history.slice(-29), gesture.before];
      future = [];
      touch();
    } else if (!gesture.box && doubleTap("stage", e)) quickAdd("");
    gesture = null;
    dragging = ""; dropTarget = ""; detaching = false; placing = false; marquee = null;
    if (stage?.hasPointerCapture(e.pointerId))
      stage.releasePointerCapture(e.pointerId);
  }
  function zoom(factor: number, clientX?: number, clientY?: number) {
    if (!current || !editable) return;
    const rect = stage?.getBoundingClientRect(),
      x =
        clientX === undefined
          ? (rect?.width ?? 700) / 2
          : clientX - (rect?.left ?? 0),
      y =
        clientY === undefined
          ? (rect?.height ?? 500) / 2
          : clientY - (rect?.top ?? 0);
    remember();
    const v = current.viewport,
      z = Math.max(0.1, Math.min(4, v.zoom * factor));
    v.x = Math.max(-1e7, Math.min(1e7, x - ((x - v.x) * z) / v.zoom));
    v.y = Math.max(-1e7, Math.min(1e7, y - ((y - v.y) * z) / v.zoom));
    v.zoom = z;
  }
  function fit() {
    if (!current || !editable) return;
    remember();
    frame();
  }
  function frame() {
    if (!current) return;
    const ns = current.graph.nodes,
      rect = stage?.getBoundingClientRect();
    if (!ns.length) {
      current.viewport = { x: 40, y: 40, zoom: 1 };
      return;
    }
    const left = Math.min(...ns.map((n) => n.x)),
      top = Math.min(...ns.map((n) => n.y)),
      width = Math.max(...ns.map((n) => n.x + box(n).w)) - left,
      height = Math.max(...ns.map((n) => n.y + box(n).h)) - top;
    const w = rect?.width ?? 700,
      h = rect?.height ?? 500,
      z = Math.max(0.1, Math.min(1.25, (w - 60) / width, (h - 60) / height));
    current.viewport = {
      x: (w - width * z) / 2 - left * z,
      y: (h - height * z) / 2 - top * z,
      zoom: z,
    };
  }
  function clampPosition(value: number) {
    return Math.max(-LIMIT, Math.min(LIMIT, Math.round(value)));
  }
  /** Horizontal branch curve from parent pill `a` to child pill `b`'s facing side. */
  function branchCurve(a: Node, b: Node) {
    const as = box(a), bs = box(b);
    const ca = {x: a.x + as.w / 2, y: a.y + as.h / 2}, cb = {x: b.x + bs.w / 2, y: b.y + bs.h / 2};
    const side = cb.x >= ca.x ? 1 : -1, sx = ca.x + side * as.w / 2, ex = cb.x - side * bs.w / 2, mx = (sx + ex) / 2;
    return {d: `M ${sx} ${ca.y} C ${mx} ${ca.y}, ${mx} ${cb.y}, ${ex} ${cb.y}`, lx: mx, ly: (ca.y + cb.y) / 2 - 6};
  }
  function snapPath(parent: string, child: string) {
    const a = current?.graph.nodes.find(n => n.id === parent), b = current?.graph.nodes.find(n => n.id === child);
    return a && b ? branchCurve(a, b).d : "";
  }
  /** Point where the ray from a node box's center along (dx, dy) leaves the box. */
  function boundary(c: Point, dx: number, dy: number, size: {w: number; h: number}): Point {
    const t = Math.min(
      dx === 0 ? Infinity : size.w / 2 / Math.abs(dx),
      dy === 0 ? Infinity : size.h / 2 / Math.abs(dy),
    );
    return Number.isFinite(t) ? { x: c.x + dx * t, y: c.y + dy * t } : c;
  }
  /**
   * Edge paths. A node's tree edge is a horizontal branch curve from the parent's side to the
   * child's facing side. Other connections (Q65 E) are arcs bent off the chord (brackets round the
   * right side when both pills share a column), fanned apart for bidirectional or parallel
   * connections, and stacked loops for self-connections, with endpoints on the pill borders so
   * arrowheads stay visible.
   */
  const geometry = $derived.by(() => {
    const shapes = new Map<string, { d: string; lx: number; ly: number; tree: boolean }>();
    if (!current) return shapes;
    const byId = new Map(current.graph.nodes.map((n) => [n.id, n])),
      pairs = new Map<string, Edge[]>();
    for (const e of current.graph.edges) {
      if (treeEdges.has(e.id)) {
        shapes.set(e.id, {...branchCurve(byId.get(e.from)!, byId.get(e.to)!), tree: true});
        continue;
      }
      const key = e.from < e.to ? `${e.from} ${e.to}` : `${e.to} ${e.from}`;
      const list = pairs.get(key);
      if (list) list.push(e);
      else pairs.set(key, [e]);
    }
    for (const list of pairs.values())
      list.forEach((e, index) => {
        const a = byId.get(e.from)!,
          b = byId.get(e.to)!,
          as = box(a),
          bs = box(b);
        if (a.id === b.id) {
          const r = 18 + index * 12,
            sx = a.x + as.w - 44,
            ex = a.x + as.w - 14;
          shapes.set(e.id, {
            d: `M ${sx} ${a.y} C ${sx - 10} ${a.y - 2.2 * r}, ${ex + 10} ${a.y - 2.2 * r}, ${ex} ${a.y}`,
            lx: (sx + ex) / 2,
            ly: a.y - 1.65 * r - 6,
            tree: false,
          });
          return;
        }
        // Pills sharing a column: bracket around their right side instead of cutting through the column.
        if (Math.abs(a.x + as.w / 2 - (b.x + bs.w / 2)) < (as.w + bs.w) / 2) {
          const sx = a.x + as.w, ex = b.x + bs.w, ay = a.y + as.h / 2, by = b.y + bs.h / 2,
            // The bracket's apex clears every shown pill in the column between its two ends.
            right = visibleNodes.reduce((edge, n) => n.y + box(n).h / 2 > Math.min(ay, by) && n.y + box(n).h / 2 < Math.max(ay, by) && n.x <= Math.max(sx, ex)
              ? Math.max(edge, n.x + box(n).w) : edge, Math.max(sx, ex)),
            rx = (right + 28 + index * 24 - 0.125 * (sx + ex)) / 0.75;
          shapes.set(e.id, {
            d: `M ${sx} ${ay} C ${rx} ${ay}, ${rx} ${by}, ${ex} ${by}`,
            lx: 0.125 * (sx + ex) + 0.75 * rx,
            ly: (ay + by) / 2 + 4,
            tree: false,
          });
          return;
        }
        const ca = { x: a.x + as.w / 2, y: a.y + as.h / 2 },
          cb = { x: b.x + bs.w / 2, y: b.y + bs.h / 2 },
          sign = e.from < e.to ? 1 : -1,
          dx = (cb.x - ca.x) * sign,
          dy = (cb.y - ca.y) * sign,
          length = Math.hypot(dx, dy) || 1,
          offset = Math.min(110, Math.max(30, length * 0.25)) + (index - (list.length - 1) / 2) * 48,
          control = {
            x: (ca.x + cb.x) / 2 - (dy / length) * offset,
            y: (ca.y + cb.y) / 2 + (dx / length) * offset,
          },
          s = boundary(ca, control.x - ca.x, control.y - ca.y, as),
          t = boundary(cb, control.x - cb.x, control.y - cb.y, bs);
        shapes.set(e.id, {
          d: `M ${s.x} ${s.y} Q ${control.x} ${control.y} ${t.x} ${t.y}`,
          lx: 0.25 * s.x + 0.5 * control.x + 0.25 * t.x,
          ly: 0.25 * s.y + 0.5 * control.y + 0.25 * t.y + 4,
          tree: false,
        });
      });
    return shapes;
  });
  /** Left-to-right tree over tree edges: roots are nodes without a parent (or, inside cycles, the busiest node); parents center beside their children; folded branches take one row. */
  function treeLayout(graph: Graph) {
    const out = new Map(graph.nodes.map((n) => [n.id, [] as string[]])),
      incoming = new Map(graph.nodes.map((n) => [n.id, 0]));
    for (const e of graph.edges)
      if (treeEdges.has(e.id)) {
        out.get(e.from)!.push(e.to);
        incoming.set(e.to, incoming.get(e.to)! + 1);
      }
    const children = new Map<string, string[]>(),
      depth = new Map<string, number>(),
      roots: string[] = [];
    const grow = (root: string) => {
      roots.push(root);
      depth.set(root, 0);
      children.set(root, []);
      const queue = [root];
      for (let i = 0; i < queue.length; i++)
        for (const next of out.get(queue[i])!)
          if (!depth.has(next)) {
            depth.set(next, depth.get(queue[i])! + 1);
            children.get(queue[i])!.push(next);
            children.set(next, []);
            queue.push(next);
          }
    };
    for (const n of graph.nodes)
      if (incoming.get(n.id) === 0 && !depth.has(n.id)) grow(n.id);
    for (const n of [...graph.nodes].sort(
      (a, b) => out.get(b.id)!.length - out.get(a.id)!.length,
    ))
      if (!depth.has(n.id)) grow(n.id);
    const positions = new Map<string, Point>(),
      byId = new Map(graph.nodes.map((n) => [n.id, n])),
      row = PILL_H + 14,
      anchors: string[] = [];
    let slot = 0;
    const place = (id: string, x: number): number => {
      const n = byId.get(id)!, size = box(n);
      // Free nodes are anchors: they keep their own place and their branch is laid out around them.
      const kids: string[] = [];
      if (!n.collapsed) for (const kid of children.get(id)!) (byId.get(kid)!.free ? anchors : kids).push(kid);
      const ys = kids.map((kid) => place(kid, x + size.w + 56));
      const cy = ys.length ? (ys[0] + ys[ys.length - 1]) / 2 : slot++ * row + row / 2;
      positions.set(id, { x, y: cy - size.h / 2 });
      return cy;
    };
    for (const root of roots) {
      if (byId.get(root)!.free) anchors.push(root);
      else {
        place(root, 0);
        slot += 0.8;
      }
    }
    for (let i = 0; i < anchors.length; i++) {
      const anchor = byId.get(anchors[i])!, placed = new Set(positions.keys());
      slot = 0;
      const shift = anchor.y + box(anchor).h / 2 - place(anchor.id, anchor.x);
      for (const [id, point] of positions) if (!placed.has(id)) point.y += shift;
    }
    return positions;
  }
  /** Deterministic Fruchterman–Reingold from current positions, then box-overlap removal. */
  function forceLayout(graph: Graph) {
    const ns = graph.nodes,
      sizes = ns.map((n) => box(n)),
      count = ns.length,
      spread = new Set(ns.map((n) => `${n.x},${n.y}`)).size === count,
      p = ns.map((n, i) =>
        spread
          ? { x: n.x + sizes[i].w / 2, y: n.y + sizes[i].h / 2 }
          : {
              x: Math.cos((2 * Math.PI * i) / count) * 60 * Math.sqrt(count),
              y: Math.sin((2 * Math.PI * i) / count) * 60 * Math.sqrt(count),
            },
      ),
      index = new Map(ns.map((n, i) => [n.id, i])),
      links = graph.edges
        .filter((e) => e.from !== e.to)
        .map((e) => [index.get(e.from)!, index.get(e.to)!]),
      k = 170;
    let temperature = 40 * Math.sqrt(count);
    for (let step = 0; step < 220; step++) {
      const d = p.map(() => ({ x: 0, y: 0 }));
      for (let i = 0; i < count; i++)
        for (let j = i + 1; j < count; j++) {
          let dx = p[i].x - p[j].x,
            dy = p[i].y - p[j].y;
          if (dx === 0 && dy === 0) {
            dx = ((i * 7919 + j) % 13) - 6 || 1;
            dy = ((i + j * 104729) % 11) - 5 || 1;
          }
          const dist = Math.max(1, Math.hypot(dx, dy)),
            force = (k * k) / dist;
          d[i].x += (dx / dist) * force;
          d[i].y += (dy / dist) * force;
          d[j].x -= (dx / dist) * force;
          d[j].y -= (dy / dist) * force;
        }
      for (const [a, b] of links) {
        const dx = p[a].x - p[b].x,
          dy = p[a].y - p[b].y,
          dist = Math.max(1, Math.hypot(dx, dy)),
          force = (dist * dist) / k;
        d[a].x -= (dx / dist) * force;
        d[a].y -= (dy / dist) * force;
        d[b].x += (dx / dist) * force;
        d[b].y += (dy / dist) * force;
      }
      // Gravity keeps disconnected nodes and components from drifting apart.
      const cx = p.reduce((sum, q) => sum + q.x, 0) / count,
        cy = p.reduce((sum, q) => sum + q.y, 0) / count;
      for (let i = 0; i < count; i++) {
        d[i].x -= (p[i].x - cx) * 0.5;
        d[i].y -= (p[i].y - cy) * 0.5;
      }
      for (let i = 0; i < count; i++) {
        const length = Math.max(1, Math.hypot(d[i].x, d[i].y)),
          move = Math.min(length, temperature);
        p[i].x += (d[i].x / length) * move;
        p[i].y += (d[i].y / length) * move;
      }
      temperature = Math.max(1, temperature * 0.975);
    }
    for (let pass = 0; pass < 60; pass++) {
      let moved = false;
      for (let i = 0; i < count; i++)
        for (let j = i + 1; j < count; j++) {
          const needX = (sizes[i].w + sizes[j].w) / 2 + 40,
            needY = (sizes[i].h + sizes[j].h) / 2 + 24,
            ox = needX - Math.abs(p[i].x - p[j].x),
            oy = needY - Math.abs(p[i].y - p[j].y);
          if (ox <= 0 || oy <= 0) continue;
          moved = true;
          if (ox / needX < oy / needY) {
            const shift = (p[i].x <= p[j].x ? -ox : ox) / 2;
            p[i].x += shift;
            p[j].x -= shift;
          } else {
            const shift = (p[i].y <= p[j].y ? -oy : oy) / 2;
            p[i].y += shift;
            p[j].y -= shift;
          }
        }
      if (!moved) break;
    }
    return new Map(
      ns.map((n, i) => [
        n.id,
        { x: p[i].x - sizes[i].w / 2, y: p[i].y - sizes[i].h / 2 },
      ]),
    );
  }
  function arrange(kind: "force" | "tree" | "radial") {
    if (!current || !editable || !current.graph.nodes.length) return;
    remember();
    let positions: Map<string, Point>;
    if (kind === "radial") {
      const levels = new Map<number, Node[]>();
      for (const n of current.graph.nodes) {
        const level = depth(n.id);
        levels.set(level, [...(levels.get(level) ?? []), n]);
      }
      positions = new Map();
      for (const [level, nodes] of levels) nodes.forEach((n, i) => {
        const angle = 2 * Math.PI * i / nodes.length;
        const radius = level === 0 && nodes.length === 1 ? 0 : (level + 1) * Math.max(220, nodes.length * 42);
        const size = box(n);
        positions.set(n.id, {x: Math.cos(angle) * radius - size.w / 2, y: Math.sin(angle) * radius - size.h / 2});
      });
    } else positions = kind === "tree" ? treeLayout(current.graph) : forceLayout(current.graph);
    for (const n of current.graph.nodes) {
      const point = positions.get(n.id)!;
      n.x = clampPosition(point.x);
      n.y = clampPosition(point.y);
    }
    frame();
  }
  function degreeOf(id: string) {
    return (
      current?.graph.edges.filter((e) => e.from === id || e.to === id).length ??
      0
    );
  }
  const mapGraph = $derived.by(() => {
    if (!current) return { nodes: [] as GraphNode[], links: [] as GraphLink[] };
    const degree = new Map<string, number>();
    for (const e of current.graph.edges) {
      degree.set(e.from, (degree.get(e.from) ?? 0) + 1);
      if (e.to !== e.from) degree.set(e.to, (degree.get(e.to) ?? 0) + 1);
    }
    return {
      nodes: current.graph.nodes.map((n): GraphNode => ({
        id: n.id,
        label: name(n),
        group: n.kind,
        kind: n.kind,
        size: Math.min(10, 1 + (degree.get(n.id) ?? 0)),
        detail: `${n.kind}${n.source ? " / linked source" : ""} / ${degree.get(n.id) ?? 0} connections`,
      })),
      links: current.graph.edges.map((e): GraphLink => ({
        source: e.from,
        target: e.to,
        label: e.label,
        directed: true,
      })),
    };
  });
  function mapTitle(id: number) {
    const m =
      network?.maps.find((m) => m.id === id) ?? maps.find((m) => m.id === id);
    return m ? `${m.title} #${m.id}` : `Map #${id}`;
  }
  const netGraph = $derived.by(() => {
    if (!network) return { nodes: [] as GraphNode[], links: [] as GraphLink[] };
    return {
      nodes: network.nodes.map((n): GraphNode => ({
        id: n.id,
        label: n.label,
        kind: n.kind,
        group:
          colorBy === "kind"
            ? n.kind
            : colorBy === "cluster"
              ? `cluster ${n.cluster + 1}`
              : n.maps.length > 1
                ? "shared across maps"
                : mapTitle(n.maps[0]),
        size: Math.min(10, 1 + n.degree),
        detail: `${n.kind} / in ${n.maps.length} map${n.maps.length === 1 ? "" : "s"} / ${n.degree} connections${n.live?.missing ? " / source deleted" : ""}`,
      })),
      links: network.edges.map((e): GraphLink => ({
        source: e.from,
        target: e.to,
        label: e.weight > 1 ? `${e.label} ×${e.weight}` : e.label,
        directed: true,
        weight: e.weight,
      })),
    };
  });
  let networkRequest = 0;
  async function loadNetwork() {
    const ticket = ++networkRequest,
      params = new URLSearchParams();
    if (netMode) params.set("mode", netMode);
    const chosen = netMaps.filter((id) => netChoices.some((m) => m.id === id));
    if (chosen.length) params.set("map_ids", chosen.join(","));
    if (mergeLabels) params.set("merge_labels", "true");
    netBusy = true;
    netError = "";
    try {
      const result = await api<Network>(`/schema/network?${params}`);
      if (ticket !== networkRequest) return;
      network = result;
      if (!result.nodes.some((n) => n.id === netSelected)) netSelected = "";
    } catch (e) {
      if (ticket === networkRequest)
        netError = e instanceof Error ? e.message : String(e);
    } finally {
      if (ticket === networkRequest) netBusy = false;
    }
  }
  $effect(() => {
    if (view !== "network") return;
    void [netMode, netMaps.length, mergeLabels];
    void loadNetwork();
  });
  function neighbors(id: string) {
    const edges = network?.edges ?? [];
    return {
      out: edges.filter((e) => e.from === id),
      in: edges.filter((e) => e.to === id && e.from !== id),
    };
  }
  function netLabel(id: string) {
    return network?.nodes.find((n) => n.id === id)?.label ?? id;
  }
  /** Leave the read-only graph for the editor with the node selected, focused and in view. */
  async function editInMap(mapId: number, nodeId: string) {
    if (current?.id !== mapId) {
      await open(mapId);
      if (current?.id !== mapId) return;
    }
    view = "edit";
    const n = current!.graph.nodes.find((n) => n.id === nodeId);
    if (!n) {
      error = "That node is no longer in the saved map. Reload the network.";
      return;
    }
    choose(n);
    await tick();
    if (!pane.active) return;
    const rect = stage?.getBoundingClientRect(),
      v = current!.viewport;
    if (rect) {
      const size = box(n),
        sx = n.x * v.zoom + v.x,
        sy = n.y * v.zoom + v.y;
      if (
        sx < 0 ||
        sy < 0 ||
        sx + size.w * v.zoom > rect.width ||
        sy + size.h * v.zoom > rect.height
      ) {
        v.x = Math.max(
          -1e7,
          Math.min(1e7, rect.width / 2 - (n.x + size.w / 2) * v.zoom),
        );
        v.y = Math.max(
          -1e7,
          Math.min(1e7, rect.height / 2 - (n.y + size.h / 2) * v.zoom),
        );
      }
    }
    stage
      ?.querySelector<SVGGElement>(`[data-node="${CSS.escape(n.id)}"]`)
      ?.focus();
  }
  function pick(e: PointerEvent) {
    const edge = (e.target as Element | null)?.getAttribute?.("data-edge");
    if (edge && !e.shiftKey) {
      selectedEdge = edge;
      selected = "";
      if (doubleTap(`edge:${edge}`, e)) void relabel(edge);
      return;
    }
    start(e);
  }
  function download() {
    if (!current) return;
    const graph = clone(current.graph);
    for (const n of graph.nodes) {
      n.label = name(n).slice(0, 200);
      n.url = (n.source ? current.live[n.id]?.url : undefined) ?? n.url;
      n.source = null;
    }
    const data = {
      format: "kitty-schema",
      version: 1,
      title: current.title,
      mode: current.mode,
      graph,
      viewport: current.viewport,
    };
    const blob = new Blob([JSON.stringify(data)], { type: "application/json" }),
      url = URL.createObjectURL(blob),
      a = document.createElement("a");
    a.href = url;
    a.download = `schema-${current.id}.json`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  async function importFile(e: Event) {
    const input = e.currentTarget as HTMLInputElement,
      file = input.files?.[0];
    input.value = "";
    if (
      !file ||
      busy ||
      (dirty && !confirm("Discard unsaved edits to open the imported map?"))
    )
      return;
    if (file.size > 1_000_000) {
      error = "Import must be at most 1 MB.";
      return;
    }
    busy = true;
    error = "";
    try {
      const document = JSON.parse(await file.text());
      accepted(
        await api<MapItem>("/schema/import", {
          title: document.title,
          mode: document.mode,
          document,
        }),
      );
      await refresh();
    } catch (e) {
      failure(e);
    } finally {
      busy = false;
    }
  }
  async function findJobs(offset = 0) {
    if (busy) return;
    busy = true;
    error = "";
    try {
      const result = await api<{ jobs: Job[]; more: boolean }>(
        `/schema/jobs?search=${encodeURIComponent(jobSearch)}&offset=${offset}`,
      );
      jobs = result.jobs;
      moreJobs = result.more;
      jobOffset = offset;
    } catch (e) {
      failure(e);
    } finally {
      busy = false;
    }
  }
  async function importJobs() {
    if (!current || !editable || dirty || !jobIds.length) return;
    busy = true;
    error = "";
    try {
      accepted(
        await api<MapItem>("/schema/import-jobs", {
          id: current.id,
          revision: current.revision,
          job_ids: jobIds,
        }),
      );
      jobIds = [];
      drawer = "";
      await refresh();
    } catch (e) {
      failure(e);
      blocked = error.includes("changed in another window");
    } finally {
      busy = false;
    }
  }
  async function loadAutoLinks() {
    if (!current) return;
    const id = current.id;
    autoRequestedFor = id;
    autoBusy = true;
    autoError = "";
    try {
      const result = await api<AutoLinks>(`/schema/backlinks?map_id=${id}`);
      if (current?.id !== id) return;
      autoLinks = result;
      autoPicked = autoPicked.filter((n) =>
        result.suggestions.some(
          (s) => s.node_id === n && s.references.some((r) => !r.linked),
        ),
      );
    } catch (e) {
      autoError = e instanceof Error ? e.message : String(e);
    } finally {
      autoBusy = false;
    }
  }
  async function applyAutoLinks(nodeIds: string[]) {
    if (!current || !editable || dirty || !nodeIds.length) return;
    busy = true;
    error = "";
    try {
      accepted(
        await api<MapItem>("/schema/backlinks/apply", {
          map_id: current.id,
          revision: current.revision,
          node_ids: nodeIds,
        }),
      );
      autoPicked = [];
      await refresh();
      await loadAutoLinks();
    } catch (e) {
      failure(e);
      blocked = error.includes("changed in another window");
    } finally {
      busy = false;
    }
  }
  function focusNode(id: string) {
    const n = current?.graph.nodes.find((n) => n.id === id);
    if (!n) return;
    view = "edit";
    choose(n);
  }
  function leaveFor(e: MouseEvent) {
    if (dirty && !confirm("Leave with unsaved map edits? Save or export first."))
      e.preventDefault();
  }
  $effect(() => {
    const id = current?.id ?? 0;
    if (drawer === "links" && id && autoRequestedFor !== id) void loadAutoLinks();
  });
  $effect(() => {
    if (!current || !dirty) return;
    const value = {revision: current.revision, snapshot: snapshot()};
    try { localStorage.setItem(draftKey(current.id), JSON.stringify(value)); }
    catch { error = "Local draft backup is unavailable; keep this tab open until saved."; }
  });
  /** Embedded in a Notes page: open the page's own map, if it has one. */
  async function loadPageMap() {
    const id = pageId;
    pageChecked = false;
    current = null;
    dirty = false;
    blocked = false;
    history = [];
    future = [];
    if (!id) { pageChecked = true; return; }
    try {
      const { map_id } = await api<{ map_id: number | null }>(`/notes/pages/${id}/map`);
      if (id !== pageId) return;
      if (map_id) await open(map_id, true);
    } catch (e) { failure(e); }
    finally { if (id === pageId) pageChecked = true; }
  }
  async function createPageMap() {
    if (!pageId || busy) return;
    busy = true;
    error = "";
    let id = 0;
    try { ({ map_id: id } = await api<{ map_id: number }>(`/notes/pages/${pageId}/map`, {})); }
    catch (e) { failure(e); }
    finally { busy = false; }
    if (!id) return;
    await open(id, true);
    const root = current?.graph.nodes[0];
    if (root) { choose(root); focusOn(root.id); }
  }
  /** Ctrl K hands the Mind maps view a map to open (or asks for a new one) through the session. */
  function handoff() {
    if (embedded || section !== "schema") return false;
    let wanted = 0, fresh = false;
    try {
      wanted = Number(sessionStorage.getItem("kitty.schema.open-map"));
      fresh = sessionStorage.getItem("kitty.schema.new-map") === "1";
      sessionStorage.removeItem("kitty.schema.open-map");
      sessionStorage.removeItem("kitty.schema.new-map");
    } catch { /* Nothing handed over. */ }
    if (wanted && maps.some(m => m.id === wanted)) void open(wanted);
    else if (fresh) { view = "edit"; creating = true; }
    return Boolean(wanted || fresh);
  }
  onMount(() => {
    const fonts = getComputedStyle(document.documentElement);
    uiFont = fonts.getPropertyValue("--font-ui").trim() || uiFont;
    void document.fonts?.ready.then(() => { pillWidths.clear(); fontsReady += 1; });
    void refresh()
      .then(() => {
        if (embedded || handoff()) return;
        let lastMap = 0;
        try { lastMap = Number(localStorage.getItem(`kitty.schema.last-map.${section}`)); } catch { /* Use the first map if storage is unavailable. */ }
        const first = maps.find(m => m.id === lastMap) ?? maps.find(m => m.mode === "study") ?? maps[0];
        if (first) return open(first.id);
      })
      .catch(failure);
    const guard = (e: BeforeUnloadEvent) => {
      if (dirty) {
        e.preventDefault();
        e.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", guard);
    return () => {
      window.removeEventListener("beforeunload", guard);
      clearTimeout(autosaveTimer);
      if (dirty && !blocked) void save();
      (onstatus ?? pane.setStatus)(null);
      if (!embedded) pane.clearTrail(section);
    };
  });
  $effect(() => {
    if (!embedded) return;
    void pageId;
    untrack(() => void loadPageMap());
  });
  // Autosave: once the map is idle (no drag, stroke or label being typed), shortly after the last edit.
  $effect(() => {
    void edits;
    if (!current || !dirty || blocked || saveFailed || busy || saving || dragging || editing || labelling || inking || lassoLoop) return;
    autosaveTimer = setTimeout(() => void save(), 600);
    return () => clearTimeout(autosaveTimer);
  });
  /** Q3: the save state is the tab's dot (or the host's, when it merges its own). */
  const saveState = $derived<SaveStatus | null>(
    !current ? null
      : blocked || saveFailed ? { state: "failed", detail: error || "Couldn't save the map" }
      : dirty || saving ? { state: "unsaved", detail: "Unsaved changes · Ctrl S saves" }
      : { state: "saved" },
  );
  $effect(() => {
    const state = saveState;
    untrack(() => (onstatus ?? pane.setStatus)(state));
  });
  let seenRevision: number | undefined;
  $effect(() => {
    if (seenRevision === undefined) seenRevision = revision;
    else if (revision !== seenRevision) {
      seenRevision = revision;
      void refresh().catch(failure);
      if (current && !dirty && !busy && !saving) void open(current.id, true);
      if (view === "network") void loadNetwork();
    }
  });
  // Chrome (Q18 D): what the top bar, the pill, the topic bar and their menus offer.
  const otherMaps = $derived(maps.filter(m => m.id !== current?.id));
  const subjectName = $derived.by(() => {
    const n = current?.graph.nodes.find(item => item.source?.kind === "subject");
    return n ? name(n) : "";
  });
  const TEMPLATES = ["Blank", "Lecture", "Chapter", "Exam review"].map(value => ({value, label: value}));
  const MODES = [{value: "study", label: "Study"}, {value: "general", label: "General"}, {value: "jobs", label: "Jobs"}];
  const KINDS: Record<string, string> = {topic: "Topic", class: "Study class", idea: "Idea", skill: "Skill or goal", company: "Company", job: "Job note", task: "Task"};
  const LAYOUTS = [["tree", "Tree"], ["radial", "Radial"], ["force", "Force"]] as const;
  let switching = $state(false), layoutOpen = $state(false), styleOpen = $state(false), draftTitle = $state("");
  let ctxWidth = $state(0);
  const draggedRoot = $derived.by(() => {
    const n = dragging ? current?.graph.nodes.find(item => item.id === dragging) : undefined;
    return n && isRoot(n) ? n : undefined;
  });
  /** Starts "Connect to…" from the selected topic: the next topic clicked (or Enter on) is the other end. */
  function startConnect() {
    if (!node || !editable) return;
    inkMode = false;
    linking = node.id;
    focusOn(node.id);
  }
  const mapActions = $derived<OverflowItem[]>(current ? [
    {label: dirty ? "Export local edits" : "Export JSON", icon: "download", onselect: download},
    {label: "Import map JSON…", onselect: () => importInput?.click(), disabled: busy},
    {label: "Reload saved map", icon: "refresh", onselect: () => current && void open(current.id, true), disabled: busy},
    {separator: true},
    {label: autoNew ? `Linked mentions · ${autoNew} new` : "Linked mentions…", onselect: () => drawer = "links"},
    {label: "Ask AI about this map…", icon: "ai", onselect: () => drawer = "ai"},
    ...(current.mode === "jobs" && canImportJobs ? [{label: "Import tracked jobs…", icon: "jobs", onselect: () => { drawer = "jobs"; void findJobs(); }}] : []),
    {separator: true},
    {label: "Delete map…", danger: true, onselect: removeMap, disabled: !editable},
  ] : [{label: "Import map JSON…", onselect: () => importInput?.click(), disabled: busy}]);
  const topicActions = $derived<OverflowItem[]>(node && current ? [
    {label: "Connect to…", icon: "tool-connector", onselect: startConnect, disabled: !editable},
    {label: node.collapsed ? "Expand branch" : "Collapse branch", kbd: "Space", onselect: () => node && collapse(node)},
    {label: "Reset position", onselect: resetPosition, disabled: !editable || !node.free},
    {separator: true},
    {label: "Split branch into map", onselect: () => node && void transferBranch(node.id), disabled: !editable},
    ...(isRoot(node) ? otherMaps.slice(0, 8).map((m): OverflowItem => ({label: `Merge into “${m.title}”`, onselect: () => node && void transferBranch(node.id, m.id), disabled: !editable})) : []),
    {separator: true},
    {label: "Make flashcards", icon: "flashcards", onselect: () => void studyBranch("flashcards")},
    {label: "Quiz this branch", icon: "questions", onselect: () => void studyBranch("quiz")},
    {label: "Start session", icon: "study", onselect: () => void startBranchSession(), disabled: !editable},
    ...(node.source?.kind === "note_page" || node.map_id ? [{label: node.map_id ? "Open linked map" : "Open note page", icon: "external", onselect: () => node && openCard(node)}] : []),
    {separator: true},
    {label: "Details…", onselect: () => details = true},
  ] : []);
</script>

<svelte:window onkeydowncapture={dragKey} onpointerdowncapture={(e) => { if (!(e.target as Element).closest?.("svg.stage, .map-outline")) lastTap.key = ""; }} />
<section class="schema" class:surface={!embedded} class:embedded aria-label="Mental schema maps" aria-busy={busy} data-save-state={saveState?.state ?? "none"} bind:this={container} use:fitHeight={420}>
  {#snippet newMapChips()}
    <Select label="Starting template" kind="segments" bind:value={template} options={TEMPLATES} />
    <Select label="Map category" kind="segments" bind:value={mode} options={MODES} />
  {/snippet}
  {#snippet newMap(primary: boolean)}
    <QuickAdd label="New map" placeholder="What are you learning?" bind:open={creating} bind:value={draftTitle} placement="bottom-start"
      chips={newMapChips} onsave={startMap} onmore={(text) => { draftTitle = text; creatingFull = true; }}>
      {#snippet trigger(props)}
        {#if primary}<button type="button" class="pill primary page-action" {...props}><Icon name="plus" size={14} />New map</button>
        {:else}<button type="button" class="icon-button" aria-label="New map" title="New map" disabled={busy} {...props}><Icon name="plus" size={15} /></button>{/if}
      {/snippet}
    </QuickAdd>
  {/snippet}
  {#snippet swatches(size: "sm" | "md")}
    <span class="swatches {size}" role="group" aria-label="Colours">
      {#each PALETTE as slot, index (slot)}<button class="swatch" style:--dot={`var(--${slot})`} aria-label={`Colour ${index + 1}`} aria-pressed={!!node && node.color === slot}
        title={`Colour ${index + 1}`} disabled={!editable || !picked.size} onclick={() => paint(slot)}></button>{/each}
      <button class="swatch auto" aria-label="Automatic colour" aria-pressed={!!node && !node.color} title="Automatic branch colour" disabled={!editable || !picked.size} onclick={() => paint(undefined)}></button>
    </span>
  {/snippet}

  {#if !embedded}
    <header class="top">
      <Popover bind:open={switching} label="Maps" placement="bottom-start" class="map-switcher">
        {#snippet trigger(props)}
          <button type="button" class="switcher" aria-label="Switch map" title="Switch map" disabled={busy} {...props}><Icon name="schema" size={15} /><span>Maps</span><span class="chev" aria-hidden="true">▾</span></button>
        {/snippet}
        <div class="switch-list">
          {#each maps as m (m.id)}
            <button type="button" class="switch-item" aria-current={m.id === current?.id && view !== "network" ? "true" : undefined}
              onclick={() => { switching = false; view = "edit"; if (m.id !== current?.id) void open(m.id); }}>{m.title}<small>{m.mode}</small></button>
          {:else}<p class="hint">No maps yet.</p>{/each}
        </div>
        {#if maps.length > 1}
          <hr class="rule" />
          <button type="button" class="switch-item" aria-current={view === "network" ? "true" : undefined} onclick={() => { switching = false; view = "network"; }}>All maps, merged</button>
        {/if}
      </Popover>
      {#if current}{@render newMap(false)}{/if}
      {#if current && view !== "network"}
        <span class="slash" aria-hidden="true">/</span>
        <input class="title" aria-label="Map title" value={current.title} maxlength="200" disabled={!editable}
          oninput={(e) => { remember(); current!.title = e.currentTarget.value; }} />
        {#if subjectName}<span class="subject" title="Linked study class"><i class="dot" aria-hidden="true"></i>{subjectName}</span>{/if}
      {/if}
      <span class="grow"></span>
      {#if current && view === "edit"}
        <Popover bind:open={layoutOpen} label="Layout" placement="bottom-end" role="menu" class="layout-menu">
          {#snippet trigger(props)}
            <button type="button" class="bar-btn" disabled={!editable} title="Lay the map out again" {...props}><Icon name="tool-connector" size={14} />Layout<span class="chev" aria-hidden="true">▾</span></button>
          {/snippet}
          {#each LAYOUTS as [id, text] (id)}<button type="button" role="menuitem" class="menu-item" onclick={() => { layoutOpen = false; arrange(id); }}>{text}</button>{/each}
        </Popover>
      {/if}
      {#if current && view !== "network"}
        <span class="vr" aria-hidden="true"></span>
        <Select label="View" kind="segments" value={view} options={[{value: "edit", label: "Map"}, {value: "map3d", label: "Graph"}]} onchange={(value) => view = value as View3D} />
      {/if}
      <OverflowMenu label="More map actions" items={mapActions} />
    </header>
  {/if}

  {#if error}
    <div role="alert" class="map-error">
      <span>{error}</span>
      {#if blocked && current}
        <button type="button" class="link" onclick={download}>Export edits</button>
        <button type="button" class="link" onclick={() => current && void open(current.id, true)}>Reload saved</button>
      {:else if saveFailed}<button type="button" class="link" onclick={() => { saveFailed = false; error = ""; void save(); }}>Retry</button>{/if}
      <button type="button" class="icon-button" aria-label="Dismiss" onclick={() => error = ""}><Icon name="close" size={13} /></button>
    </div>
  {/if}
  <input type="file" accept="application/json,.json" hidden bind:this={importInput} onchange={importFile} />

  {#if view === "network" && !embedded}
    <div class="views network-view">
      <aside class="side">
        <span class="eyebrow">Filters</span>
        <div class="field"><span>Category</span><Select label="Category" kind="list" bind:value={netMode} disabled={netBusy} options={[{value: "", label: "All categories"}, ...MODES]} /></div>
        <fieldset class="map-picks">
          <legend>Maps</legend>
          <button type="button" class="link" onclick={() => netMaps = []} disabled={!netMaps.length}>Use all maps</button>
          {#each netChoices as m (m.id)}<label class="check"><input type="checkbox" checked={netMaps.includes(m.id)}
              onchange={(e) => { netMaps = e.currentTarget.checked ? [...netMaps, m.id] : netMaps.filter((id) => id !== m.id); }} />{m.title}</label>
          {:else}<p class="hint">No maps in this category yet.</p>{/each}
        </fieldset>
        <label class="check" title="Linked classes, jobs and companies always unify across maps; this also joins unlinked topics with the same kind and label."><input type="checkbox" bind:checked={mergeLabels} />Merge same-name topics</label>
        <div class="field"><span>Colour by</span><Select label="Colour by" kind="segments" value={colorBy} onchange={(value) => colorBy = value as typeof colorBy}
          options={[{value: "cluster", label: "Cluster"}, {value: "kind", label: "Kind"}, {value: "map", label: "Map"}]} /></div>
        <Button size="sm" onclick={() => loadNetwork()} disabled={netBusy}>Refresh</Button>
        {#if network}<p class="hint">{network.maps.length} maps · {network.nodes.length} topics · {network.clusters.length} clusters{dirty ? " · open map's unsaved edits not included" : ""}</p>{/if}
        {#if network?.clusters.length}<ol class="clusters">
            {#each network.clusters.slice(0, 12) as c (c.id)}<li>Cluster {c.id + 1}: {c.size} topic{c.size === 1 ? "" : "s"}, hub “{c.label}”</li>{/each}
          </ol>{/if}
      </aside>
      <main class="graph-pane">
        {#if netError}<div role="alert" class="map-error">{netError}</div>{/if}
        {#if pane.active}
          <SchemaNetwork title="Merged schema network" nodes={netGraph.nodes} links={netGraph.links} bind:layout={netLayout}
            selected={netSelected || null} onSelect={(id) => { netSelected = id; }} />
        {/if}
      </main>
      <aside class="side">
        {#if netNode}
          {@const links = neighbors(netNode.id)}
          <h2>{netNode.label}</h2>
          <Badge label={netNode.kind} />
          <p class="hint">Cluster {netNode.cluster + 1} · {netNode.degree} connection{netNode.degree === 1 ? "" : "s"}</p>
          {#if netNode.source}
            {#if netNode.live?.missing}<p class="hint warn">Source deleted; unlink it in each map below.</p>{/if}
            {#if netNode.kind === "job" && !netNode.live?.missing}<p class="hint">{netNode.live?.company ?? "Unavailable"} · {netNode.live?.status ?? "No application"} · {netNode.live?.queue_state ?? "Unavailable"}{netNode.live?.closed_at ? " · closed" : ""}</p>{/if}
          {/if}
          {#if safeUrl(netNode.url)}<a href={safeUrl(netNode.url)} target="_blank" rel="noopener noreferrer">Open source ↗</a>{/if}
          <span class="eyebrow">Appears in</span>
          <ul class="members">
            {#each netNode.members as m (`${m.map_id}:${m.node_id}`)}<li>
                <strong>{mapTitle(m.map_id)}</strong> <small>as “{m.label}”</small>
                {#if m.notes}<p class="notes">{m.notes}</p>{/if}
                <Button size="sm" onclick={() => editInMap(m.map_id, m.node_id)} disabled={busy}>Edit in map</Button>
              </li>{/each}
          </ul>
          <span class="eyebrow">Connections</span>
          <ul class="members">
            {#each links.out as e (e.id)}<li>→ {e.label || "(unlabeled)"} → {netLabel(e.to)} <small>×{e.weight} in {e.maps.map(mapTitle).join(", ")}</small></li>{/each}
            {#each links.in as e (e.id)}<li>← {e.label || "(unlabeled)"} ← {netLabel(e.from)} <small>×{e.weight} in {e.maps.map(mapTitle).join(", ")}</small></li>{/each}
            {#if !links.out.length && !links.in.length}<li class="hint">No connections.</li>{/if}
          </ul>
        {:else}<p class="hint">Select a topic to see where it appears.</p>{/if}
      </aside>
    </div>
  {:else if current && view === "map3d"}
    <div class="views graph-view">
      <main class="graph-pane">
        {#if pane.active}
          <SchemaNetwork title={`Map / ${current.title}`} nodes={mapGraph.nodes} links={mapGraph.links} bind:layout={mapLayout}
            selected={selected || null} onSelect={(id) => { const n = current?.graph.nodes.find((n) => n.id === id); if (n) choose(n); }} />
        {/if}
      </main>
      <aside class="side">
        {#if node}
          <h2>{name(node)}</h2>
          <Badge label={node.kind} />
          <p class="hint">{degreeOf(node.id)} connections in this map</p>
          {#if node.source && current.live[node.id]?.missing}<p class="hint warn">Source deleted; unlink it in the map.</p>{/if}
          {#if node.notes}<p class="notes">{node.notes}</p>{/if}
          {#if safeUrl((node.source ? current.live[node.id]?.url : undefined) ?? node.url)}<a href={safeUrl((node.source ? current.live[node.id]?.url : undefined) ?? node.url)} target="_blank" rel="noopener noreferrer">Open source ↗</a>{/if}
          <span class="eyebrow">Connections</span>
          <ul class="members">
            {#each current.graph.edges.filter((e) => e.from === node.id || e.to === node.id) as e (e.id)}
              {@const other = current.graph.nodes.find((n) => n.id === (e.from === node.id ? e.to : e.from))!}
              <li>{e.from === node.id ? `→ ${e.label || "(unlabeled)"} → ${e.to === node.id ? "itself" : name(other)}` : `← ${e.label || "(unlabeled)"} ← ${name(other)}`}</li>
            {:else}<li class="hint">No connections.</li>{/each}
          </ul>
          <Button size="sm" onclick={() => editInMap(current!.id, node.id)}>Edit in map</Button>
        {:else}<p class="hint">Select a topic in the graph.</p>{/if}
      </aside>
    </div>
  {:else if current}
    <div class="canvas" bind:clientWidth={stageWidth}>
      {#if dragging}<p class="drag-hint" role="status">{detaching ? "Detached · drop anywhere to leave it floating · D to reattach" : placing ? "Placing freely · release to leave it here" : dropTarget ? `Drop on “${name(current.graph.nodes.find(n => n.id === dropTarget)!)}” to move it there` : "Drop on a topic to move it there · Alt places it freely · D detaches the branch"}</p>
      {:else if linking}<p class="drag-hint" role="status">Click a topic to connect “{name(current.graph.nodes.find(n => n.id === linking)!)}” to it · Esc cancels</p>
      {:else if inkMode}<p class="drag-hint" role="status">Ink sticks to the nearest topic · flip the pen to erase</p>{/if}
      {#if draggedRoot && otherMaps.length}
        <div class="merge-targets" role="group" aria-label="Merge map drop targets">
          <span>Drop into</span>
          {#each otherMaps.slice(0, 8) as m (m.id)}<button type="button" data-merge-map={m.id}>{m.title}</button>{/each}
        </div>
      {/if}
      {#if recall}
        <div class="recall-bar" role="toolbar" aria-label="Quiz">
          <span>Quiz · recall the hidden topics</span>
          <button type="button" class="cb" disabled={!node} onclick={() => revealed = [...revealed, selected]}>Reveal selected</button>
          <button type="button" class="cb" onclick={() => revealed = current!.graph.nodes.map(n => n.id)}>Reveal all</button>
          <button type="button" class="cb" onclick={() => { recall = false; revealed = []; }}>End quiz</button>
        </div>
      {/if}
      {#if outline}
        <div class="map-outline" role="tree" aria-label="Mind map outline">
          {#each visibleNodes as n (n.id)}
            <div style={`padding-left: ${depth(n.id) * 16}px`} role="treeitem" aria-level={depth(n.id) + 1} aria-selected={selected === n.id}>
              <button type="button" class="fold-toggle" onclick={() => collapse(n)} title="Collapse branch">{n.collapsed ? "▸" : "▾"}</button>
              <button type="button" class="outline-item" class:active={selected === n.id} oncontextmenu={event => deleteAtPointer(event, n)} onkeydown={e => nodeKey(e, n)} onclick={() => choose(n)} onpointerup={e => { if (doubleTap(`outline:${n.id}`, e)) openCard(n); }}>{hiddenLabel(n) ? "?" : name(n)}</button>
            </div>
          {/each}
        </div>
      {/if}
      {#if !current.graph.nodes.length}<p class="canvas-empty">Press + Topic or double-click to add a first topic.</p>{/if}
      {#if node && !dragging && !editing && !linking && !labelling && editable && visibleNodes.includes(node)}
        {@const v = current.viewport}
        {@const top = node.y * v.zoom + v.y + 8}
        <!-- Q18: a small bar rides on the selected topic (under it near the stage top). -->
        <div class="ctx-bar" class:below={top < 56} role="toolbar" aria-label="Topic" bind:offsetWidth={ctxWidth}
          style:left={`${Math.max(8, Math.min(node.x * v.zoom + v.x, stageWidth - ctxWidth - 8))}px`}
          style:top={`${top < 56 ? top + box(node).h * v.zoom + 8 : top - 8}px`}>
          {#if picked.size > 1}<span class="count">{picked.size} selected</span><span class="vr" aria-hidden="true"></span>{/if}
          <Popover bind:open={styleOpen} label="Topic style" placement="bottom-start" class="topic-style">
            {#snippet trigger(props)}
              <button type="button" class="cb colour" aria-label="Topic colour" title="Colour and style" {...props}><span class="swatch on" style:--dot={nodeColor(node)}></span><span class="chev" aria-hidden="true">▾</span></button>
            {/snippet}
            {@render swatches("md")}
            <div class="style-row">
              <button type="button" class="cb" aria-pressed={paintBranch} title="Colour the topic's whole branch" onclick={() => paintBranch = !paintBranch}>Whole branch</button>
              <button type="button" class="cb" aria-keyshortcuts="Alt+C" title="Copy style (Alt+C)" onclick={copyStyle}>Copy style</button>
              <button type="button" class="cb" aria-keyshortcuts="Alt+V" title="Paste style (Alt+V)" disabled={!copiedStyle} onclick={pasteStyle}>Paste style</button>
            </div>
          </Popover>
          {#if picked.size === 1}
            <span class="vr" aria-hidden="true"></span>
            <button type="button" class="cb" title="Rename (Ctrl+Enter, or just type)" onclick={() => node && editInline(node)}>Rename</button>
            <button type="button" class="cb" title="Break the branch off as a floating piece (D while dragging)" disabled={isRoot(node)} onclick={detachSelected}>Detach</button>
            <OverflowMenu label="More topic actions" placement="bottom-start" items={topicActions} />
          {/if}
          <span class="vr" aria-hidden="true"></span>
          <button type="button" class="cb icon" aria-label={picked.size > 1 ? `Delete ${picked.size} topics` : "Delete topic"} aria-keyshortcuts="Delete" title="Delete (Del)" onclick={deleteNode}><Icon name="close" size={13} /></button>
        </div>
      {/if}
      <svg
        bind:this={stage}
        class="stage"
        class:ink-mode={inkMode}
        aria-label="Mind map. Drag topics to reparent, Alt-drag to place freely, drag empty space to pan, double-tap a topic to edit it. Tab adds a child, Enter a sibling, arrows move, Shift+arrows select several, Ctrl+arrows reorder, Space folds. With a pen: Ink draws, the barrel button lassoes topics, the eraser end erases ink."
        role="group"
        onpointerdown={(e) => down(e)}
        onpointerover={notePen}
        onpointerleave={notePen}
        onpointermove={move}
        onpointerup={(e) => end(e)}
        onpointercancel={(e) => end(e, true)}
        onlostpointercapture={(e) => end(e, true)}
        onwheel={(e) => {
          e.preventDefault();
          zoom(e.deltaY < 0 ? 1.1 : 1 / 1.1, e.clientX, e.clientY);
        }}
      >
        <defs
          ><marker id={`${pane.id}-schema-arrow`} markerWidth="10" markerHeight="8" refX="9" refY="4" orient="auto" markerUnits="strokeWidth"
            ><path d="M0,0 L10,4 L0,8 z" fill="var(--violet)" /></marker
          ><marker id={`${pane.id}-schema-arrow-active`} markerWidth="10" markerHeight="8" refX="9" refY="4" orient="auto" markerUnits="strokeWidth"
            ><path d="M0,0 L10,4 L0,8 z" fill="var(--accent)" /></marker
          ></defs
        >
        <g transform={`translate(${current.viewport.x},${current.viewport.y}) scale(${current.viewport.zoom})`}>
          {#each shownEdges.filter(e => !(e.to === dragging && treeEdges.has(e.id) && (detaching || (dropTarget && dropTarget !== e.from)))) as e (e.id)}
            {@const shape = geometry.get(e.id)}
            {#if shape}<path
                d={shape.d}
                class="edge"
                class:branch={shape.tree}
                class:cross={!shape.tree}
                class:active={selectedEdge === e.id}
                style:--b={nodeColor(current.graph.nodes.find(n => n.id === e.to)!)}
                marker-end={shape.tree ? undefined : selectedEdge === e.id ? `url(#${pane.id}-schema-arrow-active)` : `url(#${pane.id}-schema-arrow)`}
              /><path d={shape.d} class="edge-hit" data-edge={e.id} />{/if}
          {/each}
          {#if dragging && dropTarget}<path class="snap-preview" d={snapPath(dropTarget, dragging)} />{/if}
          <!-- Labels sit under the pills so a label never blocks a topic. -->
          {#each shownEdges as e (e.id)}
            {@const shape = geometry.get(e.id)}
            {#if shape && e.label && labelling !== e.id}<text x={shape.lx} y={shape.ly} class="edge-label" class:active={selectedEdge === e.id} data-edge={e.id} text-anchor="middle"
                >{e.label.length > 32 ? e.label.slice(0, 31) + "…" : e.label}</text
              >{/if}
          {/each}
          {#each visibleNodes as n (n.id)}
            {@const size = box(n)}
            {@const root = isRoot(n)}
            {@const folded = hiddenCount(n)}
            <g
              transform={`translate(${n.x},${n.y})`}
              data-node={n.id}
              id={`${pane.id}-node-${n.id}`}
              role="button"
              tabindex="0"
              aria-label={`${n.kind}: ${hiddenLabel(n) ? "Recall" : name(n)}${folded ? `, ${folded} hidden` : ""}${n.free && !root ? ", placed freely" : ""}. Tab child, Enter sibling, arrows move, Control arrows reorder, Control Enter or type to rename, Space collapse.`}
              class:selected={picked.has(n.id)}
              class:root
              class:drop-target={dropTarget === n.id}
              class:dragging={dragging === n.id}
              class:detaching={detaching && dragging === n.id}
              class:painted={!!n.color}
              class="graph-node"
              style:--b={nodeColor(n)}
              onpointerdown={(e) => down(e, n.id)}
              onkeydown={(e) => nodeKey(e, n)}
              oncontextmenu={(event) => deleteAtPointer(event, n)}
            >
              <rect class="pill" width={size.w} height={size.h} rx={size.h / 2} />
              {#if !root}<circle class="dot" cx="16" cy={size.h / 2} r="4" />{/if}
              <text class="label" x={root ? (size.w - (folded ? 26 : 0)) / 2 : 28} y={size.h / 2 + 4.5} text-anchor={root ? "middle" : "start"}>{pillText(n)}</text>
              {#if folded}<g class="fold" transform={`translate(${size.w - 17},${size.h / 2})`}><circle r="9" /><text y="3.5" text-anchor="middle">{folded > 99 ? "99+" : folded}</text></g>{/if}
              {#if editing === n.id}
                <foreignObject x="0" y="0" width={Math.max(size.w, 180)} height={size.h}>
                  <input id={`${pane.id}-map-inline-label`} class="inline-label" aria-label="Node label" bind:value={inlineLabel} maxlength="200"
                    onpointerdown={e => e.stopPropagation()}
                    oninput={e => updateInline(e.currentTarget.value)}
                    onkeydown={(e) => {
                      e.stopPropagation();
                      if (e.key === "Escape") {e.preventDefault(); finishInline();}
                      else if (e.key === "Tab") {e.preventDefault(); finishInline(false); quickAdd(selected);}
                      else if (e.key === "Enter") {e.preventDefault(); finishInline(false); quickAdd(parents.get(selected) ?? "", selected);}
                    }} onblur={() => {if (editing === n.id) finishInline(false);}} />
                </foreignObject>
              {/if}
            </g>
          {/each}
          {#if labelling}
            {@const e = shownEdges.find(e => e.id === labelling)}
            {@const shape = e && geometry.get(e.id)}
            {#if e && shape}
              <foreignObject x={shape.lx - 80} y={shape.ly - 17} width="160" height="26">
                <input id={`${pane.id}-edge-label`} class="inline-label edge-input" aria-label="Connection label" placeholder="relationship" bind:value={edgeDraft} maxlength="200"
                  onpointerdown={event => event.stopPropagation()}
                  onkeydown={(event) => {
                    event.stopPropagation();
                    if (event.key === "Enter" || event.key === "Escape") {event.preventDefault(); finishLabel(event.key === "Enter", true);}
                  }} onblur={() => {if (labelling) finishLabel();}} />
              </foreignObject>
            {/if}
          {/if}
          {#if current.graph.strokes?.length || inking}
            <!-- Q69: ink sits over the pills, each stroke placed at its topic so it moves and folds with it. -->
            <g class="ink" aria-hidden="true">
              {#each current.graph.strokes ?? [] as s (s.id)}
                {@const owner = shownById.get(s.node)}
                {#if owner}<path transform={`translate(${owner.x},${owner.y})`} d={inkOutline(s.points, INK_WIDTH)} data-stroke={s.id} data-ink-node={s.node} />{/if}
              {/each}
              {#if inking}<path class="live" d={inkOutline(inking.points, INK_WIDTH)} />{/if}
            </g>
          {/if}
          {#if lassoLoop}<path class="lasso" d={`M${lassoLoop.points.map(p => p.join(",")).join(" L")} Z`} />{/if}
          {#if marquee}<rect class="marquee" x={marquee.x0} y={marquee.y0} width={marquee.x1 - marquee.x0} height={marquee.y1 - marquee.y0} rx="6" />{/if}
          <!-- The dragged pill is redrawn on top so it stays visible over the pill it snaps into. -->
          {#if dragging}<use href={`#${pane.id}-node-${dragging}`} class="drag-ghost" />{/if}
        </g>
      </svg>

      <!-- Q18 D: the building tools are the Notes canvas pill itself, with map tools swapped in. -->
      <CanvasPill label="Map tools" id={`${pane.id}-map-tools`} storageKey="kitty.schema.map-tools" moreLabel="More map tools" menuLabel="Map options">
        <button class="pill-btn" aria-label="Select" title="Select and drag" aria-pressed={!inkMode && !linking} onclick={() => { inkMode = false; linking = ""; }}><Icon name="tool-select" size={18} /></button>
        <button class="pill-btn" aria-label="Connect topics" title="Connect the selected topic to another" aria-pressed={!!linking} disabled={!editable || !node} onclick={() => linking ? (linking = "") : startConnect()}><Icon name="tool-connector" size={18} /></button>
        <button class="pill-btn" aria-label="Ink" title="Draw pressure-sensitive ink; each stroke sticks to the nearest topic" aria-pressed={inkMode} disabled={!editable} onclick={() => { inkMode = !inkMode; linking = ""; }}><Icon name="tool-pen" size={18} /></button>
        <span class="pill-rule" aria-hidden="true"></span>
        <button class="pill-btn pill-word" title="Add child (Tab)" aria-keyshortcuts="Tab" disabled={!editable} onclick={() => quickAdd()}>{node ? "+ Child" : "+ Topic"}</button>
        <button class="pill-btn pill-word" title="Add sibling (Enter)" aria-keyshortcuts="Enter" disabled={!editable || !node} onclick={() => quickAdd(parents.get(selected) ?? "", selected)}>+ Sibling</button>
        <span class="pill-rule" aria-hidden="true"></span>
        {@render swatches("sm")}
        <span class="pill-rule" aria-hidden="true"></span>
        <button class="pill-btn" aria-label="Undo" title="Undo (Ctrl+Z)" disabled={!editable || (!history.length && (!current.can_undo || dirty))} onclick={() => void undo()}><Icon name="undo" size={17} /></button>
        <button class="pill-btn" aria-label="Redo" title="Redo (Ctrl+Shift+Z)" disabled={!editable || (!future.length && (!current.can_redo || dirty))} onclick={() => void redo()}><Icon name="redo" size={17} /></button>
        {#snippet more(close)}
          <span class="eyebrow">View</span>
          <button type="button" class="pill-menu-item" aria-pressed={outline} onclick={() => outline = !outline}><Icon name="menu" size={14} />Outline</button>
          <button type="button" class="pill-menu-item" aria-pressed={recall} onclick={() => { recall = !recall; revealed = []; close(); }}><Icon name="questions" size={14} />{recall ? "End quiz" : "Quiz me"}</button>
          <span class="eyebrow">Add</span>
          <form class="menu-form" onsubmit={(e) => { e.preventDefault(); addNode(); }}>
            <Select label="Topic type" kind="list" bind:value={kind} options={Object.entries(KINDS).filter(([id]) => id !== "task").map(([value, text]) => ({value, label: text}))} disabled={!editable} />
            {#if kind === "class"}
              <Select label="Study class" kind="search" bind:value={subjectId} placeholder="Choose a class" options={subjects.map(s => ({value: String(s.id), label: s.name}))} disabled={!editable} />
            {:else}
              <input aria-label="Topic label" placeholder="Label" bind:value={label} maxlength="200" disabled={!editable} />
            {/if}
            <button type="submit" class="pill-menu-item" disabled={!editable || (current?.graph.nodes.length ?? 0) >= 300 || (kind === "class" ? !subjectId : !label.trim())}><Icon name="plus" size={14} />Add topic</button>
          </form>
          <input type="search" aria-label="Find note pages" placeholder="Link a note page…" bind:value={noteSearch} oninput={findNoteCards} />
          {#each notePages.slice(0, 6) as page (page.id)}<button type="button" class="pill-menu-item" disabled={!editable} onclick={() => { close(); void addNoteCard(page); }}>▤ {page.title}</button>{/each}
          {#if current?.mode === "jobs" && canImportJobs}<button type="button" class="pill-menu-item" onclick={() => { close(); drawer = "jobs"; void findJobs(); }}><Icon name="jobs" size={14} />Import tracked jobs…</button>{/if}
          <span class="eyebrow">Find</span>
          <input type="search" aria-label="Find a topic" placeholder="Topic, kind or note…" bind:value={search} />
          {#if search}{#each filtered.slice(0, 8) as n (n.id)}<button type="button" class="pill-menu-item" onclick={() => { close(); choose(n); focusOn(n.id); }}>{hiddenLabel(n) ? "?" : name(n)}</button>{:else}<p class="hint">No topic matches.</p>{/each}{/if}
          {#if embedded}
            <span class="eyebrow">Layout</span>
            <div class="pill-menu-row">
              {#each LAYOUTS as [id, text] (id)}<button type="button" class="pill-menu-item" disabled={!editable} onclick={() => { close(); arrange(id); }}>{text}</button>{/each}
            </div>
            <span class="eyebrow">Map</span>
            {#each mapActions as item, index (index)}
              {#if !("separator" in item)}<button type="button" class="pill-menu-item" class:danger={item.danger} disabled={item.disabled} onclick={() => { close(); item.onselect(); }}>{item.label}</button>{/if}
            {/each}
          {/if}
        {/snippet}
      </CanvasPill>

      <div class="corner" role="group" aria-label="Zoom">
        <button type="button" class="z" aria-label="Zoom out" disabled={!editable} onclick={() => zoom(1 / 1.2)}>−</button>
        <span class="pct">{Math.round(current.viewport.zoom * 100)}%</span>
        <button type="button" class="z" aria-label="Zoom in" disabled={!editable} onclick={() => zoom(1.2)}>+</button>
        <span class="vr" aria-hidden="true"></span>
        <button type="button" class="z fit" aria-label="Fit map" title="Fit the map in view" disabled={!editable} onclick={fit}>Fit</button>
      </div>
    </div>
  {:else if embedded}
    {#if pageChecked}
      <div class="empty-state">
        <p>This page has no map yet.</p>
        <button type="button" class="pill primary page-action" disabled={busy || !pageId} onclick={createPageMap}><Icon name="plus" size={14} />Create map</button>
      </div>
    {/if}
  {:else}
    <div class="empty-state">
      <p>{maps.length ? "Open a map from Maps, or start a new one." : "No maps yet. Start one to think in branches."}</p>
      {@render newMap(true)}
    </div>
  {/if}

  {#if creatingFull}
    <EditDrawer title="New map" closeLabel="Close new map" onclose={() => creatingFull = false}>
      <form id={`${pane.id}-new-map`} class="drawer-form" onsubmit={(e) => { e.preventDefault(); void startMap(draftTitle); }}>
        <label class="field">Title<input bind:value={draftTitle} maxlength="200" placeholder="What are you learning?" required /></label>
        <div class="field"><span>Starting template</span><Select label="Starting template" kind="segments" bind:value={template} options={TEMPLATES} /></div>
        <div class="field"><span>Category</span><Select label="Map category" kind="segments" bind:value={mode} options={MODES} /></div>
        {#if subjects.length}<div class="field"><span>Start from a subject</span><Select label="Start from a subject" kind="search" bind:value={newSubject}
          options={[{value: "", label: "No subject"}, ...subjects.map(s => ({value: String(s.id), label: s.name}))]} /></div>{/if}
        <button type="button" class="link" onclick={() => importInput?.click()}>Import a map JSON instead…</button>
      </form>
      {#snippet footer()}
        <Button variant="ghost" onclick={() => creatingFull = false}>Cancel</Button>
        <Button intent="primary" type="submit" form={`${pane.id}-new-map`} disabled={busy || !draftTitle.trim()}>Create map</Button>
      {/snippet}
    </EditDrawer>
  {/if}

  {#if details && node && current}
    {@const n = node}
    <EditDrawer title={name(n)} meta={`${KINDS[n.kind] ?? n.kind}${n.source ? " · linked" : ""}`} closeLabel="Close topic details" onclose={() => details = false}>
      <div class="drawer-form">
        <label class="field">{n.source ? "Fallback label" : "Label"}<input bind:value={draftLabel} maxlength="200" disabled={!editable}
          oninput={e => {draftLabel = e.currentTarget.value; updateNode();}} /></label>
        <label class="field">Notes<textarea bind:value={draftNotes} maxlength="10000" rows="6" disabled={!editable}
          oninput={e => {draftNotes = e.currentTarget.value; updateNode();}}></textarea></label>
        <label class="field">Reference URL<input bind:value={draftUrl} type="url" maxlength="4000" disabled={!editable} placeholder="https://…"
          oninput={e => {draftUrl = e.currentTarget.value; updateNode();}} /></label>
        {#if safeUrl((n.source ? current.live[n.id]?.url : undefined) ?? n.url)}<a href={safeUrl((n.source ? current.live[n.id]?.url : undefined) ?? n.url)} target="_blank" rel="noopener noreferrer">Open source ↗</a>{/if}
        <div class="field"><span>Move under</span><Select label="Move under" kind="search" value={parents.get(n.id) ?? ""} disabled={!editable} onchange={reparent}
          options={[{value: "", label: "No parent (root topic)"}, ...current.graph.nodes.filter(item => !descendants(n.id).has(item.id)).map(item => ({value: item.id, label: name(item)}))]} /></div>
        {#if otherMaps.length}<div class="field"><span>Linked map</span><Select label="Linked map" kind="list" value={n.map_id ? String(n.map_id) : ""} disabled={!editable}
          onchange={(value) => { remember(); n.map_id = value ? Number(value) : null; }} options={[{value: "", label: "None"}, ...otherMaps.map(m => ({value: String(m.id), label: m.title}))]} /></div>{/if}
        {#if n.source}
          <span class="eyebrow">Linked {n.source.kind.replace("_", " ")}</span>
          {#if current.live[n.id]?.missing}<p class="hint warn">The source was deleted. Unlink to keep this topic.</p>{/if}
          {#if n.kind === "job"}<p class="hint">{current.live[n.id]?.company ?? "Unavailable"} · {current.live[n.id]?.status ?? "No application"} · {current.live[n.id]?.queue_state ?? "Unavailable"}{current.live[n.id]?.closed_at ? " · closed" : ""}</p>{/if}
          <Button size="sm" onclick={unlink} disabled={!editable}>Unlink, keep as a map note</Button>
        {/if}
        <span class="eyebrow">Connections</span>
        <ul class="links">
          {#each current.graph.edges.filter(e => e.from === n.id || e.to === n.id) as e (e.id)}
            {@const other = current.graph.nodes.find(item => item.id === (e.from === n.id ? e.to : e.from))}
            <li>
              <span>{e.from === n.id ? "→" : "←"} {other ? name(other) : "?"}</span>
              <input aria-label={`Label of the connection with ${other ? name(other) : "a topic"}`} value={e.label} maxlength="200" placeholder="relationship" disabled={!editable}
                onchange={(event) => editEdge(e.id, "label", event.currentTarget.value)} />
              <button type="button" class="icon-button" aria-label={`Remove the connection with ${other ? name(other) : "a topic"}`} disabled={!editable} onclick={() => removeEdge(e.id)}><Icon name="close" size={13} /></button>
            </li>
          {:else}<li class="hint">No connections.</li>{/each}
        </ul>
      </div>
      {#snippet footer()}
        <Button variant="ghost" disabled={!editable} onclick={() => { details = false; deleteNode(); }}>Delete topic</Button>
      {/snippet}
    </EditDrawer>
  {/if}

  {#if drawer === "links" && current}
    <EditDrawer title="Linked mentions" meta={autoNew ? `${autoNew} new` : ""} closeLabel="Close linked mentions" onclose={() => drawer = ""}>
      <div class="drawer-form">
        <div class="row">
          <Button size="sm" onclick={loadAutoLinks} disabled={autoBusy}>{autoBusy ? "Scanning…" : "Rescan"}</Button>
          <Button size="sm" intent="primary" onclick={() => applyAutoLinks(autoPicked)} disabled={!editable || dirty || !autoPicked.length}>Link selected ({autoPicked.length})</Button>
          <Button size="sm" onclick={() => applyAutoLinks(autoPending)} disabled={!editable || dirty || !autoPending.length}>Link all ({autoNew})</Button>
        </div>
        {#if autoError}<div role="alert" class="map-error">{autoError}</div>{/if}
        {#if autoView && autoView.revision !== current.revision}<p class="hint">The map changed since this scan; rescan to refresh.</p>{/if}
        {#if autoView && !autoView.suggestions.length}<p class="hint">No mentions yet. Write <code>[[Topic]]</code> in a note to link one.</p>{/if}
        <ul class="mentions">
          {#each autoView?.suggestions ?? [] as s (s.node_id)}
            {@const fresh = s.references.filter((r) => !r.linked).length}
            <li class="hub">
              <div class="hub-head">
                <label class="check"><input type="checkbox" checked={autoPicked.includes(s.node_id)} disabled={busy || !fresh}
                  onchange={(e) => { autoPicked = e.currentTarget.checked ? [...autoPicked, s.node_id] : autoPicked.filter((id) => id !== s.node_id); }}
                /><span class="sr-only">Link mentions of {s.label}</span></label>
                <button type="button" class="hub-node" onclick={() => focusNode(s.node_id)}>{s.label}</button>
                <Badge label={(fresh ? `${fresh} new / ${s.references.length}` : `all ${s.references.length} linked`) + (s.truncated ? " · more not shown" : "")} />
              </div>
              <ul class="branches">
                {#each s.references as r (`${r.kind}:${r.id}`)}
                  <li class:linked={r.linked} class:explicit={r.match === "wikilink"}>
                    <a href={`#${r.view}`} onclick={leaveFor}><span class="ref-kind">{REF_KINDS[r.kind] ?? r.kind}{r.match === "wikilink" ? " · [[link]]" : ""}{r.linked ? " · linked" : ""}</span>{r.title}</a>
                    {#if r.snippet}<small>{r.snippet}</small>{/if}
                  </li>
                {/each}
              </ul>
            </li>
          {/each}
        </ul>
      </div>
    </EditDrawer>
  {/if}

  {#if drawer === "jobs" && current?.mode === "jobs" && canImportJobs}
    <EditDrawer title="Import tracked jobs" closeLabel="Close job import" onclose={() => drawer = ""}>
      <div class="drawer-form">
        <form class="row" onsubmit={(e) => { e.preventDefault(); void findJobs(); }}>
          <input type="search" aria-label="Search title or company" placeholder="Title or company…" bind:value={jobSearch} maxlength="200" />
          <Button size="sm" type="submit" disabled={busy}>Search</Button>
        </form>
        <ul class="jobs">
          {#each jobs as job (job.id)}<li>
              <label class="check"><input type="checkbox" checked={jobIds.includes(job.id)} disabled={busy || (!jobIds.includes(job.id) && jobIds.length >= 50)}
                onchange={(e) => { jobIds = e.currentTarget.checked ? [...jobIds, job.id] : jobIds.filter((id) => id !== job.id); }}
              /><span>{job.title} / {job.company}<small>{job.status ?? "No application"} · {job.queue_state}</small></span></label>
            </li>{/each}
        </ul>
        <div class="row">
          <Button size="sm" onclick={() => findJobs(Math.max(0, jobOffset - 50))} disabled={busy || jobOffset === 0}>Previous</Button>
          <Button size="sm" onclick={() => findJobs(jobOffset + 50)} disabled={busy || !moreJobs}>Next</Button>
        </div>
      </div>
      {#snippet footer()}
        <Button intent="primary" onclick={importJobs} disabled={!editable || dirty || !jobIds.length}>Import {jobIds.length} selected</Button>
      {/snippet}
    </EditDrawer>
  {/if}

  {#if drawer === "ai" && current}
    <EditDrawer title="Ask AI about this map" closeLabel="Close map feedback" onclose={() => drawer = ""}>
      <MindMapFeedback title={current.title} {demo}
        graph={{
          nodes: current.graph.nodes.map(n => ({id: n.id, label: name(n), notes: n.notes})),
          edges: current.graph.edges.map(e => ({from: e.from, to: e.to, label: e.label})),
        }} />
    </EditDrawer>
  {/if}
</section>

<style>
  /* Q18: one surface that fills its tab above the dock (or its host, embedded in Notes). */
  .schema {
    position: relative;
    display: flex;
    flex-direction: column;
    height: var(--map-height, var(--fit-h, calc(100dvh - var(--workbench-top, 0px) - var(--dock-space, 0px) - 34px)));
    min-height: 420px;
    overflow: hidden;
    color: var(--ink);
    font: 400 var(--fs-control) / 1.35 var(--font-ui);
  }
  .schema.embedded {
    height: 100%;
    min-height: 0;
  }

  /* ── slim top bar ── */
  .top {
    display: flex;
    align-items: center;
    gap: 6px;
    flex-shrink: 0;
    min-height: 46px;
    padding: 0 10px 0 8px;
    border-bottom: 1px solid var(--hairline);
  }
  .top > * { flex-shrink: 0; }
  .grow { flex: 1; }
  .switcher,
  .bar-btn {
    display: inline-flex;
    align-items: center;
    gap: 7px;
    height: var(--control-h);
    min-height: 0;
    padding: 0 10px;
    border: 0;
    border-radius: var(--r-control);
    background: transparent;
    color: var(--muted);
    font: 500 var(--fs-control) / 1 var(--font-ui);
  }
  .switcher:hover:not(:disabled),
  .switcher[aria-expanded="true"],
  .bar-btn:hover:not(:disabled),
  .bar-btn[aria-expanded="true"] { background: var(--wash); color: var(--ink); }
  .chev { color: var(--subtle); font-size: 10px; }
  .slash { color: var(--subtle); }
  .top .title {
    flex: 0 1 auto;
    width: min(36ch, 40%);
    min-width: 8ch;
    height: var(--control-h);
    padding: 0 8px;
    border: 1px solid transparent;
    border-radius: 7px;
    background: transparent;
    box-shadow: none;
    color: var(--ink);
    font: 600 14px / 1.2 var(--font-ui);
    text-overflow: ellipsis;
  }
  .top .title:hover:not(:disabled) { border-color: var(--hairline); }
  .top .title:focus { border-color: var(--accent); outline: none; }
  .subject { display: inline-flex; align-items: center; gap: 6px; color: var(--subtle); font-size: 12px; white-space: nowrap; }
  .subject .dot { width: 6px; height: 6px; border-radius: 999px; background: var(--c3); }
  .vr { flex-shrink: 0; width: 1px; height: 18px; margin: 0 4px; background: var(--hairline); }
  :global(.kitty-popover.map-switcher) { min-width: 240px; }
  .switch-list { display: grid; gap: 1px; max-height: 50dvh; overflow: auto; }
  .switch-item,
  .menu-item {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    width: 100%;
    min-height: 0;
    padding: 6px 10px;
    border: 0;
    border-radius: 8px;
    background: transparent;
    color: var(--ink);
    font: 500 var(--fs-control) / 1.3 var(--font-ui);
    text-align: left;
  }
  .switch-item small { margin: 0; color: var(--subtle); font: 500 var(--fs-chrome) / 1 var(--font-mono); text-transform: uppercase; letter-spacing: 0.08em; }
  .switch-item:hover,
  .menu-item:hover,
  .menu-item:focus-visible { background: var(--wash); }
  .switch-item[aria-current="true"] { color: var(--accent); }

  /* ── canvas ── */
  .canvas {
    position: relative;
    flex: 1;
    min-height: 0;
    overflow: hidden;
    container-type: size;
    background-color: color-mix(in srgb, var(--solid-void) 55%, transparent);
    background-image: radial-gradient(color-mix(in srgb, var(--ink) 9%, transparent) 1px, transparent 1px);
    background-size: 22px 22px;
  }
  .stage {
    position: absolute;
    inset: 0;
    display: block;
    width: 100%;
    height: 100%;
    touch-action: none;
    user-select: none;
    cursor: grab;
  }
  .canvas-empty,
  .empty-state p { margin: 0; color: var(--muted); }
  .canvas-empty {
    position: absolute;
    left: 50%;
    top: 40%;
    transform: translateX(-50%);
    pointer-events: none;
  }
  .empty-state {
    flex: 1;
    display: grid;
    place-content: center;
    justify-items: center;
    gap: 14px;
    padding: 40px;
  }
  .map-error {
    position: absolute;
    left: 12px;
    right: 12px;
    top: 58px;
    z-index: 6;
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 6px 6px 6px 12px;
    border: 1px solid color-mix(in srgb, var(--error) 50%, var(--hairline));
    border-radius: 10px;
    background: color-mix(in srgb, var(--solid-raised) 92%, var(--error));
    color: var(--ink);
    overflow-wrap: anywhere;
  }
  .embedded .map-error { top: 12px; }
  .map-error > span { flex: 1; }
  .views .map-error,
  .drawer-form .map-error { position: static; }
  .link {
    min-height: 0;
    padding: 2px 6px;
    border: 0;
    background: none;
    color: var(--accent);
    font: 500 var(--fs-control) / 1.3 var(--font-ui);
    text-decoration: underline;
    text-underline-offset: 2px;
  }

  /* Q44: rounded pills with a branch-coloured dot; parentless nodes are inverted ink roots. */
  .graph-node { cursor: move; outline: none; }
  .graph-node .pill {
    fill: color-mix(in srgb, var(--b) 14%, var(--solid-panel));
    stroke: color-mix(in srgb, var(--b) 55%, transparent);
    stroke-width: 1.2;
    transition: fill 160ms;
  }
  .graph-node:hover .pill { fill: color-mix(in srgb, var(--b) 26%, var(--solid-panel)); }
  .graph-node.selected .pill,
  .graph-node:focus-visible .pill {
    stroke: var(--b);
    stroke-width: 2;
    filter: drop-shadow(0 0 8px color-mix(in srgb, var(--b) 45%, transparent));
  }
  .graph-node .dot { fill: var(--b); }
  .graph-node .label { fill: var(--ink); font: 500 12.5px var(--font-ui); pointer-events: none; }
  .graph-node.root .pill { fill: var(--ink); stroke: none; }
  .graph-node.root:hover .pill { fill: color-mix(in srgb, var(--ink) 88%, var(--solid-void)); }
  .graph-node.root.selected .pill,
  .graph-node.root:focus-visible .pill { stroke: var(--accent); stroke-width: 2.5; filter: drop-shadow(0 0 8px color-mix(in srgb, var(--accent) 45%, transparent)); }
  .graph-node.root .label { fill: var(--solid-void); font: 600 13px var(--font-ui); }
  .graph-node .fold circle { fill: color-mix(in srgb, var(--b) 40%, var(--solid-panel)); }
  .graph-node.root .fold circle { fill: var(--solid-void); }
  .graph-node .fold text { fill: var(--ink); font: 600 9px var(--font-mono); pointer-events: none; }
  .graph-node.root.painted .pill { fill: var(--b); }
  .graph-node.drop-target .pill {
    stroke: var(--accent);
    stroke-width: 2.5;
    filter: drop-shadow(0 0 10px color-mix(in srgb, var(--accent) 60%, transparent));
  }
  .graph-node.dragging { opacity: 0.88; cursor: grabbing; }
  .graph-node.detaching .pill { stroke: var(--b); stroke-width: 1.6; stroke-dasharray: 5 4; }
  .graph-node.root.detaching .pill { stroke: var(--accent); }
  .snap-preview { fill: none; stroke: var(--accent); stroke-width: 1.8; stroke-dasharray: 6 5; pointer-events: none; }
  .drag-ghost { pointer-events: none; opacity: 0.7; }
  .marquee, .lasso {
    fill: color-mix(in srgb, var(--accent) 10%, transparent);
    stroke: var(--accent);
    stroke-width: 1;
    stroke-dasharray: 4 3;
    vector-effect: non-scaling-stroke;
    pointer-events: none;
  }
  /* Q69: pen ink is filled outlines whose width follows pressure; it never takes pointer hits from the pills. */
  .ink { fill: var(--ink); opacity: 0.88; pointer-events: none; }
  .ink .live { opacity: 0.75; }
  .stage.ink-mode { cursor: crosshair; }
  .inline-label {
    width: 100%;
    height: 100%;
    box-sizing: border-box;
    padding: 0 14px;
    border: 1.5px solid var(--accent);
    border-radius: 999px;
    color: var(--ink);
    background: var(--solid-panel);
    font: 500 12.5px var(--font-ui);
  }
  .edge { fill: none; stroke: var(--violet); stroke-width: 2; pointer-events: none; }
  /* Q65 E: cross connections are dashed violet arcs outside the tree. */
  .edge.cross { stroke-width: 1.6; stroke-dasharray: 6 5; stroke-linecap: round; }
  .edge-input { padding: 0 10px; border-color: var(--violet); font-size: 11.5px; }
  .edge.branch { stroke: var(--b); stroke-width: 1.6; stroke-opacity: 0.6; }
  .edge.active { stroke: var(--accent); stroke-width: 3; }
  .edge-hit { fill: none; stroke: transparent; stroke-width: 14; pointer-events: stroke; cursor: pointer; }
  .edge-label {
    fill: var(--ink);
    font: 10px var(--font-ui);
    paint-order: stroke;
    stroke: var(--solid-void);
    stroke-width: 5px;
    cursor: pointer;
  }
  .edge-label.active { fill: var(--accent); }
  @media (prefers-reduced-motion: reduce) { .graph-node .pill { transition: none; } }

  /* Floating glass pieces over the canvas. */
  .drag-hint,
  .merge-targets,
  .recall-bar,
  .ctx-bar,
  .corner,
  .map-outline {
    position: absolute;
    z-index: 2;
    border: 1px solid var(--hairline);
    background: color-mix(in srgb, var(--solid-raised) 86%, transparent);
    backdrop-filter: var(--glass);
    -webkit-backdrop-filter: var(--glass);
  }
  .drag-hint {
    left: 50%;
    top: 12px;
    transform: translateX(-50%);
    margin: 0;
    padding: 5px 12px;
    border-radius: 999px;
    color: var(--muted);
    font-size: 12px;
    white-space: nowrap;
    pointer-events: none;
  }
  .merge-targets {
    left: 50%;
    top: 52px;
    z-index: 3;
    transform: translateX(-50%);
    display: flex;
    align-items: center;
    gap: 4px;
    max-width: calc(100% - 24px);
    padding: 4px 4px 4px 12px;
    border-radius: 999px;
    color: var(--muted);
    white-space: nowrap;
    overflow-x: auto;
  }
  .merge-targets button {
    min-height: 0;
    height: 26px;
    padding: 0 12px;
    border: 1px dashed var(--hairline);
    border-radius: 999px;
    background: transparent;
    color: var(--ink);
    font: 500 12px / 1 var(--font-ui);
  }
  .merge-targets button:hover { border-color: var(--accent); background: var(--wash); }
  .recall-bar {
    left: 50%;
    top: 12px;
    transform: translateX(-50%);
    display: flex;
    align-items: center;
    gap: 2px;
    padding: 3px 4px 3px 12px;
    border-radius: 999px;
    color: var(--muted);
    white-space: nowrap;
  }
  .map-outline {
    left: 12px;
    top: 12px;
    display: grid;
    align-content: start;
    gap: 1px;
    width: min(260px, calc(100% - 24px));
    max-height: calc(100% - 96px);
    overflow: auto;
    padding: 8px;
    border-radius: 12px;
  }
  .map-outline [role="treeitem"] { display: flex; align-items: center; gap: 2px; }
  .fold-toggle,
  .outline-item {
    min-height: 0;
    padding: 3px 6px;
    border: 0;
    border-radius: 6px;
    background: none;
    color: var(--muted);
    font: 500 12.5px / 1.3 var(--font-ui);
    text-align: left;
  }
  .fold-toggle { width: 20px; padding: 3px 0; color: var(--subtle); font-size: 10px; }
  .outline-item { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .outline-item:hover,
  .outline-item.active { background: var(--wash); color: var(--ink); }

  /* Q18: the contextual bar on the selected topic. 30px tall, it ends at the topic's top edge and
     stays clear of the centre of the sibling above, which a tap may still pick. */
  .ctx-bar {
    display: flex;
    align-items: center;
    gap: 1px;
    height: 30px;
    box-sizing: border-box;
    padding: 0 4px;
    border-radius: 999px;
    box-shadow: 0 10px 26px -14px color-mix(in srgb, var(--shadow-tint) 85%, transparent);
    transform: translateY(-100%);
    white-space: nowrap;
  }
  .ctx-bar.below { transform: none; }
  .ctx-bar .vr { height: 16px; margin: 0 3px; }
  .ctx-bar :global(.overflow-trigger) { width: 24px; height: 24px; min-height: 0; }
  .cb {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    height: 24px;
    min-height: 0;
    padding: 0 9px;
    border: 0;
    border-radius: 999px;
    background: transparent;
    color: var(--muted);
    font: 500 12px / 1 var(--font-ui);
    white-space: nowrap;
  }
  .cb:hover:not(:disabled),
  .cb[aria-pressed="true"],
  .cb[aria-expanded="true"] { background: var(--wash); color: var(--ink); }
  .cb:disabled { opacity: 0.45; cursor: default; }
  .cb.icon { width: 24px; padding: 0; justify-content: center; }
  .cb.colour { padding: 0 6px 0 3px; }
  .count { padding: 0 6px; color: var(--ink); font: 600 12px var(--font-ui); }
  .style-row { display: flex; flex-wrap: wrap; gap: 2px; margin-top: 6px; }
  :global(.kitty-popover.topic-style) { padding: 8px; }

  /* Q65 D: round theme-slot swatches (the pill's colour row and the topic style popover). */
  .swatches { display: inline-flex; align-items: center; gap: 2px; }
  .swatch {
    flex-shrink: 0;
    width: 20px;
    height: 20px;
    min-height: 0;
    padding: 0;
    border: 2px solid transparent;
    border-radius: 50%;
    background: var(--dot) content-box;
    box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--ink) 20%, transparent);
    cursor: pointer;
    transition: transform 140ms ease;
  }
  .swatches.sm .swatch { width: 18px; height: 18px; }
  span.swatch { display: inline-block; width: 18px; height: 18px; cursor: inherit; }
  .swatch:hover:not(:disabled) { transform: scale(1.12); }
  .swatch:disabled { opacity: 0.4; cursor: default; }
  .swatch[aria-pressed="true"],
  .swatch.on { border-color: var(--dot); }
  .swatch.auto {
    --dot: var(--muted);
    background: conic-gradient(var(--c1), var(--c2), var(--c3), var(--c4), var(--c5), var(--c6), var(--c1)) content-box;
  }
  @media (prefers-reduced-motion: reduce) { .swatch { transition: none; } }

  /* Zoom: a small corner control (the top right when the canvas is narrow, clear of the pill). */
  .corner {
    right: 12px;
    bottom: 12px;
    display: flex;
    align-items: center;
    height: 28px;
    box-sizing: border-box;
    padding: 0 3px;
    border-radius: 999px;
  }
  @container (max-width: 980px) { .corner { top: 12px; bottom: auto; } }
  .z {
    display: grid;
    place-items: center;
    min-width: 22px;
    height: 22px;
    min-height: 0;
    padding: 0 4px;
    border: 0;
    border-radius: 999px;
    background: transparent;
    color: var(--muted);
    font: 500 14px / 1 var(--font-ui);
  }
  .z.fit { padding: 0 7px; font-size: 11.5px; }
  .z:hover:not(:disabled) { background: var(--wash); color: var(--ink); }
  .pct { min-width: 38px; color: var(--muted); font: 500 var(--fs-chrome) / 1 var(--font-mono); text-align: center; }
  .corner .vr { height: 14px; margin: 0 2px; }

  /* The pill's ⋯ panel: small forms and searches. */
  .menu-form { display: grid; gap: 6px; }
  :global(.pill-menu) input[type="search"],
  .menu-form input { width: 100%; box-sizing: border-box; }
  .pill-menu-item.danger { color: var(--error); }
  .hint { margin: 0; color: var(--muted); font-size: 12px; line-height: 1.5; }
  .hint.warn { color: var(--warning); }

  /* Graph and merged-network views: the graph fills the canvas, an inspector rides on the right. */
  .views {
    flex: 1;
    min-height: 0;
    display: grid;
    grid-template-columns: minmax(0, 1fr) minmax(230px, 280px);
  }
  .network-view { grid-template-columns: minmax(220px, 260px) minmax(0, 1fr) minmax(230px, 280px); }
  .graph-pane { position: relative; min-width: 0; min-height: 0; padding: 0; border: 0; }
  .side {
    display: flex;
    flex-direction: column;
    gap: 10px;
    min-width: 0;
    padding: 14px;
    overflow: auto;
    border-left: 1px solid var(--hairline);
  }
  .network-view .side:first-child { border-left: 0; border-right: 1px solid var(--hairline); }
  .side h2 { margin: 0; font-size: var(--fs-heading); overflow-wrap: anywhere; }
  .field { display: grid; gap: 5px; color: var(--muted); font-size: var(--fs-label); }
  fieldset { display: grid; gap: 6px; max-height: 260px; margin: 0; padding: 0; overflow: auto; border: 0; }
  legend { padding: 0 0 4px; color: var(--muted); font-size: var(--fs-label); }
  label.check { display: flex; align-items: center; gap: 8px; color: var(--ink); }
  label.check input { width: auto; }
  .members,
  .clusters,
  .links,
  .jobs,
  .mentions { display: grid; gap: 6px; margin: 0; padding: 0; list-style: none; font-size: 12px; }
  .clusters { color: var(--muted); font-size: 11px; }
  .members li { display: grid; gap: 6px; padding: 8px 0; border-top: 1px solid var(--hairline); overflow-wrap: anywhere; }
  .notes { margin: 4px 0; color: var(--muted); font-size: 12px; white-space: pre-wrap; overflow-wrap: anywhere; }
  small { display: block; margin-top: 3px; color: var(--muted); font-size: 11px; }
  a { display: block; color: var(--violet); overflow-wrap: anywhere; }

  /* Drawers (Q8): every field of a topic, linked mentions, job import, AI feedback, a new map. */
  .drawer-form { display: grid; gap: 12px; }
  .row { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; }
  .drawer-form input:not([type="checkbox"]),
  .drawer-form textarea { width: 100%; box-sizing: border-box; }
  .drawer-form textarea { resize: vertical; }
  .links li { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) auto; align-items: center; gap: 6px; }
  .links li span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .jobs li { padding: 6px 0; border-top: 1px solid var(--hairline); }
  .jobs label { align-items: start; }
  .hub-head { display: flex; align-items: center; gap: 8px; }
  .hub-node {
    flex: 1;
    min-width: 0;
    min-height: 0;
    padding: 6px 10px;
    border: 1px solid var(--accent);
    border-radius: 999px;
    background: transparent;
    color: var(--ink);
    font: 600 12.5px / 1.3 var(--font-ui);
    text-align: left;
    overflow-wrap: anywhere;
  }
  .branches { display: grid; gap: 6px; margin: 0 0 0 14px; padding: 6px 0 0 12px; border-left: 2px solid var(--violet); list-style: none; }
  .branches li { padding: 6px 8px; border-radius: 8px; background: var(--wash); overflow-wrap: anywhere; }
  .branches li.linked { opacity: 0.62; }
  .branches a { margin: 0; color: var(--ink); text-decoration: none; }
  .branches a:hover,
  .branches a:focus-visible { color: var(--accent); }
  .ref-kind { display: block; margin-bottom: 2px; color: var(--violet); font: 500 var(--fs-chrome) / 1.2 var(--font-mono); letter-spacing: 0.06em; text-transform: uppercase; }
  code { color: var(--violet); }
  @media (max-width: 900px) {
    .views,
    .network-view { grid-template-columns: minmax(0, 1fr); grid-auto-rows: minmax(320px, auto); overflow: auto; }
    .side,
    .network-view .side:first-child { border: 0; border-top: 1px solid var(--hairline); }
  }
</style>
