<script lang="ts">
  import { Button } from "@nunera/bracket";
  import EmptyState from "../EmptyState.svelte";
  import ExamCountdowns from "./ExamCountdowns.svelte";
  import PlanStrip from "./PlanStrip.svelte";
  import PlanTimeline from "./PlanTimeline.svelte";
  import ReturnsQueue from "./ReturnsQueue.svelte";
  import { HEAVY_MINUTES, blockDay, blockMinutes, daysBetween, formatDay, localTime, shiftDays } from "./planning";
  import type { Collection, Guide, Item, Workspace } from "./types";

  /**
   * Exams & plans: countdown cards per exam, the plan as a timeline or a
   * 12-day strip (drag blocks between days), and the spaced-returns queue.
   */
  let { data, disabled, change, confirm, notify, fail, onedit, onstudy, onopen }: {
    data: Workspace;
    disabled: boolean;
    change: (path: string, body: unknown) => Promise<boolean>;
    confirm: (message: string) => boolean;
    notify: (message: string) => void;
    fail: (message: string) => void;
    onedit: (kind: Collection, item?: Item) => void;
    onstudy: (exam: Item) => void;
    onopen: (guide: Guide) => void;
  } = $props();

  const today = $derived(data.stats.today);
  const upcoming = $derived(data.exams.filter((exam) => exam.exam_date! >= today).sort((a, b) => a.exam_date!.localeCompare(b.exam_date!)));
  const past = $derived(data.exams.filter((exam) => exam.exam_date! < today));
  const returns = $derived(data.guides.filter((guide) => guide.state.next_review));
  const subjectName = (id: number | null | undefined) => data.subjects.find((item) => item.id === id)?.name || "Unassigned";

  const VIEW_KEY = "kitty.study.plan-view";
  let view = $state<"timeline" | "days">("timeline");
  try { if (localStorage.getItem(VIEW_KEY) === "days") view = "days"; } catch { /* Timeline by default. */ }
  function setView(next: "timeline" | "days") {
    view = next;
    try { localStorage.setItem(VIEW_KEY, next); } catch { /* The choice still applies in this session. */ }
  }

  let selectedId = $state<number | null>(null);
  const selected = $derived(data.plans.find((block) => block.id === selectedId) ?? null);

  let planOpen = $state(false);
  let planExam = $state("");
  let planStart = $state("");
  let planMinutes = $state(180);
  let planBlock = $state(60);
  let planTime = $state("17:00");
  let replacePlan = $state(false);
  $effect.pre(() => { if (!planStart) planStart = today; });

  function openPlan(exam: Item) {
    planExam = exam.id.toString();
    planOpen = true;
    requestAnimationFrame(() => document.getElementById("study-plan-form")?.scrollIntoView({ block: "nearest" }));
  }
  async function generatePlan(event: SubmitEvent) {
    event.preventDefault();
    const exam = data.exams.find((item) => item.id === Number(planExam));
    if (!exam) return fail("Choose an exam.");
    if (await change("/study/generate-plan", {
      exam_id: exam.id, exam_revision: exam.revision, start_date: planStart, minutes: Number(planMinutes),
      block_minutes: Number(planBlock), local_time: planTime, replace: replacePlan,
    })) {
      planOpen = false;
      notify("Your dated plan is saved locally. Export blocks to your calendar explicitly.");
    }
  }
  async function exportBlock(block: Item) {
    if (!confirm("Add this study block to your local calendar? Google sync remains a separate calendar choice.")) return;
    if (await change("/calendar/events", {
      title: block.title, start: block.start, end: block.end, timezone: block.timezone, all_day: false,
      source_key: `study-plan:${block.id}`,
      description: `Study block for ${subjectName(block.subject_id)}. Created from your saved study plan.`,
    })) notify("Calendar block saved; repeated export does not duplicate it.");
  }
  /** Moves a block to another day at the same local time, keeping it before its exam. */
  async function move(block: Item, date: string) {
    const exam = data.exams.find((item) => item.id === block.exam_id);
    if (date < today) return fail("Study blocks can only move to today or later.");
    if (exam && date >= exam.exam_date!) return fail(`Blocks for ${exam.title} stay before the exam on ${formatDay(exam.exam_date!, { day: "numeric", month: "short" })}.`);
    const days = daysBetween(blockDay(block), date);
    const timezone = block.timezone || "UTC";
    selectedId = block.id;
    if (!await change("/study/plan", { id: block.id, revision: block.revision, start: shiftDays(block.start!, timezone, days), end: shiftDays(block.end!, timezone, days) })) return;
    const load = data.plans.filter((item) => blockDay(item) === date).reduce((sum, item) => sum + blockMinutes(item), 0);
    if (load > HEAVY_MINUTES) notify(`${formatDay(date, { weekday: "long", day: "numeric", month: "short" })} now has ${load} min planned — a heavy day.`);
  }
  async function grade(guide: Guide, result: "recalled" | "shaky") {
    await change("/study/returns", { session_id: guide.id, revision: guide.revision, result });
  }
</script>

