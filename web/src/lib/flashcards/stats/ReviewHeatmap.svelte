<script lang="ts">
  import { Panel } from "@nunera/bracket";
  import { dayLabel, type MemoryStats } from "./types";

  /** Daily review heatmap (Q41 F): 26 weeks, one Monday-first cell per day, with the streak. */
  let { heatmap, streak }: { heatmap: MemoryStats["heatmap"]; streak: number } = $props();

  let pad = $derived((new Date(`${heatmap[0].day}T12:00:00`).getDay() + 6) % 7);
  let weeks = $derived(Math.ceil((heatmap.length + pad) / 7));
  let max = $derived(Math.max(1, ...heatmap.map((day) => day.reviews)));
  let cells = $derived(
    heatmap.map((day, index) => ({
      ...day,
      week: Math.floor((index + pad) / 7),
      weekday: (index + pad) % 7,
      level: day.reviews ? Math.min(4, Math.ceil((day.reviews / max) * 4)) : 0,
      today: index === heatmap.length - 1,
    })),
  );
  let months = $derived(
    cells
      .filter((cell) => cell.weekday === 0)
      .map((cell) => ({ week: cell.week, label: new Date(`${cell.day}T12:00:00`).toLocaleDateString(undefined, { month: "short" }) }))
      .filter((month, index, all) => index === 0 || month.label !== all[index - 1].label),
  );
  let total = $derived(heatmap.reduce((sum, day) => sum + day.reviews, 0));
  let active = $derived(heatmap.filter((day) => day.reviews).length);
  let hover = $state<(typeof cells)[number] | null>(null);
  const count = (reviews: number) => `${reviews} ${reviews === 1 ? "review" : "reviews"}`;
  let readout = $derived(
    hover ? `${dayLabel(hover.day)} · ${count(hover.reviews)}` : `${streak}-day streak · ${active}/${heatmap.length} days · ${count(total)}`,
  );
</script>

<Panel title="Reviews per day · 26 weeks" {readout} aria-label="Reviews per day · 26 weeks" class="memory-stat">
  <div class="heat" style:--weeks={weeks} role="img" aria-label={`Daily review heatmap: ${count(total)} on ${active} days, ${streak}-day streak`}>
    <div class="months" aria-hidden="true">
      {#each months as month}<span style:grid-column={month.week + 1}>{month.label}</span>{/each}
    </div>
    <div class="days" aria-hidden="true"><span>M</span><span></span><span>W</span><span></span><span>F</span><span></span><span></span></div>
    <div class="grid" role="presentation" onpointerleave={() => (hover = null)}>
      {#each cells as cell (cell.day)}
        <span
          class={`cell level${cell.level}`}
          class:today={cell.today}
          class:on={hover === cell}
          style:grid-column={cell.week + 1}
          style:grid-row={cell.weekday + 1}
          title={`${dayLabel(cell.day)}: ${count(cell.reviews)}`}
          role="presentation"
          onpointerenter={() => (hover = cell)}
        ></span>
      {/each}
    </div>
  </div>
  <p class="legend">less {#each [0, 1, 2, 3, 4] as level}<i class={`cell level${level}`}></i>{/each} more</p>
</Panel>

<style>
  .heat {
    display: grid;
    grid-template-columns: 14px 1fr;
    gap: 4px;
  }
  .months {
    grid-column: 2;
    display: grid;
    grid-template-columns: repeat(var(--weeks), 1fr);
    color: var(--subtle);
    font: 8.5px var(--font-mono);
    letter-spacing: 0.06em;
  }
  .months span {
    white-space: nowrap;
  }
  .days {
    display: grid;
    grid-template-rows: repeat(7, 1fr);
    gap: 3px;
    align-items: center;
    color: var(--subtle);
    font: 8px var(--font-mono);
    line-height: 1;
  }
  .grid {
    display: grid;
    grid-template-columns: repeat(var(--weeks), 1fr);
    grid-template-rows: repeat(7, 1fr);
    gap: 3px;
  }
  .cell {
    display: block;
    aspect-ratio: 1;
    border-radius: 3px;
  }
  .level0 {
    background: color-mix(in srgb, var(--ink) 6%, transparent);
  }
  .level1 {
    background: color-mix(in srgb, var(--violet) 28%, transparent);
  }
  .level2 {
    background: color-mix(in srgb, var(--violet) 50%, transparent);
  }
  .level3 {
    background: color-mix(in srgb, var(--violet) 75%, transparent);
  }
  .level4 {
    background: var(--violet);
  }
  .grid .cell.on {
    outline: 1.5px solid var(--ink);
    outline-offset: 1px;
  }
  .grid .cell.today {
    box-shadow: 0 0 0 1.5px var(--solid-void), 0 0 0 2.5px var(--ink);
  }
  .legend {
    display: flex;
    align-items: center;
    justify-content: flex-end;
    gap: 3px;
    margin: 8px 0 0;
    color: var(--subtle);
    font: 10px var(--font-mono);
  }
  .legend .cell {
    width: 10px;
  }
</style>
