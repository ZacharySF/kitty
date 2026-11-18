<script lang="ts">
  import { onMount } from "svelte";
  import HomeWidget from "./HomeWidget.svelte";
  import { studyTimer } from "./studyTimer.svelte";
  import type { View } from "./workspace";

  /**
   * Focus on home: Study's Pomodoro timer. The time left, large (a session's length before
   * it starts), over its buttons, beside a timer bezel: sixty ticks (every fifth longer), all
   * lit before a session and going out clockwise from the top as the phase runs. It is the
   * same timer the top bar and Study show (studyTimer.svelte.ts), so starting it here starts
   * it everywhere.
   */
  let { id, onview }: { id: string; onview: (view: View) => unknown } = $props();
  onMount(() => studyTimer.use());

  const timer = $derived(studyTimer.result?.timer);
  const idle = $derived(!timer || timer.status === "idle");
  const paused = $derived(timer?.status === "paused");
  const left = $derived(idle ? 1 : timer?.duration_seconds ? Math.min(1, studyTimer.remaining / timer.duration_seconds) : 0);
  const TICKS = 60;
  /** The ticks still lit: the last `lit` round the bezel, so the dark part grows from the top. */
  const lit = $derived(Math.ceil(left * TICKS));
  const blocked = $derived(!timer || studyTimer.busy || studyTimer.offline);
  /** Before a session starts: how long its focus phase is, as the clock will show it. */
  const length = $derived.by(() => {
    const seconds = studyTimer.result?.settings?.focus_seconds;
    return seconds ? `${Math.floor(seconds / 60).toString().padStart(2, "0")}:${(seconds % 60).toString().padStart(2, "0")}` : "";
  });
</script>

<HomeWidget {id} title="Focus" icon="timer" hero compact tint="var(--tile-5)" opens="study" onopen={() => onview("study")} art={bezel}>
  {#if idle}{#if length}<p class="figure waiting">{length}</p>{/if}
  {:else}<p class="figure" class:waiting={paused} role="timer" aria-label={`${studyTimer.phase}, ${studyTimer.clock} left`}>{studyTimer.clock}</p>{/if}
  <div class="actions">
    {#if idle}<button type="button" class="pill primary" disabled={blocked} onclick={() => studyTimer.act("start")}>Start</button>
    {:else if paused}
      <button type="button" class="pill primary" disabled={blocked} onclick={() => studyTimer.act("resume")}>Resume</button>
      <button type="button" class="pill" disabled={blocked} onclick={() => studyTimer.act("stop")}>Stop</button>
    {:else}<button type="button" class="pill" disabled={blocked} onclick={() => studyTimer.act("pause")}>Pause</button>{/if}
    {#if !idle && (paused || studyTimer.phase !== "Focus")}<span class="phase">{paused ? "Paused" : studyTimer.phase}</span>{/if}
  </div>
  {#if studyTimer.error && !studyTimer.result}<p class="quiet" role="status">{studyTimer.error}</p>{/if}
</HomeWidget>

{#snippet bezel()}
  <svg class="bezel" class:waiting={paused} viewBox="0 0 120 120" aria-hidden="true">
    {#each { length: TICKS } as _, at (at)}
      <line x1="60" y1="5" x2="60" y2={at % 5 === 0 ? 20 : 14} class:on={at >= TICKS - lit} transform={`rotate(${at * 6} 60 60)`} />
    {/each}
  </svg>
{/snippet}

<style>
  .figure.waiting {
    color: var(--muted);
  }
  .actions {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px;
    margin-top: 14px;
  }
  .phase {
    margin-left: 4px;
    color: var(--muted);
    font-size: 13px;
  }
  .bezel {
    width: clamp(88px, 40cqi, 152px);
    height: clamp(88px, 40cqi, 152px);
    stroke: color-mix(in srgb, var(--ink) 16%, transparent);
    stroke-width: 2.6;
    stroke-linecap: round;
  }
  .bezel line.on {
    stroke: var(--tile-5);
  }
  .bezel.waiting {
    opacity: 0.5;
  }
  .quiet {
    margin: 10px 0 0;
    color: var(--muted);
    font-size: 14px;
  }
</style>
