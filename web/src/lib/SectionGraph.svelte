<script module lang="ts">
  import type { GraphNode } from "./graph3d";

  export type SectionNode = GraphNode & {
    entity: "track" | "job" | "deck" | "flashcard_note" | "folder" | "notebook" | "page" | "subject" | "project" | "concept" | "category" | "source" | "opportunity";
    ref: number | string | null;
  };
</script>

<script lang="ts">
  import { onMount, untrack } from "svelte";
  import { Button, Panel } from "@nunera/bracket";
  import { api } from "./api";
  import { mountGraph, type GraphHandle, type GraphLayout, type GraphLink } from "./graph3d";
  import { GRAPH_SETTINGS_EVENT, graphLayout, graphPhysics, graphLook } from "./graphPreferences";
  import { prefersReducedMotion } from "./appearance";
  import GraphLookToggle from "./GraphLookToggle.svelte";
  import { useWorkspaceContext } from "./workspaceContext.svelte";

  const pane = useWorkspaceContext();

  type SectionData = { nodes: SectionNode[]; links: GraphLink[]; total: number; shown: number };
  let {
    section,
    revision = 0,
    onselect,
    ondelete,
    focus = null,
    listToggle = true,
    height = "min(70vh, 850px)",
  }: {
    section: "jobs" | "flashcards" | "notes" | "study" | "opportunities";
    revision?: number;
    onselect?: (node: SectionNode) => void;
    ondelete?: (node: SectionNode) => void | Promise<void>;
    focus?: string | null;
    listToggle?: boolean;
    height?: string;
  } = $props();

  const titles = { jobs: "Jobs", flashcards: "Flashcards", notes: "Notes", study: "Study", opportunities: "Opportunities" };
  const empty = {
    jobs: "No jobs or configured tracks yet. Discovered jobs will appear here without needing to be saved.",
    flashcards: "No decks or notes yet. Create a deck and add notes to begin your flashcards graph.",
    notes: "No folders, notebooks or pages yet. Create a notebook to begin your notes graph.",
    study: "No subjects or projects yet. Create one to begin your study graph.",
    opportunities: "No opportunities yet. Add a project or discover public sources.",
  };
  const layouts: { id: GraphLayout; label: string }[] = [
    { id: "clusters", label: "Grouped" },
    { id: "force", label: "Force" },
    { id: "tree", label: "Tree" },
    { id: "sphere", label: "Sphere" },
  ];

  let graph = $state.raw<SectionData | null>(null);
  let loading = $state(true);
  let error = $state("");
  let notice = $state("");
  let mode = $state<"graph" | "list">("graph");
  let layout = $state<GraphLayout>(graphLayout());
  let selected = $state<SectionNode | null>(null);
  let query = $state("");
  let container = $state<HTMLDivElement>();
  let handle = $state.raw<GraphHandle | null>(null);
  let shownGraph: SectionData | null = null;
  let listLimit = $state(60);
  $effect(() => { void query; listLimit = 60; });
  onMount(() => {
    const syncSettings = () => {
      layout = graphLayout();
      handle?.setPhysics(graphPhysics());
      void handle?.setLook(graphLook()).catch(cause => { notice = (cause as Error).message; });
    };
    window.addEventListener(GRAPH_SETTINGS_EVENT, syncSettings);
    return () => window.removeEventListener(GRAPH_SETTINGS_EVENT, syncSettings);
  });
  const hasNodes = $derived(!!graph?.nodes.length);
  const matches = $derived.by(() => {
    const term = query.trim().toLocaleLowerCase();
    return (graph?.nodes ?? []).filter((node) =>
      !term || `${node.label} ${node.entity} ${node.detail ?? ""}`.toLocaleLowerCase().includes(term),
    );
  });

  function choose(node: SectionNode) {
    if (!pane.active) return;
    selected = node;
    handle?.focus(node.id);
    onselect?.(node);
  }
  function fit() {
    selected = null;
    handle?.focus(null);
  }

  $effect(() => {
    const currentSection = section;
    const currentRevision = revision;
    const currentFocus = focus;
    void currentRevision;
    let active = true;
    loading = true;
    error = "";
    graph = null;
    selected = null;
    query = "";
    const params = new URLSearchParams();
    if(currentFocus)params.set("focus",currentFocus);
    void api<SectionData>(`/atlas/sections/${currentSection}${params.size ? `?${params}` : ""}`).then(
      (result) => {
        if (active) {
          graph = result;
          loading = false;
        }
      },
      (cause) => {
        if (active) {
          error = cause instanceof Error ? cause.message : "The graph could not be loaded.";
          loading = false;
        }
      },
    );
    return () => { active = false; };
  });

  $effect(() => {
    const target = container;
    if (!target || !hasNodes || mode !== "graph") return;
    let disposed = false;
    let mounted: GraphHandle | null = null;
    const initial = untrack(() => graph!);
    const initialLayout = untrack(() => layout);
    notice = "";
    void mountGraph(target, {
      nodes: initial.nodes,
      links: initial.links,
      layout: initialLayout,
      look: graphLook(),
      physics: graphPhysics(),
      reducedMotion: prefersReducedMotion(),
      active: untrack(() => pane.active),
      onSelect: (node) => {
        if (!pane.active) return;
        const entity = untrack(() => graph?.nodes.find((item) => item.id === node.id));
        if (entity) choose(entity);
      },
      onContextMenu: (node, event) => {
        if (!pane.active) return;
        const entity = untrack(() => graph?.nodes.find(item => item.id === node.id));
        if (entity && ondelete) { event.preventDefault(); void ondelete(entity); }
      },
    }).then(
      (result) => {
        if (disposed) {
          result.destroy();
          return;
        }
        mounted = result;
        shownGraph = initial;
        handle = result;
      },
      (cause) => {
        if (disposed) return;
        notice = `${cause instanceof Error ? cause.message : "Graph rendering is unavailable."} Use the keyboard-accessible list instead.`;
        mode = "list";
      },
    );
    return () => {
      disposed = true;
      mounted?.destroy();
      handle = null;
    };
  });
  $effect(() => {
    if (handle && graph && graph !== shownGraph) {
      shownGraph = graph;
      handle.update(graph);
    }
  });
  $effect(() => {
    handle?.setLayout(layout);
  });
  $effect(() => {
    handle?.setActive(pane.active);
  });
