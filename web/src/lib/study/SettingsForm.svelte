<script lang="ts">
  import { Button } from "@nunera/bracket";
  import type { Settings } from "./types";

  /** Pomodoro preferences; changes apply from the next phase. */
  let { settings = $bindable(), dirty = $bindable(), disabled, onsave }: {
    settings: Settings;
    dirty: boolean;
    disabled: boolean;
    onsave: (event: SubmitEvent) => void;
  } = $props();
</script>

<form class="panel" onsubmit={onsave} oninput={() => (dirty = true)}>
  <h3>Pomodoro preferences</h3>
  <div class="form-grid">
    <label>Focus duration (seconds; default 1500 = 25 min)<input type="number" min="60" max="14400" required bind:value={settings.focus_seconds} /></label>
    <label>Break duration (seconds; default 300 = 5 min)<input type="number" min="60" max="7200" required bind:value={settings.short_break_seconds} /></label>
    <label>IANA timezone<input required bind:value={settings.timezone} placeholder="Europe/London" /></label>
  </div>
  <label class="check-row"><input type="checkbox" bind:checked={settings.sound} />Play a phase-completion sound</label>
  <label class="check-row"><input type="checkbox" bind:checked={settings.notifications} />Desktop notification (browser permission required)</label>
  <p class="muted">
    Each new session begins with five minutes of priming, which never counts as focus. Focus and break then repeat continuously. Preference changes affect the next phase, not the current one.
    Start or Resume unlocks sound in this browser. Keep kitty! open for phase alerts; no sound is promised when the browser is closed.
    After sleep or disconnection, the next phase starts when observed, never by inventing missed cycles.
  </p>
  <Button type="submit" {disabled}>Save preferences</Button>
</form>
