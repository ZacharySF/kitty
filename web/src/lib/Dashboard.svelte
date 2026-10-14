<script lang="ts">
  import { api, dateLabel, type Job } from "./api";
  import type { View } from "./workspace";
  let {
    revision = 0,
    onopenjob,
    onview,
  }: {
    revision?: number;
    onopenjob: (id: number) => void;
    onview: (view: View) => void;
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
</script>

<section class="panel dashboard-brief">
  <header class="panel-heading">
    <div>
      <span class="eyebrow">DON’T MISS THE NEXT STEP</span>
      <h2>Reminders & deadlines</h2>
    </div>
  </header>
  <div class="brief-body">
    {#if error}<p class="error-banner" role="alert">
        {error}
      </p>{/if}{#if !data && !error}<p class="muted" role="status">
        Loading reminders…
      </p>{:else if data}
      {#if !data.reminders.length && !data.deadlines.length}<p class="muted">
          Nothing needs your attention here today.
        </p>{/if}
      <ul>
        {#each data.reminders.slice(0, 6) as item}<li>
            <button
              onclick={() =>
                item.job_id ? onopenjob(item.job_id) : onview("reminders")}
              >{item.label}<small>Due {dateLabel(item.due_at)}</small></button
            >
          </li>{/each}{#each data.deadlines.slice(0, 6) as item}<li>
            <button onclick={() => onopenjob(item.id)}
              >{item.company} · {item.title}<small
                >Deadline {dateLabel(item.deadline_at)}</small
              ></button
            >
          </li>{/each}
      </ul>
      <button class="text-button" onclick={() => onview("reminders")}
        >See all reminders →</button
      >
      {#if data.stale.length}<details>
          <summary
            >Applications waiting for a reply ({data.stale.length})</summary
          >
          <ul>
            {#each data.stale.slice(0, 5) as item}<li>
                <button onclick={() => onopenjob(item.job_id)}
                  >{item.company_name}<small
                    >{item.days_since_change} days since activity · {item.title}</small
                  ></button
                >
              </li>{/each}
          </ul>
          <button class="text-button" onclick={() => onview("pipeline")}
            >See my applications →</button
          >
        </details>{/if}
      {#if data.openings.length}<details>
          <summary>Expected job openings ({data.openings.length})</summary>
          {#each data.openings as item}<p>
              {item.company}<small class="muted"
                >{dateLabel(item.opens_on)} · {item.basis} · estimate, not a verified
                opening</small
              >
            </p>{/each}
        </details>{/if}
      <details>
        <summary>Weekly goals & search health</summary>
        {#if data.source_problems.length}<h3>Job boards that need attention</h3>
          <ul>
            {#each data.source_problems as item}<li>
                <button onclick={() => onview("sources")}
                  >{item.name}<span class="badge">{item.status}</span></button
                >
              </li>{/each}
          </ul>{/if}
        <div class="attention">
          <button onclick={() => onview("mail")}
            ><strong>{data.proposals}</strong><span>Mail proposals</span
            ></button
          ><button onclick={() => onview("questions")}
            ><strong>{data.questions}</strong><span>Unanswered questions</span
            ></button
          ><button onclick={() => onview("triage")}
            ><strong>{data.pending_batches}</strong><span
              >Pending AI batches</span
            ></button
          >
        </div>
        <h3>This week, by track</h3>
        <table>
          <thead
            ><tr
              ><th>Track</th><th>Sent / goal</th><th>Left</th><th>New A / B</th
              ></tr
            ></thead
          ><tbody
            >{#each data.weekly as item}<tr
                ><th>{item.track}</th><td
                  >{item.applied_this_week} / {item.weekly_target}</td
                ><td>{item.remaining}</td><td
                  >{data.new_by_track[item.track]?.A || 0} / {data.new_by_track[
                    item.track
                  ]?.B || 0}</td
                ></tr
              >{/each}</tbody
          >
        </table>
        <div class="rates">
          <span
            >Response <strong
              >{(data.funnel.response_rate * 100).toFixed(0)}%</strong
            ></span
          ><span
            >Assessment or later <strong
              >{(data.funnel.advance_rate * 100).toFixed(0)}%</strong
            ></span
          >
        </div>
        <small class="muted"
          >Current application stages, not cumulative history.</small
        >
        <details>
          <summary>Open job totals by tier and state</summary>
          <dl>
            {#each Object.entries(data.tiers) as [key, count]}<div>
                <dt>Tier {key}</dt>
                <dd>{count}</dd>
              </div>{/each}{#each Object.entries(data.states) as [key, count]}<div
              >
                <dt>{key.replaceAll("_", " ")}</dt>
                <dd>{count}</dd>
              </div>{/each}
          </dl>
        </details>
      </details>
    {/if}
  </div>
</section>

<style>
  .brief-body {
    padding: 20px;
    font-size: 13px;
  }
  .attention {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 8px;
    margin: 20px 0;
  }
  .attention button {
    display: grid;
    gap: 8px;
    color: var(--ink);
    background: var(--surface-raised);
    padding: 12px 6px;
    border: 1px solid var(--border);
    border-radius: 8px;
  }
  .attention strong {
    font-size: 22px;
    font-weight: 500;
  }
  .attention span {
    font-size: 11px;
    color: var(--muted);
    line-height: 1.6;
  }
  h3 {
    font-size: 20px;
    margin: 26px 0 14px;
  }
  table {
    width: 100%;
    border-collapse: collapse;
    font-size: 12px;
  }
  th,
  td {
    text-align: left;
    padding: 9px 4px;
    border-bottom: 1px solid var(--border);
  }
  thead {
    color: var(--muted);
  }
  tbody th {
    font-weight: 400;
  }
  ul {
    list-style: none;
    padding: 0;
  }
  li button {
    width: 100%;
    text-align: left;
    background: none;
    border-bottom: 1px solid var(--border);
    padding: 12px 0;
    color: var(--ink);
    font-size: 13px;
    line-height: 1.6;
  }
  li button:hover {
    color: var(--accent);
  }
  small {
    display: block;
    line-height: 1.6;
    font-size: 12px;
    color: var(--muted);
  }
  li .badge {
    float: right;
  }
  .rates {
    display: flex;
    justify-content: space-between;
    gap: 16px;
    margin: 18px 0 8px;
    font-size: 12px;
    color: var(--muted);
  }
  .rates strong {
    display: block;
    font-size: 20px;
    font-weight: 500;
    color: var(--ink);
    margin-top: 8px;
  }
  details {
    margin-top: 24px;
    padding-top: 18px;
    border-top: 1px solid var(--border);
  }
  summary {
    cursor: pointer;
    color: var(--muted);
    font-size: 13px;
  }
  dl {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 12px;
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
</style>
