<script lang="ts">
  import type { Appearance } from "./appearance";
  import type { GraphLook } from "./graph3d";
  import { PETAL, PETAL_ANGLES } from "./icons";

  /** Q59: a small kitty window drawn in the live theme with the chosen appearance. */
  let { appearance, look }: { appearance: Appearance; look: GraphLook } = $props();
  const uid = $props.id();

  const NODES = [
    { x: 30, y: 34, r: 7, c: 1 },
    { x: 78, y: 22, r: 4, c: 1 },
    { x: 64, y: 62, r: 5, c: 2 },
    { x: 108, y: 48, r: 7, c: 3 },
    { x: 132, y: 80, r: 4, c: 3 },
    { x: 40, y: 88, r: 4, c: 4 },
    { x: 92, y: 98, r: 6, c: 4 },
  ];
  const EDGES = [[0, 1], [0, 2], [2, 3], [3, 4], [2, 5], [5, 6], [3, 6], [1, 3]];
  const PETALS = [
    { x: 12, y: 18, r: 20 },
    { x: 46, y: 70, r: 140 },
    { x: 71, y: 30, r: 250 },
    { x: 88, y: 76, r: 320 },
  ];
</script>

<div
  class="preview"
  role="img"
  aria-label="Appearance preview"
  data-title-font={appearance.titleFont}
  data-density={appearance.density}
  data-motion={appearance.motion}
  data-motif={appearance.motif ? "on" : "off"}
  data-background={appearance.background}
  data-look={look}
>
  {#if appearance.background === "petals"}
    <div class="petals" aria-hidden="true">
      {#each PETALS as petal, index (index)}
        <svg viewBox="-6 -11 12 12" style:left={`${petal.x}%`} style:top={`${petal.y}%`} style:rotate={`${petal.r}deg`} style:--fill={`var(--c${(index % 3) + 1})`}><path d={PETAL} /></svg>
      {/each}
    </div>
  {/if}
  <div class="bar">
    <span class="mark" data-wordmark>kitty!{#if appearance.motif}<svg class="bloom" viewBox="-11 -11 22 22" aria-hidden="true">{#each PETAL_ANGLES as angle (angle)}<path d={PETAL} transform={`rotate(${angle})`} />{/each}</svg>{/if}</span>
    <span class="groups"><b>Plan</b><span>Study</span><span>Jobs</span></span>
  </div>
  <div class="body">
    <svg class="graph" viewBox="0 0 150 116" aria-hidden="true">
      <defs>
        {#each [1, 2, 3, 4] as c (c)}
          <radialGradient id={`ball-${uid}-${c}`} cx="0.35" cy="0.3" r="0.75">
            <stop offset="0" stop-color="var(--ink)" stop-opacity="0.95" />
            <stop offset="0.35" stop-color={`var(--c${c})`} />
            <stop offset="1" stop-color="var(--solid-void)" />
          </radialGradient>
        {/each}
      </defs>
      {#each EDGES as [a, b] (`${a}-${b}`)}
        <line x1={NODES[a].x} y1={NODES[a].y} x2={NODES[b].x} y2={NODES[b].y} />
      {/each}
      {#each NODES as node, index (index)}
        <circle
          cx={node.x}
          cy={node.y}
          r={look === "3d" ? node.r * 1.25 : node.r}
          fill={look === "3d" ? `url(#ball-${uid}-${node.c})` : `var(--c${node.c})`}
          class:beat={index === 3}
        />
      {/each}
    </svg>
    <div class="panel">
      <span class="eyebrow">today</span>
      <span class="title">Saturday</span>
      <ul>
        <li><i style:background="var(--c1)"></i>Linear algebra<em>09:00</em></li>
        <li><i style:background="var(--c2)"></i>Focus block<em>11:00</em></li>
        <li><i style:background="var(--c3)"></i>Seminar<em>14:00</em></li>
      </ul>
    </div>
  </div>
</div>

<style>
  .preview {
    --pad: 6px;
    --row: 2px;
    --size: 9.5px;
    --title: var(--font-title);
    position: relative;
    display: grid;
    grid-template-rows: auto 1fr;
    overflow: hidden;
    border: 1px solid var(--hairline);
    border-radius: 12px;
    background:
      radial-gradient(70% 60% at 15% 20%, color-mix(in srgb, var(--c1) 18%, transparent), transparent 70%),
      radial-gradient(60% 60% at 90% 90%, color-mix(in srgb, var(--c3) 14%, transparent), transparent 70%),
      var(--solid-void);
    color: var(--ink);
    font-family: var(--font-ui);
  }
  .preview[data-density="comfortable"] {
    --pad: 9px;
    --row: 5px;
    --size: 10.5px;
  }
  .preview[data-title-font="plain"] {
    --title: var(--font-ui);
  }
  .petals {
    position: absolute;
    inset: 0;
    pointer-events: none;
  }
  .petals svg {
    position: absolute;
    width: 12px;
    height: 12px;
    fill: var(--fill);
    opacity: 0.6;
  }
  .bar {
    position: relative;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    padding: 7px var(--pad);
    border-bottom: 1px solid var(--hairline);
  }
  .mark {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    font: 600 14px var(--font-wordmark);
  }
  .bloom {
    width: 11px;
    height: 11px;
    fill: var(--c1);
  }
  .groups {
    display: inline-flex;
    gap: 2px;
    padding: 2px;
    border: 1px solid var(--hairline);
    border-radius: 999px;
    font: 500 7px var(--font-mono);
    letter-spacing: 0.12em;
    text-transform: uppercase;
  }
  .groups > * {
    padding: 2px 6px;
    border-radius: 999px;
    color: var(--muted);
  }
  .groups b {
    background: color-mix(in srgb, var(--ink) 12%, transparent);
    color: var(--ink);
    font-weight: 500;
  }
  .body {
    position: relative;
    display: grid;
    grid-template-columns: 1.1fr 1fr;
    gap: var(--pad);
    padding: var(--pad);
  }
  .graph {
    width: 100%;
    max-height: 150px;
    align-self: center;
  }
  .graph line {
    stroke: color-mix(in srgb, var(--violet) 35%, transparent);
    stroke-width: 0.8;
  }
  .beat {
    transform-box: fill-box;
    transform-origin: center;
    animation: beat 2.4s ease-in-out infinite;
  }
  .preview[data-motion="reduced"] .beat {
    animation: none;
  }
  @keyframes beat {
    50% {
      transform: scale(1.3);
    }
  }
  .panel {
    display: grid;
    align-content: start;
    gap: calc(var(--row) + 2px);
    padding: var(--pad);
    border: 1px solid var(--hairline);
    border-radius: 10px;
    background: color-mix(in srgb, var(--solid-panel) 75%, transparent);
  }
  .panel .eyebrow {
    font-size: 9px;
    line-height: 1;
  }
  .title {
    font: 600 15px var(--title);
  }
  ul {
    display: grid;
    gap: var(--row);
    margin: 0;
    padding: 0;
    list-style: none;
    font-size: var(--size);
  }
  li {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: var(--row) 0;
    border-bottom: 1px solid color-mix(in srgb, var(--hairline) 60%, transparent);
  }
  li i {
    width: 6px;
    height: 6px;
    flex-shrink: 0;
    border-radius: 999px;
  }
  em {
    margin-left: auto;
    color: var(--muted);
    font: normal 8px var(--font-mono);
  }
</style>
