<script lang="ts">
  import Icon from "../Icon.svelte";
  import Popover from "../ui/Popover.svelte";
  import Select from "../ui/Select.svelte";
  import type { Settings } from "./types";

  /**
   * Q15 A: timer settings in a popover from the ⚙ on the timer. Minute steppers,
   * sound and notification switches and the time zone; each change saves at once
   * and applies from the next phase.
   */
  let { settings, disabled, onsave }: {
    settings: Settings;
    disabled: boolean;
    /** Saves the full settings; resolves false when the save failed. */
    onsave: (next: Settings) => Promise<boolean>;
  } = $props();

  let open = $state(false);
  const minutes = (seconds: number) => Math.max(1, Math.round(seconds / 60));
  const clamp = (value: number, min: number, max: number) => Math.min(max, Math.max(min, value));
  /** Steps to the next multiple of 5 minutes, so an odd saved length lands on the grid. */
  const step = (value: number, by: number) => (by > 0 ? Math.floor(value / 5) * 5 + 5 : Math.ceil(value / 5) * 5 - 5);

  function save(patch: Partial<Settings>) {
    void onsave({ ...settings, ...patch });
  }
  async function notifications(on: boolean) {
    if (on && "Notification" in window && Notification.permission !== "granted"
      && (await Notification.requestPermission()) !== "granted") return;
    save({ notifications: on });
  }
</script>

{#snippet stepper(seconds: number, key: "focus_seconds" | "short_break_seconds", min: number, max: number, label: string)}
  {@const value = minutes(seconds)}
  <span class="stepper" role="group" aria-label={`${label} length`}>
    <button type="button" aria-label={`Shorter ${label.toLowerCase()}`} disabled={disabled || value <= min}
      onclick={() => save({ [key]: clamp(step(value, -1), min, max) * 60 })}>−</button>
    <output>{value} <small>min</small></output>
    <button type="button" aria-label={`Longer ${label.toLowerCase()}`} disabled={disabled || value >= max}
      onclick={() => save({ [key]: clamp(step(value, 1), min, max) * 60 })}>+</button>
  </span>
{/snippet}

{#snippet toggle(on: boolean, label: string, flip: () => void)}
  <button type="button" class="row toggle" role="switch" aria-checked={on} {disabled} onclick={flip}>
    <span class="name">{label}</span><span class="switch"><i></i></span>
  </button>
{/snippet}

<Popover bind:open label="Timer settings" placement="bottom-end" class="timer-settings">
  {#snippet trigger(props)}
    <button type="button" class="icon-button gear" aria-label="Timer settings" title="Timer settings" {...props}><Icon name="settings" size={16} /></button>
  {/snippet}
  <div class="settings">
    <span class="eyebrow">Timer</span>
    <div class="row"><span class="name">Focus</span>{@render stepper(settings.focus_seconds, "focus_seconds", 5, 240, "Focus")}</div>
    <div class="row"><span class="name">Break</span>{@render stepper(settings.short_break_seconds, "short_break_seconds", 5, 60, "Break")}</div>
    <hr class="rule" />
    {@render toggle(settings.sound, "Sound at phase end", () => save({ sound: !settings.sound }))}
    {@render toggle(settings.notifications, "Desktop notifications", () => notifications(!settings.notifications))}
    <hr class="rule" />
    <div class="row"><span class="name">Timezone</span><Select kind="timezone" label="Timezone" value={settings.timezone} {disabled} onchange={(timezone) => save({ timezone })} /></div>
  </div>
</Popover>

<style>
  .settings {
    display: grid;
    gap: 2px;
    width: 300px;
    max-width: calc(100vw - 32px);
    padding: 10px 12px 12px;
    text-align: left;
  }
  .eyebrow {
    margin-bottom: 4px;
  }
  .row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    min-height: 36px;
  }
  .name {
    color: var(--ink);
    font: 400 12.5px/1.2 var(--font-ui);
  }
  .toggle {
    width: 100%;
    padding: 0;
    border: 0;
    background: transparent;
    color: inherit;
    font: inherit;
    text-align: left;
    cursor: pointer;
  }
  hr {
    width: 100%;
    margin: 4px 0;
  }
  .stepper {
    display: inline-flex;
    align-items: center;
    height: var(--control-h);
    border: 1px solid var(--hairline);
    border-radius: var(--r-pill, 999px);
  }
  .stepper button {
    width: 28px;
    height: 100%;
    padding: 0;
    border: 0;
    background: transparent;
    color: var(--muted);
    font: 500 14px var(--font-ui);
    cursor: pointer;
  }
  .stepper button:hover:not(:disabled) {
    color: var(--ink);
  }
  .stepper button:disabled {
    opacity: 0.4;
    cursor: default;
  }
  output {
    min-width: 52px;
    color: var(--ink);
    font: 500 12.5px var(--font-ui);
    font-variant-numeric: tabular-nums;
    text-align: center;
  }
  output small {
    color: var(--muted);
    font-size: 11px;
  }
  .switch {
    position: relative;
    flex-shrink: 0;
    width: 34px;
    height: 20px;
    border: 1px solid var(--hairline);
    border-radius: 999px;
    background: color-mix(in srgb, var(--ink) 8%, transparent);
    transition: background-color 160ms;
  }
  .switch i {
    position: absolute;
    top: 2px;
    left: 2px;
    width: 14px;
    height: 14px;
    border-radius: 999px;
    background: var(--muted);
    transition: transform 160ms, background-color 160ms;
  }
  [aria-checked="true"] .switch {
    background: color-mix(in srgb, var(--c3, var(--accent)) 45%, transparent);
  }
  [aria-checked="true"] .switch i {
    transform: translateX(14px);
    background: var(--ink);
  }
  @media (prefers-reduced-motion: reduce) {
    .switch,
    .switch i {
      transition: none;
    }
  }
</style>
