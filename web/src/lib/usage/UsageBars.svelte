<script lang="ts">
  import { SOURCES, dayName, dayReadout, stackBars, tokens, type UsageDay } from "./usage";

  /**
   * Tokens per day, one bar a day, stacked by source in lightness steps of the ink. The
   * chart is drawn at its real width, so its axis text stays 13px. Pointing at a bar
   * (or moving to it with the arrow keys) names the day on the line below.
   */
  let { days }: { days: UsageDay[] } = $props();
  const H = 150;
  const LABELS = 22;
  /** Room above the plot for the top grid line's number. */
  const TOP = 20;
  /** Room right of the bars for the grid lines' numbers. */
  const GUTTER = 48;
  let width = $state(0);
  let hover = $state(-1);
  const chart = $derived(stackBars(days, H));
  const slot = $derived(days.length ? Math.max(0, width - GUTTER) / days.length : 0);
  const used = $derived(SOURCES.filter((source) => chart.bars.some((bar) => bar.parts.some((part) => part.source === source.id))));
  const ink = (source: string) => `color-mix(in srgb, var(--ink) ${SOURCES.find((item) => item.id === source)!.ink}%, transparent)`;
  /** A date under every seventh bar, counted back from today so the last one is always named. */
  const named = (index: number) => (days.length - 1 - index) % 7 === 0;
  function key(event: KeyboardEvent) {
    if (event.key === "ArrowLeft") hover = Math.max(0, (hover < 0 ? days.length : hover) - 1);
    else if (event.key === "ArrowRight") hover = Math.min(days.length - 1, hover + 1);
    else return;
    event.preventDefault();
  }
</script>

<div class="bars" bind:clientWidth={width}>
  {#if width && days.length}
    <!-- svelte-ignore a11y_no_noninteractive_tabindex -->
    <svg viewBox={`0 ${-TOP} ${width} ${H + LABELS + TOP}`} {width} height={H + LABELS + TOP} role="img" tabindex="0"
      aria-label={`Tokens per day for ${days.length} days`} onpointerleave={() => (hover = -1)} onkeydown={key} onblur={() => (hover = -1)}>
      {#each [1, 0.5] as fraction (fraction)}
        <line class="grid" x1="0" x2={width} y1={H - H * fraction + 0.5} y2={H - H * fraction + 0.5} />
        <text class="scale" x={width} y={H - H * fraction - 5}>{tokens(chart.top * fraction)}</text>
      {/each}
      {#each chart.bars as bar, index (bar.day)}
        <g class:on={hover === index} role="presentation" onpointerenter={() => (hover = index)}>
          <rect class="hit" x={index * slot} y="0" width={slot} height={H + LABELS} />
          {#each bar.parts as part (part.source)}
            <rect x={index * slot + slot * 0.18} y={part.y} width={slot * 0.64} height={part.height} fill={ink(part.source)} />
          {/each}
          {#if named(index)}<text class="date" x={index * slot + slot / 2} y={H + 17}>{dayName(bar.day)}</text>{/if}
        </g>
      {/each}
      <line class="base" x1="0" x2={width} y1={H + 0.5} y2={H + 0.5} />
    </svg>
  {/if}
  <p class="readout" role="status">{hover >= 0 && days[hover] ? dayReadout(days[hover]) : "\u00a0"}</p>
  {#if used.length}
    <p class="legend">{#each used as source (source.id)}<span><i style:background={ink(source.id)}></i>{source.label}</span>{/each}</p>
  {/if}
</div>

<style>
  .bars {
    min-width: 0;
  }
  svg {
    display: block;
    outline: none;
  }
  svg:focus-visible {
    outline: 1px solid var(--ink);
    outline-offset: 4px;
  }
  .grid {
    stroke: var(--hairline);
  }
  .base {
    stroke: var(--muted);
  }
  .hit {
    fill: transparent;
  }
  g.on .hit {
    fill: color-mix(in srgb, var(--ink) 7%, transparent);
  }
  text {
    fill: var(--muted);
    font: 13px var(--font-ui);
    font-variant-numeric: tabular-nums;
  }
  .date {
    text-anchor: middle;
  }
  .scale {
    text-anchor: end;
  }
  .readout,
  .legend {
    margin: 8px 0 0;
    color: var(--muted);
    font-size: 14px;
    line-height: 1.4;
  }
  .readout {
    color: var(--ink);
    font-variant-numeric: tabular-nums;
  }
  .legend {
    display: flex;
    flex-wrap: wrap;
    gap: 4px 16px;
  }
  .legend span {
    display: inline-flex;
    align-items: center;
    gap: 6px;
  }
  .legend i {
    width: 10px;
    height: 10px;
  }
</style>
