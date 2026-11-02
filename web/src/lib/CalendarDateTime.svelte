<script lang="ts">
  import Select from "./ui/Select.svelte";

  let {
    value = $bindable(""),
    label,
    timed = true,
    required = false,
  }: { value?: string; label: string; timed?: boolean; required?: boolean } = $props();
  const quarterHours = Array.from({ length: 96 }, (_, i) =>
    `${String(Math.floor(i / 4)).padStart(2, "0")}:${String((i % 4) * 15).padStart(2, "0")}`,
  );
  const time = $derived(value.slice(11, 16));
  const choices = $derived(
    time && !quarterHours.includes(time) ? [...quarterHours, time].sort() : quarterHours,
  );
</script>

<div class="date-time-field">
  <label>{label} date<input type="date" value={value.slice(0, 10)} {required}
    onchange={(event) => {
      const date = event.currentTarget.value;
      value = date ? timed ? `${date}T${time || "09:00"}` : date : "";
    }} /></label>
  {#if timed}
    <label>{label} time<Select kind="list" label={`${label} time`} value={time || "09:00"} disabled={!value}
      onchange={(next) => (value = `${value.slice(0, 10)}T${next}`)} options={choices.map((choice) => ({ value: choice, label: choice }))} /></label>
  {/if}
</div>

<style>
  .date-time-field { display: flex; flex-wrap: wrap; gap: 0.6rem; }
  label { display: flex; flex: 1; min-width: 7rem; flex-direction: column; gap: 0.35rem; }
  input {
    min-width: 0; width: 100%; max-width: 100%; box-sizing: border-box;
    font: inherit; color: var(--ink); border: 1px solid var(--border);
    background: var(--surface); border-radius: 0.4rem; padding: 0.5rem;
  }
</style>
