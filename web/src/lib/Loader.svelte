<script lang="ts">
  import CatEyes from "./themes/art/CatEyes.svelte";
  import { themeMotif } from "./themes/motif.svelte";

  /**
   * Loading placeholder. `view`: the theme's loader art (contract.ts `LoaderArt`; by default
   * a cat whose glowing eyes blink and glance) with a visible caption, for whole views.
   * `rows`: shimmering skeleton rows where a list will appear; the label is announced but not
   * shown. Motion stops under reduced motion.
   */
  let { kind = "rows", label, rows = 3 }: { kind?: "view" | "rows"; label: string; rows?: number } = $props();
  const widths = [0.82, 0.64, 0.74, 0.58, 0.7];
  const Art = $derived(themeMotif.module?.LoaderArt);
</script>

{#if kind === "view"}
  <div class="kitty-loader view" data-kind="view" role="status">
    <span class="art" data-loader-art={Art ? themeMotif.id : "default"}>{#if Art}<Art />{:else}<CatEyes />{/if}</span>
    <p>{label}</p>
  </div>
{:else}
  <div class="kitty-loader rows" data-kind="rows" role="status" aria-label={label}>
    {#each Array.from({ length: rows }, (_, index) => widths[index % widths.length]) as width, index (index)}
      <div class="skeleton-row" aria-hidden="true">
        <span class="dot"></span>
        <span class="lines"><span style:width={`${width * 100}%`}></span><span style:width={`${width * 60}%`}></span></span>
      </div>
    {/each}
  </div>
{/if}

<style>
  .kitty-loader.view {
    display: grid;
    justify-items: center;
    gap: 8px;
    padding: 24px;
    color: var(--muted);
    text-align: center;
  }
  .art {
    display: grid;
    place-items: center;
  }
  .view p {
    margin: 0;
    font-size: 12.5px;
  }
  .kitty-loader.rows {
    display: grid;
    gap: 14px;
    width: 100%;
    max-width: 640px;
    padding: 16px 4px;
  }
  .skeleton-row {
    display: flex;
    align-items: center;
    gap: 12px;
  }
  .dot,
  .lines span {
    background: linear-gradient(
      90deg,
      var(--surface-raised) 0 30%,
      color-mix(in srgb, var(--violet) 22%, var(--surface-raised)) 50%,
      var(--surface-raised) 70% 100%
    );
    background-size: 300% 100%;
    animation: kitty-shimmer 1.6s linear infinite;
  }
  .dot {
    flex: none;
    width: 28px;
    height: 28px;
    border-radius: 999px;
  }
  .lines {
    display: grid;
    flex: 1;
    gap: 7px;
  }
  .lines span {
    height: 9px;
    border-radius: 5px;
  }
  .lines span + span {
    height: 7px;
    opacity: 0.6;
  }
  @keyframes kitty-shimmer {
    from { background-position: 100% 0; }
    to { background-position: 0 0; }
  }
</style>
