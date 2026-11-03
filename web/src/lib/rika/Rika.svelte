<script lang="ts">
  import { onMount } from "svelte";
  import "./rika.css";
  import RikaBar from "./RikaBar.svelte";
  import RikaHistory from "./RikaHistory.svelte";
  import RikaToasts from "./RikaToasts.svelte";
  import { rika } from "./session.svelte";

  /**
   * Rika in the window (docs/rika-design.md): the bar along the bottom, reply toasts above
   * it and the history drawer on the right. Ctrl + J opens or closes the drawer from
   * anywhere but a terminal or a modal dialog. `onnavigate` runs Rika's "open view" actions.
   */
  let { onnavigate }: { onnavigate: (view: string) => void } = $props();

  function keydown(event: KeyboardEvent) {
    if (!event.ctrlKey || event.altKey || event.shiftKey || event.metaKey || event.key.toLowerCase() !== "j") return;
    if (event.defaultPrevented || (event.target as Element | null)?.closest?.("[data-kitty-terminal]")) return;
    if (document.querySelector('dialog[open], [role="dialog"][aria-modal="true"]')) return;
    event.preventDefault();
    if (!event.repeat) rika.toggleDrawer();
  }
  onMount(() => rika.start((view) => onnavigate(view)));
</script>

<svelte:window onkeydown={keydown} />
<RikaBar />
<RikaToasts />
{#if rika.drawer.open}<RikaHistory />{/if}
