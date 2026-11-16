<script lang="ts">
  import { buildStore } from "../build.svelte";
  import Icon from "../Icon.svelte";
  import RikaPanel from "./RikaPanel.svelte";

  /**
   * Rika beside any page (Ctrl + R, owned by App.svelte): home's own Rika panel, docked on the
   * right below the top bar at the width Rika's column has on home. The page narrows beside it
   * (it publishes --rika-dock-space, which styles.css takes off main), so nothing hides under it.
   * While it shows, it stands in for the drawer and the reply toasts, as home's panel does.
   * Escape inside it, Close or Ctrl + R again closes it (`onclose`).
   */
  let { onclose }: { onclose: () => void } = $props();
  // Home's column: `ui.home.rika` of home's content box (main less home's 26px sides, at most 1560px), or its default.
  const share = $derived(buildStore.build.ui.home.rika);
  const width = $derived(share
    ? `calc((min(100vw - 32px - var(--build-editor-space, 0px), 1560px) - 52px) * ${share})`
    : "clamp(340px, 30vw, 460px)");
  $effect(() => {
    // Home's 48px gap between the page and Rika; main's own 26px side padding is part of it.
    document.documentElement.style.setProperty("--rika-dock-space", `calc(${width} + 48px)`);
    return () => document.documentElement.style.removeProperty("--rika-dock-space");
  });
  function keydown(event: KeyboardEvent) {
    if (event.key !== "Escape" || event.defaultPrevented) return;
    event.preventDefault();
    onclose();
  }
</script>

<!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
<aside class="rika-dock" aria-label="Rika side panel" style:width onkeydown={keydown}>
  <RikaPanel id="rika-dock" active>
    {#snippet tail()}<button type="button" class="icon-button" aria-label="Close Rika" title="Close · Ctrl R or Escape" onclick={onclose}>
        <Icon name="close" size={16} />
      </button>{/snippet}
  </RikaPanel>
</aside>

<style>
  /* Where home's Rika column stands: home's 12px top and the workbench's 10px gap below the
     bar, the workbench's 16px and home's 26px sides, the workbench's 10px foot. */
  .rika-dock {
    position: fixed;
    top: calc(var(--workbench-top, 0px) + 22px);
    right: calc(var(--build-editor-space, 0px) + 42px);
    bottom: 10px;
    z-index: 30;
    display: flex;
    flex-direction: column;
  }
  .rika-dock :global(.rika-panel) {
    width: 100%;
  }
  .rika-dock .icon-button {
    align-self: center;
  }
  /* Phone widths: no room beside the page, so Rika covers it on the theme's raised surface. */
  @media (max-width: 599.98px) {
    .rika-dock {
      left: 10px;
      right: 10px;
      width: auto !important;
      padding: 0 12px;
      background: var(--surface-raised);
      border-radius: var(--r-panel);
    }
  }
</style>
