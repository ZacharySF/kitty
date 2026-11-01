<script lang="ts">
  import { Button } from "@nunera/bracket";
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

<div class="editor panel" role="region" aria-label="Edit completed session">
  <form onsubmit={save}>
    <h3>{initial ? "Edit session" : "Log completed session"}</h3>
    <div class="form-grid">
      <label>Start timestamp with explicit offset<input required bind:value={start} placeholder="2026-10-14T09:00:00-04:00" /></label>
      <label>IANA timezone<input required bind:value={zone} /></label>
      <label>Duration in minutes<input type="number" min="0.016667" max="10080" step="any" required bind:value={minutes} /></label>
      <label>Subject<select bind:value={subject}><option value="">Unassigned</option>{#each data.subjects as item (item.id)}<option value={item.id.toString()}>{item.name}</option>{/each}</select></label>
      <label>Project<select bind:value={project}><option value="">None</option>{#each data.projects as item (item.id)}<option value={item.id.toString()}>{item.name}</option>{/each}</select></label>
      <label>Notes<textarea bind:value={notes}></textarea></label>
      <label>Distractions<textarea bind:value={distractions}></textarea></label>
    </div>
    <p class="muted">
      Duration means time actually studied, not time away. Editing the start/duration of a paused focus replaces its interval distribution with the manual contiguous interval you enter.
    </p>
    <div class="row">
      <Button type="submit" {disabled}>Save completed session</Button><Button onclick={onclose}>Cancel</Button>
    </div>
  </form>
</div>
