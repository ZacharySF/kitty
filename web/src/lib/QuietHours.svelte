<script lang="ts">
  import { Panel } from "@nunera/bracket";
  import { beginDrag } from "./pointerDrag";
  import { notifier } from "./notifications.svelte";
  import { QUIET_EVENT, clock, digestLine, inQuietHours, minuteOfDay, quietSpan, readQuiet, writeQuiet, type QuietHours } from "./notify";

  let quiet = $state<QuietHours>(readQuiet(localStorage));
  let error = $state("");
  let dial = $state<SVGSVGElement>();
  let now = $state(minuteOfDay(new Date()));
  const R = 70;
  const C = 92;
  const STEP = 15;
  const HANDLES = ["from", "to"] as const;
  const span = $derived(quietSpan(quiet));
  const quietNow = $derived(inQuietHours(quiet, now));
  const point = (minute: number, radius = R) => {
    const angle = ((minute / 1440) * 360 - 90) * (Math.PI / 180);
    return { x: C + Math.cos(angle) * radius, y: C + Math.sin(angle) * radius };
  };
  const arc = $derived.by(() => {
    const a = point(quiet.from);
    const b = point(quiet.to);
    return `M${a.x},${a.y} A${R},${R} 0 ${span > 720 ? 1 : 0} 1 ${b.x},${b.y}`;
  });
  const duration = (minutes: number) => `${Math.floor(minutes / 60)}h${minutes % 60 ? ` ${minutes % 60}m` : ""}`;

  $effect(() => {
    const timer = setInterval(() => (now = minuteOfDay(new Date())), 30_000);
    return () => clearInterval(timer);
  });

  function save(next: Partial<QuietHours>) {
    quiet = { ...quiet, ...next };
    try {
      writeQuiet(localStorage, quiet);
      error = "";
    } catch {
      error = "This browser could not save local preferences.";
    }
    window.dispatchEvent(new CustomEvent(QUIET_EVENT));
  }
  function grab(event: PointerEvent, which: "from" | "to") {
    event.preventDefault();
    (event.currentTarget as SVGElement).focus();
    const set = (x: number, y: number) => {
      const box = dial!.getBoundingClientRect();
      const px = ((x - box.left) / box.width) * 184 - C;
      const py = ((y - box.top) / box.height) * 184 - C;
      let degrees = (Math.atan2(py, px) * 180) / Math.PI + 90;
      if (degrees < 0) degrees += 360;
      save({ [which]: (Math.round((degrees / 360) * 1440 / STEP) * STEP) % 1440 });
    };
    beginDrag(event, { start: set, move: set, drop: () => {} });
  }
  function nudge(event: KeyboardEvent, which: "from" | "to") {
    const delta = { ArrowRight: STEP, ArrowUp: STEP, ArrowLeft: -STEP, ArrowDown: -STEP, PageUp: 60, PageDown: -60 }[event.key];
    if (!delta) return;
    event.preventDefault();
    save({ [which]: (quiet[which] + delta + 1440) % 1440 });
  }
</script>

