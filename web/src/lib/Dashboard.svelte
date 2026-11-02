<script lang="ts">
  import { api, dateLabel, type Job } from "./api";
  import type { View } from "./workspace";
  import Icon from "./Icon.svelte";
  import Popover from "./ui/Popover.svelte";
  let {
    revision = 0,
    onopenjob,
    onview,
    onguide,
  }: {
    revision?: number;
    onopenjob: (id: number) => void;
    onview: (view: View) => void;
    onguide?: () => void;
  } = $props();
  type Digest = {
    day: string;
    weekly: {
      track: string;
      weekly_target: number;
      applied_this_week: number;
      remaining: number;
    }[];
    new_by_track: Record<string, Record<string, number>>;
    funnel: { response_rate: number; advance_rate: number };
    reminders: {
      id: number;
      job_id: number | null;
      label: string;
      due_at: string;
    }[];
    deadlines: Job[];
    stale: {
      job_id: number;
      company_name: string;
      title: string;
      days_since_change: number;
    }[];
    openings: { company: string; opens_on: string; basis: string }[];
    source_problems: { name: string; status: string }[];
    proposals: number;
    questions: number;
    pending_batches: number;
    tiers: Record<string, number>;
    states: Record<string, number>;
  };
  type Due = { key: string; text: string; detail: string; at: string; tone: string; open: () => void };
  const DETAILS = "Search health and weekly goals";
  let data = $state<Digest | null>(null);
  let error = $state("");
  $effect(() => {
    void revision;
    let cancelled = false;
    error = "";
    void api<Digest>("/dashboard")
      .then((result) => {
        if (!cancelled) data = result;
      })
      .catch((cause) => {
        if (!cancelled) error = cause.message;
      });
    return () => {
      cancelled = true;
    };
  });
  /** Q22: the next few reminders and deadlines, soonest first; the full lists live in Reminders. */
  const due = $derived.by<Due[]>(() => {
    if (!data) return [];
    const reminders = data.reminders.map((item) => ({
      key: `r${item.id}`,
      text: item.label,
      detail: `Reminder · ${dateLabel(item.due_at)}`,
      at: item.due_at,
      tone: "var(--warning)",
      open: () => (item.job_id ? onopenjob(item.job_id) : onview("reminders")),
    }));
    const deadlines = data.deadlines.filter((job) => job.deadline_at).map((job) => ({
      key: `d${job.id}`,
      text: `${job.company} deadline`,
      detail: `${job.company} · ${job.title} · ${dateLabel(job.deadline_at)}`,
      at: job.deadline_at as string,
      tone: "var(--c2)",
      open: () => onopenjob(job.id),
    }));
    return [...reminders, ...deadlines].sort((a, b) => a.at.localeCompare(b.at)).slice(0, 3);
  });
  const goal = $derived(
    (data?.weekly ?? []).reduce(
      (sum, item) => ({ sent: sum.sent + item.applied_this_week, target: sum.target + item.weekly_target }),
      { sent: 0, target: 0 },
    ),
  );
  /** "Today", a weekday within the week, otherwise a short date. */
  function when(value: string) {
    const date = new Date(value.length === 10 ? `${value}T00:00` : value);
    if (Number.isNaN(date.getTime())) return value;
    const start = (day: Date) => new Date(day.getFullYear(), day.getMonth(), day.getDate()).getTime();
    const days = Math.round((start(date) - start(new Date())) / 86_400_000);
    if (days === 0) return "Today";
    if (days === 1) return "Tomorrow";
    if (days > 1 && days < 7) return date.toLocaleDateString(undefined, { weekday: "short" });
    return date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  }
</script>

