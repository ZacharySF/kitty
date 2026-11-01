<script lang="ts">
  import type { Item } from "./types";

  /** Study tasks with subject dots: open first by priority, checklists inline. */
  let { tasks, subjects, limit = Infinity, disabled = false, ontoggle, onchecklist, onedit }: {
    tasks: Item[];
    subjects: Item[];
    limit?: number;
    disabled?: boolean;
    ontoggle: (task: Item) => void;
    onchecklist?: (task: Item, checklistId: string) => void;
    onedit?: (task: Item) => void;
  } = $props();

  const rank = { high: 0, normal: 1, low: 2 } as Record<string, number>;
  const sorted = $derived([...tasks]
    .sort((a, b) => Number(a.done) - Number(b.done) || (rank[a.priority || "normal"] ?? 1) - (rank[b.priority || "normal"] ?? 1))
    .slice(0, limit));
  const color = (id: number | null | undefined) => subjects.find((item) => item.id === id)?.color || "var(--subtle)";
</script>

<ul class="task-list">
  {#each sorted as task (task.id)}
    <li class:done={task.done}>
      <label class="task">
        <input type="checkbox" checked={task.done} {disabled} onchange={() => ontoggle(task)} />
        <span class="dot" style:background={color(task.subject_id)} aria-hidden="true"></span>
        <span class="title">{task.title}</span>
        {#if task.priority === "high" && !task.done}<span class="flag">high</span>{/if}
        {#if task.due_date && !task.done}<span class="due">{task.due_date.slice(5)}</span>{/if}
      </label>
      {#if onedit}<button type="button" class="mini" aria-label={`Edit task ${task.title}`} onclick={() => onedit(task)}>✎</button>{/if}
      {#if onchecklist && task.checklist?.length && !task.done}
        <ul class="checklist">
          {#each task.checklist as entry (entry.id)}
            <li><label class:done={entry.done}><input type="checkbox" checked={entry.done} {disabled} onchange={() => onchecklist(task, entry.id)} />{entry.text}</label></li>
          {/each}
        </ul>
      {/if}
    </li>
  {/each}
</ul>

<style>
  ul {
    display: grid;
    gap: 1px;
    margin: 0;
    padding: 0;
    list-style: none;
  }
  .task-list > li {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    align-items: center;
    border-radius: 8px;
  }
  .task-list > li:hover {
    background: color-mix(in srgb, var(--ink) 5%, transparent);
  }
  label {
    display: flex;
    align-items: center;
    gap: 8px;
    min-width: 0;
    padding: 5px 6px;
    color: var(--ink);
    font-size: 12.5px;
    cursor: pointer;
  }
  input {
    flex: none;
    width: auto;
    margin: 0;
    accent-color: var(--accent);
  }
  .title {
    display: -webkit-box;
    overflow: hidden;
    -webkit-box-orient: vertical;
    -webkit-line-clamp: 2;
    line-clamp: 2;
    line-height: 1.3;
  }
  .flag,
  .due {
    flex: none;
    margin-left: auto;
    color: var(--muted);
    font: 500 10px var(--font-mono);
  }
  .flag {
    color: var(--warning);
  }
  .flag + .due {
    margin-left: 0;
  }
  .done .title,
  label.done {
    color: var(--subtle);
    text-decoration: line-through;
  }
  .checklist {
    grid-column: 1 / -1;
    padding: 0 0 4px 24px;
  }
  .checklist label {
    padding: 2px 6px;
    color: var(--muted);
    font-size: 12px;
  }
  .mini {
    width: 22px;
    height: 22px;
    margin-right: 2px;
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
</style>
