<script lang="ts">
  import { onMount } from "svelte";
  import { Button } from "@nunera/bracket";
  import { webglAvailable } from "./graph3d";
  import { GRAPH_SETTINGS_EVENT, graphLook, rendererLook, setGraphLook, type AtlasLook } from "./graphPreferences";
  /** `brain`: offer the home-only Brain look. Elsewhere a remembered Brain shows as 3D. */
  let { brain = false }: { brain?: boolean } = $props();
  let look = $state(graphLook());
  let error = $state("");
  const supports3D = webglAvailable();
  const shown = $derived(brain ? look : rendererLook(look));
  onMount(() => {
    const sync = () => { look = graphLook(); };
    window.addEventListener(GRAPH_SETTINGS_EVENT, sync);
    return () => window.removeEventListener(GRAPH_SETTINGS_EVENT, sync);
  });
  function choose(next: AtlasLook) {
    try { setGraphLook(next); error = ""; }
    catch { error = "This browser could not save the graph preference."; }
  }
</script>
<div class="look" role="group" aria-label="Graph look">
  <Button size="sm" variant={shown === "2d" ? "soft" : "ghost"} aria-pressed={shown === "2d"} onclick={() => choose("2d")}>2D</Button>
  <Button size="sm" variant={shown === "3d" ? "soft" : "ghost"} aria-pressed={shown === "3d"} disabled={!supports3D} title={supports3D ? "Show graphs in 3D" : "3D requires WebGL; 2D remains available"} onclick={() => choose("3d")}>3D</Button>
  {#if brain}<Button size="sm" variant={shown === "brain" ? "soft" : "ghost"} aria-pressed={shown === "brain"} disabled={!supports3D} title={supports3D ? "Show home as a brain of every record and edit" : "The Brain requires WebGL; 2D remains available"} onclick={() => choose("brain")}>Brain</Button>{/if}
  {#if error}<span role="alert">{error}</span>{/if}
</div>
<style>
  .look { display: flex; align-items: center; gap: 0.25rem; }
  [role="alert"] { color: var(--danger, var(--ink)); font-size: 0.75rem; }
</style>
