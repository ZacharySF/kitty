<script lang="ts">
  import type { SprintHistory } from "./api";
  import type { MathSettings } from "./settings";
  import Spark from "./Spark.svelte";
  import { trend } from "./sprint";

  /** The History drawer: the sprint trend at the current length and format, the daily streak, recent runs. */
  let { history, settings, error = "" }: { history: SprintHistory | null; settings: MathSettings; error?: string } = $props();
  const label = $derived(settings.mode === "mc" ? "multiple choice" : "typed");
  const runs = $derived(
    (history?.sprints ?? [])
      .filter((run) => run.mode === settings.mode && run.duration_s === settings.duration_s)
      .sort((a, b) => a.finished_at.localeCompare(b.finished_at)),
  );
  const shape = $derived(trend(runs.map((run) => run.score)));

  function when(at: string) {
    const date = new Date(at);
    const today = new Date();
    const days = Math.round((new Date(today.toDateString()).getTime() - new Date(date.toDateString()).getTime()) / 86_400_000);
    const time = date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
    return days === 0 ? `Today ${time}` : days === 1 ? `Yesterday ${time}` : date.toLocaleDateString([], { weekday: "short", day: "numeric", month: "short" });
  }
</script>

<h4>History</h4>
{#if error}<p class="note" role="alert">{error}</p>{/if}
<div class="streak" data-streak={history?.streak_days ?? 0}>
  <strong>{history?.streak_days ?? 0}</strong>
  <span>day sprint streak</span>
</div>
<div class="panel">
  <span class="eyebrow">Sprints · {settings.duration_s} s · {label}</span>
  {#if runs.length}
    <Spark values={runs.map((run) => run.score)} w={280} h={64} label="Sprint score trend" />
    <span class="best">best {shape.best} {#if runs.length > 1}· last {shape.change >= 0 ? "+" : "−"}{Math.abs(shape.change)}{/if}</span>
  {:else}
    <span class="note">Finish a sprint at this length to start the line.</span>
  {/if}
</div>
{#if runs.length}
  <ul class="rows" aria-label="Recent sprints">
    {#each runs.slice(-6).reverse() as run (run.id)}
      <li>
        <span class="what"><strong>Mental math sprint · {run.duration_s} s</strong><small>{when(run.finished_at)}</small></span>
        <span class="score">{run.score}{#if run.mode === "mc"}<small> ({run.wrong} wrong)</small>{/if}</span>
      </li>
    {/each}
  </ul>
{/if}

<style>
  h4 {
    margin: 0;
    font-size: 14px;
  }
  .streak {
    display: flex;
    align-items: center;
    gap: 12px;
  }
  .streak strong {
    color: var(--accent);
    font: 600 40px/1 var(--font-mono);
  }
  .streak span {
    display: grid;
    color: var(--ink);
    font-size: 13px;
  }
  .note,
  .best {
    color: var(--muted);
    font-size: 11.5px;
  }
  .panel {
    display: grid;
    gap: 8px;
    padding: 12px;
    border: 1px solid var(--hairline);
    border-radius: 12px;
  }
  .eyebrow {
    color: var(--subtle);
    font: 500 10px var(--font-mono);
    letter-spacing: 0.12em;
    text-transform: uppercase;
  }
  .note {
    margin: 0;
  }
  .rows {
    display: grid;
    margin: 0;
    padding: 0;
    list-style: none;
  }
  .rows li {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 8px 0;
  }
  .rows li + li {
    border-top: 1px solid color-mix(in srgb, var(--hairline) 60%, transparent);
  }
  .what {
    display: grid;
    flex: 1;
    min-width: 0;
  }
  .what strong {
    font: 500 12.5px var(--font-ui);
  }
  .what small {
    color: var(--muted);
    font-size: 11px;
  }
  .score {
    color: var(--ink);
    font: 600 14px var(--font-mono);
  }
  .score small {
    color: var(--muted);
    font-weight: 400;
  }
</style>