<div class="strip" role="region" aria-label="Reminders, deadlines and goals" aria-busy={!data && !error}>
  <span class="strip-label">This week</span>
  {#if error}<span class="error" role="alert">{error}</span>
  {:else if data}
    {#each due as item (item.key)}
      <button type="button" class="due" title={item.detail} onclick={item.open}
        ><span class="dot" style:background={item.tone}></span><span class="text">{item.text}</span><span class="when">{when(item.at)}</span></button
      >
    {:else}
      <span class="empty">Nothing due this week</span>
    {/each}
  {/if}
  <span class="tail">
    {#if data}
      <span class="goal" title={`Applications sent this week: ${goal.sent} of ${goal.target}`}>
        <span class="strip-label">Goal</span>
        <span class="goal-bar" aria-hidden="true"><span style:width={`${goal.target ? Math.min(100, (goal.sent / goal.target) * 100) : 0}%`}></span></span>
        <span class="goal-num">{goal.sent}/{goal.target}</span>
      </span>
    {/if}
    {#if onguide}<button type="button" class="strip-action" onclick={onguide}><Icon name="questions" size={14} />How to apply</button>{/if}
    {#if data}
      <Popover label={DETAILS} placement="bottom-end" class="jobs-digest" focus="panel">
        {#snippet trigger(props)}
          <button type="button" class="icon-button" aria-label={DETAILS} title={DETAILS} {...props}><Icon name="more" size={16} /></button>
        {/snippet}
        <div class="digest">
          <button type="button" class="link" onclick={() => onview("reminders")}>See all reminders <Icon name="arrow" size={13} /></button>
          {#if data.stale.length}
            <section>
              <h3 class="section-head">Waiting for a reply · {data.stale.length}</h3>
              <ul>
                {#each data.stale.slice(0, 5) as item (item.job_id)}<li>
                    <button type="button" onclick={() => onopenjob(item.job_id)}
                      >{item.company_name}<small>{item.days_since_change} days since activity · {item.title}</small></button
                    >
                  </li>{/each}
              </ul>
              <button type="button" class="link" onclick={() => onview("pipeline")}>See my applications <Icon name="arrow" size={13} /></button>
            </section>
          {/if}
          {#if data.openings.length}
            <section title="Estimates, not verified openings">
              <h3 class="section-head">Expected openings · {data.openings.length}</h3>
              <ul>
                {#each data.openings as item (`${item.company}-${item.opens_on}`)}<li class="row">
                    {item.company}<small>{dateLabel(item.opens_on)} · {item.basis}</small>
                  </li>{/each}
              </ul>
            </section>
          {/if}
          {#if data.source_problems.length}
            <section>
              <h3 class="section-head">Job boards that need attention</h3>
              <ul>
                {#each data.source_problems as item (item.name)}<li>
                    <button type="button" class="split" onclick={() => onview("sources")}>{item.name}<span class="badge">{item.status}</span></button>
                  </li>{/each}
              </ul>
            </section>
          {/if}
          <section class="attention">
            <button type="button" onclick={() => onview("mail")}><strong>{data.proposals}</strong>Mail proposals</button>
            <button type="button" onclick={() => onview("questions")}><strong>{data.questions}</strong>Unanswered questions</button>
            <button type="button" onclick={() => onview("triage")}><strong>{data.pending_batches}</strong>Pending AI batches</button>
          </section>
          <section>
            <h3 class="section-head">This week, by track</h3>
            <table>
              <thead><tr><th>Track</th><th>Sent / goal</th><th>Left</th><th>New A / B</th></tr></thead>
              <tbody>
                {#each data.weekly as item (item.track)}<tr>
                    <th>{item.track}</th><td>{item.applied_this_week} / {item.weekly_target}</td><td>{item.remaining}</td><td
                      >{data.new_by_track[item.track]?.A || 0} / {data.new_by_track[item.track]?.B || 0}</td
                    >
                  </tr>{/each}
              </tbody>
            </table>
          </section>
          <section class="rates" title="Current application stages, not cumulative history">
            <span>Response <strong>{(data.funnel.response_rate * 100).toFixed(0)}%</strong></span>
            <span>Assessment or later <strong>{(data.funnel.advance_rate * 100).toFixed(0)}%</strong></span>
          </section>
          <section>
            <h3 class="section-head">Open jobs by rank and state</h3>
            <dl>
              {#each Object.entries(data.tiers) as [key, count] (key)}<div><dt>Rank {key}</dt><dd>{count}</dd></div>{/each}
              {#each Object.entries(data.states) as [key, count] (key)}<div><dt>{key.replaceAll("_", " ")}</dt><dd>{count}</dd></div>{/each}
            </dl>
          </section>
        </div>
      </Popover>
    {/if}
  </span>
</div>

<style>
  /* Q22: an unboxed hairline row under the Find jobs header; always visible, never folds. */
  .strip {
    display: flex;
    align-items: center;
    gap: 18px;
    min-width: 0;
    min-height: 42px;
    padding: 4px 20px;
    border: 0;
    border-top: 1px solid var(--hairline);
    background: transparent;
    font-size: var(--fs-control);
    white-space: nowrap;
  }
  .strip-label {
    color: var(--muted);
    font: 600 var(--fs-label) / 1.3 var(--font-ui);
  }
  .due {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    min-width: 0;
    height: var(--control-h);
    padding: 0;
    border: 0;
    background: none;
    color: var(--ink);
    font: inherit;
    cursor: pointer;
  }
  .due:hover .text {
    color: var(--accent);
  }
  .due .text {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .dot {
    width: 6px;
    height: 6px;
    flex-shrink: 0;
    border-radius: 999px;
  }
  .when {
    color: var(--subtle);
    font: 10.5px var(--font-mono);
    letter-spacing: 0.06em;
    text-transform: uppercase;
  }
  .empty,
  .error {
    color: var(--muted);
  }
  .error {
    color: var(--error);
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .tail {
    display: inline-flex;
    align-items: center;
    gap: 14px;
    margin-left: auto;
  }
  .goal {
    display: inline-flex;
    align-items: center;
    gap: 9px;
  }
  .goal-bar {
    width: 90px;
    height: 4px;
    border-radius: 999px;
    background: color-mix(in srgb, var(--ink) 10%, transparent);
    overflow: hidden;
  }
  .goal-bar span {
    display: block;
    height: 100%;
    background: var(--c3);
  }
  .goal-num {
    color: var(--ink);
    font: 500 11px var(--font-mono);
  }
  .strip-action {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    height: var(--control-h);
    padding: 0;
    border: 0;
    background: none;
    color: var(--muted);
    font: 500 var(--fs-control) / 1 var(--font-ui);
    cursor: pointer;
  }
  .strip-action:hover {
    color: var(--ink);
  }
  .due:focus-visible,
  .strip-action:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }

  /* The details popover: hairline-separated groups, no boxes. */
  :global(.kitty-popover.jobs-digest) {
    width: min(380px, calc(100vw - 16px));
  }
  .digest {
    display: grid;
    padding: 4px 6px;
    font-size: var(--fs-control);
  }
  .digest section {
    padding: 10px 0;
    border-top: 1px solid var(--hairline);
  }
  .digest h3 {
    margin: 0 0 4px;
  }
  .link {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    justify-self: start;
    height: var(--control-h);
    padding: 0;
    border: 0;
    background: none;
    color: var(--accent);
    font: 500 var(--fs-control) / 1 var(--font-ui);
    cursor: pointer;
  }
  ul {
    display: grid;
    margin: 0;
    padding: 0;
    list-style: none;
  }
  li button,
  li.row {
    display: grid;
    width: 100%;
    padding: 6px 0;
    border: 0;
    background: none;
    color: var(--ink);
    font: inherit;
    line-height: 1.45;
    text-align: left;
    white-space: normal;
  }
  li button {
    cursor: pointer;
  }
  li button:hover {
    color: var(--accent);
  }
  li button.split {
    display: flex;
    justify-content: space-between;
    gap: 8px;
  }
  small {
    color: var(--muted);
    font-size: 11.5px;
  }
  .attention {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 8px;
  }
  .attention button {
    display: grid;
    gap: 2px;
    padding: 0;
    border: 0;
    background: none;
    color: var(--muted);
    font: 11.5px / 1.35 var(--font-ui);
    text-align: left;
    white-space: normal;
    cursor: pointer;
  }
  .attention button:hover {
    color: var(--ink);
  }
  .attention strong {
    color: var(--ink);
    font: 500 18px / 1.2 var(--font-ui);
  }
  table {
    width: 100%;
    border-collapse: collapse;
    font-size: 12px;
  }
  th,
  td {
    padding: 5px 4px 5px 0;
    border-bottom: 1px solid var(--hairline);
    text-align: left;
  }
  thead {
    color: var(--muted);
  }
  thead th {
    font-weight: 500;
  }
  tbody th {
    font-weight: 400;
  }
  .rates {
    display: flex;
    justify-content: space-between;
    gap: 16px;
    color: var(--muted);
    font-size: 12px;
  }
  .rates strong {
    display: block;
    margin-top: 2px;
    color: var(--ink);
    font: 500 18px / 1.2 var(--font-ui);
  }
  dl {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 4px 16px;
    margin: 0;
    font-size: 12px;
  }
  dl div {
    display: flex;
    justify-content: space-between;
    gap: 6px;
  }
  dt {
    color: var(--muted);
  }
  dd {
    margin: 0;
  }
  @media (max-width: 760px) {
    .strip {
      flex-wrap: wrap;
      gap: 6px 14px;
      padding: 6px 12px;
      white-space: normal;
    }
  }
</style>
