<script lang="ts">
  import { Button } from "@nunera/bracket";
  import EmptyState from "../EmptyState.svelte";
  import type { Collection, Item, Workspace } from "./types";

  /** Exams, the dated-plan generator and saved study blocks. */
  let { data, disabled, change, confirm, notify, fail, onedit, ondelete }: {
    data: Workspace;
    disabled: boolean;
    change: (path: string, body: unknown) => Promise<boolean>;
    confirm: (message: string) => boolean;
    notify: (message: string) => void;
    fail: (message: string) => void;
    onedit: (kind: Collection, item?: Item) => void;
    ondelete: (kind: Collection, item: Item) => void;
  } = $props();

  const subjectName = (id: number | null | undefined) => data.subjects.find((item) => item.id === id)?.name || "Unassigned";
  let planExam = $state("");
  let planStart = $state("");
  let planMinutes = $state(180);
  let planBlock = $state(60);
  let planTime = $state("17:00");
  let replacePlan = $state(false);
  $effect.pre(() => { if (!planStart) planStart = data.stats.today; });

  async function generatePlan(event: SubmitEvent) {
    event.preventDefault();
    const exam = data.exams.find((item) => item.id === Number(planExam));
    if (!exam) return fail("Choose an exam.");
    if (await change("/study/generate-plan", {
      exam_id: exam.id, exam_revision: exam.revision, start_date: planStart, minutes: Number(planMinutes),
      block_minutes: Number(planBlock), local_time: planTime, replace: replacePlan,
    })) notify("Your dated plan is saved locally. Export blocks to your calendar explicitly.");
  }
  async function exportBlock(block: Item) {
    if (!confirm("Add this study block to your local calendar? Google sync remains a separate calendar choice.")) return;
    if (await change("/calendar/events", {
      title: block.title, start: block.start, end: block.end, timezone: block.timezone, all_day: false,
      source_key: `study-plan:${block.id}`,
      description: `Study block for ${subjectName(block.subject_id)}. Created from your saved study plan.`,
    })) notify("Calendar block saved; repeated export does not duplicate it.");
  }
</script>

<div class="row"><h3>Exam schedule</h3><Button onclick={() => onedit("exams")}>Add exam</Button></div>
{#each data.exams as exam (exam.id)}
  <div class="panel row">
    <strong>{exam.title}</strong><span>{exam.exam_date} · {exam.timezone} · {subjectName(exam.subject_id)}</span>
    <Button onclick={() => (planExam = exam.id.toString())}>Plan study</Button>
    <Button onclick={() => onedit("exams", exam)}>Edit</Button><Button onclick={() => ondelete("exams", exam)}>Delete</Button>
  </div>
{:else}<p>Add the dates you actually know. No exam dates are guessed.</p>{/each}
<form class="panel" onsubmit={generatePlan}>
  <h3>Make a dated study plan</h3>
  <div class="form-grid">
    <label>Exam<select required bind:value={planExam}><option value="">Choose exam</option>{#each data.exams as exam (exam.id)}<option value={exam.id.toString()}>{exam.title} · {exam.exam_date}</option>{/each}</select></label>
    <label>First date<input type="date" required bind:value={planStart} /></label>
    <label>Total minutes<input type="number" min="1" max="10080" required bind:value={planMinutes} /></label>
    <label>Minutes per block<input type="number" min="1" max="240" required bind:value={planBlock} /></label>
    <label>Start time in exam timezone<input type="time" required bind:value={planTime} /></label>
  </div>
  <label class="check-row"><input type="checkbox" bind:checked={replacePlan} />Explicitly replace this exam's existing local plan</label>
  <p class="muted">One block per day, evenly spaced before the exam. Dates and durations come only from your inputs. Calendar exports are separate records.</p>
  <Button type="submit" {disabled}>Save dated plan</Button>
</form>
<h3>Saved study blocks</h3>
{#each data.plans as block (block.id)}
  <article class="panel row">
    <label class="check-row"><input type="checkbox" checked={block.done} {disabled} onchange={() => change("/study/plan", { id: block.id, revision: block.revision, done: !block.done })} />{block.title}</label>
    <span>{new Date(block.start!).toLocaleString(undefined, { timeZone: block.timezone })} — {new Date(block.end!).toLocaleTimeString(undefined, { timeZone: block.timezone })} ({block.timezone})</span>
    <Button {disabled} onclick={() => exportBlock(block)}>Add to calendar</Button>
    <Button onclick={() => change("/study/plan", { action: "delete", id: block.id, revision: block.revision })}>Delete block</Button>
  </article>
{:else}<EmptyState level={3} title="No planned blocks yet"><p>Make a dated study plan above to fill this in.</p></EmptyState>{/each}
<p class="muted">Checking a planned block does not log study time. Complete a focus session or add a manual session.</p>