</script>

<Panel
  title={titles[section]}
  readout={graph ? `${graph.shown} / ${graph.total}` : undefined}
  class="section-graph-panel"
  aria-label={titles[section]}
>
  <div class="section-graph" data-kitty-panel="graph" tabindex="-1" role="region" aria-label={titles[section]}>
    {#if loading}
      <p class="message" role="status">Loading {section} connections…</p>
    {:else if error}
      <p class="message error" role="alert">{error}</p>
    {:else if !hasNodes}
      <p class="message">{empty[section]}</p>
    {:else}
      <div class="controls">
        <GraphLookToggle />
        {#if listToggle}
        <div class="view-controls" role="group" aria-label="Graph presentation">
          <Button size="sm" variant={mode === "graph" ? "soft" : "ghost"} aria-pressed={mode === "graph"} onclick={() => mode = "graph"}>Graph</Button>
          <Button size="sm" variant={mode === "list" ? "soft" : "ghost"} aria-pressed={mode === "list"} onclick={() => mode = "list"}>List</Button>
        </div>
        {/if}
        <label class="layout">Layout
          <select bind:value={layout} disabled={mode === "list"}>
            {#each layouts as item}<option value={item.id}>{item.label}</option>{/each}
          </select>
        </label>
        <Button size="sm" variant="ghost" onclick={fit} disabled={mode !== "graph" || !handle}>Fit all</Button>
      </div>
      {#if graph!.shown < graph!.total}<p class="counts">{graph!.shown} of {graph!.total} nodes · open a group to explore more</p>{/if}
      {#if notice}<p class="notice" role="status">{notice}</p>{/if}
      {#if mode === "graph"}
        <div class="canvas" style:height={height} bind:this={container} aria-hidden="true"></div>
      {/if}
      <details class="node-list" open={mode === "list"}>
        <summary>Browse ({graph!.shown})</summary>
        <label class="search">Find a node<input type="search" bind:value={query} placeholder="Search names or types" /></label>
        {#if matches.length}
          <ul aria-label={`${titles[section]} nodes`}>
            {#each matches.slice(0, listLimit) as node (node.id)}
              <li>
                <button class:hub={node.kind === "hub"} class:current={selected?.id === node.id} aria-pressed={selected?.id === node.id} onclick={() => choose(node)}
                  oncontextmenu={(event) => { if (ondelete && !window.getSelection()?.toString()) { event.preventDefault(); void ondelete(node); } }}>
                  <span>{node.label}</span>
                  <small>{node.entity}{node.detail ? ` · ${node.detail}` : ""}</small>
                </button>
              </li>
            {/each}
          </ul>
          {#if matches.length > listLimit}
            <Button size="sm" variant="ghost" onclick={() => listLimit += 60}>Show next {Math.min(60, matches.length - listLimit)} · {listLimit} of {matches.length}</Button>
          {/if}
        {:else}<p class="message">No nodes match your search.</p>{/if}
      </details>
    {/if}
  </div>
</Panel>

<style>
  .section-graph { display: grid; gap: 0.65rem; min-width: 0; }
  .section-graph:global([data-panel-fullscreen]) { display: flex; flex-direction: column; }
  .section-graph:global([data-panel-fullscreen]) .canvas { flex: 1; height: auto !important; min-height: 330px; }
  .counts, .notice, .message { margin: 0; }
  .controls, .view-controls { display: flex; align-items: center; flex-wrap: wrap; gap: 0.4rem; }
  .layout { display: flex; align-items: center; gap: 0.45rem; margin-left: auto; color: var(--muted); font-size: 0.8rem; }
  select, input { min-width: 0; border: 1px solid var(--border-fine, var(--border)); border-radius: 0.35rem; background: var(--surface); color: var(--ink); font: inherit; padding: 0.4rem 0.55rem; }
  .counts { color: var(--muted); font-size: 0.75rem; line-height: 1.5; }
  .canvas { height: 330px; width: 100%; min-width: 0; overflow: hidden; border: 1px solid var(--border-fine, var(--border)); border-radius: 0.5rem; background: var(--canvas); }
  .message { padding: 0.7rem 0; color: var(--muted); }
  .error, .notice { color: var(--accent); font-size: 0.85rem; }
  summary { cursor: pointer; font-size: 0.82rem; padding: 0.4rem 0; }
  .search { display: grid; gap: 0.35rem; color: var(--muted); font-size: 0.8rem; margin: 0.5rem 0; }
  ul { list-style: none; padding: 0; margin: 0; max-height: 330px; overflow: auto; display: grid; gap: 0.25rem; }
  li { min-width: 0; }
  li button { display: grid; gap: 0.2rem; width: 100%; text-align: left; border: 1px solid transparent; border-radius: 0.35rem; padding: 0.45rem 0.6rem; background: transparent; color: var(--ink); cursor: pointer; font: inherit; font-size: 0.82rem; overflow-wrap: anywhere; }
  li button { transition: background-color var(--motion-fast, 140ms), border-color var(--motion-fast, 140ms); }
  li button:hover, li button.current { background: var(--surface); border-color: var(--border); }
  li button.hub > span { font-weight: 600; }
  li small { color: var(--muted); font-size: 0.73rem; }
  button:focus-visible, input:focus-visible, select:focus-visible, summary:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
  @media (max-width: 600px) { .canvas { height: 290px; } .layout { margin-left: 0; } }
  @media (prefers-reduced-motion: reduce) { li button { transition: none; } }
</style>
