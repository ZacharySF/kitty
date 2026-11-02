<script lang="ts" module>
  /** Q61: the week or month of the job search told in sentences, from /api/reports only. */
  export type Period = { start: string; applied: number; replies: number; advanced: number; offers: number };
  export type Summary = {
    weeks: { start: string; applied: number }[];
    weekly_total: number;
    weekly_target: number;
    streak_days: number;
    week: Period;
    month: Period & { previous_applied: number };
  };
  export type Rate = { key: string; applications: number; responses: number; response_rate: number };
</script>

<script lang="ts">
  let {
    summary,
    rates,
    tables,
    ontables,
  }: { summary: Summary; rates: Record<string, Rate[]>; tables: boolean; ontables: () => void } = $props();
  let period = $state<"week" | "month">("week");
  const plural = (n: number, word: string, many = `${word}s`) => `${n} ${n === 1 ? word : many}`;
  const percent = (rate: number) => `${Math.round(rate * 100)}%`;
  const monthName = $derived(new Date(`${summary.month.start}T12:00:00`).toLocaleString("en", { month: "long" }));
  const sparkMax = $derived(Math.max(1, summary.weekly_target, ...summary.weeks.map(week => week.applied)));
  /** The grouping that hears back most, when there is a real comparison to make. */
  function best(rows: Rate[] | undefined) {
    const enough = (rows ?? []).filter(row => row.applications >= 3 && row.key !== "(none)");
    if (enough.length < 2) return null;
    const top = [...enough].sort((a, b) => b.response_rate - a.response_rate)[0];
    return top.response_rate > 0 ? top : null;
  }
  const bestTrack = $derived(best(rates.track));
  const bestSource = $derived(best(rates.source));
  const headline = $derived.by(() => {
    const { week, month, weekly_target: target, streak_days: streak } = summary;
    if (period === "month") {
      if (month.offers) return `${monthName}: ${plural(month.offers, "offer")} so far.`;
      if (month.replies) return `${monthName}: ${plural(month.replies, "reply", "replies")} so far.`;
      return month.applied ? `${monthName}: ${month.applied} sent so far.` : `A quiet ${monthName} so far.`;
    }
    if (target && week.applied >= target) return "This week's target is met.";
    if (streak >= 3) return `${streak} days in a row.`;
    if (week.replies) return `${plural(week.replies, "reply", "replies")} this week.`;
    return week.applied ? `${week.applied} sent this week.` : "A quiet week so far.";
  });
  const next = $derived.by(() => {
    const { weekly_total: total, weekly_target: target, streak_days: streak } = summary;
    if (target && total < target) return `send ${plural(target - total, "more application", "more applications")} to reach this week's target of ${target}.`;
    if (!streak) return "send one application today to start a streak.";
    if (bestSource) return `lean on ${bestSource.key}, your best reply rate (${percent(bestSource.response_rate)}).`;
    return "keep the streak going with one application today.";
  });
</script>