{#snippet blockActions()}
  <footer class="selection">
    {#if selected}
      <span class="dot" style:background={data.subjects.find((item) => item.id === selected.subject_id)?.color || "var(--accent)"}></span>
      <b>{selected.title}</b>
      <span class="muted">{formatDay(blockDay(selected), { weekday: "short", day: "numeric", month: "short" })} · {localTime(selected.start!, selected.timezone || "UTC")} · {blockMinutes(selected)} min{selected.done ? " · done" : ""}</span>
      <span class="spacer"></span>
      <Button size="sm" {disabled} onclick={() => change("/study/plan", { id: selected.id, revision: selected.revision, done: !selected.done })}>{selected.done ? "Mark not done" : "Mark done"}</Button>
      <Button size="sm" {disabled} onclick={() => exportBlock(selected)}>Add to calendar</Button>
      <Button size="sm" {disabled} onclick={async () => { if (await change("/study/plan", { action: "delete", id: selected.id, revision: selected.revision })) selectedId = null; }}>Delete block</Button>
    {:else}
      <span class="muted">Select a block to mark it done, add it to your calendar or delete it. Done blocks do not log study time.</span>
    {/if}
    <span class="sum eyebrow">{data.plans.filter((block) => block.done).length}/{data.plans.length} blocks done</span>
  </footer>
{/snippet}

<div class="plans">
  <div class="heading">
    <span class="eyebrow">Exams · {upcoming.length} ahead</span>
    <div class="row">
      <Button size="sm" onclick={() => onedit("exams")}>Add exam</Button>
      <Button size="sm" onclick={() => (planOpen = !planOpen)} aria-expanded={planOpen}>Make a plan</Button>
    </div>
  </div>
  {#if upcoming.length}
    <ExamCountdowns {data} exams={upcoming} onedit={(exam) => onedit("exams", exam)} onplan={openPlan} {onstudy} />
  {:else}
    <EmptyState level={3} title="No exams ahead"><p>Add the dates you actually know. No exam dates are guessed.</p></EmptyState>
  {/if}
  {#if past.length}
    <details class="past">
      <summary>Past exams · {past.length}</summary>
      <ul>{#each past as exam (exam.id)}<li><button type="button" onclick={() => onedit("exams", exam)}>{exam.title} · {exam.exam_date}</button></li>{/each}</ul>
    </details>
  {/if}

  {#if planOpen}
    <form class="panel" id="study-plan-form" onsubmit={generatePlan}>
      <h3>Make a dated study plan</h3>
      <div class="form-grid">
        <label>Exam<select required bind:value={planExam}><option value="">Choose exam</option>{#each upcoming as exam (exam.id)}<option value={exam.id.toString()}>{exam.title} · {exam.exam_date}</option>{/each}</select></label>
        <label>First date<input type="date" required bind:value={planStart} /></label>
        <label>Total minutes<input type="number" min="1" max="10080" required bind:value={planMinutes} /></label>
        <label>Minutes per block<input type="number" min="1" max="240" required bind:value={planBlock} /></label>
        <label>Start time in exam timezone<input type="time" required bind:value={planTime} /></label>
      </div>
      <label class="check-row"><input type="checkbox" bind:checked={replacePlan} />Explicitly replace this exam's existing local plan</label>
      <p class="muted">One block per day, evenly spaced before the exam. Dates and durations come only from your inputs.</p>
      <div class="row"><Button type="submit" {disabled}>Save dated plan</Button><Button onclick={() => (planOpen = false)}>Cancel</Button></div>
    </form>
  {/if}

  <div class="heading">
    <span class="eyebrow">Study blocks</span>
    <div class="view-switch" role="radiogroup" aria-label="Plan layout">
      <button type="button" role="radio" aria-checked={view === "timeline"} onclick={() => setView("timeline")}>Timeline</button>
      <button type="button" role="radio" aria-checked={view === "days"} onclick={() => setView("days")}>Days</button>
    </div>
  </div>
  {#if view === "timeline"}
    <section class="panel board" aria-label="Timeline to exams">
      <PlanTimeline {data} exams={upcoming} {returns} selected={selectedId} onselect={(block) => (selectedId = block.id)} />
      {@render blockActions()}
    </section>
  {:else}
    <section class="panel board" aria-label="Next 12 days">
      <PlanStrip {data} {returns} selected={selectedId} {disabled} onselect={(block) => (selectedId = block.id)} onmove={move} />
      {@render blockActions()}
    </section>
  {/if}

  <div class="heading"><span class="eyebrow">Spaced returns · 1 · 3 · 7 · 16 · 35 days</span></div>
  <ReturnsQueue {data} {returns} {disabled} ongrade={grade} {onopen} />
</div>

<style>
  .plans {
    container-type: inline-size;
    display: grid;
    gap: 12px;
  }
  .heading {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    margin-top: 6px;
  }
  .board {
    display: grid;
    gap: 12px;
    margin: 0;
    padding: 16px 18px 12px;
  }
  .selection {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px;
    min-height: 30px;
    padding-top: 10px;
    border-top: 1px solid var(--hairline);
    color: var(--ink);
    font-size: 12.5px;
  }
  .selection .muted {
    font-size: 12px;
  }
  .spacer {
    flex: 1;
  }
  .sum {
    margin-left: auto;
  }
  .dot {
    width: 8px;
    height: 8px;
    border-radius: 999px;
  }
  .view-switch {
    display: inline-flex;
    gap: 2px;
    padding: 2px;
    border: 1px solid var(--hairline);
    border-radius: 999px;
  }
  .view-switch button {
    padding: 3px 10px;
    border: 0;
    border-radius: 999px;
    background: transparent;
    color: var(--muted);
    font-size: 12px;
  }
  .view-switch button[aria-checked="true"] {
    background: color-mix(in srgb, var(--ink) 12%, transparent);
    color: var(--ink);
  }
  .past ul {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    margin: 6px 0 0;
    padding: 0;
    list-style: none;
  }
  .past button {
    padding: 2px 9px;
    border: 1px solid var(--hairline);
    border-radius: 999px;
    background: transparent;
    color: var(--muted);
    font-size: 12px;
  }
</style>
