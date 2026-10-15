<script lang="ts">
  import { onMount } from "svelte";
  import { Button } from "@nunera/bracket";
  import { api } from "./api";
  let { revision = 0, demo = false }: { revision?: number; demo?: boolean } =
    $props();
  type Timer = {
    revision: number;
    status: "idle" | "running" | "paused" | "awaiting_next";
    phase: "focus" | "short_break" | "long_break";
    phase_id: string | null;
    completed_at: string | null;
    deadline_at: string | null;
    remaining_seconds: number;
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
  const phase = $derived(
    timerState?.timer.phase === "focus"
      ? "Focus"
      : timerState?.timer.phase === "long_break"
        ? "Long break"
        : "Short break",
  );

  function unlockSound() {
    if (!timerState?.settings?.sound || !("AudioContext" in window)) return;
    audio ||= new AudioContext();
    void audio.resume().catch(() => {
      error =
        "Sound permission is blocked; the visual phase alert remains available.";
    });
  }
  function announce(result: Result) {
    const timer = result.timer;
    if (!timer.completed_at || !timer.phase_id) return;
    const key = `${timer.phase_id}:${timer.completed_at}`;
    if (alerted === key) return;
    try {
      if (localStorage.getItem("kitty-study-last-alert") === key) {
        alerted = key;
        return;
      }
      localStorage.setItem("kitty-study-last-alert", key);
    } catch {
      /* In-memory deduplication still avoids repeats in this window. */
    }
    alerted = key;
    alert =
      timer.phase === "focus"
        ? "Break finished. Start focus when ready."
        : "Focus finished and saved. Take or skip your break.";
    if (
      result.settings?.notifications &&
      "Notification" in window &&
      Notification.permission === "granted"
    ) {
      try {
        new Notification("kitty! study", {
          body: alert,
          tag: "kitty-study-phase",
        });
      } catch {
        error =
          "Desktop notifications are unavailable; the visual alert remains available.";
      }
    }
    if (result.settings?.sound) {
      if (audio?.state === "running") {
        const oscillator = audio.createOscillator();
        const gain = audio.createGain();
        oscillator.connect(gain);
        gain.connect(audio.destination);
        oscillator.frequency.value = 660;
        gain.gain.setValueAtTime(0.08, audio.currentTime);
        gain.gain.exponentialRampToValueAtTime(0.001, audio.currentTime + 0.45);
        oscillator.start();
        oscillator.stop(audio.currentTime + 0.5);
      } else
        error =
          "Phase complete. Sound needs a prior click with sound enabled; visual alerts remain active.";
    }
  }
  async function load() {
    if (loading) return;
    loading = true;
    try {
      const result = await api<Result>("/study/timer");
      timerState = result;
      offset = Date.parse(result.server_now) - Date.now();
      offline = false;
      announce(result);
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
    document.addEventListener("visibilitychange", wake);
    window.addEventListener("focus", changed);
    window.addEventListener("online", changed);
    window.addEventListener("kitty-study-changed", changed);
    return () => {
      mounted = false;
      window.clearInterval(tick);
      window.clearInterval(refresh);
      document.removeEventListener("pointerdown", unlockSound);
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

{#if timerState && (timerState.timer.status !== "idle" || alert || error)}
  <aside class="focus-bar" aria-label="Persistent study timer" class:offline>
    {#if timerState.timer.status !== "idle"}
      <strong>{phase} · {clock}</strong><span
        >{timerState.timer.status.replaceAll("_", " ")}{demo
          ? " · local demo"
          : ""}{offline ? " · cached estimate" : ""}</span
      >
      {#if timerState.timer.status === "running"}<Button
          size="sm"
          disabled={busy || offline}
          onclick={() => act("pause")}>Pause</Button
        >
      {:else if timerState.timer.status === "paused"}<Button
          size="sm"
          disabled={busy || offline}
          onclick={() => act("resume")}>Resume</Button
        >
      {:else}<Button
          size="sm"
          disabled={busy || offline}
          onclick={() => act("start")}>Start {phase.toLowerCase()}</Button
        >{/if}
      {#if timerState.timer.phase !== "focus"}<Button
          size="sm"
          disabled={busy || offline}
          onclick={() => act("skip_break")}>Skip break</Button
        >{/if}
      <Button size="sm" disabled={busy || offline} onclick={() => act("stop")}
        >Stop</Button
      >
    {/if}
    {#if alert}<span role="status">{alert}</span><Button
        size="sm"
        onclick={() => (alert = "")}>Dismiss alert</Button
      >{/if}
    {#if error}<span class="error" role="status">{error}</span><Button
        size="sm"
        onclick={() => {
          error = "";
          void load();
        }}>Reconnect / dismiss</Button
      >{/if}
  </aside>
{/if}

<style>
  .focus-bar {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.7rem;
    padding: 0.65rem 1rem;
    border-top: 1px solid var(--accent);
    background: var(--surface-raised);
    color: var(--ink);
  }
  strong {
    font-variant-numeric: tabular-nums;
  }
  span {
    color: var(--muted);
  }
  .error,
  .offline {
    color: var(--warning);
  }
</style>
