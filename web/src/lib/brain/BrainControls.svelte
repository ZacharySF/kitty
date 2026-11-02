<!-- The Brain's View panel: a collapsible, mindcorpus-like column of form, grouping, motion, drawing and colour controls. -->
<script lang="ts">
  import type { Snippet } from "svelte";
  import type { BrainGroup } from "./brainLayout";
  import {
    BRAIN_BACKGROUNDS,
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
    colours,
    swatches,
    picked,
    onrecolour,
    onresetcolours,
    onchange,
    onreset,
    lead,
  }: {
    view: BrainView;
    reducedMotion: boolean;
    /** The legend's groups, whose colours can be changed. */
    colours: BrainGroup[];
    /** Theme colours offered in the picker. */
    swatches: string[];
    /** Group keys the user has recoloured. */
    picked: Record<string, string>;
    onrecolour: (key: string, hex: string | null) => void;
    onresetcolours: () => void;
    onchange: (next: BrainView) => void;
    onreset: () => void;
    /** Controls drawn in View's row, before it (home's look switch). */
    lead?: Snippet;
  } = $props();

  /** The group whose colour picker is open. */
  let editing = $state<string | null>(null);
  let custom = $state("");
  const editingGroup = $derived(colours.find((group) => group.key === editing) ?? null);
  function openPicker(group: BrainGroup) {
    editing = editing === group.key ? null : group.key;
    custom = group.hex;
  }
  function applyCustom() {
    const hex = custom.trim().startsWith("#") ? custom.trim() : `#${custom.trim()}`;
    if (editing && /^#[0-9a-f]{6}$/i.test(hex)) onrecolour(editing, hex);
  }

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
  <div class="bar">
    {@render lead?.()}
    <button type="button" class="opener" aria-expanded={view.panel} aria-controls={panelId} onclick={() => set({ panel: !view.panel })}>
      <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M2 4h7M12 4h2M2 8h2M7 8h7M2 12h9M14 12h0" /><circle cx="10.5" cy="4" r="1.5" /><circle cx="5.5" cy="8" r="1.5" /><circle cx="12.5" cy="12" r="1.5" /></svg>
      <span>View</span>
    </button>
  </div>
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
        {@render choices("Background", BRAIN_BACKGROUNDS, view.background, (background) => set({ background }))}
        <label class="row slider">
          <span class="label">Point size</span>
          <input type="range" aria-label="Point size" min={BRAIN_POINT_SIZE.min} max={BRAIN_POINT_SIZE.max} step={BRAIN_POINT_SIZE.step} value={view.pointSize} style:--fill={fill(view.pointSize, BRAIN_POINT_SIZE)} oninput={(event) => set({ pointSize: Number(event.currentTarget.value) })} />
          <output>{percent(view.pointSize)}</output>
        </label>
      </section>
      <section>
        <h4>Colours</h4>
        <ul class="colours" aria-label="Colours">
          {#each colours as group (group.key)}
            <li>
              <button
                type="button"
                class="row colour"
                aria-label={`Colour of ${group.label}`}
                aria-expanded={editing === group.key}
                data-key={group.key}
                onclick={() => openPicker(group)}
              ><i style:background={group.hex}></i><span class="label">{group.label}</span>{#if picked[group.key]}<span class="mark" title="Your colour">•</span>{/if}</button>
              {#if editing === group.key && editingGroup}
                <div class="picker" role="group" aria-label={`Pick a colour for ${group.label}`}>
                  <div class="swatches">
                    {#each swatches as swatch}
                      <button type="button" aria-label={swatch} aria-pressed={group.hex === swatch} style:background={swatch} onclick={() => onrecolour(group.key, swatch)}></button>
                    {/each}
                  </div>
                  <div class="custom">
                    <input type="color" aria-label="Custom colour" value={group.hex} oninput={(event) => onrecolour(group.key, event.currentTarget.value)} />
                    <input type="text" aria-label="Hex colour" spellcheck="false" maxlength="7" bind:value={custom} onchange={applyCustom} onkeydown={(event) => { if (event.key === "Enter") applyCustom(); }} />
                    {#if picked[group.key]}<button type="button" class="link" onclick={() => onrecolour(group.key, null)}>Theme</button>{/if}
                  </div>
                </div>
              {/if}
            </li>
          {/each}
        </ul>
        <button type="button" class="link reset-colours" disabled={!Object.keys(picked).length} onclick={() => { editing = null; onresetcolours(); }}>Reset colours</button>
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
    color: var(--brain-ink);
  }
  button {
    font: inherit;
    color: inherit;
    cursor: pointer;
  }
  .bar {
    display: flex;
    align-items: center;
    gap: 6px;
  }
  .opener {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    height: 28px;
    padding: 0 10px;
    background: var(--brain-ground);
    border: 1px solid var(--brain-track);
    border-radius: 3px;
    backdrop-filter: var(--brain-blur);
    -webkit-backdrop-filter: var(--brain-blur);
  }
  .opener:hover,
  .opener[aria-expanded="true"] {
    border-color: var(--brain-edge-hot);
  }
  .opener svg {
    width: 14px;
    height: 14px;
    fill: var(--brain-ground);
    stroke: currentColor;
    stroke-width: 1.2;
    stroke-linecap: round;
  }
  .panel {
    width: 236px;
    overflow-y: auto;
    padding: 4px 12px 12px;
    background: var(--brain-surface);
    border: 1px solid var(--brain-edge-soft);
    border-radius: 3px;
    backdrop-filter: var(--brain-blur);
    -webkit-backdrop-filter: var(--brain-blur);
    scrollbar-width: thin;
    scrollbar-color: var(--brain-edge) transparent;
  }
  section {
    padding: 10px 0 8px;
    border-bottom: 1px solid var(--brain-rule);
  }
  h4 {
    margin: 0 0 8px;
    font-size: 9.5px;
    font-weight: 500;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: var(--brain-faint);
  }
  .row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
    min-height: 24px;
  }
  .label {
    color: var(--brain-label);
    white-space: nowrap;
  }
  .segments {
    display: inline-flex;
    border: 1px solid var(--brain-edge);
    border-radius: 3px;
    overflow: hidden;
  }
  .segments button {
    padding: 3px 7px;
    background: transparent;
    border: 0;
    color: var(--brain-mute);
  }
  .segments button + button {
    border-left: 1px solid var(--brain-edge);
  }
  .segments button:hover {
    color: var(--brain-strong);
  }
  .segments button[aria-pressed="true"] {
    background: var(--brain-ink);
    color: var(--brain-ground-solid);
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
    background: var(--brain-edge);
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
    background: var(--brain-ground-solid);
    transition: transform 0.15s;
  }
  .switch[aria-checked="true"] i {
    background: var(--brain-ink);
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
    background: linear-gradient(var(--brain-ink), var(--brain-ink)) 0 50% / var(--fill) 2px no-repeat, linear-gradient(var(--brain-track), var(--brain-track)) 0 50% / 100% 2px no-repeat;
    appearance: none;
    -webkit-appearance: none;
    cursor: pointer;
  }
  .slider input[type="range"]::-webkit-slider-thumb {
    width: 10px;
    height: 10px;
    border: 0;
    border-radius: 50%;
    background: var(--brain-ink);
    -webkit-appearance: none;
  }
  .slider input[type="range"]::-moz-range-thumb {
    width: 10px;
    height: 10px;
    border: 0;
    border-radius: 50%;
    background: var(--brain-ink);
  }
  .panel .slider input[type="range"]:disabled {
    opacity: 0.45;
    cursor: default;
  }
  .slider output {
    text-align: right;
    color: var(--brain-mute);
    font-variant-numeric: tabular-nums;
  }
  .reset {
    display: block;
    margin: 10px 0 0 auto;
    padding: 3px 0;
    background: none;
    border: 0;
    color: var(--brain-mute);
  }
  .reset:hover,
  .link:not(:disabled):hover {
    color: var(--brain-strong);
  }
  .colours {
    display: grid;
    gap: 1px;
    max-height: 180px;
    margin: 0;
    padding: 0;
    overflow-y: auto;
    list-style: none;
    scrollbar-width: thin;
  }
  .colour {
    justify-content: flex-start;
    width: 100%;
    padding: 0;
    background: none;
    border: 0;
    text-align: left;
  }
  .colour i,
  .swatches button {
    flex: none;
    width: 11px;
    height: 11px;
    border-radius: 50%;
    box-shadow: 0 0 0 1px var(--brain-edge);
  }
  .colour .label {
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .colour[aria-expanded="true"] .label,
  .colour:hover .label {
    color: var(--brain-strong);
  }
  .mark {
    margin-left: auto;
    color: var(--brain-mute);
  }
  .picker {
    display: grid;
    gap: 6px;
    padding: 6px 0 8px 19px;
  }
  .swatches {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
  }
  .swatches button {
    width: 14px;
    height: 14px;
    padding: 0;
    border: 0;
  }
  .swatches button[aria-pressed="true"] {
    box-shadow: 0 0 0 2px var(--brain-ink);
  }
  .custom {
    display: flex;
    align-items: center;
    gap: 6px;
  }
  .panel .custom input[type="color"] {
    width: 22px;
    height: 18px;
    padding: 0;
    border: 1px solid var(--brain-edge);
    border-radius: 3px;
    background: none;
    cursor: pointer;
  }
  .panel .custom input[type="text"] {
    width: 74px;
    height: 20px;
    padding: 0 5px;
    font: 10.5px/1 var(--font-mono, ui-monospace, monospace);
    color: var(--brain-ink);
    background: transparent;
    border: 1px solid var(--brain-edge);
    border-radius: 3px;
  }
  .link {
    padding: 2px 0;
    background: none;
    border: 0;
    color: var(--brain-mute);
  }
  .link:disabled {
    opacity: 0.45;
    cursor: default;
  }
  .reset-colours {
    margin-top: 6px;
  }
  button:focus-visible,
  input:focus-visible {
    outline: 1px solid var(--brain-strong);
    outline-offset: 2px;
  }
  @media (prefers-reduced-motion: reduce) {
    .switch i,
    .switch i::after {
      transition: none;
    }
  }
</style>
