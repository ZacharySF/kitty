<script lang="ts">
  import Blossom from "./Blossom.svelte";
  import { addDays, blockDay, blockMinutes, daysBetween, formatDay, localTime, returnTitle } from "./planning";
  import type { Guide, Item, Workspace } from "./types";

  /** Two weeks on one axis: a lane per exam with its blocks, the exam as a blossom, and a spaced-returns lane. */
  let { data, exams, returns, selected, onselect }: {
    data: Workspace;
    exams: Item[];
    returns: Guide[];
    selected: number | null;
    onselect: (block: Item) => void;
  } = $props();

  const FROM = -2;
  const TO = 13;
  const SPAN = TO - FROM + 1;
  const today = $derived(data.stats.today);
  const x = (offset: number) => ((offset - FROM + 0.5) / SPAN) * 100;
  const colour = (id: number | null | undefined) => data.subjects.find((item) => item.id === id)?.color || "var(--accent)";
  const lanes = $derived(exams.map((exam) => ({
    exam,
    left: daysBetween(today, exam.exam_date!),
    blocks: data.plans.filter((block) => block.exam_id === exam.id)
      .map((block) => ({ block, offset: daysBetween(today, blockDay(block)) }))
      .filter(({ offset }) => offset >= FROM && offset <= TO),
  })));
  const marks = $derived(returns.map((guide) => ({ guide, offset: Math.max(FROM, daysBetween(today, guide.state.next_review)) }))
    .filter(({ offset }) => offset <= TO));
</script>

<div class="timeline">
  <div class="axis" aria-hidden="true">
    {#each { length: SPAN } as _, index (index)}
      {@const date = addDays(today, FROM + index)}
      <span class="tick" class:today={FROM + index === 0} style:left={`${x(FROM + index)}%`}>
        <b>{formatDay(date, { weekday: "narrow" })}</b>{Number(date.slice(8))}
      </span>
    {/each}
  </div>
  <div class="lanes">
    <span class="now" style:left={`${x(0)}%`} aria-hidden="true"></span>
    {#each lanes as lane (lane.exam.id)}
      <div class="lane" style:--tone={colour(lane.exam.subject_id)}>
        <span class="label"><span class="dot" style:background={colour(lane.exam.subject_id)}></span>{lane.exam.title}</span>
        <span class="rail" style:width={`${x(Math.min(lane.left, TO)) - x(FROM)}%`} style:left={`${x(FROM)}%`}></span>
        {#each lane.blocks as { block, offset } (block.id)}
          {@const minutes = blockMinutes(block)}
          <button type="button" class="block" class:done={block.done} class:on={selected === block.id}
            style:left={`${x(offset)}%`} style:width={`${Math.min(0.9, Math.max(0.35, minutes / 90)) * (100 / SPAN)}%`}
            aria-label={`${block.title}, ${formatDay(blockDay(block), { weekday: "long", day: "numeric", month: "long" })} ${localTime(block.start!, block.timezone || "UTC")}, ${minutes} min${block.done ? ", done" : ""}`}
            aria-pressed={selected === block.id} onclick={() => onselect(block)}></button>
        {/each}
        {#if lane.left <= TO}
          <span class="exam" style:left={`${x(lane.left)}%`} role="img"
            aria-label={`${lane.exam.title} ${lane.left === 0 ? "today" : `in ${lane.left} ${lane.left === 1 ? "day" : "days"}`}`}>
            <Blossom size={22} tone={colour(lane.exam.subject_id)} />
          </span>
        {:else}
          <span class="beyond">in {lane.left} days →</span>
        {/if}
      </div>
    {/each}
    <div class="lane returns">
      <span class="label"><span class="petal" aria-hidden="true">✿</span>Spaced returns</span>
      {#each marks as { guide, offset } (guide.id)}
        <span class="return" style:left={`${x(offset)}%`} style:--tone={colour(guide.subject_id)} role="img"
          aria-label={`Spaced return: ${returnTitle(guide)}, ${guide.state.next_review <= today ? "due" : formatDay(guide.state.next_review, { weekday: "short", day: "numeric", month: "short" })}`}
          title={returnTitle(guide)}></span>
      {/each}
    </div>
  </div>
</div>

<style>
  .timeline {
    display: grid;
    gap: 6px;
    padding: 4px 0 0 150px;
  }
  .axis {
    position: relative;
    height: 26px;
  }
  .tick {
    position: absolute;
    display: grid;
    justify-items: center;
    transform: translateX(-50%);
    color: var(--muted);
    font: 500 10px var(--font-mono);
  }
  .tick b {
    color: var(--subtle);
    font-size: 8.5px;
    font-weight: 500;
  }
  .tick.today {
    padding: 0 5px;
    border-radius: 6px;
    background: var(--accent);
    color: var(--solid-void);
  }
  .tick.today b {
    color: var(--solid-void);
  }
  .lanes {
    position: relative;
    display: grid;
    gap: 8px;
  }
  .now {
    position: absolute;
    top: -4px;
    bottom: -4px;
    width: 1px;
    background: var(--accent);
    box-shadow: 0 0 8px var(--accent);
  }
  .lane {
    position: relative;
    height: 36px;
    border-radius: 10px;
    background: color-mix(in srgb, var(--surface-raised) 70%, transparent);
  }
  .label {
    position: absolute;
    right: calc(100% + 12px);
    top: 50%;
    display: flex;
    align-items: center;
    gap: 7px;
    max-width: 138px;
    overflow: hidden;
    color: var(--ink);
    font-size: 12.5px;
    text-overflow: ellipsis;
    white-space: nowrap;
    transform: translateY(-50%);
  }
  .dot {
    flex: none;
    width: 8px;
    height: 8px;
    border-radius: 999px;
  }
  .rail {
    position: absolute;
    top: 50%;
    height: 2px;
    background: linear-gradient(90deg, transparent, color-mix(in srgb, var(--tone) 50%, transparent));
  }
  .block {
    position: absolute;
    top: 8px;
    height: 20px;
    padding: 0;
    border: 1px solid var(--tone);
    border-radius: 999px;
    background: color-mix(in srgb, var(--tone) 14%, var(--solid-void));
    transform: translateX(-50%);
    transition: transform 0.15s ease, background 0.15s ease;
  }
  .block.done {
    background: var(--tone);
  }
  .block.on,
  .block:hover {
    box-shadow: 0 0 0 2px color-mix(in srgb, var(--tone) 40%, transparent), 0 0 14px color-mix(in srgb, var(--tone) 45%, transparent);
    transform: translateX(-50%) scale(1.08);
  }
  .exam {
    position: absolute;
    top: 50%;
    transform: translate(-50%, -50%);
    filter: drop-shadow(0 0 6px color-mix(in srgb, var(--tone) 70%, transparent));
  }
  .beyond {
    position: absolute;
    right: 8px;
    top: 50%;
    color: var(--muted);
    font: 500 10px var(--font-mono);
    transform: translateY(-50%);
  }
  .returns {
    height: 26px;
    border: 1px dashed var(--hairline);
    background: none;
  }
  .petal {
    color: var(--accent);
  }
  .return {
    position: absolute;
    top: 50%;
    width: 10px;
    height: 10px;
    border: 2px solid var(--tone);
    border-radius: 999px 0;
    transform: translate(-50%, -50%) rotate(45deg);
  }
  @container (max-width: 640px) {
    .timeline {
      padding-left: 0;
    }
    .label {
      position: static;
      transform: none;
      padding: 2px 8px;
      font-size: 11px;
    }
  }
  @media (prefers-reduced-motion: reduce) {
    .block {
      transition: none;
    }
  }
</style>
