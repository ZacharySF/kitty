<script lang="ts">
  import Icon from "../Icon.svelte";
  import { FULLSCREEN_ATTRIBUTE, PANEL_ATTRIBUTE, PANEL_FULLSCREEN_EVENT, togglePanelFullscreen } from "../panelFullscreen";

  /** One visible click for CSS panel fullscreen of the panel holding this button. */
  let { class: className = "icon-button", size = 15 }: { class?: string; size?: number } = $props();
  let button = $state<HTMLButtonElement>();
  let active = $state(false);
  $effect(() => {
    const sync = () => (active = Boolean(button?.closest(`[${PANEL_ATTRIBUTE}]`)?.hasAttribute(FULLSCREEN_ATTRIBUTE)));
    sync();
    window.addEventListener(PANEL_FULLSCREEN_EVENT, sync);
    return () => window.removeEventListener(PANEL_FULLSCREEN_EVENT, sync);
  });
</script>

<button bind:this={button} type="button" class={className}
  aria-label={active ? "Exit full screen" : "Full screen"}
  title={active ? "Exit full screen · Esc" : "Full screen · Ctrl+Shift+F"}
  onclick={() => button && togglePanelFullscreen(button)}><Icon name={active ? "collapse" : "expand"} {size} /></button>
