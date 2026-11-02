<script lang="ts">
  import { Button } from "@nunera/bracket";
  import EditDrawer from "../ui/EditDrawer.svelte";
  import Select from "../ui/Select.svelte";
  import type { Collection, Item, Workspace } from "./types";

  /** The side drawer that edits (or completes a quick-added) subject, concept, goal, task or exam. */
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

  const NOUN: Record<Collection, string> = { subjects: "subject", projects: "concept", targets: "goal", tasks: "task", exams: "exam" };
  const formId = $props.id();
  // A snapshot of the item: edits stay local until saved.
  // svelte-ignore state_referenced_locally
  let editor = $state<Item>(structuredClone($state.snapshot(item)));
  let checklistText = $state("");
  const subject = $derived(data.subjects.find((entry) => entry.id === editor.subject_id));
  /** Select works in strings; the item keeps numeric ids or null. */
  const idText = (id: number | null | undefined) => (id == null ? "" : String(id));
  const idValue = (text: string) => (text ? Number(text) : null);
  const subjects = $derived([{ value: "", label: "Unassigned" }, ...data.subjects.map((entry) => ({ value: String(entry.id), label: entry.name ?? "", color: entry.color }))]);
  const projects = $derived([{ value: "", label: "None" }, ...data.projects.map((entry) => ({ value: String(entry.id), label: entry.name ?? "" }))]);

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

<EditDrawer title={`${editor.id ? "Edit" : "New"} ${NOUN[kind]}`} meta={kind === "subjects" ? "" : subject?.name ?? ""}
  accent={kind === "subjects" ? editor.color : subject?.color ?? ""} closeLabel={`Close ${NOUN[kind]} editor`} {onclose}>
  <form id={formId} class="fields" onsubmit={save}>
    {#if kind === "subjects" || kind === "projects"}<label class="field">Name<input required maxlength="250" bind:value={editor.name} /></label>
    {:else}<label class="field">Title<input required maxlength="500" bind:value={editor.title} /></label>{/if}
    {#if kind === "subjects"}<label class="field">Colour<input type="color" bind:value={editor.color} /></label>
    {:else}<div class="field"><span>Subject</span><Select kind="list" label="Subject" options={subjects} value={idText(editor.subject_id)} onchange={(next) => (editor.subject_id = idValue(next))} /></div>{/if}
    {#if kind === "tasks" || kind === "targets"}<div class="field"><span>Concept</span><Select kind="list" label="Concept" options={projects} value={idText(editor.project_id)} onchange={(next) => (editor.project_id = idValue(next))} /></div>{/if}
    {#if kind === "targets"}
      <div class="field"><span>Period</span><Select label="Period" options={[{ value: "daily", label: "Daily" }, { value: "weekly", label: "Weekly" }]} bind:value={editor.period as string} /></div>
      <label class="field">Minutes<input type="number" min="1" max="10080" required bind:value={editor.minutes} /></label>
    {/if}
    {#if kind === "tasks"}
      <div class="field"><span>Priority</span><Select label="Priority" options={[{ value: "low", label: "Low" }, { value: "normal", label: "Normal" }, { value: "high", label: "High" }]} bind:value={editor.priority as string} /></div>
      <label class="field">Due date<input type="date" bind:value={editor.due_date} /></label>
    {/if}
    {#if kind === "exams"}
      <label class="field">Exam date<input type="date" required bind:value={editor.exam_date} /></label>
      <div class="field"><span>Timezone</span><Select kind="timezone" label="Timezone" bind:value={editor.timezone as string} /></div>
      <label class="field">Notes<textarea rows="3" bind:value={editor.notes}></textarea></label>
    {/if}
    {#if kind === "tasks"}
      <div class="field">
        <span>Checklist</span>
        {#each editor.checklist || [] as entry (entry.id)}
          <div class="check">
            <label class="check-row"><input type="checkbox" bind:checked={entry.done} />{entry.text}</label>
            <button type="button" class="icon-button" aria-label={`Remove ${entry.text}`} onclick={() => (editor.checklist = editor.checklist?.filter((other) => other.id !== entry.id))}>×</button>
          </div>
        {/each}
        <div class="check">
          <input aria-label="New checklist item" placeholder="New item" bind:value={checklistText} maxlength="500"
            onkeydown={(event) => { if (event.key === "Enter") { event.preventDefault(); addChecklist(); } }} />
          <Button size="sm" onclick={addChecklist}>Add</Button>
        </div>
      </div>
    {/if}
  </form>
  {#snippet footer()}
    {#if editor.id}<Button intent="danger" variant="ghost" {disabled} onclick={async () => { if (await ondelete(kind, item)) onclose(); }}>Delete</Button><span class="grow"></span>{/if}
    <Button onclick={onclose}>Cancel</Button>
    <Button type="submit" form={formId} variant="solid" intent="primary" {disabled}>Save</Button>
  {/snippet}
</EditDrawer>

<style>
  .fields {
    display: grid;
    gap: 12px;
  }
  .field {
    display: grid;
    gap: 6px;
    color: var(--muted);
    font-size: 12px;
  }
  .field :is(input:not([type="checkbox"]), textarea) {
    width: 100%;
    box-sizing: border-box;
  }
  .check {
    display: flex;
    align-items: center;
    gap: 6px;
  }
  .check-row {
    flex: 1;
    display: flex;
    align-items: center;
    gap: 8px;
    color: var(--ink);
  }
  .grow {
    flex: 1;
  }
</style>
