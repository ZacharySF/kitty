<script module lang="ts">
  export type CalendarView = "day" | "week" | "month" | "agenda";
  export const VIEWS: { id: CalendarView; label: string; key: string }[] = [
    { id: "day", label: "Day", key: "D" },
    { id: "week", label: "Week", key: "W" },
    { id: "month", label: "Month", key: "M" },
    { id: "agenda", label: "Agenda", key: "A" },
  ];
</script>

<script lang="ts">
  import Icon from "../Icon.svelte";

  /** Glass capsule floating over the bottom of the calendar: range, step, go-to-date and D W M A views. */
  let { heading, view, date, flash = "", onview, onstep, ontoday, ondate }: {
    heading: string;
    view: string;
    date: string;
    /** Last shortcut key pressed, shown briefly as a key cap. */
    flash?: string;
    onview: (view: CalendarView) => void;
    onstep: (step: number) => void;
    ontoday: () => void;
    ondate: (date: string) => void;
  } = $props();
  const unit = $derived(view === "week" || view === "month" || view === "day" ? view : "period");
  let picker = $state<HTMLInputElement>();
</script>

<div class="range-pill" role="toolbar" aria-label="Calendar range">
  <button class="icon" aria-label={`Previous ${unit}`} aria-keyshortcuts="J" onclick={() => onstep(-1)}>‹</button>
  <button class="range" aria-live="polite" title="Back to today (T)" aria-keyshortcuts="T"
    aria-label={`${heading}. Back to today`} onclick={ontoday}>{heading}</button>
  <button class="icon" aria-label={`Next ${unit}`} aria-keyshortcuts="K" onclick={() => onstep(1)}>›</button>
  <button class="icon" aria-label="Go to date" title="Go to date" onclick={() => picker?.showPicker()}><Icon name="calendar" size={14} /></button>
  <input bind:this={picker} class="picker" type="date" tabindex="-1" aria-hidden="true" value={date}
    onchange={(event) => ondate(event.currentTarget.value)} />
  <span class="sep" aria-hidden="true"></span>
  {#each VIEWS as v (v.id)}
    <button class="letter" class:on={view === v.id} aria-pressed={view === v.id} aria-label={v.label}
      aria-keyshortcuts={v.key} title={`${v.label} (${v.key})`} onclick={() => onview(v.id)}>{v.key}</button>
  {/each}
  <kbd class="flash" class:show={!!flash} aria-hidden="true">{flash}</kbd>
</div>

<style>
  .range-pill {
    position: sticky;
    bottom: 14px;
    z-index: 6;
    display: flex;
    align-items: center;
    gap: 4px;
    width: max-content;
    max-width: calc(100% - 1rem);
    margin: -3.4rem auto 0.8rem;
    padding: 4px;
    border: 1px solid var(--hairline);
    border-radius: 999px;
    background: color-mix(in srgb, var(--solid-raised) 78%, transparent);
    backdrop-filter: blur(14px);
    box-shadow: 0 10px 28px color-mix(in srgb, var(--shadow-tint) 45%, transparent);
    white-space: nowrap;
  }
  button {
    border: 1px solid transparent;
    background: transparent;
    color: var(--ink);
    cursor: pointer;
  }
  .icon {
    display: inline-grid;
    place-items: center;
    width: 28px;
    height: 28px;
    padding: 0;
    border-radius: 999px;
    color: var(--muted);
  }
  .icon:hover,
  .range:hover,
  .letter:hover {
    background: color-mix(in srgb, var(--ink) 7%, transparent);
    color: var(--ink);
  }
  .range {
    min-width: 150px;
    padding: 4px 10px;
    border-radius: 999px;
    font: 700 0.95rem var(--font-title);
  }
  .picker {
    position: absolute;
    left: 50%;
    bottom: 0;
    width: 1px;
    height: 1px;
    opacity: 0;
    pointer-events: none;
  }
  .sep {
    width: 1px;
    height: 18px;
    margin: 0 4px;
    background: var(--hairline);
  }
  .letter {
    width: 28px;
    height: 28px;
    padding: 0;
    border-radius: 999px;
    color: var(--muted);
    font: 600 0.7rem var(--font-mono);
  }
  .letter.on {
    background: var(--ink);
    color: var(--solid-void);
  }
  .flash {
    display: inline-grid;
    place-items: center;
    min-width: 22px;
    height: 22px;
    margin-left: 2px;
    border: 1px solid var(--accent);
    border-radius: 6px;
    color: var(--accent);
    font: 600 0.68rem var(--font-mono);
    opacity: 0;
    transform: translateY(3px);
    transition: opacity 160ms ease, transform 160ms ease;
  }
  .flash.show {
    opacity: 1;
    transform: none;
  }
  @media (prefers-reduced-motion: reduce) {
    .flash {
      transition: none;
      transform: none;
    }
  }
</style>
