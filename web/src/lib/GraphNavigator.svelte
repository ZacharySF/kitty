<script lang="ts">
  import { onMount } from "svelte";
  import { Button, Panel } from "@nunera/bracket";
  import { api, type Bootstrap } from "./api";
  import Icon from "./Icon.svelte";
  import Dashboard from "./Dashboard.svelte";
  import {
    navigation,
    viewEnabled,
    visibleGroups,
    visibleRelations,
    type View,
  } from "./workspace";
  import { activeModules } from "./modules.svelte";
  import {
    mountGraph,
    type GraphHandle,
    type GraphLink,
    type GraphNode,
  } from "./graph3d";
  import { GRAPH_SETTINGS_EVENT, graphLayout, graphPhysics, graphLook } from "./graphPreferences";
  import GraphLookToggle from "./GraphLookToggle.svelte";
  import { useWorkspaceContext } from "./workspaceContext.svelte";

  const pane = useWorkspaceContext();

  type Atlas = {
    sections: {kind:string;label:string;view:View;count:number|null}[];
  };

  let {
    workspace,
    revision,
    onview,
    onopenjob,
    onguide,
  }: {
    workspace: Bootstrap | null;
    revision: number;
    onview: (view: View) => unknown;
    onopenjob: (id: number) => void;
    onguide: () => void;
  } = $props();

  const atlasId = `atlas-${pane.id}`;
  const reducedMotion =
    typeof window !== "undefined" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  const enabled = $derived(activeModules.enabled);
  const applyai = $derived(enabled.includes("applyai"));
  const workspaceTools = $derived(enabled.includes("workspace"));
  const sections = $derived(
    visibleGroups(enabled).map((group) => ({
      label: group.label,
      items: group.views
        .filter((id) => id !== "today" && (group.label !== "Job search" || id === "jobs"))
        .map((id) => navigation.find((item) => item.id === id)!),
    })),
  );

  let atlas = $state<Atlas | null>(null);
  let atlasError = $state("");
  const layout = graphLayout();
  let look = $state(graphLook());
  const physics = graphPhysics();
  let mode = $state<"loading" | "graph" | "list">("loading");
  let notice = $state("");
  let hovered = $state<GraphNode | null>(null);
  let selected = $state<GraphNode | null>(null);
  let query = $state("");
  let daily = $state<{
    events: number | null;
    focus: number | null;
    due: number | null;
    opportunities: number | null;
  }>({ events: null, focus: null, due: null, opportunities: null });
  let dailyLoading = $state(false);
  let container: HTMLDivElement;
  let searchField = $state<HTMLInputElement>();
  let handle = $state.raw<GraphHandle | null>(null);
  let pendingGroup: string | null = null;

  const itemCounts = $derived.by(() => {
    const counts = new Map<View, number>();
    for (const item of atlas?.sections ?? [])
      if (item.count !== null && viewEnabled(item.view, enabled))
        counts.set(item.view, item.count + (counts.get(item.view) || 0));
    return counts;
  });
  const graph = $derived.by(() => {
    const nodes: GraphNode[] = [];
    const links: GraphLink[] = [];
    const rootViews: Partial<Record<string, View>> = { Study: "study", "Job search": "jobs", Settings: "settings" };
    for (const group of sections) {
      nodes.push({
        id: rootViews[group.label] ? `view:${rootViews[group.label]}` : `group:${group.label}`,
        label: group.label,
        group: group.label,
        kind: "hub",
        size: 7,
        detail: `${group.items.length} sections`,
        href: rootViews[group.label] ? `#${rootViews[group.label]}` : undefined,
      });
    }
    for (const group of sections) {
      for (const item of group.items) {
        if (item.id === rootViews[group.label]) continue;
        const count = itemCounts.get(item.id) ?? 0;
        nodes.push({
          id: `view:${item.id}`,
          label: item.label,
          group: group.label,
          kind: "hub",
          size: 3,
          detail: count ? `${count} linked item${count === 1 ? "" : "s"}` : "",
          href: `#${item.id}`,
        });
        links.push({
          source: rootViews[group.label] ? `view:${rootViews[group.label]}` : `group:${group.label}`,
          target: `view:${item.id}`,
        });
      }
    }
    const visible = new Set(nodes.map(node => node.id));
    const existing = new Set(links.map(link => `${link.source}\0${link.target}`));
    for (const [a, b] of visibleRelations(enabled))
      if (visible.has(`view:${a}`) && visible.has(`view:${b}`) &&
          !existing.has(`view:${a}\0view:${b}`) && !existing.has(`view:${b}\0view:${a}`))
        links.push({
          source: `view:${a}`,
          target: `view:${b}`,
          weight: 0.8,
          label: "related sections",
        });
    return { nodes, links };
  });
  const matches = $derived.by(() => {
    const text = query.trim().toLowerCase();
    if (!text) return [];
    const rank = (node: GraphNode) =>
      node.id.startsWith("view:") ? 0 : node.id.startsWith("group:") ? 1 : 2;
    return graph.nodes
      .filter((node) => node.label.toLowerCase().includes(text))
      .sort(
        (a, b) =>
          rank(a) - rank(b) ||
          Number(!a.label.toLowerCase().startsWith(text)) -
            Number(!b.label.toLowerCase().startsWith(text)) ||
          a.label.localeCompare(b.label),
      )
      .slice(0, 8);
  });

  function kindLabel(node: GraphNode) {
    if (node.id.startsWith("group:")) return "Group";
    if (node.id.startsWith("view:")) return "Section";
    return "Section";
  }
  function openable(node: GraphNode) {
    return !node.id.startsWith("group:");
  }
  function open(node: GraphNode) {
    if (node.id.startsWith("view:")) {
      onview(node.id.slice(5) as View);
      return;
    }
  }
  function locate(node: GraphNode) {
    selected = node;
    handle?.focus(node.id);
  }
  function clearSelection() {
    selected = null;
    handle?.focus(null);
  }
  function searchKey(event: KeyboardEvent) {
    if (event.key !== "Enter") return;
    event.preventDefault();
    const first = matches[0];
    if (!first) return;
    if (selected?.id === first.id || mode === "list") {
      if (openable(first)) open(first);
      else locate(first);
    } else locate(first);
  }
  export function focusGroup(label: string) {
    if (!sections.some(group => group.label === label)) return;
    if (!pane.active || (!handle && mode === "loading")) {
      pendingGroup = label;
      return;
    }
    const node = graph.nodes.find(node => node.label === label && node.group === label);
    if (!node) return;
    pendingGroup = null;
    locate(node);
  }
  export function focusSearch() {
    if (pane.active) searchField?.focus();
  }

  let atlasRequest = 0;
  async function loadAtlas() {
    const request = ++atlasRequest;
    if (!enabled.length) {
      atlas = null;
      atlasError = "";
      return;
    }
    try {
      const result = await api<Atlas>("/atlas");
      if (request !== atlasRequest) return;
      atlas = result;
      atlasError = "";
    } catch (cause) {
      if (request === atlasRequest) atlasError = (cause as Error).message;
    }
  }
  let dailyRequest = 0;
  async function refreshDaily() {
    const request = ++dailyRequest;
    if (!workspaceTools) {
      daily = { events: null, focus: null, due: null, opportunities: null };
      dailyLoading = false;
      return;
    }
    dailyLoading = true;
    const start = new Date();
    start.setHours(0, 0, 0, 0);
    const end = new Date(start);
    end.setDate(end.getDate() + 1);
    const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
    const calendarQuery = new URLSearchParams({
      start: start.toISOString(),
      end: end.toISOString(),
      timezone,
    });
    const [calendar, study, flashcards, opportunities] =
      await Promise.allSettled([
        api<{ events: unknown[] }>(`/calendar?${calendarQuery}`),
        api<{ stats: { today_seconds: number } }>("/study"),
        api<{ stats: { due: number } }>("/flashcards"),
        api<{ total: number }>("/opportunities"),
      ]);
    if (request !== dailyRequest) return;
    daily = {
      events:
        calendar.status === "fulfilled" ? calendar.value.events.length : null,
      focus:
        study.status === "fulfilled" ? study.value.stats.today_seconds : null,
      due:
        flashcards.status === "fulfilled" ? flashcards.value.stats.due : null,
      opportunities:
        opportunities.status === "fulfilled" ? opportunities.value.total : null,
    };
    dailyLoading = false;
  }

  let seenRevision: number | undefined;
  $effect(() => {
    const current = revision;
    if (seenRevision === current) return;
    const first = seenRevision === undefined;
    seenRevision = current;
    void refreshDaily();
    if (!first) void loadAtlas();
  });
  $effect(() => {
    const next = graph;
    handle?.update(next);
  });
  $effect(() => {
    if (selected && !graph.nodes.some((node) => node.id === selected?.id))
      selected = null;
  });
  $effect(() => {
    const active = pane.active;
    handle?.setActive(active);
    if (active && (handle || mode === "list") && pendingGroup) focusGroup(pendingGroup);
  });

  onMount(() => {
    let disposed = false;
    const syncSettings = () => {
      handle?.setLayout(graphLayout());
      handle?.setPhysics(graphPhysics());
      look = graphLook();
      void handle?.setLook(look).catch(cause => { notice = (cause as Error).message; });
    };
    window.addEventListener(GRAPH_SETTINGS_EVENT, syncSettings);
    void (async () => {
      await loadAtlas();
      if (disposed) return;
      try {
        const mounted = await mountGraph(container, {
          ...graph,
          layout,
          physics,
          look: graphLook(),
          reducedMotion,
          active: pane.active,
          onSelect: (node) => {
            if (!pane.active) return;
            if (openable(node)) open(node);
            else selected = node;
          },
          onHover: (node) => { if (pane.active || !node) hovered = node; },
        });
        if (disposed) {
          mounted.destroy();
          return;
        }
        handle = mounted;
        mode = "graph";
      } catch (cause) {
        mode = "list";
        notice = `${(cause as Error).message} The section list below still opens every page.`;
      }
    })();
    return () => {
      disposed = true;
      window.removeEventListener(GRAPH_SETTINGS_EVENT, syncSettings);
      handle?.destroy();
      handle = null;
    };
  });

  const readouts = $derived(
    [
      { label: "Events today", value: daily.events, view: "calendar" as View },
      {
        label: "Focused today",
        value:
          daily.focus === null ? null : `${Math.floor(daily.focus / 60)} min`,
        view: "study" as View,
      },
      { label: "Cards due", value: daily.due, view: "flashcards" as View },
      {
        label: "Opportunities",
        value: daily.opportunities,
        view: "opportunities" as View,
      },
      {
        label: "Jobs to explore",
        value: workspace?.counts?.jobs ?? null,
        view: "jobs" as View,
      },
      {
        label: "In your queue",
        value: workspace?.counts?.queued ?? null,
        view: "queue" as View,
      },
      {
        label: "Applications",
        value: workspace?.counts?.applications ?? null,
        view: "pipeline" as View,
      },
      {
        label: "Saved jobs",
        value: workspace?.counts?.favorites ?? null,
        view: "favorites" as View,
      },
    ].filter((readout) => viewEnabled(readout.view, enabled)),
  );
