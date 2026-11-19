<script module lang="ts">
  export type CalendarView = "day" | "week" | "month" | "agenda" | "year" | "plan";
  export const VIEWS: { id: CalendarView; label: string; key: string }[] = [
    { id: "day", label: "Day", key: "D" },
    { id: "week", label: "Week", key: "W" },
    { id: "month", label: "Month", key: "M" },
    { id: "agenda", label: "Agenda", key: "A" },
    { id: "year", label: "Year", key: "Y" },
    { id: "plan", label: "Plan", key: "P" },
  ];
</script>

<script lang="ts">
  import type { Snippet } from "svelte";

  /** The calendar's one header row: the range as the title, ‹ Today › (‹ This week › and so on), the views as words, then quiet tools and the primary add. */
  let { heading, view, date, tools, add, onview, onstep, ontoday, ondate }: {
    heading: string;
    view: string;
    date: string;
    /** Quiet controls before the add (the calendars popover). */
    tools?: Snippet;
    /** The calendar's create control, last in the row. */
    add?: Snippet;
    onview: (view: CalendarView) => void;
    onstep: (step: number) => void;
    ontoday: () => void;
    ondate: (date: string) => void;
  } = $props();
  const unit = $derived(view === "plan" ? "day" : view === "agenda" ? "period" : view);
  /** The way back to now, in the view's own unit: "This week" in the week view, "Today" in a day or the agenda. */
  const back = $derived(view === "week" ? "This week" : view === "month" ? "This month" : view === "year" ? "This year" : "Today");
  let picker = $state<HTMLInputElement>();
</script>

<div class="range-bar" role="toolbar" aria-label="Calendar range">
  <h2>
    <button class="range" title="Go to date" aria-label={`${heading}. Go to date`} onclick={() => picker?.showPicker()}>{heading}</button>
    <input bind:this={picker} class="picker" type="date" tabindex="-1" aria-hidden="true" value={date}
      onchange={(event) => ondate(event.currentTarget.value)} />
  </h2>
  <div class="step">
    <button class="chevron" aria-label={`Previous ${unit}`} title={`Previous ${unit} (J)`} aria-keyshortcuts="J" onclick={() => onstep(-1)}>‹</button>
    <button class="today" title={`${back} (T)`} aria-keyshortcuts="T" onclick={ontoday}>{back}</button>
    <button class="chevron" aria-label={`Next ${unit}`} title={`Next ${unit} (K)`} aria-keyshortcuts="K" onclick={() => onstep(1)}>›</button>
  </div>
  <nav class="views" aria-label="Calendar view" data-page-tabs>
    {#each VIEWS as v (v.id)}
      <button aria-pressed={view === v.id} aria-keyshortcuts={v.key} title={`${v.label} (${v.key})`} onclick={() => onview(v.id)}>{v.label}</button>
    {/each}
  </nav>
  {#if tools}{@render tools()}{/if}
  {#if add}{@render add()}{/if}
</div>

<style>
  .range-bar {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 8px 14px;
    min-width: 0;
  }
  h2 {
    position: relative;
    flex: 1 1 auto;
    min-width: 0;
    margin: 0;
  }
  :global(:root body) .range-bar .range {
    max-width: 100%;
    padding: 0;
    overflow: hidden;
    border: 0;
    background: transparent;
    color: var(--ink);
    font: 400 26px/1.2 var(--font-ui);
    text-align: left;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .picker {
    position: absolute;
    left: 0;
    bottom: 0;
    width: 1px;
    height: 1px;
    opacity: 0;
    pointer-events: none;
  }
  .step {
    display: inline-flex;
    align-items: center;
    gap: 2px;
  }
  :global(:root body) .range-bar .step button {
    height: var(--control-h);
    padding: 0 10px;
    border: 0;
    border-radius: var(--r-control);
    background: transparent;
    color: var(--muted);
    font: 500 14px/1 var(--font-ui);
  }
  :global(:root body) .range-bar .step .chevron {
    width: var(--control-h);
    padding: 0 0 2px;
    font-size: 20px;
  }
  :global(:root body) .range-bar .step .today {
    color: var(--ink);
  }
  :global(:root body) .range-bar .step button:hover {
    background: color-mix(in srgb, var(--ink) 8%, transparent);
    color: var(--ink);
  }
  /* One pill of segments, drawn like the app's other view switches (Jobs' Map / List): the pill owns the hairline. */
  .views {
    display: inline-flex;
    align-items: stretch;
    gap: 2px;
    box-sizing: border-box;
    height: var(--control-h-lg);
    padding: 2px;
    border: 1px solid var(--hairline);
    border-radius: var(--r-control);
  }
  :global(:root body) .range-bar .views button {
    height: auto;
    min-height: 0;
    margin: 0;
    padding: 0 12px;
    border: 0;
    border-radius: var(--r-control);
    background: transparent;
    color: var(--muted);
    font: 500 14px/1 var(--font-ui);
    white-space: nowrap;
  }
  :global(:root body) .range-bar .views button:hover {
    color: var(--ink);
  }
  :global(:root body) .range-bar .views button[aria-pressed="true"] {
    background: var(--press-fill, var(--accent));
    color: var(--press-ink, var(--on-accent));
  }
  :global(:root body) .range-bar button:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }
</style>
