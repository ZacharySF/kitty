<script lang="ts">
  import { addDays } from "./dates";
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
  const focusEvents = $derived(focusRow?.reduce((sum, day) => sum + (load[day]?.events ?? 0), 0) ?? 0);
  const focusMinutes = $derived(focusRow?.reduce((sum, day) => sum + minutes(day), 0) ?? 0);
  /** The picked week's chart keeps its room while another week is previewed, so the rail below does not jump. */
  const pickedBusy = $derived(pickedRow >= 0 && rows[pickedRow].some((day) => minutes(day) > 0));
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
        <span class="wk" role="rowheader" aria-label={`Week ${isoWeek(row[0])}`}>{isoWeek(row[0])}</span>
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
    <div class="peek" class:roomy={pickedBusy} role="status" aria-label="Week preview">
      <span class="sum">Week {isoWeek(focusRow[0])} · {focusEvents === 1 ? "1 event" : `${focusEvents} events`}{focusMinutes ? ` · ${duration(focusMinutes)}` : ""}</span>
      {#if focusMinutes}
        <div class="bars" aria-hidden="true">
          {#each focusRow as day, i (day)}
            <div class="bar" class:today={day === today}>
              <i style:height={`${Math.min(1, minutes(day) / 420) * 100}%`}></i>
              <span>{weekdays[i][0]}</span>
            </div>
          {/each}
        </div>
      {/if}
    </div>
  {/if}
</div>

<style>
  /* A plain section of the rail: no frame, hairlines only. */
  .mini {
    display: grid;
    gap: 8px;
    padding-bottom: 14px;
  }
  header {
    display: flex;
    align-items: center;
    gap: 2px;
  }
  header strong {
    margin-right: auto;
    font: 600 16px/1.3 var(--font-ui);
  }
  .nav {
    width: 28px;
    height: 28px;
    padding: 0 0 2px;
    border: 0;
    border-radius: 999px;
    background: transparent;
    color: var(--muted);
    font-size: 20px;
    line-height: 1;
  }
  .nav:hover {
    background: color-mix(in srgb, var(--ink) 8%, transparent);
    color: var(--ink);
  }
  .grid {
    display: grid;
    grid-template-columns: 30px repeat(7, minmax(0, 1fr));
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
    font: 400 13px var(--font-ui);
    font-variant-numeric: tabular-nums;
  }
  .wd {
    height: 26px;
  }
  .d {
    position: relative;
    isolation: isolate;
    height: 30px;
    padding: 0;
    border: 0;
    border-radius: 0;
    background: transparent;
    color: var(--ink);
    font-size: 13px;
    font-variant-numeric: tabular-nums;
    cursor: pointer;
  }
  .row .wk {
    border-radius: 999px 0 0 999px;
  }
  .row.hot > * {
    background: color-mix(in srgb, var(--ink) 5%, transparent);
  }
  .row.picked > * {
    background: color-mix(in srgb, var(--ink) 9%, transparent);
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
    background: currentColor;
    transform: translateX(-50%);
  }
  /* The day's own mark is a circle: soft for the picked day, filled for today. */
  .d[aria-selected="true"]::before,
  .d.today::before {
    content: "";
    position: absolute;
    inset: 2px calc(50% - 13px);
    z-index: -1;
    border-radius: 999px;
    background: color-mix(in srgb, var(--ink) 16%, transparent);
  }
  .d.today {
    color: var(--solid-void);
    font-weight: 600;
  }
  .d.today::before {
    background: var(--ink);
  }
  .peek {
    display: grid;
    gap: 8px;
    padding-top: 12px;
    border-top: 1px solid var(--hairline);
  }
  .peek.roomy {
    align-content: start;
    min-height: 106px;
  }
  .sum {
    color: var(--muted);
    font-size: 13px;
  }
  .bars {
    display: grid;
    grid-template-columns: repeat(7, minmax(0, 1fr));
    gap: 6px;
    height: 64px;
  }
  .bar {
    display: grid;
    grid-template-rows: 1fr auto;
    justify-items: center;
    gap: 4px;
  }
  .bar i {
    align-self: end;
    width: 100%;
    max-width: 18px;
    min-height: 2px;
    border-radius: 3px 3px 1px 1px;
    background: color-mix(in srgb, var(--ink) 30%, transparent);
  }
  .bar.today i {
    background: var(--ink);
  }
  .bar span {
    color: var(--subtle);
    font: 400 13px/1 var(--font-ui);
  }
</style>
