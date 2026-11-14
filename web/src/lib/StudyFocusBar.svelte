<script lang="ts">
  import { onMount, untrack } from "svelte";
  import { Button, Popover } from "@nunera/bracket";
  import TimerRing from "./TimerRing.svelte";
  let { revision = 0, demo = false }: { revision?: number; demo?: boolean } =
    $props();
  import { studyTimer, type TimerResult as Result } from "./studyTimer.svelte";
  import { sendSystem } from "./systemNotify";
  /** The top bar's timer: the shared study timer (studyTimer.svelte.ts), its sound and phase alerts. */
  const timerState = $derived(studyTimer.result);
  const remaining = $derived(studyTimer.remaining);
  const clock = $derived(studyTimer.clock);
  const phase = $derived(studyTimer.phase);
  const busy = $derived(studyTimer.busy);
  const offline = $derived(studyTimer.offline);
  const error = $derived(studyTimer.error);
  const act = (action: string) => studyTimer.act(action);
  let alert = $state("");
  let soundReady = $state(false);
  let soundError = $state("");
  let open = $state(false);
  let audio: AudioContext | null = null;
  let alerted = "";
  const attention = $derived(Boolean(alert || error || soundError));

  // Background pointer/key gestures retry silently; only an explicit Start,
  // Resume or Enable sound reports that audio is unavailable.
  function unlockSound(event?: Event) {
    if (timerState?.settings?.sound === false) return;
    const explicit = !event || event.type === "click" || event.type === "kitty-study-unlock-sound";
    const report = (message: string) => { if (explicit) soundError = message; };
    if (audio?.state === "running") {
      soundReady = true;
      soundError = "";
      return;
    }
    if (!("AudioContext" in window)) {
      report("This browser cannot play timer sounds. Visual phase alerts remain available.");
      return;
    }
    try {
      audio ||= new AudioContext();
      audio.onstatechange = () => { soundReady = audio?.state === "running"; };
      // resume() is invoked synchronously during the original pointer/key gesture.
      void audio.resume().then(() => {
        soundReady = audio?.state === "running";
        if (soundReady) soundError = "";
        else report("Sound is blocked. Enable sound in this tab; visual alerts remain active.");
      }).catch(() => {
        soundReady = false;
        report("Sound permission is blocked. Visual phase alerts remain available.");
      });
    } catch {
      soundReady = false;
      report("Sound is unavailable in this browser. Visual phase alerts remain available.");
    }
  }
  function beep() {
    if (audio?.state !== "running") return false;
    const oscillator = audio.createOscillator();
    const gain = audio.createGain();
    oscillator.connect(gain);
    gain.connect(audio.destination);
    oscillator.frequency.value = 660;
    gain.gain.setValueAtTime(0.12, audio.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.001, audio.currentTime + 0.6);
    oscillator.onended = () => { oscillator.disconnect(); gain.disconnect(); };
    oscillator.start();
    oscillator.stop(audio.currentTime + 0.65);
    return true;
  }
  async function announce(result: Result) {
    const timer = result.timer;
    const key = timer.completed_phase_id;
    if (!key || alerted === key) return;
    alerted = key;
    alert = timer.completed_phase === "priming"
      ? "Priming complete. Your focus has started; priming was not counted as study time."
      : timer.completed_phase === "focus"
        ? "Focus complete and saved. Your break has started. Review your guide and reflection."
        : "Break complete. Your next focus has started.";
    if (timer.status === "idle") alert += " The cycle is now stopped.";
    const notify = () => {
      let sounded = false;
      let notified = false;
      try {
        sounded = localStorage.getItem("kitty-study-last-sound") === key;
        notified = localStorage.getItem("kitty-study-last-notification") === key;
      } catch {
        // The in-memory completion token still prevents repeat alerts in this tab.
      }
      if (result.settings?.sound && !sounded) {
        if (beep()) {
          try { localStorage.setItem("kitty-study-last-sound", key); } catch { /* Optional storage. */ }
        } else {
          soundReady = false;
          soundError = "Phase complete. Sound is not enabled in this tab. Enable sound for the next phase; visual alerts remain active.";
        }
      }
      if (!notified && result.settings?.notifications) {
        // Claimed before sending, so two tabs never both notify.
        try { localStorage.setItem("kitty-study-last-notification", key); } catch { /* Optional storage. */ }
        void sendSystem({ key: "kitty-study-phase", title: "Study", body: alert, actions: false });
      }
    };
    // The shared lock makes check/play/claim atomic across tabs in supporting browsers.
    if (navigator.locks) await navigator.locks.request("kitty-study-phase-alert", notify);
    else notify();
  }
  onMount(() => {
    const release = studyTimer.use();
    document.addEventListener("pointerdown", unlockSound);
    document.addEventListener("keydown", unlockSound);
    window.addEventListener("kitty-study-unlock-sound", unlockSound);
    return () => {
      release();
      document.removeEventListener("pointerdown", unlockSound);
      document.removeEventListener("keydown", unlockSound);
      window.removeEventListener("kitty-study-unlock-sound", unlockSound);
      void audio?.close();
    };
  });
  // Each fresh answer may carry a phase that just ended.
  $effect(() => {
    if (studyTimer.loads) untrack(() => { if (studyTimer.result) void announce(studyTimer.result); });
  });
  let seenRevision = revision;
  $effect(() => {
    if (revision === seenRevision) return;
    seenRevision = revision;
    void studyTimer.load();
  });
