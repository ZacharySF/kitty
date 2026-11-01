<script lang="ts">
  import { Panel } from "@nunera/bracket";
  import type { MemoryStats } from "./types";

  /**
   * Retention curve (Q41 B): FSRS-6 forgetting curves R(t, S) for the scope's weak,
   * medium and strong cards (stability quartiles) against the deck's target, with
   * measured recall dots sized by review count and a probe that follows the pointer.
   */
  let { retention }: { retention: MemoryStats["retention"] } = $props();

  const W = 330;
  const H = 150;
  const L = 26;
  const R = 8;
  const T = 8;
  const B = 18;
  const DAYS = 120;
  const x = (t: number) => L + (Math.min(t, DAYS) / DAYS) * (W - L - R);
  const y = (r: number) => T + ((1 - Math.max(0.6, r)) / 0.4) * (H - T - B);
  // FSRS-6: R(t, S) = (1 + F·t/S)^(−decay), with F chosen so that R(S, S) = 90%.
  let factor = $derived(Math.pow(0.9, -1 / retention.decay) - 1);
  const recall = (t: number, stability: number) => Math.pow(1 + (factor * t) / stability, -retention.decay);
  let curves = $derived(
    retention.stability
      ? ([
          ["weak", retention.stability.weak, "var(--c1)"],
          ["medium", retention.stability.medium, "var(--c3)"],
          ["strong", retention.stability.strong, "var(--c6)"],
        ] as const)
      : [],
  );
  const days = (stability: number) => (stability < 10 ? stability.toFixed(1) : Math.round(stability).toString());
  /** End-of-curve label heights, pushed apart so equal stabilities stay readable. */
  let labelY = $derived.by(() => {
    const heights = curves.map(([, stability]) => y(recall(DAYS, stability)) - 4);
    // Lowest label first; each higher curve's label sits at least one line above the last.
    const order = heights.map((_, index) => index).sort((a, b) => heights[b] - heights[a]);
    for (let k = 1; k < order.length; k++) heights[order[k]] = Math.min(heights[order[k]], heights[order[k - 1]] - 9);
    return heights;
  });
  const path = (stability: number) =>
    Array.from({ length: DAYS + 1 }, (_, t) => `${t ? "L" : "M"}${x(t).toFixed(1)} ${y(recall(t, stability)).toFixed(1)}`).join(" ");
  let target = $derived(Math.round(retention.target * 100));
  let probe = $state<number | null>(null);
  let svg = $state<SVGSVGElement>();
  function move(event: PointerEvent) {
    if (!svg) return;
    const box = svg.getBoundingClientRect();
    const vx = ((event.clientX - box.left) / box.width) * W;
    probe = Math.round(Math.min(DAYS, Math.max(0, ((vx - L) / (W - L - R)) * DAYS)));
  }
  let busiest = $derived(retention.measured.reduce<(typeof retention.measured)[number] | null>((best, bucket) => (!best || bucket.n > best.n ? bucket : best), null));
  let readout = $derived(
    probe !== null && curves.length
      ? `after ${probe}d · ${curves.map(([name, stability]) => `${name} ${Math.round(recall(probe!, stability) * 100)}%`).join(" · ")}`
      : busiest
        ? `measured ${Math.round(busiest.r * 100)}% at ${busiest.t} d · target ${target}%`
        : `target ${target}%`,
  );
</script>

<Panel title="Retention curve" {readout} aria-label="Retention curve" class="memory-stat">
  {#if curves.length}
    <svg
      bind:this={svg}
      viewBox={`0 0 ${W} ${H}`}
      role="img"
      aria-label={`Forgetting curves for ${curves.map(([name, stability]) => `${name} cards (stability ${days(stability)} d)`).join(", ")} against a ${target}% target`}
      onpointermove={move}
      onpointerleave={() => (probe = null)}
    >
      {#each [0.6, 0.7, 0.8, 0.9, 1] as r}
        <line class="grid" x1={L} x2={W - R} y1={y(r)} y2={y(r)} />
        <text class="yl" x={L - 4} y={y(r) + 2.5}>{Math.round(r * 100)}</text>
      {/each}
      {#each [0, 30, 60, 90, 120] as day}
        <text class="xl" x={x(day)} y={H - 4}>{day}d</text>
      {/each}
      <line class="target" x1={L} x2={W - R} y1={y(retention.target)} y2={y(retention.target)} />
      <text class="tl" x={x(70)} y={y(retention.target) + 9}>target {target}%</text>
      {#each curves as [name, stability, color], index}
        <path class="curve" d={path(stability)} style:stroke={color} />
        <text class="cl" x={x(DAYS) - 2} y={labelY[index]} style:fill={color}>{name} · S {days(stability)}d</text>
      {/each}
      {#each retention.measured as bucket}
        <circle class="point" cx={x(bucket.t)} cy={y(bucket.r)} r={1.6 + Math.sqrt(bucket.n) / 7}><title>{bucket.n} reviews near {bucket.t} d: {Math.round(bucket.r * 100)}% recalled</title></circle>
      {/each}
      {#if probe !== null}
        <line class="cursor" x1={x(probe)} x2={x(probe)} y1={T} y2={H - B} />
        {#each curves as [, stability, color]}
          <circle class="probe" cx={x(probe)} cy={y(recall(probe, stability))} r="2.6" style:fill={color} />
        {/each}
      {/if}
    </svg>
    <p class="legend"><i></i>measured recall (size = reviews) · curves = predicted recall</p>
  {:else}
    <p class="empty">Forgetting curves appear once cards in this scope reach review.</p>
  {/if}
</Panel>

<style>
  svg {
    width: 100%;
    height: auto;
    cursor: crosshair;
  }
  .grid {
    stroke: color-mix(in srgb, var(--hairline) 50%, transparent);
  }
  .yl,
  .xl,
  .tl {
    fill: var(--subtle);
    font: 7.5px var(--font-mono);
  }
  .yl {
    text-anchor: end;
  }
  .xl {
    text-anchor: middle;
  }
  .tl {
    fill: var(--muted);
  }
  .target {
    stroke: color-mix(in srgb, var(--ink) 45%, transparent);
    stroke-dasharray: 4 3;
  }
  .curve {
    fill: none;
    stroke-width: 1.8;
    stroke-linecap: round;
  }
  .cl {
    font: 600 7.5px var(--font-mono);
    text-anchor: end;
  }
  .point {
    fill: color-mix(in srgb, var(--ink) 80%, transparent);
    stroke: var(--solid-void);
    stroke-width: 1;
  }
  .cursor {
    stroke: color-mix(in srgb, var(--ink) 35%, transparent);
  }
  .probe {
    stroke: var(--solid-void);
    stroke-width: 1;
  }
  .legend {
    display: flex;
    align-items: center;
    gap: 6px;
    margin: 8px 0 0;
    color: var(--subtle);
    font: 10px var(--font-mono);
  }
  .legend i {
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: color-mix(in srgb, var(--ink) 80%, transparent);
  }
  .empty {
    margin: 0;
    color: var(--muted);
  }
</style>
