<script lang="ts">
  import { Button } from "@nunera/bracket";
  import HomeWidget from "./HomeWidget.svelte";
  import Icon from "./Icon.svelte";
  import type { View } from "./workspace";

  /** A view whose module is on but whose pack (download) this process lacks: the Brain's card. */
  let { id, label, onview }: { id: string; label: string; onview: (view: View) => void } = $props();
  /** "Download" lands on Settings' Modules page (`#settings/modules`), as the Brain's card does. */
  function openModuleSettings() {
    onview("settings");
    if (location.hash === "#settings") history.replaceState(history.state, "", "#settings/modules");
  }
</script>

<div class="pack-missing" data-pack-missing={id}>
  <HomeWidget id={`pack-missing-${id}`} title={`${label} isn't installed`}>
    <p class="note">Download it, then restart.</p>
    <Button intent="primary" size="sm" onclick={openModuleSettings}>Download <Icon name="arrow" size={14} /></Button>
  </HomeWidget>
</div>

<style>
  .pack-missing {
    display: grid;
    place-items: center;
    height: 100%;
    padding: 20px;
  }
  .pack-missing > :global(*) {
    width: min(320px, 100%);
  }
  .note {
    margin: 0 0 8px;
    color: var(--muted);
    font-size: 12px;
  }
</style>
