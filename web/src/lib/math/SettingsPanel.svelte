<script lang="ts">
  import Select from "../ui/Select.svelte";
  import { DURATIONS, OPERATIONS, type MathSettings, type Operation } from "./settings";

  /** The Settings drawer; changes apply from the next sprint and are remembered. */
  let { settings, onchange }: { settings: MathSettings; onchange: (next: MathSettings) => void } = $props();
  const FORMATS = [{ value: "zetamac", label: "Typed" }, { value: "mc", label: "Multiple choice" }];
  const LENGTHS = DURATIONS.map((seconds) => ({ value: String(seconds), label: seconds < 60 ? `${seconds} s` : `${seconds / 60} min` }));

  function toggle(op: Operation) {
    const ops = settings.ops.includes(op) ? settings.ops.filter((item) => item !== op) : OPERATIONS.map((item) => item.op).filter((item) => item === op || settings.ops.includes(item));
    if (ops.length) onchange({ ...settings, ops });
  }
</script>

<h4>Settings</h4>
<div class="field">
  <span>Sprint format</span>
  <Select label="Sprint format" options={FORMATS} value={settings.mode} onchange={(mode) => onchange({ ...settings, mode: mode === "mc" ? "mc" : "zetamac" })} />
</div>
<div class="field">
  <span>Sprint length</span>
  <Select kind="list" label="Sprint length" options={LENGTHS} value={String(settings.duration_s)} onchange={(value) => onchange({ ...settings, duration_s: Number(value) })} />
</div>
<fieldset class="field">
  <legend>Operations</legend>
  {#each OPERATIONS as item (item.op)}
    <label class="check"><input type="checkbox" checked={settings.ops.includes(item.op)} onchange={() => toggle(item.op)} /><b>{item.sign}</b>{item.range}</label>
  {/each}
</fieldset>
<label class="check">
  <input type="checkbox" checked={settings.focus} onchange={(event) => onchange({ ...settings, focus: event.currentTarget.checked })} />
  Start a Study focus cycle on Math when I practise
</label>

<style>
  h4 {
    margin: 0;
    font-size: 14px;
  }
  .field {
    display: grid;
    gap: 6px;
    margin: 0;
    padding: 0;
    border: 0;
    color: var(--muted);
    font-size: 12px;
  }
  legend {
    margin-bottom: 6px;
    padding: 0;
  }
  .check {
    display: flex;
    align-items: center;
    gap: 8px;
    color: var(--ink);
    font-size: 12.5px;
  }
  .check b {
    width: 12px;
    font: 600 13px var(--font-mono);
  }
</style>
