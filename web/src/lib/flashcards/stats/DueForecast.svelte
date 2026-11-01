<script lang="ts">
  import { Panel } from "@nunera/bracket";
  import { dayLabel, type MemoryStats } from "./types";

  /** Due forecast (Q41 A): review and learning cards due on each of the next 30 days. */
  let { forecast }: { forecast: MemoryStats["forecast"] } = $props();

  const W = 330;
  const H = 120;
  let max = $derived(Math.max(1, ...forecast.map((day) => day.review + day.learning)));
  let bw = $derived(W / forecast.length);
  const y = (value: number) => (value / max) * (H - 8);
  let week = $derived(forecast.slice(1, 8).reduce((sum, day) => sum + day.review + day.learning, 0));
  let hover = $state(-1);
  const describe = (index: number) => {
    const day = forecast[index];
    return `${index === 0 ? "today" : dayLabel(day.day)} · ${day.review + day.learning} due${day.learning ? ` (${day.learning} learning)` : ""}`;
  };
  let readout = $derived(
    hover < 0 ? `tomorrow ${forecast[1].review + forecast[1].learning} · next 7 days ${week}` : describe(hover),
  );
</script>

<Panel title="Due forecast · 30 days" {readout} aria-label="Due forecast · 30 days" class="memory-stat">
  <svg viewBox={`0 0 ${W} ${H + 16}`} role="img" aria-label={`Cards due per day for the next 30 days: ${week} in the next week`} onpointerleave={() => (hover = -1)}>
    {#each [0.5, 1] as fraction}
      <line class="grid" x1="0" x2={W} y1={H - y(max * fraction)} y2={H - y(max * fraction)} />
      <text class="gl" x={W} y={H - y(max * fraction) - 3}>{Math.round(max * fraction)}</text>
    {/each}
    {#each forecast as day, index}
      <g class="bar" class:on={hover === index} class:today={index === 0} role="presentation" onpointerenter={() => (hover = index)}>
        <title>{describe(index)}</title>
        <rect class="hit" x={index * bw} y="0" width={bw} height={H} />
        <rect class="review" x={index * bw + 1.5} y={H - y(day.review)} width={bw - 3} height={y(day.review)} rx="1.5" />
        {#if day.learning}<rect class="learning" x={index * bw + 1.5} y={H - y(day.review) - y(day.learning)} width={bw - 3} height={y(day.learning)} rx="1.5" />{/if}
      </g>
    {/each}
    <line class="base" x1="0" x2={W} y1={H} y2={H} />
    {#each [0, 7, 14, 21, 28] as index}
      <text class="xl" x={index * bw + bw / 2} y={H + 12}>{index === 0 ? "today" : `+${index}d`}</text>
    {/each}
  </svg>
  <p class="legend"><i class="review"></i>review <i class="learning"></i>learning <span>overdue folds into today</span></p>
</Panel>

<style>
  svg {
    width: 100%;
    height: auto;
    overflow: visible;
  }
  .grid {
    stroke: color-mix(in srgb, var(--hairline) 60%, transparent);
    stroke-dasharray: 2 3;
  }
  .gl,
  .xl {
    fill: var(--subtle);
    font: 7.5px var(--font-mono);
  }
  .gl {
    text-anchor: end;
  }
  .xl {
    text-anchor: middle;
  }
  .base {
    stroke: var(--border);
  }
  .hit {
    fill: transparent;
  }
  .bar .review {
    fill: color-mix(in srgb, var(--violet) 55%, transparent);
  }
  .bar .learning {
    fill: var(--warning);
  }
  .bar.today .review {
    fill: var(--violet);
  }
  .bar.on .review {
    fill: var(--ink);
  }
  .bar.on .hit {
    fill: color-mix(in srgb, var(--ink) 5%, transparent);
  }
  .legend {
    display: flex;
    align-items: center;
    gap: 4px;
    margin: 8px 0 0;
    color: var(--subtle);
    font: 10px var(--font-mono);
  }
  .legend i {
    width: 8px;
    height: 8px;
    margin-left: 8px;
    border-radius: 2px;
  }
  .legend i:first-child {
    margin-left: 0;
  }
  .legend i.review {
    background: color-mix(in srgb, var(--violet) 55%, transparent);
  }
  .legend i.learning {
    background: var(--warning);
  }
  .legend span {
    margin-left: auto;
  }
</style>
