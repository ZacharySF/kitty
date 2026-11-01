<script lang="ts">
  import { Button } from "@nunera/bracket";
  import { minutesLabel, type Session, type Workspace } from "./types";

  /** Completed study records in the selected period, with manual add / edit / delete. */
  let { data, period, label, onopen, ondelete }: {
    data: Workspace;
    period: "week" | "month";
    label: string;
    onopen: (session?: Session) => void;
    ondelete: (session: Session) => void;
  } = $props();

  const subjectName = (id: number | null | undefined) => data.subjects.find((item) => item.id === id)?.name || "Unassigned";
</script>

<div class="row"><h3>Study records · {label}</h3><Button onclick={() => onopen()}>Add manual study</Button></div>
<p class="muted">
  Completed focus and explicit manual study only. Records use actual study time in the selected {period}, including sessions crossing midnight. Edit or delete a record to correct your history.
</p>
{#each data.sessions as session (session.id)}
  <article class="panel">
    <div class="row">
      <strong>{minutesLabel(session.duration_seconds)} · {subjectName(session.subject_id)}</strong>
      <span>{new Date(session.started_at).toLocaleString(undefined, { timeZone: session.timezone })} ({session.timezone}) · {session.source}</span>
      <Button onclick={() => onopen(session)}>Edit</Button><Button onclick={() => ondelete(session)}>Delete</Button>
      {#if session.period_seconds !== session.duration_seconds}<span>{minutesLabel(session.period_seconds)} in this {period}</span>{/if}
    </div>
    {#if session.notes}<p class="preserve">{session.notes}</p>{/if}
    {#if session.distractions}<p class="muted preserve">Distractions: {session.distractions}</p>{/if}
  </article>
{:else}
  <p>No study records in this {period}. Browse an earlier period or complete your first focus session.</p>
{/each}
