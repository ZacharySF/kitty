<script lang="ts">
  import { Button } from "@nunera/bracket";
  import type { Collection, Item, Workspace } from "./types";

  /** Add / edit / delete form for subjects, concepts, goals, tasks and exams. */
  let { kind, item, data, disabled, change, ondelete, onclose }: {
    kind: Collection;
    /** The item to edit, or defaults for a new one (id 0). */
    item: Item;
    data: Workspace;
    disabled: boolean;
    change: (path: string, body: unknown) => Promise<boolean>;
    ondelete: (kind: Collection, item: Item) => Promise<boolean>;
    onclose: () => void;
  } = $props();

  // A snapshot of the item: edits stay local until saved.
  // svelte-ignore state_referenced_locally
  let editor = $state<Item>(structuredClone($state.snapshot(item)));
  let checklistText = $state("");

  async function save(event: SubmitEvent) {
    event.preventDefault();
    const identity = editor.id ? { id: editor.id, revision: editor.revision } : {};
    const common = { subject_id: editor.subject_id || null };
    const fields = kind === "subjects" ? { name: editor.name, color: editor.color }
      : kind === "projects" ? { name: editor.name, ...common }
      : kind === "targets" ? { title: editor.title, ...common, project_id: editor.project_id || null, period: editor.period, minutes: Number(editor.minutes) }
      : kind === "tasks" ? { title: editor.title, ...common, project_id: editor.project_id || null, priority: editor.priority, due_date: editor.due_date || null, done: editor.done || false, checklist: editor.checklist || [] }
      : { title: editor.title, ...common, exam_date: editor.exam_date, timezone: editor.timezone, notes: editor.notes || "" };
    if (await change(`/study/${kind}`, { action: "save", ...identity, ...fields })) onclose();
  }
  function addChecklist() {
    if (!checklistText.trim()) return;
    editor.checklist = [...(editor.checklist || []), { id: crypto.randomUUID(), text: checklistText.trim(), done: false }];
    checklistText = "";
  }
</script>

<div class="editor panel" role="region" aria-label={`Edit ${kind}`}>
  <form onsubmit={save}>
    <h3>{editor.id ? "Edit" : "Add"} {kind}</h3>
    <div class="form-grid">
      {#if kind === "subjects" || kind === "projects"}<label>Name<input required maxlength="250" bind:value={editor.name} /></label>
      {:else}<label>Title<input required maxlength="500" bind:value={editor.title} /></label>{/if}
      {#if kind === "subjects"}<label>Color<input type="color" bind:value={editor.color} /></label>
      {:else}<label>Subject<select bind:value={editor.subject_id}><option value={null}>Unassigned</option>{#each data.subjects as entry (entry.id)}<option value={entry.id}>{entry.name}</option>{/each}</select></label>{/if}
      {#if kind === "tasks" || kind === "targets"}<label>Project<select bind:value={editor.project_id}><option value={null}>None</option>{#each data.projects as entry (entry.id)}<option value={entry.id}>{entry.name}</option>{/each}</select></label>{/if}
      {#if kind === "targets"}
        <label>Period<select bind:value={editor.period}><option value="daily">Daily</option><option value="weekly">Weekly</option></select></label>
        <label>Minutes<input type="number" min="1" max="10080" required bind:value={editor.minutes} /></label>
      {/if}
      {#if kind === "tasks"}
        <label>Priority<select bind:value={editor.priority}><option value="low">Low</option><option value="normal">Normal</option><option value="high">High</option></select></label>
        <label>Due date<input type="date" bind:value={editor.due_date} /></label>
      {/if}
      {#if kind === "exams"}
        <label>Exam date<input type="date" required bind:value={editor.exam_date} /></label>
        <label>IANA timezone<input required bind:value={editor.timezone} /></label>
        <label>Notes<textarea bind:value={editor.notes}></textarea></label>
      {/if}
    </div>
    {#if kind === "tasks"}
      <h4>Checklist</h4>
      {#each editor.checklist || [] as entry (entry.id)}
        <div class="row">
          <label class="check-row"><input type="checkbox" bind:checked={entry.done} />{entry.text}</label>
          <Button onclick={() => (editor.checklist = editor.checklist?.filter((other) => other.id !== entry.id))}>Remove</Button>
        </div>
      {/each}
      <div class="row">
        <label class="field">New checklist item<input bind:value={checklistText} maxlength="500" /></label>
        <Button onclick={addChecklist}>Add item</Button>
      </div>
    {/if}
    <div class="row">
      <Button type="submit" {disabled}>Save</Button>
      <Button onclick={onclose}>Cancel</Button>
      {#if editor.id}<Button intent="danger" {disabled} onclick={async () => { if (await ondelete(kind, item)) onclose(); }}>Delete</Button>{/if}
    </div>
  </form>
</div>
