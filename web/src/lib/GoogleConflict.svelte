<script lang="ts" module>
  /** The event fields a conflict compares (a subset of Calendar's Event). */
  export type ConflictEvent = {
    title: string;
    start: string;
    end: string;
    all_day: boolean;
    timezone: string;
    location: string;
    description: string;
    reminders: number[];
    rrule: string;
    revision?: number;
    deleted?: boolean;
  };
  export type Conflict = { id: number; local: ConflictEvent; remote: ConflictEvent | null };
</script>

<script lang="ts">
  /**
   * Q62: both versions drawn on the day grid in the event's calendar paint (`fill`, with
   * readable `ink`), kitty's as a solid block and Google's as a dashed ghost. Clicking a
   * block picks it; the list strikes out what the other choice would lose.
   */
  let { conflict, fill, ink, disabled, onresolve }: {
    conflict: Conflict;
    fill: string;
    ink: string;
    disabled: boolean;
    onresolve: (choice: "local" | "remote" | "duplicate") => void;
  } = $props();
  type Side = "local" | "remote";
  let choice = $state<Side | null>(null);
  const SIDES: Side[] = ["local", "remote"];
  const event = (side: Side) => (side === "local" ? (conflict.local.deleted ? null : conflict.local) : conflict.remote);
  const minutes = (value: string) => {
    const date = new Date(value);
    return date.getHours() * 60 + date.getMinutes();
  };
  const hhmm = (value: string) => new Date(value).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  const day = (value: string) =>
    new Date(value.length === 10 ? `${value}T12:00:00` : value).toLocaleDateString([], { weekday: "short", day: "numeric", month: "short" });
  const when = (e: ConflictEvent) => (e.all_day ? `${day(e.start)} · all day` : `${day(e.start)} · ${hhmm(e.start)}–${hhmm(e.end)}`);
  /** Shared hour axis around both versions' times of day. */
  const axis = $derived.by(() => {
    const timed = SIDES.map(event).filter((e): e is ConflictEvent => !!e && !e.all_day);
    const from = Math.min(...timed.map(e => minutes(e.start)), 9 * 60);
    const to = Math.max(...timed.map(e => Math.max(minutes(e.end), minutes(e.start) + 30)), from + 3 * 60);
    const first = Math.max(0, Math.floor(from / 60) - 1);
    const last = Math.min(24, Math.ceil(to / 60) + 1);
    return { first, last, hours: Array.from({ length: last - first }, (_, i) => first + i) };
  });
  const top = (m: number) => `${((m / 60 - axis.first) / (axis.last - axis.first)) * 100}%`;
  const height = (e: ConflictEvent) => {
    const start = minutes(e.start);
    const end = Math.max(new Date(e.end).toDateString() === new Date(e.start).toDateString() ? minutes(e.end) : 24 * 60, start + 15);
    return `${((end - start) / 60 / (axis.last - axis.first)) * 100}%`;
  };
  const FIELDS: { label: string; show: (e: ConflictEvent) => string }[] = [
    { label: "Title", show: e => e.title },
    { label: "Time", show: when },
    { label: "Where", show: e => e.location || "—" },
    { label: "Notes", show: e => e.description || "—" },
    { label: "Reminders", show: e => (e.reminders.length ? e.reminders.map(m => `${m} min`).join(", ") : "none") },
    { label: "Repeats", show: e => e.rrule || "no" },
  ];
  const changes = $derived.by(() => {
    const [mine, theirs] = [event("local"), event("remote")];
    if (!mine || !theirs) return [{ label: "Event", local: mine ? when(mine) : "Deleted in rika!", remote: theirs ? when(theirs) : "Deleted on Google" }];
    return FIELDS.map(f => ({ label: f.label, local: f.show(mine), remote: f.show(theirs) })).filter(f => f.local !== f.remote);
  });
  const name = (side: Side) => (side === "local" ? "rika" : "Google");
</script>