{#snippet spark()}
  <span class="spark" role="img" aria-label={`Applications per week, last ${summary.weeks.length} weeks: ${summary.weeks.map(week => week.applied).join(", ")}`}
    >{#each summary.weeks as week (week.start)}<i style:height={`${Math.max(8, (week.applied / sparkMax) * 100)}%`} class:zero={!week.applied}></i>{/each}</span
  >
{/snippet}

{#snippet outcomes(item: Period, when: string)}
  {#if item.replies}
    <b>{plural(item.replies, "reply", "replies")}</b> came in {when}{#if item.advanced}, <b>{item.advanced}</b> to an assessment or interview{/if}{#if item.offers}, and
      <b>{plural(item.offers, "offer")}</b>{/if}.
  {:else}
    No replies came in {when}.
  {/if}
{/snippet}

<article class="story" aria-label="Job-search summary">
  <header>
    <span class="eyebrow">report · {period === "week" ? "this week" : monthName}</span>
    <div class="seg" role="radiogroup" aria-label="Period">
      <button role="radio" aria-checked={period === "week"} onclick={() => (period = "week")}>Week</button>
      <button role="radio" aria-checked={period === "month"} onclick={() => (period = "month")}>Month</button>
    </div>
  </header>
  <h3>{headline}</h3>
  {#if period === "week"}
    <p>
      You sent <b>{plural(summary.week.applied, "application")}</b> this week{#if summary.weekly_target}
        against a target of <b>{summary.weekly_target}</b>{/if}
      {@render spark()}{#if summary.streak_days}, and you've applied <b>{plural(summary.streak_days, "day")} in a row</b>.{:else}.
        Your streak starts with the next one.{/if}
    </p>
    <p>{@render outcomes(summary.week, "this week")}</p>
  {:else}
    <p>
      <b>{plural(summary.month.applied, "application")}</b> in {monthName} so far, against
      <b>{summary.month.previous_applied}</b> last month. Weekly volume, oldest first: {@render spark()}.
    </p>
    <p>{@render outcomes(summary.month, `during ${monthName}`)}</p>
  {/if}
  {#if bestTrack}
    <p><b>{bestTrack.key}</b> answers most: <b>{bestTrack.responses} of {bestTrack.applications}</b> heard back ({percent(bestTrack.response_rate)}).</p>
  {/if}
  <footer>
    <span class="next"><i aria-hidden="true">✿</i><span>Next: {next}</span></span>
    <button class="pill" aria-expanded={tables} aria-controls="report-tables" onclick={ontables}>{tables ? "Hide tables" : "Show tables"}</button>
  </footer>
</article>

<style>
  .story {
    display: grid;
    gap: 10px;
    margin: 0 0 1rem;
    padding: 18px 20px;
    border: 1px solid var(--hairline);
    border-radius: 16px;
    background:
      radial-gradient(80% 90% at 100% 0%, color-mix(in srgb, var(--c1, var(--violet)) 12%, transparent), transparent 60%),
      color-mix(in srgb, var(--solid-panel, #1c1c1c) 60%, transparent);
  }
  header,
  footer {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
  }
  .eyebrow {
    color: var(--violet);
  }
  h3 {
    margin: 0;
    color: var(--ink);
    font: 700 23px/1.2 var(--font-ui);
  }
  p {
    margin: 0;
    max-width: 62ch;
    color: var(--muted);
    font-size: 14px;
    line-height: 1.6;
  }
  b {
    color: var(--ink);
    font-weight: 600;
  }
  .spark {
    display: inline-flex;
    align-items: end;
    gap: 2px;
    height: 13px;
    margin: 0 4px;
    vertical-align: -1px;
  }
  .spark i {
    width: 4px;
    border-radius: 1px;
    background: color-mix(in srgb, var(--c1, var(--violet)) 70%, transparent);
  }
  .spark i.zero {
    background: color-mix(in srgb, var(--ink) 18%, transparent);
  }
  .spark i:last-child {
    background: var(--c1, var(--violet));
  }
  footer {
    padding-top: 10px;
    border-top: 1px solid var(--hairline);
  }
  .next {
    color: var(--ink);
    font-size: 13px;
  }
  .next i {
    margin-right: 4px;
    color: var(--c1, var(--violet));
    font-style: normal;
  }
  footer .pill {
    min-height: 28px;
    padding: 3px 12px;
    font-size: 10px;
  }
  .seg {
    display: inline-flex;
    gap: 2px;
    padding: 3px;
    border: 1px solid var(--hairline);
    border-radius: 999px;
  }
  .seg button {
    padding: 3px 10px;
    border: 0;
    border-radius: 999px;
    background: transparent;
    color: var(--muted);
    font: 500 10px var(--font-mono);
    letter-spacing: 0.1em;
    text-transform: uppercase;
    cursor: pointer;
  }
  .seg button[aria-checked="true"] {
    background: color-mix(in srgb, var(--ink) 12%, transparent);
    color: var(--ink);
  }
</style>
