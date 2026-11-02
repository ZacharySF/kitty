<script lang="ts">
  import { HEAVY_MINUTES, addDays, blockDay, blockMinutes, formatDay, localTime, returnTitle } from "./planning";
  import type { Guide, Item, Workspace } from "./types";

  /** Twelve day columns from today with stacked blocks, exams and returns; drag blocks between days. */
  let { data, returns, selected, disabled, onselect, onmove }: {
    data: Workspace;
    returns: Guide[];
    selected: number | null;
    disabled: boolean;
    onselect: (block: Item) => void;
    onmove: (block: Item, date: string) => void;
  } = $props();

  const DAYS = 12;
  const today = $derived(data.stats.today);
  const dates = $derived(Array.from({ length: DAYS }, (_, index) => addDays(today, index)));
  const colour = (id: number | null | undefined) => data.subjects.find((item) => item.id === id)?.color || "var(--accent)";
  const byDay = $derived.by(() => {
    const days = new Map<string, Item[]>();
    for (const block of data.plans) days.set(blockDay(block), [...(days.get(blockDay(block)) ?? []), block]);
    return days;
  });
  let dragging = $state<number | null>(null);
  let over = $state<string | null>(null);

  function drop(date: string) {
    const block = data.plans.find((item) => item.id === dragging);
    dragging = null;
    over = null;
    if (block && blockDay(block) !== date) onmove(block, date);
  }
  function nudge(event: KeyboardEvent, block: Item) {
    if (!event.altKey || (event.key !== "ArrowLeft" && event.key !== "ArrowRight")) return;
    event.preventDefault();
    onmove(block, addDays(blockDay(block), event.key === "ArrowLeft" ? -1 : 1));
  }
</script>

