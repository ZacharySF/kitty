<script lang="ts">
  import HomeWidget from "./HomeWidget.svelte";
  import { listGoals } from "./goals/api";
  import type { Goal } from "./goals/goals";
  import type { View } from "./workspace";

  /**
   * Goals on home: the active ones (three at most) as nested gauges, open at the foot (the
   * first goal outermost), each filled as far as the goal is along, beside a list that names
   * them with the percentage and the days left. Each goal keeps one tile colour in both.
   */
  let { id, revision, onview }: { id: string; revision: number; onview: (view: View) => unknown } = $props();
  let goals = $state<Goal[] | null>(null);
  let error = $state("");
  $effect(() => {
    void revision;
    listGoals()
      .then((all) => { goals = all.filter((goal) => goal.status === "active").slice(0, 3); error = ""; })
      .catch((cause) => (error = (cause as Error).message));
  });
  const left = (goal: Goal) => (goal.days_left === null ? "" : goal.days_left < 0 ? "past due" : goal.days_left === 0 ? "today" : `${goal.days_left}d left`);
  const percent = (goal: Goal) => Math.min(100, Math.round(goal.progress * 100));
  /** Gauge `index`'s radius in a 120-unit box: 10 wide, 4 apart. */
  const radius = (index: number) => 53 - index * 14;
  /** A gauge sweeps three quarters of its circle. */
  const SWEEP = 75;
</script>

<HomeWidget {id} title="Goals" icon="goals" hero tint="var(--tile-1)" opens="goals" onopen={() => onview("goals")} art={goals?.length ? gauges : undefined}>
  {#if error}<p class="note" role="alert">{error}</p>
  {:else if goals && !goals.length}<p class="note">No goals yet.</p>
  {:else if goals}
    <ul>
      {#each goals as goal, index (goal.id)}
        <li style:--shade={`var(--tile-${index + 1})`}>
          <i aria-hidden="true"></i>
          <span class="title">{goal.title}</span>
          <span class="meta"><b>{percent(goal)}%</b>{#if left(goal)}<span>{left(goal)}</span>{/if}</span>
        </li>
      {/each}
    </ul>
  {/if}
</HomeWidget>

{#snippet gauges()}
  <svg class="gauges" viewBox="0 0 120 120" aria-hidden="true">
    {#each goals ?? [] as goal, index (goal.id)}
      <!-- The gap sits at the foot: each gauge starts lower left and ends lower right. -->
      <g style:--shade={`var(--tile-${index + 1})`} transform="rotate(135 60 60)">
        <circle class="track" cx="60" cy="60" r={radius(index)} pathLength="100" stroke-dasharray={`${SWEEP} 100`} />
        {#if percent(goal)}<circle class="value" cx="60" cy="60" r={radius(index)} pathLength="100" stroke-dasharray={`${(percent(goal) / 100) * SWEEP} 100`} />{/if}
      </g>
    {/each}
  </svg>
{/snippet}

<style>
  ul {
    display: grid;
    gap: 10px;
    margin: 0;
    padding: 0;
    list-style: none;
  }
  li {
    display: grid;
    grid-template-columns: 8px minmax(0, 1fr);
    align-items: baseline;
    gap: 0 10px;
    font-size: 15px;
    line-height: 1.3;
  }
  li i {
    align-self: center;
    width: 8px;
    height: 8px;
    border-radius: var(--r-control);
    background: var(--shade);
  }
  .title {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .meta {
    grid-column: 2;
    display: flex;
    gap: 8px;
    color: var(--muted);
    font-size: 13px;
    font-variant-numeric: tabular-nums;
  }
  .meta b {
    color: var(--ink);
    font-weight: 600;
  }
  .note {
    margin: 0;
    color: var(--muted);
    font-size: 13px;
  }
  .gauges {
    width: 152px;
    height: 152px;
    fill: none;
    stroke-width: 10;
    stroke-linecap: round;
  }
  .gauges g {
    stroke: var(--shade);
  }
  .gauges .track {
    opacity: 0.2;
  }
</style>
