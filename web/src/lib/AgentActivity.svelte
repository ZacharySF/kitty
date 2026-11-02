<script lang="ts">
  import { onMount } from "svelte";
  import { api } from "./api";
  import TernPanes from "./TernPanes.svelte";
  import type { AgentState, TerminalActivity } from "./terminal";

  /**
   * Q56: one live card per herdr agent (task, last output line, a 30-second output
   * sparkline, a running timer, Open pane when blocked). Reading never starts herdr.
   */
  let { onopen }: { /** Show the terminal once herdr has focused a pane. */ onopen: () => void } = $props();

  const POLL_MS = 3000;
  /** Revision samples covering the last 30 seconds of polls. */
  const SAMPLES = 30_000 / POLL_MS + 1;
  const TONE: Record<AgentState, string> = {
    working: "var(--accent)",
    blocked: "var(--warning)",
    done: "var(--success)",
    idle: "var(--muted)",
    unknown: "var(--subtle)",
  };
  let activity = $state<TerminalActivity | null>(null);
  let error = $state("");
  let now = $state(Date.now());
  /** Each pane's output revision at the last SAMPLES polls, oldest first. */
  let revisions = $state<Record<string, number[]>>({});

  const agents = $derived(
    (activity?.workspaces ?? []).flatMap((space) =>
      space.agents.map((agent) => ({ ...agent, space: space.label, focused: space.focused })),
    ),
  );

  async function load() {
    try {
      const next = await api<TerminalActivity>("/terminal/activity");
      const panes = next.workspaces.flatMap((space) => space.agents);
      revisions = Object.fromEntries(
        panes.map((agent) => [agent.pane, [...(revisions[agent.pane] ?? []), agent.revision].slice(-SAMPLES)]),
      );
      activity = next;
      error = "";
    } catch (cause) {
      error = (cause as Error).message;
    }
  }

  async function openPane(pane: string) {
    try {
      await api("/terminal/focus", { pane });
      onopen();
    } catch (cause) {
      error = (cause as Error).message;
    }
  }

  /** Output per poll interval (revision steps), padded to a full 30 seconds. */
  function sparkPath(samples: number[], close: boolean): string {
    const steps = samples.slice(1).map((value, index) => Math.max(0, value - samples[index]));
    const values = [...Array(SAMPLES - 1 - steps.length).fill(0), ...steps];
    const peak = Math.max(4, ...values);
    const points = values.map(
      (value, index) => `${((index / (values.length - 1)) * 160).toFixed(1)},${(28 - (value / peak) * 26).toFixed(1)}`,
    );
    return `M${points.join(" L")}${close ? " L160,30 L0,30 Z" : ""}`;
  }

  const clock = (ms: number) => {
    const seconds = Math.max(0, Math.floor(ms / 1000));
    const minutes = Math.floor(seconds / 60);
    return minutes >= 60
      ? `${Math.floor(minutes / 60)}:${String(minutes % 60).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`
      : `${minutes}:${String(seconds % 60).padStart(2, "0")}`;
  };

  onMount(() => {
    void load();
    const poll = window.setInterval(() => {
      if (!document.hidden) void load();
    }, POLL_MS);
    const tick = window.setInterval(() => (now = Date.now()), 1000);
    return () => {
      window.clearInterval(poll);
      window.clearInterval(tick);
    };
  });
</script>

