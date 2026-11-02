<script lang="ts">
  import { buildStore, setPreferences } from "../build.svelte";
  import { Button } from "@nunera/bracket";
  import EmptyState from "../EmptyState.svelte";
  import OverflowMenu from "../ui/OverflowMenu.svelte";
  import Popover from "../ui/Popover.svelte";
  import QuickAdd from "../ui/QuickAdd.svelte";
  import Select from "../ui/Select.svelte";
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
    onedit: (kind: Collection, item?: Item, defaults?: Partial<Item>) => void;
    onstudy: (exam: Item) => void;
    onopen: (guide: Guide) => void;
  } = $props();

  const today = $derived(data.stats.today);
  const upcoming = $derived(data.exams.filter((exam) => exam.exam_date! >= today).sort((a, b) => a.exam_date!.localeCompare(b.exam_date!)));
  const past = $derived(data.exams.filter((exam) => exam.exam_date! < today));
  const returns = $derived(data.guides.filter((guide) => guide.state.next_review));
  const subjectName = (id: number | null | undefined) => data.subjects.find((item) => item.id === id)?.name || "Unassigned";

  // Timeline or days: a build preference (ui.study.plans_view).
  const view = $derived(buildStore.build.ui.study.plans_view);
  function setView(next: "timeline" | "days") {
    void setPreferences({ ui: { study: { plans_view: next } } });
  }

  let selectedId = $state<number | null>(null);
  const selected = $derived(data.plans.find((block) => block.id === selectedId) ?? null);

  let examTitle = $state("");
  let examDate = $state("");
  let examSubject = $state("");
  $effect.pre(() => { if (!examDate) examDate = shiftDay(today, 7); });
  const subjectOptions = $derived([{ value: "", label: "No subject" }, ...data.subjects.map((item) => ({ value: String(item.id), label: item.name ?? "", color: item.color }))]);
  function shiftDay(day: string, days: number) {
    return new Date(Date.parse(`${day}T12:00:00Z`) + days * 86_400_000).toISOString().slice(0, 10);
  }
  /** A subject typed into the exam's subject chip is created on the spot. */
  async function createSubject(name: string) {
    if (await change("/study/subjects", { action: "save", name, color: "#7c6fdb" }))
      examSubject = String(data.subjects.filter((item) => item.name === name).at(-1)?.id ?? "");
  }
  async function addExam(title: string) {
    if (!examDate) return false;
    const saved = await change("/study/exams", {
      action: "save", title, subject_id: examSubject ? Number(examSubject) : null, exam_date: examDate, timezone: data.settings.timezone, notes: "",
    });
    if (saved) { examDate = shiftDay(today, 7); examSubject = ""; }
    return saved;
  }

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
    }
  }
  async function exportBlock(block: Item) {
    if (!confirm("Add this study block to your local calendar? Google sync remains a separate calendar choice.")) return;
    await change("/calendar/events", {
      title: block.title, start: block.start, end: block.end, timezone: block.timezone, all_day: false,
      source_key: `study-plan:${block.id}`,
      description: `Study block for ${subjectName(block.subject_id)}. Created from your saved study plan.`,
    });
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
    {/if}
    {#if data.plans.length}<span class="sum eyebrow" title="Done blocks do not log study time">{data.plans.filter((block) => block.done).length}/{data.plans.length} blocks done</span>{/if}
  </footer>
{/snippet}

<div class="plans">
  <div class="heading">
    <span class="eyebrow">Exams{#if upcoming.length} <span class="count">{upcoming.length}</span>{/if}</span>
    <div class="row">
      {#if past.length}<OverflowMenu label="Past exams" items={past.map((exam) => ({ label: `${exam.title} · ${exam.exam_date}`, onselect: () => onedit("exams", exam) }))} />{/if}
      {#if upcoming.length}
        <Popover bind:open={planOpen} label="Make a plan" placement="bottom-end">
          {#snippet trigger(props)}<button type="button" class="pill" {...props}>Make a plan</button>{/snippet}
          <form class="plan-form" onsubmit={generatePlan}>
            <div class="field"><span>Exam</span><Select kind="list" label="Exam" placeholder="Choose exam" options={upcoming.map((exam) => ({ value: exam.id.toString(), label: exam.title ?? "", hint: exam.exam_date }))} bind:value={planExam} /></div>
            <label class="field">First day<input type="date" required bind:value={planStart} /></label>
            <div class="pair">
              <label class="field">Total minutes<input type="number" min="1" max="10080" required bind:value={planMinutes} /></label>
              <label class="field">Per block<input type="number" min="1" max="240" required bind:value={planBlock} /></label>
            </div>
            <label class="field">Start time<input type="time" required bind:value={planTime} /></label>
            <label class="check-row"><input type="checkbox" bind:checked={replacePlan} />Replace this exam's existing plan</label>
            <div class="row end"><button type="submit" class="pill primary" {disabled}>Save plan</button></div>
          </form>
        </Popover>
      {/if}
      <QuickAdd label="Add exam" placeholder="Organic chemistry midterm" bind:value={examTitle} onsave={addExam}
        onmore={(title) => onedit("exams", undefined, { title, exam_date: examDate, subject_id: examSubject ? Number(examSubject) : null })}>
        {#snippet chips()}
          <input class="date-chip" type="date" aria-label="Exam date" required bind:value={examDate} />
          <Select kind="search" label="Exam subject" options={subjectOptions} bind:value={examSubject} oncreate={createSubject} createLabel={(name) => `New subject “${name}”`} />
        {/snippet}
      </QuickAdd>
    </div>
  </div>
  {#if upcoming.length}
    <ExamCountdowns {data} exams={upcoming} onedit={(exam) => onedit("exams", exam)} onplan={openPlan} {onstudy} />
  {:else}
    <EmptyState level={3} title="No exams ahead"><p>Add an exam to start its countdown.</p></EmptyState>
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

  {#if returns.length}
    <div class="heading"><span class="eyebrow" title="Returns after 1 · 3 · 7 · 16 · 35 days">Spaced returns</span></div>
    <ReturnsQueue {data} {returns} {disabled} ongrade={grade} {onopen} />
  {/if}
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
  .count {
    margin-left: 4px;
    color: var(--muted);
    font: 500 10.5px var(--font-mono);
  }
  .plan-form {
    display: grid;
    gap: 10px;
    width: 280px;
    padding: 12px;
  }
  .field {
    display: grid;
    gap: 6px;
    color: var(--muted);
    font-size: 12px;
  }
  .field input {
    width: 100%;
    box-sizing: border-box;
  }
  .pair {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 8px;
  }
  .end {
    justify-content: flex-end;
  }
  .date-chip {
    height: 24px;
    padding: 0 10px;
    border: 1px solid var(--hairline);
    border-radius: 999px;
    background: transparent;
    color: var(--ink);
    font: 500 12px var(--font-ui);
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
</style>
