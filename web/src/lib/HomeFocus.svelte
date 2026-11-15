<script lang="ts">
  import { onMount } from "svelte";
  import HomeWidget from "./HomeWidget.svelte";
  import { studyTimer } from "./studyTimer.svelte";

  /**
   * Focus on home: Study's Pomodoro timer on one line (the phase, the time left, one
   * button) over a hairline that drains as the phase runs. It is the same timer the top
   * bar and Study show (studyTimer.svelte.ts), so starting it here starts it everywhere.
   */
  let { id }: { id: string } = $props();
  onMount(() => studyTimer.use());

  const timer = $derived(studyTimer.result?.timer);
  const idle = $derived(!timer || timer.status === "idle");
  const paused = $derived(timer?.status === "paused");
  const left = $derived(!idle && timer?.duration_seconds ? Math.min(1, studyTimer.remaining / timer.duration_seconds) : 0);
  const blocked = $derived(!timer || studyTimer.busy || studyTimer.offline);
</script>

<HomeWidget {id} title="Focus" icon="timer" eyebrow={idle ? "" : paused ? "Paused" : studyTimer.phase === "Focus" ? "" : studyTimer.phase}>
  {#snippet tail()}
    {#if !idle}<span class="clock" class:paused role="timer" aria-label={`${studyTimer.phase}, ${studyTimer.clock} left`}>{studyTimer.clock}</span>{/if}
    {#if idle}<button type="button" class="text-button" disabled={blocked} onclick={() => studyTimer.act("start")}>Start</button>
    {:else if paused}
      <button type="button" class="text-button" disabled={blocked} onclick={() => studyTimer.act("resume")}>Resume</button>
      <button type="button" class="text-button" disabled={blocked} onclick={() => studyTimer.act("stop")}>Stop</button>
    {:else}<button type="button" class="text-button" disabled={blocked} onclick={() => studyTimer.act("pause")}>Pause</button>{/if}
  {/snippet}
  <div class="bar" aria-hidden="true"><span style:width={`${left * 100}%`}></span></div>
  {#if studyTimer.error && !studyTimer.result}<p class="quiet" role="status">{studyTimer.error}</p>{/if}
</HomeWidget>

<style>
  .clock {
    font-size: 22px;
    line-height: 1.2;
    font-variant-numeric: tabular-nums;
  }
  .clock.paused {
    color: var(--muted);
  }
  .bar {
    height: 2px;
    background: var(--hairline);
  }
  .bar span {
    display: block;
    height: 100%;
    background: var(--ink);
  }
  .quiet {
    margin: 10px 0 0;
    color: var(--muted);
    font-size: 14px;
  }
</style>
