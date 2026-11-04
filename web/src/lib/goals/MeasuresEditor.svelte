<script lang="ts">
  import Icon from "../Icon.svelte";
  import Select from "../ui/Select.svelte";
  import { AUTO_CHOICES, emptyMeasure, type MeasureAuto, type MeasureDraft } from "./goals";

  /**
   * Q14 A: a goal's measures, each a name, a target and a unit. A manual measure keeps
   * the amount you type; an automatic one counts linked data (applications, study minutes,
   * tasks done, math topics mastered) and falls back to everything since the goal was set.
   */
  let { measures = $bindable() }: { measures: MeasureDraft[] } = $props();
  const COUNTS = [{ value: "", label: "Manual" }, ...AUTO_CHOICES.map((item) => ({ value: item.value, label: item.label }))];

  function count(row: MeasureDraft, value: string) {
    const auto = (value || null) as MeasureAuto;
    const choice = AUTO_CHOICES.find((item) => item.value === auto);
    // A fresh automatic row takes its name and unit from what it counts.
    if (choice && !row.label.trim()) row.label = choice.label;
    if (choice && !row.unit.trim()) row.unit = choice.unit;
    row.auto = auto;
  }
</script>

<fieldset class="measures-editor">
  <legend>Measures</legend>
  {#each measures as row, index (index)}
    <div class="row" role="group" aria-label={row.label.trim() || `Measure ${index + 1}`}>
      <label class="field name"><span class="field-label">Name</span><input bind:value={row.label} placeholder="Applications sent" /></label>
      <div class="field counts">
        <span class="field-label">Counts</span>
        <Select label={`How ${row.label.trim() || `measure ${index + 1}`} counts`} kind="list" value={row.auto ?? ""} options={COUNTS} onchange={(value) => count(row, value)} />
      </div>
      {#if !row.auto}
        <label class="field number"><span class="field-label">Now</span><input type="number" min="0" step="any" inputmode="decimal" value={row.current} oninput={(event) => (row.current = event.currentTarget.value)} /></label>
      {/if}
      <label class="field number"><span class="field-label">Target</span><input type="number" min="0" step="any" inputmode="decimal" value={row.target} oninput={(event) => (row.target = event.currentTarget.value)} placeholder="40" /></label>
      <label class="field unit"><span class="field-label">Unit</span><input bind:value={row.unit} placeholder="min, %" /></label>
      <button type="button" class="icon-button remove" aria-label={`Remove ${row.label.trim() || `measure ${index + 1}`}`} title="Remove measure"
        onclick={() => (measures = measures.filter((_, at) => at !== index))}><Icon name="close" size={13} /></button>
    </div>
  {:else}
    <p class="note">No measures yet. Progress shows 0% until you add one.</p>
  {/each}
  <button type="button" class="pill add" onclick={() => (measures = [...measures, emptyMeasure()])}><Icon name="plus" size={13} />Add measure</button>
</fieldset>

<style>
  .measures-editor {
    display: grid;
    gap: 10px;
    min-width: 0;
    margin: 0;
    padding: 10px 0 0;
    border: 0;
    border-top: 1px solid var(--hairline);
  }
  legend {
    padding: 0;
    color: var(--subtle);
    font: 600 var(--fs-label) / 1.3 var(--font-ui);
  }
  .row {
    display: grid;
    grid-template-columns: repeat(6, minmax(0, 1fr));
    align-items: end;
    gap: 6px 8px;
    padding-bottom: 10px;
    border-bottom: 1px solid color-mix(in srgb, var(--hairline) 60%, transparent);
  }
  .field {
    display: grid;
    gap: 4px;
    min-width: 0;
  }
  .field-label {
    color: var(--muted);
    font-size: 11px;
  }
  .name {
    grid-column: span 4;
  }
  .counts {
    grid-column: span 2;
  }
  .number {
    grid-column: span 2;
  }
  .unit {
    grid-column: span 1;
  }
  input {
    width: 100%;
    min-width: 0;
    box-sizing: border-box;
  }
  .note {
    margin: 0;
    color: var(--muted);
    font-size: 12px;
  }
  .add {
    justify-self: start;
    min-height: 28px;
  }
</style>
