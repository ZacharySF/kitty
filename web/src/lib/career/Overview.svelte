<script lang="ts">
  import { api, type Job } from "../api";
  import Icon from "../Icon.svelte";
  import Loader from "../Loader.svelte";
  import AskRika from "../rika/AskRika.svelte";
  import RikaMark from "../rika/RikaMark.svelte";
  import RikaRemembers from "../rika/RikaRemembers.svelte";
  import type { View } from "../workspace";
  import { careerCounts } from "./counts.svelte";
  import { countLabel } from "./rail";
  import {
    deadlinesThisWeek, suggestion, weekGoal, weekday,
    type DueReminder, type Proposal, type WeekTrack,
  } from "./overview";

  /**
   * Career's Overview (Q13 B), where Career opens: this week's application goal, the new strong
   * matches, what closes or is due this week, the status emails waiting for review and Rika's
   * one suggestion. Each part reads an existing route and says plainly when it has nothing.
   */
  let { revision = 0, onview, onopenjob, onadd }: {
    revision?: number;
    onview: (next: View) => void;
    onopenjob: (id: number) => void;
    onadd: () => void;
  } = $props();

  type Dashboard = { day: string; weekly: WeekTrack[]; reminders: DueReminder[]; deadlines: Job[]; proposals: number };
  let dashboard = $state<Dashboard | null>(null);
  let matches = $state<Job[]>([]);
  let proposals = $state<Proposal[]>([]);
  let loading = $state(true);
  let error = $state("");

  $effect(() => {
    void revision;
    let cancelled = false;
    void Promise.allSettled([
      api<Dashboard>("/dashboard"),
      api<{ jobs: Job[] }>(`/jobs?${new URLSearchParams({ view: "inbox", tier: "A", limit: "3" })}`),
      api<{ proposals: Proposal[] }>("/mail"),
    ]).then(([digest, strong, mail]) => {
      if (cancelled) return;
      if (digest.status === "fulfilled") dashboard = digest.value;
      else error = (digest.reason as Error).message;
      matches = strong.status === "fulfilled" ? strong.value.jobs : [];
      proposals = mail.status === "fulfilled" ? mail.value.proposals : [];
      loading = false;
    });
    return () => { cancelled = true; };
  });

  const today = $derived(dashboard?.day ?? new Date().toISOString().slice(0, 10));
  const goal = $derived(weekGoal(dashboard?.weekly ?? []));
  const deadlines = $derived(deadlinesThisWeek(today, dashboard?.deadlines ?? [], dashboard?.reminders ?? []));
  const emails = $derived(proposals.length);
  const tip = $derived(suggestion({ today, goal, matches, emails, deadlines }));
  const waiting = $derived(deadlines.length + emails);
  const newJobs = $derived(careerCounts.value?.new ?? 0);
  const dateLine = $derived(new Date(`${today}T12:00:00Z`).toLocaleDateString("en-US", { weekday: "long", day: "numeric", month: "long", timeZone: "UTC" }));
  const RADIUS = 46;
  const CIRCLE = 2 * Math.PI * RADIUS;

  const initials = (company: string) => company.replace(/[^\p{L}\p{N} ]/gu, "").split(/\s+/).filter(Boolean).slice(0, 2).map((word) => word[0]).join("").toUpperCase() || "?";
  const status = (value: string) => value.replace(/_/g, " ").replace(/^oa$/i, "online assessment");
  function act() {
    if (tip.action.job !== undefined) onopenjob(tip.action.job);
    else if (tip.action.view) onview(tip.action.view);
  }
</script>

