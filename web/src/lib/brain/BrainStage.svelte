<!-- The Brain look on home: every record and every edit in the workspace as one glowing point. -->
<script lang="ts">
  import { onMount, tick, type Snippet } from "svelte";
  import { api, type BrainData, type BrainNode } from "../api";
  import { webglAvailable } from "../graph3d";
  import { THEME_EVENT } from "../themes";
  import type { BrainBackdrop, BrainScene } from "./brainScene";
  import { brainGroups, brainLines, brainScaffold, brainShell, brainStyle, BRAIN_HUB, BRAIN_PALETTE, BRAIN_REACH, expandPalette, isolateModel, layoutBrain, mixColours, type BrainPalette, type Rgb } from "./brainLayout";
  import { BRAIN_GROUPINGS, DEFAULT_BRAIN_VIEW, loadBrainColours, loadBrainView, saveBrainColours, saveBrainView, type BrainView } from "./brainSettings";
  import BrainControls from "./BrainControls.svelte";
  import Loader from "../Loader.svelte";

  let {
    active = true,
    revision = 0,
    start,
    end,
  }: {
    active?: boolean;
    revision?: number;
    /** Home's corner controls: `start` top-left, `end` in View's row top-right. */
    start?: Snippet;
    end?: Snippet;
  } = $props();

  const LEGEND_LIMIT = 9;
  const reducedMotion = typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  let view = $state<BrainView>(loadBrainView());
  let picks = $state(loadBrainColours());
  let data = $state.raw<BrainData | null>(null);
  let status = $state<"loading" | "ready" | "missing" | "error">("loading");
  let problem = $state("");
  let renderProblem = $state(webglAvailable() ? "" : "The brain needs WebGL, which this browser does not offer. 2D still maps your workspace.");
  let focusKey = $state<string | null>(null);
  /** The group shown on its own (clicked sphere, sector or lobe), by key. */
  let isolatedKey = $state<string | null>(null);
  let hoverGroup = $state<number | null>(null);
  /** Legend entry whose kinds key is open (hover or keyboard focus). */
  let keyFor = $state<string | null>(null);
  let themeTick = $state(0);
  let hover = $state.raw<{ node: BrainNode; x: number; y: number } | null>(null);
  let birth = $state<"pending" | "running" | "done">("pending");
  let showAll = $state(false);
  let root = $state<HTMLDivElement>();
  let host = $state<HTMLDivElement>();
  let scene = $state.raw<BrainScene | null>(null);
  const labelElements: HTMLElement[] = [];

  const layout = $derived(view.layout);
  const groupBy = $derived(view.groupBy);
  const themeColours = $derived.by(() => {
    void themeTick;
    return readTheme();
  });
  /** Groups and kinds without colour: what the layout depends on, so recolouring never moves a point. */
  const structure = $derived(data ? brainGroups(data, groupBy) : null);
  /** The same groups coloured by the theme and the user's picks. */
  const allGroups = $derived(data ? brainGroups(data, groupBy, { ...themeColours, overrides: picks[groupBy] }) : null);
  const isolated = $derived(allGroups && isolatedKey !== null ? allGroups.groups.findIndex((group) => group.key === isolatedKey) : -1);
  /** What is on stage: every group, or the isolated group's kinds as groups of their own. */
  const model = $derived(allGroups && isolated >= 0 ? isolateModel(allGroups, isolated, data!) : allGroups);
  const placement = $derived(data && structure ? layoutBrain(data, structure, layout, isolated >= 0 ? isolated : null) : null);
  const style = $derived(data && allGroups ? brainStyle(data, allGroups) : null);
  const colours = $derived(style?.colors ?? null);
  /** The brain's rim and its group-less filaments: its groups' colours blended (by records) and lifted toward white; dark ink on a light backdrop. */
  const rimColour = $derived.by((): Rgb => {
    if (backdropTone.cover > 0.5) return backdropTone.ink;
    const groups = model?.groups ?? [];
    const weight = groups.reduce((sum, group) => sum + group.records + group.edits * 0.2, 0) || 1;
    const mean = [0, 1, 2].map((c) => groups.reduce((sum, group) => sum + group.rgb[c] * (group.records + group.edits * 0.2), 0) / weight);
    return mean.map((value) => value + (1 - value) * 0.4) as Rgb;
  });
  /** Each network segment in its group's colour, blending toward the group at its far end. */
  const networkColours = $derived.by(() => {
    const network = placement?.network;
    if (!network || !model) return null;
    const tone = (group: number) => model.groups[group]?.rgb ?? rimColour;
    const out = new Float32Array(network.count * 3);
    for (let k = 0; k < network.count; k += 1) {
      const from = tone(network.groups[k]), to = tone(network.others[k]), t = network.mixes[k];
      out[k * 3] = from[0] + (to[0] - from[0]) * t;
      out[k * 3 + 1] = from[1] + (to[1] - from[1]) * t;
      out[k * 3 + 2] = from[2] + (to[2] - from[2]) * t;
    }
    return out;
  });
  /** Swatches offered by the colour editor: the theme's colours stretched to a dozen. */
  const swatches = $derived(themeColours.seeds ? expandPalette(themeColours.seeds, 12) : BRAIN_PALETTE);
  const indexOf = $derived(new Map((data?.nodes ?? []).map((node, index) => [node.id, index])));
  const sectionLabels = $derived(new Map((data?.sections ?? []).map((section) => [section.id, section.label])));
  const focusIndex = $derived(model && focusKey !== null ? model.groups.findIndex((group) => group.key === focusKey) : -1);
  const legend = $derived(model ? (showAll ? model.groups : model.groups.slice(0, LEGEND_LIMIT)) : []);
  const grouping = $derived(BRAIN_GROUPINGS.find((item) => item.id === groupBy)?.label ?? "Section");
  const isolatedGroup = $derived(allGroups && isolated >= 0 ? allGroups.groups[isolated] : null);
  const hoverKey = $derived(model && hoverGroup !== null ? model.groups[hoverGroup]?.key ?? null : null);

  function change(next: BrainView) {
    view = next;
    saveBrainView(next);
  }
  function reset() {
    focusKey = null;
    isolatedKey = null;
    showAll = false;
    change({ ...DEFAULT_BRAIN_VIEW, panel: view.panel });
    scene?.resetView();
  }
  function toggleFocus(key: string) {
    focusKey = focusKey === key ? null : key;
  }
  function recolour(key: string, hex: string | null) {
    const mode = { ...picks[groupBy] };
    if (hex) mode[key] = hex.toLowerCase();
    else delete mode[key];
    picks = { ...picks, [groupBy]: mode };
    saveBrainColours(picks);
  }
  function resetColours() {
    picks = { ...picks, [groupBy]: {} };
    saveBrainColours(picks);
  }
  const THEME_SEEDS = ["--c1", "--c2", "--c3", "--c4", "--c5", "--c6", "--br-structure", "--br-signal", "--br-go", "--br-reference", "--br-caution", "--br-alternate", "--br-danger"];
  /** The active theme's colours, and warm-leaning tones from its roles for what edits did. */
  function readTheme(): BrainPalette {
    if (typeof document === "undefined") return {};
    const css = getComputedStyle(document.documentElement);
    const value = (name: string) => css.getPropertyValue(name).trim().toLowerCase();
    const seeds = THEME_SEEDS.map(value).filter((hex) => /^#[0-9a-f]{6}$/.test(hex));
    if (seeds.length < 3) return {};
    const [danger, caution, signal, go, alternate] = ["--br-danger", "--br-caution", "--br-signal", "--br-go", "--br-alternate"].map(value);
    const hex = (text: string) => /^#[0-9a-f]{6}$/.test(text);
    if (![danger, caution, signal, go, alternate].every(hex)) return { seeds };
    return {
      seeds,
      actions: {
        remove: danger,
        move: mixColours(danger, caution, 0.5),
        save: mixColours(signal, caution, 0.45),
        create: caution,
        progress: mixColours(go, caution, 0.45),
        review: mixColours(alternate, danger, 0.4),
      },
    };
  }
  function isolate(key: string | null) {
    isolatedKey = key;
    focusKey = null;
    hoverGroup = null;
    keyFor = null;
    showAll = false;
  }
  function onkeydown(event: KeyboardEvent) {
    if (event.key === "Escape" && active && isolatedKey !== null && !event.defaultPrevented) {
      event.preventDefault();
      isolate(null);
    }
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
  /** Colour of node `id` as drawn (test seam). */
  export function pointColour(id: string) {
    const index = indexOf.get(id);
    return index === undefined || !colours ? null : Array.from(colours.subarray(index * 3, index * 3 + 3));
  }
  /** Layout position of node `id`, in layout units. */
  export function worldPoint(id: string) {
    const index = indexOf.get(id);
    return index === undefined || !placement ? null : Array.from(placement.positions.subarray(index * 3, index * 3 + 3));
  }
  /** Highlights a workspace section, when the brain is grouped by section or group. */
  export function focusSection(section: string) {
    const key = groupBy === "section" ? section : groupBy === "group" ? data?.sections.find((item) => item.id === section)?.group : undefined;
    if (!key || !allGroups?.groups.some((group) => group.key === key)) return;
    if (isolatedKey !== null) isolate(null);
    focusKey = key;
  }

  /** Relative luminance of what shows through the canvas: the stage's ancestors' backgrounds composited. */
  function parseColour(text: string): [number, number, number, number] | null {
    const srgb = /color\(srgb\s+([^)]+)\)/.exec(text);
    const rgb = /rgba?\(([^)]+)\)/.exec(text);
    const parts = (srgb ?? rgb)?.[1].split(/[\s,/]+/).filter(Boolean).map(Number.parseFloat);
    if (!parts || parts.length < 3 || parts.some(Number.isNaN)) return null;
    const scale = srgb ? 1 : 255;
    return [parts[0] / scale, parts[1] / scale, parts[2] / scale, parts[3] ?? 1];
  }
  function backdrop(): BrainBackdrop {
    if (view.background === "black" || !root) return { cover: 0, ink: [0.62, 0.62, 0.62] };
    const seen = [0, 0, 0];
    let remaining = 1;
    for (let element = root.parentElement; element && remaining > 0.02; element = element.parentElement) {
      const colour = parseColour(getComputedStyle(element).backgroundColor);
      if (!colour || !colour[3]) continue;
      for (let c = 0; c < 3; c += 1) seen[c] += remaining * colour[3] * colour[c];
      remaining *= 1 - colour[3];
    }
    // Whatever is left is the desktop behind a transparent window: assume a mid grey.
    for (let c = 0; c < 3; c += 1) seen[c] += remaining * 0.5;
    const linear = seen.map((value) => (value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4));
    const luminance = 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2];
    const cover = Math.min(1, Math.max(0, (luminance - 0.12) / 0.23));
    const ink = parseColour(getComputedStyle(root).color) ?? [0.9, 0.9, 0.9, 1];
    // On a light backdrop the scaffold needs dark ink, even under a dark theme's pale text colour.
    const tone = cover > 0.5 ? Math.min(1, 0.32 / Math.max(0.01, Math.max(ink[0], ink[1], ink[2]))) : 0.68;
    return { cover: cover * cover * (3 - 2 * cover), ink: [ink[0] * tone, ink[1] * tone, ink[2] * tone] };
  }
  const backdropTone = $derived.by(() => {
    void themeTick;
    void root;
    return backdrop();
  });

  /** Label sizes, measured once per label set (transforms never change them). */
  let labelSizes = new WeakMap<HTMLElement, [number, number]>();
  /**
   * Floats each label at its group's anchor without overlapping another: the hovered group first, then
   * the largest; a label that collides is nudged up or down a line, and hidden if that still collides.
   */
  function placeLabels() {
    if (!scene || !root || view.labels === "off" || !model) return;
    const box = root.getBoundingClientRect();
    // The View panel, legend and back pill are taken from the start: labels never hide under them.
    const taken: [number, number, number, number][] = [...root.querySelectorAll(".controls, .legend p, .legend > ul > li, .back")].map((element) => {
      const rect = element.getBoundingClientRect();
      return [rect.left - box.left, rect.top - box.top, rect.right - box.left, rect.bottom - box.top];
    });
    const clear = (l: number, t: number, r: number, b: number) =>
      l >= 0 && t >= 0 && r <= box.width && b <= box.height && taken.every(([L, T, R, B]) => r + 4 <= L || l >= R + 4 || b + 2 <= T || t >= B + 2);
    const order = model.groups.map((_, group) => group);
    if (hoverGroup !== null) order.sort((a, b) => Number(b === hoverGroup) - Number(a === hoverGroup));
    for (const group of order) {
      const element = labelElements[group];
      if (!element) continue;
      const point = view.labels === "hover" && hoverGroup !== group ? null : scene.anchorPoint(group);
      let spot: [number, number] | null = null;
      if (point) {
        let size = labelSizes.get(element);
        if (!size) labelSizes.set(element, (size = [element.offsetWidth, element.offsetHeight]));
        const [width, height] = size;
        const left = point.x - box.left - width / 2;
        for (const shift of [0, -1, 1, -2, 2]) {
          const top = point.y - box.top - height / 2 + shift * (height + 2);
          if (clear(left, top, left + width, top + height)) {
            spot = [left, top];
            taken.push([left, top, left + width, top + height]);
            break;
          }
        }
      }
      element.style.visibility = spot ? "visible" : "hidden";
      if (spot) element.style.transform = `translate(${spot[0]}px, ${spot[1]}px)`;
    }
  }

  /** The open View panel covers the stage's right edge: the scene frames the brain left of it. */
  function measureInset() {
    if (!scene || !root) return;
    const panel = view.panel ? root.querySelector(".controls .panel") : null;
    scene.setInset(panel ? root.getBoundingClientRect().right - panel.getBoundingClientRect().left + 8 : 0);
  }

  onMount(() => {
    let disposed = false;
    let created: BrainScene | null = null;
    void load();
    const stageSize = new ResizeObserver(() => measureInset());
    if (root) stageSize.observe(root);
    if (!renderProblem)
      void import("./brainScene").then(({ createBrainScene }) => {
        if (disposed || !host) return;
        try {
          created = createBrainScene(host, {
            reducedMotion,
            onHover(index, group, x, y) {
              const node = index === null ? null : data?.nodes[index];
              const box = root?.getBoundingClientRect();
              hover = node && box && view.labels !== "off" ? { node, x: x - box.left, y: y - box.top } : null;
              hoverGroup = group;
            },
            onPick(_, group) {
              // A click opens the group under the pointer on its own; inside it, a click highlights one kind.
              const picked = group === null || !model ? null : model.groups[group];
              if (isolatedKey === null) {
                if (picked) isolate(picked.key);
                else focusKey = null;
              } else if (picked) toggleFocus(picked.key);
            },
            onFrame: placeLabels,
            onBirth(done) { birth = done ? "done" : "running"; },
          });
          scene = created;
        } catch (cause) {
          renderProblem = `The brain could not start WebGL (${(cause as Error).message}). 2D still maps your workspace.`;
        }
      });
    const onTheme = () => (themeTick += 1);
    window.addEventListener(THEME_EVENT, onTheme);
    return () => {
      disposed = true;
      stageSize.disconnect();
      window.removeEventListener(THEME_EVENT, onTheme);
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
    if (!scene || !data || !placement || !colours || !style) return;
    scene.setCloud(
      {
        positions: placement.positions,
        colors: colours!,
        sizes: style.sizes,
        seeds: style.seeds,
        births: placement.births,
        groups: placement.groups,
      },
      placement,
      shownData === data,
    );
    shownData = data;
  });
  $effect(() => {
    if (!scene || !placement || !colours) return;
    if (view.lines) {
      const lines = brainLines(placement, colours!);
      scene.setLines(lines.positions, lines.colors);
    } else scene.setLines(null, null);
  });
  $effect(() => {
    if (!scene || !model) return;
    scene.setScaffold(view.scaffold ? brainScaffold(layout, model) : null);
  });
  $effect(() => {
    if (!scene) return;
    scene.setNetwork(placement?.network && networkColours ? { network: placement.network, colors: networkColours, hub: BRAIN_HUB, reach: BRAIN_REACH } : null);
  });
  $effect(() => {
    if (!scene) return;
    scene.setShell(layout === "anatomy" && view.scaffold ? { ...brainShell(), color: rimColour } : null);
  });
  $effect(() => { scene?.setFocus(focusIndex >= 0 ? focusIndex : null); });
  $effect(() => { scene?.setHover(hoverGroup); });
  $effect(() => { scene?.setBackdrop(backdropTone); });
  $effect(() => {
    if (isolatedKey !== null && allGroups && isolated < 0) isolatedKey = null;
  });
  $effect(() => { scene?.setMotion(view.turning && !reducedMotion, view.speed); });
  $effect(() => { scene?.setPointSize(view.pointSize); });
  $effect(() => {
    void view.panel;
    if (scene) void tick().then(measureInset);
  });
  $effect(() => { scene?.setActive(active); });
  $effect(() => {
    if (focusKey !== null && model && !model.groups.some((group) => group.key === focusKey)) focusKey = null;
  });
  $effect(() => {
    if (view.labels === "off") hover = null;
    void hoverGroup;
    if (view.labels !== "off" && model) {
      labelSizes = new WeakMap();
      placeLabels();
    }
  });

  function dateText(at: string | null) {
    const date = at ? new Date(at) : null;
    return date && !Number.isNaN(date.getTime())
      ? date.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" })
      : "undated";
  }
</script>

<svelte:window {onkeydown} />

{#snippet kindName(group: { label: string; shades: { edit: boolean }[] })}{group.label}{#if isolatedGroup && group.shades[0]?.edit}<em>{" edits"}</em>{/if}{/snippet}

<div
  class="brain"
  bind:this={root}
  data-brain-stage
  data-birth={birth}
  data-layout={view.layout}
  data-background={view.background}
  data-backdrop={backdropTone.cover > 0.5 ? "light" : "dark"}
  data-isolated={isolatedKey ?? undefined}
  data-hover-group={hoverKey ?? undefined}
>
  <p class="sr-only">
    Every record and every edit in your workspace drawn as a point of light in a brain made of a glowing neural network: records are its neurons, folded into one region of the cortex per {grouping.toLowerCase()}; each {grouping.toLowerCase()}'s edits travel the fibre tracts into its region, newest nearest the brain's centre.
    Drag to rotate, scroll to zoom, click a group to open it on its own and press Escape to return. The legend below highlights one {grouping.toLowerCase()} at a time.
  </p>
  <div class="canvas" bind:this={host}></div>

  {#if renderProblem}
    <div class="state"><p class="title">The brain cannot be drawn here</p><p>{renderProblem}</p></div>
  {:else if status === "missing"}
    <div class="state" role="status">
      <p class="title">The brain needs the newer backend</p>
      <p>Update rika! to see the Brain; 2D still maps your workspace.</p>
    </div>
  {:else if status === "error"}
    <div class="state" role="alert">
      <p class="title">The brain could not load</p>
      <p>{problem}</p>
      <button type="button" onclick={() => { status = "loading"; void load(); }}>Retry</button>
    </div>
  {:else if status === "loading"}
    <div class="state"><Loader kind="view" label="Waking the brain…" /></div>
  {:else if data && !data.nodes.length}
    <div class="state" role="status">
      <p class="title">Nothing to draw yet</p>
      <p>Records and edits appear here as points while you work.</p>
    </div>
  {/if}

  {#if start || isolatedGroup}
    <div class="start">
      {@render start?.()}
      {#if isolatedGroup}
        <button type="button" class="back" aria-label={`Back to all ${grouping.toLowerCase()}s`} onclick={() => isolate(null)}>
          <span aria-hidden="true">←</span> All<span class="sep" aria-hidden="true">·</span><i style:background={isolatedGroup.hex}></i>{isolatedGroup.label}
        </button>
      {/if}
    </div>
  {/if}

  {#if model && view.labels !== "off"}
    <div class="labels" aria-hidden="true">
      {#each model.groups as group, index (group.key)}
        <span
          bind:this={labelElements[index]}
          class:dim={focusIndex >= 0 && focusIndex !== index}
          class:hot={hoverGroup === index}
          class:quiet={view.labels === "hover" && hoverGroup !== index}
        >{@render kindName(group)}</span>
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
      <p>{#if isolatedGroup}{isolatedGroup.label} by kind{:else}By {grouping}{/if}</p>
      <p>Records + edits</p>
      <ul aria-label="Legend">
        {#each legend as group, index (group.key)}
          <li
            onmouseenter={() => (keyFor = group.key)}
            onmouseleave={() => (keyFor = null)}
            onfocusin={() => (keyFor = group.key)}
            onfocusout={() => (keyFor = null)}
          >
            <button
              type="button"
              data-key={group.key}
              aria-pressed={focusKey === group.key}
              class:dim={focusKey !== null && focusKey !== group.key}
              class:hot={hoverGroup === index}
              title={`${group.records} records · ${group.edits} edits`}
              onclick={() => toggleFocus(group.key)}
            ><i style:background={group.hex} style:box-shadow={`0 0 6px ${group.hex}`}></i><span class="name">{@render kindName(group)}</span><span class="count">{group.records + group.edits}</span></button>
            {#if keyFor === group.key && group.shades.length > 1}
              <ul class="kinds" aria-label={`${group.label} kinds`}>
                {#each group.shades.slice(0, 10) as shade (shade.key)}
                  <li data-kind={shade.key}><i style:background={shade.hex}></i><span class="name">{shade.label}{#if shade.edit}<em>{" edits"}</em>{/if}</span><span class="count">{shade.count}</span></li>
                {/each}
                {#if group.shades.length > 10}<li class="rest">{group.shades.length - 10} more kinds</li>{/if}
              </ul>
            {/if}
          </li>
        {/each}
        {#if model.groups.length > LEGEND_LIMIT}
          <li><button type="button" class="more" onclick={() => (showAll = !showAll)}>{showAll ? "Fewer" : `${model.groups.length - LEGEND_LIMIT} more`}</button></li>
        {/if}
      </ul>
    </div>
  {/if}

  <BrainControls
    {view}
    {reducedMotion}
    colours={allGroups?.groups ?? []}
    {swatches}
    picked={picks[groupBy]}
    onrecolour={recolour}
    onresetcolours={resetColours}
    onchange={change}
    onreset={reset}
    lead={end}
  />
</div>

<style>
  .brain {
    position: absolute;
    inset: 0;
    z-index: 2;
    overflow: hidden;
    /* Transparent: the theme's backdrop shows through and text takes the theme's inks. */
    --brain-ink: var(--ink, #f2f2f2);
    --brain-strong: var(--ink, #fff);
    --brain-text: var(--ink, #e8e8e8);
    --brain-label: color-mix(in srgb, var(--ink, #d8d8d8) 90%, transparent);
    --brain-count: var(--muted, #cfcfcf);
    --brain-mute: var(--muted, #a8a8a8);
    --brain-quiet: var(--subtle, #8c8c8c);
    --brain-faint: var(--subtle, #7d7d7d);
    --brain-ground: var(--br-panel, #000);
    --brain-ground-solid: var(--solid-void, #000);
    --brain-surface: var(--br-panel, rgba(0, 0, 0, 0.88));
    --brain-tip: color-mix(in srgb, var(--solid-panel, #000) 92%, transparent);
    --brain-edge: var(--br-line, #333);
    --brain-edge-soft: var(--hairline, #262626);
    --brain-rule: var(--hairline, #1c1c1c);
    --brain-track: var(--br-line, #3a3a3a);
    --brain-edge-hot: var(--br-lineHot, #8a8a8a);
    --brain-halo: var(--solid-void, #000);
    --brain-blur: var(--glass, none);
    background: transparent;
    color: var(--brain-ink);
    font: 12px/1.35 var(--br-font-ui, system-ui, sans-serif);
  }
  /* Black: the original night-sky stage. */
  .brain[data-background="black"] {
    --brain-ink: #f2f2f2;
    --brain-strong: #fff;
    --brain-text: #e8e8e8;
    --brain-label: #d8d8d8;
    --brain-count: #cfcfcf;
    --brain-mute: #a8a8a8;
    --brain-quiet: #8c8c8c;
    --brain-faint: #7d7d7d;
    --brain-ground: #000;
    --brain-ground-solid: #000;
    --brain-surface: rgba(0, 0, 0, 0.88);
    --brain-tip: rgba(0, 0, 0, 0.86);
    --brain-edge: #333;
    --brain-edge-soft: #262626;
    --brain-rule: #1c1c1c;
    --brain-track: #3a3a3a;
    --brain-edge-hot: #8a8a8a;
    --brain-halo: #000;
    --brain-blur: none;
    background: #000;
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
    color: var(--brain-mute);
    font-size: 12px;
  }
  .state p {
    margin: 0 0 6px;
  }
  .state .title {
    color: var(--brain-ink);
    font-size: 13px;
  }
  .state button {
    margin-top: 6px;
    padding: 3px 10px;
    font: inherit;
    color: var(--brain-ink);
    background: var(--brain-ground);
    border: 1px solid var(--brain-edge);
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
    color: var(--brain-label);
    text-shadow: 0 0 6px var(--brain-halo), 0 0 2px var(--brain-halo);
    pointer-events: none;
    will-change: transform;
    transition: opacity 0.15s;
  }
  .labels span.dim {
    opacity: 0.3;
  }
  .labels span.quiet {
    visibility: hidden !important;
  }
  .labels span.hot {
    z-index: 1;
    color: var(--brain-strong);
    font-weight: 600;
    opacity: 1;
  }
  .labels em,
  .legend em {
    font-style: normal;
    opacity: 0.7;
  }
  /* Home's corner controls, then the way back from one isolated group to all of them. */
  .start {
    position: absolute;
    top: 14px;
    left: 14px;
    z-index: 4;
    display: flex;
    align-items: center;
    gap: 6px;
  }
  .back {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    height: 28px;
    padding: 0 12px;
    font: inherit;
    font-size: 11.5px;
    color: var(--brain-ink);
    background: var(--brain-ground);
    border: 1px solid var(--brain-track);
    border-radius: 999px;
    backdrop-filter: var(--brain-blur);
    -webkit-backdrop-filter: var(--brain-blur);
    cursor: pointer;
  }
  .back:hover {
    border-color: var(--brain-edge-hot);
  }
  .back .sep {
    color: var(--brain-mute);
  }
  .back i {
    width: 7px;
    height: 7px;
    border-radius: 50%;
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
    background: var(--brain-tip);
    border: 1px solid var(--brain-edge);
    border-radius: 3px;
    pointer-events: none;
    font-size: 11px;
  }
  .tooltip strong {
    font-weight: 500;
    color: var(--brain-strong);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .tooltip span {
    color: var(--brain-mute);
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
    color: var(--brain-label);
    text-shadow: 0 0 6px var(--brain-halo);
  }
  .legend p + p {
    margin-bottom: 12px;
    color: var(--brain-quiet);
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
    color: var(--brain-text);
    text-shadow: 0 0 6px var(--brain-halo);
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
  .legend li {
    position: relative;
  }
  .legend .count {
    color: var(--brain-count);
    font-variant-numeric: tabular-nums;
  }
  .legend button.dim {
    opacity: 0.35;
  }
  .legend button.hot .name,
  .legend button[aria-pressed="true"] .name {
    color: var(--brain-strong);
    text-decoration: underline;
    text-underline-offset: 3px;
  }
  .legend .more {
    display: block;
    color: var(--brain-mute);
  }
  .legend button:hover .name,
  .legend .more:hover {
    color: var(--brain-strong);
  }
  /* The kinds inside a legend entry, shown while it is hovered or focused. */
  .legend .kinds {
    position: absolute;
    left: -8px;
    bottom: calc(100% + 4px);
    z-index: 4;
    display: grid;
    gap: 3px;
    min-width: 188px;
    margin: 0;
    padding: 7px 8px;
    list-style: none;
    background: var(--brain-tip);
    border: 1px solid var(--brain-edge);
    border-radius: 3px;
    backdrop-filter: var(--brain-blur);
    -webkit-backdrop-filter: var(--brain-blur);
    pointer-events: none;
    font-size: 11px;
  }
  .legend .kinds li {
    display: grid;
    grid-template-columns: 8px minmax(0, 1fr) auto;
    align-items: center;
    gap: 8px;
    color: var(--brain-text);
  }
  .legend .kinds i {
    width: 7px;
    height: 7px;
    border-radius: 50%;
  }
  .legend .kinds .rest {
    display: block;
    color: var(--brain-mute);
  }
  .legend button:focus-visible,
  .state button:focus-visible,
  .back:focus-visible {
    outline: 1px solid var(--brain-strong);
    outline-offset: 2px;
  }
  @media (prefers-reduced-motion: reduce) {
    .legend button {
      transition: none;
    }
  }
</style>
