<script lang="ts">
  import { Button } from "@nunera/bracket";
  import { BarChart } from "@nunera/bracket/charts";
  import { minutesLabel, type Collection, type Item, type Workspace } from "./types";

  /** Study time, subjects and goals for the selected history period. */
  let { data, period, label, onedit, ondelete }: {
    data: Workspace;
    period: "week" | "month";
    label: string;
    onedit: (kind: Collection, item?: Item) => void;
    ondelete: (kind: Collection, item: Item) => void;
  } = $props();

  const subjectName = (id: number | null | undefined) => data.subjects.find((item) => item.id === id)?.name || "Unassigned";
  const daySeries = $derived([{ name: "Study minutes", points: data.stats.history.days.map((day, index) => ({ x: index, y: day.seconds / 60, label: day.date })) }]);
  const subjectSeries = $derived([{ name: "Study minutes by subject", points: data.stats.history.subjects.map((subject, index) => ({ x: index, y: subject.seconds / 60, label: subject.name })) }]);
</script>

<div class="summary">
  <article><strong>{minutesLabel(data.stats.today_seconds)}</strong><span>Today</span></article>
  <article><strong>{minutesLabel(data.stats.week_seconds)}</strong><span>This week</span></article>
  <article><strong>{minutesLabel(data.stats.month_seconds)}</strong><span>This month</span></article>
  <article><strong>{data.stats.daily_streak} days</strong><span>Study streak</span></article>
</div>
<p class="muted">
  Reports use {data.stats.timezone}. Time is split across local midnights; streaks use completed session dates, never logins. An ongoing streak can include yesterday / last week.
</p>
<div class="panel">
  <h3>Study activity · last 12 weeks</h3>
  <div class="heatmap" aria-label="Daily study activity">
    {#each data.stats.heatmap as cell (cell.date)}<div class="heat-cell" style:opacity={cell.seconds ? Math.min(1, 0.3 + cell.seconds / 14400) : 0.12}
        title={`${cell.date}: ${minutesLabel(cell.seconds)}`} aria-label={`${cell.date}: ${minutesLabel(cell.seconds)}`}></div>{/each}
  </div>
  <p class="muted">Darker means more completed study time. Total: {minutesLabel(data.stats.total_seconds)} across {data.stats.session_count} sessions.</p>
</div>
<div class="chart-grid">
  <article class="panel">
    <h3>{period === "week" ? "Selected week" : "Selected month"} · minutes</h3>
    <BarChart series={daySeries} minY={0} label={`Study minutes: ${label}`} width={520} height={220} />
    <details>
      <summary>View time data</summary>{#each data.stats.history.days as day (day.date)}<p>{day.date}: {minutesLabel(day.seconds)}</p>{/each}
    </details>
  </article>
  <article class="panel">
    <h3>Subjects this {period} · minutes</h3>
    <BarChart series={subjectSeries} minY={0} label="Completed study time by subject" width={520} height={220} />
    <ul>
      {#each data.stats.history.subjects as subject (subject.id)}<li><span style:color={subject.color}>{subject.name}</span>: {minutesLabel(subject.seconds)}</li>
      {:else}<li>Your first completed session will appear here.</li>{/each}
    </ul>
  </article>
</div>
<div class="panel">
  <div class="row"><h3>Goals</h3><Button onclick={() => onedit("targets")}>Add goal</Button></div>
  {#each data.targets as target (target.id)}{@const goal = data.stats.goals.find((item) => item.id === target.id)}
    <div class="goal">
      <div><strong>{target.title}</strong> · {target.period} · {subjectName(target.subject_id)}</div>
      <progress value={goal?.seconds || 0} max={goal?.goal_seconds || 1}></progress>
      <span>{minutesLabel(goal?.seconds || 0)} / {target.minutes} min</span>
      <Button onclick={() => onedit("targets", target)}>Edit</Button>
      <Button onclick={() => ondelete("targets", target)}>Delete</Button>
    </div>
  {:else}<p>Set a daily or weekly time target for all study, a subject, or a project.</p>{/each}
</div>

<style>
  .summary {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
    gap: 0.75rem;
    margin-bottom: 1rem;
  }
  .summary article {
    display: grid;
    gap: 0.35rem;
    padding: 1rem;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 0.5rem;
  }
  .summary strong {
    font-size: 1.5rem;
  }
  .summary span {
    color: var(--muted);
  }
  .heatmap {
    display: grid;
    grid-auto-flow: column;
    grid-template-rows: repeat(7, 1fr);
    grid-template-columns: repeat(12, 1fr);
    gap: 0.35rem;
    max-width: 620px;
    margin-bottom: 0.75rem;
  }
  .heat-cell {
    background: var(--accent);
    aspect-ratio: 1;
    min-height: 14px;
    border-radius: 0.2rem;
  }
  .chart-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(min(100%, 230px), 1fr));
    gap: 0.85rem;
    margin: 0.85rem 0;
  }
  .chart-grid :global(svg) {
    max-width: 100%;
    height: auto;
  }
  .goal {
    display: flex;
    gap: 0.7rem;
    flex-wrap: wrap;
    align-items: center;
    margin: 1rem 0;
  }
  .goal progress {
    accent-color: var(--accent);
  }
</style>
