<script lang="ts">
  import { blockDay, blockMinutes, daysBetween, formatDay, localTime } from "./planning";
  import { minutesLabel, type Item, type Workspace } from "./types";

  /** One card per upcoming exam: days left, a readiness ring, blocks left and one action. */
  let { data, exams, onedit, onplan, onstudy }: {
    data: Workspace;
    exams: Item[];
    onedit: (exam: Item) => void;
    onplan: (exam: Item) => void;
    onstudy: (exam: Item) => void;
  } = $props();

  const today = $derived(data.stats.today);
  const RADIUS = 15;
  const LENGTH = 2 * Math.PI * RADIUS;
</script>

<section class="countdowns" aria-label="Exam countdowns">
  {#each exams as exam (exam.id)}
    {@const subject = data.subjects.find((item) => item.id === exam.subject_id)}
    {@const blocks = data.plans.filter((block) => block.exam_id === exam.id)}
    {@const done = blocks.filter((block) => block.done).length}
    {@const left = daysBetween(today, exam.exam_date!)}
    {@const ready = blocks.length ? done / blocks.length : 0}
    {@const next = blocks.filter((block) => !block.done && blockDay(block) >= today).sort((a, b) => a.start!.localeCompare(b.start!))[0]}
    <article class="card" class:soon={left <= 7} style:--tone={subject?.color || "var(--accent)"}>
      <header>
        <span class="eyebrow">{subject?.name || "No subject"}</span>
        {#if left <= 7}<span class="chip">{left === 0 ? "today" : "this week"}</span>{/if}
      </header>
      <div class="count">
        <strong>{left}</strong>
        <span>{left === 1 ? "day" : "days"}<br /><small>{formatDay(exam.exam_date!, { weekday: "short", day: "numeric", month: "short" })}</small></span>
      </div>
      <button type="button" class="title" onclick={() => onedit(exam)} aria-label={`Edit exam ${exam.title}`}>{exam.title}</button>
      <div class="ready">
        <svg width="38" height="38" viewBox="0 0 38 38" role="img" aria-label={`${Math.round(ready * 100)}% of planned blocks done`}>
          <circle cx="19" cy="19" r={RADIUS} class="track" />
          {#if done}<circle cx="19" cy="19" r={RADIUS} class="arc" stroke-dasharray={`${LENGTH * ready} ${LENGTH}`} />{/if}
          <text x="19" y="22">{Math.round(ready * 100)}</text>
        </svg>
        <div>
          <b>{done}/{blocks.length} blocks</b>
          <small>{blocks.length ? `${minutesLabel(blocks.filter((block) => !block.done).reduce((sum, block) => sum + blockMinutes(block) * 60, 0))} left` : "No plan yet"}</small>
          {#if next}<small>Next: {formatDay(blockDay(next), { weekday: "short", day: "numeric" })} · {localTime(next.start!, next.timezone || "UTC")}</small>{/if}
        </div>
      </div>
      {#if blocks.length}
        <button type="button" class="action" class:primary={left <= 7} onclick={() => onstudy(exam)}>Study now</button>
      {:else}
        <button type="button" class="action primary" onclick={() => onplan(exam)}>Make a plan</button>
      {/if}
    </article>
  {/each}
</section>

<style>
  .countdowns {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
    gap: 12px;
  }
  .card {
    position: relative;
    display: grid;
    align-content: start;
    gap: 10px;
    padding: 14px;
    overflow: hidden;
    border: 1px solid color-mix(in srgb, var(--tone) 30%, var(--hairline));
    border-radius: 14px;
    background: radial-gradient(120% 70% at 100% 0%, color-mix(in srgb, var(--tone) 14%, transparent), transparent 60%), var(--surface);
    transition: transform 0.2s ease, border-color 0.2s ease;
  }
  .card:hover {
    transform: translateY(-2px);
    border-color: var(--tone);
  }
  .card::before {
    content: "";
    position: absolute;
    inset: 0 0 auto;
    height: 3px;
    background: var(--tone);
  }
  header {
    display: flex;
    align-items: center;
    justify-content: space-between;
  }
  .chip {
    padding: 1px 7px;
    border-radius: 999px;
    background: color-mix(in srgb, var(--warning) 20%, transparent);
    color: var(--warning);
    font: 600 9px var(--font-mono);
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }
  .count {
    display: flex;
    align-items: flex-end;
    gap: 8px;
  }
  .count strong {
    color: var(--ink);
    font: 700 52px/0.9 var(--font-ui);
  }
  .count span {
    color: var(--muted);
    font: 500 11px/1.3 var(--font-mono);
    text-transform: uppercase;
  }
  .count small {
    color: var(--subtle);
    font-size: 9.5px;
  }
  .title {
    justify-self: start;
    padding: 0;
    background: none;
    color: var(--ink);
    font: 600 14px var(--font-ui);
    text-align: left;
  }
  .title:hover {
    text-decoration: underline;
    text-decoration-color: var(--tone);
  }
  .ready {
    display: flex;
    align-items: center;
    gap: 10px;
  }
  .ready div {
    display: grid;
    gap: 1px;
  }
  .ready b {
    color: var(--ink);
    font: 500 12px var(--font-ui);
  }
  .ready small {
    color: var(--muted);
    font-size: 11px;
  }
  svg circle {
    fill: none;
    stroke-width: 3.5;
  }
  .track {
    stroke: color-mix(in srgb, var(--ink) 10%, transparent);
  }
  .arc {
    stroke: var(--tone);
    stroke-linecap: round;
    transform: rotate(-90deg);
    transform-origin: 19px 19px;
  }
  text {
    fill: var(--muted);
    font: 500 9px var(--font-mono);
    text-anchor: middle;
  }
  .action {
    justify-self: start;
    padding: 4px 12px;
    border: 1px solid var(--border);
    border-radius: 999px;
    background: transparent;
    color: var(--ink);
    font: 500 12px var(--font-ui);
  }
  .action:hover {
    border-color: var(--tone);
  }
  .action.primary {
    border-color: var(--tone);
    background: color-mix(in srgb, var(--tone) 22%, transparent);
  }
  @media (prefers-reduced-motion: reduce) {
    .card {
      transition: none;
    }
  }
</style>
