<script lang="ts">
  import { RETURN_LADDER, daysBetween, formatDay, returnTitle } from "./planning";
  import type { Guide, Workspace } from "./types";

  /** Spaced returns in Due / This week / Later lanes; grading a due return climbs or resets its ladder. */
  let { data, returns, disabled, ongrade, onopen }: {
    data: Workspace;
    returns: Guide[];
    disabled: boolean;
    ongrade: (guide: Guide, result: "recalled" | "shaky") => void;
    onopen: (guide: Guide) => void;
  } = $props();

  const LANES = [
    { label: "Due", test: (days: number) => days <= 0, empty: "Nothing due. Every return is on schedule." },
    { label: "This week", test: (days: number) => days > 0 && days <= 7, empty: "Nothing this week." },
    { label: "Later", test: (days: number) => days > 7, empty: "Nothing later yet." },
  ];
  const colour = (id: number | null) => data.subjects.find((item) => item.id === id)?.color || "var(--accent)";
  const when = (days: number, date: string) => days < 0 ? `${-days}d late` : days === 0 ? "today" : formatDay(date, { day: "numeric", month: "short" });
</script>

<section class="queue" aria-label="Spaced returns">
  {#each LANES as lane, index (lane.label)}
    {@const list = returns.map((guide) => ({ guide, days: daysBetween(data.stats.today, guide.state.next_review) }))
      .filter(({ days }) => lane.test(days)).sort((a, b) => a.days - b.days)}
    <section class="lane" class:due={index === 0} aria-label={lane.label}>
      <header><span class="eyebrow">{lane.label}</span><span class="count">{list.length}</span></header>
      {#each list as { guide, days } (guide.id)}
        <article class="card" style:--tone={colour(guide.subject_id)}>
          <button type="button" class="title" onclick={() => onopen(guide)}><span class="dot"></span>{returnTitle(guide)}</button>
          {#if guide.state.reflection}<p>{guide.state.reflection}</p>{/if}
          <div class="ladder" role="img"
            aria-label={`Rung ${guide.return_step + 1} of ${RETURN_LADDER.length} on the ${RETURN_LADDER.join("·")}-day ladder, next return ${when(days, guide.state.next_review)}`}>
            {#each RETURN_LADDER as gap, rung (gap)}<span class:on={rung <= guide.return_step}></span>{/each}
            <small>{when(days, guide.state.next_review)}</small>
          </div>
          {#if index === 0}
            <div class="actions">
              <button type="button" class="grade primary" {disabled} onclick={() => ongrade(guide, "recalled")}>Recalled</button>
              <button type="button" class="grade" {disabled} onclick={() => ongrade(guide, "shaky")}>Shaky</button>
            </div>
          {/if}
        </article>
      {:else}
        <p class="empty">{lane.empty}</p>
      {/each}
    </section>
  {/each}
</section>

<style>
  .queue {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 12px;
    align-items: start;
  }
  @container (max-width: 640px) {
    .queue {
      grid-template-columns: minmax(0, 1fr);
    }
  }
  .lane {
    display: grid;
    align-content: start;
    gap: 8px;
    min-height: 140px;
    padding: 10px;
    border: 1px solid var(--hairline);
    border-radius: 12px;
    background: color-mix(in srgb, var(--surface-raised) 60%, transparent);
  }
  .lane.due {
    border-color: color-mix(in srgb, var(--accent) 45%, var(--hairline));
  }
  header {
    display: flex;
    align-items: center;
    justify-content: space-between;
  }
  .count {
    color: var(--muted);
    font: 500 10px var(--font-mono);
  }
  .card {
    display: grid;
    gap: 6px;
    padding: 9px 10px;
    border: 1px solid color-mix(in srgb, var(--border) 60%, transparent);
    border-left: 2px solid var(--tone);
    border-radius: 9px;
    background: var(--surface);
    animation: arrive 0.3s ease;
  }
  .title {
    display: flex;
    align-items: center;
    gap: 7px;
    padding: 0;
    background: none;
    color: var(--ink);
    font: 500 12.5px var(--font-ui);
    text-align: left;
  }
  .title:hover {
    text-decoration: underline;
  }
  .dot {
    flex: none;
    width: 7px;
    height: 7px;
    border-radius: 999px;
    background: var(--tone);
  }
  p {
    display: -webkit-box;
    margin: 0;
    overflow: hidden;
    color: var(--muted);
    font-size: 11.5px;
    line-height: 1.4;
    -webkit-box-orient: vertical;
    -webkit-line-clamp: 2;
    line-clamp: 2;
  }
  .ladder {
    display: flex;
    align-items: center;
    gap: 3px;
  }
  .ladder span {
    width: 14px;
    height: 4px;
    border-radius: 999px;
    background: color-mix(in srgb, var(--ink) 12%, transparent);
  }
  .ladder span.on {
    background: var(--tone);
  }
  .ladder small {
    margin-left: auto;
    color: var(--muted);
    font: 500 9.5px var(--font-mono);
  }
  .actions {
    display: flex;
    gap: 6px;
  }
  .grade {
    padding: 3px 10px;
    border: 1px solid var(--border);
    border-radius: 999px;
    background: transparent;
    color: var(--ink);
    font: 500 11.5px var(--font-ui);
  }
  .grade.primary {
    border-color: var(--tone);
    background: color-mix(in srgb, var(--tone) 22%, transparent);
  }
  .empty {
    color: var(--muted);
    font-size: 12px;
  }
  @keyframes arrive {
    from {
      opacity: 0;
      transform: translateY(-4px);
    }
  }
  @media (prefers-reduced-motion: reduce) {
    .card {
      animation: none;
    }
  }
</style>
