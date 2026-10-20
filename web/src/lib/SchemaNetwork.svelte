<script lang="ts">
  import { onMount } from "svelte";
  import { Panel, Button } from "@nunera/bracket";
  import {
    mountGraph,
    webglAvailable,
    type GraphHandle,
    type GraphLayout,
    type GraphLink,
    type GraphNode,
    type GraphPhysics,
  } from "./graph3d";
  import { GRAPH_SETTINGS_EVENT, graphLayout, graphPhysics } from "./graphPreferences";
  import { useWorkspaceContext } from "./workspaceContext.svelte";

  const pane = useWorkspaceContext();
  // Read-only 3D view of a schema graph plus an equivalent searchable
  // adjacency table: every 3D interaction has a keyboard/screen-reader path.
  let {
    title,
    nodes,
    links,
    selected = null,
    layout = $bindable<GraphLayout>(graphLayout()),
    onSelect,
  }: {
    title: string;
    nodes: GraphNode[];
    links: GraphLink[];
    selected?: string | null;
    layout?: GraphLayout;
    onSelect: (id: string) => void;
  } = $props();
  const LAYOUTS: [GraphLayout, string][] = [
    ["force", "Force"],
    ["tree", "Tree"],
    ["clusters", "Clusters"],
    ["sphere", "Sphere"],
  ];
  const PAGE = 60;
  let host = $state<HTMLDivElement>(),
    handle = $state.raw<GraphHandle | null>(null),
    problem = $state(""),
    loading = $state(true),
    hovered = $state.raw<{ id: string; label: string; group: string } | null>(
      null,
    ),
    physics = $state<GraphPhysics>(graphPhysics()),
    query = $state(""),
    limit = $state(PAGE);
  let shownNodes: GraphNode[] | null = null,
    shownLinks: GraphLink[] | null = null,
    shownLayout: GraphLayout | null = null,
    shownSelected: string | null = null;
  // 3d-force-graph mutates node/link objects, so it only ever receives copies.
  const copy = () => ({
    nodes: nodes.map((n) => ({ ...n })),
    links: links.map((l) => ({ ...l })),
  });
  const index = $derived.by(() => {
    const byId = new Map(nodes.map((n) => [n.id, n])),
      out = new Map<string, GraphLink[]>(),
      incoming = new Map<string, GraphLink[]>();
    for (const l of links) {
      out.set(l.source, [...(out.get(l.source) ?? []), l]);
      if (l.target !== l.source)
        incoming.set(l.target, [...(incoming.get(l.target) ?? []), l]);
    }
    return { byId, out, incoming };
  });
  const degree = (id: string) =>
    (index.out.get(id)?.length ?? 0) + (index.incoming.get(id)?.length ?? 0);
  const rows = $derived.by(() => {
    const needle = query.trim().toLowerCase();
    return nodes
      .filter((n) =>
        `${n.label} ${n.group} ${n.kind ?? ""} ${n.detail ?? ""}`
          .toLowerCase()
          .includes(needle),
      )
      .sort(
        (a, b) => degree(b.id) - degree(a.id) || a.label.localeCompare(b.label),
      );
  });
  const hoverNeighbors = $derived(
    hovered
      ? new Set([
          ...(index.out.get(hovered.id) ?? []).map((l) => l.target),
          ...(index.incoming.get(hovered.id) ?? []).map((l) => l.source),
        ]).size
      : 0,
  );
  function labelOf(id: string) {
    return index.byId.get(id)?.label ?? id;
  }
  function describe(l: GraphLink) {
    return l.label ? ` (${l.label})` : "";
  }
  onMount(() => {
    let alive = true,
      mounted: GraphHandle | null = null;
    const syncSettings = () => {
      layout = graphLayout();
      physics = graphPhysics();
      handle?.setPhysics(physics);
    };
    window.addEventListener(GRAPH_SETTINGS_EVENT, syncSettings);
    if (!webglAvailable()) {
      problem =
        "WebGL is unavailable in this browser, so the 3D view is off. The adjacency table below has the same nodes and connections.";
      loading = false;
    } else {
      const data = copy();
      shownNodes = nodes;
      shownLinks = links;
      shownLayout = layout;
      shownSelected = selected;
      mountGraph(host!, {
        ...data,
        layout,
        physics: { ...physics },
        active: pane.active,
        onSelect: (n) => { if (pane.active) onSelect(n.id); },
        onHover: (n) => {
          if (!pane.active && n) return;
          hovered = n ? { id: n.id, label: n.label, group: n.group } : null;
        },
        reducedMotion: matchMedia("(prefers-reduced-motion: reduce)").matches,
      })
        .then((graph) => {
          if (!alive) {
            graph.destroy();
            return;
          }
          mounted = graph;
          handle = graph;
          if (selected) graph.focus(selected);
        })
        .catch((e: unknown) => {
          if (alive)
            problem = `${e instanceof Error ? e.message : String(e)} Use the adjacency table below.`;
        })
        .finally(() => {
          if (alive) loading = false;
        });
    }
    return () => {
      alive = false;
      window.removeEventListener(GRAPH_SETTINGS_EVENT, syncSettings);
      mounted?.destroy();
      handle = null;
    };
  });
  $effect(() => {
    if (!handle || (nodes === shownNodes && links === shownLinks)) return;
    shownNodes = nodes;
    shownLinks = links;
    handle.update(copy());
  });
  $effect(() => {
    if (!handle || layout === shownLayout) return;
    shownLayout = layout;
    handle.setLayout(layout);
  });
  $effect(() => {
    if (!handle || selected === shownSelected) return;
    shownSelected = selected;
    handle.focus(selected);
  });
  $effect(() => {
    handle?.setActive(pane.active);
  });