<article class="ghost" aria-label={`Conflict: ${conflict.local.title}`} style:--block={fill} style:--block-ink={ink}>
  <div class="day" role="group" aria-label="Both versions on the day">
    {#each axis.hours as hour}<span class="hr" style:top={top(hour * 60)}><b>{String(hour).padStart(2, "0")}:00</b></span>{/each}
    {#each SIDES as side}
      {@const e = event(side)}
      <button
        class="blk"
        data-side={side}
        class:on={choice === side}
        class:off={choice !== null && choice !== side}
        class:gone={!e}
        style:top={e && !e.all_day ? top(minutes(e.start)) : "0"}
        style:height={e && !e.all_day ? height(e) : "100%"}
        aria-pressed={choice === side}
        aria-label={`${name(side)}'s version: ${e ? `${e.title}, ${when(e)}` : "deleted"}`}
        onclick={() => (choice = side)}
      >
        <small>{name(side)}</small>
        {#if e}<strong>{e.title}</strong><span>{when(e)}{e.location ? ` · ${e.location}` : ""}</span>
        {:else}<strong>Deleted {side === "local" ? "in rika!" : "on Google"}</strong>{/if}
      </button>
    {/each}
  </div>
  <div class="side">
    <span class="eyebrow">conflict · {day(conflict.local.start)}</span>
    <h4>{choice ? `Keeping ${name(choice)}'s version` : "Click the version to keep"}</h4>
    <ul class="changes" aria-label="What changes">
      {#each changes as change (change.label)}
        <li>
          <span>{change.label}</span>
          <s class:lost={choice === "remote"} data-side="local" aria-label={`kitty: ${change.local}${choice === "remote" ? ", lost" : ""}`}>{change.local}</s>
          <b class:lost={choice === "local"} data-side="remote" aria-label={`Google: ${change.remote}${choice === "local" ? ", lost" : ""}`}>{change.remote}</b>
        </li>
      {:else}
        <li class="same">The versions differ only in details rika! does not show here.</li>
      {/each}
    </ul>
    <div class="acts">
      <button class="pill primary" disabled={!choice || disabled} onclick={() => choice && onresolve(choice)}
        >{choice === "local" ? "Keep rika!'s" : choice === "remote" ? "Use Google's" : "Pick a block"}</button
      >
      <button class="linkish" disabled={disabled || !conflict.remote} onclick={() => onresolve("duplicate")}>Keep both (save rika!'s as a copy)</button>
    </div>
  </div>
</article>

<style>
  .ghost {
    display: grid;
    grid-template-columns: minmax(0, 1.25fr) minmax(0, 1fr);
    gap: 14px;
    padding: 14px;
    border: 1px solid var(--hairline, var(--border));
    border-radius: 14px;
  }
  .day {
    position: relative;
    height: 260px;
    margin-left: 40px;
    border-left: 1px solid var(--hairline, var(--border));
  }
  .hr {
    position: absolute;
    left: 0;
    right: 0;
    border-top: 1px solid color-mix(in srgb, var(--hairline, var(--border)) 60%, transparent);
  }
  .hr b {
    position: absolute;
    left: -40px;
    top: -7px;
    color: var(--subtle);
    font: 400 10px var(--font-mono);
  }
  .blk {
    position: absolute;
    display: grid;
    align-content: start;
    gap: 1px;
    padding: 6px 8px;
    overflow: hidden;
    border-radius: 9px;
    color: var(--ink);
    text-align: left;
    cursor: pointer;
    transition: opacity 200ms, box-shadow 200ms;
  }
  /* rika's version reads like the calendar's own filled block; Google's is its dashed ghost. */
  .blk[data-side="local"] {
    left: 6px;
    right: calc(50% + 2px);
    border: 1px solid var(--block);
    background: var(--block);
    color: var(--block-ink);
  }
  .blk[data-side="remote"] {
    left: calc(50% + 2px);
    right: 6px;
    border: 1.5px dashed var(--block);
    background: color-mix(in srgb, var(--block) 12%, transparent);
  }
  .blk.gone {
    border: 1.5px dashed color-mix(in srgb, var(--block) 70%, transparent);
    background: transparent;
    color: var(--muted);
  }
  .blk.on {
    z-index: 1;
    box-shadow: 0 0 0 2px var(--br-lineHot), 0 6px 16px color-mix(in srgb, var(--block) 35%, transparent);
  }
  .blk.on[data-side="remote"]:not(.gone) {
    border-style: solid;
    background: var(--block);
    color: var(--block-ink);
  }
  .blk.off {
    opacity: 0.3;
  }
  .blk small {
    font: 9px var(--font-mono);
    letter-spacing: 0.12em;
    text-transform: uppercase;
    opacity: 0.72;
  }
  .blk strong {
    font: 600 12px var(--font-ui);
  }
  .blk span {
    font-size: 11px;
    opacity: 0.72;
  }
  .side {
    display: grid;
    align-content: start;
    gap: 8px;
  }
  .eyebrow {
    color: var(--violet);
  }
  h4 {
    margin: 0;
    color: var(--ink);
    font: 600 15px var(--font-ui);
  }
  .changes {
    display: grid;
    gap: 6px;
    margin: 4px 0;
    padding: 0;
    list-style: none;
  }
  .changes li {
    display: grid;
    grid-template-columns: 74px 1fr;
    gap: 0 8px;
    font-size: 12px;
  }
  .changes li > span {
    grid-row: span 2;
    color: var(--subtle);
    font: 10px var(--font-mono);
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }
  .changes s,
  .changes b {
    padding-left: 6px;
    color: var(--ink);
    font-weight: 500;
    text-decoration: none;
  }
  .changes s {
    border-left: 2px solid var(--block);
  }
  .changes b {
    border-left: 2px dashed var(--block);
  }
  .changes .lost {
    opacity: 0.45;
    text-decoration: line-through;
  }
  .changes .same {
    display: block;
    color: var(--muted);
  }
  .acts {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 10px;
  }
  .acts .pill {
    min-height: 30px;
    padding: 3px 14px;
    font-size: 10.5px;
  }
  .acts .pill:disabled {
    opacity: 0.5;
  }
  .linkish {
    border: 0;
    background: none;
    color: var(--muted);
    font-size: 12px;
    text-decoration: underline;
    cursor: pointer;
  }
  .linkish:disabled {
    cursor: default;
    opacity: 0.5;
  }
  @media (max-width: 760px) {
    .ghost {
      grid-template-columns: minmax(0, 1fr);
    }
  }
  @media (prefers-reduced-motion: reduce) {
    .blk {
      transition: none;
    }
  }
</style>
