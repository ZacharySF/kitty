<script module lang="ts">
  /** `style` sets `--block` / `--block-ink`, the filled colours of the event (subject, palette slot or calendar). */
  export type PlanEvent = { key: string; title: string; start: number; end: number; column: number; columns: number; style: string; open: () => void };
  /** `color` is the task's study subject colour; unlinked tasks use the accent. */
  export type PlanTask = { id: string; title: string; priority: string; done: boolean; at: number | null; color?: string; open: () => void };
</script>

<script lang="ts">
  import { PLAN_END, PLAN_LENGTH, PLAN_START } from "./plan";

  /** Today split: the day's timeline next to its tasks; Plan drops a task into the next free gap. */
  let { eyebrow, now = null, events, tasks, busy = false, onplan, onunplan, ontoggle }: {
    eyebrow: string;
    /** Current minute when the day is today. */
    now?: number | null;
    events: PlanEvent[];
    tasks: PlanTask[];
    busy?: boolean;
    onplan: (id: string) => void;
    onunplan: (id: string) => void;
    ontoggle: (id: string) => void;
  } = $props();
  const HOUR = 44;
  const hours = Array.from({ length: (PLAN_END - PLAN_START) / 60 }, (_, i) => PLAN_START / 60 + i);
  const top = (m: number) => ((Math.max(PLAN_START, Math.min(PLAN_END, m)) - PLAN_START) / 60) * HOUR;
  const clock = (m: number) => `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
  const done = $derived(tasks.filter((t) => t.done).length);
</script>

<section class="plan" aria-label="Day plan">
  <header>
    <div>
      <span class="eyebrow">{eyebrow}</span>
      <h3>Day plan</h3>
    </div>
    <span class="ring" style:--p={tasks.length ? done / tasks.length : 0} role="img" aria-label={`${done} of ${tasks.length} tasks done`}>
      <b>{done}/{tasks.length}</b>
    </span>
  </header>
  <div class="split">
    <div class="timeline" style:height={`${hours.length * HOUR}px`}>
      {#each hours as hr (hr)}<span class="hr" style:top={`${top(hr * 60)}px`}>{String(hr).padStart(2, "0")}</span>{/each}
      {#each events as e (e.key)}
        {#if e.end > PLAN_START && e.start < PLAN_END}
          <button class="blk" class:past={now !== null && e.end < now} style={e.style}
            style:top={`${top(e.start)}px`} style:height={`${Math.max(16, top(e.end) - top(e.start) - 1)}px`}
            style:left={`calc(${(e.column / e.columns) * 100}% + 2px)`} style:width={`calc(${100 / e.columns}% - 3px)`}
            onclick={e.open}>{clock(e.start)} {e.title}</button>
        {/if}
      {/each}
      {#each tasks.filter((t) => t.at !== null) as t (t.id)}
        <button class="blk task" style:--task={t.color} style:top={`${top(t.at ?? 0)}px`} style:height={`${(PLAN_LENGTH / 60) * HOUR - 1}px`}
          onclick={t.open}>☐ {t.title}</button>
      {/each}
      {#if now !== null && now >= PLAN_START && now <= PLAN_END}
        <div class="now" style:top={`${top(now)}px`} aria-hidden="true"><i>{clock(now)}</i></div>
      {/if}
    </div>
    <ul class="tasks">
      {#each tasks as t (t.id)}
        <li class:done={t.done}>
          <label>
            <input type="checkbox" checked={t.done} disabled={busy} onchange={() => ontoggle(t.id)} />
            <span>{t.title}</span>
          </label>
          <div class="meta">
            <small class="prio {t.priority}">{t.priority}</small>
            <small>{PLAN_LENGTH}m</small>
            {#if !t.done}
              <button class="planbtn" class:set={t.at !== null} disabled={busy}
                aria-label={t.at !== null ? `Unplan ${t.title} (${clock(t.at)})` : `Plan ${t.title}`}
                onclick={() => (t.at !== null ? onunplan(t.id) : onplan(t.id))}>{t.at !== null ? `→ ${clock(t.at)}` : "Plan"}</button>
            {/if}
          </div>
        </li>
      {:else}
        <li class="empty">No open tasks for this day.</li>
      {/each}
    </ul>
  </div>
</section>

<style>
  .plan {
    display: grid;
    gap: 10px;
    padding: 12px 14px 4rem;
  }
  header {
    display: flex;
    align-items: center;
    justify-content: space-between;
  }
  header .eyebrow {
    color: var(--violet);
  }
  h3 {
    margin: 0;
    font: 700 1.2rem var(--font-ui);
  }
  .ring {
    display: grid;
    place-items: center;
    width: 40px;
    height: 40px;
    border-radius: 999px;
    background:
      radial-gradient(closest-side, var(--solid-void) 76%, transparent 78%),
      conic-gradient(var(--accent) calc(var(--p) * 360deg), color-mix(in srgb, var(--ink) 10%, transparent) 0);
  }
  .ring b {
    font: 600 0.65rem var(--font-mono);
  }
  .split {
    display: grid;
    grid-template-columns: minmax(0, 1fr) minmax(0, 1.05fr);
    gap: 14px;
  }
  .timeline {
    position: relative;
    margin-left: 26px;
    background-image: linear-gradient(to bottom, color-mix(in srgb, var(--ink) 10%, transparent) 1px, transparent 1px);
    background-size: 100% 44px;
  }
  .hr {
    position: absolute;
    left: -26px;
    color: var(--subtle);
    font: 500 0.6rem var(--font-mono);
    transform: translateY(-40%);
  }
  .blk {
    position: absolute;
    left: 2px;
    width: calc(100% - 3px);
    overflow: hidden;
    padding: 2px 6px;
    border: 0;
    border-radius: 6px;
    background: var(--block);
    color: var(--block-ink);
    font-size: 0.72rem;
    line-height: 1.25;
    text-align: left;
    white-space: nowrap;
    text-overflow: ellipsis;
    cursor: pointer;
  }
  .blk.past {
    opacity: 0.55;
  }
  .blk.task {
    border: 1.5px dashed var(--task, var(--accent));
    background: color-mix(in srgb, var(--task, var(--accent)) 12%, transparent);
    color: var(--ink);
    animation: drop 240ms ease-out;
  }
  @keyframes drop {
    from {
      opacity: 0;
      transform: translateX(10px);
    }
  }
  .now {
    position: absolute;
    left: -4px;
    right: 0;
    border-top: 1.5px dashed var(--c1);
  }
  .now i {
    position: absolute;
    right: 0;
    top: -13px;
    color: var(--c1);
    font: 600 0.6rem var(--font-mono);
    font-style: normal;
  }
  .tasks {
    display: grid;
    align-content: start;
    gap: 6px;
    margin: 0;
    padding: 0;
    list-style: none;
  }
  li {
    display: grid;
    gap: 3px;
    padding: 7px 9px;
    border: 1px solid var(--hairline);
    border-left: 2px solid var(--accent);
    border-radius: 8px;
    background: color-mix(in srgb, var(--solid-panel) 55%, transparent);
  }
  li.empty {
    border-left-color: var(--hairline);
    color: var(--muted);
    font-size: 0.8rem;
  }
  label {
    display: flex;
    gap: 7px;
    align-items: flex-start;
    font-size: 0.82rem;
    line-height: 1.3;
  }
  label input {
    flex: none;
    margin: 2px 0 0;
  }
  li.done label span {
    color: var(--muted);
    text-decoration: line-through;
  }
  .meta {
    display: flex;
    align-items: center;
    gap: 6px;
    padding-left: 20px;
  }
  .meta small {
    color: var(--subtle);
    font: 500 0.6rem var(--font-mono);
    letter-spacing: 0.06em;
    text-transform: uppercase;
  }
  .prio.high {
    color: var(--warning);
  }
  .planbtn {
    margin-left: auto;
    padding: 1px 9px;
    border: 1px solid var(--hairline);
    border-radius: 999px;
    background: transparent;
    color: var(--ink);
    font: 500 0.62rem var(--font-mono);
    letter-spacing: 0.08em;
    text-transform: uppercase;
    cursor: pointer;
  }
  .planbtn.set {
    border-color: var(--accent);
    color: var(--accent);
  }
  @media (prefers-reduced-motion: reduce) {
    .blk.task {
      animation: none;
    }
  }
</style>
