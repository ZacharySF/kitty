<script lang="ts">
  import { onMount } from "svelte";
  import EmptyState from "../EmptyState.svelte";
  import Icon from "../Icon.svelte";
  import Loader from "../Loader.svelte";
  import TimerRing from "../TimerRing.svelte";
  import { isView, type View } from "../workspace";
  import { useWorkspaceContext } from "../workspaceContext.svelte";
  import { listGoals, loadGoal } from "./api";
  import GoalDetail from "./GoalDetail.svelte";
  import GoalForm from "./GoalForm.svelte";
  import { daysLeftLabel, goalFromHash, goalHash, percent, statusLabel, type Goal, type Id, type LinkKind } from "./goals";

  /**
   * Goals (docs/rika-design.md Q14 A): first-class goals with a why, a target date and
   * measures, linked to the notes, tasks, jobs and study sessions that move them, while
   * Rika tracks progress and suggests what it finds. The list sits on the left; the chosen
   * goal's page fills the rest. `#goals/<id>` opens one goal.
   */
  let { revision = 0, onview, onopenjob }: { revision?: number; onview: (view: View) => unknown; onopenjob: (id: number) => void } = $props();
  const pane = useWorkspaceContext();

  let goals = $state<Goal[] | null>(null);
  let selected = $state<string | null>(goalFromHash(location.hash));
  let detail = $state<Goal | null>(null);
  let error = $state("");
  /** The create/edit drawer: `goal` null creates. */
  let editor = $state<{ goal: Goal | null } | null>(null);

  let listRequest = 0;
  async function loadList() {
    const serial = ++listRequest;
    try {
      const found = await listGoals();
      if (serial !== listRequest) return;
      goals = found;
      // A deep link to a goal that no longer exists opens the first one instead.
      if (!selected || !found.some((goal) => String(goal.id) === selected)) selected = found[0] ? String(found[0].id) : null;
    } catch (cause) {
      if (serial === listRequest) error = (cause as Error).message;
    }
  }

  let detailRequest = 0;
  async function loadDetail(id: string | null) {
    const serial = ++detailRequest;
    if (!id) {
      detail = null;
      return;
    }
    try {
      const goal = await loadGoal(id);
      if (serial === detailRequest) detail = goal;
    } catch (cause) {
      if (serial === detailRequest) error = (cause as Error).message;
    }
  }

  $effect(() => {
    void revision;
    void loadList();
  });
  $effect(() => {
    const id = selected;
    void loadDetail(id);
  });

  function choose(id: Id) {
    selected = String(id);
    if (pane.active) history.replaceState(history.state, "", goalHash(id));
  }
  /** The replaced goal answered by any change; the list keeps its summary in step. */
  function changed(goal: Goal) {
    detail = goal;
    const { links: _links, suggestions: _suggestions, ...summary } = goal;
    goals = goals?.some((item) => String(item.id) === String(goal.id))
      ? goals.map((item) => (String(item.id) === String(goal.id) ? summary : item))
      : [...(goals ?? []), summary];
  }
  function saved(goal: Goal) {
    editor = null;
    changed(goal);
    choose(goal.id);
    void loadList();
  }
  function deleted(id: Id) {
    editor = null;
    goals = goals?.filter((goal) => String(goal.id) !== String(id)) ?? null;
    detail = null;
    selected = goals?.[0] ? String(goals[0].id) : null;
    if (pane.active) history.replaceState(history.state, "", selected ? goalHash(selected) : "#goals");
  }
  function open(item: { kind: LinkKind; ref: string; view: string | null }) {
    if (item.kind === "job" && /^\d+$/.test(item.ref)) onopenjob(Number(item.ref));
    else if (item.view && isView(item.view)) onview(item.view);
  }

  $effect(() => {
    pane.setTrail("goals", detail ? [{ label: detail.title }] : []);
    return () => pane.clearTrail("goals");
  });
  onMount(() => {
    // A deep link (a brief item, a notification) while Goals is already open picks that goal.
    const follow = () => {
      const id = goalFromHash(location.hash);
      if (pane.active && id && id !== selected) selected = id;
    };
    window.addEventListener("hashchange", follow);
    return () => window.removeEventListener("hashchange", follow);
  });
</script>