</script>

<div class="network">
  <div class="stage-wrap">
    <div
      class="stage3d"
      bind:this={host}
      role="img"
      aria-label={`${title}: ${nodes.length} nodes and ${links.length} connections in 3D. Drag with the left or scroll-wheel button held to rotate, scroll to zoom, click a node to inspect it. The adjacency table below lists the same graph for keyboard and screen-reader use.`}
    ></div>
    {#if loading}<p class="overlay">Loading 3D renderer…</p>{/if}
    {#if problem}<p class="overlay problem" role="alert">{problem}</p>{/if}
    {#if !nodes.length && !loading && !problem}<p class="overlay">
        No nodes to show yet.
      </p>{/if}
    <p class="hover" aria-hidden="true">
      {hovered
        ? `${hovered.label} / ${hovered.group} / ${hoverNeighbors} neighbor${hoverNeighbors === 1 ? "" : "s"}`
        : "Hover a node to highlight its neighbors"}
    </p>
  </div>
  <Panel
    title="Layout"
    readout={`${nodes.length} nodes · ${links.length} connections`}
  >
    <div class="controls">
      <label
        >Layout<select bind:value={layout}
          >{#each LAYOUTS as [id, text]}<option value={id}>{text}</option
            >{/each}</select
        ></label
      >
      <Button onclick={() => handle?.focus(null)} disabled={!handle}
        >Reset camera</Button
      >
    </div>
  </Panel>
  <Panel title="Nodes">
    <label
      >Search nodes<input
        type="search"
        bind:value={query}
        oninput={() => {
          limit = PAGE;
        }}
        placeholder="Label, kind, group…"
      /></label
    >
    <p class="hint" role="status">
      {rows.length} of {nodes.length} nodes match{rows.length > limit
        ? `; showing ${limit}`
        : ""}. Sorted by number of connections.
    </p>
    <div class="table-wrap">
      <table>
        <caption class="sr-only">{title} adjacency</caption>
        <thead
          ><tr
            ><th scope="col">Node</th><th scope="col">Group</th><th scope="col"
              >Links</th
            ><th scope="col">Points to</th><th scope="col">Pointed from</th></tr
          ></thead
        >
        <tbody>
          {#each rows.slice(0, limit) as n (n.id)}
            {@const out = index.out.get(n.id) ?? []}
            {@const inc = index.incoming.get(n.id) ?? []}
            <tr class:active={selected === n.id}>
              <th scope="row"
                ><button
                  type="button"
                  aria-pressed={selected === n.id}
                  onclick={() => onSelect(n.id)}
                  >{n.label}<small>{n.kind ?? ""}</small></button
                ></th
              >
              <td>{n.group}</td>
              <td>{out.length + inc.length}</td>
              <td
                >{#each out.slice(0, 20) as l}<span
                    >→ {l.target === n.id
                      ? "itself"
                      : labelOf(l.target)}{describe(l)}</span
                  >{/each}{#if out.length > 20}<span
                    >…and {out.length - 20} more</span
                  >{/if}</td
              >
              <td
                >{#each inc.slice(0, 20) as l}<span
                    >← {labelOf(l.source)}{describe(l)}</span
                  >{/each}{#if inc.length > 20}<span
                    >…and {inc.length - 20} more</span
                  >{/if}</td
              >
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
    {#if rows.length > limit}<Button
        onclick={() => {
          limit += PAGE;
        }}>Show {Math.min(PAGE, rows.length - limit)} more</Button
      >{/if}
  </Panel>
</div>

<style>
  .network {
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    gap: 12px;
    min-width: 0;
  }
  .stage-wrap {
    position: relative;
    overflow: hidden;
    border: 1px solid var(--border);
    background:
      radial-gradient(
        circle at 50% 45%,
        color-mix(in srgb, var(--violet) 10%, transparent),
        transparent 60%
      ),
      var(--chrome, var(--surface));
  }
  .stage-wrap::before,
  .stage-wrap::after {
    position: absolute;
    z-index: 1;
    color: var(--subtle, var(--muted));
    font-size: 11px;
    pointer-events: none;
  }
  .stage-wrap::before {
    content: "┌─ 3d";
    top: 4px;
    left: 6px;
  }
  .stage-wrap::after {
    content: "─┘";
    right: 6px;
    bottom: 4px;
  }
  .stage3d {
    height: 520px;
    width: 100%;
    overflow: hidden;
  }
  .overlay {
    position: absolute;
    inset: auto 16px 40px;
    margin: 0;
    padding: 10px;
    border: 1px solid var(--border);
    background: var(--surface);
    color: var(--muted);
    font-size: 12px;
  }
  .problem {
    border-color: var(--accent);
    color: var(--ink);
  }
  .hover {
    position: absolute;
    left: 10px;
    bottom: 6px;
    margin: 0;
    font-size: 11px;
    color: var(--muted);
    pointer-events: none;
  }
  .controls {
    display: flex;
    flex-wrap: wrap;
    align-items: end;
    gap: 12px;
  }
  label {
    display: grid;
    gap: 5px;
    font-size: 11px;
    color: var(--muted);
    min-width: 140px;
  }
  select,
  input[type="search"] {
    border: 1px solid var(--border);
    border-radius: 0;
    background: var(--surface);
    color: var(--ink);
    padding: 8px;
    font: inherit;
  }
  .hint {
    color: var(--muted);
    font-size: 12px;
  }
  .table-wrap {
    max-height: 420px;
    overflow: auto;
    border: 1px solid var(--border);
  }
  table {
    width: 100%;
    border-collapse: collapse;
    font-size: 12px;
  }
  th,
  td {
    border-bottom: 1px solid var(--border);
    padding: 6px 8px;
    text-align: left;
    vertical-align: top;
    overflow-wrap: anywhere;
  }
  thead th {
    position: sticky;
    top: 0;
    background: var(--chrome, var(--surface));
    color: var(--violet);
    font-weight: normal;
    text-transform: uppercase;
    font-size: 10px;
    letter-spacing: 0.06em;
  }
  td span {
    display: block;
  }
  tr.active {
    background: color-mix(in srgb, var(--accent) 12%, transparent);
  }
  th button {
    border: 1px solid var(--border);
    border-radius: 0;
    background: var(--surface);
    color: var(--ink);
    padding: 6px;
    font: inherit;
    text-align: left;
    cursor: pointer;
    width: 100%;
  }
  th button[aria-pressed="true"] {
    border-color: var(--accent);
  }
  small {
    display: block;
    color: var(--muted);
    font-size: 10px;
  }
  .sr-only {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip: rect(0 0 0 0);
    white-space: nowrap;
  }
</style>