<div class="activity" role="region" aria-label="Agent activity">
  {#if error}
    <p class="activity-note" role="alert">{error}</p>
  {:else if !activity}
    <p class="activity-note" role="status">Reading herdr's agent states…</p>
  {:else if !activity.running}
    <p class="activity-note" role="status">kitty!'s herdr session is not running. Open the terminal to start it.</p>
  {:else if !agents.length}
    <p class="activity-note" role="status">No agents are running in herdr yet.</p>
  {:else}
    <div class="cards">
      {#each agents as agent (agent.pane)}
        {@const running = agent.status === "working" || agent.status === "blocked"}
        <article
          class="card"
          data-state={agent.status}
          style:--tone={TONE[agent.status]}
          aria-label={`${agent.name} · ${agent.status}`}
        >
          <header>
            <strong>{agent.name}</strong>
            <span class="space"
              >{agent.space}{#if agent.focused}<em title="Focused workspace" aria-label="focused">◉</em>{/if}</span
            >
            <span class="chip"><i class="dot" class:pulse={agent.status === "working"}></i>{agent.status}</span>
          </header>
          <p class="task">{agent.task || "No task title yet"}</p>
          <svg
            class="spark"
            viewBox="0 0 160 30"
            preserveAspectRatio="none"
            role="img"
            aria-label={`${agent.name} output over the last 30 seconds`}
          >
            <path class="fill" d={sparkPath(revisions[agent.pane] ?? [], true)} />
            <path class="line" d={sparkPath(revisions[agent.pane] ?? [], false)} />
          </svg>
          <footer>
            <code title={agent.line}>{agent.line || "No output yet"}</code>
            {#if running}<time aria-label={`${agent.status} for ${clock(now - agent.since)}`}
                >{clock(now - agent.since)}</time
              >{:else}<span class="time-off" aria-hidden="true">—</span>{/if}
          </footer>
          {#if agent.status === "blocked"}<div class="ask">
              <button class="pill primary" onclick={() => openPane(agent.pane)}>Open pane</button>
            </div>{/if}
        </article>
      {/each}
    </div>
  {/if}
  <TernPanes />
</div>

<style>
  .activity {
    height: 100%;
    overflow: auto;
    padding: 8px 4px;
  }
  .cards {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(196px, 1fr));
    gap: 10px;
    padding: 4px 6px 10px;
  }
  .card {
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    align-content: start;
    gap: 8px;
    min-width: 0;
    padding: 11px 12px 10px;
    border: 1px solid var(--hairline);
    border-top: 2px solid color-mix(in srgb, var(--tone) 70%, transparent);
    border-radius: 12px;
    background: color-mix(in srgb, var(--solid-raised) 55%, transparent);
    transition: border-color 300ms;
  }
  .card[data-state="blocked"] {
    border-color: color-mix(in srgb, var(--tone) 45%, var(--hairline));
    box-shadow: 0 0 22px color-mix(in srgb, var(--tone) 12%, transparent);
  }
  header {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    align-items: center;
    gap: 1px 8px;
  }
  header strong {
    overflow: hidden;
    color: var(--ink);
    font: 600 14px var(--font-ui);
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .chip {
    grid-row: 1 / 3;
    grid-column: 2;
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 2px 8px;
    border: 1px solid color-mix(in srgb, var(--tone) 40%, transparent);
    border-radius: 999px;
    color: var(--tone);
    font: 500 9.5px var(--font-mono);
    letter-spacing: 0.12em;
    text-transform: uppercase;
  }
  .dot {
    width: 7px;
    height: 7px;
    border-radius: 999px;
    background: var(--tone);
    box-shadow: 0 0 6px color-mix(in srgb, var(--tone) 70%, transparent);
  }
  .dot.pulse {
    animation: pulse 1.6s ease-in-out infinite;
  }
  @keyframes pulse {
    50% {
      box-shadow: 0 0 0 4px color-mix(in srgb, var(--tone) 18%, transparent);
    }
  }
  .space {
    overflow: hidden;
    color: var(--subtle);
    font: 10px var(--font-mono);
    letter-spacing: 0.08em;
    text-overflow: ellipsis;
    text-transform: uppercase;
    white-space: nowrap;
  }
  .space em {
    margin-left: 4px;
    color: var(--violet);
    font-style: normal;
  }
  .task {
    display: -webkit-box;
    min-height: 2.7em;
    margin: 0;
    overflow: hidden;
    color: var(--ink);
    font-size: 12.5px;
    line-height: 1.35;
    -webkit-box-orient: vertical;
    -webkit-line-clamp: 2;
    line-clamp: 2;
  }
  .spark {
    width: 100%;
    height: 30px;
    overflow: visible;
  }
  .spark .fill {
    fill: color-mix(in srgb, var(--tone) 18%, transparent);
  }
  .spark .line {
    fill: none;
    stroke: var(--tone);
    stroke-width: 1.4;
    vector-effect: non-scaling-stroke;
  }
  footer {
    display: flex;
    align-items: baseline;
    gap: 8px;
  }
  code {
    flex: 1;
    min-width: 0;
    overflow: hidden;
    color: var(--muted);
    font: 10.5px var(--font-mono);
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  time,
  .time-off {
    color: var(--ink);
    font: 500 11px var(--font-mono);
    font-variant-numeric: tabular-nums;
  }
  .time-off {
    color: var(--subtle);
  }
  .ask {
    display: flex;
    gap: 6px;
  }
  .ask .pill {
    min-height: 26px;
    padding: 3px 11px;
    font-size: 10.5px;
  }
  .activity-note {
    margin: 0;
    color: var(--muted);
    font: 11px var(--font-mono);
    letter-spacing: 0.06em;
  }
  @media (prefers-reduced-motion: reduce) {
    .dot.pulse {
      animation: none;
    }
    .card {
      transition: none;
    }
  }
</style>
