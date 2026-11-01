<script lang="ts">
  import { onMount } from "svelte";
  import { Button } from "@nunera/bracket";
  import { webglAvailable } from "./graph3d";
  import type { GraphLook } from "./graph3d";
  import { GRAPH_SETTINGS_EVENT, graphLook, setGraphLook } from "./graphPreferences";
  let look = $state(graphLook());
  let error = $state("");
  const supports3D = webglAvailable();
  onMount(() => {
    const sync = () => { look = graphLook(); };
    window.addEventListener(GRAPH_SETTINGS_EVENT, sync);
    return () => window.removeEventListener(GRAPH_SETTINGS_EVENT, sync);
  });
  function choose(next: GraphLook) {
    try { setGraphLook(next); error = ""; }
    catch { error = "This browser could not save the graph preference."; }
  }
</script>
<div class="look" role="group" aria-label="Graph look">
  <Button size="sm" variant={look === "2d" ? "soft" : "ghost"} aria-pressed={look === "2d"} onclick={() => choose("2d")}>2D</Button>
  <Button size="sm" variant={look === "3d" ? "soft" : "ghost"} aria-pressed={look === "3d"} disabled={!supports3D} title={supports3D ? "Show graphs in 3D" : "3D requires WebGL; 2D remains available"} onclick={() => choose("3d")}>3D</Button>
  {#if error}<span role="alert">{error}</span>{/if}
</div>
<style>
  .look { display: flex; align-items: center; gap: 0.25rem; }
  [role="alert"] { color: var(--danger, var(--ink)); font-size: 0.75rem; }
</style>
