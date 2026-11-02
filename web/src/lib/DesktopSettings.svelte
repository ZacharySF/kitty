<script lang="ts">
  import { onMount } from "svelte";
  import { Button, Panel } from "@nunera/bracket";
  import { api } from "./api";

  type DesktopState = { available: boolean; keep_running?: boolean | null; keep_running_available?: boolean };
  type DesktopCommand = "check-updates" | "new-quick-note" | "keep-running-on" | "keep-running-off" | "quit";
  let desktop = $state<DesktopState | null>(null);
  let pending = $state(false);
  let error = $state("");
  let notice = $state("");
  const uid = $props.id();

  onMount(() => {
    let cancelled = false;
    async function refresh() {
      try {
        const next = await api<DesktopState>("/desktop");
        if (!cancelled) { desktop = next; error = ""; }
      } catch (cause) { if (!cancelled) error = (cause as Error).message; }
    }
    void refresh();
    window.addEventListener("focus", refresh);
    return () => { cancelled = true; window.removeEventListener("focus", refresh); };
  });

  async function command(value: DesktopCommand) {
    if (pending || !desktop?.available) return;
    if (value === "quit" && !window.confirm("Quit kitty! and stop its workers? Your saved data is kept.")) return;
    pending = true;
    error = "";
    notice = "";
    try {
      const result = await api<DesktopState | { ok: boolean }>("/desktop/command", { command: value });
      if ("available" in result) desktop = result;
      if (value === "check-updates") notice = "Follow the desktop dialog.";
      if (value === "new-quick-note") notice = "Opened in its own window.";
      if (value === "quit") notice = "Saving and stopping workers…";
    } catch (cause) { error = (cause as Error).message; }
    finally { pending = false; }
  }
  const keepRunningHint = $derived(!desktop?.keep_running_available
    ? "Needs a system tray on this desktop"
    : desktop.keep_running
      ? "Closing the window keeps kitty! running in the system tray for monitoring and reminders"
      : "Closing the window quits kitty! and stops workers");
</script>

<Panel title="Desktop" aria-label="Desktop">
  {#if desktop?.available}
    <div class="rows">
      <div class="set-row">
        <span class="set-label">App updates</span>
        <span class="set-end">
          <Button size="sm" disabled={pending} onclick={() => command("check-updates")}
            title="Downloads and verifies a signed update, saves your workspace, then installs and restarts">Check for updates</Button>
        </span>
      </div>
      <div class="set-row">
        <label class="set-label" for={`${uid}-keep`} title={keepRunningHint}>Keep running on close</label>
        <span class="set-end">
          <input id={`${uid}-keep`} type="checkbox" role="switch" class="keep" checked={desktop.keep_running === true} title={keepRunningHint}
            disabled={pending || !desktop.keep_running_available || desktop.keep_running === null}
            onchange={(event) => { const checked = event.currentTarget.checked; event.currentTarget.checked = desktop?.keep_running === true; void command(checked ? "keep-running-on" : "keep-running-off"); }} />
        </span>
      </div>
      <div class="set-row">
        <span class="set-label">Window</span>
        <span class="set-end">
          <Button size="sm" disabled={pending} onclick={() => command("new-quick-note")}>New quick note</Button>
          <Button size="sm" disabled={pending} onclick={() => command("quit")} title="Saves your workspace and stops workers">Quit kitty!</Button>
        </span>
      </div>
    </div>
  {:else if desktop}
    <p class="set-note">These controls live in the kitty! desktop app.</p>
  {:else if !error}
    <p class="set-note" role="status">Loading desktop settings…</p>
  {/if}
  {#if notice}<p class="set-note" role="status">{notice}</p>{/if}
  {#if error}<p class="set-note" role="alert">{error}</p>{/if}
</Panel>

<style>
  .keep {
    accent-color: var(--accent);
  }
</style>
