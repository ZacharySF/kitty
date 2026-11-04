<script lang="ts">
  import Icon from "../Icon.svelte";
  import TimerRing from "../TimerRing.svelte";
  import { decideMatch, linkItem, setMeasure, unlinkItem } from "./api";
  import {
    KIND_LABEL, KIND_TONE, amount, autoLabel, fitLabel, linkKey, measureFraction, measureTarget, measureText, pace, percent, statusLabel, targetLabel,
    type Goal, type LinkKind, type Linkable, type Match, type Measure,
  } from "./goals";
  import LinkPicker from "./LinkPicker.svelte";

  /**
   * Q14 A: one goal's page. The why, the target date and the measures on the left with
   * the items that move it ("Linked · N"); on the right Rika's progress estimate and what
   * Rika found for it, each with Link / Not relevant. Every change answers the updated goal.
   */
  let { goal, onchange, onedit, onopen }: {
    goal: Goal;
    onchange: (goal: Goal) => void;
    onedit: () => void;
    /** Opens a linked item or a find in its own view. */
    onopen: (item: { kind: LinkKind; ref: string; view: string | null }) => void;
  } = $props();

  let busy = $state(false);
  let error = $state("");
  /** Rika's finds show four at a time until asked for all. */
  let allFinds = $state(false);
  const links = $derived(goal.links ?? []);
  const suggestions = $derived((goal.suggestions ?? []).filter((match) => match.status === "new"));
  const linked = $derived(new Set(links.map(linkKey)));
  const target = $derived(targetLabel(goal));
  const onPace = $derived(pace(goal));
  const tone = $derived(goal.status === "done" ? "var(--success)" : goal.status === "dropped" ? "var(--muted)" : goal.status === "paused" ? "var(--warning)" : onPace === "behind" ? "var(--warning)" : "var(--success)");

  /** Runs one change; the answer replaces the goal. False when it failed. */
  async function run(work: () => Promise<Goal>): Promise<boolean> {
    busy = true;
    error = "";
    try {
      onchange(await work());
      return true;
    } catch (cause) {
      error = (cause as Error).message;
      return false;
    } finally {
      busy = false;
    }
  }
  function update(measure: Measure, input: HTMLInputElement) {
    const value = Number(input.value);
    if (!input.value.trim() || !Number.isFinite(value) || value < 0) {
      input.value = amount(measure.current);
      error = `“${measure.label}” needs an amount of 0 or more.`;
      return;
    }
    if (value !== measure.current) void run(() => setMeasure(goal.id, measure.id, value));
  }
  const decide = (match: Match, decision: "link" | "dismiss") => run(() => decideMatch(match.id, decision));
  const link = (item: Linkable) => run(() => linkItem(goal.id, item.kind, item.ref));
</script>

