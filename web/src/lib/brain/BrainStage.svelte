<!-- The Brain look on home: every record and every edit in the workspace as one glowing point. -->
<script lang="ts">
  import { onMount } from "svelte";
  import { api, type BrainData, type BrainNode } from "../api";
  import { webglAvailable } from "../graph3d";
  import type { BrainScene } from "./brainScene";
  import { brainGroups, brainLines, brainScaffold, brainStyle, layoutBrain } from "./brainLayout";
  import { BRAIN_GROUPINGS, DEFAULT_BRAIN_VIEW, loadBrainView, saveBrainView, type BrainView } from "./brainSettings";
  import BrainControls from "./BrainControls.svelte";

  let { active = true, revision = 0 }: { active?: boolean; revision?: number } = $props();

  const LEGEND_LIMIT = 9;
  const reducedMotion = typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  let view = $state<BrainView>(loadBrainView());
  let data = $state.raw<BrainData | null>(null);
  let status = $state<"loading" | "ready" | "missing" | "error">("loading");
  let problem = $state("");
  let renderProblem = $state(webglAvailable() ? "" : "The brain needs WebGL, which this browser does not offer. 2D still maps your workspace.");
  let focusKey = $state<string | null>(null);
  let hover = $state.raw<{ node: BrainNode; x: number; y: number } | null>(null);
  let birth = $state<"pending" | "running" | "done">("pending");
  let showAll = $state(false);
  let root = $state<HTMLDivElement>();
  let host = $state<HTMLDivElement>();
  let scene = $state.raw<BrainScene | null>(null);
  const labelElements: HTMLElement[] = [];

  const layout = $derived(view.layout);
  const groupBy = $derived(view.groupBy);
  const model = $derived(data ? brainGroups(data, groupBy) : null);
  const placement = $derived(data && model ? layoutBrain(data, model, layout) : null);
  const style = $derived(data && model ? brainStyle(data, model) : null);
  const indexOf = $derived(new Map((data?.nodes ?? []).map((node, index) => [node.id, index])));
  const sectionLabels = $derived(new Map((data?.sections ?? []).map((section) => [section.id, section.label])));
  const focusIndex = $derived(model && focusKey !== null ? model.groups.findIndex((group) => group.key === focusKey) : -1);
  const legend = $derived(model ? (showAll ? model.groups : model.groups.slice(0, LEGEND_LIMIT)) : []);
  const grouping = $derived(BRAIN_GROUPINGS.find((item) => item.id === groupBy)?.label ?? "Section");

  function change(next: BrainView) {
    view = next;
    saveBrainView(next);
  }
  function reset() {
    focusKey = null;
    showAll = false;
    change({ ...DEFAULT_BRAIN_VIEW, panel: view.panel });
    scene?.resetView();
  }
  function toggleFocus(key: string) {
    focusKey = focusKey === key ? null : key;
  }

  let request = 0;
  async function load() {
    const current = ++request;
    try {
      const result = await api<BrainData>("/brain?limit=20000");
      if (current !== request) return;
      data = result;
      status = "ready";
    } catch (cause) {
      if (current !== request) return;
      if ((cause as { status?: number }).status === 404) status = "missing";
      else {
        status = "error";
        problem = (cause as Error).message;
      }
    }
  }

  /** Viewport position of node `id` as drawn now (test seam for hover). */
  export function screenPoint(id: string) {
    const index = indexOf.get(id);
    return index === undefined ? null : scene?.screenPoint(index) ?? null;
  }
  /** Layout position of node `id`, in layout units. */
  export function worldPoint(id: string) {
    const index = indexOf.get(id);
    return index === undefined || !placement ? null : Array.from(placement.positions.subarray(index * 3, index * 3 + 3));
  }
  /** Highlights a workspace section, when the brain is grouped by section or group. */
  export function focusSection(section: string) {
    const key = groupBy === "section" ? section : groupBy === "group" ? data?.sections.find((item) => item.id === section)?.group : undefined;
    if (key && model?.groups.some((group) => group.key === key)) focusKey = key;
  }

  function placeLabels() {
    if (!scene || !root || view.labels !== "sections") return;
    const box = root.getBoundingClientRect();
    labelElements.forEach((element, group) => {
      if (!element) return;
      const point = scene!.anchorPoint(group);
      element.style.visibility = point ? "visible" : "hidden";
      if (point) element.style.transform = `translate(${point.x - box.left}px, ${point.y - box.top}px) translate(-50%, -50%)`;
    });
  }

  onMount(() => {
    let disposed = false;
    let created: BrainScene | null = null;
    void load();
    if (!renderProblem)
      void import("./brainScene").then(({ createBrainScene }) => {
        if (disposed || !host) return;
        try {
          created = createBrainScene(host, {
            reducedMotion,
            onHover(index, x, y) {
              const node = index === null ? null : data?.nodes[index];
              const box = root?.getBoundingClientRect();
              hover = node && box && view.labels !== "off" ? { node, x: x - box.left, y: y - box.top } : null;
            },
            onPick(index) {
              const group = index === null || !model ? null : model.groups[model.groupOf[index]];
              focusKey = group ? (focusKey === group.key ? null : group.key) : null;
            },
            onFrame: placeLabels,
            onBirth(done) { birth = done ? "done" : "running"; },
          });
          scene = created;
        } catch (cause) {
          renderProblem = `The brain could not start WebGL (${(cause as Error).message}). 2D still maps your workspace.`;
        }
      });
    return () => {
      disposed = true;
      created?.destroy();
      scene = null;
    };
  });

  let seenRevision: number | undefined;
  $effect(() => {
    const current = revision;
    if (seenRevision !== undefined && seenRevision !== current) void load();
    seenRevision = current;
  });

  // Same data in a new layout or grouping eases into place; fresh data replaces it.
  let shownData: BrainData | null = null;
  $effect(() => {
    if (!scene || !data || !model || !placement || !style) return;
    scene.setCloud(
      {
        positions: placement.positions,
        colors: style.colors,
        sizes: style.sizes,
        seeds: style.seeds,
        births: placement.births,
        groups: Float32Array.from(model.groupOf),
      },
      placement,
      shownData === data,
    );
    shownData = data;
  });
  $effect(() => {
    if (!scene || !placement || !style) return;
    if (view.lines) {
      const lines = brainLines(placement, style.colors);
      scene.setLines(lines.positions, lines.colors);
    } else scene.setLines(null, null);
  });
  $effect(() => {
    if (!scene || !model) return;
    scene.setScaffold(view.scaffold ? brainScaffold(layout, model) : null);
  });
  $effect(() => { scene?.setFocus(focusIndex >= 0 ? focusIndex : null); });
  $effect(() => { scene?.setMotion(view.turning && !reducedMotion, view.speed); });
  $effect(() => { scene?.setPointSize(view.pointSize); });
  $effect(() => { scene?.setActive(active); });
  $effect(() => {
    if (focusKey !== null && model && !model.groups.some((group) => group.key === focusKey)) focusKey = null;
  });
  $effect(() => {
    if (view.labels === "off") hover = null;
    if (view.labels === "sections" && model) placeLabels();
  });

  function dateText(at: string | null) {
    const date = at ? new Date(at) : null;
    return date && !Number.isNaN(date.getTime())
      ? date.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" })
      : "undated";
  }
