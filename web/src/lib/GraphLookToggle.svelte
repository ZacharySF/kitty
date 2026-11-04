<script lang="ts">
  import { Button } from "@nunera/bracket";
  import { buildStore } from "./build.svelte";
  import { webglAvailable } from "./graph3d";
  import { setGraphLook } from "./graphPreferences";
  // The build's `ui.graph.look`: a refused save reverts it and shows the build's notice.
  const look = $derived(buildStore.build.ui.graph.look);
  const supports3D = webglAvailable();
</script>
<div class="look" role="group" aria-label="Graph look">
  <Button size="sm" variant={look === "2d" ? "soft" : "ghost"} aria-pressed={look === "2d"} onclick={() => setGraphLook("2d")}>2D</Button>
  <Button size="sm" variant={look === "3d" ? "soft" : "ghost"} aria-pressed={look === "3d"} disabled={!supports3D} title={supports3D ? "Show graphs in 3D" : "3D requires WebGL; 2D remains available"} onclick={() => setGraphLook("3d")}>3D</Button>
</div>
<style>
  .look { display: flex; align-items: center; gap: 0.25rem; }
</style>
