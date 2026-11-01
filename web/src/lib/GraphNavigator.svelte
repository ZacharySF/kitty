<script lang="ts">
  import { onMount, tick } from "svelte";
  import { Button } from "@nunera/bracket";
  import { api } from "./api";
  import { prefersReducedMotion } from "./appearance";
  import Icon from "./Icon.svelte";
  import HomeAgents from "./HomeAgents.svelte";
  import HomeQuickNote from "./HomeQuickNote.svelte";
  import HomeWidget from "./HomeWidget.svelte";
  import TodayAgenda from "./TodayAgenda.svelte";
  import {
    navigation,
    viewEnabled,
    visibleGroups,
    visibleRelations,
    type View,
  } from "./workspace";
  import {
    ATLAS_HIDDEN_KEY,
    ATLAS_NODES_EVENT,
    SECTION_DRAG_TYPE,
    atlasGraph,
    atlasSections,
    hiddenSections,
    setSectionHidden,
  } from "./atlasNodes";
  import { activeModules } from "./modules.svelte";
  import {
    mountGraph,
    type GraphHandle,
    type GraphNode,
  } from "./graph3d";
  import { GRAPH_SETTINGS_EVENT, graphLayout, graphPhysics, graphLook } from "./graphPreferences";
  import GraphLookToggle from "./GraphLookToggle.svelte";
  import { useWorkspaceContext } from "./workspaceContext.svelte";
  import Loader from "./Loader.svelte";
  import BrainStage from "./brain/BrainStage.svelte";

  const pane = useWorkspaceContext();

  type Atlas = {
    sections: {kind:string;label:string;view:View;count:number|null}[];
  };

  let {
    revision,
    onview,
  }: {
    revision: number;
    onview: (view: View) => unknown;
  } = $props();

  const atlasId = `atlas-${pane.id}`;
  const reducedMotion = prefersReducedMotion();

  const enabled = $derived(activeModules.enabled);
  const workspaceTools = $derived(enabled.includes("workspace"));
  const terminal = $derived(enabled.includes("terminal"));
  const sections = $derived(atlasSections(visibleGroups(enabled), navigation));
  let hidden = $state(hiddenSections());
  const listed = (view: string): view is View =>
    sections.some((group) => group.items.some((item) => item.id === view));

  let atlas = $state<Atlas | null>(null);
  let atlasError = $state("");
  const layout = graphLayout();
  // "brain" replaces the stage with BrainStage; the 2D/3D atlas then waits, paused, underneath.
  let look = $state(graphLook());
  let brain = $state<BrainStage>();
  const physics = graphPhysics();
  let mode = $state<"loading" | "graph" | "list">("loading");
  let notice = $state("");
  let hovered = $state<GraphNode | null>(null);
  let selected = $state<GraphNode | null>(null);
  let query = $state("");
  let container: HTMLDivElement;
  let searchField = $state<HTMLInputElement>();
  let handle = $state.raw<GraphHandle | null>(null);
  let pendingGroup: string | null = null;
  let sectionsBlock = $state<HTMLElement>();
  let draggedSection = $state<View | null>(null);
  let nodeDrag: { view: View; label: string } | null = null;
  let nodeOverList = $state<string | null>(null);
  let rowMenu = $state<{ view: View; label: string; x: number; y: number } | null>(null);
  let rowMenuElement = $state<HTMLDivElement>();
  let rowMenuReturn: HTMLElement | null = null;

  const itemCounts = $derived.by(() => {
    const counts = new Map<View, number>();
    for (const item of atlas?.sections ?? [])
      if (item.count !== null && viewEnabled(item.view, enabled))
        counts.set(item.view, item.count + (counts.get(item.view) || 0));
    return counts;
  });
  const graph = $derived(atlasGraph(sections, hidden, visibleRelations(enabled), itemCounts));
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
    if (look === "brain") {
      if (node.id.startsWith("view:")) brain?.focusSection(node.id.slice(5));
    } else handle?.focus(node.id);
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
  /** Screen position of a drawn node, or null when it is not on the atlas. */
  export function nodePoint(id: string) {
    return handle?.nodePoint(id) ?? null;
  }

  /** Q13: a node released over this atlas's Sections list is hidden. */
  function overList(clientX: number, clientY: number) {
    const target = document.elementFromPoint(clientX, clientY);
    return !!target && !!sectionsBlock?.contains(target);
  }
  function dropOnList(node: GraphNode, point: { clientX: number; clientY: number }) {
    if (!pane.active || !node.id.startsWith("view:")) return;
    const view = node.id.slice(5);
    if (listed(view) && overList(point.clientX, point.clientY)) setSectionHidden(view, true);
  }
  /** Tracks a pointer pressed on a section node so the list can show it accepts the drop. */
  function stagePointerDown(event: PointerEvent) {
    const node = mode === "graph" && pane.active && look !== "brain" && !event.button ? handle?.nodeAt(event.clientX, event.clientY) : null;
    const view = node?.id.startsWith("view:") ? node.id.slice(5) : "";
    nodeDrag = listed(view) ? { view, label: node!.label } : null;
  }
  function startSectionDrag(event: DragEvent, view: View) {
    if (!event.dataTransfer) return;
    event.dataTransfer.setData(SECTION_DRAG_TYPE, view);
    event.dataTransfer.effectAllowed = "move";
    draggedSection = view;
  }
  /** Only a hidden section can be dropped onto the graph to show it. */
  function acceptsSection(event: DragEvent) {
    return mode === "graph" && !!draggedSection && hidden.has(draggedSection) &&
      !!event.dataTransfer?.types.includes(SECTION_DRAG_TYPE);
  }
  function sectionDragOver(event: DragEvent) {
    if (!acceptsSection(event)) return;
    event.preventDefault();
    event.dataTransfer!.dropEffect = "move";
  }
  function sectionDrop(event: DragEvent) {
    if (!acceptsSection(event)) return;
    event.preventDefault();
    const view = event.dataTransfer!.getData(SECTION_DRAG_TYPE);
    draggedSection = null;
    if (listed(view)) setSectionHidden(view, false);
  }

  function openRowMenu(event: MouseEvent, view: View, label: string) {
    event.preventDefault();
    const trigger = (event.currentTarget as HTMLElement).closest("li")?.querySelector<HTMLElement>(".row-menu");
    rowMenuReturn = trigger ?? null;
    const rect = (trigger ?? (event.currentTarget as HTMLElement)).getBoundingClientRect();
    rowMenu = {
      view,
      label,
      x: Math.max(8, Math.min(event.clientX || rect.left, window.innerWidth - 200)),
      y: Math.max(8, Math.min(event.clientY || rect.bottom, window.innerHeight - 100)),
    };
    void tick().then(() => rowMenuElement?.querySelector<HTMLButtonElement>("button")?.focus());
  }
  function closeRowMenu(restoreFocus = true) {
    rowMenu = null;
    if (restoreFocus && rowMenuReturn?.isConnected) rowMenuReturn.focus();
    rowMenuReturn = null;
  }
  function rowMenuKey(event: KeyboardEvent) {
    const items = [...(rowMenuElement?.querySelectorAll<HTMLButtonElement>("[role=menuitem]") ?? [])];
    const index = items.indexOf(document.activeElement as HTMLButtonElement);
    let next: number;
    if (event.key === "ArrowDown") next = (index + 1) % items.length;
    else if (event.key === "ArrowUp") next = (index + items.length - 1) % items.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = items.length - 1;
    else if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      closeRowMenu();
      return;
    } else {
      if (event.key === "Tab") closeRowMenu(false);
      return;
    }
    event.preventDefault();
    items[next]?.focus();
  }
  function toggleSection(view: View) {
    setSectionHidden(view, !hidden.has(view));
    closeRowMenu();
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
  let seenRevision: number | undefined;
  $effect(() => {
    const current = revision;
    if (seenRevision === current) return;
    const first = seenRevision === undefined;
    seenRevision = current;
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
    handle?.setActive(active && look !== "brain");
    if (active && (handle || mode === "list") && pendingGroup) focusGroup(pendingGroup);
  });

  onMount(() => {
    let disposed = false;
    const syncSettings = () => {
      handle?.setLayout(graphLayout());
      handle?.setPhysics(graphPhysics());
      look = graphLook();
      if (look !== "brain") void handle?.setLook(look).catch(cause => { notice = (cause as Error).message; });
    };
    window.addEventListener(GRAPH_SETTINGS_EVENT, syncSettings);
    // Every atlas, in this window's tabs or another window, follows the same choice.
    const syncHidden = () => { hidden = hiddenSections(); };
    const syncStorage = (event: StorageEvent) => { if (event.key === ATLAS_HIDDEN_KEY || event.key === null) syncHidden(); };
    window.addEventListener(ATLAS_NODES_EVENT, syncHidden);
    window.addEventListener("storage", syncStorage);
    // Capture phase: the renderers own the pointer while a node is dragged.
    const trackNodeDrag = (event: PointerEvent) => {
      if (nodeDrag) nodeOverList = overList(event.clientX, event.clientY) ? nodeDrag.label : null;
    };
    const endNodeDrag = () => { nodeDrag = null; nodeOverList = null; };
    window.addEventListener("pointermove", trackNodeDrag, true);
    window.addEventListener("pointerup", endNodeDrag);
    window.addEventListener("pointercancel", endNodeDrag);
    void (async () => {
      await loadAtlas();
      if (disposed) return;
      try {
        const mounted = await mountGraph(container, {
          ...graph,
          layout,
          physics,
          look: look === "brain" ? "2d" : look,
          reducedMotion,
          active: pane.active && look !== "brain",
          onSelect: (node) => {
            if (!pane.active) return;
            if (openable(node)) open(node);
            else selected = node;
          },
          onHover: (node) => { if (pane.active || !node) hovered = node; },
          onDragEnd: dropOnList,
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
      window.removeEventListener(ATLAS_NODES_EVENT, syncHidden);
      window.removeEventListener("storage", syncStorage);
      window.removeEventListener("pointermove", trackNodeDrag, true);
      window.removeEventListener("pointerup", endNodeDrag);
      window.removeEventListener("pointercancel", endNodeDrag);
      handle?.destroy();
      handle = null;
    };
  });
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
            <li
              class:hidden-section={hidden.has(item.id)}
              oncontextmenu={(event) => openRowMenu(event, item.id, item.label)}
            >
              <a
                href={`#${item.id}`}
                draggable="true"
                ondragstart={(event) => startSectionDrag(event, item.id)}
                ondragend={() => (draggedSection = null)}
                onclick={(event) => {
                  event.preventDefault();
                  onview(item.id);
                }}
                ><Icon name={item.icon} size={15} /><span>{item.label}</span
                >{#if itemCounts.get(item.id)}<small
                    aria-label={`${itemCounts.get(item.id)} linked items`}
                    >{itemCounts.get(item.id)}</small
                  >{/if}</a
              >{#if hidden.has(item.id)}<span class="sr-only">Hidden from the atlas</span
                >{/if}{#if mode === "graph" && graph.nodes.some(node => node.id === `view:${item.id}`)}<button
                  class="locate"
                  aria-label={`Show ${item.label} on the map`}
                  title="Show on the map"
                  onclick={() =>
                    locate(
                      graph.nodes.find(
                        (node) => node.id === `view:${item.id}`,
                      )!,
                    )}>◎</button
                >{/if}<button
                class="row-menu"
                aria-label={`${item.label} options`}
                aria-haspopup="menu"
                aria-expanded={rowMenu?.view === item.id}
                title="Options"
                onclick={(event) => openRowMenu(event, item.id, item.label)}>⋯</button
              >
            </li>
          {/each}
        </ul>
      </section>
    {/each}
  </nav>
{/snippet}

<div class="atlas" class:list-mode={mode === "list"} class:with-widgets={workspaceTools}>
  {#if workspaceTools}
    <aside class="rail" aria-label="Home widgets">
      <TodayAgenda id={`${atlasId}-agenda`} {revision} {onview} />
      <HomeQuickNote id={`${atlasId}-note`} {onview} />
    </aside>
  {/if}
  <div
    class="stage"
    class:drop-target={!!draggedSection && hidden.has(draggedSection) && mode === "graph"}
    role="region"
    data-kitty-panel="graph"
    tabindex="-1"
    aria-label={look === "brain" ? "Brain of every record and edit in your workspace" : `${look === "2d" ? "2D" : "3D"} atlas of your workspace`}
    aria-describedby={`${atlasId}-description`}
    ondragover={sectionDragOver}
    ondrop={sectionDrop}
    onpointerdowncapture={stagePointerDown}
  >
    <p id={`${atlasId}-description`} class="sr-only">
      {#if look === "brain"}Every record and every edit in your workspace as a point of light. Drag to rotate, scroll to zoom; the View panel changes the layout and the legend highlights one section.{:else}A map of workspace sections. {look === "2d" ? "Drag nodes to move, drag the background to pan" : "Drag to rotate"}, scroll to zoom, select a section to open its map. Drag a node onto the Sections list to hide it; each section's options menu shows or hides it.{/if}
    </p>
    <!-- Hidden under the Brain, whose stage is transparent. -->
    <div class="canvas" class:behind={look === "brain"} bind:this={container}></div>
    {#if look === "brain"}
      <BrainStage bind:this={brain} active={pane.active} {revision} />
    {:else if mode === "list"}
      <div class="fallback">
        <p class="notice" role="status">
          <span aria-hidden="true">!</span>
          {notice}
        </p>
        {@render sectionList(true)}
      </div>
    {:else}
      <div class="stage-caption" aria-hidden="true">
        <span>{#if draggedSection && hidden.has(draggedSection)}Drop to show {sections.flatMap((group) => group.items).find((item) => item.id === draggedSection)?.label} on the atlas{:else if mode === "graph" && !graph.nodes.length}Every section is hidden · drag one here from Sections to show it{:else}{look === "2d" ? "Drag a node to move · drag background to pan · scroll or pinch to zoom" : "Drag to rotate · scroll to zoom · Ctrl + ← / → to turn"} · drag a node onto Sections to hide it{/if}</span>
      </div>
      {#if mode === "loading"}<div class="stage-loading"><Loader kind="eyes" label="Drawing your atlas…" /></div>{/if}
      {#if hovered}<p class="stage-selection" aria-hidden="true">{hovered.label}</p>{/if}
    {/if}
  </div>

  <aside class="rail atlas-panel" aria-label="Workspace">
    {#if terminal}<HomeAgents id={`${atlasId}-agents`} {onview} />{/if}
    <HomeWidget id={`${atlasId}-workspace`} title="Workspace" eyebrow="atlas">
    <GraphLookToggle brain />

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

    <details
      class="block"
      class:drop-hide={!!nodeOverList}
      bind:this={sectionsBlock}
      open={mode === "list" ? undefined : true}
    >
      <summary>Sections{#if nodeOverList}<span class="drop-hint">Drop to hide {nodeOverList}</span>{/if}</summary>
      {#if mode === "list"}<p class="muted small">
          The full list is shown in the main area.
        </p>{:else}{@render sectionList(false)}{/if}
    </details>
    </HomeWidget>
  </aside>
</div>

<svelte:window
  onpointerdown={(event) => {
    if (rowMenu && event.button !== 2 && !rowMenuElement?.contains(event.target as Node)) closeRowMenu(false);
  }}
/>
{#if rowMenu}
  {@const menu = rowMenu}
  <div
    bind:this={rowMenuElement}
    class="row-menu-popup"
    role="menu"
    tabindex="-1"
    aria-label={`${menu.label} options`}
    style:left={`${menu.x}px`}
    style:top={`${menu.y}px`}
    onkeydown={rowMenuKey}
  >
    <button role="menuitem" tabindex="-1" onclick={() => { closeRowMenu(false); onview(menu.view); }}>Open</button>
    <button role="menuitem" tabindex="-1" onclick={() => toggleSection(menu.view)}
      >{hidden.has(menu.view) ? "Show on the atlas" : "Hide from the atlas"}</button
    >
  </div>
{/if}

<style>
  /* Q18 (layout C): the atlas in the centre with two narrow rails that scroll on
     their own. Fills the room App gives it (zero basis: a rail never sizes the
     row). The floor keeps a cramped graph usable. */
  .atlas {
    --rail-left: clamp(180px, 20vw, 280px);
    --rail-right: clamp(196px, 22vw, 300px);
    flex: 1 1 0px;
    min-height: 120px;
    display: grid;
    grid-template-columns: minmax(0, 1fr) var(--rail-right);
    grid-template-rows: minmax(0, 1fr);
    gap: 12px;
  }
  .atlas.with-widgets {
    grid-template-columns: var(--rail-left) minmax(0, 1fr) var(--rail-right);
  }
  .stage {
    position: relative;
    min-width: 0;
    min-height: 0;
    overflow: hidden;
    border-radius: 14px;
    background: var(--canvas);
  }
  /* Drawn over the canvas so the canvas still fills the stage edge to edge. */
  .stage::after {
    content: "";
    position: absolute;
    inset: 0;
    z-index: 2;
    border: 1px solid var(--hairline);
    border-radius: inherit;
    pointer-events: none;
  }
  .rail {
    min-width: 0;
    min-height: 0;
    display: flex;
    flex-direction: column;
    gap: 14px;
    padding-right: 2px;
    overflow-y: auto;
    overscroll-behavior: contain;
    scrollbar-width: thin;
    scrollbar-color: var(--hairline) transparent;
  }
  .rail > :global(*) {
    flex: none;
  }
  .canvas {
    position: absolute;
    inset: 0;
  }
  .canvas.behind {
    visibility: hidden;
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
    border-top: 1px solid var(--hairline);
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
  .row-menu {
    width: 26px;
    background: transparent;
    border: 1px solid transparent;
    color: var(--subtle);
  }
  .row-menu:hover,
  .row-menu:focus-visible,
  .row-menu[aria-expanded="true"] {
    color: var(--ink);
    border-color: var(--border);
  }
  .section-list li.hidden-section {
    opacity: 0.45;
  }
  .section-list li.hidden-section:hover,
  .section-list li.hidden-section:focus-within {
    opacity: 0.8;
  }
  .section-list a[draggable="true"] {
    cursor: grab;
  }
  .block.drop-hide {
    outline: 1px dashed var(--accent);
    outline-offset: 2px;
    background: var(--surface);
  }
  .drop-hint {
    margin-left: 8px;
    color: var(--accent);
    font-size: 11px;
  }
  .stage.drop-target {
    outline: 1px dashed var(--accent);
    outline-offset: -8px;
  }
  .stage.drop-target .stage-caption {
    color: var(--ink);
  }
  .row-menu-popup {
    position: fixed;
    z-index: 1000;
    min-width: 190px;
    padding: 0.3rem;
    border: 1px solid var(--border);
    border-radius: 7px;
    background: var(--surface-raised);
    box-shadow: var(--overlay-shadow);
  }
  .row-menu-popup button {
    display: block;
    width: 100%;
    border: 0;
    border-radius: 4px;
    background: transparent;
    color: var(--ink);
    padding: 0.6rem 0.7rem;
    font: inherit;
    font-size: 0.85rem;
    text-align: left;
    cursor: pointer;
  }
  .row-menu-popup button:hover,
  .row-menu-popup button:focus-visible {
    background: var(--wash);
    outline-color: var(--accent);
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
  /* A short window leaves the graph too little room for the gesture hint. */
  @media (max-height: 640px) {
    .stage-caption {
      display: none;
    }
  }
  /* Phone widths stack. The desktop minimum window (760×500) stays side by side
     even when display scaling lays it out narrower in CSS pixels. */
  @media (max-width: 599.98px) {
    .atlas,
    .atlas.with-widgets {
      flex: none;
      grid-template-columns: minmax(0, 1fr);
      grid-template-rows: none;
    }
    .stage {
      order: -1;
      height: 58vh;
      min-height: 320px;
    }
    .list-mode .stage {
      height: auto;
    }
    .rail {
      overflow: visible;
    }
    .stage-caption {
      display: none;
    }
    .fallback {
      padding: 16px;
    }
  }
</style>
