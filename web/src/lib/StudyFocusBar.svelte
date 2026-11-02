<script lang="ts">
  import { onMount } from "svelte";
  import { Button, Popover } from "@nunera/bracket";
  import { api } from "./api";
  import TimerRing from "./TimerRing.svelte";
  let { revision = 0, demo = false }: { revision?: number; demo?: boolean } =
    $props();
  type Timer = {
    revision: number;
    status: "idle" | "running" | "paused";
    phase: "priming" | "focus" | "short_break";
    phase_id: string | null;
    session_id: string | null;
    completed_at: string | null;
    completed_phase_id: string | null;
    completed_phase: "priming" | "focus" | "short_break" | null;
    deadline_at: string | null;
    remaining_seconds: number;
    duration_seconds?: number;
    cycle?: number;
    notes?: string;
  };
  type Result = {
    timer: Timer;
    server_now: string;
    settings?: { sound: boolean; notifications: boolean };
  };
  let timerState = $state<Result | null>(null);
  let now = $state(Date.now());
  let offset = $state(0);
  let busy = $state(false);
  let offline = $state(false);
  let error = $state("");
  let alert = $state("");
  let soundReady = $state(false);
  let soundError = $state("");
  let open = $state(false);
  let mounted = false;
  let loading = false;
  let audio: AudioContext | null = null;
  let alerted = "";
  const cacheKey = "kitty-study-focus-bar";
  const remaining = $derived(
    timerState?.timer.status === "running" && timerState.timer.deadline_at
      ? Math.max(
          0,
          Math.ceil(
            (Date.parse(timerState.timer.deadline_at) - now - offset) / 1000,
          ),
        )
      : Math.ceil(timerState?.timer.remaining_seconds || 0),
  );
  const clock = $derived(
    `${Math.floor(remaining / 60)
      .toString()
      .padStart(2, "0")}:${(remaining % 60).toString().padStart(2, "0")}`,
  );
  const phase = $derived(timerState?.timer.phase === "priming" ? "Priming" : timerState?.timer.phase === "focus" ? "Focus" : "Break");
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
      if (!notified && result.settings?.notifications && "Notification" in window && Notification.permission === "granted") {
        try {
          new Notification("rika! study", { body: alert, tag: "kitty-study-phase" });
          try { localStorage.setItem("kitty-study-last-notification", key); } catch { /* Optional storage. */ }
        } catch {
          error = "Desktop notifications are unavailable. Visual phase alerts remain active.";
        }
      }
    };
    // The shared lock makes check/play/claim atomic across tabs in supporting browsers.
    if (navigator.locks) await navigator.locks.request("kitty-study-phase-alert", notify);
    else notify();
  }
  async function load() {
    if (loading) return;
    loading = true;
    try {
      const result = await api<Result>("/study/timer");
      timerState = result;
      offset = Date.parse(result.server_now) - Date.now();
      offline = false;
      void announce(result);
      try {
        localStorage.setItem(cacheKey, JSON.stringify(result));
      } catch {
        /* Server remains authoritative. */
      }
    } catch (cause) {
      offline = true;
      error = (cause as Error).message;
    } finally {
      loading = false;
    }
  }
  async function act(action: string) {
    if (!timerState || busy || offline) return;
    if (
      action === "stop" &&
      !confirm(
        "Discard unfinished focus time? Completed sessions remain saved.",
      )
    )
      return;
    unlockSound();
    busy = true;
    error = "";
    try {
      await api("/study/timer", {
        action,
        revision: timerState.timer.revision,
        request_id: crypto.randomUUID(),
      });
      await load();
      window.dispatchEvent(new CustomEvent("kitty-study-changed"));
    } catch (cause) {
      error = (cause as Error).message;
      await load();
    } finally {
      busy = false;
    }
  }
  onMount(() => {
    mounted = true;
    try {
      const cached = localStorage.getItem(cacheKey);
      if (cached) {
        timerState = JSON.parse(cached);
        offline = true;
      }
    } catch {
      /* Ignore malformed cache. */
    }
    void load();
    const tick = window.setInterval(() => {
      now = Date.now();
      if (timerState?.timer.status === "running" && remaining === 0)
        void load();
    }, 250);
    const refresh = window.setInterval(() => {
      if (!busy) void load();
    }, 2000);
    const wake = () => {
      if (document.visibilityState === "visible") void load();
    };
    const changed = () => void load();
    document.addEventListener("pointerdown", unlockSound);
    document.addEventListener("keydown", unlockSound);
    window.addEventListener("kitty-study-unlock-sound", unlockSound);
    document.addEventListener("visibilitychange", wake);
    window.addEventListener("focus", changed);
    window.addEventListener("online", changed);
    window.addEventListener("kitty-study-changed", changed);
    return () => {
      mounted = false;
      window.clearInterval(tick);
      window.clearInterval(refresh);
      document.removeEventListener("pointerdown", unlockSound);
      document.removeEventListener("keydown", unlockSound);
      window.removeEventListener("kitty-study-unlock-sound", unlockSound);
      document.removeEventListener("visibilitychange", wake);
      window.removeEventListener("focus", changed);
      window.removeEventListener("online", changed);
      window.removeEventListener("kitty-study-changed", changed);
      void audio?.close();
    };
  });
  $effect(() => {
    revision;
    if (mounted) void load();
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
        {#if error}<p class="error" role="status">{error} <Button size="sm" onclick={() => { error = ""; void load(); }}>Reconnect</Button></p>{/if}
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
