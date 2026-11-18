<script module lang="ts">
  /**
   * `style` sets `--block`, the event's colour (subject, palette slot or calendar): its bar and the tint of its fill.
   * `key` names the event to the calendar's Delete key; `ondelete` is absent for a read-only calendar's event.
   */
  export type PlanEvent = { key: string; title: string; start: number; end: number; column: number; columns: number; style: string;
    picked: boolean; open: (click: MouseEvent) => void; ondelete?: () => void };
  /** `color` is the task's study subject colour; unlinked tasks stay neutral. */
  export type PlanTask = { id: string; title: string; priority: string; done: boolean; at: number | null; color?: string; open: () => void };
</script>

<script lang="ts">
  import { PLAN_END, PLAN_LENGTH, PLAN_START } from "./plan";
  import EventDelete from "./EventDelete.svelte";

  /** Today split: the day's timeline next to its tasks; Plan drops a task into the next free gap. */
  let { now = null, events, tasks, busy = false, onplan, onunplan, ontoggle }: {
    /** Current minute when the day is today. */
    now?: number | null;
    events: PlanEvent[];
    tasks: PlanTask[];
    busy?: boolean;
    onplan: (id: string) => void;
    onunplan: (id: string) => void;
    ontoggle: (id: string) => void;
  } = $props();
  const HOUR = 58;
  const hours = Array.from({ length: (PLAN_END - PLAN_START) / 60 }, (_, i) => PLAN_START / 60 + i);
  const top = (m: number) => ((Math.max(PLAN_START, Math.min(PLAN_END, m)) - PLAN_START) / 60) * HOUR;
  const clock = (m: number) => `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
  const done = $derived(tasks.filter((t) => t.done).length);
  const live = $derived(now !== null && now >= PLAN_START && now <= PLAN_END ? now : null);
</script>

<section class="plan" aria-label="Day plan">
  <div class="timeline" style:height={`${hours.length * HOUR}px`}>
    {#each hours as hr (hr)}
      <span class="hr" class:hidden={live !== null && Math.abs(hr * 60 - live) < 25} style:top={`${top(hr * 60)}px`}>{clock(hr * 60)}</span>
    {/each}
    {#each events as e (e.key)}
      {#if e.end > PLAN_START && e.start < PLAN_END}
        <div class="blk event" class:past={now !== null && e.end < now} class:picked={e.picked} data-event={e.key} style={e.style}
          style:top={`${top(e.start)}px`} style:height={`${Math.max(26, top(e.end) - top(e.start) - 2)}px`}
          style:left={`calc(${(e.column / e.columns) * 100}% + 1px)`} style:width={`calc(${100 / e.columns}% - 4px)`}>
          <button class="open" aria-current={e.picked ? "true" : undefined} onclick={e.open}><strong>{e.title}</strong><span>{clock(e.start)}</span></button>
          {#if e.ondelete}<EventDelete title={e.title} ondelete={e.ondelete} />{/if}
        </div>
      {/if}
    {/each}
    {#each tasks.filter((t) => t.at !== null) as t (t.id)}
      <button class="blk task" style:--task={t.color} style:top={`${top(t.at ?? 0)}px`} style:height={`${(PLAN_LENGTH / 60) * HOUR - 2}px`}
        onclick={t.open}><i class="ring"></i><strong>{t.title}</strong></button>
    {/each}
    {#if live !== null}
      <div class="now" style:top={`${top(live)}px`} aria-hidden="true"><i>{clock(live)}</i></div>
    {/if}
  </div>
  <div class="side">
    <header>
      <h3>Tasks</h3>
      {#if tasks.length}<span>{done} of {tasks.length} done</span>{/if}
    </header>
    <ul class="tasks">
      {#each tasks as t (t.id)}
        <li class:done={t.done}>
          <label>
            <input type="checkbox" checked={t.done} disabled={busy} onchange={() => ontoggle(t.id)} />
            <span>{t.title}</span>
          </label>
          <small>{t.priority === "normal" ? "" : `${t.priority} · `}{PLAN_LENGTH}m</small>
          {#if !t.done}
            <button class="planbtn" class:set={t.at !== null} disabled={busy}
              aria-label={t.at !== null ? `Unplan ${t.title} (${clock(t.at)})` : `Plan ${t.title}`}
              onclick={() => (t.at !== null ? onunplan(t.id) : onplan(t.id))}>{t.at !== null ? clock(t.at) : "Plan"}</button>
          {/if}
        </li>
      {:else}
        <li class="empty">No open tasks for this day.</li>
      {/each}
    </ul>
  </div>
</section>

<style>
  /* The day's hours beside its tasks, in the grid's language: hairlines, soft blocks, one now line. */
  .plan {
    display: grid;
    grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
    align-items: start;
    gap: 32px;
    padding-top: 8px;
  }
  .timeline {
    position: relative;
    margin-left: 60px;
    border-left: 1px solid var(--hairline);
    background-image: linear-gradient(to bottom, var(--hairline) 1px, transparent 1px);
    background-size: 100% 58px;
  }
  .hr,
  .now i {
    position: absolute;
    left: -60px;
    width: 52px;
    font: 400 13px/1 var(--font-ui);
    font-variant-numeric: tabular-nums;
    text-align: right;
    transform: translateY(-50%);
  }
  .hr {
    color: var(--subtle);
  }
  .hr.hidden {
    visibility: hidden;
  }
  .blk {
    position: absolute;
    display: flex;
    align-items: baseline;
    gap: 6px;
    overflow: hidden;
    padding: 4px 8px;
    border: 0;
    border-left: 3px solid var(--block);
    border-radius: 8px;
    background: color-mix(in srgb, var(--block) 22%, var(--canvas));
    color: var(--ink);
    font-size: 13px;
    line-height: 1.3;
    text-align: left;
    white-space: nowrap;
    cursor: pointer;
  }
  .blk strong {
    min-width: 0;
    overflow: hidden;
    font-weight: 600;
    text-overflow: ellipsis;
  }
  .blk span {
    color: var(--muted);
    font-variant-numeric: tabular-nums;
  }
  .blk.past {
    opacity: 0.55;
  }
  /* An event is its opening button plus its x (EventDelete.svelte); selected, it carries a ring. */
  .blk.event {
    align-items: stretch;
    padding: 0;
    cursor: auto;
  }
  .open {
    display: flex;
    flex: 1;
    align-items: baseline;
    gap: 6px;
    min-width: 0;
    padding: 4px 8px;
    overflow: hidden;
    border: 0;
    background: transparent;
    color: inherit;
    font: inherit;
    text-align: left;
    cursor: pointer;
  }
  /* The block clips what lies outside it: the focus ring is drawn inside. */
  .open:focus-visible {
    outline-offset: -2px;
  }
  .blk.picked {
    outline: 2px solid var(--ink);
    outline-offset: -2px;
  }
  .blk.task {
    left: 1px;
    width: calc(100% - 4px);
    align-items: center;
    border-left: 0;
    background: color-mix(in srgb, var(--task, var(--ink)) 12%, var(--canvas));
    animation: drop 240ms ease-out;
  }
  .ring {
    flex: none;
    box-sizing: border-box;
    width: 11px;
    height: 11px;
    border: 1.5px solid var(--task, var(--muted));
    border-radius: 50%;
  }
  @keyframes drop {
    from {
      opacity: 0;
      transform: translateX(10px);
    }
  }
  .now {
    position: absolute;
    left: 0;
    right: 0;
    z-index: 2;
    border-top: 2px solid var(--ink);
    transform: translateY(-1px);
    pointer-events: none;
  }
  .now::before {
    content: "";
    position: absolute;
    left: -5px;
    top: -6px;
    width: 10px;
    height: 10px;
    border-radius: 50%;
    background: var(--ink);
  }
  .now i {
    top: -1px;
    color: var(--ink);
    font-style: normal;
    font-weight: 600;
  }
  header {
    display: flex;
    align-items: baseline;
    gap: 8px;
    padding-bottom: 8px;
    border-bottom: 1px solid var(--hairline);
  }
  h3 {
    margin: 0;
    font: 600 16px/1.3 var(--font-ui);
  }
  header span {
    color: var(--muted);
    font-size: 14px;
  }
  .tasks {
    display: grid;
    margin: 0;
    padding: 0;
    list-style: none;
  }
  li {
    display: flex;
    align-items: center;
    gap: 10px;
    min-height: 44px;
    border-bottom: 1px solid var(--hairline);
  }
  li.empty {
    color: var(--muted);
    font-size: 14px;
  }
  label {
    display: flex;
    flex: 1;
    gap: 10px;
    align-items: center;
    min-width: 0;
    font-size: 15px;
    line-height: 1.3;
  }
  label input {
    flex: none;
    margin: 0;
  }
  li.done label span {
    color: var(--muted);
    text-decoration: line-through;
  }
  small {
    flex: none;
    color: var(--muted);
    font-size: 13px;
  }
  .planbtn {
    flex: none;
    padding: 4px 12px;
    border: 1px solid var(--hairline);
    border-radius: 999px;
    background: transparent;
    color: var(--ink);
    font: 500 13px var(--font-ui);
    font-variant-numeric: tabular-nums;
    cursor: pointer;
  }
  .planbtn:hover:not(:disabled) {
    border-color: var(--muted);
  }
  .planbtn.set {
    border-color: transparent;
    background: color-mix(in srgb, var(--ink) 12%, transparent);
  }
  @media (max-width: 760px) {
    .plan {
      grid-template-columns: minmax(0, 1fr);
    }
  }
  @media (prefers-reduced-motion: reduce) {
    .blk.task {
      animation: none;
    }
  }
</style>