</script>

<div
  class="brain"
  bind:this={root}
  data-brain-stage
  data-birth={birth}
  data-layout={view.layout}
>
  <p class="sr-only">
    Every record and every edit in your workspace drawn as a point: records form the brain, edits run down the spinal cord, newest nearest the brain.
    Drag to rotate, scroll to zoom. The legend below highlights one {grouping.toLowerCase()} at a time.
  </p>
  <div class="canvas" bind:this={host}></div>

  {#if renderProblem}
    <div class="state"><p class="title">The brain cannot be drawn here</p><p>{renderProblem}</p></div>
  {:else if status === "missing"}
    <div class="state" role="status">
      <p class="title">The brain needs the newer backend</p>
      <p>Update kitty to see every record and edit as a point of light. 2D and 3D still map your workspace.</p>
    </div>
  {:else if status === "error"}
    <div class="state" role="alert">
      <p class="title">The brain could not load</p>
      <p>{problem}</p>
      <button type="button" onclick={() => { status = "loading"; void load(); }}>Retry</button>
    </div>
  {:else if status === "loading"}
    <div class="state" role="status"><p>Waking the brain…</p></div>
  {:else if data && !data.nodes.length}
    <div class="state" role="status">
      <p class="title">Nothing to draw yet</p>
      <p>Records and edits appear here as points while you work.</p>
    </div>
  {/if}

  {#if model && view.labels === "sections"}
    <div class="labels" aria-hidden="true">
      {#each model.groups as group, index (group.key)}
        <span bind:this={labelElements[index]} class:dim={focusIndex >= 0 && focusIndex !== index}>{group.label}</span>
      {/each}
    </div>
  {/if}

  {#if hover}
    <div class="tooltip" role="tooltip" style:transform={`translate(${hover.x + 14}px, ${hover.y + 14}px)`}>
      <strong>{hover.node.label}</strong>
      <span>{sectionLabels.get(hover.node.section) ?? hover.node.section} · {hover.node.type} · {dateText(hover.node.at)}</span>
    </div>
  {/if}

  {#if model && model.groups.length}
    <div class="legend" class:panel-open={view.panel}>
      <p>By {grouping}</p>
      <p>Records + edits</p>
      <ul aria-label="Legend">
        {#each legend as group (group.key)}
          <li>
            <button
              type="button"
              data-key={group.key}
              aria-pressed={focusKey === group.key}
              class:dim={focusKey !== null && focusKey !== group.key}
              title={`${group.records} records · ${group.edits} edits`}
              onclick={() => toggleFocus(group.key)}
            ><i style:background={group.hex} style:box-shadow={`0 0 6px ${group.hex}`}></i><span class="name">{group.label}</span><span class="count">{group.records + group.edits}</span></button>
          </li>
        {/each}
        {#if model.groups.length > LEGEND_LIMIT}
          <li><button type="button" class="more" onclick={() => (showAll = !showAll)}>{showAll ? "Fewer" : `${model.groups.length - LEGEND_LIMIT} more`}</button></li>
        {/if}
      </ul>
    </div>
  {/if}

  <BrainControls {view} {reducedMotion} onchange={change} onreset={reset} />
</div>

<style>
  .brain {
    position: absolute;
    inset: 0;
    z-index: 2;
    overflow: hidden;
    background: #000;
    color: #f2f2f2;
    font: 12px/1.35 var(--br-font-ui, system-ui, sans-serif);
  }
  .canvas {
    position: absolute;
    inset: 0;
  }
  .sr-only {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip-path: inset(50%);
    white-space: nowrap;
  }
  .state {
    position: absolute;
    inset: 50% auto auto 50%;
    transform: translate(-50%, -50%);
    width: min(360px, calc(100% - 40px));
    text-align: center;
    color: #9a9a9a;
    font-size: 12px;
  }
  .state p {
    margin: 0 0 6px;
  }
  .state .title {
    color: #f2f2f2;
    font-size: 13px;
  }
  .state button {
    margin-top: 6px;
    padding: 3px 10px;
    font: inherit;
    color: #f2f2f2;
    background: #000;
    border: 1px solid #444;
    border-radius: 3px;
    cursor: pointer;
  }
  .labels span {
    position: absolute;
    top: 0;
    left: 0;
    font-size: 10px;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    white-space: nowrap;
    color: rgba(255, 255, 255, 0.86);
    text-shadow: 0 0 6px #000, 0 0 2px #000;
    pointer-events: none;
    will-change: transform;
  }
  .labels span.dim {
    opacity: 0.3;
  }
  .tooltip {
    position: absolute;
    top: 0;
    left: 0;
    z-index: 4;
    display: grid;
    gap: 2px;
    max-width: 300px;
    padding: 6px 9px;
    background: rgba(0, 0, 0, 0.86);
    border: 1px solid #333;
    border-radius: 3px;
    pointer-events: none;
    font-size: 11px;
  }
  .tooltip strong {
    font-weight: 500;
    color: #fff;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .tooltip span {
    color: #a8a8a8;
  }
  /* Spans the stage bottom like mindcorpus' legend; only its buttons take the pointer. */
  .legend {
    position: absolute;
    left: 20px;
    bottom: 16px;
    z-index: 3;
    width: calc(100% - 40px);
    pointer-events: none;
  }
  .legend.panel-open {
    width: calc(100% - 290px);
  }
  .legend p {
    margin: 0 0 10px;
    font-size: 10.5px;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: #d6d6d6;
  }
  .legend p + p {
    margin-bottom: 12px;
    color: #8c8c8c;
  }
  .legend ul {
    display: grid;
    grid-template-columns: repeat(auto-fill, 172px);
    gap: 6px 28px;
    margin: 0;
    padding: 0;
    list-style: none;
  }
  .legend button {
    display: grid;
    grid-template-columns: 8px minmax(0, 1fr) auto;
    align-items: center;
    gap: 9px;
    width: 100%;
    padding: 2px 0;
    font: inherit;
    color: #e8e8e8;
    text-align: left;
    background: none;
    border: 0;
    cursor: pointer;
    pointer-events: auto;
    transition: opacity 0.2s;
  }
  .legend button i {
    width: 7px;
    height: 7px;
    border-radius: 50%;
  }
  .legend .name {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .legend .count {
    color: #cfcfcf;
    font-variant-numeric: tabular-nums;
  }
  .legend button.dim {
    opacity: 0.35;
  }
  .legend button[aria-pressed="true"] .name {
    color: #fff;
    text-decoration: underline;
    text-underline-offset: 3px;
  }
  .legend .more {
    display: block;
    color: #a8a8a8;
  }
  .legend button:hover .name,
  .legend .more:hover {
    color: #fff;
  }
  .legend button:focus-visible,
  .state button:focus-visible {
    outline: 1px solid #fff;
    outline-offset: 2px;
  }
  @media (prefers-reduced-motion: reduce) {
    .legend button {
      transition: none;
    }
  }
</style>
