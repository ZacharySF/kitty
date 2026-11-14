<script lang="ts">
  import HomeWidget from "./HomeWidget.svelte";
  import { listGoals } from "./goals/api";
  import type { Goal } from "./goals/goals";
  import type { View } from "./workspace";

  /** Goals on home: the active ones, each with how far along it is and the days left. */
  let { id, revision, onview }: { id: string; revision: number; onview: (view: View) => unknown } = $props();
  let goals = $state<Goal[] | null>(null);
  let error = $state("");
  $effect(() => {
    void revision;
    listGoals()
      .then((all) => { goals = all.filter((goal) => goal.status === "active").slice(0, 4); error = ""; })
      .catch((cause) => (error = (cause as Error).message));
  });
  const left = (goal: Goal) => (goal.days_left === null ? "" : goal.days_left < 0 ? "past due" : goal.days_left === 0 ? "today" : `${goal.days_left}d left`);
</script>

<HomeWidget {id} title="Goals" icon="goals">
  {#if error}<p class="note" role="alert">{error}</p>
  {:else if goals && !goals.length}<p class="note">No goals yet. <button type="button" class="text-button" onclick={() => onview("goals")}>Add one</button></p>
  {:else if goals}
    <ul>
      {#each goals as goal (goal.id)}
        <li>
          <button type="button" onclick={() => onview("goals")}>
            <span class="title">{goal.title}</span>
            <span class="meta">{Math.round(goal.progress * 100)}%{left(goal) ? ` · ${left(goal)}` : ""}</span>
            <span class="bar" aria-hidden="true"><span style:width={`${Math.min(100, Math.round(goal.progress * 100))}%`}></span></span>
          </button>
        </li>
      {/each}
    </ul>
  {/if}
</HomeWidget>

<style>
  ul {
    display: grid;
    margin: 0;
    padding: 0;
    list-style: none;
  }
  button:not(.text-button) {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    gap: 6px 12px;
    width: 100%;
    padding: 9px 0;
    border: 0;
    background: none;
    color: inherit;
    font: inherit;
    text-align: left;
    cursor: pointer;
  }
  .title {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-size: 17px;
  }
  .meta,
  .note {
    color: var(--muted);
    font-size: 14px;
    font-variant-numeric: tabular-nums;
  }
  .note {
    margin: 8px 0 0;
  }
  .bar {
    grid-column: 1 / -1;
    height: 2px;
    background: var(--hairline);
  }
  .bar span {
    display: block;
    height: 100%;
    background: var(--ink);
  }
</style>