<section class="goals" aria-label="Goals">
  <aside class="list surface" aria-label="Your goals">
    <header>
      <div>
        <span class="eyebrow">Plan</span>
        <h2>Goals{#if goals?.length}<span class="count-pill">{goals.length}</span>{/if}</h2>
      </div>
      <button type="button" class="pill primary" onclick={() => (editor = { goal: null })}><Icon name="plus" size={13} />New goal</button>
    </header>
    {#if !goals}
      {#if !error}<Loader label="Loading goals…" />{/if}
    {:else if goals.length}
      <ul>
        {#each goals as goal (goal.id)}
          {@const left = daysLeftLabel(goal.days_left)}
          <li>
            <button type="button" class="goal" class:quiet={goal.status !== "active"} aria-current={String(goal.id) === selected ? "true" : undefined}
              onclick={() => choose(goal.id)}>
              <TimerRing remaining={goal.progress} total={1} phase="focus" size={34} stroke={3.5} label={`${percent(goal.progress)}%`}>
                <span class="mini" aria-hidden="true">{percent(goal.progress)}</span>
              </TimerRing>
              <span class="what">
                <b>{goal.title}</b>
                <small>{[goal.status === "active" ? "" : statusLabel(goal.status), left, `${goal.links_count} linked`].filter(Boolean).join(" · ")}</small>
              </span>
              {#if goal.suggestions_count}<span class="new" title="Rika found something for this goal">{goal.suggestions_count} new</span>{/if}
            </button>
          </li>
        {/each}
      </ul>
    {/if}
  </aside>

  <div class="detail surface">
    {#if error}<p class="error-banner" role="alert">{error}<button type="button" class="button" onclick={() => { error = ""; void loadList(); void loadDetail(selected); }}>Try again</button></p>{/if}
    {#if goals && !goals.length}
      <EmptyState title="Set your first goal">
        <p>Give it a why, a date and a few measures. Link the notes, tasks, jobs and study sessions that move it, and Rika keeps looking for more.</p>
        {#snippet actions()}<button type="button" class="pill primary" onclick={() => (editor = { goal: null })}>New goal</button>{/snippet}
      </EmptyState>
    {:else if detail}
      <GoalDetail goal={detail} onchange={changed} onedit={() => detail && (editor = { goal: detail })} onopen={open} />
    {:else if goals && selected}
      <Loader label="Opening the goal…" />
    {/if}
  </div>
</section>

{#if editor}
  {#key editor.goal?.id ?? "new"}
    <GoalForm goal={editor.goal} onsaved={saved} ondeleted={deleted} onclose={() => (editor = null)} />
  {/key}
{/if}

<style>
  .goals {
    display: grid;
    grid-template-columns: minmax(240px, 300px) minmax(0, 1fr);
    align-items: start;
    gap: 14px;
  }
  @media (max-width: 860px) {
    .goals {
      grid-template-columns: minmax(0, 1fr);
    }
  }
  .list {
    display: grid;
    gap: 6px;
    padding: 16px 12px 12px;
    border-radius: var(--r-panel);
  }
  .list header {
    display: flex;
    align-items: flex-end;
    justify-content: space-between;
    gap: 10px;
    padding: 0 4px 8px;
    border-bottom: 1px solid var(--hairline);
  }
  h2 {
    display: flex;
    align-items: center;
    gap: 8px;
    margin: 2px 0 0;
    font-size: 19px;
    font-weight: 600;
  }
  .list header .pill {
    min-height: 28px;
  }
  ul {
    display: grid;
    gap: 2px;
    margin: 0;
    padding: 0;
    list-style: none;
  }
  .goal {
    display: grid;
    grid-template-columns: 34px minmax(0, 1fr) auto;
    align-items: center;
    gap: 10px;
    width: 100%;
    min-height: 52px;
    padding: 6px 8px;
    border: 1px solid transparent;
    border-radius: 10px;
    background: transparent;
    color: var(--ink);
    text-align: left;
  }
  .goal:hover {
    background: color-mix(in srgb, var(--ink) 6%, transparent);
  }
  .goal[aria-current="true"] {
    border-color: var(--hairline);
    background: color-mix(in srgb, var(--ink) 9%, transparent);
  }
  .goal.quiet .what {
    opacity: 0.7;
  }
  .mini {
    font: 600 9.5px var(--font-mono);
  }
  .what {
    display: grid;
    gap: 2px;
    min-width: 0;
  }
  b {
    overflow: hidden;
    font: 500 13px/1.3 var(--font-ui);
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  small {
    overflow: hidden;
    color: var(--muted);
    font-size: 11px;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .new {
    padding: 1px 7px;
    border: 1px solid color-mix(in srgb, var(--c2) 55%, transparent);
    border-radius: 999px;
    background: color-mix(in srgb, var(--c2) 14%, transparent);
    font: 500 10px/1.5 var(--font-mono);
    white-space: nowrap;
  }
  .detail {
    min-width: 0;
    min-height: 360px;
    border-radius: var(--r-panel);
    container: goals-detail / inline-size;
  }
  .detail .error-banner {
    margin: 14px 22px 0;
  }
</style>
