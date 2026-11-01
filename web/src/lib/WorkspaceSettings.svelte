<script lang="ts">
  import { onMount } from "svelte";
  import { Button, Panel } from "@nunera/bracket";
  import { api, type ModuleId, type ModulesState } from "./api";
  import ThemePicker from "./ThemePicker.svelte";
  import DesktopSettings from "./DesktopSettings.svelte";
  import AccountSettings from "./AccountSettings.svelte";
  import ConnectedApps from "./ConnectedApps.svelte";
  import Keys from "./Keys.svelte";
  import Devices from "./Devices.svelte";
  import TagSettings from "./TagSettings.svelte";
  import { DEFAULT_PHYSICS, PHYSICS_LIMITS, type GraphPhysics, type GraphLayout } from "./graph3d";
  import { GRAPH_LAYOUT_KEY, GRAPH_PHYSICS_KEY, GRAPH_LAYOUTS, GRAPH_SETTINGS_EVENT, graphLayout, graphPhysics } from "./graphPreferences";
  import GraphLookToggle from "./GraphLookToggle.svelte";
  import { setGraphLook } from "./graphPreferences";
  import type { View } from "./workspace";
  import { activeModules } from "./modules.svelte";
  let { onview, onchange }: { onview: (view: View) => void; onchange: () => Promise<void> } = $props();
  let layout = $state(graphLayout());
  let physics = $state(graphPhysics());
  let notice = $state("");
  let error = $state("");
  let modules = $state<ModulesState | null>(null);
  let selection = $state<ModuleId[]>([]);
  let savingModules = $state(false);
  let moduleNotice = $state("");
  let moduleError = $state("");
  const modulesDirty = $derived(
    !!modules && (selection.length !== modules.configured.length || selection.some(id => !modules!.configured.includes(id))),
  );
  const changes = $derived.by(() => {
    if (!modules) return { start: [] as string[], stop: [] as string[] };
    const label = (id: ModuleId) => modules!.available.find(item => item.id === id)?.label ?? id;
    return {
      start: modules.configured.filter(id => !modules!.enabled.includes(id)).map(label),
      stop: modules.enabled.filter(id => !modules!.configured.includes(id)).map(label),
    };
  });
  export function hasUnsavedChanges() { return modulesDirty; }
  function adopt(next: ModulesState) {
    modules = next;
    selection = [...next.configured];
  }
  onMount(() => {
    let cancelled = false;
    api<ModulesState>("/modules")
      .then(next => { if (!cancelled) adopt(next); })
      .catch(cause => { if (!cancelled) moduleError = (cause as Error).message; });
    return () => { cancelled = true; };
  });
  function toggleModule(id: ModuleId, on: boolean) {
    if (!modules) return;
    moduleNotice = "";
    selection = modules.available.map(item => item.id).filter(item => (item === id ? on : selection.includes(item)));
  }
  async function saveModules() {
    if (!modules || savingModules) return;
    savingModules = true;
    moduleError = "";
    moduleNotice = "";
    try {
      adopt(await api<ModulesState>("/modules", { enabled: selection }));
      moduleNotice = modules?.restart_required
        ? "Saved. Restart kitty to apply this change; nothing restarts automatically."
        : "Saved. This matches what is already running.";
      await onchange();
    } catch (cause) {
      moduleError = `Could not save modules: ${(cause as Error).message}`;
    } finally {
      savingModules = false;
    }
  }
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
  <DesktopSettings />
  <AccountSettings />
  <ConnectedApps />
  <Keys />
  <Devices />
  {#if activeModules.enabled.includes("workspace")}<TagSettings />{/if}
  <div data-tutorial="modules">
  <Panel title="Modules">
    <p class="muted">Turn parts of kitty on or off. A saved choice applies the next time kitty starts; your data is never removed.</p>
    {#if modules}
      <fieldset class="modules" disabled={savingModules}>
        <legend class="sr-only">Modules</legend>
        {#each modules.available as item (item.id)}
          <label class="module-row">
            <input type="checkbox" checked={selection.includes(item.id)} onchange={(event) => toggleModule(item.id, event.currentTarget.checked)} />
            <span>
              <strong>{item.label}</strong>
              <small>{item.description}</small>
              <small class="state">{modules.enabled.includes(item.id) ? "Active now" : "Not active now"}{#if modules.configured.includes(item.id) !== modules.enabled.includes(item.id)} · {modules.configured.includes(item.id) ? "starts after restart" : "stops after restart"}{/if}</small>
            </span>
          </label>
        {/each}
      </fieldset>
      <div class="links">
        <Button intent="primary" size="sm" disabled={!modulesDirty || savingModules} onclick={saveModules}>{savingModules ? "Saving…" : "Save modules"}</Button>
        <Button size="sm" variant="ghost" disabled={!modulesDirty || savingModules} onclick={() => { if (modules) selection = [...modules.configured]; moduleNotice = ""; }}>Discard changes</Button>
      </div>
      {#if modulesDirty}<p class="muted" role="status">Unsaved selection.</p>{/if}
      {#if modules.restart_required}<p class="restart" role="status">Restart kitty to apply the saved selection{#if changes.start.length} · turns on: {changes.start.join(", ")}{/if}{#if changes.stop.length} · turns off: {changes.stop.join(", ")}{/if}. Until then the running session is unchanged.</p>{/if}
      {#if modules.error}<p role="alert">The saved module selection could not be read ({modules.error}). The running session is unaffected; saving a new selection replaces it.</p>{/if}
    {:else if !moduleError}<p class="muted" role="status">Loading modules…</p>{/if}
    {#if moduleNotice}<p class="muted" role="status">{moduleNotice}</p>{/if}
    {#if moduleError}<p role="alert">{moduleError}</p>{/if}
  </Panel>
  </div>
  <Panel title="Schemas">
    <div class="setting-row"><span>Graph look · remembered for every graph</span><GraphLookToggle /></div>
    <label class="setting-row" title="The layout used when you open Home or a section schema">Default layout<select value={layout} onchange={(event) => { layout = event.currentTarget.value as GraphLayout; save(); }}>{#each GRAPH_LAYOUTS as item}<option value={item.id}>{item.label}</option>{/each}</select></label>
    <div class="physics">{#each Object.keys(PHYSICS_LIMITS) as (keyof GraphPhysics)[] as key}
      <label title={explanations[key]}><span>{labels[key]}</span><input type="range" aria-label={labels[key]} aria-describedby={`graph-${key}-help`} min={PHYSICS_LIMITS[key].min} max={PHYSICS_LIMITS[key].max} step={PHYSICS_LIMITS[key].step} value={physics[key]} oninput={(event) => { physics[key] = Number(event.currentTarget.value); save(); }} /><output>{physics[key].toFixed(key === "repulsion" ? 0 : 2)}</output><small id={`graph-${key}-help`}>{explanations[key]}</small></label>
    {/each}</div>
    <Button size="sm" onclick={() => { layout = "tree"; physics = { ...DEFAULT_PHYSICS }; save(); try { setGraphLook("2d"); } catch { error = "This browser could not save local preferences."; } }}>Reset graph settings</Button>
    {#if notice}<p class="muted" role="status">{notice}</p>{/if}
    {#if error}<p role="alert">{error}</p>{/if}
  </Panel>
  <Panel title="Application settings"><div class="links">{#if activeModules.enabled.includes("applyai")}<Button onclick={() => onview("profile")}>Profile & saved answers</Button>{/if}<Button onclick={() => onview("ai")}>AI & privacy</Button></div></Panel>
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
  .modules { display: grid; gap: .5rem; margin: .75rem 0; padding: 0; border: 0; min-width: 0; }
  .module-row { display: flex; align-items: start; gap: .75rem; padding: .65rem .75rem; border: 1px solid var(--border-fine, var(--border)); border-radius: 8px; cursor: pointer; }
  .module-row span { display: grid; gap: .15rem; }
  .module-row small { color: var(--muted); line-height: 1.5; }
  .module-row .state { font-variant-numeric: tabular-nums; }
  .module-row input { margin-top: .2rem; accent-color: var(--accent); }
  .restart { margin: .75rem 0 0; padding: .55rem .75rem; border: 1px solid var(--warning); border-radius: 8px; font-size: .85rem; }
</style>