<div class="overview">
  <header class="head">
    <div>
      <h1>This week</h1>
      <p class="muted">{dateLine}{#if !loading}{` · ${waiting === 0 ? "nothing needs you yet" : waiting === 1 ? "one thing needs you" : `${waiting} things need you`}`}{/if}</p>
    </div>
    <div class="head-tools">
      <AskRika prompt="What should I focus on in my job search this week?" />
      <button type="button" class="button primary" onclick={onadd}><Icon name="plus" size={14} />Add a job link</button>
    </div>
  </header>

  {#if loading}
    <Loader label="Loading your week…" />
  {:else}
    {#if error}<p class="error-banner" role="alert">{error}</p>{/if}
    <div class="grid">
      <section class="tile suggestion" aria-label="Rika suggests">
        <div class="who"><RikaMark size={30} /><div><span class="eyebrow">Rika suggests</span><strong>{tip.title}</strong></div></div>
        <p>{tip.body}</p>
        <div class="actions">
          <button type="button" class="button primary" onclick={act}>{tip.action.label}</button>
          <AskRika prompt={tip.prompt} />
        </div>
      </section>

      <section class="tile goal" aria-labelledby="career-goal">
        <h2 class="section-head" id="career-goal">This week's goal</h2>
        {#if goal.target === 0}
          <p class="empty">No weekly goal yet. Give a track a weekly target and your progress shows here.</p>
          <button type="button" class="text-button" onclick={() => onview("profile")}>Open your profile <Icon name="arrow" size={14} /></button>
        {:else}
          <div class="goal-body">
            <svg viewBox="0 0 110 110" width="108" height="108" role="img" aria-label={`${goal.sent} of ${goal.target} applications sent`}>
              <circle cx="55" cy="55" r={RADIUS} class="ring" />
              <circle cx="55" cy="55" r={RADIUS} class="fill" stroke-dasharray={`${Math.min(1, goal.sent / goal.target) * CIRCLE} ${CIRCLE}`} transform="rotate(-90 55 55)" />
              <text x="55" y="58" class="big">{goal.sent}<tspan class="of">/{goal.target}</tspan></text>
              <text x="55" y="76" class="small">sent</text>
            </svg>
            <ul>
              {#each goal.tracks as track (track.name)}
                <li><span>{track.label}</span><b>{track.sent}/{track.target}</b><span class="bar"><span style:width={`${Math.min(100, (track.sent / track.target) * 100)}%`}></span></span></li>
              {/each}
              <li class="left">{goal.remaining === 0 ? "Goal met · resets Monday" : `${goal.remaining} to go · resets Monday`}</li>
            </ul>
          </div>
        {/if}
      </section>

      <section class="tile matches" aria-labelledby="career-matches">
        <div class="tile-head">
          <h2 class="section-head" id="career-matches">New strong matches{#if matches.length}<span class="count-pill">{matches.length}</span>{/if}</h2>
          <button type="button" class="text-button" onclick={() => onview("inbox")}>{newJobs ? `All ${countLabel(newJobs)} new jobs` : "New jobs"} <Icon name="arrow" size={14} /></button>
        </div>
        {#if matches.length}
          <div class="cards">
            {#each matches as job (job.id)}
              <button type="button" class="match" onclick={() => onopenjob(job.id)}>
                <span class="match-top">
                  <span class="company-mark" aria-hidden="true">{initials(job.company)}</span>
                  <span class="badge tier-a">Rank {job.tier}</span>
                  {#if job.deadline_at}<span class="due">Closes {weekday(job.deadline_at.slice(0, 10))} {Number(job.deadline_at.slice(8, 10))}</span>{/if}
                </span>
                <strong class="title">{job.title}</strong>
                <span class="company">{job.company}{#if job.location} · {job.location}{/if}{#if job.is_remote} · Remote{/if}</span>
              </button>
            {/each}
          </div>
        {:else}
          <p class="empty">No new Rank A jobs right now. Ranking your new jobs finds the strong ones.</p>
          <button type="button" class="text-button" onclick={() => onview("triage")}>Rank jobs <Icon name="arrow" size={14} /></button>
        {/if}
      </section>

      <section class="tile deadlines" aria-labelledby="career-deadlines">
        <div class="tile-head">
          <h2 class="section-head" id="career-deadlines">Deadlines this week</h2>
          <button type="button" class="text-button" onclick={() => onview("reminders")}>Reminders <Icon name="arrow" size={14} /></button>
        </div>
        {#if deadlines.length}
          <ul class="rows">
            {#each deadlines as row (row.key)}
              <li>
                <button type="button" class="row" onclick={() => (row.jobId !== null ? onopenjob(row.jobId) : onview("reminders"))}>
                  <span class="day" class:overdue={row.overdue}><small>{row.overdue ? "Late" : weekday(row.date)}</small>{Number(row.date.slice(8, 10))}</span>
                  <span class="row-main"><b>{row.title}</b><small>{row.detail}</small></span>
                </button>
              </li>
            {/each}
          </ul>
        {:else}
          <p class="empty">Nothing closes or is due in the next seven days.</p>
        {/if}
      </section>

      <section class="tile emails" aria-labelledby="career-emails">
        <div class="tile-head">
          <h2 class="section-head" id="career-emails">Status emails waiting{#if emails}<span class="count-pill">{emails}</span>{/if}</h2>
          <button type="button" class="text-button" onclick={() => onview("mail")}>Email updates <Icon name="arrow" size={14} /></button>
        </div>
        {#if emails}
          <ul class="rows">
            {#each proposals.slice(0, 4) as mail (mail.event_id)}
              <li>
                <span class="mail-icon"><Icon name="mail" size={16} /></span>
                <span class="row-main"><b>{mail.company}</b><small>{mail.title}{#if mail.evidence} · “{mail.evidence}”{/if}</small></span>
                <span class="proposal">Mark as {status(mail.to_status)}</span>
                <button type="button" class="pill" onclick={() => onview("mail")}>Review</button>
              </li>
            {/each}
          </ul>
          {#if emails > 4}<p class="more-line">and {emails - 4} more in Email updates</p>{/if}
        {:else}
          <p class="empty">No status emails waiting. A mail check proposes changes here for you to accept.</p>
        {/if}
      </section>

      <section class="remembers" aria-label="What Rika remembers">
        <span class="who-small"><RikaMark size={16} />Rika remembers</span>
        <div class="remembers-body">
          <RikaRemembers onopen={() => onview("rika")}>
            {#snippet empty()}<p class="empty">Nothing yet. Rika learns the skills your saved and queued jobs ask for.</p>{/snippet}
          </RikaRemembers>
        </div>
      </section>
    </div>
  {/if}
</div>

<style>
  .overview {
    display: grid;
    gap: 16px;
    padding: 14px 0 24px;
    container-type: inline-size;
  }
  .head {
    display: flex;
    flex-wrap: wrap;
    align-items: flex-end;
    justify-content: space-between;
    gap: 16px;
  }
  .head h1 {
    margin: 0 0 4px;
    font-family: var(--font-ui);
  }
  .head p {
    margin: 0;
    font-size: 13.5px;
  }
  .head-tools,
  .actions {
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .grid {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 14px;
  }
  .tile {
    display: grid;
    align-content: start;
    gap: 12px;
    min-width: 0;
    padding: 16px 18px;
    border: 1px solid var(--border);
    border-radius: var(--r-panel);
    background: var(--surface);
  }
  .tile-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
  }
  .tile .text-button {
    min-height: 0;
    justify-self: start;
  }
  h2.section-head {
    margin: 0;
  }
  .empty {
    margin: 0;
    color: var(--muted);
    font-size: 13px;
    line-height: 1.5;
  }
  /* Rika's suggestion: the one tinted tile. */
  .suggestion {
    grid-column: span 2;
    border-color: color-mix(in srgb, var(--accent) 30%, var(--border));
    background: color-mix(in srgb, var(--accent) 7%, var(--surface));
  }
  .who {
    display: flex;
    align-items: center;
    gap: 12px;
  }
  .who div {
    display: grid;
    gap: 3px;
  }
  .eyebrow {
    color: var(--muted);
    font: 600 var(--fs-label, 11px) / 1.3 var(--font-ui);
  }
  .who strong {
    font-size: 17px;
    font-weight: 600;
  }
  .suggestion p {
    margin: 0;
    max-width: 62ch;
    font-size: 14px;
    line-height: 1.55;
  }
  .goal-body {
    display: flex;
    align-items: center;
    gap: 16px;
  }
  .goal svg {
    flex: none;
  }
  .ring,
  .fill {
    fill: none;
    stroke-width: 9;
  }
  .ring {
    stroke: color-mix(in srgb, var(--ink) 10%, transparent);
  }
  .fill {
    stroke: var(--accent);
    stroke-linecap: round;
  }
  .big {
    fill: var(--ink);
    font: 600 28px var(--font-ui);
    text-anchor: middle;
  }
  .of {
    fill: var(--muted);
    font-size: 16px;
  }
  .small {
    fill: var(--muted);
    font: 11px var(--font-ui);
    text-anchor: middle;
  }
  .goal ul {
    display: grid;
    flex: 1;
    gap: 10px;
    min-width: 0;
    margin: 0;
    padding: 0;
    list-style: none;
    font-size: 12.5px;
  }
  .goal li {
    display: grid;
    grid-template-columns: 1fr auto;
    gap: 4px 8px;
  }
  .goal li b {
    font-weight: 500;
  }
  .bar {
    grid-column: 1 / -1;
    height: 4px;
    overflow: hidden;
    border-radius: 999px;
    background: color-mix(in srgb, var(--ink) 10%, transparent);
  }
  .bar span {
    display: block;
    height: 100%;
    background: var(--accent);
  }
  .goal li.left {
    display: block;
    color: var(--muted);
  }
  .matches {
    grid-column: span 3;
  }
  .cards {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 12px;
  }
  .match {
    display: grid;
    align-content: start;
    gap: 6px;
    min-width: 0;
    height: auto;
    padding: 14px;
    border: 1px solid var(--border);
    border-radius: var(--r-item, 10px);
    background: var(--surface-raised);
    color: var(--ink);
    text-align: left;
    cursor: pointer;
  }
  .match:hover {
    border-color: color-mix(in srgb, var(--accent) 45%, var(--border));
  }
  .match:focus-visible,
  .row:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }
  .match-top {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-bottom: 4px;
  }
  .match .company-mark {
    width: 32px;
    height: 32px;
    font-size: 11px;
  }
  .due {
    margin-left: auto;
    color: var(--warning);
    font: 600 11px var(--font-ui);
  }
  .title {
    font-size: 14.5px;
    font-weight: 600;
  }
  .company {
    overflow: hidden;
    color: var(--muted);
    font-size: 12.5px;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .emails {
    grid-column: span 2;
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
    gap: 12px;
    padding: 9px 0;
    border-top: 1px solid var(--hairline, var(--border));
  }
  .rows li:first-child {
    padding-top: 2px;
    border-top: 0;
  }
  .row {
    display: flex;
    flex: 1;
    align-items: center;
    gap: 12px;
    min-width: 0;
    height: auto;
    min-height: 0;
    padding: 0;
    border: 0;
    background: transparent;
    color: var(--ink);
    text-align: left;
    cursor: pointer;
  }
  .day {
    display: grid;
    flex: none;
    place-items: center;
    width: 40px;
    height: 40px;
    border-radius: var(--r-item, 8px);
    background: var(--wash);
    font: 600 15px/1 var(--font-ui);
  }
  .day small {
    color: var(--muted);
    font: 600 9.5px/1 var(--font-ui);
  }
  .day.overdue small {
    color: var(--warning);
  }
  .row-main {
    display: grid;
    flex: 1;
    gap: 2px;
    min-width: 0;
    font-size: 13px;
  }
  .row-main b {
    font-weight: 500;
  }
  .row-main small {
    overflow: hidden;
    color: var(--muted);
    font-size: 12px;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .mail-icon {
    display: grid;
    flex: none;
    place-items: center;
    width: 32px;
    height: 32px;
    border-radius: 999px;
    background: var(--wash);
    color: var(--muted);
  }
  .proposal {
    color: var(--ink);
    font-size: 12.5px;
    white-space: nowrap;
  }
  .more-line {
    margin: 0;
    color: var(--muted);
    font-size: 12px;
  }
  .remembers {
    display: flex;
    grid-column: span 3;
    align-items: center;
    gap: 14px;
    padding: 10px 16px;
    border: 1px dashed var(--border);
    border-radius: var(--r-panel);
  }
  .who-small {
    display: inline-flex;
    flex: none;
    align-items: center;
    gap: 8px;
    color: var(--muted);
    font: 600 var(--fs-label, 11px) / 1.3 var(--font-ui);
  }
  .remembers-body {
    flex: 1;
    min-width: 0;
    --line: var(--border);
  }
  /* The shared strip sits inside this one: its own label and rule are this tile's. */
  .remembers-body :global(.remembers) {
    margin: 0;
    padding: 0;
    border: 0;
  }
  .remembers-body :global(.remembers > .label) {
    display: none;
  }
  @container (max-width: 760px) {
    .grid,
    .cards {
      grid-template-columns: minmax(0, 1fr);
    }
    .suggestion,
    .matches,
    .emails,
    .remembers {
      grid-column: auto;
    }
    .remembers {
      flex-wrap: wrap;
    }
  }
</style>
