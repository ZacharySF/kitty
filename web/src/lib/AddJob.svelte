<script lang="ts">
  import { onMount } from "svelte";
  import { api, type Bootstrap, type Resume, type TaskRecord } from "./api";
  import TaskStatus from "./TaskStatus.svelte";
  let {
    workspace,
    onclose,
    onsaved,
  }: {
    workspace: Bootstrap | null;
    onclose: () => void;
    onsaved: (id: number) => void;
  } = $props();
  let dialog: HTMLDialogElement;
  let urlField = $state<HTMLInputElement>();
  let url = $state("");
  let company = $state("");
  let title = $state("");
  let location = $state("");
  let track = $state("");
  let tier = $state("");
  let deadline = $state("");
  let description = $state("");
  let submitted = $state(false);
  let resume = $state("");
  let resumes = $state<Resume[]>([]);
  let busy = $state(false);
  let error = $state("");
  let task = $state<TaskRecord | null>(null);
  onMount(() => {
    dialog.showModal();
    urlField?.focus();
    void api<{ resumes: Resume[] }>("/resumes")
      .then((result) => (resumes = result.resumes))
      .catch((cause) => (error = cause.message));
  });
  async function preview() {
    error = "";
    try {
      const result = await api<{ task: TaskRecord }>("/jobs/metadata", {
        url,
        confirmed: true,
      });
      task = result.task;
    } catch (cause) {
      error = (cause as Error).message;
    }
  }
  async function save(event: SubmitEvent) {
    event.preventDefault();
    busy = true;
    error = "";
    try {
      const result = await api<{ id: number }>("/jobs/add", {
        url,
        company,
        title,
        location,
        track,
        tier: tier || null,
        deadline,
        description,
        submitted,
        resume_id: resume ? Number(resume) : null,
      });
      onsaved(result.id);
      onclose();
    } catch (cause) {
      error = (cause as Error).message;
    } finally {
      busy = false;
    }
  }
</script>

<dialog
  bind:this={dialog}
  class="workspace-dialog"
  oncancel={onclose}
  onkeydown={(event) => {
    if ((event.ctrlKey || event.metaKey) && event.key === "f") {
      event.preventDefault();
      if (!workspace?.demo) void preview();
    }
  }}
>
  <header>
    <div>
      <p class="eyebrow">ADD TO YOUR WORKSPACE</p>
      <h2>Add an opportunity</h2>
    </div>
    <button
      class="icon-button"
      aria-label="Close add opportunity"
      onclick={onclose}>×</button
    >
  </header>
  <form onsubmit={save}>
    <label
      >Posting URL<input
        bind:this={urlField}
        type="url"
        required
        bind:value={url}
        placeholder="https://employer.example/jobs/…"
      /></label
    >
    <div class="metadata-action">
      <button
        class="button"
        type="button"
        disabled={!url || workspace?.demo || task?.status === "running"}
        onclick={preview}>Fetch page title <kbd>Ctrl F</kbd></button
      ><small class="muted"
        >Optional public request. Guesses fill only blank fields; review before
        saving.</small
      >
    </div>
    <TaskStatus
      {task}
      oncomplete={(result) => {
        task = result;
        if (result.status === "completed") {
          const meta = result.result as {
            company_guess?: string;
            title_guess?: string;
          };
          if (!company) company = meta.company_guess || "";
          if (!title) title = meta.title_guess || "";
        }
      }}
    />
    <div class="form-grid">
      <label
        >Company<input required bind:value={company} maxlength="250" /></label
      ><label
        >Job title<input required bind:value={title} maxlength="500" /></label
      ><label>Location<input bind:value={location} maxlength="500" /></label
      ><label
        >Track<select bind:value={track}
          ><option value="">Unassigned</option
          >{#each workspace?.tracks || [] as item}<option value={item.name}
              >{item.label}</option
            >{/each}</select
        ></label
      ><label
        >Tier<select bind:value={tier}
          ><option value="">Unranked</option><option>A</option><option>B</option
          ><option>C</option></select
        ></label
      ><label
        >Deadline<input
          bind:value={deadline}
          placeholder="YYYY-MM-DD or +14"
        /></label
      >
    </div>
    <label
      >Description<textarea rows="4" maxlength="20000" bind:value={description}
      ></textarea></label
    ><label
      >Resume<select bind:value={resume}
        ><option value="">Use track default</option
        >{#each resumes as item}<option
            value={String(item.id)}
            disabled={!item.available}
            >{item.label}{item.available ? "" : " — PDF unavailable"}</option
          >{/each}</select
      ></label
    ><label class="check"
      ><input type="checkbox" bind:checked={submitted} />I already submitted
      this application myself; record it and schedule follow-ups.</label
    >
    {#if error}<p class="error-banner" role="alert">{error}</p>{/if}
    <footer>
      <button class="button" type="button" onclick={onclose}>Cancel</button
      ><button class="button primary" disabled={busy}
        >{busy ? "Saving…" : "Save opportunity"}</button
      >
    </footer>
  </form>
</dialog>

<style>
  header {
    display: flex;
    justify-content: space-between;
    gap: 16px;
  }
  h2 {
    font-size: 24px;
    margin: 10px 0 22px;
  }
  .form-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 14px;
  }
  form {
    display: grid;
    gap: 16px;
  }
  label {
    display: grid;
    gap: 8px;
    color: var(--muted);
    font-size: 12px;
  }
  input,
  select,
  textarea {
    width: 100%;
    min-width: 0;
  }
  .check {
    display: flex;
    align-items: start;
    line-height: 1.7;
  }
  .check input {
    width: auto;
    margin-top: 4px;
  }
  .metadata-action {
    display: flex;
    align-items: center;
    gap: 12px;
  }
  .metadata-action small {
    font-size: 11px;
    line-height: 1.6;
  }
  footer {
    display: flex;
    justify-content: end;
    gap: 10px;
    margin-top: 10px;
  }
  @media (max-width: 550px) {
    .form-grid {
      grid-template-columns: 1fr;
    }
    .metadata-action {
      align-items: start;
      flex-direction: column;
    }
  }
</style>
