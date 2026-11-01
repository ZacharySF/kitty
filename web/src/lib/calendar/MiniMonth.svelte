<script lang="ts">
  import { duration, isoWeek, type DayLoad } from "./plan";

  /** Mini-month navigator: ISO weeks; hover a week to preview its load, click a date to jump there. */
  let { selected, today, refresh = 0, loadBusy, onpick }: {
    selected: string;
    today: string;
    /** Bumped whenever calendar data changes, so the preview reloads. */
    refresh?: number;
    loadBusy: (from: string, to: string) => Promise<DayLoad>;
    onpick: (day: string) => void;
  } = $props();

  const addDays = (day: string, count: number) => {
    const d = new Date(`${day}T12:00:00Z`);
    d.setUTCDate(d.getUTCDate() + count);
    return d.toISOString().slice(0, 10);
  };
  let month = $state("");
  $effect.pre(() => {
    month = selected.slice(0, 7);
  });
  function shift(step: number) {
    const d = new Date(`${month}-01T12:00:00Z`);
    d.setUTCMonth(d.getUTCMonth() + step, 1);
    month = d.toISOString().slice(0, 7);
  }
  const first = $derived.by(() => {
    const d = new Date(`${month}-01T12:00:00Z`);
    return addDays(`${month}-01`, -((d.getUTCDay() + 6) % 7));
  });
  const rows = $derived(Array.from({ length: 6 }, (_, r) => Array.from({ length: 7 }, (_, c) => addDays(first, r * 7 + c))));
  const title = $derived(new Intl.DateTimeFormat(undefined, { month: "long", year: "numeric", timeZone: "UTC" })
    .format(new Date(`${month}-01T12:00:00Z`)));
  let load = $state<DayLoad>({});
  $effect(() => {
    void refresh;
    const from = first;
    let stale = false;
    loadBusy(from, addDays(from, 42)).then((result) => { if (!stale) load = result; }).catch(() => {});
    return () => { stale = true; };
  });
  let hoverRow = $state<number | null>(null);
  const pickedRow = $derived(rows.findIndex((row) => row.includes(selected)));
  const focusRow = $derived(hoverRow !== null ? rows[hoverRow] : pickedRow >= 0 ? rows[pickedRow] : null);
  const minutes = (day: string) => load[day]?.minutes ?? 0;
  const weekdays = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"];
</script>

<div class="mini">
  <header>
    <button class="nav" aria-label="Previous month" onclick={() => shift(-1)}>‹</button>
    <strong>{title}</strong>
    <button class="nav" aria-label="Next month" onclick={() => shift(1)}>›</button>
  </header>
  <div class="grid" role="grid" aria-label={`Mini month ${title}`}>
    <div class="row" role="row">
      <span class="wk" role="columnheader" aria-label="Week"></span>
      {#each weekdays as w (w)}<span class="wd" role="columnheader">{w}</span>{/each}
    </div>
    {#each rows as row, r (row[0])}
      <div class="row" class:hot={hoverRow === r} class:picked={r === pickedRow} role="row"
        tabindex="-1" onmouseenter={() => (hoverRow = r)} onmouseleave={() => (hoverRow = null)}>
        <span class="wk" role="rowheader">W{isoWeek(row[0])}</span>
        {#each row as day (day)}
          <button class="d" class:out={day.slice(0, 7) !== month} class:today={day === today}
            class:busy={(load[day]?.events ?? 0) >= 3} role="gridcell" aria-selected={day === selected}
            aria-label={`Go to ${day}`} onfocus={() => (hoverRow = r)} onblur={() => (hoverRow = null)}
            onclick={() => onpick(day)}>{Number(day.slice(8))}</button>
        {/each}
      </div>
    {/each}
  </div>
  {#if focusRow}
    <div class="peek" role="status" aria-label="Week preview">
      <span class="eyebrow">week {isoWeek(focusRow[0])} · {focusRow.reduce((sum, day) => sum + (load[day]?.events ?? 0), 0)} events · {duration(focusRow.reduce((sum, day) => sum + minutes(day), 0))}</span>
      <div class="bars" aria-hidden="true">
        {#each focusRow as day, i (day)}
          <div class="bar" class:today={day === today}>
            <i style:height={`${Math.min(1, minutes(day) / 420) * 100}%`}></i>
            <span>{weekdays[i][0]}</span>
          </div>
        {/each}
      </div>
    </div>
  {/if}
</div>

<style>
  .mini {
    display: grid;
    gap: 6px;
    padding: 10px 12px 12px;
    border: 1px solid var(--border);
    border-radius: 0.8rem;
    background: color-mix(in srgb, var(--solid-panel) 55%, transparent);
  }
  header {
    display: flex;
    align-items: center;
    justify-content: space-between;
  }
  header strong {
    font: 700 0.95rem var(--font-title);
  }
  .nav {
    width: 24px;
    height: 24px;
    padding: 0;
    border: 1px solid var(--hairline);
    border-radius: 999px;
    background: transparent;
    color: var(--muted);
  }
  .nav:hover {
    color: var(--ink);
  }
  .grid {
    display: grid;
    grid-template-columns: 28px repeat(7, minmax(0, 1fr));
    row-gap: 2px;
  }
  .row {
    display: contents;
  }
  .wd,
  .wk {
    display: grid;
    place-items: center;
    color: var(--subtle);
    font: 500 0.55rem var(--font-mono);
    letter-spacing: 0.06em;
    text-transform: uppercase;
  }
  .d {
    position: relative;
    isolation: isolate;
    height: 26px;
    padding: 0;
    border: 0;
    border-radius: 0;
    background: transparent;
    color: var(--ink);
    font-size: 0.72rem;
    font-variant-numeric: tabular-nums;
    cursor: pointer;
  }
  .row .wk {
    border-radius: 999px 0 0 999px;
  }
  .row.hot > * {
    background: color-mix(in srgb, var(--ink) 6%, transparent);
  }
  .row.picked > * {
    background: color-mix(in srgb, var(--c1) 22%, transparent);
  }
  .row.picked .wk {
    color: var(--ink);
  }
  .row > .d:last-child {
    border-radius: 0 999px 999px 0;
  }
  .d.out {
    color: var(--subtle);
  }
  .d.busy::after {
    content: "";
    position: absolute;
    left: 50%;
    bottom: 2px;
    width: 3px;
    height: 3px;
    border-radius: 999px;
    background: var(--c1);
    transform: translateX(-50%);
  }
  .d.today {
    color: var(--solid-void);
    font-weight: 700;
  }
  .d.today::before {
    content: "";
    position: absolute;
    inset: 2px calc(50% - 11px);
    z-index: -1;
    border-radius: 999px;
    background: var(--accent);
  }
  .peek {
    display: grid;
    gap: 6px;
    padding-top: 8px;
    border-top: 1px solid var(--hairline);
  }
  .peek .eyebrow {
    color: var(--muted);
  }
  .bars {
    display: grid;
    grid-template-columns: repeat(7, minmax(0, 1fr));
    gap: 6px;
    height: 54px;
  }
  .bar {
    display: grid;
    grid-template-rows: 1fr auto;
    justify-items: center;
    gap: 2px;
  }
  .bar i {
    align-self: end;
    width: 100%;
    max-width: 18px;
    min-height: 2px;
    border-radius: 4px 4px 2px 2px;
    background: color-mix(in srgb, var(--c3) 70%, transparent);
  }
  .bar.today i {
    background: var(--accent);
  }
  .bar span {
    color: var(--subtle);
    font: 500 0.55rem var(--font-mono);
  }
</style>