</script>

<!-- A small ring and the time in the top bar; the popover holds the controls and any alerts. -->
{#if timerState && (timerState.timer.status !== "idle" || attention)}
  <div class="study-timer">
    <Popover bind:open placement="bottom-end" arrow={false}>
      {#snippet trigger(props)}
        <Button {...props} size="sm" variant="ghost" class="study-timer-trigger"
          aria-label={`Study timer: ${phase} ${clock}, ${timerState?.timer.status.replaceAll("_", " ")}${attention ? ", needs attention" : ""}`}>
          {#if timerState?.timer.status !== "idle"}
            <TimerRing size={20} stroke={2.5} remaining={remaining}
              total={timerState?.timer.duration_seconds || (timerState?.timer.phase === "priming" ? 300 : remaining)}
              phase={timerState?.timer.phase ?? "focus"} label={`${phase} progress`} />
            <span class="time" class:paused={timerState?.timer.status === "paused"}>{clock}</span>
          {:else}<span class="time">Study</span>{/if}
          {#if attention}<span class="attention" aria-hidden="true"></span>{/if}
        </Button>
      {/snippet}
      <div class="study-timer-panel" role="group" aria-label="Study timer controls">
        {#if timerState.timer.status !== "idle"}
          <p class="heading"><strong>{phase} · {clock}</strong><span>{timerState.timer.status.replaceAll("_", " ")}{demo ? " · local demo" : ""}{offline ? " · cached estimate" : ""}</span></p>
          {#if timerState.timer.notes}<p class="notes" title={timerState.timer.notes}>{timerState.timer.notes.split("\n")[0].slice(0, 80)}</p>{/if}
          <div class="controls">
            {#if timerState.timer.status === "running"}<Button size="sm" disabled={busy || offline} onclick={() => act("pause")}>Pause</Button>
            {:else if timerState.timer.status === "paused"}<Button size="sm" disabled={busy || offline} onclick={() => act("resume")}>Resume</Button>
            {:else}<Button size="sm" disabled={busy || offline} onclick={() => act("start")}>Start {phase.toLowerCase()}</Button>{/if}
            {#if timerState.timer.phase === "short_break"}<Button size="sm" disabled={busy || offline} onclick={() => act("skip_break")}>Skip break</Button>{/if}
            <Button size="sm" disabled={busy || offline} onclick={() => act("stop")}>Stop</Button>
          </div>
          {#if timerState.settings?.sound && !soundReady}
            <p class="sound-warning" role="status">Sound is not ready in this tab. <Button size="sm" onclick={unlockSound}>Enable sound</Button></p>
          {/if}
        {/if}
        {#if alert}<p role="status">{alert} <Button size="sm" onclick={() => (alert = "")}>Dismiss</Button></p>{/if}
        {#if soundError}<p class="sound-warning" role="alert">{soundError}</p>{/if}
        {#if error}<p class="error" role="status">{error} <Button size="sm" onclick={() => { studyTimer.error = ""; void studyTimer.load(); }}>Reconnect</Button></p>{/if}
      </div>
    </Popover>
  </div>
{/if}

<style>
  .study-timer {
    display: inline-flex;
    flex-shrink: 0;
  }
  /* Bracket's "-end" placement sits over the trigger; drop below it like the theme picker. */
  .study-timer :global(.root[data-placement="bottom-end"] > [role="dialog"]) {
    top: calc(100% + 8px);
    bottom: auto;
    right: 0;
  }
  .study-timer :global(.study-timer-trigger) {
    position: relative;
    gap: 7px;
  }
  .time {
    font: 500 12.5px var(--font-ui);
    font-variant-numeric: tabular-nums;
    letter-spacing: 0;
    text-transform: none;
  }
  .time.paused {
    color: var(--muted);
  }
  .attention {
    position: absolute;
    top: 3px;
    right: 3px;
    width: 7px;
    height: 7px;
    border-radius: 999px;
    background: var(--warning);
  }
  .study-timer-panel {
    display: grid;
    gap: 10px;
    min-width: 260px;
    max-width: 340px;
    padding: 12px;
  }
  .study-timer-panel p {
    margin: 0;
    color: var(--muted);
  }
  .heading {
    display: grid;
    gap: 2px;
  }
  .heading strong {
    color: var(--ink);
    font-variant-numeric: tabular-nums;
    font-size: 15px;
  }
  .controls {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
  }
  .error,
  .sound-warning {
    color: var(--warning);
  }
</style>
