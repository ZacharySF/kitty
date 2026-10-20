<script lang="ts">
  import { Button, Panel } from "@nunera/bracket";
  import ThemePicker from "./ThemePicker.svelte";
  import { DEFAULT_PHYSICS, PHYSICS_LIMITS, type GraphPhysics, type GraphLayout } from "./graph3d";
  import { GRAPH_LAYOUT_KEY, GRAPH_PHYSICS_KEY, GRAPH_LAYOUTS, GRAPH_SETTINGS_EVENT, graphLayout, graphPhysics } from "./graphPreferences";
  import type { View } from "./workspace";
  let { onview }: { onview: (view: View) => void } = $props();
  let layout = $state(graphLayout());
  let physics = $state(graphPhysics());
  let notice = $state("");
  let error = $state("");
  const labels: Record<keyof GraphPhysics, string> = { repulsion: "Repulsion", spring: "Spring", damping: "Damping" };
  const explanations: Record<keyof GraphPhysics, string> = {
    repulsion: "How strongly nodes push apart. Increase to give crowded branches more room.",
    spring: "How strongly connected nodes pull together. Lower values leave looser branches.",
    damping: "How quickly node movement slows. Higher values settle the graph sooner.",
  };
  function save() {
    try {
      localStorage.setItem(GRAPH_LAYOUT_KEY, layout);
      localStorage.setItem(GRAPH_PHYSICS_KEY, JSON.stringify(physics));
      window.dispatchEvent(new CustomEvent(GRAPH_SETTINGS_EVENT, { detail: { layout, physics: { ...physics } } }));
      notice = "Saved · open schemas update immediately";
      error = "";
    } catch { error = "This browser could not save local preferences."; }
  }
</script>
<section class="settings-workspace" aria-label="Workspace settings">
  <header><h1>Settings</h1></header>
  <Panel title="Appearance"><div class="setting-row"><span>Theme</span><ThemePicker /></div></Panel>
  <Panel title="Schemas">
    <label class="setting-row" title="The layout used when you open Home or a section schema">Default layout<select value={layout} onchange={(event) => { layout = event.currentTarget.value as GraphLayout; save(); }}>{#each GRAPH_LAYOUTS as item}<option value={item.id}>{item.label}</option>{/each}</select></label>
    <div class="physics">{#each Object.keys(PHYSICS_LIMITS) as (keyof GraphPhysics)[] as key}
      <label title={explanations[key]}><span>{labels[key]}</span><input type="range" aria-label={labels[key]} aria-describedby={`graph-${key}-help`} min={PHYSICS_LIMITS[key].min} max={PHYSICS_LIMITS[key].max} step={PHYSICS_LIMITS[key].step} value={physics[key]} oninput={(event) => { physics[key] = Number(event.currentTarget.value); save(); }} /><output>{physics[key].toFixed(key === "repulsion" ? 0 : 2)}</output><small id={`graph-${key}-help`}>{explanations[key]}</small></label>
    {/each}</div>
    <Button size="sm" onclick={() => { layout = "tree"; physics = { ...DEFAULT_PHYSICS }; save(); }}>Reset graph settings</Button>
    {#if notice}<p class="muted" role="status">{notice}</p>{/if}
    {#if error}<p role="alert">{error}</p>{/if}
  </Panel>
  <Panel title="Application settings"><div class="links"><Button onclick={() => onview("profile")}>Profile & saved answers</Button><Button onclick={() => onview("ai")}>AI & privacy</Button></div></Panel>
</section>
<style>
  .settings-workspace { display: grid; gap: 1rem; max-width: 850px; margin: 0 auto; }
  h1 { margin: 0; font-size: 1.6rem; }
  .setting-row { display: flex; justify-content: space-between; align-items: center; gap: 1rem; padding: .55rem 0; }
  select { min-width: 160px; }
  .settings-workspace :global(.br-panel) { border-color: var(--border-fine, var(--border)); }
  .physics { display: grid; gap: .8rem; margin: 1rem 0; }
  .physics label { display: grid; grid-template-columns: 100px minmax(80px, 1fr) 48px; align-items: center; gap: 1rem; }
  .physics small { grid-column: 1 / -1; color: var(--muted); line-height: 1.5; }
  output { text-align: right; color: var(--muted); font-variant-numeric: tabular-nums; }
  input[type=range] { width: 100%; accent-color: var(--accent); }
  .links { display: flex; flex-wrap: wrap; gap: .65rem; }
  .muted { color: var(--muted); font-size: .8rem; }
</style>
