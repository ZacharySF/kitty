<script lang="ts">
  import { tick } from "svelte";
  import { api, type TaskRecord } from "./api";
  import TaskStatus from "./TaskStatus.svelte";

  let { jobId, demo = false }: { jobId?: number; demo?: boolean } = $props();
  type Question = {
    id: number;
    text: string;
    kind: string;
    job_ids: number[];
    companies: string[];
    required_jobs: number;
    choices: string[];
  };
  let questions = $state<Question[]>([]);
  let kinds = $state<string[]>([]);
  let kind = $state("all");
  let text = $state("");
  let error = $state("");
  let busy = $state(false);
  let task = $state<TaskRecord | null>(null);
  let collectLimit = $state(100);
  let states = $state(["new", "queued"]);
  let tiers = $state(["A", "B"]);
  let minJobs = $state(0);
  let dialog: HTMLDialogElement;
  let cancel: HTMLButtonElement;
  let input: HTMLTextAreaElement;
  const shown = $derived(
    questions.filter(
      (item) =>
        (kind === "all" || item.kind === kind) &&
        item.job_ids.length >= minJobs,
    ),
  );
  function download() {
    const url = URL.createObjectURL(
      new Blob([JSON.stringify({ questions: shown }, null, 2)], {
        type: "application/json",
      }),
    );
    const link = document.createElement("a");
    link.href = url;
    link.download = "kitty-questions.json";
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  async function refresh() {
    error = "";
    try {
      const data = await api<{ questions: Question[]; kinds: string[] }>(
        `/questions${jobId ? `?job_id=${jobId}` : ""}`,
      );
      questions = data.questions;
      kinds = data.kinds;
    } catch (cause) {
      error =
        cause instanceof Error ? cause.message : "Unable to load questions";
    }
  }
  async function save() {
    busy = true;
    error = "";
    try {
      await api("/questions", { text, ...(jobId ? { job_id: jobId } : {}) });
      text = "";
      await refresh();
    } catch (cause) {
      error = cause instanceof Error ? cause.message : "Unable to log question";
    } finally {
      busy = false;
    }
  }
  async function collect() {
    if (demo || busy) return;
    busy = true;
    error = "";
    try {
      task = await api<TaskRecord>("/questions/collect", {
        confirmed: true,
        limit: collectLimit,
        states,
        tiers,
      });
      dialog.close();
    } catch (cause) {
      error =
        cause instanceof Error ? cause.message : "Unable to collect questions";
    } finally {
      busy = false;
    }
  }
  async function confirmCollect() {
    await tick();
    dialog.showModal();
    cancel.focus();
  }
  export function focusQuestion() {
    input?.focus();
  }
  $effect(() => {
    jobId;
    void refresh();
  });
</script>

<section class="questions" aria-label="Screening questions">
  <header class="row">
    <h3>Screening questions</h3>
    <button class="button" onclick={refresh} disabled={busy}>Refresh</button>
    <button
      class="button"
      onclick={confirmCollect}
      disabled={demo || busy || task?.status === "running"}
      >Collect queue questions</button
    >
  </header>
  <p class="muted">
    Answers are yours to write in Profile → Answers. Unknown questions stay
    blank; nothing generates applicant facts.
  </p>
  {#if demo}<p class="muted">
      Public form collection is disabled in demo mode.
    </p>{/if}
  {#if error}<p class="error-banner" role="alert">{error}</p>{/if}
  <TaskStatus
    {task}
    oncomplete={(result) => {
      task = result;
      void refresh();
    }}
  />
  <form
    onsubmit={(event) => {
      event.preventDefault();
      void save();
    }}
  >
    <label
      >Question encountered {#if jobId}(job #{jobId}){/if}
      <textarea
        bind:this={input}
        bind:value={text}
        required
        maxlength="4000"
        rows="2"
        placeholder="Paste the exact question; no answer is invented"
      ></textarea>
    </label>
    <button class="button" disabled={busy || !text.trim()}>Log question</button>
  </form>
  <label
    >Question type <select bind:value={kind}
      ><option value="all">All types</option>{#each kinds as value}<option
          {value}>{value}</option
        >{/each}</select
    ></label
  >
  <label
    >Minimum jobs asking <input
      type="number"
      min="0"
      max="100000"
      step="1"
      bind:value={minJobs}
    /></label
  >
  <button class="button" onclick={download}
    >Download filtered questions JSON</button
  >
  {#if !shown.length}<p class="muted">
      No questions in this view. This does not prove that every application is
      complete.
    </p>{/if}
  <ul>
    {#each shown as question (question.id)}
      <li>
        <strong>{question.text}</strong>
        <span class="badge">{question.kind}</span>
        <p class="muted">
          {question.job_ids.length} jobs · required by {question.required_jobs} ·
          {question.companies.join(", ") || "Manually logged"}
        </p>
        {#if question.choices.length}<p>
            Choices: {question.choices.join(" · ")}
          </p>{/if}
      </li>
    {/each}
  </ul>
</section>

<dialog bind:this={dialog} aria-label="Confirm public question collection">
  <h3>Read public application questions?</h3>
  <p>
    This makes polite, cached requests for the selected queue states and tiers
    across all tracks. Unsupported or restricted forms are skipped with reasons.
    No forms are filled or submitted.
  </p>
  <label
    >Maximum jobs <input
      type="number"
      min="1"
      max="1000"
      step="1"
      bind:value={collectLimit}
    /></label
  >
  <label
    >Queue states (select one or more)<select multiple bind:value={states}
      ><option value="new">New</option><option value="queued">Queued</option
      ><option value="later">Later</option><option value="skipped"
        >Skipped</option
      ><option value="not_eligible">Not eligible</option><option value="applied"
        >Applied</option
      ></select
    ></label
  >
  <label
    >Tiers (select one or more)<select multiple bind:value={tiers}
      ><option value="A">A</option><option value="B">B</option><option value="C"
        >C</option
      ></select
    ></label
  >
  <div class="row">
    <button bind:this={cancel} class="button" onclick={() => dialog.close()}
      >Cancel</button
    ><button
      class="button primary"
      onclick={collect}
      disabled={busy ||
        !states.length ||
        !tiers.length ||
        !Number.isInteger(collectLimit) ||
        collectLimit < 1 ||
        collectLimit > 1000}>Collect questions</button
    >
  </div>
  {#if error}<p class="error-banner" role="alert">{error}</p>{/if}
</dialog>

<style>
  .questions {
    margin-top: 1rem;
  }
  .row {
    display: flex;
    flex-wrap: wrap;
    gap: 0.6rem;
    align-items: center;
  }
  h3 {
    margin-right: auto;
  }
  label {
    display: grid;
    gap: 0.35rem;
    margin: 0.65rem 0;
  }
  textarea,
  select,
  input {
    color: var(--ink);
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 0.4rem;
    padding: 0.6rem;
    max-width: 100%;
  }
  textarea {
    width: 100%;
    box-sizing: border-box;
  }
  li {
    padding: 0.7rem 0;
    border-bottom: 1px solid var(--border);
  }
  ul {
    padding-left: 1.2rem;
  }
  dialog {
    color: var(--ink);
    background: var(--surface-raised);
    border: 1px solid var(--border);
    border-radius: 0.8rem;
    max-width: 34rem;
    width: calc(100% - 3rem);
  }
  dialog::backdrop {
    background: #0008;
  }
</style>
