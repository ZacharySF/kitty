<script lang="ts">
  import EditDrawer from "../ui/EditDrawer.svelte";
  import Select from "../ui/Select.svelte";
  import type { Session, Workspace } from "./types";

  /** Log or correct a completed study session. */
  let { session, data, subjectId, projectId, disabled, change, onclose }: {
    session: Session | null;
    data: Workspace;
    /** Defaults for a new record: the session focus picked in the cockpit. */
    subjectId: string;
    projectId: string;
    disabled: boolean;
    change: (path: string, body: unknown) => Promise<boolean>;
    onclose: () => void;
  } = $props();

  // Edits stay local until saved: the form starts from a snapshot of the props.
  // svelte-ignore state_referenced_locally
  const initial = session, defaults = { zone: data.settings.timezone, subjectId, projectId };
  let start = $state(initial?.started_at || new Date(Date.now() - 25 * 60000).toISOString());
  let zone = $state(initial?.timezone || defaults.zone || "UTC");
  let minutes = $state(initial ? initial.duration_seconds / 60 : 25);
  let subject = $state(initial ? initial.subject_id?.toString() || "" : defaults.subjectId);
  let project = $state(initial ? initial.project_id?.toString() || "" : defaults.projectId);
  let notes = $state(initial?.notes || "");
  let distractions = $state(initial?.distractions || "");
  const formId = $props.id();

  async function save(event: SubmitEvent) {
    event.preventDefault();
    if (await change("/study/sessions", {
      action: "save",
      request_id: crypto.randomUUID(),
      ...(initial ? { id: initial.id, revision: initial.revision } : {}),
      started_at: start,
      timezone: zone,
      duration_seconds: Math.round(minutes * 60),
      subject_id: subject ? Number(subject) : null,
      project_id: project ? Number(project) : null,
      notes,
      distractions,
    })) onclose();
  }
</script>

<EditDrawer title={initial ? "Edit session" : "Log study"} closeLabel="Close session editor" {onclose}
  accent={data.subjects.find((item) => String(item.id) === subject)?.color ?? ""}>
  <form id={formId} class="fields" onsubmit={save}>
    <label class="field">Started<input required bind:value={start} placeholder="2026-10-14T09:00:00-04:00" /></label>
    <div class="field"><span>Timezone</span><Select kind="timezone" label="Timezone" bind:value={zone} /></div>
    <label class="field">Minutes studied<input type="number" min="0.016667" max="10080" step="any" required bind:value={minutes} /></label>
    <div class="field"><span>Subject</span><Select kind="list" label="Subject" options={[{ value: "", label: "Unassigned" }, ...data.subjects.map((item) => ({ value: String(item.id), label: item.name ?? "", color: item.color }))]} bind:value={subject} /></div>
    <div class="field"><span>Concept</span><Select kind="list" label="Concept" options={[{ value: "", label: "None" }, ...data.projects.map((item) => ({ value: String(item.id), label: item.name ?? "" }))]} bind:value={project} /></div>
    <label class="field">Notes<textarea rows="3" bind:value={notes}></textarea></label>
    <label class="field">Distractions<textarea rows="2" bind:value={distractions}></textarea></label>
  </form>
  {#snippet footer()}
    <button type="button" class="pill" onclick={onclose}>Cancel</button>
    <button type="submit" class="pill primary" form={formId} {disabled}>Save</button>
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
  .field :is(input, textarea) {
    width: 100%;
    box-sizing: border-box;
  }
</style>
