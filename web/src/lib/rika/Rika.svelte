<script lang="ts">
  import { onMount, untrack } from "svelte";
  import "./rika.css";
  import { morningBrief } from "../goals/brief.svelte";
  import RikaHistory from "./RikaHistory.svelte";
  import RikaToasts from "./RikaToasts.svelte";
  import { rika } from "./session.svelte";

  /**
   * Rika in the window (docs/rika-design.md): on home the panel (RikaPanel, in a column of
   * its own); everywhere else the drawer on the right and reply toasts. Ctrl + J
   * opens or closes the drawer, or focuses the panel's field, from anywhere but a terminal
   * or a modal dialog. `onnavigate` runs Rika's "open view" actions.
   */
  let { onnavigate }: { onnavigate: (view: string) => void } = $props();

  function keydown(event: KeyboardEvent) {
    if (!event.ctrlKey || event.altKey || event.shiftKey || event.metaKey || event.key.toLowerCase() !== "j") return;
    if (event.defaultPrevented || (event.target as Element | null)?.closest?.("[data-kitty-terminal]")) return;
    if (document.querySelector('dialog[open], [role="dialog"][aria-modal="true"]')) return;
    event.preventDefault();
    if (!event.repeat) rika.toggleDrawer();
  }
  // Ctrl K "Morning brief" opens the chip's popover, which sits in the panel or the drawer.
  let briefOpen = false;
  $effect(() => {
    const opened = morningBrief.open && !briefOpen;
    briefOpen = morningBrief.open;
    if (opened && !rika.panelVisible && !rika.drawer.open) untrack(() => rika.showDrawer("conversation"));
  });
  onMount(() => rika.start((view) => onnavigate(view)));
</script>

<svelte:window onkeydown={keydown} />
{#if !rika.panelVisible}<RikaToasts />{/if}
{#if rika.drawer.open}<RikaHistory />{/if}
