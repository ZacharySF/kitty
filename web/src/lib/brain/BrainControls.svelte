<!-- The Brain's View panel: a collapsible, mindcorpus-like column of form, grouping, motion and drawing controls. -->
<script lang="ts">
  import {
    BRAIN_GROUPINGS,
    BRAIN_LABELS,
    BRAIN_LAYOUTS,
    BRAIN_POINT_SIZE,
    BRAIN_SPEED,
    type BrainView,
  } from "./brainSettings";

  let {
    view,
    reducedMotion,
    onchange,
    onreset,
  }: {
    view: BrainView;
    reducedMotion: boolean;
    onchange: (next: BrainView) => void;
    onreset: () => void;
  } = $props();

  const panelId = `brain-view-${Math.random().toString(36).slice(2, 8)}`;
  const set = (patch: Partial<BrainView>) => onchange({ ...view, ...patch });
  const percent = (value: number) => `${Math.round(value * 100)}%`;
  /** How much of a slider's track is lit. */
  const fill = (value: number, limits: { min: number; max: number }) => `${((value - limits.min) / (limits.max - limits.min)) * 100}%`;
</script>

{#snippet choices<T extends string>(label: string, options: { id: T; label: string }[], value: T, choose: (id: T) => void)}
  <div class="row">
    <span class="label">{label}</span>
    <div class="segments" role="group" aria-label={label}>
      {#each options as option}
        <button type="button" aria-pressed={value === option.id} onclick={() => choose(option.id)}>{option.label}</button>
      {/each}
    </div>
  </div>
{/snippet}

{#snippet toggle(label: string, on: boolean, flip: () => void, disabled = false, hint = "")}
  <button type="button" class="row switch" role="switch" aria-checked={on} {disabled} title={hint || undefined} onclick={flip}>
    <span class="label">{label}</span><i aria-hidden="true"></i>
  </button>
{/snippet}

<div class="controls">
  <button type="button" class="opener" aria-expanded={view.panel} aria-controls={panelId} onclick={() => set({ panel: !view.panel })}>
    <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M2 4h7M12 4h2M2 8h2M7 8h7M2 12h9M14 12h0" /><circle cx="10.5" cy="4" r="1.5" /><circle cx="5.5" cy="8" r="1.5" /><circle cx="12.5" cy="12" r="1.5" /></svg>
    <span>View</span>
  </button>
  {#if view.panel}
    <aside id={panelId} class="panel" aria-label="Brain view">
      <section>
        <h4>Form</h4>
        {@render choices("Layout", BRAIN_LAYOUTS, view.layout, (layout) => set({ layout }))}
      </section>
      <section>
        <h4>Groups</h4>
        {@render choices("Group by", BRAIN_GROUPINGS, view.groupBy, (groupBy) => set({ groupBy }))}
        {@render choices("Labels", BRAIN_LABELS, view.labels, (labels) => set({ labels }))}
      </section>
      <section>
        <h4>Motion</h4>
        {@render toggle("Turning", view.turning && !reducedMotion, () => set({ turning: !view.turning }), reducedMotion, reducedMotion ? "Your system asks for reduced motion" : "")}
        <label class="row slider">
          <span class="label">Speed</span>
          <input type="range" aria-label="Speed" min={BRAIN_SPEED.min} max={BRAIN_SPEED.max} step={BRAIN_SPEED.step} value={view.speed} disabled={reducedMotion} style:--fill={fill(view.speed, BRAIN_SPEED)} oninput={(event) => set({ speed: Number(event.currentTarget.value) })} />
          <output>{percent(view.speed)}</output>
        </label>
      </section>
      <section>
        <h4>Drawing</h4>
        {@render toggle("Lines", view.lines, () => set({ lines: !view.lines }))}
        {@render toggle("Scaffold", view.scaffold, () => set({ scaffold: !view.scaffold }))}
        <label class="row slider">
          <span class="label">Point size</span>
          <input type="range" aria-label="Point size" min={BRAIN_POINT_SIZE.min} max={BRAIN_POINT_SIZE.max} step={BRAIN_POINT_SIZE.step} value={view.pointSize} style:--fill={fill(view.pointSize, BRAIN_POINT_SIZE)} oninput={(event) => set({ pointSize: Number(event.currentTarget.value) })} />
          <output>{percent(view.pointSize)}</output>
        </label>
      </section>
      <button type="button" class="reset" onclick={onreset}>Reset</button>
    </aside>
  {/if}
</div>

<style>
  .controls {
    position: absolute;
    top: 14px;
    right: 14px;
    z-index: 3;
    display: flex;
    flex-direction: column;
    align-items: flex-end;
    gap: 8px;
    max-height: calc(100% - 28px);
    font: 11px/1.3 var(--br-font-ui, system-ui, sans-serif);
    color: #f2f2f2;
  }
  button {
    font: inherit;
    color: inherit;
    cursor: pointer;
  }
  .opener {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    height: 28px;
    padding: 0 10px;
    background: #000;
    border: 1px solid #3a3a3a;
    border-radius: 3px;
  }
  .opener:hover,
  .opener[aria-expanded="true"] {
    border-color: #8a8a8a;
  }
  .opener svg {
    width: 14px;
    height: 14px;
    fill: #000;
    stroke: currentColor;
    stroke-width: 1.2;
    stroke-linecap: round;
  }
  .panel {
    width: 236px;
    overflow-y: auto;
    padding: 4px 12px 12px;
    background: rgba(0, 0, 0, 0.88);
    border: 1px solid #262626;
    border-radius: 3px;
    scrollbar-width: thin;
    scrollbar-color: #333 transparent;
  }
  section {
    padding: 10px 0 8px;
    border-bottom: 1px solid #1c1c1c;
  }
  h4 {
    margin: 0 0 8px;
    font-size: 9.5px;
    font-weight: 500;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: #7d7d7d;
  }
  .row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
    min-height: 24px;
  }
  .label {
    color: #d8d8d8;
    white-space: nowrap;
  }
  .segments {
    display: inline-flex;
    border: 1px solid #333;
    border-radius: 3px;
    overflow: hidden;
  }
  .segments button {
    padding: 3px 7px;
    background: transparent;
    border: 0;
    color: #a8a8a8;
  }
  .segments button + button {
    border-left: 1px solid #333;
  }
  .segments button:hover {
    color: #fff;
  }
  .segments button[aria-pressed="true"] {
    background: #f2f2f2;
    color: #000;
  }
  .switch {
    width: 100%;
    padding: 0;
    background: none;
    border: 0;
    text-align: left;
  }
  .switch i {
    position: relative;
    width: 24px;
    height: 13px;
    border-radius: 7px;
    background: #333;
    transition: background 0.15s;
  }
  .switch i::after {
    content: "";
    position: absolute;
    top: 2px;
    left: 2px;
    width: 9px;
    height: 9px;
    border-radius: 50%;
    background: #000;
    transition: transform 0.15s;
  }
  .switch[aria-checked="true"] i {
    background: #f2f2f2;
  }
  .switch[aria-checked="true"] i::after {
    transform: translateX(11px);
  }
  .switch:disabled {
    opacity: 0.45;
    cursor: default;
  }
  .slider {
    display: grid;
    grid-template-columns: auto minmax(0, 1fr) 36px;
  }
  /* Beats the app's themed field styling: a hairline track with a small white knob. */
  .panel .slider input[type="range"] {
    width: 100%;
    height: 14px;
    margin: 0;
    padding: 0;
    border: 0;
    border-radius: 0;
    background: linear-gradient(#f2f2f2, #f2f2f2) 0 50% / var(--fill) 2px no-repeat, linear-gradient(#3a3a3a, #3a3a3a) 0 50% / 100% 2px no-repeat;
    appearance: none;
    -webkit-appearance: none;
    cursor: pointer;
  }
  .slider input[type="range"]::-webkit-slider-thumb {
    width: 10px;
    height: 10px;
    border: 0;
    border-radius: 50%;
    background: #f2f2f2;
    -webkit-appearance: none;
  }
  .slider input[type="range"]::-moz-range-thumb {
    width: 10px;
    height: 10px;
    border: 0;
    border-radius: 50%;
    background: #f2f2f2;
  }
  .panel .slider input[type="range"]:disabled {
    opacity: 0.45;
    cursor: default;
  }
  .slider output {
    text-align: right;
    color: #a8a8a8;
    font-variant-numeric: tabular-nums;
  }
  .reset {
    display: block;
    margin: 10px 0 0 auto;
    padding: 3px 0;
    background: none;
    border: 0;
    color: #a8a8a8;
  }
  .reset:hover {
    color: #fff;
  }
  button:focus-visible,
  input:focus-visible {
    outline: 1px solid #fff;
    outline-offset: 2px;
  }
  @media (prefers-reduced-motion: reduce) {
    .switch i,
    .switch i::after {
      transition: none;
    }
  }
</style>