</script>

{#snippet sectionList(large: boolean)}
  <nav class="section-list" class:large aria-label="Workspace sections">
    {#each sections as group, index}
      <section aria-labelledby={`${atlasId}-group-${index}${large ? "-l" : ""}`}>
        <h3 id={`${atlasId}-group-${index}${large ? "-l" : ""}`}>
          <span class="swatch" style={`--swatch: var(--c${index + 1})`}
          ></span>{group.label}
        </h3>
        <ul>
          {#each group.items as item}
            <li>
              <a
                href={`#${item.id}`}
                onclick={(event) => {
                  event.preventDefault();
                  onview(item.id);
                }}
                ><Icon name={item.icon} size={15} /><span>{item.label}</span
                >{#if itemCounts.get(item.id)}<small
                    aria-label={`${itemCounts.get(item.id)} linked items`}
                    >{itemCounts.get(item.id)}</small
                  >{/if}</a
              >{#if mode === "graph" && graph.nodes.some(node => node.id === `view:${item.id}`)}<button
                  class="locate"
                  aria-label={`Show ${item.label} on the map`}
                  title="Show on the map"
                  onclick={() =>
                    locate(
                      graph.nodes.find(
                        (node) => node.id === `view:${item.id}`,
                      )!,
                    )}>◎</button
                >{/if}
            </li>
          {/each}
        </ul>
      </section>
    {/each}
  </nav>
{/snippet}

<div class="atlas" class:list-mode={mode === "list"}>
  <div
    class="stage"
    role="region"
    aria-label={`${look === "2d" ? "2D" : "3D"} atlas of your workspace`}
    aria-describedby={`${atlasId}-description`}
  >
    <p id={`${atlasId}-description`} class="sr-only">
      A map of workspace sections. {look === "2d" ? "Drag nodes to move, drag the background to pan" : "Drag to rotate"}, scroll to zoom, select a section to open its map.
    </p>
    <div class="canvas" bind:this={container}></div>
    {#if mode === "list"}
      <div class="fallback">
        <p class="notice" role="status">
          <span aria-hidden="true">!</span>
          {notice}
        </p>
        {@render sectionList(true)}
      </div>
    {:else}
      <div class="stage-caption" aria-hidden="true">
        <span>{look === "2d" ? "Drag a node to move · drag background to pan · scroll or pinch to zoom" : "Drag to rotate · scroll to zoom · Ctrl + ← / → to turn"}</span>
      </div>
      {#if mode === "loading"}<p class="stage-loading" role="status">
          Drawing your atlas…
        </p>{/if}
      {#if hovered}<p class="stage-selection" aria-hidden="true">{hovered.label}</p>{/if}
    {/if}
  </div>

  <Panel
    as="aside"
    class="atlas-panel"
    title="Workspace"
    aria-label="Atlas controls and navigation"
  >
    <GraphLookToggle />

    <div class="search">
      <label for={`${atlasId}-search`}>Find a section <kbd>/</kbd></label>
      <input
        id={`${atlasId}-search`}
        type="search"
        bind:this={searchField}
        bind:value={query}
        onkeydown={searchKey}
        placeholder="Find a section…"
        autocomplete="off"
        aria-describedby={`${atlasId}-search-status`}
      />
      <p id={`${atlasId}-search-status`} class="sr-only" aria-live="polite">
        {query.trim() ? `${matches.length} matches` : ""}
      </p>
      {#if matches.length}
        <ul class="matches" aria-label="Matching nodes">
          {#each matches as node}
            <li>
              <button
                class:current={selected?.id === node.id}
                onclick={() =>
                  mode === "graph"
                    ? locate(node)
                    : openable(node) && open(node)}
                ><span>{node.label}</span><small
                  >{kindLabel(node)} · {node.group}</small
                ></button
              >
            </li>
          {/each}
        </ul>
      {:else if query.trim()}<p class="empty">
          No node matches “{query.trim()}”.
        </p>{/if}
    </div>

    {#if selected}
      <section class="selected" aria-label="Selected node">
        <p class="eyebrow">{kindLabel(selected)} · {selected.group}</p>
        <strong>{selected.label}</strong>
        {#if selected.detail}<p>{selected.detail}</p>{/if}
        <div class="row">
          {#if openable(selected)}<Button
              intent="primary"
              size="sm"
              onclick={() => selected && open(selected)}
              >Open <Icon name="arrow" size={14} /></Button
            >{/if}
          <Button variant="ghost" size="sm" onclick={clearSelection}
            >Clear</Button
          >
        </div>
      </section>
    {/if}

    {#if atlasError}<p class="error" role="alert">Linked counts unavailable.
      <button class="text-button" onclick={loadAtlas}>Retry</button>
      <button class="icon-button" aria-label="Dismiss linked count message" onclick={() => (atlasError = "")}>×</button>
    </p>{/if}

    {#if !enabled.length}
      <section class="selected" aria-label="No modules active">
        <p class="eyebrow">Modules are off</p>
        <strong>Turn on Workspace or Apply AI</strong>
        <p>Only the core shell and settings are active. Choose modules in Settings; they start after you restart kitty.</p>
        <div class="row">
          <Button intent="primary" size="sm" onclick={() => onview("settings")}
            >Open module settings <Icon name="arrow" size={14} /></Button
          >
        </div>
      </section>
    {/if}

    <details class="block" open={mode === "list" ? undefined : true}>
      <summary>Sections</summary>
      {#if mode === "list"}<p class="muted small">
          The full list is shown in the main area.
        </p>{:else}{@render sectionList(false)}{/if}
    </details>

    {#if readouts.length}<details class="block">
      <summary>Today</summary>
      <div class="readouts" aria-busy={dailyLoading}>
        {#each readouts as readout}
          <button onclick={() => onview(readout.view)}
            ><span>{readout.label}</span><strong
              >{readout.value === null ? "—" : readout.value}</strong
            ></button
          >
        {/each}
      </div>
      <p class="muted small">
        {dailyLoading
          ? "Updating your day…"
          : "Live local totals · — means unavailable, not zero."}
      </p>
      {#if applyai}<Button size="sm" onclick={onguide}
        ><Icon name="questions" size={15} /> How to apply</Button
      >
      <div class="brief">
        <Dashboard {revision} {onopenjob} onview={(view) => onview(view)} />
      </div>{/if}
    </details>{/if}
  </Panel>
</div>

<style>
  .atlas {
    flex: 1;
    min-height: 0;
    display: grid;
    grid-template-columns: minmax(0, 1fr) minmax(280px, 320px);
    border: 1px solid var(--border-fine, var(--border));
    background: var(--canvas);
  }
  .stage {
    position: relative;
    min-width: 0;
    min-height: 0;
    overflow: hidden;
  }
  .canvas {
    position: absolute;
    inset: 0;
  }
  .list-mode .canvas {
    display: none;
  }
  .stage-caption,
  .stage-loading,
  .stage-selection {
    position: absolute;
    z-index: 1;
    margin: 0;
    font: 12px var(--br-font-ui);
    color: var(--muted);
    pointer-events: none;
  }
  .stage-caption {
    bottom: 16px;
    left: 20px;
    right: 20px;
    text-align: center;
  }
  .stage-loading {
    inset: 50% auto auto 50%;
    transform: translate(-50%, -50%);
  }
  .stage-selection {
    top: 16px;
    left: 20px;
    max-width: calc(100% - 40px);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    color: var(--ink);
  }
  .swatch {
    display: inline-block;
    width: 6px;
    height: 6px;
    margin-right: 7px;
    background: var(--swatch);
    border-radius: 50%;
  }
  .fallback {
    position: relative;
    height: 100%;
    overflow: auto;
    padding: 28px;
  }
  .notice {
    margin: 0 0 20px;
    padding: 12px 14px;
    border: 1px solid var(--warning);
    color: var(--ink);
    background: var(--chrome);
    font-size: 12px;
  }
  .notice span {
    color: var(--warning);
  }
  :global(.atlas-panel) {
    min-width: 0;
    min-height: 0;
    overflow-y: auto;
    border: 0 !important;
    border-left: 1px solid var(--border) !important;
    background: var(--chrome) !important;
    scrollbar-width: thin;
    scrollbar-color: var(--border) transparent;
  }
  :global(.atlas-panel .br-panel__header) {
    position: sticky;
    top: 0;
    z-index: 1;
    background: var(--chrome);
    font-size: 12px;
    border-bottom: 1px solid var(--border);
    padding-bottom: 10px;
  }
  :global(.atlas-panel .br-panel__readout) {
    color: var(--muted);
  }
  .search label {
    display: flex;
    justify-content: space-between;
    font-size: 12px;
    color: var(--muted);
    margin-bottom: 6px;
  }
  .search input {
    width: 100%;
    font-size: 13px;
  }
  .matches,
  .section-list ul {
    list-style: none;
    margin: 6px 0 0;
    padding: 0;
  }
  .matches button {
    width: 100%;
    display: grid;
    text-align: left;
    gap: 2px;
    padding: 7px 8px;
    background: transparent;
    border: 1px solid transparent;
    color: var(--ink);
    font-size: 12px;
  }
  .matches button:hover,
  .matches button.current {
    border-color: var(--border);
    background: var(--surface-raised);
  }
  .matches small,
  .empty,
  .small {
    color: var(--subtle);
    font-size: 10px;
  }
  .selected {
    margin-top: 12px;
    padding: 10px 12px;
    border: 1px solid var(--border);
    border-left: 2px solid var(--accent);
    background: var(--surface);
  }
  .selected .eyebrow {
    margin: 0 0 4px;
    font-size: 10px;
  }
  .selected strong {
    display: block;
    font-size: 13px;
    overflow-wrap: anywhere;
  }
  .selected p:not(.eyebrow) {
    margin: 6px 0 0;
    font-size: 11px;
    color: var(--muted);
  }
  .row {
    display: flex;
    gap: 8px;
    margin-top: 10px;
  }
  .block {
    margin-top: 14px;
    border-top: 1px solid var(--border);
    padding-top: 10px;
  }
  .block > summary {
    font: 500 13px var(--br-font-ui);
    color: var(--ink);
    cursor: pointer;
    padding: 2px 0 8px;
  }
  .error {
    font-size: 11px;
    color: var(--error);
  }
  .section-list section + section {
    margin-top: 10px;
  }
  .section-list h3 {
    margin: 0;
    font-size: 12px;
    font-weight: 600;
    color: var(--muted);
    display: flex;
    align-items: center;
  }
  .section-list li {
    display: flex;
    align-items: stretch;
    gap: 2px;
  }
  .section-list a {
    flex: 1;
    min-width: 0;
    display: flex;
    align-items: center;
    gap: 9px;
    padding: 6px 8px;
    border: 1px solid transparent;
    color: var(--muted);
    text-decoration: none;
    font-size: 12px;
  }
  .section-list a > span {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .section-list a:hover {
    color: var(--ink);
    border-color: var(--border);
    background: var(--surface-raised);
  }
  .section-list small {
    margin-left: auto;
    color: var(--accent);
    font-size: 10px;
  }
  .locate {
    width: 28px;
    background: transparent;
    border: 1px solid transparent;
    color: var(--subtle);
  }
  .locate:hover {
    color: var(--violet);
    border-color: var(--border);
  }
  .section-list.large {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
    gap: 18px;
  }
  .section-list.large section + section {
    margin-top: 0;
  }
  .section-list.large a {
    font-size: 13px;
    padding: 9px 10px;
  }
  .readouts {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 6px;
  }
  .readouts button {
    text-align: left;
    display: grid;
    gap: 3px;
    padding: 8px 10px;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 4px;
  }
  .readouts button:hover {
    border-color: var(--accent);
  }
  .readouts span {
    font-size: 10px;
    color: var(--muted);
  }
  .readouts strong {
    font: 17px var(--br-font-display);
    color: var(--ink);
  }
  .brief {
    margin-top: 12px;
  }
  .brief :global(.panel-heading) {
    padding: 12px 14px;
  }
  .brief :global(.panel-heading h2) {
    font-size: 16px;
  }
  @media (max-width: 760px) {
    .atlas {
      grid-template-columns: minmax(0, 1fr);
    }
    .stage {
      height: 58vh;
      min-height: 320px;
    }
    .list-mode .stage {
      height: auto;
    }
    :global(.atlas-panel) {
      overflow: visible;
      border-left: 0 !important;
      border-top: 1px solid var(--border) !important;
    }
    .stage-caption {
      display: none;
    }
    .fallback {
      padding: 16px;
    }
  }
</style>
