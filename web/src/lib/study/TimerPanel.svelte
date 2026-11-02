<script lang="ts">
  import { Button } from "@nunera/bracket";
  import TimerRing from "../TimerRing.svelte";
  import Select from "../ui/Select.svelte";
  import TimerSettings from "./TimerSettings.svelte";
  import { minutesLabel, phaseLabel, type Settings, type Workspace } from "./types";

  /** The focus-first page's centre: session chip, the live ring, the controls and ⚙ timer settings. */
  let { data, targetId = $bindable(), subjectId, projectId, remaining, clockLabel, phaseTotal, disabled, ontimer, onsettings }: {
    data: Workspace;
    targetId: string;
    subjectId: string;
    projectId: string;
    remaining: number;
    clockLabel: string;
    phaseTotal: number;
    disabled: boolean;
    ontimer: (action: string) => void;
    onsettings: (next: Settings) => Promise<boolean>;
  } = $props();

  const timer = $derived(data.timer);
  const subject = $derived(data.subjects.find((item) => String(item.id) === subjectId));
  const concept = $derived(data.projects.find((item) => String(item.id) === projectId));
  const goal = $derived(data.targets.find((item) => String(item.id) === targetId));
  const rhythm = $derived(`5 min priming → ${minutesLabel(data.settings.focus_seconds)} focus → ${minutesLabel(data.settings.short_break_seconds)} break → repeat. Only completed focus counts.`);
  const goals = $derived([{ value: "", label: "No goal" }, ...data.targets.map((item) => ({ value: String(item.id), label: item.title ?? "" }))]);
</script>

<section class="timer-card" class:break-phase={timer.phase !== "focus"} aria-label="Session timer">
  <span class="gear"><TimerSettings settings={data.settings} {disabled} onsave={onsettings} /></span>
  <div class="meta">
    <span class="chip" title={rhythm}><span class="dot" style:background={subject?.color || "var(--subtle)"} aria-hidden="true"></span>{subject?.name || "Open study"}{#if concept}<span class="concept">· {concept.name}</span>{/if}</span>
    <span class="session eyebrow">Session {timer.cycle + 1}{#if timer.status !== "idle"}{#if goal} · goal: {goal.title}{/if}{/if}</span>
    {#if timer.status === "idle" && data.targets.length}
      <span class="goal"><Select kind="list" label="Goal" options={goals} bind:value={targetId} {disabled} /></span>
    {/if}
  </div>
  <TimerRing {remaining} total={phaseTotal} phase={timer.phase} rounds={timer.cycle}
    label={`${phaseLabel(timer.phase)}, ${clockLabel.replace(/^0/, "")} left of ${Math.floor(phaseTotal / 60)}:${(phaseTotal % 60).toString().padStart(2, "0")}`}
  ><div class="clock">{clockLabel}</div></TimerRing>
  <div class="actions">
    {#if timer.status === "running"}<Button {disabled} onclick={() => ontimer("pause")}>Pause <kbd>Space</kbd></Button>
    {:else if timer.status === "paused"}<Button {disabled} onclick={() => ontimer("resume")}>Resume <kbd>Space</kbd></Button>
    {:else}<Button variant="solid" intent="primary" size="lg" {disabled} onclick={() => ontimer("start")}>Start session <kbd>Space</kbd></Button>{/if}
    {#if timer.phase === "short_break"}<Button {disabled} onclick={() => ontimer("skip_break")}>Skip break <kbd>B</kbd></Button>{/if}
    {#if timer.status !== "idle"}<Button variant="ghost" {disabled} onclick={() => ontimer("stop")}>Stop <kbd>S</kbd></Button>{/if}
  </div>
</section>

<style>
  .timer-card {
    position: relative;
    display: grid;
    align-content: center;
    justify-items: center;
    gap: 14px;
    text-align: center;
  }
  .gear {
    position: absolute;
    top: 0;
    right: 0;
  }
  .meta {
    display: grid;
    justify-items: center;
    gap: 8px;
  }
  .chip {
    display: inline-flex;
    align-items: center;
    gap: 7px;
    max-width: 100%;
    height: 24px;
    padding: 0 10px;
    border: 1px solid var(--hairline);
    border-radius: 999px;
    color: var(--ink);
    font: 500 12px var(--font-ui);
  }
  .chip .concept {
    color: var(--muted);
  }
  .dot {
    width: 8px;
    height: 8px;
    border-radius: 999px;
  }
  .clock {
    color: var(--ink);
    font-size: 3.2rem;
    font-variant-numeric: tabular-nums;
    font-weight: 500;
    letter-spacing: -0.04em;
    line-height: 1;
  }
  .actions {
    display: flex;
    flex-wrap: wrap;
    justify-content: center;
    align-items: center;
    gap: 6px;
  }
  kbd {
    font-size: 0.75em;
    color: var(--muted);
  }
</style>
