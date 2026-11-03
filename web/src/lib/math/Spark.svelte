<script lang="ts">
  /** A small score trend: one point per run, the latest marked. */
  let { values, w = 240, h = 56, label }: { values: number[]; w?: number; h?: number; label: string } = $props();

  const PAD = 6;
  const points = $derived.by(() => {
    if (!values.length) return [];
    const low = Math.min(...values, 0);
    const span = Math.max(...values, 1) - low || 1;
    const step = values.length > 1 ? (w - PAD * 2) / (values.length - 1) : 0;
    return values.map((value, index) => [values.length > 1 ? PAD + index * step : w / 2, h - PAD - ((value - low) / span) * (h - PAD * 2)] as const);
  });
  const last = $derived(points.at(-1));
</script>

<svg class="spark" width={w} height={h} viewBox={`0 0 ${w} ${h}`} role="img" aria-label={label} data-points={values.length}>
  <line class="base" x1={PAD} x2={w - PAD} y1={h - PAD} y2={h - PAD} />
  {#if points.length > 1}<polyline points={points.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" ")} />{/if}
  {#each points.slice(0, -1) as [x, y], index (index)}<circle cx={x} cy={y} r="2.2" class="point" />{/each}
  {#if last}<circle cx={last[0]} cy={last[1]} r="4" class="last" />{/if}
</svg>

<style>
  .spark {
    display: block;
    max-width: 100%;
    overflow: visible;
  }
  .base {
    stroke: var(--hairline);
    stroke-width: 1;
  }
  polyline {
    fill: none;
    stroke: var(--accent);
    stroke-width: 2;
    stroke-linejoin: round;
    stroke-linecap: round;
  }
  .point {
    fill: color-mix(in srgb, var(--accent) 70%, var(--ink));
  }
  .last {
    fill: var(--accent);
    stroke: var(--solid-void, var(--br-void, #000));
    stroke-width: 2;
  }
</style>
