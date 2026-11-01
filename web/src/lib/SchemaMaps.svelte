<script lang="ts">
  import { onMount, tick } from "svelte";
  import { Panel, Button, Badge } from "@nunera/bracket";
  import { api, safeUrl } from "./api";
  import SchemaNetwork from "./SchemaNetwork.svelte";
  import type { GraphLayout, GraphLink, GraphNode } from "./graph3d";
  import { useWorkspaceContext } from "./workspaceContext.svelte";
  import MindMapFeedback from "./MindMapFeedback.svelte";
  import { activeModules } from "./modules.svelte";
  let { revision = 0, demo = false, section = "schema", embedded = false }: { revision?: number; demo?: boolean; section?: "schema" | "study"; embedded?: boolean } =
    $props();
  const pane = useWorkspaceContext();
  // Importing tracked jobs reads Apply AI data; it exists only while that module is active.
  const canImportJobs = $derived(activeModules.enabled.includes("applyai"));
  let container = $state<HTMLElement>();
  function confirm(message: string) {
    return pane.active && window.confirm(message);
  }
  export function beforeLeave(): boolean {
    return !dirty || confirm("Leave this map? Unsaved edits remain in its local draft; use Save map to persist them.");
  }
  export function hasUnsavedChanges(): boolean {
    return dirty || busy || blocked;
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
  };
  type Edge = { id: string; from: string; to: string; label: string };
  type Graph = { nodes: Node[]; edges: Edge[] };
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
    suggestions: { node_id: string; label: string; references: Reference[] }[];
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
  let measurer: CanvasRenderingContext2D | null = null, uiFont = "Inter, sans-serif", headingFont = "Inter, sans-serif";
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
    const text = textWidth(pillText(n), root ? `600 13px ${headingFont}` : `500 12.5px ${uiFont}`);
    return {w: Math.round(Math.min(320, Math.max(root ? 72 : 64, text + (root ? 36 : 44) + (fold ? 26 : 0)))), h: root ? ROOT_H : PILL_H};
  }
  let maps = $state<Summary[]>([]),
    subjects = $state<{ id: number; name: string }[]>([]);
  let current = $state<MapItem | null>(null),
    busy = $state(false),
    dirty = $state(false),
    blocked = $state(false);
  const parents = $derived(new Map([...(current?.graph.edges ?? [])].reverse().filter(e => e.from !== e.to).map(e => [e.to, e.from])));
  /** The first non-loop edge into each node is its tree edge; any other edge is drawn as a free connection. */
  const treeEdges = $derived(new Set([...new Map([...(current?.graph.edges ?? [])].reverse().filter(e => e.from !== e.to).map(e => [e.to, e.id])).values()]));
  let error = $state(""),
    notice = $state(""),
    newTitle = $state(""),
    mode = $state("study");
  let selected = $state(""),
    selectedEdge = $state(""),
    search = $state(""),
    kind = $state("topic"),
    label = $state(""),
    subjectId = $state("");
  let edgeFrom = $state(""),
    edgeTo = $state(""),
    edgeLabel = $state("relates to");
  let draftLabel = $state(""),
    draftNotes = $state(""),
    draftUrl = $state("");
  let history = $state<Snapshot[]>([]),
    stage = $state<SVGSVGElement>();
  let showJobs = $state(false),
    jobSearch = $state(""),
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
  } | null = null;
  // Q65 B: the node being dragged, the pill it would snap into, and whether D detached it.
  let dragging = $state(""), dropTarget = $state(""), detaching = $state(false);
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
  let showLinks = $state(false),
    autoLinks = $state<AutoLinks | null>(null),
    autoPicked = $state<string[]>([]),
    autoBusy = $state(false),
    autoError = $state("");
  let autoRequestedFor = 0;
  let outline = $state(false), recall = $state(false), revealed = $state<string[]>([]);
  let editing = $state(""), inlineLabel = $state("");
  let noteSearch = $state(""), notePages = $state<{id: number; title: string}[]>([]);
  let template = $state("Blank");
  const draftKey = (id: number) => `kitty.schema.draft.${id}`;
  function descendants(id: string) {
    const ids = new Set([id]);
    for (let i = 0; i < (current?.graph.nodes.length ?? 0); i++)
      for (const edge of current?.graph.edges ?? []) if (ids.has(edge.from)) ids.add(edge.to);
    return ids;
  }
  function reparent(parent: string) {
    if (!current || !node || !editable) return;
    if (parent && descendants(node.id).has(parent)) { error = "A topic cannot be moved inside its own branch."; return; }
    remember();
    current.graph.edges = current.graph.edges.filter(edge => edge.to !== node.id);
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
      for (const e of current!.graph.edges.filter(e => e.from === id)) {
        if (!seen.has(e.to)) {hidden.add(e.to); visit(e.to, seen);}
      }
    };
    for (const n of current.graph.nodes) if (n.collapsed) visit(n.id);
    const result: Node[] = [], seen = new Set<string>();
    const append = (n: Node) => {
      if (seen.has(n.id) || hidden.has(n.id)) return;
      seen.add(n.id); result.push(n);
      for (const edge of current!.graph.edges.filter(e => e.from === n.id)) {
        const child = current!.graph.nodes.find(n => n.id === edge.to);
        if (child) append(child);
      }
    };
    for (const n of current.graph.nodes.filter(n => !current!.graph.edges.some(e => e.to === n.id))) append(n);
    for (const n of current.graph.nodes) append(n);
    return result;
  });
  function depth(id: string) {
    const seen = new Set<string>();
    while (!seen.has(id)) {
      seen.add(id);
      const parent = current?.graph.edges.find(e => e.to === id);
      if (!parent) break;
      id = parent.from;
    }
    return seen.size - 1;
  }
  function branchColor(n: Node) {
    let branch = n.id;
    const seen = new Set<string>();
    while (!seen.has(branch)) {
      seen.add(branch);
      const parent = current?.graph.edges.find(e => e.to === branch)?.from;
      if (!parent || !current?.graph.edges.some(e => e.to === parent)) break;
      branch = parent;
    }
    const index = current?.graph.nodes.findIndex(n => n.id === branch) ?? 0;
    return `var(--c${Math.max(0, index) % 6 + 1}, var(--accent))`;
  }
  function hiddenLabel(n: Node) {
    return recall && !!current?.graph.edges.some(e => e.to === n.id) && !revealed.includes(n.id);
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
      notice = "Recall the hidden topics and their connections, then reveal each answer.";
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
  async function subjectMap(id: number) {
    const subject = subjects.find(s => s.id === id);
    if (!subject) return;
    const title = `${subject.name} · ${template}`;
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
  $effect(() => {
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
    dirty = true;
    notice = "Unsaved local edits";
  }
  function accepted(item: MapItem, restoreDraft = false) {
    current = item;
    dirty = false;
    blocked = false;
    history = [];
    selected = "";
    selectedEdge = "";
    editing = "";
    notice = "Saved locally";
    if (restoreDraft) {
      try {
        const raw = localStorage.getItem(draftKey(item.id));
        if (raw) {
          const draft = JSON.parse(raw);
          if (draft.snapshot?.graph?.nodes && draft.snapshot?.graph?.edges) {
            Object.assign(item, draft.snapshot);
            dirty = true;
            blocked = draft.revision !== item.revision;
            notice = blocked ? "Recovered local edits; the saved map changed. Export or reload to resolve." : "Recovered unsaved map edits — Save map to keep them.";
          }
        }
      } catch { error = "The local map draft could not be restored."; }
    }
    try { localStorage.setItem(`kitty.schema.last-map.${section}`, String(item.id)); } catch { /* Map selection is optional; SQLite remains authoritative. */ }
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
    if (
      busy ||
      (!force &&
        dirty &&
        !confirm(
          "Discard unsaved map edits? Export them first to keep a copy.",
        ))
    )
      return;
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
  async function newMap() {
    if (busy || (dirty && !confirm("Leave this map? Unsaved edits remain in its local draft; use Save map to persist them."))) return;
    current = null;
    selected = "";
    selectedEdge = "";
    editing = "";
    dirty = false;
    blocked = false;
    error = "";
    notice = "";
    newTitle = "";
    view = "edit";
    await tick();
    if (pane.active) container?.querySelector<HTMLInputElement>(`#${CSS.escape(`${pane.id}-new-map-title`)}`)?.focus();
  }
  async function create() {
    if (
      busy ||
      !newTitle.trim() ||
      (dirty && !confirm("Discard unsaved map edits?"))
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
  async function save() {
    finishInline(false);
    if (!current || !editable || !dirty) return;
    const selectedId = selected;
    busy = true;
    error = "";
    try {
      accepted(
        await api<MapItem>("/schema/save", {
          id: current.id,
          revision: current.revision,
          title: current.title,
          graph: current.graph,
          viewport: current.viewport,
        }),
      );
      try { localStorage.removeItem(draftKey(current.id)); } catch { /* Saved on server. */ }
      const activeNode = current.graph.nodes.find(n => n.id === selectedId);
      if (activeNode) choose(activeNode);
      await refresh();
    } catch (e) {
      failure(e);
      blocked = error.includes("changed in another window");
    } finally {
      busy = false;
    }
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
      history = history.slice(0, -1);
      Object.assign(current, clone(previous));
      dirty = true;
      selected = "";
      selectedEdge = "";
      return;
    }
    if (dirty || !current.can_undo) return;
    busy = true;
    error = "";
    try {
      accepted(
        await api<MapItem>("/schema/undo", {
          id: current.id,
          revision: current.revision,
        }),
      );
      await refresh();
    } catch (e) {
      failure(e);
      blocked = error.includes("changed in another window");
    } finally {
      busy = false;
    }
  }
  function choose(n: Node) {
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
  function deleteNode() {
    if (!current || !node || !editable) return;
    remember();
    current.graph.nodes = current.graph.nodes.filter((n) => n.id !== selected);
    current.graph.edges = current.graph.edges.filter(
      (e) => e.from !== selected && e.to !== selected,
    );
    if (edgeFrom === selected) edgeFrom = "";
    if (edgeTo === selected) edgeTo = "";
    selected = "";
  }
  function deleteAtPointer(event: MouseEvent, target: Node) {
    if ((event.target as Element).closest('input,textarea,[contenteditable="true"]') || window.getSelection()?.toString()) return;
    event.preventDefault();
    if (!editable || !confirm(`Remove “${name(target)}” from this map? Linked notes, cards and jobs stay unchanged.`)) return;
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
  function connect() {
    if (
      !current ||
      !editable ||
      !current.graph.nodes.some((n) => n.id === edgeFrom) ||
      !current.graph.nodes.some((n) => n.id === edgeTo) ||
      current.graph.edges.length >= 600
    )
      return;
    remember();
    current.graph.edges.push({
      id: crypto.randomUUID(),
      from: edgeFrom,
      to: edgeTo,
      label: edgeLabel,
    });
    arrange("tree");
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
  function nodeKey(e: KeyboardEvent, n: Node) {
    if (!editable || e.altKey) return;
    if (gesture?.node) {
      // Mid-drag, D breaks the branch off (press again to reattach on drop).
      if (e.key.toLowerCase() === "d") { e.preventDefault(); e.stopPropagation(); detaching = !detaching; dropTarget = ""; }
      return;
    }
    if (e.ctrlKey || e.metaKey) {
      if (e.key === "Enter") { e.preventDefault(); e.stopPropagation(); void editInline(n); }
      else if (e.key === "ArrowUp" || e.key === "ArrowDown") { e.preventDefault(); e.stopPropagation(); choose(n); reorder(n, e.key === "ArrowUp" ? -1 : 1); }
      return;
    }
    choose(n);
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
      if (target) { choose(target); focusOn(target.id); }
    } else if (e.key.length === 1) { e.preventDefault(); e.stopPropagation(); void editInline(n, e.key); }
  }
  function start(e: PointerEvent, id = "") {
    if (!current || !editable || gesture || e.button !== 0) return;
    e.preventDefault();
    e.stopPropagation();
    if (id) {
      (e.currentTarget as SVGGElement).focus();
      const n = current.graph.nodes.find((n) => n.id === id)!;
      choose(n);
    }
    gesture = {
      pointer: e.pointerId,
      x: e.clientX,
      y: e.clientY,
      node: id,
      branch: id ? [...descendants(id)] : [],
      before: snapshot(),
      moved: false,
    };
    stage?.setPointerCapture(e.pointerId);
  }
  /** The pill under the pointer that a dragged branch may move into (never its own branch). */
  function dropCandidate(e: PointerEvent, branch: string[]) {
    if (!current || !stage) return undefined;
    const rect = stage.getBoundingClientRect(), v = current.viewport;
    const x = (e.clientX - rect.left - v.x) / v.zoom, y = (e.clientY - rect.top - v.y) / v.zoom;
    return visibleNodes.find(n => !branch.includes(n.id) && x >= n.x && x <= n.x + box(n).w && y >= n.y && y <= n.y + box(n).h);
  }
  function move(e: PointerEvent) {
    if (!gesture || !current || gesture.pointer !== e.pointerId) return;
    const dx = e.clientX - gesture.x,
      dy = e.clientY - gesture.y;
    if (Math.abs(dx) + Math.abs(dy) < 2 && !gesture.moved) return;
    gesture.moved = true;
    if (gesture.node) {
      const branch = gesture.branch, origin = gesture.before.graph.nodes.find((n) => n.id === gesture!.node)!;
      const target = detaching ? undefined : dropCandidate(e, branch);
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
    notice = "Branch detached · save to keep";
  }
  function end(e: PointerEvent, cancel = false) {
    if (!gesture || !current || gesture.pointer !== e.pointerId) return;
    const target = dropTarget;
    if (cancel) Object.assign(current, clone(gesture.before));
    else if (gesture.moved && gesture.node) {
      const dragged = current.graph.nodes.find(n => n.id === gesture!.node)!;
      if (detaching || target || dragged.free) {
        history = [...history.slice(-29), gesture.before];
        if (detaching) detachBranch(dragged);
        else if (target) {
          current.graph.edges = current.graph.edges.filter(e => !(treeEdges.has(e.id) && e.to === dragged.id));
          current.graph.edges.push({id: crypto.randomUUID(), from: target, to: dragged.id, label: ""});
          delete dragged.free;
          const parent = current.graph.nodes.find(n => n.id === target);
          if (parent) parent.collapsed = false;
        }
        relayout(false);
        dirty = true;
        notice = detaching ? "Branch detached · save to keep" : "Unsaved local edits";
      } else Object.assign(current, clone(gesture.before)); // No target: the branch snaps back into the layout.
    } else if (gesture.moved) {
      history = [...history.slice(-29), gesture.before];
      dirty = true;
      notice = "Unsaved local edits";
    }
    gesture = null;
    dragging = ""; dropTarget = ""; detaching = false;
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
   * child's facing side. Other connections are straight when a pair has one connection, mirrored
   * arcs for bidirectional or parallel connections and stacked loops for self-connections, with
   * endpoints on the pill borders so arrowheads stay visible.
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
        const ca = { x: a.x + as.w / 2, y: a.y + as.h / 2 },
          cb = { x: b.x + bs.w / 2, y: b.y + bs.h / 2 },
          sign = e.from < e.to ? 1 : -1,
          dx = (cb.x - ca.x) * sign,
          dy = (cb.y - ca.y) * sign,
          length = Math.hypot(dx, dy) || 1,
          offset = list.length === 1 ? 0 : (index - (list.length - 1) / 2) * 48,
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
    notice = "Layout updated · save to keep";
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
    if (edge) {
      selectedEdge = edge;
      selected = "";
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
      showJobs = false;
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
      notice = "Linked mentions saved";
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
    if (showLinks && id && autoRequestedFor !== id) void loadAutoLinks();
  });
  $effect(() => {
    if (!current || !dirty) return;
    const value = {revision: current.revision, snapshot: snapshot()};
    try {
      localStorage.setItem(draftKey(current.id), JSON.stringify(value));
      if (!blocked) notice = "Draft backed up on this device · Save map to persist";
    }
    catch { error = "Local draft backup is unavailable. Save map before leaving."; }
  });
  onMount(() => {
    const fonts = getComputedStyle(document.documentElement);
    uiFont = fonts.getPropertyValue("--font-ui").trim() || uiFont;
    headingFont = fonts.getPropertyValue("--heading").trim() || headingFont;
    void document.fonts?.ready.then(() => { pillWidths.clear(); fontsReady += 1; });
    void refresh()
      .then(() => {
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
    return () => { window.removeEventListener("beforeunload", guard); pane.clearTrail(section); };
  });
  let seenRevision: number | undefined;
  $effect(() => {
    if (seenRevision === undefined) seenRevision = revision;
    else if (revision !== seenRevision) {
      seenRevision = revision;
      void refresh().catch(failure);
      if (current && !dirty && !busy) void open(current.id, true);
      if (view === "network") void loadNetwork();
    }
  });
</script>

<section class="schema" aria-label="Mental schema maps" bind:this={container}>
  {#snippet mapControls()}
    <div class="toolbar">
      <label
        >Map<select
          value={current?.id ?? ""}
          disabled={busy}
          onchange={(e) => {
            if (e.currentTarget.value) void open(Number(e.currentTarget.value));
          }}
          ><option value="">Select map</option>{#each maps as m}<option
              value={m.id}>{m.title}</option
            >{/each}</select
        ></label
      >
      {#if current}<Button intent="primary" size="sm" onclick={newMap} disabled={busy}>New map</Button>{/if}
      {#if !current}
      <form class="toolbar map-create" onsubmit={e => {e.preventDefault(); void createTemplate();}}>
        <label>New map title<input id={`${pane.id}-new-map-title`} bind:value={newTitle} maxlength="200" placeholder="What are you learning?" required disabled={busy} /></label>
        <Button type="submit" intent="primary" disabled={busy || !newTitle.trim()}>Create map</Button>
        <details><summary>Advanced options</summary>
          <label>Map category<select bind:value={mode} disabled={busy}><option value="study">Study</option><option value="general">General</option><option value="jobs">Jobs</option></select></label>
          <label>Starting template<select bind:value={template}><option>Blank</option><option>Lecture</option><option>Chapter</option><option>Exam review</option></select></label>
          <label class="file">Import map JSON<input type="file" accept="application/json,.json" onchange={importFile} disabled={busy} /></label>
        </details>
      </form>
      {/if}
    </div>
    {#if subjects.length}<details><summary>Start from a subject</summary><div class="toolbar">
      {#each subjects as subject}<Button onclick={() => subjectMap(subject.id)} disabled={busy}>{subject.name} +</Button>{/each}
    </div></details>{/if}
  {/snippet}
  {#if embedded}
    {@render mapControls()}
  {:else}
    <Panel title="Mind maps" readout="">{@render mapControls()}</Panel>
  {/if}
  {#if error}<div role="alert" class="error">{error}</div>{/if}
  <div class="toolbar views" role="group" aria-label="Schema view">
    {#each [["edit", "Mind map"], ["map3d", "Graph"], ["network", "All maps"]] as [id, text]}<Button
        intent={view === id ? "primary" : "neutral"}
        aria-pressed={view === id}
        disabled={id === "map3d" && !current}
        onclick={() => {
          view = id as View3D;
        }}>{text}</Button
      >{/each}
  </div>
  {#if view === "network"}
    <div class="workspace network-workspace">
      <aside>
        <Panel title="[01] Network filters">
          <label
            >Mode<select bind:value={netMode} disabled={netBusy}
              ><option value="">All modes</option><option value="study"
                >Study</option
              ><option value="general">General</option><option value="jobs"
                >Jobs</option
              ></select
            ></label
          >
          <fieldset class="map-picks">
            <legend>Maps ({netMaps.length ? netMaps.length : "all"})</legend>
            <Button
              onclick={() => {
                netMaps = [];
              }}
              disabled={!netMaps.length}>Use all maps</Button
            >
            {#each netChoices as m (m.id)}<label class="check"
                ><input
                  type="checkbox"
                  checked={netMaps.includes(m.id)}
                  onchange={(e) => {
                    netMaps = e.currentTarget.checked
                      ? [...netMaps, m.id]
                      : netMaps.filter((id) => id !== m.id);
                  }}
                />{m.title} <small>{m.mode}</small></label
              >{:else}<p class="hint">No maps in this mode yet.</p>{/each}
          </fieldset>
          <label class="check"
            ><input type="checkbox" bind:checked={mergeLabels} />Merge unlinked
            nodes with the same kind + label</label
          >
          <p class="hint">
            Linked classes, jobs and companies always unify across maps. Label
            merge is off by default so different ideas that share a name stay
            separate.
          </p>
          <label
            >Color by<select bind:value={colorBy}
              ><option value="cluster">Cluster (connected group)</option><option
                value="kind">Node kind</option
              ><option value="map">Map (shared nodes highlighted)</option
              ></select
            ></label
          >
          <Button onclick={() => loadNetwork()} disabled={netBusy}
            >Refresh from saved maps</Button
          >
          <p class="hint" role="status">
            {netBusy
              ? "Merging maps…"
              : network
                ? `${network.maps.length} maps → ${network.nodes.length} nodes, ${network.edges.length} connections, ${network.clusters.length} clusters; ${network.unified} nodes unified across maps or labels.`
                : ""}{dirty
              ? " Unsaved edits in the open map are not included."
              : ""}
          </p>
          {#if network?.clusters.length}<ol class="clusters">
              {#each network.clusters.slice(0, 12) as c (c.id)}<li>
                  Cluster {c.id + 1}: {c.size} node{c.size === 1 ? "" : "s"},
                  hub “{c.label}”
                </li>{/each}
            </ol>{/if}
        </Panel>
      </aside>
      <main class="graph-pane">
        {#if netError}<div role="alert" class="error">{netError}</div>{/if}
        {#if pane.active}
        <SchemaNetwork
          title="Merged schema network"
          nodes={netGraph.nodes}
          links={netGraph.links}
          bind:layout={netLayout}
          selected={netSelected || null}
          onSelect={(id) => {
            netSelected = id;
          }}
        />
        {/if}
      </main>
      <aside>
        <Panel title="[03] Network inspector">
          {#if netNode}
            {@const links = neighbors(netNode.id)}
            <h2>{netNode.label}</h2>
            <Badge label={netNode.kind} />
            <p>
              Cluster {netNode.cluster + 1} · {netNode.degree} connection{netNode.degree ===
              1
                ? ""
                : "s"}
            </p>
            {#if netNode.source}<p>
                Linked {netNode.source.kind} #{netNode.source.id}
              </p>
              {#if netNode.live?.missing}<p class="error">
                  Source deleted. Unlink the node in each map below.
                </p>{/if}
              {#if netNode.kind === "job" && !netNode.live?.missing}<p>
                  Company: {netNode.live?.company ?? "Unavailable"}
                </p>
                <p>
                  Application: {netNode.live?.status ??
                    "No application recorded"}
                </p>
                <p>
                  Queue: {netNode.live?.queue_state ?? "Unavailable"}{netNode
                    .live?.closed_at
                    ? " / closed"
                    : ""}
                </p>{/if}{/if}
            {#if safeUrl(netNode.url)}<a
                href={safeUrl(netNode.url)}
                target="_blank"
                rel="noopener noreferrer">Open safe source ↗</a
              >{/if}
            <h3>Appears in</h3>
            <ul class="members">
              {#each netNode.members as m (`${m.map_id}:${m.node_id}`)}<li>
                  <strong>{mapTitle(m.map_id)}</strong>
                  <small>as “{m.label}”</small>
                  {#if m.notes}<p class="notes">{m.notes}</p>{/if}
                  <Button
                    onclick={() => editInMap(m.map_id, m.node_id)}
                    disabled={busy}>Edit in 2D editor</Button
                  >
                </li>{/each}
            </ul>
            <h3>Connections</h3>
            <ul class="members">
              {#each links.out as e (e.id)}<li>
                  → {e.label || "(unlabeled)"} → {netLabel(e.to)}
                  <small>×{e.weight} in {e.maps.map(mapTitle).join(", ")}</small
                  >
                </li>{/each}
              {#each links.in as e (e.id)}<li>
                  ← {e.label || "(unlabeled)"} ← {netLabel(e.from)}
                  <small>×{e.weight} in {e.maps.map(mapTitle).join(", ")}</small
                  >
                </li>{/each}
              {#if !links.out.length && !links.in.length}<li>
                  No connections.
                </li>{/if}
            </ul>
          {:else}<p>
              Select a node in the network or the adjacency table to see
              which maps it appears in, its live source status, and to jump to
              editing it.
            </p>{/if}
        </Panel>
      </aside>
    </div>
  {:else if current}
    <div class="toolbar mapbar">
      <label
        >Map title<input
          value={current.title}
          maxlength="200"
          disabled={!editable}
          oninput={(e) => {
            remember();
            current!.title = e.currentTarget.value;
          }}
        /></label
      >
      <Button intent="primary" onclick={save} disabled={!editable || !dirty}
        >{dirty ? "Save map · unsaved changes" : "Map saved"}</Button
      >
      <Button
        onclick={undo}
        disabled={!editable ||
          (!history.length && (!current.can_undo || dirty))}
        >Undo {history.length ? "local" : "last save"}</Button
      >
      <details><summary>Advanced map actions</summary><div class="toolbar">
        <Button onclick={download}>Export {dirty ? "local edits" : "JSON"}</Button>
        <Button onclick={() => current && open(current.id, true)} disabled={busy}>Reload saved map</Button>
        <Button aria-pressed={showLinks} onclick={() => showLinks = !showLinks}>Linked mentions{autoNew ? ` / ${autoNew} new` : ""}</Button>
        <Button onclick={removeMap} disabled={!editable}>Delete map…</Button>
      </div></details>
      <span role="status"
        >{busy
          ? "Working…"
          : blocked
            ? "Edits held safely — export or reload"
            : notice}</span
      >
    </div>
    {#if showLinks}
      <div id={`${pane.id}-schema-autolinks`} class="autolinks">
        <Panel
          title="[∞] Auto-links / linked mentions"
          readout={autoBusy
            ? "SCANNING"
            : autoView
              ? `${autoNew} NEW`
              : "LOCAL"}
        >
          <p class="intro">
            Kitty reads your notes, study items, calendar, flashcards and recent
            jobs on this machine and finds where each node is mentioned.
            <code>[[Node label]]</code> in a note is an explicit link. Linking adds
            a reference node with a “mentioned in” branch — source records stay untouched.
          </p>
          <div class="toolbar">
            <Button onclick={loadAutoLinks} disabled={autoBusy}
              >{autoBusy ? "Scanning…" : "Rescan"}</Button
            >
            <Button
              intent="primary"
              onclick={() => applyAutoLinks(autoPicked)}
              disabled={!editable || dirty || !autoPicked.length}
              >Link selected ({autoPicked.length})</Button
            >
            <Button
              onclick={() => applyAutoLinks(autoPending)}
              disabled={!editable || dirty || !autoPending.length}
              >Auto-link everything ({autoNew})</Button
            >
            {#if autoView}<span class="hint"
                >Scanned {Object.entries(autoView.scanned)
                  .map(([k, n]) => `${n} ${(REF_KINDS[k] ?? k).toLowerCase()}`)
                  .join(" · ") || "nothing yet"}</span
              >{/if}
          </div>
          {#if dirty}<p class="error">
              Save your graph before linking mentions.
            </p>{/if}
          {#if autoError}<div role="alert" class="error">{autoError}</div>{/if}
          {#if autoView && autoView.revision !== current.revision}<p
              class="hint"
            >
              The map changed since this scan — rescan to refresh.
            </p>{/if}
          {#if autoView && !autoView.suggestions.length}<p class="hint">
              No mentions found yet. Use node labels in notes, study tasks or
              calendar items — or write <code>[[Node label]]</code> in a note.
            </p>{/if}
          <ul class="mindmap">
            {#each autoView?.suggestions ?? [] as s (s.node_id)}
              {@const fresh = s.references.filter((r) => !r.linked).length}
              <li class="hub">
                <div class="hub-head">
                  <label class="check"
                    ><input
                      type="checkbox"
                      checked={autoPicked.includes(s.node_id)}
                      disabled={busy || !fresh}
                      onchange={(e) => {
                        autoPicked = e.currentTarget.checked
                          ? [...autoPicked, s.node_id]
                          : autoPicked.filter((id) => id !== s.node_id);
                      }}
                    /><span class="sr-only">Link mentions of {s.label}</span
                    ></label
                  >
                  <button
                    type="button"
                    class="hub-node"
                    onclick={() => focusNode(s.node_id)}>{s.label}</button
                  >
                  <Badge
                    label={fresh
                      ? `${fresh} new / ${s.references.length}`
                      : `all ${s.references.length} linked`}
                  />
                </div>
                <ul class="branches">
                  {#each s.references as r (`${r.kind}:${r.id}`)}
                    <li class:linked={r.linked} class:explicit={r.match === "wikilink"}>
                      <a href={`#${r.view}`} onclick={leaveFor}
                        ><span class="ref-kind"
                          >{REF_KINDS[r.kind] ?? r.kind}{r.match === "wikilink"
                            ? " · [[link]]"
                            : ""}{r.linked ? " · linked" : ""}</span
                        >{r.title}</a
                      >
                      {#if r.snippet}<small>{r.snippet}</small>{/if}
                    </li>
                  {/each}
                </ul>
              </li>
            {/each}
          </ul>
        </Panel>
      </div>
    {/if}
    {#if view === "map3d"}
      <div class="workspace map3d-workspace">
        <main class="graph-pane">
          {#if pane.active}
          <SchemaNetwork
            title={`Map / ${current.title}`}
            nodes={mapGraph.nodes}
            links={mapGraph.links}
            bind:layout={mapLayout}
            selected={selected || null}
            onSelect={(id) => {
              const n = current?.graph.nodes.find((n) => n.id === id);
              if (n) choose(n);
            }}
          />
          {/if}
          {#if dirty}<p class="hint">
              Showing unsaved local edits from the 2D editor.
            </p>{/if}
        </main>
        <aside>
          <Panel title="[03] Node inspector / read-only">
            {#if node}
              <h2>{name(node)}</h2>
              <Badge label={node.kind} />
              <p>{degreeOf(node.id)} connections in this map</p>
              {#if node.source}<p>
                  Linked {node.source.kind} #{node.source.id}
                </p>
                {#if current.live[node.id]?.missing}<p class="error">
                    Source deleted. Unlink this node in the 2D editor.
                  </p>{/if}{#if node.kind === "job" && !current.live[node.id]?.missing}<p
                  >
                    Company: {current.live[node.id]?.company ?? "Unavailable"}
                  </p>
                  <p>
                    Application: {current.live[node.id]?.status ??
                      "No application recorded"}
                  </p>{/if}{/if}
              {#if node.notes}<p class="notes">{node.notes}</p>{/if}
              {#if safeUrl((node.source ? current.live[node.id]?.url : undefined) ?? node.url)}<a
                  href={safeUrl(
                    (node.source ? current.live[node.id]?.url : undefined) ??
                      node.url,
                  )}
                  target="_blank"
                  rel="noopener noreferrer">Open safe source ↗</a
                >{/if}
              <h3>Connections</h3>
              <ul class="members">
                {#each current.graph.edges.filter((e) => e.from === node.id || e.to === node.id) as e (e.id)}
                  {@const other = current.graph.nodes.find(
                    (n) => n.id === (e.from === node.id ? e.to : e.from),
                  )!}
                  <li>
                    {e.from === node.id
                      ? `→ ${e.label || "(unlabeled)"} → ${e.to === node.id ? "itself" : name(other)}`
                      : `← ${e.label || "(unlabeled)"} ← ${name(other)}`}
                  </li>
                {:else}<li>No connections.</li>{/each}
              </ul>
              <Button
                intent="primary"
                onclick={() => editInMap(current!.id, node.id)}
                >Edit in 2D editor</Button
              >
            {:else}<p>
                Select a node in the graph or the adjacency table. Editing happens in
                the mind-map editor.
              </p>{/if}
          </Panel>
        </aside>
      </div>
    {:else}
      <div class="workspace editor-workspace">
        <details><summary>Organize</summary>
        <aside>
          <Panel title="[01] Nodes + sources">
            <label
              >Node type<select bind:value={kind} disabled={!editable}
                ><option value="topic">Topic</option><option value="class"
                  >Existing study class</option
                ><option value="idea">Idea</option><option value="skill"
                  >Skill / learning goal</option
                ><option value="company">Company (unlinked)</option><option
                  value="job">Job (unlinked note)</option
                ></select
              ></label
            >
            {#if kind === "class"}<label
                >Class<select bind:value={subjectId} disabled={!editable}
                  ><option value="">Choose existing class</option
                  >{#each subjects as s}<option value={s.id}>{s.name}</option
                    >{/each}</select
                ></label
              >
              <p class="hint">
                Classes are linked to Study, never duplicated. Create new
                classes in Study first.
              </p>
            {:else}<label
                >Label<input
                  bind:value={label}
                  maxlength="200"
                  disabled={!editable}
                  onkeydown={(e) => {
                    if (e.key === "Enter") addNode();
                  }}
                /></label
              >{/if}
            <Button
              onclick={addNode}
              disabled={!editable || current.graph.nodes.length >= 300}
              >Add node</Button
            >
            {#if current.mode === "jobs" && canImportJobs}<Button
                onclick={() => {
                  showJobs = !showJobs;
                  if (showJobs) void findJobs();
                }}
                disabled={busy}>Import tracked jobs…</Button
              >
              <p class="hint">
                Imports only factual job → at → company links. Add your own
                skills or connect study classes/topics. No eligibility claims.
              </p>{/if}
            <label
              >Search nodes<input
                bind:value={search}
                type="search"
                placeholder="Class, topic, idea…"
              /></label
            >
            <ul class="node-list">
              {#each filtered as n}<li>
                  <button
                    class:active={selected === n.id}
                    onclick={() => choose(n)}
                    oncontextmenu={(event) => deleteAtPointer(event, n)}
                    ><span>{hiddenLabel(n) ? "?" : name(n)}</span><small
                      >{n.kind}{n.source ? " / linked" : ""}</small
                    ></button
                  >
                </li>{/each}
            </ul>
            <p class="hint">
              {current.graph.nodes.length}/300 nodes · {current.graph.edges
                .length}/600 connections
            </p>
          </Panel>
          <details><summary>Add note card</summary>
            <input aria-label="Find note pages" bind:value={noteSearch} oninput={findNoteCards} type="search" />
            <Button onclick={findNoteCards}>Find</Button>
            {#each notePages as page}<Button onclick={() => addNoteCard(page)} disabled={!editable}>{page.title} +</Button>{/each}
          </details>
        </aside>
        </details>
        <main class="graph-pane" data-kitty-panel="graph" tabindex="-1">
          <div class="toolbar">
            <Button onclick={() => arrange("tree")} disabled={!editable}>Tree layout</Button>
            <Button onclick={() => quickAdd()} disabled={!editable}>{node ? "+ Child" : "+ Topic"}</Button>
            <Button onclick={() => quickAdd(current?.graph.edges.find(edge => edge.to === selected)?.from ?? "")} disabled={!editable || !node}>+ Sibling</Button>
            <Button onclick={() => node && editInline(node)} disabled={!editable || !node}>Rename</Button>
            <Button onclick={() => outline = !outline} aria-pressed={outline}>Outline</Button>
            <Button onclick={() => {recall = !recall; revealed = [];}} aria-pressed={recall}>{recall ? "End recall" : "Quiz me"}</Button>
            {#if recall}<Button disabled={!node} onclick={() => revealed = [...revealed, selected]}>Reveal selected answer</Button><Button onclick={() => revealed = current!.graph.nodes.map(n => n.id)}>Reveal all</Button>{/if}
            <Button onclick={() => zoom(1.2)} disabled={!editable}
              >Zoom +</Button
            ><Button onclick={() => zoom(1 / 1.2)} disabled={!editable}
              >Zoom −</Button
            ><Button onclick={fit} disabled={!editable}>Fit graph</Button
            ><details><summary>More layouts</summary><div class="toolbar">
              <Button onclick={() => arrange("radial")} disabled={!editable}>Radial</Button>
              <Button onclick={() => arrange("force")} disabled={!editable}>Force</Button>
            </div></details><span>{Math.round(current.viewport.zoom * 100)}%</span>
          </div>
          <p class="hint">Click a topic and type (or Ctrl+Enter) to rename. Tab adds a child; Enter adds a sibling, including while typing. Save map keeps changes in your workspace; draft backup is device-only.</p>
          {#if outline}
            <div class="map-outline" role="tree" aria-label="Mind map outline">
              {#each visibleNodes as n (n.id)}
                <div style={`padding-left: ${depth(n.id) * 20}px`} role="treeitem" aria-level={depth(n.id) + 1} aria-selected={selected === n.id}>
                  <Button onclick={() => collapse(n)} title="Collapse branch">{n.collapsed ? "▸" : "▾"}</Button>
                  <button class:active={selected === n.id} oncontextmenu={event => deleteAtPointer(event, n)} onkeydown={e => nodeKey(e, n)} onclick={() => choose(n)} ondblclick={() => openCard(n)}>{hiddenLabel(n) ? "?" : name(n)}</button>
                </div>
              {/each}
            </div>
          {/if}
          <div class="stage-wrap">
          {#if dragging}<p class="drag-hint" role="status">{detaching ? "Detached · drop anywhere to leave it floating · D to reattach" : dropTarget ? `Drop on “${name(current.graph.nodes.find(n => n.id === dropTarget)!)}” to move it there` : "Drop on a topic to move it there · D detaches the branch"}</p>{/if}
          <svg
            bind:this={stage}
            class="stage"
            aria-label="Schema graph. Drag nodes to position; drag empty space to pan. Use node list and inspector for keyboard editing."
            role="group"
            ondblclick={(e) => {if (!(e.target as Element).closest("[data-node]")) quickAdd("");}}
            onpointerdown={pick}
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
              ><marker
                id={`${pane.id}-schema-arrow`}
                markerWidth="10"
                markerHeight="8"
                refX="9"
                refY="4"
                orient="auto"
                markerUnits="strokeWidth"
                ><path d="M0,0 L10,4 L0,8 z" fill="var(--violet)" /></marker
              ><marker
                id={`${pane.id}-schema-arrow-active`}
                markerWidth="10"
                markerHeight="8"
                refX="9"
                refY="4"
                orient="auto"
                markerUnits="strokeWidth"
                ><path d="M0,0 L10,4 L0,8 z" fill="var(--accent)" /></marker
              ></defs
            >
            <g
              transform={`translate(${current.viewport.x},${current.viewport.y}) scale(${current.viewport.zoom})`}
            >
              {#each current.graph.edges.filter(e => visibleNodes.some(n => n.id === e.from) && visibleNodes.some(n => n.id === e.to) && !(e.to === dragging && treeEdges.has(e.id) && (detaching || (dropTarget && dropTarget !== e.from)))) as e (e.id)}
                {@const shape = geometry.get(e.id)}
                {#if shape}<path
                    d={shape.d}
                    class="edge"
                    class:branch={shape.tree}
                    class:active={selectedEdge === e.id}
                    style:--b={branchColor(current.graph.nodes.find(n => n.id === e.to)!)}
                    marker-end={shape.tree ? undefined : selectedEdge === e.id
                      ? `url(#${pane.id}-schema-arrow-active)`
                      : `url(#${pane.id}-schema-arrow)`}
                  /><path d={shape.d} class="edge-hit" data-edge={e.id} />{/if}
              {/each}
              {#if dragging && dropTarget}<path class="snap-preview" d={snapPath(dropTarget, dragging)} />{/if}
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
                  aria-label={`${n.kind}: ${hiddenLabel(n) ? "Recall" : name(n)}${folded ? `, ${folded} hidden` : ""}. Tab child, Enter sibling, arrows move, Control arrows reorder, Control Enter or type to rename, Space collapse.`}
                  class:selected={selected === n.id}
                  class:root
                  class:drop-target={dropTarget === n.id}
                  class:dragging={dragging === n.id}
                  class:detaching={detaching && dragging === n.id}
                  class="graph-node"
                  style:--b={branchColor(n)}
                  ondblclick={() => openCard(n)}
                  onpointerdown={(e) => start(e, n.id)}
                  onkeydown={(e) => nodeKey(e, n)}
                  onclick={() => choose(n)}
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
              {#each current.graph.edges as e (e.id)}
                {@const shape = geometry.get(e.id)}
                {#if shape && e.label}<text
                    x={shape.lx}
                    y={shape.ly}
                    class="edge-label"
                    class:active={selectedEdge === e.id}
                    data-edge={e.id}
                    text-anchor="middle"
                    >{e.label.length > 32
                      ? e.label.slice(0, 31) + "…"
                      : e.label}</text
                  >{/if}
              {/each}
              <!-- The dragged pill is redrawn on top so it stays visible over the pill it snaps into. -->
              {#if dragging}<use href={`#${pane.id}-node-${dragging}`} class="drag-ghost" />{/if}
            </g>
          </svg>
          </div>
          <p class="hint">
            Tab child · Enter sibling · arrows move · Ctrl+↑/↓ reorder · Ctrl+Enter or type to rename · Space fold
          </p>
          <details><summary>Connections</summary>
          <Panel title="[02] Directional connections">
            <div class="toolbar">
              <label
                >From<select bind:value={edgeFrom} disabled={!editable}
                  ><option value="">Choose node</option
                  >{#each current.graph.nodes as n}<option value={n.id}
                      >{name(n)}</option
                    >{/each}</select
                ></label
              ><span>→</span><label
                >To<select bind:value={edgeTo} disabled={!editable}
                  ><option value="">Choose node</option
                  >{#each current.graph.nodes as n}<option value={n.id}
                      >{name(n)}</option
                    >{/each}</select
                ></label
              ><label
                >Relationship<input
                  bind:value={edgeLabel}
                  maxlength="200"
                  disabled={!editable}
                /></label
              ><Button
                onclick={connect}
                disabled={!editable || !edgeFrom || !edgeTo}>Connect</Button
              >
            </div>
            <ul class="edges">
              {#each current.graph.edges as e}<li>
                  <button
                    onclick={() => {
                      selectedEdge = selectedEdge === e.id ? "" : e.id;
                    }}
                    >{name(current.graph.nodes.find((n) => n.id === e.from)!)} → {e.label ||
                      "(unlabeled)"} → {name(
                      current.graph.nodes.find((n) => n.id === e.to)!,
                    )}</button
                  >{#if selectedEdge === e.id}<div class="edge-edit">
                      <label
                        >From<select
                          value={e.from}
                          onchange={(event) =>
                            editEdge(e.id, "from", event.currentTarget.value)}
                          disabled={!editable}
                          >{#each current.graph.nodes as n}<option value={n.id}
                              >{name(n)}</option
                            >{/each}</select
                        ></label
                      ><label
                        >To<select
                          value={e.to}
                          onchange={(event) =>
                            editEdge(e.id, "to", event.currentTarget.value)}
                          disabled={!editable}
                          >{#each current.graph.nodes as n}<option value={n.id}
                              >{name(n)}</option
                            >{/each}</select
                        ></label
                      ><label
                        >Label<input
                          value={e.label}
                          maxlength="200"
                          onchange={(event) =>
                            editEdge(e.id, "label", event.currentTarget.value)}
                          disabled={!editable}
                        /></label
                      ><Button
                        onclick={() => removeEdge(e.id)}
                        disabled={!editable}>Remove connection</Button
                      >
                    </div>{/if}
                </li>{/each}
            </ul>
          </Panel>
          </details>
        </main>
        {#if node && !hiddenLabel(node)}
        <aside>
          <Panel title="Node">
            {#if node}
              <h2>{name(node)}</h2>
              <Badge label={node.kind} />
              <div class="toolbar">
                <Button onclick={() => collapse(node)}>{node.collapsed ? "Expand" : "Collapse"}</Button>
                <Button onclick={detachSelected} disabled={!editable || isRoot(node)} title="Break this branch off as a floating piece (or press D while dragging it)">Detach branch</Button>
                <Button onclick={() => studyBranch("flashcards")}>Make flashcards</Button>
                <Button onclick={() => studyBranch("quiz")}>Quiz this branch</Button>
                <Button onclick={startBranchSession} disabled={!editable}>Start session</Button>
                {#if node.source?.kind === "note_page" || node.map_id}<Button onclick={() => openCard(node)}>Open ↗</Button>{/if}
              </div>
              <label>Move under<select value={current.graph.edges.find(edge => edge.to === node.id)?.from ?? ""} disabled={!editable} onchange={e => reparent(e.currentTarget.value)}>
                <option value="">No parent (root topic)</option>
                {#each current.graph.nodes.filter(item => !descendants(node.id).has(item.id)) as parent}<option value={parent.id}>{name(parent)}</option>{/each}
              </select></label>
              <label>Linked map<select value={node.map_id ?? ""} onchange={e => {remember(); node.map_id = e.currentTarget.value ? Number(e.currentTarget.value) : null;}}><option value="">None</option>{#each maps.filter(m => m.id !== current!.id) as m}<option value={m.id}>{m.title}</option>{/each}</select></label>
              {#if node.source}<p>
                  Linked {node.source.kind} #{node.source.id}
                </p>
                {#if current.live[node.id]?.missing}<p class="error">
                    Source deleted. Unlink this node before saving.
                  </p>{/if}{#if node.kind === "job"}<p>
                    Company: {current.live[node.id]?.company ?? "Unavailable"}
                  </p>
                  <p>
                    Application: {current.live[node.id]?.status ??
                      "No application recorded"}
                  </p>
                  <p>
                    Queue: {current.live[node.id]?.queue_state ??
                      "Unavailable"}{current.live[node.id]?.closed_at
                      ? " / closed"
                      : ""}
                  </p>{/if}
                <p class="hint">
                  Source title/status refresh on reload. Map notes do not modify
                  source records.
                </p>
                <Button onclick={unlink} disabled={!editable}
                  >Unlink / keep as map note</Button
                >{/if}
              <label
                >{node.source ? "Local fallback label" : "Label"}<input
                  bind:value={draftLabel}
                  maxlength="200"
                  disabled={!editable}
                  oninput={e => {draftLabel = e.currentTarget.value; updateNode();}} /></label
              >
              <label
                >Notes<textarea
                  bind:value={draftNotes}
                  maxlength="10000"
                  rows="7"
                  disabled={!editable}
                  oninput={e => {draftNotes = e.currentTarget.value; updateNode();}}></textarea></label
              >
              <label
                >Local reference URL<input
                  bind:value={draftUrl}
                  type="url"
                  maxlength="4000"
                  disabled={!editable}
                  placeholder="https://…"
                  oninput={e => {draftUrl = e.currentTarget.value; updateNode();}} /></label
              >
              {#if safeUrl((node.source ? current.live[node.id]?.url : undefined) ?? node.url)}<a
                  href={safeUrl(
                    (node.source ? current.live[node.id]?.url : undefined) ??
                      node.url,
                  )}
                  target="_blank"
                  rel="noopener noreferrer">Open safe source ↗</a
                >{/if}
              <div class="toolbar">
                <Button
                  onclick={() => {
                    edgeFrom = node.id;
                  }}
                  disabled={!editable}>Use as From</Button
                ><Button
                  onclick={() => {
                    edgeTo = node.id;
                  }}
                  disabled={!editable}>Use as To</Button
                >
              </div>
              <Button onclick={deleteNode} disabled={!editable}
                >Delete node + its connections</Button
              >
            {:else}<p>
                Select a node in the graph or searchable list. Full labels,
                notes, safe source links and live job status appear here.
              </p>{/if}
          </Panel>
        </aside>
        {/if}
      </div>
    {/if}
    {#if showJobs && current.mode === "jobs" && canImportJobs}<Panel
        title="Import selected tracked jobs / factual sources only"
        ><div class="toolbar">
          <label
            >Search title or company<input
              bind:value={jobSearch}
              maxlength="200"
              onkeydown={(e) => {
                if (e.key === "Enter") void findJobs();
              }}
            /></label
          ><Button onclick={() => findJobs()} disabled={busy}>Search</Button>
        </div>
        {#if dirty}<p class="error">
            Save your graph before importing tracked jobs.
          </p>{/if}
        <ul class="jobs">
          {#each jobs as job}<li>
              <label
                ><input
                  type="checkbox"
                  checked={jobIds.includes(job.id)}
                  disabled={busy ||
                    (!jobIds.includes(job.id) && jobIds.length >= 50)}
                  onchange={(e) => {
                    jobIds = e.currentTarget.checked
                      ? [...jobIds, job.id]
                      : jobIds.filter((id) => id !== job.id);
                  }}
                /><span
                  >{job.title} / {job.company}<small
                    >{job.status ?? "No application"} · {job.queue_state}</small
                  ></span
                ></label
              >
            </li>{/each}
        </ul>
        <div class="toolbar">
          <Button
            onclick={() => findJobs(Math.max(0, jobOffset - 50))}
            disabled={busy || jobOffset === 0}>Previous</Button
          ><Button
            onclick={() => findJobs(jobOffset + 50)}
            disabled={busy || !moreJobs}>Next</Button
          ><Button
            intent="primary"
            onclick={importJobs}
            disabled={!editable || dirty || !jobIds.length}
            >Import {jobIds.length} selected jobs</Button
          >
        </div>
        <p class="hint">
          Repeated imports reuse the same job/company nodes and “at”
          connections. Application statuses remain live; skills and requirements
          are never invented.
        </p></Panel
      >{/if}
  {:else}<p class="empty">
      Name your map and choose Create map. Select a topic, then add a child or sibling.
      Type to rename; use Save map to keep your work.
    </p>{/if}
  {#if current}
    <MindMapFeedback
      title={current.title}
      graph={{
        nodes: current.graph.nodes.map(n => ({id: n.id, label: name(n), notes: n.notes})),
        edges: current.graph.edges.map(e => ({from: e.from, to: e.to, label: e.label})),
      }}
      {demo}
    />
  {/if}
</section>

<style>
  .editor-workspace { display: flex; align-items: flex-start; flex-wrap: wrap; }
  .editor-workspace > main { flex: 1 1 440px; min-width: 0; }
  .editor-workspace > details { flex: 0 0 auto; max-width: 250px; }
  .editor-workspace > aside { flex: 0 1 260px; }
  .map-outline { max-height: 340px; overflow: auto; padding: 12px; border: 1px solid var(--border); }
  .map-outline button { color: var(--ink); background: var(--surface); border: 0; padding: 6px; text-align: left; }
  .map-outline button.active { color: var(--accent); }
  .schema {
    display: grid;
    gap: 14px;
    color: var(--ink);
    font-family: var(--br-font-ui, monospace);
  }
  .intro,
  .hint {
    color: var(--muted);
    font-size: 12px;
    line-height: 1.65;
  }
  .intro {
    margin: 0 0 12px;
  }
  .toolbar {
    display: flex;
    flex-wrap: wrap;
    align-items: end;
    gap: 8px;
  }
  .mapbar {
    position: sticky;
    top: var(--workbench-top, 0px);
    z-index: 5;
    padding: 12px;
    border: 1px solid var(--border);
    background: var(--surface);
  }
  .mapbar span {
    font-size: 11px;
    color: var(--muted);
    align-self: center;
  }
  label {
    display: grid;
    gap: 5px;
    font-size: 11px;
    min-width: 0;
    color: var(--muted);
  }
  input,
  select,
  textarea {
    width: 100%;
    min-width: 0;
    border: 1px solid var(--border);
    border-radius: 0;
    background: var(--surface);
    color: var(--ink);
    padding: 8px;
    font: inherit;
  }
  textarea {
    resize: vertical;
  }
  .workspace {
    display: grid;
    grid-template-columns: minmax(200px, 235px) minmax(320px, 1fr) minmax(
        210px,
        260px
      );
    gap: 12px;
    align-items: start;
  }
  aside {
    min-width: 0;
  }
  aside :global(.br-panel-body) {
    display: grid;
    gap: 10px;
  }
  aside label {
    margin: 10px 0;
  }
  h2 {
    font-size: 16px;
    overflow-wrap: anywhere;
  }
  .graph-pane {
    min-width: 0;
  }
  .stage {
    display: block;
    width: 100%;
    height: 510px;
    margin: 8px 0;
    border: 1px solid var(--border);
    background-color: var(--surface);
    background-image: radial-gradient(var(--border) 1px, transparent 1px);
    background-size: 20px 20px;
    touch-action: none;
    user-select: none;
    cursor: grab;
  }
  /* Q44: rounded pills with a branch-coloured dot; parentless nodes are inverted ink roots. */
  .graph-node {
    cursor: move;
    outline: none;
  }
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
  .graph-node .label {
    fill: var(--ink);
    font: 500 12.5px var(--font-ui);
    pointer-events: none;
  }
  .graph-node.root .pill { fill: var(--ink); stroke: none; }
  .graph-node.root:hover .pill { fill: color-mix(in srgb, var(--ink) 88%, var(--solid-void)); }
  .graph-node.root.selected .pill,
  .graph-node.root:focus-visible .pill { stroke: var(--accent); stroke-width: 2.5; filter: drop-shadow(0 0 8px color-mix(in srgb, var(--accent) 45%, transparent)); }
  .graph-node.root .label { fill: var(--solid-void); font: 600 13px var(--heading); }
  .graph-node .fold circle { fill: color-mix(in srgb, var(--b) 40%, var(--solid-panel)); }
  .graph-node.root .fold circle { fill: var(--solid-void); }
  .graph-node .fold text { fill: var(--ink); font: 600 9px var(--font-mono); pointer-events: none; }
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
  /* Q65 B: the pill a drag would snap into lights up; a detached branch shows a dashed outline. */
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
  .stage-wrap { position: relative; }
  .drag-hint {
    position: absolute;
    left: 50%;
    top: 18px;
    z-index: 1;
    transform: translateX(-50%);
    margin: 0;
    padding: 5px 12px;
    border: 1px solid var(--hairline);
    border-radius: 999px;
    background: color-mix(in srgb, var(--solid-raised) 80%, transparent);
    color: var(--muted);
    font-size: 12px;
    white-space: nowrap;
    pointer-events: none;
  }
  @media (prefers-reduced-motion: reduce) { .graph-node .pill { transition: none; } }
  .edge {
    fill: none;
    stroke: var(--violet);
    stroke-width: 2;
    pointer-events: none;
  }
  .edge.branch {
    stroke: var(--b);
    stroke-width: 1.6;
    stroke-opacity: 0.6;
  }
  .edge.active {
    stroke: var(--accent);
    stroke-width: 3;
  }
  .edge-hit {
    fill: none;
    stroke: transparent;
    stroke-width: 14;
    pointer-events: stroke;
    cursor: pointer;
  }
  .edge-label {
    fill: var(--ink);
    font: 10px var(--br-font-ui, monospace);
    paint-order: stroke;
    stroke: var(--surface);
    stroke-width: 5px;
    cursor: pointer;
  }
  .edge-label.active {
    fill: var(--accent);
  }
  .views {
    align-items: center;
  }
  .map3d-workspace {
    grid-template-columns: minmax(320px, 1fr) minmax(220px, 280px);
  }
  .network-workspace {
    grid-template-columns: minmax(0, 1fr) minmax(230px, 290px);
  }
  .network-workspace > main {
    grid-column: 1;
    grid-row: 1 / span 2;
  }
  .network-workspace > aside {
    grid-column: 2;
  }
  fieldset {
    border: 1px solid var(--border);
    margin: 0;
    padding: 10px;
    display: grid;
    gap: 6px;
    max-height: 260px;
    overflow: auto;
  }
  legend {
    font-size: 11px;
    color: var(--muted);
    padding: 0 4px;
  }
  aside label.check {
    display: flex;
    align-items: center;
    gap: 8px;
    margin: 0;
    color: var(--ink);
  }
  label.check input {
    width: auto;
  }
  label.check small {
    display: inline;
    margin: 0;
  }
  h3 {
    font-size: 12px;
    color: var(--violet);
    margin: 12px 0 4px;
    text-transform: uppercase;
    letter-spacing: 0.06em;
  }
  .members,
  .clusters {
    list-style: none;
    padding: 0;
    margin: 0;
    display: grid;
    gap: 6px;
    font-size: 12px;
    max-height: 340px;
    overflow: auto;
  }
  .clusters {
    color: var(--muted);
    font-size: 11px;
  }
  .members li {
    border: 1px solid var(--border);
    padding: 8px;
    overflow-wrap: anywhere;
    display: grid;
    gap: 6px;
  }
  .notes {
    white-space: pre-wrap;
    color: var(--muted);
    font-size: 12px;
    margin: 4px 0;
    overflow-wrap: anywhere;
  }
  .node-list,
  .edges,
  .jobs {
    list-style: none;
    padding: 0;
    margin: 10px 0;
    max-height: 320px;
    overflow: auto;
  }
  .node-list button,
  .edges > li > button {
    width: 100%;
    text-align: left;
    border: 1px solid var(--border);
    border-radius: 0;
    background: var(--surface);
    color: var(--ink);
    padding: 9px;
    margin-bottom: 4px;
    font: inherit;
    overflow-wrap: anywhere;
    cursor: pointer;
  }
  .node-list button.active {
    border-color: var(--accent);
  }
  small {
    display: block;
    color: var(--muted);
    margin-top: 5px;
    font-size: 10px;
  }
  .edge-edit {
    display: grid;
    gap: 8px;
    padding: 10px;
    border: 1px solid var(--violet);
    margin-bottom: 10px;
  }
  .error {
    padding: 12px;
    border: 1px solid var(--accent);
    color: var(--ink);
    overflow-wrap: anywhere;
  }
  .file input {
    max-width: 210px;
  }
  .jobs li {
    padding: 7px;
    border-bottom: 1px solid var(--border);
  }
  .jobs label {
    display: flex;
    align-items: start;
    gap: 10px;
  }
  .jobs input {
    width: auto;
    margin-top: 3px;
  }
  a {
    color: var(--violet);
    overflow-wrap: anywhere;
    display: block;
    margin: 10px 0;
  }
  .empty {
    padding: 40px;
    border: 1px solid var(--border);
    color: var(--muted);
    text-align: center;
  }
  .autolinks code {
    color: var(--violet);
  }
  .mindmap {
    list-style: none;
    padding: 0;
    margin: 12px 0 0;
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
    gap: 14px;
    max-height: 560px;
    overflow: auto;
  }
  .hub {
    min-width: 0;
  }
  .hub-head {
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .hub-head .check {
    margin: 0;
  }
  .hub-head input {
    width: auto;
  }
  .hub-node {
    flex: 1;
    min-width: 0;
    text-align: left;
    border: 2px solid var(--accent);
    border-radius: 0;
    background: var(--surface);
    color: var(--ink);
    padding: 8px 10px;
    font: inherit;
    font-weight: 600;
    overflow-wrap: anywhere;
    cursor: pointer;
  }
  .branches {
    list-style: none;
    margin: 0 0 0 18px;
    padding: 6px 0 0 14px;
    border-left: 2px solid var(--violet);
    display: grid;
    gap: 6px;
  }
  .branches li {
    position: relative;
    border: 1px solid var(--border);
    padding: 7px 9px;
    background: var(--surface);
    overflow-wrap: anywhere;
  }
  .branches li::before {
    content: "";
    position: absolute;
    left: -15px;
    top: 16px;
    width: 14px;
    border-top: 2px solid var(--violet);
  }
  .branches li.explicit {
    border-color: var(--violet);
  }
  .branches li.linked {
    opacity: 0.62;
  }
  .branches a {
    display: block;
    margin: 0;
    color: var(--ink);
    font-size: 12px;
    text-decoration: none;
  }
  .branches a:hover,
  .branches a:focus-visible {
    color: var(--accent);
  }
  .ref-kind {
    display: block;
    color: var(--violet);
    font-size: 9px;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    margin-bottom: 2px;
  }
  .branches small {
    margin-top: 4px;
    line-height: 1.5;
  }
  @media (max-width: 1200px) {
    .workspace {
      grid-template-columns: 210px minmax(300px, 1fr);
    }
    .map3d-workspace,
    .network-workspace {
      grid-template-columns: minmax(0, 1fr);
    }
    .workspace > aside:last-child {
      grid-column: 1 / -1;
    }
    .network-workspace > aside,
    .network-workspace > main {
      grid-column: 1;
      grid-row: auto;
    }
  }
  @media (max-width: 760px) {
    .workspace,
    .network-workspace {
      grid-template-columns: minmax(0, 1fr);
    }
    .workspace > aside:last-child,
    .network-workspace > aside,
    .network-workspace > main {
      grid-column: auto;
      grid-row: auto;
    }
    .stage {
      height: 430px;
    }
  }
</style>
