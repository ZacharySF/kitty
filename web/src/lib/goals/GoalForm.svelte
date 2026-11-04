<script lang="ts">
  import { untrack } from "svelte";
  import EditDrawer from "../ui/EditDrawer.svelte";
  import Select from "../ui/Select.svelte";
  import { createGoal, deleteGoal, loadGoal, updateGoal } from "./api";
  import { STATUSES, emptyMeasure, measureDraft, measureInputs, type Goal, type GoalStatus, type Id, type MeasureDraft } from "./goals";
  import MeasuresEditor from "./MeasuresEditor.svelte";

  /**
   * Q14 A: creates a goal or edits one (title, why, target date, status, measures) in the
   * side drawer. Saving sends the revision it was opened at; if the goal changed
   * meanwhile the server refuses, and the drawer reloads it so nothing is overwritten.
   */
  let { goal, onsaved, ondeleted, onclose }: {
    /** Null creates a new goal. */
    goal: Goal | null;
    onsaved: (goal: Goal) => void;
    ondeleted: (id: Id) => void;
    onclose: () => void;
  } = $props();

  // The drawer edits a copy taken when it opens; a reload after a conflict replaces it.
  let base = $state<Goal | null>(null);
  let title = $state("");
  let why = $state("");
  let target = $state("");
  let status = $state<GoalStatus>("active");
  let measures = $state<MeasureDraft[]>([]);
  let error = $state("");
  let busy = $state(false);
  let confirming = $state(false);

  function fill(source: Goal | null) {
    base = source;
    title = source?.title ?? "";
    why = source?.why ?? "";
    target = source?.target_date ?? "";
    status = source?.status ?? "active";
    measures = source ? source.measures.map(measureDraft) : [emptyMeasure()];
  }
  // The parent keys this drawer by goal, so the goal it opened with is the one it edits.
  untrack(() => fill(goal));

  async function save(event: SubmitEvent) {
    event.preventDefault();
    const name = title.trim();
    if (!name) {
      error = "A goal needs a title.";
      return;
    }
    const checked = measureInputs(measures);
    if (checked.error) {
      error = checked.error;
      return;
    }
    busy = true;
    error = "";
    const fields = { title: name, why: why.trim(), target_date: target || null, measures: checked.measures };
    try {
      onsaved(base ? await updateGoal(base.id, { ...fields, status, revision: base.revision }) : await createGoal(fields));
    } catch (cause) {
      await conflict(cause, "This goal changed while you were editing; it has been reloaded. Make your change again.");
    } finally {
      busy = false;
    }
  }

  async function remove() {
    if (!base) return;
    if (!confirming) {
      confirming = true;
      return;
    }
    busy = true;
    error = "";
    try {
      await deleteGoal(base.id, base.revision);
      ondeleted(base.id);
    } catch (cause) {
      await conflict(cause, "This goal changed since you opened it; it has been reloaded. Delete it again if you still want to.");
    } finally {
      busy = false;
      confirming = false;
    }
  }

  /** A 409 reloads the goal and explains; anything else is shown as it came. */
  async function conflict(cause: unknown, message: string) {
    const failure = cause as Error & { status?: number };
    if (failure.status !== 409 || !base) {
      error = failure.message;
      return;
    }
    try {
      fill(await loadGoal(base.id));
      error = message;
    } catch (reload) {
      error = (reload as Error).message;
    }
  }
</script>

<EditDrawer title={base ? "Edit goal" : "New goal"} meta={base ? base.title : "Rika tracks it and looks for what moves it"} accent="var(--c1)" {onclose}>
  <form id="goal-form" class="goal-form" onsubmit={save}>
    <label class="field"><span class="field-label">Title</span><input bind:value={title} required maxlength="200" placeholder="Land a summer 2027 frontend internship" /></label>
    <label class="field"><span class="field-label">Why</span><textarea bind:value={why} rows="3" maxlength="2000" placeholder="What it gets you, in your words"></textarea></label>
    <label class="field"><span class="field-label">Target date</span><input type="date" bind:value={target} /></label>
    {#if base}
      <div class="field">
        <span class="field-label">Status</span>
        <Select label="Status" kind="segments" bind:value={status} options={STATUSES} />
      </div>
    {/if}
    <MeasuresEditor bind:measures />
    {#if error}<p class="error" role="alert">{error}</p>{/if}
  </form>
  {#snippet footer()}
    {#if base}
      <button type="button" class="pill danger" disabled={busy} onclick={remove}>{confirming ? "Delete for good" : "Delete goal"}</button>
      <span class="spacer"></span>
    {/if}
    <button type="button" class="pill" onclick={onclose}>Cancel</button>
    <button type="submit" form="goal-form" class="pill primary" disabled={busy}>{base ? "Save" : "Create goal"}</button>
  {/snippet}
</EditDrawer>

<style>
  .goal-form {
    display: grid;
    gap: 12px;
  }
  .field {
    display: grid;
    gap: 5px;
  }
  .field-label {
    color: var(--muted);
    font-size: 12px;
  }
  textarea {
    resize: vertical;
  }
  .error {
    margin: 0;
    color: var(--error);
    font-size: 12px;
  }
  .spacer {
    flex: 1;
  }
  .pill {
    min-height: 30px;
  }
  .danger {
    color: var(--error);
  }
</style>
