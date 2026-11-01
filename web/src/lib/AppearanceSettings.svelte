<script lang="ts">
  import { onMount } from "svelte";
  import { Panel } from "@nunera/bracket";
  import AppearancePreview from "./AppearancePreview.svelte";
  import { PRESETS, savedAppearance, setAppearance, type Appearance, type Preset } from "./appearance";
  import type { GraphLook } from "./graph3d";
  import { GRAPH_SETTINGS_EVENT, graphLook, homeLook, setGraphLook, setHomeLook, type HomeLook } from "./graphPreferences";
  import { THEMES, type ThemeId } from "./themeCatalog";
  import { THEME_EVENT, currentTheme, setTheme } from "./themes";

  /**
   * Q59: every look control in one panel beside a live preview, plus Cozy,
   * Focused and Minimal presets that set them all; each still changes alone after.
   */
  let appearance = $state(savedAppearance());
  let theme = $state<ThemeId>(currentTheme());
  let look = $state<GraphLook>(graphLook());
  let home = $state<HomeLook>(homeLook());
  let error = $state("");

  type Choice = { [K in keyof Appearance]: Appearance[K] extends boolean ? never : K }[keyof Appearance];
  const CHOICES: { key: Choice; label: string; options: [string, string][] }[] = [
    { key: "titleFont", label: "Titles", options: [["fraunces", "Fraunces"], ["plain", "Plain"]] },
    { key: "density", label: "Density", options: [["compact", "Compact · 13px"], ["comfortable", "Comfortable · 14px"]] },
    { key: "motion", label: "Motion", options: [["full", "Full"], ["reduced", "Reduced"]] },
    { key: "background", label: "Background", options: [["nebula", "Nebula"], ["petals", "Petal drift"]] },
  ];
  const LOOKS: [GraphLook, string][] = [["2d", "2D"], ["3d", "3D"]];
  const HOME_LOOKS: [HomeLook, string][] = [["brain", "Brain"], ["2d", "2D map"]];

  function change(next: Appearance) {
    appearance = next;
    setAppearance(next);
  }
  function chooseLook(next: GraphLook) {
    try {
      setGraphLook(next);
      look = next;
      error = "";
    } catch {
      error = "This browser could not save the section graph look.";
    }
  }
  function chooseHome(next: HomeLook) {
    try {
      setHomeLook(next);
      home = next;
      error = "";
    } catch {
      error = "This browser could not save the home look.";
    }
  }
  function applyPreset(preset: Preset) {
    setTheme(preset.theme);
    chooseLook(preset.look);
    change({ ...preset.appearance });
  }
  const matches = (preset: Preset) =>
    preset.theme === theme && preset.look === look &&
    (Object.keys(preset.appearance) as (keyof Appearance)[]).every((key) => preset.appearance[key] === appearance[key]);

  onMount(() => {
    const syncTheme = () => (theme = currentTheme());
    const syncLook = () => {
      look = graphLook();
      home = homeLook();
    };
    window.addEventListener(THEME_EVENT, syncTheme);
    window.addEventListener(GRAPH_SETTINGS_EVENT, syncLook);
    return () => {
      window.removeEventListener(THEME_EVENT, syncTheme);
      window.removeEventListener(GRAPH_SETTINGS_EVENT, syncLook);
    };
  });
</script>