<div class="goal-page">
  <div class="main">
    <header>
      <div class="heading">
        <span class="label">Goal</span>
        <h2>{goal.title}</h2>
      </div>
      <button type="button" class="pill" onclick={onedit}>Edit goal</button>
    </header>
    <div class="facts">
      {#if target}<span class="chip" style:--tone="var(--c2)">{target}</span>{/if}
      <span class="chip" style:--tone={tone}><i class="dot" aria-hidden="true"></i>{goal.status === "active" && onPace ? onPace : statusLabel(goal.status)}</span>
    </div>
    {#if goal.why}
      <p class="why"><span class="label">Why</span>{goal.why}</p>
    {/if}
    {#if error}<p class="error" role="alert">{error}</p>{/if}

    <section class="measures" aria-labelledby={`goal-${goal.id}-measures`}>
      <h3 class="label" id={`goal-${goal.id}-measures`}>Measures</h3>
      {#each goal.measures as measure, index (measure.id)}
        <div class="measure" style:--tone={`var(--c${(index % 6) + 1})`}>
          <span class="name">
            {measure.label}
            {#if measure.auto}<small title={measure.computed ? "Counted from what is linked to this goal" : "Counted from everything since this goal was set"}
                >auto · {measure.computed ? "from linked items" : "since the goal was set"}</small>{/if}
          </span>
          <span class="bar" role="progressbar" aria-label={measure.label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent(measureFraction(measure))}
            aria-valuetext={measureText(measure)}><span style:width={`${percent(measureFraction(measure))}%`}></span></span>
          {#if measure.auto}
            <span class="num" title={autoLabel(measure.auto)}>{measureText(measure)}</span>
          {:else}
            <span class="num">
              <input type="number" min="0" step="any" inputmode="decimal" value={amount(measure.current)} disabled={busy}
                aria-label={`${measure.label}: amount so far`} onchange={(event) => update(measure, event.currentTarget)} />
              <span>/ {measureTarget(measure)}</span>
            </span>
          {/if}
        </div>
      {:else}
        <p class="note">No measures yet. <button type="button" class="text-button" onclick={onedit}>Add one</button> so Rika can track progress.</p>
      {/each}
    </section>

    <section class="linked" aria-labelledby={`goal-${goal.id}-linked`}>
      <div class="section-top">
        <h3 class="label" id={`goal-${goal.id}-linked`}>Linked · {links.length}</h3>
        <LinkPicker {linked} onpick={link} />
      </div>
      {#if links.length}
        <ul>
          {#each links as item (linkKey(item))}
            <li style:--tone={KIND_TONE[item.kind]} class:missing={item.missing}>
              <button type="button" class="open" disabled={item.missing} onclick={() => onopen(item)}>
                <span class="kind">{KIND_LABEL[item.kind] ?? item.kind}</span>
                <b>{item.title}</b>
                <small>{item.missing ? "No longer exists" : item.detail}</small>
              </button>
              <button type="button" class="unlink" aria-label={`Unlink ${item.title}`} title="Unlink" disabled={busy}
                onclick={() => run(() => unlinkItem(goal.id, item.kind, item.ref))}><Icon name="close" size={12} /></button>
            </li>
          {/each}
        </ul>
      {:else}
        <p class="note">Link the notes, tasks, jobs and study sessions that move this goal.</p>
      {/if}
    </section>
  </div>

  <aside class="side" aria-label="Progress and Rika's finds">
    <div class="progress">
      <TimerRing remaining={goal.progress} total={1} phase="focus" size={104} stroke={6} label={`${percent(goal.progress)}% of the way`}>
        <span class="pct" aria-hidden="true">{percent(goal.progress)}%</span>
      </TimerRing>
      <small>{goal.status === "done" ? "Done" : goal.measures.length ? `Rika's estimate from ${goal.measures.length === 1 ? "the measure" : `the ${goal.measures.length} measures`}` : "No measures to estimate from"}</small>
    </div>
    <section class="found" aria-labelledby={`goal-${goal.id}-found`}>
      <h3 class="label" id={`goal-${goal.id}-found`}>Rika found · {suggestions.length}</h3>
      {#each allFinds ? suggestions : suggestions.slice(0, 4) as match (match.id)}
        <article class="match" style:--tone={KIND_TONE[match.kind]} aria-label={match.title}>
          <span class="kind">{KIND_LABEL[match.kind]}{#if match.urgent} · <em>time-sensitive</em>{/if}</span>
          <button type="button" class="title" onclick={() => onopen(match)}>{match.title}</button>
          <small>{[match.detail, fitLabel(match)].filter(Boolean).join(" · ")}</small>
          <div class="acts">
            <button type="button" class="pill primary" disabled={busy} onclick={() => decide(match, "link")}>Link</button>
            <button type="button" class="pill" disabled={busy} onclick={() => decide(match, "dismiss")}>Not relevant</button>
          </div>
        </article>
      {:else}
        <p class="note">Nothing new. Rika keeps looking.</p>
      {/each}
      {#if suggestions.length > 4}
        <button type="button" class="text-button more" aria-expanded={allFinds} onclick={() => (allFinds = !allFinds)}
          >{allFinds ? "Show fewer" : `Show ${suggestions.length - 4} more`}</button>
      {/if}
    </section>
  </aside>
</div>

<style>
  .goal-page {
    display: grid;
    grid-template-columns: minmax(0, 1fr) 280px;
    gap: 24px;
    padding: 18px 22px 22px;
  }
  @container goals-detail (max-width: 720px) {
    .goal-page {
      grid-template-columns: minmax(0, 1fr);
    }
  }
  .main,
  .side {
    display: grid;
    align-content: start;
    gap: 12px;
    min-width: 0;
  }
  header {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 12px;
  }
  .heading {
    display: grid;
    gap: 2px;
    min-width: 0;
  }
  h2 {
    margin: 0;
    font: 600 var(--fs-title, 24px) / 1.2 var(--font-display, var(--font-ui));
    overflow-wrap: anywhere;
  }
  .label {
    margin: 0;
    color: var(--subtle);
    font: 600 var(--fs-label) / 1.3 var(--font-ui);
  }
  .facts {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
  }
  .chip {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 2px 10px;
    border: 1px solid color-mix(in srgb, var(--tone) 50%, transparent);
    border-radius: 999px;
    background: color-mix(in srgb, var(--tone) 12%, transparent);
    color: var(--ink);
    font: 500 11.5px/1.6 var(--font-ui);
  }
  .dot {
    width: 7px;
    height: 7px;
    border-radius: 999px;
    background: var(--tone);
  }
  .why {
    display: grid;
    gap: 2px;
    max-width: 62ch;
    margin: 0;
    color: var(--ink);
    line-height: 1.5;
    white-space: pre-line;
  }
  .measures,
  .linked,
  .found {
    display: grid;
    gap: 8px;
    padding-top: 10px;
    border-top: 1px solid var(--hairline);
  }
  .measure {
    display: grid;
    grid-template-columns: minmax(120px, 230px) minmax(60px, 1fr) auto;
    align-items: center;
    gap: 12px;
    font-size: 12.5px;
  }
  .name {
    display: grid;
    min-width: 0;
  }
  .name small {
    color: var(--subtle);
    font: 500 9.5px/1.5 var(--font-mono);
    letter-spacing: 0.06em;
    text-transform: uppercase;
  }
  .bar {
    height: 5px;
    overflow: hidden;
    border-radius: 999px;
    background: color-mix(in srgb, var(--ink) 10%, transparent);
  }
  .bar span {
    display: block;
    height: 100%;
    border-radius: inherit;
    background: var(--tone);
    transition: width var(--motion-ms, 200ms) ease-out;
  }
  .num {
    display: inline-flex;
    align-items: center;
    justify-content: flex-end;
    gap: 6px;
    min-width: 80px;
    font: 500 12px var(--font-mono);
    white-space: nowrap;
  }
  .num input {
    width: 64px;
    min-height: 26px;
    padding: 2px 6px;
    font: inherit;
    text-align: right;
  }
  .section-top {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
  }
  .section-top :global(.pill) {
    min-height: 26px;
  }
  .linked ul {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
    gap: 0 18px;
    margin: 0;
    padding: 0;
    list-style: none;
  }
  .linked li {
    display: flex;
    align-items: center;
    min-width: 0;
    border-top: 1px solid color-mix(in srgb, var(--hairline) 50%, transparent);
  }
  .open {
    display: flex;
    flex: 1;
    align-items: baseline;
    gap: 8px;
    min-width: 0;
    min-height: 34px;
    padding: 6px 2px;
    border: 0;
    background: transparent;
    color: var(--ink);
    text-align: left;
    white-space: nowrap;
  }
  .open:hover:not(:disabled) b {
    color: var(--accent);
  }
  .missing .open {
    opacity: 0.6;
  }
  .kind {
    flex: none;
    width: 52px;
    color: var(--subtle);
    font: 500 9.5px/1.6 var(--font-mono);
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }
  .open b {
    overflow: hidden;
    font-weight: 500;
    text-overflow: ellipsis;
  }
  .open small,
  .match small,
  .progress small,
  .note {
    overflow: hidden;
    color: var(--muted);
    font-size: 11px;
    text-overflow: ellipsis;
  }
  .unlink {
    display: grid;
    flex: none;
    place-items: center;
    width: 26px;
    height: 26px;
    padding: 0;
    border: 0;
    border-radius: 6px;
    background: transparent;
    color: var(--muted);
    opacity: 0;
  }
  .linked li:hover .unlink,
  .linked li:focus-within .unlink {
    opacity: 1;
  }
  .unlink:hover {
    background: color-mix(in srgb, var(--ink) 8%, transparent);
    color: var(--ink);
  }
  .note {
    margin: 0;
  }
  .progress {
    display: grid;
    justify-items: center;
    gap: 6px;
    text-align: center;
  }
  .pct {
    color: var(--ink);
    font: 600 20px var(--font-mono);
  }
  .match {
    display: grid;
    gap: 3px;
    padding: 9px 10px;
    border: 1px solid var(--hairline);
    border-left: 2px solid var(--tone);
    border-radius: 10px;
    background: color-mix(in srgb, var(--solid-raised, #262626) 55%, transparent);
  }
  .match .kind {
    width: auto;
  }
  .match em {
    color: var(--warning);
    font-style: normal;
  }
  .title {
    padding: 0;
    border: 0;
    background: transparent;
    color: var(--ink);
    font: 600 12.5px/1.35 var(--font-ui);
    text-align: left;
  }
  .title:hover {
    color: var(--accent);
  }
  .more {
    justify-self: start;
  }
  .acts {
    display: flex;
    gap: 6px;
    margin-top: 4px;
  }
  .acts .pill {
    min-height: 26px;
  }
  .error {
    margin: 0;
    color: var(--error);
    font-size: 12px;
  }
  @media (prefers-reduced-motion: reduce) {
    .bar span {
      transition: none;
    }
  }
</style>
