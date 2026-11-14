<script lang="ts">
  import BrainCloud from "./BrainCloud.svelte";
  import { themeMotif } from "./themes/motif.svelte";

  /**
   * Loading placeholder: the point-cloud brain and a caption that names what is loading.
   * `view`: centred, for whole views; a theme may bring its own art (contract.ts
   * `LoaderArt`). `rows`: one quiet line where a list will appear. Motion stops under
   * reduced motion.
   */
  let { kind = "rows", label }: { kind?: "view" | "rows"; label: string } = $props();
  const Art = $derived(themeMotif.module?.LoaderArt);
</script>

{#if kind === "view"}
  <div class="kitty-loader view" data-kind="view" role="status">
    <span class="art" data-loader-art={Art ? themeMotif.id : "default"}>{#if Art}<Art />{:else}<BrainCloud size={96} />{/if}</span>
    <p>{label}</p>
  </div>
{:else}
  <div class="kitty-loader rows" data-kind="rows" role="status">
    <BrainCloud size={36} />
    <p>{label}</p>
  </div>
{/if}

<style>
  .kitty-loader {
    color: var(--muted);
  }
  .kitty-loader p {
    margin: 0;
    font-size: 14px;
  }
  .kitty-loader.view {
    display: grid;
    justify-items: center;
    gap: 12px;
    padding: 24px;
    text-align: center;
  }
  .art {
    display: grid;
    place-items: center;
  }
  .kitty-loader.rows {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 16px;
  }
</style>
