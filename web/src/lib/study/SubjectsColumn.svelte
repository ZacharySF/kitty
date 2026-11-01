<script lang="ts">
  import TaskList from "./TaskList.svelte";
  import { minutesLabel, type Collection, type Item, type Workspace } from "./types";

  /**
   * Cockpit left column: what to study. Subjects (with this week's minutes in
   * their colour) and concepts pick the session focus; tasks sit below.
   */
  let { data, subjectId = $bindable(), projectId = $bindable(), locked, disabled, onedit, ontoggle, onchecklist }: {
    data: Workspace;
    subjectId: string;
    projectId: string;
    /** A session is running or paused, so its focus cannot change. */
    locked: boolean;
    disabled: boolean;
    onedit: (kind: Collection, item?: Item, defaults?: Partial<Item>) => void;
    ontoggle: (task: Item) => void;
    onchecklist: (task: Item, checklistId: string) => void;
  } = $props();

  /** Seconds per subject since Monday, from the per-day subject split. */
  const week = $derived.by(() => {
    const totals = new Map<number | null, number>();
    for (const day of data.stats.days)
      if (day.date >= data.stats.week_start)
        for (const part of day.subjects) totals.set(part.id, (totals.get(part.id) || 0) + part.seconds);
    return totals;
  });
  const most = $derived(Math.max(1, ...data.subjects.map((item) => week.get(item.id) || 0)));
  const concepts = $derived(data.projects.filter((item) => !subjectId || item.subject_id === Number(subjectId) || item.subject_id == null));
  const open = $derived(data.tasks.filter((item) => !item.done).length);

  function pick(id: number) {
    subjectId = subjectId === String(id) ? "" : String(id);
    projectId = "";
  }
</script>

<section class="column subjects-column" aria-label="Subjects & tasks">
  <header>
    <span class="eyebrow">Subjects · this week</span>
    <button type="button" class="add" onclick={() => onedit("subjects")}>Add subject</button>
  </header>
  {#if data.subjects.length}
    <ul class="subjects">
      {#each data.subjects as subject (subject.id)}
        {@const seconds = week.get(subject.id) || 0}
        <li>
          <button type="button" class="subject" aria-pressed={subjectId === String(subject.id)} disabled={locked} onclick={() => pick(subject.id)}>
            <span class="dot" style:background={subject.color} aria-hidden="true"></span>
            <span class="name">{subject.name}</span>
            <span class="minutes">{minutesLabel(seconds)}</span>
            <span class="meter" aria-hidden="true"><i style:width={`${(seconds / most) * 100}%`} style:background={subject.color}></i></span>
          </button>
          <button type="button" class="mini" aria-label={`Edit subject ${subject.name}`} onclick={() => onedit("subjects", subject)}>✎</button>
        </li>
      {/each}
    </ul>
  {:else}
    <p class="hint">Add a subject to colour your sessions, progress and plans.</p>
  {/if}
  <header>
    <span class="eyebrow">Concepts</span>
    <button type="button" class="add" onclick={() => onedit("projects", undefined, { subject_id: subjectId ? Number(subjectId) : null })}>Add concept</button>
  </header>
  {#if concepts.length}
    <ul class="concepts">
      {#each concepts as concept (concept.id)}
        <li>
          <button type="button" class="concept" aria-pressed={projectId === String(concept.id)} disabled={locked} onclick={() => (projectId = projectId === String(concept.id) ? "" : String(concept.id))}>{concept.name}</button>
          <button type="button" class="mini" aria-label={`Edit concept ${concept.name}`} onclick={() => onedit("projects", concept)}>✎</button>
        </li>
      {/each}
    </ul>
  {:else}
    <p class="hint">No concepts{subjectId ? " for this subject" : ""} yet.</p>
  {/if}
  <hr />
  <header>
    <span class="eyebrow">Tasks · {open} open</span>
    <button type="button" class="add" onclick={() => onedit("tasks", undefined, { subject_id: subjectId ? Number(subjectId) : null })}>Add task</button>
  </header>
  {#if data.tasks.length}
    <TaskList tasks={data.tasks} subjects={data.subjects} {disabled} {ontoggle} {onchecklist} onedit={(task) => onedit("tasks", task)} />
  {:else}
    <p class="hint">Make the next step small and specific.</p>
  {/if}
</section>

<style>
  .subjects-column {
    display: grid;
    align-content: start;
    gap: 8px;
  }
  header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
  }
  .add {
    padding: 2px 8px;
    border: 1px solid var(--hairline);
    border-radius: 999px;
    background: transparent;
    color: var(--muted);
    font: 500 11px var(--font-ui);
  }
  .add:hover {
    border-color: var(--border);
    color: var(--ink);
  }
  ul {
    display: grid;
    gap: 2px;
    margin: 0;
    padding: 0;
    list-style: none;
  }
  li {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    align-items: center;
  }
  .subject {
    display: grid;
    grid-template-columns: auto minmax(0, 1fr) auto;
    align-items: center;
    gap: 3px 8px;
    width: 100%;
    padding: 6px 8px;
    border: 1px solid transparent;
    border-radius: 9px;
    background: transparent;
    color: var(--ink);
    font: 500 12.5px var(--font-ui);
    text-align: left;
  }
  .subject:hover:not(:disabled),
  .concept:hover:not(:disabled) {
    background: color-mix(in srgb, var(--ink) 5%, transparent);
  }
  .subject[aria-pressed="true"],
  .concept[aria-pressed="true"] {
    border-color: var(--hairline);
    background: var(--wash);
  }
  .subject:disabled,
  .concept:disabled {
    cursor: default;
    opacity: 0.75;
  }
  .subject[aria-pressed="true"]:disabled,
  .concept[aria-pressed="true"]:disabled {
    opacity: 1;
  }
  .dot {
    width: 8px;
    height: 8px;
    border-radius: 999px;
  }
  .name {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .minutes {
    color: var(--muted);
    font: 500 10.5px var(--font-mono);
  }
  .meter {
    grid-column: 2 / 4;
    height: 2px;
    border-radius: 2px;
    background: color-mix(in srgb, var(--ink) 8%, transparent);
  }
  .meter i {
    display: block;
    height: 100%;
    border-radius: inherit;
  }
  .concepts {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
  }
  .concepts li {
    display: flex;
  }
  .concept {
    max-width: 100%;
    overflow: hidden;
    padding: 3px 9px;
    border: 1px solid var(--hairline);
    border-radius: 999px;
    background: transparent;
    color: var(--ink);
    font-size: 12px;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .mini {
    width: 22px;
    height: 22px;
    padding: 0;
    border: 1px solid transparent;
    border-radius: 999px;
    background: transparent;
    color: var(--muted);
    font-size: 12px;
    opacity: 0;
  }
  li:hover .mini,
  .mini:focus-visible {
    opacity: 1;
  }
  .mini:hover {
    border-color: var(--hairline);
    color: var(--ink);
  }
  @media (hover: none) {
    .mini {
      opacity: 1;
    }
  }
  .concepts .mini {
    width: 18px;
  }
  .hint {
    margin: 0;
    color: var(--muted);
    font-size: 12px;
  }
  hr {
    width: 100%;
    margin: 4px 0;
    border: 0;
    border-top: 1px solid var(--hairline);
  }
</style>
