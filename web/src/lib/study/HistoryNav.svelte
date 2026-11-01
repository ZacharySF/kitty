<script lang="ts">
  import { Button } from "@nunera/bracket";
  import { minutesLabel, type Stats } from "./types";

  /** Week / month period switch and navigation shared by Progress and History. */
  let { stats, period, date, label, onnavigate }: {
    stats: Stats;
    period: "week" | "month";
    date: string;
    label: string;
    onnavigate: (anchor: string, period?: "week" | "month") => void;
  } = $props();
</script>

<div class="history-navigation panel">
  <div>
    <span class="eyebrow">Study history</span>
    <h3>{label}</h3>
    <p class="muted">{minutesLabel(stats.history.seconds)} studied · {stats.history.session_count} records · {stats.timezone}</p>
  </div>
  <div class="row">
    <div class="period-switch" aria-label="History period">
      <Button size="sm" aria-pressed={period === "week"} onclick={() => onnavigate(stats.history.start, "week")}>Week</Button>
      <Button size="sm" aria-pressed={period === "month"} onclick={() => onnavigate(stats.history.start, "month")}>Month</Button>
    </div>
    <Button size="sm" aria-label={`Previous ${period}`} onclick={() => onnavigate(stats.history.previous)}>←</Button>
    <Button size="sm" onclick={() => onnavigate("")}>Today</Button>
    <Button size="sm" aria-label={`Next ${period}`} onclick={() => onnavigate(stats.history.next)}>→</Button>
    <label class="history-date">Jump to date<input type="date" value={date || stats.today} onchange={(event) => onnavigate(event.currentTarget.value)} /></label>
  </div>
</div>

<style>
  .history-navigation {
    display: flex;
    align-items: center;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 1rem;
  }
  h3 {
    margin: 0.3rem 0;
    font-size: 1.12rem;
  }
  p {
    margin: 0;
    font-size: 0.8rem;
  }
  .period-switch {
    display: flex;
    gap: 0.6rem;
  }
  .history-date {
    display: flex;
    flex-direction: column;
    gap: 0.2rem;
    font-size: 0.7rem;
    color: var(--muted);
  }
  .history-date input {
    padding: 0.35rem 0.5rem;
    font-size: 0.8rem;
  }
</style>