{#snippet toggle(on: boolean, label: string, hint: string, set: (value: boolean) => void)}
  <div class="switch-row">
    <span><strong>{label}</strong><small>{hint}</small></span>
    <button class="switch" role="switch" aria-checked={on} aria-label={label} onclick={() => set(!on)}><i></i></button>
  </div>
{/snippet}

<Panel title="Quiet hours" aria-label="Quiet hours">
  <div class="quiet" class:off={!quiet.enabled}>
    <div class="dial-wrap" title="Drag either end, or use the arrow keys">
      <svg viewBox="0 0 184 184" class="dial" bind:this={dial} role="group" aria-label={`Quiet hours ${clock(quiet.from)} to ${clock(quiet.to)}`}>
        <circle cx={C} cy={C} r={R} class="track" />
        {#each Array.from({ length: 24 }, (_, hour) => hour) as hour}
          {@const a = point(hour * 60, R + 11)}
          {@const b = point(hour * 60, R + (hour % 6 ? 14 : 17))}
          <line x1={a.x} y1={a.y} x2={b.x} y2={b.y} class="tick" class:major={hour % 6 === 0} />
        {/each}
        {#each [0, 6, 12, 18] as hour}
          {@const p = point(hour * 60, R - 14)}
          <text x={p.x} y={p.y + 3} class="hour">{String(hour).padStart(2, "0")}</text>
        {/each}
        <path d={arc} class="arc" />
        {#each [point(now)] as p}<circle cx={p.x} cy={p.y} r="3" class="now"><title>Now, {clock(now)}</title></circle>{/each}
        <g transform={`translate(${C} ${C - 16})`} class="moon" aria-hidden="true"><path d="M4 -7 A8 8 0 1 0 6 6 A6.5 6.5 0 1 1 4 -7Z" /></g>
        <text x={C} y={C + 8} class="span">{duration(span)}</text>
        <text x={C} y={C + 20} class="sub">{clock(quiet.from)}–{clock(quiet.to)}</text>
        {#each HANDLES as which}
          {@const p = point(quiet[which])}
          <circle
            cx={p.x}
            cy={p.y}
            r="8"
            class="handle"
            role="slider"
            tabindex="0"
            aria-label={which === "from" ? "Quiet from" : "Quiet until"}
            aria-valuemin={0}
            aria-valuemax={1440 - STEP}
            aria-valuenow={quiet[which]}
            aria-valuetext={clock(quiet[which])}
            onpointerdown={(event) => grab(event, which)}
            onkeydown={(event) => nudge(event, which)}
          />
        {/each}
      </svg>
    </div>
    <div class="side">
      <h3>{quiet.enabled ? `Hold notifications from ${clock(quiet.from)} until ${clock(quiet.to)}` : "Notifications arrive any time"}</h3>
      {@render toggle(quiet.enabled, "Quiet hours", quietNow ? "Quiet right now" : "Toasts wait inside the window", (value) => save({ enabled: value }))}
      {@render toggle(quiet.letCalendar, "Let calendar events through", "Calendar reminders still ring", (value) => save({ letCalendar: value }))}
      {@render toggle(quiet.digest, "Morning digest", `Held items arrive at ${clock(quiet.to)}`, (value) => save({ digest: value }))}
      <p class="held" role="status" title="kitty checks reminders only while it runs. In the desktop app, Keep running on close (Settings → Desktop) keeps checking from the system tray.">
        {notifier.heldNotices.length ? `Held now: ${digestLine(notifier.heldNotices)}` : "Nothing held right now"}
      </p>
      {#if error}<p role="alert">{error}</p>{/if}
    </div>
  </div>
</Panel>

<style>
  .quiet {
    display: grid;
    grid-template-columns: 190px minmax(0, 1fr);
    gap: 18px;
    align-items: center;
  }
  .dial-wrap {
    display: grid;
    justify-items: center;
    gap: 2px;
  }
  .dial {
    width: 184px;
    height: 184px;
    touch-action: none;
  }
  .off .arc {
    opacity: 0.35;
  }
  .track {
    fill: none;
    stroke: color-mix(in srgb, var(--ink) 8%, transparent);
    stroke-width: 12;
  }
  .tick {
    stroke: var(--hairline);
    stroke-width: 1;
  }
  .tick.major {
    stroke: var(--muted);
  }
  .hour {
    fill: var(--subtle);
    font: 8px var(--font-mono);
    text-anchor: middle;
  }
  .arc {
    fill: none;
    stroke: color-mix(in srgb, var(--c3, var(--violet)) 75%, transparent);
    stroke-width: 12;
    stroke-linecap: round;
    filter: drop-shadow(0 0 6px color-mix(in srgb, var(--c3, var(--violet)) 55%, transparent));
  }
  .now {
    fill: var(--warning);
  }
  .moon path {
    fill: var(--c3, var(--violet));
    opacity: 0.85;
  }
  .span {
    fill: var(--ink);
    font: 700 16px var(--font-ui);
    text-anchor: middle;
  }
  .sub {
    fill: var(--muted);
    font: 8.5px var(--font-mono);
    letter-spacing: 0.08em;
    text-anchor: middle;
  }
  .handle {
    fill: var(--ink);
    stroke: var(--c3, var(--violet));
    stroke-width: 3;
    cursor: grab;
    outline: none;
  }
  .handle:focus-visible {
    stroke: var(--violet);
    stroke-width: 4;
  }
  .side {
    display: grid;
    gap: 6px;
  }
  h3 {
    margin: 0 0 4px;
    color: var(--ink);
    font: 600 15px var(--font-ui);
  }
  .switch-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    padding: 6px 0;
    border-top: 1px solid color-mix(in srgb, var(--hairline) 60%, transparent);
  }
  .switch-row span {
    display: grid;
  }
  .switch-row strong {
    color: var(--ink);
    font: 500 13px var(--font-ui);
  }
  .switch-row small {
    color: var(--muted);
    font-size: 11.5px;
  }
  .switch {
    position: relative;
    flex-shrink: 0;
    width: 34px;
    height: 20px;
    padding: 0;
    border: 1px solid var(--hairline);
    border-radius: 999px;
    background: color-mix(in srgb, var(--ink) 8%, transparent);
    cursor: pointer;
    transition: background-color 160ms;
  }
  .switch i {
    position: absolute;
    top: 2px;
    left: 2px;
    width: 14px;
    height: 14px;
    border-radius: 999px;
    background: var(--muted);
    transition: transform 160ms, background-color 160ms;
  }
  .switch[aria-checked="true"] {
    background: color-mix(in srgb, var(--c3, var(--violet)) 45%, transparent);
  }
  .switch[aria-checked="true"] i {
    transform: translateX(14px);
    background: var(--ink);
  }
  .held {
    margin: 4px 0 0;
    padding-top: 8px;
    border-top: 1px solid color-mix(in srgb, var(--hairline) 60%, transparent);
    color: var(--muted);
    font-size: 12px;
    cursor: help;
  }
  @media (max-width: 620px) {
    .quiet {
      grid-template-columns: minmax(0, 1fr);
    }
  }
  @media (prefers-reduced-motion: reduce) {
    .switch,
    .switch i {
      transition: none;
    }
  }
</style>
