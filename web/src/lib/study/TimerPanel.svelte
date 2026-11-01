<script lang="ts">
  import { Button } from "@nunera/bracket";
  import TimerRing from "../TimerRing.svelte";
  import { minutesLabel, phaseLabel, type Workspace } from "./types";

  /** Cockpit centre: the session focus, the live ring and the session controls. */
  let { data, targetId = $bindable(), subjectId, projectId, remaining, clockLabel, phaseTotal, disabled, ontimer }: {
    data: Workspace;
    targetId: string;
    subjectId: string;
    projectId: string;
    remaining: number;
    clockLabel: string;
    phaseTotal: number;
    disabled: boolean;
    ontimer: (action: string) => void;
  } = $props();

  const timer = $derived(data.timer);
  const subject = $derived(data.subjects.find((item) => String(item.id) === subjectId));
  const concept = $derived(data.projects.find((item) => String(item.id) === projectId));
  const mood = $derived(timer.status === "idle" ? "Ready when you are" : timer.status === "paused" ? "Paused"
    : timer.phase === "priming" ? "Preview the gist and prepare your plan" : timer.phase === "focus" ? "One thing at a time" : "Step away and recharge");
</script>

<section class="column timer-card" class:break-phase={timer.phase !== "focus"} aria-label="Session timer">
  <div class="focus-line">
    <span class="chip"><span class="dot" style:background={subject?.color || "var(--subtle)"} aria-hidden="true"></span>{subject?.name || "Open study"}{#if concept}<span class="concept">· {concept.name}</span>{/if}</span>
    <span class="cycle">{timer.cycle} focus {timer.cycle === 1 ? "session" : "sessions"} completed</span>
  </div>
  <div class="phase-label"><span class="phase-dot"></span>{phaseLabel(timer.phase)}<span class="muted">· {mood}</span></div>
  <TimerRing {remaining} total={phaseTotal} phase={timer.phase} rounds={timer.cycle}
    label={`${phaseLabel(timer.phase)}, ${clockLabel.replace(/^0/, "")} left of ${Math.floor(phaseTotal / 60)}:${(phaseTotal % 60).toString().padStart(2, "0")}`}
  ><div class="clock">{clockLabel}</div></TimerRing>
  <p class="rhythm">5 min priming <span>→</span> {minutesLabel(data.settings.focus_seconds)} focus <span>→</span> {minutesLabel(data.settings.short_break_seconds)} break <span>→</span> repeat</p>
  <div class="actions">
    {#if timer.status === "running"}<Button {disabled} onclick={() => ontimer("pause")}>Pause <kbd>Space</kbd></Button>
    {:else if timer.status === "paused"}<Button {disabled} onclick={() => ontimer("resume")}>Resume <kbd>Space</kbd></Button>
    {:else}<Button variant="solid" intent="primary" {disabled} onclick={() => ontimer("start")}>Start session · 5 min priming <kbd>Space</kbd></Button>{/if}
    <Button disabled={disabled || timer.status === "idle"} onclick={() => ontimer("stop")}>Stop <kbd>s</kbd></Button>
    {#if timer.phase === "short_break"}<Button {disabled} onclick={() => ontimer("skip_break")}>Skip break <kbd>b</kbd></Button>{/if}
  </div>
  <label class="goal">Goal
    <select bind:value={targetId} disabled={timer.status !== "idle"}>
      <option value="">None</option>
      {#each data.targets as item (item.id)}<option value={item.id.toString()}>{item.title}</option>{/each}
    </select>
  </label>
  <p class="muted timer-help">Priming, pauses and breaks never count; only completed focus does.</p>
</section>

<style>
  .timer-card {
    display: grid;
    align-content: center;
    justify-items: center;
    gap: 0.55rem;
    text-align: center;
    background: linear-gradient(145deg, var(--surface-raised), var(--surface));
    border-color: color-mix(in srgb, var(--accent) 28%, var(--border));
  }
  .timer-card :global(.timer-ring) {
    margin: 0.4rem auto;
  }
  .focus-line {
    display: grid;
    justify-items: center;
    gap: 4px;
  }
  .chip {
    display: inline-flex;
    align-items: center;
    gap: 7px;
    max-width: 100%;
    padding: 3px 11px;
    border: 1px solid var(--hairline);
    border-radius: 999px;
    color: var(--ink);
    font: 500 12.5px var(--font-ui);
  }
  .chip .concept {
    color: var(--muted);
  }
  .dot {
    width: 8px;
    height: 8px;
    border-radius: 999px;
  }
  .cycle {
    color: var(--muted);
    font: 500 10.5px var(--font-mono);
  }
  .clock {
    color: var(--ink);
    font-size: 3.6rem;
    font-variant-numeric: tabular-nums;
    font-weight: 500;
    letter-spacing: -0.05em;
    line-height: 1;
  }
  .phase-label,
  .rhythm,
  .actions {
    display: flex;
    flex-wrap: wrap;
    justify-content: center;
    align-items: center;
    gap: 0.5rem;
  }
  .phase-label {
    font-size: 0.85rem;
  }
  .phase-dot {
    width: 0.45rem;
    height: 0.45rem;
    background: var(--accent);
    border-radius: 50%;
  }
  .break-phase .phase-dot {
    background: var(--success);
  }
  .rhythm,
  .timer-help {
    margin: 0;
    color: var(--muted);
    font-size: 0.8rem;
  }
  .rhythm span {
    opacity: 0.55;
  }
  .goal {
    display: flex;
    flex-direction: row;
    align-items: center;
    gap: 0.5rem;
    color: var(--muted);
    font-size: 0.8rem;
  }
  .goal select {
    min-width: 9rem;
    padding: 0.3rem 0.5rem;
    border: 1px solid var(--border);
    border-radius: 0.35rem;
    background: var(--canvas);
    color: var(--ink);
  }
  kbd {
    font-size: 0.75em;
    color: var(--muted);
  }
</style>