<div class="strip">
  {#each dates as date, index (date)}
    {@const blocks = byDay.get(date) ?? []}
    {@const load = blocks.reduce((sum, block) => sum + blockMinutes(block), 0)}
    {@const heavy = load > HEAVY_MINUTES}
    {@const exams = data.exams.filter((exam) => exam.exam_date === date)}
    {@const due = returns.filter((guide) => (index === 0 ? guide.state.next_review <= date : guide.state.next_review === date))}
    <section class="day" class:today={index === 0} class:over={over === date} class:heavy role="group"
      aria-label={`${formatDay(date, { weekday: "short", day: "numeric", month: "short" })}, ${load} min planned${heavy ? ", heavy day" : ""}`}
      ondragover={(event) => { if (dragging !== null) { event.preventDefault(); over = date; } }}
      ondragleave={() => { if (over === date) over = null; }}
      ondrop={(event) => { event.preventDefault(); drop(date); }}>
      <header>
        <span class="weekday">{formatDay(date, { weekday: "short" })}</span>
        <span class="date">{Number(date.slice(8))}</span>
      </header>
      <div class="load" aria-hidden="true"><i style:width={`${Math.min(100, (load / (HEAVY_MINUTES * 1.5)) * 100)}%`}></i></div>
      {#if heavy}<span class="warning">Heavy day · {load} min</span>{/if}
      {#each exams as exam (exam.id)}<div class="exam" style:--tone={colour(exam.subject_id)}>{exam.title}</div>{/each}
      {#each blocks as block (block.id)}
        <button type="button" class="block" class:done={block.done} class:on={selected === block.id} class:dragging={dragging === block.id}
          style:--tone={colour(block.subject_id)} style:min-height={`${18 + blockMinutes(block) * 0.4}px`}
          draggable={!disabled} aria-pressed={selected === block.id}
          aria-label={`${block.title}, ${localTime(block.start!, block.timezone || "UTC")}, ${blockMinutes(block)} min${block.done ? ", done" : ""}`}
          aria-keyshortcuts="Alt+ArrowLeft Alt+ArrowRight"
          ondragstart={(event) => { dragging = block.id; event.dataTransfer?.setData("text/plain", String(block.id)); }}
          ondragend={() => { dragging = null; over = null; }}
          onkeydown={(event) => nudge(event, block)}
          onclick={() => onselect(block)}>
          <b>{block.title}</b><small>{localTime(block.start!, block.timezone || "UTC")} · {blockMinutes(block)}m</small>
        </button>
      {/each}
      {#each due as guide (guide.id)}<div class="return" style:--tone={colour(guide.subject_id)} title={returnTitle(guide)}>✿ {returnTitle(guide)}</div>{/each}
    </section>
  {/each}
</div>
<p class="hint">Drag a block to another day, or focus it and press Alt + ←/→. Days past {HEAVY_MINUTES} minutes are marked heavy.</p>

<style>
  .strip {
    display: grid;
    grid-template-columns: repeat(12, minmax(0, 1fr));
    gap: 5px;
  }
  @container (max-width: 760px) {
    .strip {
      grid-template-columns: repeat(6, minmax(0, 1fr));
    }
  }
  .day {
    display: grid;
    grid-auto-rows: min-content;
    gap: 4px;
    min-width: 0;
    min-height: 200px;
    padding: 6px 5px;
    border: 1px solid color-mix(in srgb, var(--border) 60%, transparent);
    border-radius: 10px;
    background: color-mix(in srgb, var(--surface-raised) 60%, transparent);
    transition: border-color 0.15s ease, background 0.15s ease;
  }
  .day.today {
    border-color: color-mix(in srgb, var(--accent) 60%, transparent);
  }
  .day.heavy {
    border-color: color-mix(in srgb, var(--warning) 55%, transparent);
  }
  .day.over {
    border-color: var(--ink);
    background: color-mix(in srgb, var(--accent) 10%, transparent);
  }
  header {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    padding: 0 2px;
  }
  .weekday {
    color: var(--subtle);
    font: 500 8.5px var(--font-mono);
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }
  .today .weekday {
    color: var(--accent);
  }
  .date {
    color: var(--ink);
    font: 700 15px var(--font-ui);
  }
  .load {
    height: 2px;
    border-radius: 2px;
    background: color-mix(in srgb, var(--ink) 8%, transparent);
  }
  .load i {
    display: block;
    height: 100%;
    border-radius: inherit;
    background: var(--muted);
  }
  .heavy .load i {
    background: var(--warning);
  }
  .warning {
    color: var(--warning);
    font: 600 8.5px/1.2 var(--font-mono);
    text-transform: uppercase;
  }
  .exam {
    padding: 3px 5px;
    overflow: hidden;
    border-radius: 6px;
    background: var(--tone);
    color: var(--solid-void);
    font: 600 9px/1.2 var(--font-mono);
    letter-spacing: 0.04em;
    text-overflow: ellipsis;
    text-transform: uppercase;
  }
  .block {
    display: grid;
    align-content: start;
    gap: 1px;
    min-width: 0;
    padding: 3px 5px;
    border: 0;
    border-left: 2px solid var(--tone);
    border-radius: 5px;
    background: color-mix(in srgb, var(--tone) 18%, transparent);
    text-align: left;
    cursor: grab;
    transition: opacity 0.15s ease, transform 0.15s ease;
  }
  .block:hover {
    transform: translateY(-1px);
    background: color-mix(in srgb, var(--tone) 28%, transparent);
  }
  .block.on {
    box-shadow: 0 0 0 1px var(--tone);
  }
  .block.done b {
    text-decoration: line-through;
  }
  .block.dragging {
    opacity: 0.4;
  }
  .block b {
    overflow: hidden;
    color: var(--ink);
    font: 500 10px/1.25 var(--font-ui);
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .block small {
    color: var(--muted);
    font: 500 8.5px var(--font-mono);
  }
  .return {
    padding: 2px 5px;
    overflow: hidden;
    border: 1px dashed var(--tone);
    border-radius: 5px;
    color: var(--muted);
    font: 500 9px var(--font-mono);
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .hint {
    margin: 8px 0 0;
    color: var(--muted);
    font-size: 12px;
  }
  @media (prefers-reduced-motion: reduce) {
    .day,
    .block {
      transition: none;
    }
  }
</style>