{#snippet segments(label: string, name: string, options: [string, string][], value: string, pick: (id: string) => void)}
  <fieldset class="field">
    <legend>{label}</legend>
    <div class="segments">
      {#each options as [id, text] (id)}
        <label class:on={value === id}>
          <input type="radio" {name} value={id} checked={value === id} onchange={() => pick(id)} />{text}
        </label>
      {/each}
    </div>
  </fieldset>
{/snippet}

<Panel title="Appearance" aria-label="Appearance">
  <div class="presets" role="group" aria-label="Appearance presets">
    {#each PRESETS as preset (preset.id)}
      <button class="preset" aria-pressed={matches(preset)} onclick={() => applyPreset(preset)}>
        <span class="name">{preset.name}</span>
        <span class="blurb">{preset.blurb}</span>
      </button>
    {/each}
  </div>
  <div class="layout">
    <div class="controls">
      <fieldset class="field wide">
        <legend>Theme</legend>
        <div class="themes">
          {#each THEMES as item (item.id)}
            <label class:on={theme === item.id} title={item.label}>
              <input type="radio" name="appearance-theme" value={item.id} checked={theme === item.id} onchange={() => setTheme(item.id)} />
              <span class="swatch" style:background={item.roles.void} style:border-color={item.roles.line}
                ><i style:background={item.roles.structure}></i><i style:background={item.graph[2]}></i></span
              >
              <small>{item.label}</small>
            </label>
          {/each}
        </div>
      </fieldset>
      {#each CHOICES as choice (choice.key)}
        {@render segments(choice.label, `appearance-${choice.key}`, choice.options, appearance[choice.key], (id) =>
          change({ ...appearance, [choice.key]: id } as Appearance))}
      {/each}
      {@render segments("Home", "appearance-home", HOME_LOOKS, home, (id) => chooseHome(id as HomeLook))}
      {@render segments("Section graph look", "appearance-look", LOOKS, look, (id) => chooseLook(id as GraphLook))}
      <label class="check wide">
        <input type="checkbox" checked={appearance.motif} onchange={(event) => change({ ...appearance, motif: event.currentTarget.checked })} />
        Blossom motif in icons and the theme picker
      </label>
      {#if error}<p class="wide" role="alert">{error}</p>{/if}
    </div>
    <AppearancePreview {appearance} {look} />
  </div>
</Panel>

<style>
  .presets {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 8px;
    margin-bottom: 14px;
  }
  .preset {
    display: grid;
    gap: 4px;
    align-content: start;
    padding: 10px 12px;
    border: 1px solid var(--hairline);
    border-radius: 12px;
    background: color-mix(in srgb, var(--solid-panel) 50%, transparent);
    color: var(--ink);
    text-align: left;
    cursor: pointer;
    transition: border-color 140ms, background-color 140ms;
  }
  .preset[aria-pressed="true"] {
    border-color: var(--violet);
    background: color-mix(in srgb, var(--violet) 11%, var(--solid-panel));
  }
  .name {
    font: 600 16px var(--font-title);
  }
  .blurb {
    color: var(--muted);
    font-size: 11.5px;
    line-height: 1.35;
  }
  .layout {
    display: grid;
    grid-template-columns: minmax(0, 1.15fr) minmax(220px, 1fr);
    gap: 16px;
    align-items: start;
  }
  .controls {
    display: flex;
    flex-wrap: wrap;
    align-items: start;
    gap: 12px 18px;
  }
  .wide {
    flex-basis: 100%;
  }
  .field {
    display: grid;
    gap: 5px;
    margin: 0;
    padding: 0;
    border: 0;
    min-width: 0;
  }
  legend {
    padding: 0;
    margin-bottom: 5px;
    color: var(--muted);
    font-size: 12px;
  }
  .segments {
    display: inline-flex;
    flex-wrap: nowrap;
    gap: 2px;
    width: fit-content;
    padding: 3px;
    border: 1px solid var(--hairline);
    border-radius: 999px;
    background: color-mix(in srgb, var(--solid-panel) 40%, transparent);
  }
  .segments label {
    position: relative;
    padding: 4px 11px;
    border-radius: 999px;
    color: var(--muted);
    font: 500 11px var(--font-ui);
    cursor: pointer;
    white-space: nowrap;
  }
  .segments label.on {
    background: color-mix(in srgb, var(--ink) 13%, transparent);
    color: var(--ink);
  }
  .segments label:has(input:focus-visible),
  .themes label:has(input:focus-visible) {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }
  /* The real radio covers its whole segment, invisible, so clicks and keys reach it. */
  .segments input,
  .themes input {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    margin: 0;
    opacity: 0;
    cursor: pointer;
  }
  .themes {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(62px, 1fr));
    gap: 4px;
  }
  .themes label {
    position: relative;
    display: grid;
    justify-items: center;
    gap: 3px;
    padding: 5px 2px;
    border: 1px solid transparent;
    border-radius: 10px;
    cursor: pointer;
  }
  .themes label.on {
    border-color: color-mix(in srgb, var(--violet) 60%, transparent);
    background: var(--wash);
  }
  .themes small {
    max-width: 100%;
    overflow: hidden;
    color: var(--muted);
    font-size: 10.5px;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .themes label.on small {
    color: var(--ink);
  }
  /* Theme swatches show each theme's own colours, so they are literal by design. */
  .swatch {
    display: flex;
    align-items: end;
    justify-content: center;
    gap: 3px;
    width: 34px;
    height: 22px;
    padding-bottom: 4px;
    border: 1px solid;
    border-radius: 7px;
  }
  .swatch i {
    width: 7px;
    height: 7px;
    border-radius: 999px;
  }
  .check {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    color: var(--ink);
    font-size: 12.5px;
  }
  .check input {
    accent-color: var(--violet);
  }
  @media (max-width: 760px) {
    .layout {
      grid-template-columns: minmax(0, 1fr);
    }
  }
  @media (max-width: 520px) {
    .presets {
      grid-template-columns: minmax(0, 1fr);
    }
  }
</style>
