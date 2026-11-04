<script lang="ts">
  import { onMount } from "svelte";
  import { Button } from "@nunera/bracket";
  import { api } from "./api";
  import { prefersReducedMotion } from "./appearance";
  import Icon from "./Icon.svelte";
  import HomeAgents from "./HomeAgents.svelte";
  import HomeQuickNote from "./HomeQuickNote.svelte";
  import HomeSections from "./HomeSections.svelte";
  import HomeUpNext from "./HomeUpNext.svelte";
  import HomeWidget from "./HomeWidget.svelte";
  import TodayAgenda from "./TodayAgenda.svelte";
  import HomeBrief from "./goals/HomeBrief.svelte";
  import { morningBrief } from "./goals/brief.svelte";
  import Select from "./ui/Select.svelte";
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
    webglAvailable,
    type GraphHandle,
    type GraphNode,
  } from "./graph3d";
  import {
    GRAPH_SETTINGS_EVENT,
    HOME_RAIL_KEY,
    graphLayout,
    graphPhysics,
    homeLook,
    homeRailCollapsed,
    setHomeLook,
    setHomeRailCollapsed,
    type HomeLook,
  } from "./graphPreferences";
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
  /** Q13: the rails hold only widgets; the right one also carries the modules-off notice. */
  const upNext = $derived((["calendar", "flashcards", "reminders"] as View[]).some((view) => viewEnabled(view, enabled)));
  const hasRight = $derived(terminal || upNext || !enabled.length);
  /** Q15 A: the morning brief leads the left rail, which shows for it even with Study tools off. */
  $effect(() => morningBrief.start());
  const hasLeft = $derived(workspaceTools || !!morningBrief.brief);
  const sections = $derived(atlasSections(visibleGroups(enabled), navigation));
  let hidden = $state(hiddenSections());
  const listed = (view: string): view is View =>
    sections.some((group) => group.items.some((item) => item.id === view));

  let atlas = $state<Atlas | null>(null);
  let atlasError = $state("");
  const layout = graphLayout();
  // Home is the Brain or the 2D atlas. The Brain replaces the stage; the 2D atlas then waits,
  // paused and hidden, underneath. Without WebGL there is only the 2D atlas.
  const supportsBrain = webglAvailable();
  let chosen = $state(homeLook());
  const look = $derived<HomeLook>(supportsBrain ? chosen : "2d");
  let lookError = $state("");
  function chooseLook(next: string) {
    try {
      setHomeLook(next as HomeLook);
      lookError = "";
    } catch {
      lookError = "This browser could not save the home look.";
    }
  }
  /** Q13: the side rail folds to an edge strip so the Brain widens; every home follows one choice. */
  let collapsed = $state(homeRailCollapsed());
  /** "Open module settings" lands on Settings' Modules page (`#settings/modules`). */
  function openModuleSettings() {
    onview("settings");
    if (location.hash === "#settings") history.replaceState(history.state, "", "#settings/modules");
  }
  function toggleRail() {
    try {
      setHomeRailCollapsed(!collapsed);
    } catch {
      collapsed = !collapsed;
    }
  }
  let brain = $state<BrainStage>();
  let sectionsControl = $state<HomeSections>();
  let sectionsOpen = $state(false);
  const physics = graphPhysics();
  let mode = $state<"loading" | "graph" | "list">("loading");
  let notice = $state("");
  let hovered = $state<GraphNode | null>(null);
  let selected = $state<GraphNode | null>(null);
  let container: HTMLDivElement;
  let handle = $state.raw<GraphHandle | null>(null);
  let pendingGroup: string | null = null;
  let draggedSection = $state<View | null>(null);
  let nodeDrag: { view: View; label: string } | null = null;
  let nodeOverList = $state<string | null>(null);

  const itemCounts = $derived.by(() => {
    const counts = new Map<View, number>();
    for (const item of atlas?.sections ?? [])
      if (item.count !== null && viewEnabled(item.view, enabled))
        counts.set(item.view, item.count + (counts.get(item.view) || 0));
    return counts;
  });
  const graph = $derived(atlasGraph(sections, hidden, visibleRelations(enabled), itemCounts));
  const current = $derived(selected?.id.startsWith("view:") ? (selected.id.slice(5) as View) : null);

  function sectionNode(view: View) {
    return graph.nodes.find((node) => node.id === `view:${view}`) ?? null;
  }
  /** The Brain finds any section; the 2D atlas only one it draws. */
  function locatable(view: View) {
    return look === "brain" ? listed(view) : mode === "graph" && !!sectionNode(view);
  }
  function locate(node: GraphNode) {
    selected = node;
    if (look === "brain") {
      if (node.id.startsWith("view:")) brain?.focusSection(node.id.slice(5));
    } else handle?.focus(node.id);
  }
  function locateSection(view: View) {
    const node = sectionNode(view);
    if (node) locate(node);
    else if (look === "brain") {
      selected = null;
      brain?.focusSection(view);
    }
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
  /** "/" opens Sections on its search. */
  export function focusSearch() {
    if (pane.active) void sectionsControl?.focusSearch();
  }
  /** Home's own keys: "]" folds or unfolds the side rail. */
  export function handleKey(event: KeyboardEvent): boolean {
    if (!pane.active || event.key !== "]" || !hasRight) return false;
    event.preventDefault();
    toggleRail();
    return true;
  }
  /** Screen position of a drawn node, or null when it is not on the atlas. */
  export function nodePoint(id: string) {
    return handle?.nodePoint(id) ?? null;
  }

  /** Q13: a node released over the Sections control (or its open list) is hidden. */
  function overList(clientX: number, clientY: number) {
    return !!sectionsControl?.contains(document.elementFromPoint(clientX, clientY));
  }
  function dropOnList(node: GraphNode, point: { clientX: number; clientY: number }) {
    if (!pane.active || !node.id.startsWith("view:")) return;
    const view = node.id.slice(5);
    if (listed(view) && overList(point.clientX, point.clientY)) setSectionHidden(view, true);
  }
  /** Tracks a pointer pressed on a section node so the Sections control can show it accepts the drop. */
  function stagePointerDown(event: PointerEvent) {
    const node = mode === "graph" && pane.active && look !== "brain" && !event.button ? handle?.nodeAt(event.clientX, event.clientY) : null;
    const view = node?.id.startsWith("view:") ? node.id.slice(5) : "";
    nodeDrag = listed(view) ? { view, label: node!.label } : null;
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
  function toggleSection(view: View) {
    setSectionHidden(view, !hidden.has(view));
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
      chosen = homeLook();
      collapsed = homeRailCollapsed();
    };
    window.addEventListener(GRAPH_SETTINGS_EVENT, syncSettings);
    // Every atlas, in this window's tabs or another window, follows the same choice.
    const syncHidden = () => { hidden = hiddenSections(); };
    const syncStorage = (event: StorageEvent) => {
      if (event.key === ATLAS_HIDDEN_KEY || event.key === null) syncHidden();
      if (event.key === HOME_RAIL_KEY || event.key === null) collapsed = homeRailCollapsed();
    };
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
          look: "2d",
          reducedMotion,
          active: pane.active && look !== "brain",
          onSelect: (node) => {
            if (!pane.active) return;
            if (node.id.startsWith("view:")) onview(node.id.slice(5) as View);
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
        notice = (cause as Error).message;
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

{#snippet sectionsButton()}
  <div class="corner" data-home-corner>
    <HomeSections
      bind:this={sectionsControl}
      bind:open={sectionsOpen}
      {sections}
      {hidden}
      counts={itemCounts}
      {current}
      holding={nodeOverList}
      error={atlasError}
      {locatable}
      {onview}
      onlocate={locateSection}
      ontoggle={toggleSection}
      ondrag={(view) => (draggedSection = view)}
      onretry={loadAtlas}
    />
  </div>
{/snippet}

{#snippet lookSwitch()}
  <div class="corner" data-home-corner>
    {#if lookError}<span class="look-error" role="alert">{lookError}</span>{/if}
    <Select
      label="Home look"
      kind="segments"
      value={look}
      options={[{ value: "brain", label: "Brain", disabled: !supportsBrain }, { value: "2d", label: "2D" }]}
      onchange={chooseLook}
    />
  </div>
{/snippet}

<div
  class="atlas"
  class:list-mode={mode === "list"}
  class:with-left={hasLeft}
  class:with-right={hasRight}
  class:rail-collapsed={hasRight && collapsed}
>
  {#if hasLeft}
    <aside class="rail" aria-label="Home widgets">
      <HomeBrief id={`${atlasId}-brief`} />
      {#if workspaceTools}
        <TodayAgenda id={`${atlasId}-agenda`} {revision} {onview} />
        <HomeQuickNote id={`${atlasId}-note`} {onview} />
      {/if}
    </aside>
  {/if}
  <div
    class="stage"
    class:drop-target={!!draggedSection && hidden.has(draggedSection) && mode === "graph"}
    role="region"
    data-kitty-panel="graph"
    tabindex="-1"
    aria-label={look === "brain" ? "Brain of every record and edit in your workspace" : "2D atlas of your workspace"}
    aria-describedby={`${atlasId}-description`}
    ondragover={sectionDragOver}
    ondrop={sectionDrop}
    onpointerdowncapture={stagePointerDown}
  >
    <p id={`${atlasId}-description`} class="sr-only">
      {#if look === "brain"}Every record and every edit in your workspace as a point of light in a glowing neural-network brain. Drag to rotate, scroll to zoom; the View panel changes the layout and the legend highlights one section.{:else}A map of workspace sections. Drag nodes to move, drag the background to pan, scroll to zoom, select a section to open its map. Drag a node onto Sections to hide it; each section's options menu shows or hides it.{/if}
    </p>
    <!-- Hidden under the Brain, whose stage is transparent. -->
    <div class="canvas" class:behind={look === "brain"} bind:this={container}></div>
    {#if look === "brain"}
      <BrainStage bind:this={brain} active={pane.active} {revision} start={sectionsButton} end={lookSwitch} />
    {:else}
      <div class="corners">
        {@render sectionsButton()}
        {@render lookSwitch()}
      </div>
      {#if mode === "list"}
        <div class="fallback">
          <p class="notice" role="status"><span aria-hidden="true">!</span> {notice}</p>
          <HomeSections
            inline
            {sections}
            {hidden}
            counts={itemCounts}
            {locatable}
            {onview}
            onlocate={locateSection}
            ontoggle={toggleSection}
            ondrag={() => {}}
            onretry={loadAtlas}
          />
        </div>
      {:else}
        {#if draggedSection && hidden.has(draggedSection)}
          <p class="stage-caption" aria-hidden="true">Drop to show {sections.flatMap((group) => group.items).find((item) => item.id === draggedSection)?.label} on the atlas</p>
        {:else if mode === "graph" && !graph.nodes.length}
          <p class="stage-caption">Every section is hidden. Drag one here from Sections.</p>
        {/if}
        {#if mode === "loading"}<div class="stage-loading"><Loader kind="view" label="Drawing your atlas…" /></div>{/if}
        {#if hovered}<p class="stage-selection" aria-hidden="true">{hovered.label}</p>{/if}
      {/if}
    {/if}
  </div>

  {#if hasRight}
    <div class="side" class:collapsed>
      <button
        type="button"
        class="rail-toggle"
        aria-label="Side widgets"
        aria-expanded={!collapsed}
        aria-controls={collapsed ? undefined : `${atlasId}-activity`}
        title={collapsed ? "Show the side widgets (])" : "Hide the side widgets (])"}
        onclick={toggleRail}><span class="chevron" aria-hidden="true"></span></button
      >
      {#if !collapsed}
        <aside id={`${atlasId}-activity`} class="rail" aria-label="Activity">
          {#if terminal}<HomeAgents id={`${atlasId}-agents`} {onview} />{/if}
          {#if upNext}<HomeUpNext id={`${atlasId}-next`} {revision} {onview} />{/if}
          {#if !enabled.length}
            <HomeWidget id={`${atlasId}-modules`} title="Modules are off">
              <p class="note">Turn on modules in Settings; they start after a restart.</p>
              <Button intent="primary" size="sm" onclick={openModuleSettings}>Open module settings <Icon name="arrow" size={14} /></Button>
            </HomeWidget>
          {/if}
        </aside>
      {/if}
    </div>
  {/if}
</div>

<style>
  /* Q18 (layout C): the atlas in the centre with narrow widget rails that scroll on
     their own. Fills the room App gives it (zero basis: a rail never sizes the
     row). The floor keeps a cramped graph usable. Q13: the right rail folds to a
     thin strip so the stage takes its room. */
  .atlas {
    --rail-left: clamp(180px, 20vw, 280px);
    --rail-right: clamp(196px, 22vw, 300px);
    --rail-strip: 18px;
    flex: 1 1 0px;
    min-height: 120px;
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    grid-template-rows: minmax(0, 1fr);
    gap: 12px;
  }
  .atlas.with-left {
    grid-template-columns: var(--rail-left) minmax(0, 1fr);
  }
  .atlas.with-right {
    grid-template-columns: minmax(0, 1fr) var(--rail-right);
  }
  .atlas.with-left.with-right {
    grid-template-columns: var(--rail-left) minmax(0, 1fr) var(--rail-right);
  }
  .atlas.rail-collapsed {
    --rail-right: var(--rail-strip);
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
  /* The Brain on its Transparent background: neither the stage's canvas colour nor the atlas's
     glass sheet (fill, blur, inner shadow) paints behind it, so the theme's backdrop shows through. */
  .atlas:has(> .stage > :global([data-brain-stage][data-background="transparent"])) {
    background: none;
    box-shadow: none;
    backdrop-filter: none;
    -webkit-backdrop-filter: none;
  }
  .stage:has(> :global([data-brain-stage][data-background="transparent"])) {
    background: none;
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
  .side {
    position: relative;
    min-width: 0;
    min-height: 0;
    display: grid;
    grid-template-rows: minmax(0, 1fr);
  }
  /* Expanded: a small tab in the gap on the rail's inner edge. Collapsed: the whole strip. */
  .rail-toggle {
    position: absolute;
    top: 50%;
    left: -13px;
    z-index: 3;
    display: grid;
    place-items: center;
    width: 14px;
    height: 44px;
    padding: 0;
    border: 1px solid var(--hairline);
    border-radius: var(--r-chip, 6px);
    background: color-mix(in srgb, var(--solid-panel, #1c1c1c) 70%, transparent);
    color: var(--muted);
    transform: translateY(-50%);
    cursor: pointer;
  }
  .rail-toggle:hover,
  .rail-toggle:focus-visible {
    border-color: var(--br-lineHot, var(--ink));
    color: var(--ink);
  }
  .side.collapsed .rail-toggle {
    inset: 0;
    width: auto;
    height: auto;
    transform: none;
    border-radius: var(--r-panel, 14px);
    background: transparent;
  }
  /* Points the way the rail moves: right to hide it, left to bring it back. */
  .chevron {
    width: 6px;
    height: 6px;
    margin-left: -3px;
    border-top: 1.5px solid currentColor;
    border-right: 1.5px solid currentColor;
    rotate: 45deg;
  }
  .side.collapsed .chevron {
    margin: 0 -3px 0 0;
    rotate: -135deg;
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
  /* The 2D atlas's corner row; the Brain draws the same controls in its own. */
  .corners {
    position: absolute;
    inset: 14px 14px auto;
    z-index: 3;
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 6px;
    pointer-events: none;
  }
  .corner {
    display: flex;
    align-items: center;
    gap: 6px;
    pointer-events: auto;
  }
  .look-error {
    color: var(--error);
    font-size: 11px;
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
    top: 54px;
    left: 20px;
    max-width: calc(100% - 40px);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    color: var(--ink);
  }
  .fallback {
    position: relative;
    height: 100%;
    overflow: auto;
    padding: 56px 28px 28px;
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
  .note {
    margin: 0 0 8px;
    color: var(--muted);
    font-size: 12px;
  }
  .stage.drop-target {
    outline: 1px dashed var(--accent);
    outline-offset: -8px;
  }
  .stage.drop-target .stage-caption {
    color: var(--ink);
  }
  /* Phone widths stack. The desktop minimum window (760×500) stays side by side
     even when display scaling lays it out narrower in CSS pixels. */
  @media (max-width: 599.98px) {
    .atlas,
    .atlas.with-left,
    .atlas.with-right,
    .atlas.with-left.with-right {
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
    .side {
      gap: 8px;
    }
    .rail-toggle,
    .side.collapsed .rail-toggle {
      position: static;
      inset: auto;
      width: 100%;
      height: 24px;
      transform: none;
    }
    .chevron {
      rotate: 135deg;
    }
    .side.collapsed .chevron {
      rotate: -45deg;
    }
    .fallback {
      padding: 56px 16px 16px;
    }
  }
</style>
