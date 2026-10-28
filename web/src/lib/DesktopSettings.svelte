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
      if (value === "check-updates") notice = "Update check requested. Follow the desktop dialog.";
      if (value === "new-quick-note") notice = "New quick note requested in a separate desktop window.";
      if (value === "quit") notice = "Quit requested. kitty! is saving and stopping workers.";
    } catch (cause) { error = (cause as Error).message; }
    finally { pending = false; }
  }
</script>

<section aria-label="Desktop settings">
  <Panel title="Desktop">
    {#if desktop?.available}
      <label class="keep-running">
        <input type="checkbox" checked={desktop.keep_running === true}
          disabled={pending || !desktop.keep_running_available || desktop.keep_running === null}
          onchange={(event) => { const checked = event.currentTarget.checked; event.currentTarget.checked = desktop?.keep_running === true; void command(checked ? "keep-running-on" : "keep-running-off"); }} />
        <span>Keep running on close (monitoring / reminders)</span>
      </label>
      <p class="muted">Closing the window {desktop.keep_running ? "keeps kitty! running in the system tray" : "quits kitty! and stops workers"}. Quit always saves and stops workers.</p>
      {#if !desktop.keep_running_available}<p class="muted">Keep running on close needs a system tray on this desktop.</p>{/if}
      <div class="actions">
        <Button size="sm" disabled={pending} onclick={() => command("check-updates")}>Check for updates</Button>
        <Button size="sm" disabled={pending} onclick={() => command("new-quick-note")}>New quick note</Button>
        <Button size="sm" disabled={pending} onclick={() => command("quit")}>Quit kitty! (save and stop workers)</Button>
      </div>
    {:else if desktop}
      <p class="muted">These controls are available in the kitty! desktop app. This browser website has no desktop shell.</p>
    {:else if !error}
      <p class="muted" role="status">Loading desktop settings…</p>
    {/if}
    {#if notice}<p class="muted" role="status">{notice}</p>{/if}
    {#if error}<p role="alert">{error}</p>{/if}
  </Panel>
</section>

<style>
  .keep-running { display: flex; align-items: center; gap: .75rem; padding: .55rem 0; }
  input { accent-color: var(--accent); }
  .actions { display: flex; flex-wrap: wrap; gap: .65rem; }
  .muted { color: var(--muted); font-size: .8rem; line-height: 1.5; }
</style>
