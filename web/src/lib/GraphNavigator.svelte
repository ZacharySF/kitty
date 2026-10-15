<script lang="ts">
  import { onMount } from "svelte";
  import { Button, Panel } from "@nunera/bracket";
  import { api, type Bootstrap } from "./api";
  import Icon from "./Icon.svelte";
  import Dashboard from "./Dashboard.svelte";
  import {
    navigation,
    navigationGroups,
    navigationRelations,
    type View,
  } from "./workspace";
  import {
    DEFAULT_PHYSICS,
    PHYSICS_LIMITS,
    mountGraph,
    webglAvailable,
    type GraphHandle,
    type GraphLayout,
    type GraphLink,
    type GraphNode,
    type GraphPhysics,
  } from "./graph3d";

  type AtlasItem = {
    id: string;
    kind: string;
    ref: number | string;
    label: string;
    detail: string;
    view: View;
  };
  type Atlas = {
    limit: number;
    kinds: { kind: string; label: string; view: View }[];
    items: AtlasItem[];
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

  const LAYOUT_KEY = "kitty.atlas.layout";
  const PHYSICS_KEY = "kitty.atlas.physics";
  const layouts: { id: GraphLayout; label: string; note: string }[] = [
    { id: "sphere", label: "Sphere", note: "hubs inside, items on the shell" },
    { id: "clusters", label: "Clusters", note: "one hull per group" },
    { id: "tree", label: "Tree", note: "radial levels from each group" },
    { id: "force", label: "Force", note: "free physics" },
  ];
  const physicsLabels: Record<keyof GraphPhysics, string> = {
    repulsion: "Repulsion",
    spring: "Spring",
    damping: "Damping",
  };
  const cat = " /\\_/\\\n( o.o )\n > ^ <";
  const date = new Intl.DateTimeFormat(undefined, {
    weekday: "long",
    month: "long",
    day: "numeric",
  }).format(new Date());
  const reducedMotion =
    typeof window !== "undefined" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  const sections = navigationGroups.map((group) => ({
    label: group.label,
    items: group.views
      .filter((id) => id !== "today")
      .map((id) => navigation.find((item) => item.id === id)!),
  }));
  const groupOf = new Map<View, string>(
    sections.flatMap((group) =>
      group.items.map((item) => [item.id, group.label] as [View, string]),
    ),
  );

  function storedLayout(): GraphLayout {
    const value = localStorage.getItem(LAYOUT_KEY);
    return layouts.some((item) => item.id === value)
      ? (value as GraphLayout)
      : "sphere";
  }
  function storedPhysics(): GraphPhysics {
    try {
      const value = JSON.parse(localStorage.getItem(PHYSICS_KEY) || "{}");
      const result = { ...DEFAULT_PHYSICS };
      for (const key of Object.keys(PHYSICS_LIMITS) as (keyof GraphPhysics)[]) {
        const limit = PHYSICS_LIMITS[key];
        if (typeof value[key] === "number")
          result[key] = Math.min(limit.max, Math.max(limit.min, value[key]));
      }
      return result;
    } catch {
      return { ...DEFAULT_PHYSICS };
    }
  }

  let atlas = $state<Atlas | null>(null);
  let atlasError = $state("");
  let layout = $state<GraphLayout>(storedLayout());
  let physics = $state<GraphPhysics>(storedPhysics());
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
  let handle: GraphHandle | null = null;

  const itemsById = $derived(
    new Map((atlas?.items ?? []).map((item) => [item.id, item])),
  );
  const itemCounts = $derived.by(() => {
    const counts = new Map<View, number>();
    for (const item of atlas?.items ?? [])
      counts.set(item.view, (counts.get(item.view) ?? 0) + 1);
    return counts;
  });
  const graph = $derived.by(() => {
    const nodes: GraphNode[] = [];
    const links: GraphLink[] = [];
    for (const group of sections) {
      nodes.push({
        id: `group:${group.label}`,
        label: group.label.toUpperCase(),
        group: group.label,
        kind: "hub",
        size: 7,
        detail: `${group.items.length} sections`,
      });
    }
    for (const group of sections) {
      for (const item of group.items) {
        const count = itemCounts.get(item.id) ?? 0;
        nodes.push({
          id: `view:${item.id}`,
          label: item.label,
          group: group.label,
          kind: "hub",
          size: 3,
          detail: [
            item.key ? `${item.key}` : "",
            count ? `${count} linked item${count === 1 ? "" : "s"}` : "",
            "click to open",
          ]
            .filter(Boolean)
            .join(" · "),
          href: `#${item.id}`,
        });
        links.push({
          source: `group:${group.label}`,
          target: `view:${item.id}`,
        });
      }
    }
    for (const [a, b] of navigationRelations)
      if (groupOf.has(a) && groupOf.has(b))
        links.push({
          source: `view:${a}`,
          target: `view:${b}`,
          weight: 0.8,
          label: "related sections",
        });
    for (const item of atlas?.items ?? []) {
      const group = groupOf.get(item.view);
      if (!group) continue;
      const kind = atlas?.kinds.find((entry) => entry.kind === item.kind);
      nodes.push({
        id: item.id,
        label: item.label,
        group,
        kind: item.kind,
        size: 1,
        detail: [kind?.label, item.detail].filter(Boolean).join(" · "),
      });
      links.push({
        source: `view:${item.view}`,
        target: item.id,
        weight: 0.6,
      });
    }
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
    return (
      atlas?.kinds.find((entry) => entry.kind === node.kind)?.label ?? "Item"
    );
  }
  function openable(node: GraphNode) {
    return !node.id.startsWith("group:");
  }
  function open(node: GraphNode) {
    if (node.id.startsWith("view:")) {
      onview(node.id.slice(5) as View);
      return;
    }
    const item = itemsById.get(node.id);
    if (!item) return;
    if (
      (item.kind === "job" || item.kind === "application") &&
      typeof item.ref === "number"
    )
      onopenjob(item.ref);
    else onview(item.view);
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
  function chooseLayout(next: GraphLayout) {
    layout = next;
    localStorage.setItem(LAYOUT_KEY, next);
    handle?.setLayout(next);
  }
  function tune(key: keyof GraphPhysics, value: number) {
    physics = { ...physics, [key]: value };
    localStorage.setItem(PHYSICS_KEY, JSON.stringify(physics));
    handle?.setPhysics({ [key]: value });
  }
  function resetPhysics() {
    physics = { ...DEFAULT_PHYSICS };
    localStorage.removeItem(PHYSICS_KEY);
    handle?.setPhysics(physics);
  }
  export function focusSearch() {
    searchField?.focus();
  }

  let atlasRequest = 0;
  async function loadAtlas() {
    const request = ++atlasRequest;
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

  onMount(() => {
    let disposed = false;
    void (async () => {
      await loadAtlas();
      if (disposed) return;
      if (!webglAvailable()) {
        mode = "list";
        notice =
          "WebGL is unavailable in this browser, so the 3D atlas is replaced by the section list.";
        return;
      }
      try {
        const mounted = await mountGraph(container, {
          ...graph,
          layout,
          physics,
          reducedMotion,
          onSelect: (node) => {
            if (openable(node)) open(node);
            else selected = node;
          },
          onHover: (node) => (hovered = node),
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
      handle?.destroy();
      handle = null;
    };
  });

  const readouts = $derived([
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
      value: workspace?.counts.jobs ?? null,
      view: "jobs" as View,
    },
    {
      label: "In your queue",
      value: workspace?.counts.queued ?? null,
      view: "queue" as View,
    },
    {
      label: "Applications",
      value: workspace?.counts.applications ?? null,
      view: "pipeline" as View,
    },
    {
      label: "Saved jobs",
      value: workspace?.counts.favorites ?? null,
      view: "favorites" as View,
    },
  ]);
</script>

{#snippet sectionList(large: boolean)}
  <nav class="section-list" class:large aria-label="Workspace sections">
    {#each sections as group, index}
      <section aria-labelledby={`atlas-group-${index}${large ? "-l" : ""}`}>
        <h3 id={`atlas-group-${index}${large ? "-l" : ""}`}>
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
                  >{/if}{#if item.key}<kbd>{item.key}</kbd>{/if}</a
              >{#if mode === "graph"}<button
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
    aria-label="3D atlas of your workspace"
    aria-describedby="atlas-description"
  >
    <p id="atlas-description" class="sr-only">
      A 3D network: {sections.length} group hubs, their sections, and {atlas
        ?.items.length ?? 0} recent items. Pointer users can drag to orbit, scroll
      to zoom, and click a node to open it. Keyboard and screen-reader users can use
      the atlas search and the Sections list in the side panel, which reach the same
      places.
    </p>
    <div class="canvas" bind:this={container}></div>
    {#if mode === "list"}
      <div class="fallback">
        <p class="notice" role="status">
          <span aria-hidden="true">[!]</span>
          {notice}
        </p>
        {@render sectionList(true)}
      </div>
    {:else}
      <div class="hud top" aria-hidden="true">
        <span class="prompt"
          ><b>kitty@home</b> <i>~</i> $ atlas --layout {layout}</span
        >
        <span>drag to orbit · scroll to zoom · click a node to open</span>
      </div>
      {#if mode === "loading"}<p class="hud loading" role="status">
          Drawing your atlas…
        </p>{/if}
      <div class="hud legend" aria-hidden="true">
        {#each sections as group, index}<span
            ><span class="swatch" style={`--swatch: var(--c${index + 1})`}
            ></span>{group.label}</span
          >{/each}
        <span class="hover-readout"
          >{hovered ? `› ${hovered.label}` : "› hover a node"}</span
        >
      </div>
    {/if}
  </div>

  <Panel
    as="aside"
    class="atlas-panel"
    title="~/atlas / control"
    icon="▣"
    readout={`${graph.nodes.length} NODES`}
    aria-label="Atlas controls and navigation"
  >
    <header class="greeting">
      <div
        class="art"
        role="img"
        aria-label="Anime artwork chosen for your workspace"
      ></div>
      <div class="hello">
        <pre aria-hidden="true">{cat}</pre>
        <div>
          <p class="prompt"><b>kitty@home</b> <i>~</i> $ welcome</p>
          <h1 id="page-title">HELLO, <span>KITTY!</span></h1>
          <p class="date">{date}</p>
        </div>
      </div>
    </header>

    <div class="search">
      <label for="atlas-search">Jump to a node <kbd>/</kbd></label>
      <input
        id="atlas-search"
        type="search"
        bind:this={searchField}
        bind:value={query}
        onkeydown={searchKey}
        placeholder="calendar, a deck, a job…"
        autocomplete="off"
        aria-describedby="atlas-search-status"
      />
      <p id="atlas-search-status" class="sr-only" aria-live="polite">
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

    {#if mode !== "list"}
      <details class="block" open>
        <summary>◇ Map</summary>
        <fieldset class="layouts">
          <legend>Layout</legend>
          {#each layouts as option}
            <label class:checked={layout === option.id} title={option.note}>
              <input
                type="radio"
                name="atlas-layout"
                value={option.id}
                checked={layout === option.id}
                onchange={() => chooseLayout(option.id)}
              />{option.label}
            </label>
          {/each}
        </fieldset>
        <div class="physics">
          {#each Object.keys(PHYSICS_LIMITS) as (keyof GraphPhysics)[] as key}
            <label>
              <span>{physicsLabels[key]}</span>
              <input
                type="range"
                min={PHYSICS_LIMITS[key].min}
                max={PHYSICS_LIMITS[key].max}
                step={PHYSICS_LIMITS[key].step}
                value={physics[key]}
                oninput={(event) =>
                  tune(key, Number(event.currentTarget.value))}
              />
              <output
                >{physics[key].toFixed(key === "repulsion" ? 0 : 2)}</output
              >
            </label>
          {/each}
          <button class="text-button" onclick={resetPhysics}
            >Reset physics</button
          >
        </div>
        <dl class="counts">
          <div>
            <dt>nodes</dt>
            <dd>{graph.nodes.length}</dd>
          </div>
          <div>
            <dt>links</dt>
            <dd>{graph.links.length}</dd>
          </div>
          <div>
            <dt>items</dt>
            <dd>{atlas?.items.length ?? "—"}</dd>
          </div>
        </dl>
        {#if atlasError}<p class="error" role="alert">
            Linked items unavailable: {atlasError}
            <button class="text-button" onclick={loadAtlas}>Retry</button>
          </p>{/if}
      </details>
    {/if}

    <details class="block" open={mode === "list" ? undefined : true}>
      <summary>☰ Sections</summary>
      {#if mode === "list"}<p class="muted small">
          The full list is shown in the main area.
        </p>{:else}{@render sectionList(false)}{/if}
    </details>

    <details class="block">
      <summary>◷ Today</summary>
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
      <Button size="sm" onclick={onguide}
        ><Icon name="questions" size={15} /> How to apply</Button
      >
      <div class="brief">
        <Dashboard {revision} {onopenjob} onview={(view) => onview(view)} />
      </div>
    </details>
  </Panel>
</div>

<style>
  .atlas {
    flex: 1;
    min-height: 0;
    display: grid;
    grid-template-columns: minmax(0, 1fr) minmax(300px, 360px);
    border: 1px solid var(--border);
    background:
      radial-gradient(circle at 42% 50%, #1b16288c, transparent 62%),
      var(--canvas);
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
  .stage::before,
  .stage::after {
    content: "";
    position: absolute;
    width: 14px;
    height: 14px;
    border-color: var(--violet);
    pointer-events: none;
    z-index: 2;
  }
  .stage::before {
    top: 8px;
    left: 8px;
    border-top: 1px solid;
    border-left: 1px solid;
  }
  .stage::after {
    bottom: 8px;
    right: 8px;
    border-bottom: 1px solid;
    border-right: 1px solid;
  }
  .hud {
    position: absolute;
    z-index: 1;
    margin: 0;
    font: 11px var(--br-font-ui);
    color: var(--subtle);
    pointer-events: none;
  }
  .hud.top {
    top: 14px;
    left: 26px;
    right: 26px;
    display: flex;
    justify-content: space-between;
    gap: 14px;
    flex-wrap: wrap;
  }
  .prompt b {
    color: var(--success);
    font-weight: 500;
  }
  .prompt i {
    color: var(--violet);
    font-style: normal;
  }
  .hud.loading {
    inset: 50% auto auto 50%;
    transform: translate(-50%, -50%);
    color: var(--muted);
  }
  .hud.legend {
    left: 26px;
    bottom: 16px;
    display: flex;
    flex-wrap: wrap;
    gap: 6px 16px;
    align-items: center;
  }
  .hover-readout {
    color: var(--ink);
    min-width: 0;
    max-width: 46ch;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .swatch {
    display: inline-block;
    width: 8px;
    height: 8px;
    margin-right: 7px;
    background: var(--swatch);
    box-shadow: 0 0 8px var(--swatch);
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
    font: 10px var(--br-font-display);
    color: var(--violet);
  }
  .greeting {
    margin: 12px 0 14px;
    border: 1px solid var(--violet);
    position: relative;
  }
  .art {
    height: 64px;
    background-image: url("/art/application-background.png");
    background-size: cover;
    background-position: 67% 38%;
    filter: saturate(0.7) contrast(1.05);
    border-bottom: 1px solid var(--border);
  }
  .hello {
    display: flex;
    gap: 14px;
    align-items: center;
    padding: 10px 12px;
  }
  .hello pre {
    margin: 0;
    color: var(--accent);
    font: 12px/1.2 var(--br-font-ui);
  }
  .hello .prompt,
  .date {
    margin: 0;
    font-size: 10px;
    color: var(--subtle);
  }
  h1 {
    margin: 4px 0;
    font: 22px/1.1 var(--br-font-display);
    letter-spacing: -1px;
    color: var(--ink);
    text-shadow: 2px 2px var(--border);
  }
  h1 span {
    color: var(--violet);
  }
  .search label {
    display: flex;
    justify-content: space-between;
    font-size: 11px;
    color: var(--muted);
    margin-bottom: 6px;
  }
  .search input {
    width: 100%;
    font-size: 12px;
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
    border-color: var(--violet);
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
    font: 12px var(--br-font-display);
    letter-spacing: 1px;
    color: var(--ink);
    cursor: pointer;
    padding: 2px 0 8px;
  }
  .layouts {
    border: 0;
    margin: 0;
    padding: 0;
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 4px;
  }
  .layouts legend {
    font-size: 10px;
    color: var(--subtle);
    margin-bottom: 6px;
    letter-spacing: 1px;
  }
  .layouts label {
    border: 1px solid var(--border);
    text-align: center;
    padding: 6px 2px;
    font-size: 11px;
    color: var(--muted);
    cursor: pointer;
  }
  .layouts label.checked {
    border-color: var(--accent);
    color: var(--ink);
    background: var(--surface-raised);
  }
  .layouts label:focus-within {
    outline: 2px solid var(--violet);
    outline-offset: 1px;
  }
  .layouts input {
    position: absolute;
    opacity: 0;
    width: 1px;
    height: 1px;
  }
  .physics {
    display: grid;
    gap: 6px;
    margin-top: 12px;
  }
  .physics label {
    display: grid;
    grid-template-columns: 76px minmax(0, 1fr) 42px;
    align-items: center;
    gap: 8px;
    font-size: 11px;
    color: var(--muted);
  }
  .physics input {
    width: 100%;
    height: 18px;
    padding: 0;
    border: 0;
    background: transparent;
    appearance: none;
    cursor: pointer;
  }
  .physics input::-webkit-slider-runnable-track {
    height: 2px;
    background: var(--border);
  }
  .physics input::-moz-range-track {
    height: 2px;
    background: var(--border);
  }
  .physics input::-moz-range-progress {
    height: 2px;
    background: var(--accent);
  }
  .physics input::-webkit-slider-thumb {
    appearance: none;
    width: 10px;
    height: 14px;
    margin-top: -6px;
    background: var(--accent);
    border: 1px solid var(--canvas);
    border-radius: 0;
  }
  .physics input::-moz-range-thumb {
    width: 10px;
    height: 14px;
    background: var(--accent);
    border: 1px solid var(--canvas);
    border-radius: 0;
  }
  .physics input:focus-visible {
    outline: 2px solid var(--violet);
    outline-offset: 2px;
  }
  .physics output {
    text-align: right;
    color: var(--ink);
    font-size: 11px;
  }
  .physics .text-button {
    justify-self: start;
    font-size: 11px;
  }
  .counts {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 4px;
    margin: 12px 0 0;
  }
  .counts div {
    border: 1px solid var(--border);
    padding: 6px 8px;
  }
  .counts dt {
    font-size: 9px;
    letter-spacing: 1px;
    color: var(--subtle);
    text-transform: uppercase;
  }
  .counts dd {
    margin: 0;
    font: 16px var(--br-font-display);
    color: var(--ink);
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
    font-size: 10px;
    letter-spacing: 1.5px;
    color: var(--subtle);
    text-transform: uppercase;
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
  .section-list kbd {
    font-size: 9px;
    margin-left: auto;
  }
  .section-list small + kbd {
    margin-left: 6px;
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
    border-left: 2px solid var(--violet);
  }
  .readouts button:hover {
    border-color: var(--violet);
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
    .hud.top > span:last-child {
      display: none;
    }
    .fallback {
      padding: 16px;
    }
  }
</style>
